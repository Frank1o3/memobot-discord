"""
AI cog for AI-related Discord commands and event handling.

Contains AI-specific slash commands and the on_message event handler
for processing messages for AI responses.
"""

import asyncio
import logging
import random

import discord
from discord import app_commands
from discord.ext import commands

from server.ai_client import AIClient
from server.config import Config
from server.context import (
    build_context,
    extract_recent_conversation,
    fetch_channel_history,
    should_summarize,
)
from server.cogs.ai.parser import parse_tool_calls
from server.decision import ReplyDecisionMaker
from server.memory import MemoryManager
from server.prompts import (
    ERROR_RESPONSES,
    build_system_prompt,
)

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
        music_cog: "MusicCog | None" = None,
    ) -> None:
        self.bot = bot
        self._config = config
        self._ai_client = ai_client
        self._decision_maker = decision_maker
        self._memory_manager = memory_manager
        self._music_cog = music_cog

        # Track active tasks for cleanup
        self._active_tasks: set[asyncio.Task] = set()

        logger.info("AICog initialized")

    @commands.hybrid_command(
        name="stats",
        description="View bot statistics and status",
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

    @commands.hybrid_command(
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

    @commands.Cog.listener()
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

            # Parse and execute tool calls from the AI response
            cleaned_response, tool_calls = parse_tool_calls(response_text)
            
            # Execute any tool calls found
            if tool_calls:
                await self._execute_tool_calls(message, tool_calls)
            
            # Use cleaned response (with tools removed) for display
            response_text = cleaned_response if cleaned_response else response_text

            # Simulate typing based on response length
            typing_duration = min(
                len(response_text) * self._config.typing_speed,
                10.0,  # Cap at 10 seconds
            )

            if typing_duration > 0.5:
                await channel.typing()
                await asyncio.sleep(typing_duration)

            # Send response - prefer reply if triggered by reply/mention
            if response_text:  # Only send if there's text to send
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
            if should_summarize(len(formatted_messages), self._config.summary_trigger):
                logger.info("Context size exceeded threshold, would summarize")
                # Summarization logic could be added here

            logger.info(f"Response sent ({len(response_text)} chars)")

        except discord.Forbidden:
            logger.warning(f"Cannot send messages to channel {channel.id}")
        except discord.HTTPException as e:
            logger.error(f"Failed to send message: {e}")
        except Exception as e:
            logger.error(f"Unexpected error handling response: {e}", exc_info=True)

    async def _execute_tool_calls(
        self,
        message: discord.Message,
        tool_calls: list,
    ) -> None:
        """
        Execute tool calls from the AI response.

        Args:
            message: The triggering Discord message.
            tool_calls: List of parsed ToolCall objects.
        """
        if not self._music_cog:
            logger.debug("Music cog not available, skipping tool execution")
            return

        if not message.guild:
            logger.debug("Tool calls only work in guilds")
            return

        guild_id = message.guild.id
        player = self._music_cog.get_player(guild_id)

        for tool_call in tool_calls:
            try:
                await self._execute_single_tool(message, tool_call, player)
            except Exception as e:
                logger.error(
                    f"Failed to execute tool {tool_call.name}: {e}",
                    exc_info=True,
                )

    async def _execute_single_tool(
        self,
        message: discord.Message,
        tool_call,
        player,
    ) -> None:
        """
        Execute a single tool call.

        Args:
            message: The triggering Discord message.
            tool_call: The ToolCall object to execute.
            player: The GuildPlayer instance for this guild.
        """
        tool_name = tool_call.name
        attributes = tool_call.attributes

        if tool_name == "join_vc":
            await self._handle_join_vc(message, player)
        elif tool_name == "leave_vc":
            await self._handle_leave_vc(message, player)
        elif tool_name == "queue":
            await self._handle_queue_tool(message, player, attributes)
        elif tool_name == "skip":
            await self._handle_skip(message, player, attributes)
        elif tool_name == "pause":
            await self._handle_pause(message, player)
        elif tool_name == "resume":
            await self._handle_resume(message, player)
        elif tool_name == "stop":
            await self._handle_stop(message, player)
        elif tool_name == "loop":
            await self._handle_loop(message, player)
        elif tool_name == "volume":
            await self._handle_volume(message, player, attributes)
        else:
            logger.debug(f"Unknown tool: {tool_name}")

    async def _handle_join_vc(
        self,
        message: discord.Message,
        player,
    ) -> None:
        """Handle join_vc tool call."""
        if not message.author.voice or not message.author.voice.channel:
            logger.debug("User not in voice channel, cannot join")
            return

        channel = message.author.voice.channel
        if not isinstance(channel, discord.VoiceChannel):
            logger.debug("User not in a regular voice channel")
            return

        if player.voice_client and player.voice_client.is_connected():
            logger.debug("Already connected to voice channel")
            return

        await player.connect(channel)
        logger.info(f"Joined voice channel {channel.name} via tool call")

    async def _handle_leave_vc(
        self,
        message: discord.Message,
        player,
    ) -> None:
        """Handle leave_vc tool call."""
        if not player.voice_client or not player.voice_client.is_connected():
            logger.debug("Not in a voice channel, cannot leave")
            return

        await player.disconnect()
        logger.info("Left voice channel via tool call")

    async def _handle_queue_tool(
        self,
        message: discord.Message,
        player,
        attributes: dict[str, str],
    ) -> None:
        """Handle queue tool call with action and query attributes."""
        action = attributes.get("action", "")
        query = attributes.get("query", "")

        if not query:
            logger.debug("Queue tool called without query")
            return

        if action == "add":
            await self._handle_queue_add(message, player, query)
        elif action == "remove":
            await self._handle_queue_remove(message, player, query)
        else:
            logger.debug(f"Unknown queue action: {action}")

    async def _handle_queue_add(
        self,
        message: discord.Message,
        player,
        query: str,
    ) -> None:
        """Handle queue add action."""
        if not message.guild:
            return

        # Auto-join if not connected
        if not player.voice_client or not player.voice_client.is_connected():
            if message.author.voice and message.author.voice.channel:
                channel = message.author.voice.channel
                if isinstance(channel, discord.VoiceChannel):
                    await player.connect(channel)
                else:
                    return
            else:
                return

        # Resolve the query
        result = await self._music_cog._resolver.resolve(
            query,
            requested_by=message.author,
        )

        if not result.tracks:
            logger.debug(f"Could not find track for query: {query}")
            return

        track = result.tracks[0]
        player.add_to_queue(track)

        was_idle = player.state == "stopped"

        if was_idle:
            success = await player.play(track)
            # Remove from queue since play() doesn't pop it
            if success and track in player.queue:
                player.queue.remove(track)

            logger.info(f"Started playing {track.title} via tool call")
        else:
            logger.info(f"Added {track.title} to queue via tool call")

    async def _handle_queue_remove(
        self,
        message: discord.Message,
        player,
        query: str,
    ) -> None:
        """Handle queue remove action - remove by song name."""
        # Search for matching track in queue
        for i, track in enumerate(player.queue):
            if query.lower() in track.title.lower():
                removed = player.remove_from_queue(i)
                if removed:
                    logger.info(f"Removed {removed.title} from queue via tool call")
                return

        logger.debug(f"No matching track found in queue for: {query}")

    async def _handle_skip(
        self,
        message: discord.Message,
        player,
        attributes: dict[str, str],
    ) -> None:
        """Handle skip tool call with optional query to skip to specific song."""
        if not player.voice_client or not player.voice_client.is_connected():
            logger.debug("Not in a voice channel, cannot skip")
            return

        if player.state == "stopped" or not player.current_track:
            logger.debug("Nothing is playing, cannot skip")
            return

        # Check if query is provided to skip to a specific song
        query = attributes.get("query", "")
        
        if query:
            # Search for the song in the queue and skip to it
            found_index = None
            for i, track in enumerate(player.queue):
                if query.lower() in track.title.lower():
                    found_index = i
                    break
            
            if found_index is not None:
                # Move all tracks before the found track to the back of queue
                # or simply remove them from history perspective
                for _ in range(found_index + 1):
                    if player.queue:
                        next_track = player.queue.popleft()
                        if _ == found_index:
                            # This is the target track, play it
                            await player.play(next_track)
                            logger.info(f"Skipped to {next_track.title} via tool call")
                            break
                        else:
                            # Archive skipped tracks to history
                            if player.current_track:
                                player.history.append(player.current_track)
                            player._current_track = next_track
                return
            else:
                logger.debug(f"Song '{query}' not found in queue, doing normal skip")
        
        # Normal skip behavior
        had_next = await player.play_next()
        if had_next:
            logger.info(f"Skipped to {player.current_track.title if player.current_track else 'Unknown'} via tool call")
        else:
            logger.info("Stopped playback (no more tracks) via tool call")

    async def _handle_pause(
        self,
        message: discord.Message,
        player,
    ) -> None:
        """Handle pause tool call."""
        if not player.voice_client or not player.voice_client.is_connected():
            logger.debug("Not in a voice channel, cannot pause")
            return

        if player.state != "playing":
            logger.debug("Nothing is playing, cannot pause")
            return

        paused = player.pause()
        if paused:
            logger.info("Playback paused via tool call")

    async def _handle_resume(
        self,
        message: discord.Message,
        player,
    ) -> None:
        """Handle resume tool call."""
        if not player.voice_client or not player.voice_client.is_connected():
            logger.debug("Not in a voice channel, cannot resume")
            return

        if player.state != "paused":
            logger.debug("Nothing is paused, cannot resume")
            return

        resumed = player.resume()
        if resumed:
            logger.info("Playback resumed via tool call")

    async def _handle_stop(
        self,
        message: discord.Message,
        player,
    ) -> None:
        """Handle stop tool call."""
        if not player.voice_client or not player.voice_client.is_connected():
            logger.debug("Not in a voice channel, cannot stop")
            return

        player.stop()
        logger.info("Playback stopped and queue cleared via tool call")

    async def _handle_loop(
        self,
        message: discord.Message,
        player,
    ) -> None:
        """Handle loop tool call - toggle repeat mode."""
        if not player.voice_client or not player.voice_client.is_connected():
            logger.debug("Not in a voice channel, cannot toggle loop")
            return

        new_mode = player.toggle_repeat()
        logger.info(f"Loop mode set to {new_mode} via tool call")

    async def _handle_volume(
        self,
        message: discord.Message,
        player,
        attributes: dict[str, str],
    ) -> None:
        """Handle volume tool call with level attribute."""
        if not player.voice_client or not player.voice_client.is_connected():
            logger.debug("Not in a voice channel, cannot change volume")
            return

        level_str = attributes.get("level", "")
        try:
            level = int(level_str)
            if 0 <= level <= 100:
                player.set_volume(level)
                logger.info(f"Volume set to {level}% via tool call")
            else:
                logger.debug(f"Invalid volume level: {level}")
        except ValueError:
            logger.debug(f"Could not parse volume level: {level_str}")

    @commands.Cog.listener()
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
