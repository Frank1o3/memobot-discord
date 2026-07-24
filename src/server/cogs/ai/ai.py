"""
AI cog for AI-related Discord commands and event handling.

Contains AI-specific slash commands and the on_message event handler
for processing messages for AI responses.
"""

import asyncio
import logging
import random
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from server.ai_client import AIClient
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


class AICog(commands.Cog):
    """AI cog responsible for AI-specific Discord functionality."""

    def __init__(
        self,
        bot: commands.Bot,
        config: Config,
        ai_client: AIClient,
        decision_maker: ReplyDecisionMaker,
        memory_manager: MemoryManager,
    ) -> None:
        self.bot = bot
        self._config = config
        self._ai_client = ai_client
        self._decision_maker = decision_maker
        self._memory_manager = memory_manager

        # Track active tasks for cleanup
        self._active_tasks: set[asyncio.Task] = set()
        self.bot.on_message

        logger.info("AICog initialized")

    @app_commands.command(
        name="stats",
        description="View bot statistics and status",
    )
    async def stats(self, interaction: discord.Interaction) -> None:
        """Show bot statistics."""
        stats = self.get_stats()
        embed = discord.Embed(
            title="📊 Bot Statistics",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Memory",
            value=(
                f"Users: {stats['memory_stats']['total_users']}\n"
                f"Total Memories: {stats['memory_stats']['total_memories']}"
            ),
            inline=True,
        )
        embed.add_field(
            name="AI",
            value=f"Model: {stats['ai_stats']['model']}",
            inline=True,
        )
        embed.add_field(
            name="Active Tasks",
            value=str(stats["active_tasks"]),
            inline=True,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="clearmemories",
        description="Clear all stored memories about you",
    )
    async def clearmemories(self, interaction: discord.Interaction) -> None:
        """Clear user's stored memories."""
        if self._memory_manager and interaction.user:
            count = self._memory_manager.clear_user_memories(interaction.user.id)
            await interaction.response.send_message(
                f"🗑️ Cleared {count} memories about you.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Unable to clear memories at this time.",
                ephemeral=True,
            )

    @commands.command(
        name="stats",
        help="View bot statistics",
    )
    async def prefix_stats(self, ctx: commands.Context) -> None:
        """Show bot statistics via prefix command."""
        stats = self.get_stats()
        embed = discord.Embed(
            title="📊 Bot Statistics",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Memory",
            value=(
                f"Users: {stats['memory_stats']['total_users']}\n"
                f"Total Memories: {stats['memory_stats']['total_memories']}"
            ),
            inline=True,
        )
        embed.add_field(
            name="AI",
            value=f"Model: {stats['ai_stats']['model']}",
            inline=True,
        )
        await ctx.send(embed=embed)

    @commands.command(
        name="clearmemories",
        help="Clear all stored memories about you",
    )
    async def prefix_clear_memories(self, ctx: commands.Context) -> None:
        """Clear user's stored memories via prefix command."""
        if self._memory_manager and ctx.author:
            count = self._memory_manager.clear_user_memories(ctx.author.id)
            await ctx.send(f"🗑️ Cleared {count} memories about you.")
        else:
            await ctx.send("Unable to clear memories at this time")
    
    async def on_message(self, message: discord.Message) -> None:
        """
        Handle incoming messages for AI processing.

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
        if message.author == self.bot.user:
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
                    self.bot.user,
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

    def get_stats(self) -> dict:
        """Get statistics about the AI cog."""
        return {
            "active_tasks": len(self._active_tasks),
            "memory_stats": self._memory_manager.get_stats(),
            "ai_stats": self._ai_client.get_stats(),
        }

    async def cog_unload(self) -> None:
        """Clean up when cog is unloaded."""
        logger.info("Cleaning up AICog...")

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

        logger.info("AICog cleanup complete")


async def setup(
    bot: commands.Bot,
    config: Config,
    ai_client: AIClient,
    decision_maker: ReplyDecisionMaker,
    memory_manager: MemoryManager,
) -> None:
    """Load the AICog cog with dependencies."""
    cog = AICog(bot, config, ai_client, decision_maker, memory_manager)
    await bot.add_cog(cog)
