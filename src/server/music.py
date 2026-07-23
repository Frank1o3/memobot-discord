"""
Music module for the Discord AI chatbot.

This module provides voice channel and music playback functionality,
allowing the bot to:
- Join voice channels
- Play music from URLs or search queries
- Control playback (pause, resume, skip, stop)
- Manage the song queue
"""

import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands
import yt_dlp

logger = logging.getLogger(__name__)


class SongQueue:
    """Manages the queue of songs to be played."""

    def __init__(self) -> None:
        self._queue: list[dict] = []
        self._current_song: Optional[dict] = None

    def add(self, song: dict) -> None:
        """Add a song to the queue."""
        self._queue.append(song)
        logger.info(f"Added '{song['title']}' to queue")

    def next(self) -> Optional[dict]:
        """Get the next song from the queue."""
        if self._queue:
            self._current_song = self._queue.pop(0)
            return self._current_song
        self._current_song = None
        return None

    def skip(self) -> Optional[dict]:
        """Skip the current song and get the next one."""
        return self.next()

    def clear(self) -> None:
        """Clear the entire queue."""
        self._queue.clear()
        self._current_song = None

    @property
    def current(self) -> Optional[dict]:
        """Get the current song."""
        return self._current_song

    @property
    def queue(self) -> list[dict]:
        """Get the song queue."""
        return self._queue.copy()

    @property
    def size(self) -> int:
        """Get the queue size."""
        return len(self._queue)

    def remove(self, index: int) -> Optional[dict]:
        """Remove a song from the queue by index."""
        if 0 <= index < len(self._queue):
            return self._queue.pop(index)
        return None


class VoiceState:
    """Holds the voice state for a guild."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.voice_client: Optional[discord.VoiceClient] = None
        self.queue = SongQueue()
        self.is_playing = False
        self.is_paused = False
        self._player_task: Optional[asyncio.Task] = None

    async def connect(self, channel: discord.VoiceChannel) -> bool:
        """Connect to a voice channel."""
        try:
            if self.voice_client and self.voice_client.is_connected():
                await self.voice_client.move_to(channel)
            else:
                self.voice_client = await channel.connect()
            logger.info(f"Connected to voice channel {channel.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to voice channel: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from the voice channel."""
        if self.voice_client:
            await self.voice_client.disconnect()
            self.voice_client = None
            self.queue.clear()
            self.is_playing = False
            self.is_paused = False
            logger.info("Disconnected from voice channel")

    async def play(self, song: dict) -> None:
        """Play a song in the voice channel."""
        if not self.voice_client:
            logger.warning("Not connected to voice channel")
            return

        try:
            # Stop any currently playing audio
            if self.voice_client.is_playing():
                self.voice_client.stop()

            # Create FFmpeg PCM audio source
            ffmpeg_options = {
                "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                "options": "-vn",
            }

            source = discord.FFmpegPCMAudio(song["url"], **ffmpeg_options)
            source = discord.PCMVolumeTransformer(source, volume=0.5)

            def after_playing(error: Optional[Exception]) -> None:
                """Callback when a song finishes playing."""
                if error:
                    logger.error(f"Error playing song: {error}")
                # Schedule the next song
                if not self.is_paused:
                    asyncio.run_coroutine_threadsafe(
                        self._play_next(),
                        self.bot.loop,
                    )

            self.voice_client.play(source, after=after_playing)
            self.is_playing = True
            self.is_paused = False
            logger.info(f"Now playing: {song['title']}")

        except Exception as e:
            logger.error(f"Failed to play song: {e}")
            raise

    async def _play_next(self) -> None:
        """Play the next song in the queue."""
        next_song = self.queue.next()
        if next_song:
            await self.play(next_song)
        else:
            self.is_playing = False
            logger.info("Queue empty, stopped playing")

    def pause(self) -> bool:
        """Pause the current song."""
        if self.voice_client and self.voice_client.is_playing() and not self.is_paused:
            self.voice_client.pause()
            self.is_paused = True
            logger.info("Playback paused")
            return True
        return False

    def resume(self) -> bool:
        """Resume paused playback."""
        if self.voice_client and self.voice_client.is_paused():
            self.voice_client.resume()
            self.is_paused = False
            logger.info("Playback resumed")
            return True
        return False

    def stop(self) -> None:
        """Stop playback and clear queue."""
        if self.voice_client:
            self.voice_client.stop()
        self.queue.clear()
        self.is_playing = False
        self.is_paused = False
        logger.info("Playback stopped")


