"""
Events module for the Discord AI chatbot.

This module contains all event handlers and message processing logic,
including the main message handling pipeline that coordinates between
all other modules.
"""

import asyncio
import logging
import random
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from server.ai import AIClient
from server.config import Config
from server.context import (
    fetch_channel_history,
    build_context,
    extract_recent_conversation,
    should_summarize,
)
from server.decision import ReplyDecisionMaker
from server.memory import MemoryManager
from server.prompts import (
    build_system_prompt,
    RATE_LIMIT_RESPONSE,
    ERROR_RESPONSES,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class EventHandler:
    """
    Handles all Discord events and coordinates bot responses.

    This class serves as the central coordinator between:
    - Discord events (messages, reactions, etc.)
    - AI inference (via AIClient)
    - Decision making (via ReplyDecisionMaker)
    - Memory management (via MemoryManager)
    """

    def __init__(
        self,
        bot: commands.Bot,
        config: Config,
        ai_client: AIClient,
        decision_maker: ReplyDecisionMaker,
        memory_manager: MemoryManager,
    ) -> None:
        """
        Initialize the event handler.

        Args:
            bot: The Discord bot instance.
            config: Bot configuration.
            ai_client: AI client for Groq API.
            decision_maker: Reply decision system.
            memory_manager: Long-term memory manager.
        """
        self._bot = bot
        self._config = config
        self._ai_client = ai_client
        self._decision_maker = decision_maker
        self._memory_manager = memory_manager

        # Track active tasks for cleanup
        self._active_tasks: set[asyncio.Task] = set()

        logger.info("EventHandler initialized")

    async def is_ready(self) -> None:
        """
        Handle the bot ready event.

        Syncs slash commands and performs initialization.
        """
        try:
            # Sync slash commands
            await self._bot.tree.sync()
            logger.info(f"Logged in as {self._bot.user}")
            logger.info(f"Bot ID: {self._bot.user.id}")
            logger.info(f"Servers: {len(self._bot.guilds)}")

            # Load memories
            self._memory_manager.load()
            logger.info(
                f"Loaded {self._memory_manager.get_stats()['total_memories']} memories"
            )

            # Log command info
            slash_commands = len(self._bot.tree.get_commands())
            prefix_commands = len(self._bot.commands)
            logger.info(
                f"Loaded {slash_commands} slash commands and {prefix_commands} prefix commands"
            )

        except Exception as e:
            logger.error(f"Error during is_ready: {e}")
            raise

    async def on_message(self, message: discord.Message) -> None:
        """
        Handle incoming messages.

        This is the main message processing pipeline that:
        1. Ignores bot messages (except own)
        2. Checks if we should reply
        3. Builds context from channel history
        4. Generates AI response
        5. Sends response with typing simulation

        Args:
            message: The Discord message to process.
        """
        # Ignore our own messages
        if message.author == self._bot.user:
            return

        # Ignore other bot messages
        if message.author.bot:
            return

        # Check if we should reply
        should_reply, reason = self._decision_maker.should_reply(message)

        if not should_reply:
            logger.debug(
                f"Not replying to message {message.id} from {message.author}: {reason}"
            )
            return

        logger.info(
            f"Replying to message {message.id} from {message.author} (reason: {reason})"
        )

        # Create task for handling the response
        task = asyncio.create_task(self._handle_response(message, reason))
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    async def _handle_response(
        self,
        message: discord.Message,
        reason: str,
    ) -> None:
        """
        Handle generating and sending a response.

        Args:
            message: The triggering message.
            reason: Why we decided to reply.
        """
        channel = message.channel

        try:
            # Check rate limit
            if not self._decision_maker._is_on_cooldown(message.author.id):
                # Fetch channel history
                history = await fetch_channel_history(
                    channel,
                    limit=self._config.max_context_messages + 20,
                )

                # Build context
                context_str, formatted_messages = build_context(
                    history,
                    self._bot.user,
                    self._config.max_context_messages,
                )

                # Get user's relevant memories
                memory_context = self._memory_manager.get_relevant_context(
                    message.author.id,
                    context_str,
                    message.author.display_name,
                )

                # Build messages for AI
                system_prompt = build_system_prompt()
                if memory_context:
                    system_prompt += "\n\n" + memory_context

                # Format conversation for AI
                user_messages = []
                if context_str:
                    user_messages.append(
                        {
                            "role": "user",
                            "content": f"Conversation context:\n{context_str}",
                        }
                    )

                # Add the current message
                current_msg_content = message.content or "[No text content]"

                # Include attachment info
                if message.attachments:
                    attachment_info = ", ".join(
                        f"[File: {a.filename}]({a.url})" for a in message.attachments
                    )
                    current_msg_content += f"\n\nAttachments: {attachment_info}"

                user_messages.append(
                    {
                        "role": "user",
                        "content": f"{message.author.display_name}: {current_msg_content}",
                    }
                )

                # Generate response
                response_text = ""
                async for chunk in self._ai_client.generate_response(
                    system_prompt,
                    user_messages,
                ):
                    response_text += chunk

                if not response_text.strip():
                    response_text = random.choice(ERROR_RESPONSES)

                # Simulate typing based on response length
                typing_duration = min(
                    len(response_text) * self._config.typing_speed,
                    10.0,  # Cap at 10 seconds
                )

                if typing_duration > 0.5:
                    await channel.typing()
                    await asyncio.sleep(typing_duration)

                # Send response - prefer reply if triggered by reply/mention
                if reason in ("reply_to_bot", "mentioned"):
                    await message.reply(response_text)
                else:
                    await channel.send(response_text)

                # Record the response
                self._decision_maker.record_response(message.author.id)
                self._decision_maker.record_bot_response_in_channel(channel.id)

                # Extract and save memories periodically
                if len(formatted_messages) >= 10:
                    recent_conv = extract_recent_conversation(formatted_messages)
                    memories = await self._ai_client.extract_memories(recent_conv)
                    if memories:
                        self._memory_manager.add_memories_from_text(
                            message.author.id,
                            message.author.display_name,
                            recent_conv,
                            memories,
                        )

                # Check if summarization is needed
                if should_summarize(
                    len(formatted_messages), self._config.summary_trigger
                ):
                    logger.info("Context size exceeded threshold, would summarize")
                    # Summarization logic could be added here

                logger.info(f"Response sent ({len(response_text)} chars)")

            else:
                # On cooldown - send brief message
                await channel.send(RATE_LIMIT_RESPONSE)
                logger.debug(f"User {message.author.id} on cooldown")

        except discord.Forbidden:
            logger.warning(f"Cannot send messages to channel {channel.id}")
        except discord.HTTPException as e:
            logger.error(f"Failed to send message: {e}")
        except Exception as e:
            logger.error(f"Unexpected error handling response: {e}", exc_info=True)

    async def on_message_edit(
        self,
        before: discord.Message,
        after: discord.Message,
    ) -> None:
        """
        Handle message edits.

        Only processes edits if the original message was from a user
        and the bot is actively engaged in the conversation.

        Args:
            before: The message before editing.
            after: The message after editing.
        """
        # Ignore bot messages
        if after.author.bot:
            return

        # Only process if content actually changed
        if before.content == after.content:
            return

        # Check if we're involved in this conversation
        state = self._decision_maker._get_channel_state(after.channel.id)
        if not state.is_bot_involved_recently():
            return

        logger.debug(f"Processing edited message {after.id}")

        # Re-evaluate whether to respond
        should_reply, reason = self._decision_maker.should_reply(after)

        if should_reply and reason != "cooldown":
            task = asyncio.create_task(self._handle_response(after, reason))
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """
        Handle joining a new guild/server.

        Args:
            guild: The guild that was joined.
        """
        logger.info(f"Joined guild {guild.name} ({guild.id})")

        # Try to send a welcome message to a general channel
        for channel in guild.text_channels:
            if (
                "general" in channel.name.lower()
                or channel.permissions_for(guild.me).send_messages
            ):
                try:
                    await channel.send(
                        "👋 Hey there! I'm your friendly AI assistant. "
                        "Mention me or use `/help` to get started!"
                    )
                    break
                except discord.Forbidden:
                    continue

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """
        Handle leaving a guild/server.

        Args:
            guild: The guild that was left.
        """
        logger.info(f"Left guild {guild.name} ({guild.id})")

    async def cleanup(self) -> None:
        """
        Clean up active tasks and resources.

        Called during graceful shutdown.
        """
        logger.info("Cleaning up event handler...")

        # Cancel active tasks
        for task in self._active_tasks:
            if not task.done():
                task.cancel()

        # Wait for tasks to complete
        if self._active_tasks:
            await asyncio.gather(*self._active_tasks, return_exceptions=True)

        # Save memories
        self._memory_manager.save()

        # Cleanup decision maker
        self._decision_maker.cleanup()

        logger.info("Event handler cleanup complete")

    def get_stats(self) -> dict:
        """
        Get statistics about the event handler.

        Returns:
            Dictionary with handler statistics.
        """
        return {
            "active_tasks": len(self._active_tasks),
            "memory_stats": self._memory_manager.get_stats(),
            "ai_stats": self._ai_client.get_stats(),
        }


def setup_event_handlers(
    bot: commands.Bot,
    config: Config,
    ai_client: AIClient,
    decision_maker: ReplyDecisionMaker,
    memory_manager: MemoryManager,
) -> EventHandler:
    """
    Set up all event handlers on the bot.

    Args:
        bot: The Discord bot instance.
        config: Bot configuration.
        ai_client: AI client for Groq API.
        decision_maker: Reply decision system.
        memory_manager: Long-term memory manager.

    Returns:
        The created EventHandler instance.
    """
    handler = EventHandler(bot, config, ai_client, decision_maker, memory_manager)

    # Register event handlers
    bot.on_ready = handler.is_ready
    bot.on_message = handler.on_message
    bot.on_message_edit = handler.on_message_edit
    bot.on_guild_join = handler.on_guild_join
    bot.on_guild_remove = handler.on_guild_remove

    logger.info("Event handlers registered")
    return handler