class MusicCog(commands.Cog):
    """Music commands cog for the Discord bot."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.voice_states: dict[int, VoiceState] = {}
        self.ydl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }

    def get_voice_state(self, guild_id: int) -> VoiceState:
        """Get or create a voice state for a guild."""
        if guild_id not in self.voice_states:
            self.voice_states[guild_id] = VoiceState(self.bot)
        return self.voice_states[guild_id]

    async def fetch_video_info(self, url: str) -> Optional[dict]:
        """Fetch video information from YouTube or other supported sites."""
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    return {
                        "title": info.get("title", "Unknown"),
                        "url": info.get("url", url),
                        "duration": info.get("duration", 0),
                        "uploader": info.get("uploader", "Unknown"),
                        "thumbnail": info.get("thumbnail", ""),
                    }
        except Exception as e:
            logger.error(f"Failed to fetch video info: {e}")
        return None

    async def search_youtube(self, query: str) -> Optional[dict]:
        """Search YouTube for a query and return the first result."""
        try:
            search_opts = {
                **self.ydl_opts,
                "default_search": "ytsearch1",
            }
            with yt_dlp.YoutubeDL(search_opts) as ydl:
                info = ydl.extract_info(f"ytsearch:{query}", download=False)
                if info and "entries" in info and info["entries"]:
                    result = info["entries"][0]
                    return {
                        "title": result.get("title", "Unknown"),
                        "url": result.get("url", ""),
                        "duration": result.get("duration", 0),
                        "uploader": result.get("uploader", "Unknown"),
                        "thumbnail": result.get("thumbnail", ""),
                    }
        except Exception as e:
            logger.error(f"Failed to search YouTube: {e}")
        return None

    @commands.command(
        name="join",
        help="Join the voice channel you're in",
        aliases=["connect", "comein"],
    )
    async def join(self, ctx: commands.Context) -> None:
        """Join the user's voice channel."""
        if not ctx.author.voice:
            await ctx.send("❌ You need to be in a voice channel first!")
            return

        if not ctx.author.voice.channel:
            await ctx.send("❌ You're not in a voice channel!")
            return

        voice_state = self.get_voice_state(ctx.guild.id)

        if voice_state.voice_client and voice_state.voice_client.is_connected():
            await ctx.send(f"🎵 Already connected to {voice_state.voice_client.channel.name}")
            return

        connected = await voice_state.connect(ctx.author.voice.channel)
        if connected:
            await ctx.send(f"🔊 Joined {ctx.author.voice.channel.name}")
        else:
            await ctx.send("❌ Failed to join voice channel")

    @commands.command(
        name="leave",
        help="Leave the voice channel",
        aliases=["disconnect", "getout"],
    )
    async def leave(self, ctx: commands.Context) -> None:
        """Leave the current voice channel."""
        voice_state = self.get_voice_state(ctx.guild.id)

        if not voice_state.voice_client or not voice_state.voice_client.is_connected():
            await ctx.send("❌ I'm not in a voice channel!")
            return

        await voice_state.disconnect()
        await ctx.send("👋 Left the voice channel")

    @commands.command(
        name="play",
        help="Play a song from URL or search query",
        aliases=["p"],
    )
    async def play(self, ctx: commands.Context, *, query: str) -> None:
        """Play a song from a URL or search query."""
        voice_state = self.get_voice_state(ctx.guild.id)

        if not voice_state.voice_client or not voice_state.voice_client.is_connected():
            if not ctx.author.voice or not ctx.author.voice.channel:
                await ctx.send("❌ You need to be in a voice channel! Use `!join` first.")
                return
            connected = await voice_state.connect(ctx.author.voice.channel)
            if not connected:
                await ctx.send("❌ Failed to join voice channel")
                return

        await ctx.send(f"🔍 Searching for: {query}...")

        # Check if it's a URL or search query
        if query.startswith(("http://", "https://")):
            song_info = await self.fetch_video_info(query)
        else:
            song_info = await self.search_youtube(query)

        if not song_info:
            await ctx.send("❌ Could not find that song!")
            return

        # Add to queue
        voice_state.queue.add(song_info)
        await ctx.send(f"🎵 Added '{song_info['title']}' to the queue")

        # Start playing if not already playing
        if not voice_state.is_playing:
            next_song = voice_state.queue.next()
            if next_song:
                await ctx.send(f"▶️ Now playing: {next_song['title']}")
                await voice_state.play(next_song)

    @commands.command(
        name="pause",
        help="Pause the current song",
    )
    async def pause(self, ctx: commands.Context) -> None:
        """Pause the current song."""
        voice_state = self.get_voice_state(ctx.guild.id)

        if not voice_state.voice_client or not voice_state.voice_client.is_connected():
            await ctx.send("❌ I'm not in a voice channel!")
            return

        if voice_state.pause():
            await ctx.send("⏸️ Playback paused")
        else:
            await ctx.send("❌ Nothing is playing or already paused")

    @commands.command(
        name="resume",
        help="Resume paused playback",
        aliases=["unpause"],
    )
    async def resume(self, ctx: commands.Context) -> None:
        """Resume paused playback."""
        voice_state = self.get_voice_state(ctx.guild.id)

        if not voice_state.voice_client or not voice_state.voice_client.is_connected():
            await ctx.send("❌ I'm not in a voice channel!")
            return

        if voice_state.resume():
            await ctx.send("▶️ Playback resumed")
        else:
            await ctx.send("❌ Nothing is paused")

    @commands.command(
        name="skip",
        help="Skip the current song",
        aliases=["next"],
    )
    async def skip(self, ctx: commands.Context) -> None:
        """Skip the current song."""
        voice_state = self.get_voice_state(ctx.guild.id)

        if not voice_state.voice_client or not voice_state.voice_client.is_connected():
            await ctx.send("❌ I'm not in a voice channel!")
            return

        if not voice_state.is_playing:
            await ctx.send("❌ Nothing is playing!")
            return

        next_song = voice_state.queue.skip()
        if next_song:
            await ctx.send(f"⏭️ Skipped to: {next_song['title']}")
            await voice_state.play(next_song)
        else:
            voice_state.stop()
            await ctx.send("⏹️ No more songs in queue, stopped playback")

    @commands.command(
        name="stop",
        help="Stop playback and clear queue",
        aliases=["clear"],
    )
    async def stop(self, ctx: commands.Context) -> None:
        """Stop playback and clear the queue."""
        voice_state = self.get_voice_state(ctx.guild.id)

        if not voice_state.voice_client or not voice_state.voice_client.is_connected():
            await ctx.send("❌ I'm not in a voice channel!")
            return

        voice_state.stop()
        await ctx.send("⏹️ Stopped playback and cleared queue")

    @commands.command(
        name="queue",
        help="Show the current song queue",
        aliases=["q"],
    )
    async def queue(self, ctx: commands.Context) -> None:
        """Show the current song queue."""
        voice_state = self.get_voice_state(ctx.guild.id)

        current = voice_state.queue.current
        queue_list = voice_state.queue.queue

        if not current and not queue_list:
            await ctx.send("📭 The queue is empty!")
            return

        embed = discord.Embed(
            title="🎵 Current Queue",
            color=discord.Color.blue(),
        )

        if current:
            embed.add_field(
                name="▶️ Now Playing",
                value=f"**{current['title']}**",
                inline=False,
            )

        if queue_list:
            queue_str = ""
            for i, song in enumerate(queue_list[:10], 1):  # Show max 10 songs
                queue_str += f"{i}. **{song['title']}**\n"
            if len(queue_list) > 10:
                queue_str += f"... and {len(queue_list) - 10} more songs"
            embed.add_field(
                name=f"📋 Upcoming ({len(queue_list)} songs)",
                value=queue_str,
                inline=False,
            )

        await ctx.send(embed=embed)

    @commands.command(
        name="nowplaying",
        help="Show the currently playing song",
        aliases=["np", "current"],
    )
    async def nowplaying(self, ctx: commands.Context) -> None:
        """Show the currently playing song."""
        voice_state = self.get_voice_state(ctx.guild.id)

        current = voice_state.queue.current
        if not current:
            await ctx.send("🎵 Nothing is currently playing!")
            return

        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"**{current['title']}**",
            color=discord.Color.green(),
        )
        if current.get("uploader"):
            embed.add_field(name="Artist", value=current["uploader"], inline=True)
        if current.get("duration"):
            duration = current["duration"]
            minutes = int(duration // 60)
            seconds = int(duration % 60)
            embed.add_field(name="Duration", value=f"{minutes}:{seconds:02d}", inline=True)
        if current.get("thumbnail"):
            embed.set_thumbnail(url=current["thumbnail"])

        await ctx.send(embed=embed)

    @commands.command(
        name="remove",
        help="Remove a song from the queue by index",
    )
    async def remove(self, ctx: commands.Context, index: int) -> None:
        """Remove a song from the queue by index."""
        voice_state = self.get_voice_state(ctx.guild.id)

        if index < 1 or index > voice_state.queue.size:
            await ctx.send(f"❌ Invalid index! Queue has {voice_state.queue.size} songs.")
            return

        removed = voice_state.queue.remove(index - 1)
        if removed:
            await ctx.send(f"🗑️ Removed '{removed['title']}' from the queue")
        else:
            await ctx.send("❌ Could not remove song")

    @commands.command(
        name="volume",
        help="Set the playback volume (0-100)",
        aliases=["vol"],
    )
    async def volume(self, ctx: commands.Context, level: int) -> None:
        """Set the playback volume."""
        voice_state = self.get_voice_state(ctx.guild.id)

        if not voice_state.voice_client or not voice_state.voice_client.is_connected():
            await ctx.send("❌ I'm not in a voice channel!")
            return

        if level < 0 or level > 100:
            await ctx.send("❌ Volume must be between 0 and 100!")
            return

        if voice_state.voice_client.source:
            voice_state.voice_client.source.volume = level / 100
            await ctx.send(f"🔊 Volume set to {level}%")
        else:
            await ctx.send("❌ Nothing is playing!")

    @commands.command(
        name="music",
        help="Display music bot help and commands",
    )
    async def music_help(self, ctx: commands.Context) -> None:
        """Display music bot help information."""
        embed = discord.Embed(
            title="🎵 Music Bot Commands",
            description="Here's how to use the music features:",
            color=discord.Color.purple(),
        )
        embed.add_field(
            name="🔊 Voice Control",
            value=(
                "`!join` - Join your voice channel\n"
                "`!leave` - Leave the voice channel"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎵 Playback Control",
            value=(
                "`!play <URL/search>` - Play a song\n"
                "`!pause` - Pause playback\n"
                "`!resume` - Resume playback\n"
                "`!skip` - Skip to next song\n"
                "`!stop` - Stop and clear queue"
            ),
            inline=False,
        )
        embed.add_field(
            name="📋 Queue Management",
            value=(
                "`!queue` - Show current queue\n"
                "`!nowplaying` - Show current song\n"
                "`!remove <#>` - Remove song from queue\n"
                "`!volume <0-100>` - Set volume"
            ),
            inline=False,
        )
        embed.set_footer(text="Tip: You can use YouTube URLs or just search by song name!")

        await ctx.send(embed=embed)

    async def cog_unload(self) -> None:
        """Cleanup when cog is unloaded."""
        logger.info("Unloading music cog, disconnecting from all voice channels")
        for voice_state in self.voice_states.values():
            await voice_state.disconnect()


def setup(bot: commands.Bot) -> None:
    """Load the music cog."""
    bot.add_cog(MusicCog(bot))
    logger.info("Music cog loaded")
