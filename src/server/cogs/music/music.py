"""
Music cog for Discord AI chatbot.

Provides slash commands for voice channel and music playback.
"""

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
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
        logger.info("Added '%s' to queue", song["title"])

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
        """Get a copy of the queued songs."""
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

    async def connect(self, channel: discord.VoiceChannel) -> bool:
        """Connect to a voice channel."""
        try:
            if self.voice_client and self.voice_client.is_connected():
                await self.voice_client.move_to(channel)
            else:
                self.voice_client = await channel.connect()

            logger.info("Connected to voice channel %s", channel.name)
            return True

        except Exception as e:
            logger.error(
                "Failed to connect to voice channel: %s",
                e,
                exc_info=True,
            )
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
            if self.voice_client.is_playing():
                self.voice_client.stop()

            ffmpeg_options = {
                "before_options": (
                    "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
                ),
                "options": "-vn",
            }

            source = discord.FFmpegPCMAudio(
                song["url"],
                **ffmpeg_options,
            )

            source = discord.PCMVolumeTransformer(
                source,
                volume=0.5,
            )

            def after_playing(error: Optional[Exception]) -> None:
                """Callback when a song finishes playing."""
                if error:
                    logger.error(
                        "Error playing song: %s",
                        error,
                    )

                if not self.is_paused:
                    asyncio.run_coroutine_threadsafe(
                        self._play_next(),
                        self.bot.loop,
                    )

            self.voice_client.play(
                source,
                after=after_playing,
            )

            self.is_playing = True
            self.is_paused = False

            logger.info(
                "Now playing: %s",
                song["title"],
            )

        except Exception as e:
            logger.error(
                "Failed to play song: %s",
                e,
                exc_info=True,
            )
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
    """Music slash-command cog."""

    music = app_commands.Group(
        name="music",
        description="Music playback and voice controls.",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.voice_states: dict[int, VoiceState] = {}

        bot.tree.add_command(self.music)

        self.ydl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }

        super().__init__()

    def get_voice_state(self, guild_id: int) -> VoiceState:
        """Get or create a voice state for a guild."""
        if guild_id not in self.voice_states:
            self.voice_states[guild_id] = VoiceState(self.bot)

        return self.voice_states[guild_id]

    async def fetch_video_info(
        self,
        url: str,
    ) -> Optional[dict]:
        """Fetch video information from a URL."""
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = await asyncio.to_thread(
                    ydl.extract_info,
                    url,
                    download=False,
                )

                if info:
                    return {
                        "title": info.get("title", "Unknown"),
                        "url": info.get("url", url),
                        "duration": info.get("duration", 0),
                        "uploader": info.get("uploader", "Unknown"),
                        "thumbnail": info.get("thumbnail", ""),
                    }

        except Exception as e:
            logger.error(
                "Failed to fetch video info: %s",
                e,
                exc_info=True,
            )

        return None

    async def search_youtube(
        self,
        query: str,
    ) -> Optional[dict]:
        """Search YouTube for a query."""
        try:
            search_opts = {
                **self.ydl_opts,
                "default_search": "ytsearch1",
            }

            with yt_dlp.YoutubeDL(search_opts) as ydl:
                info = await asyncio.to_thread(
                    ydl.extract_info,
                    f"ytsearch:{query}",
                    download=False,
                )

                if info and "entries" in info and info["entries"]:
                    result = info["entries"][0]

                    return {
                        "title": result.get(
                            "title",
                            "Unknown",
                        ),
                        "url": result.get(
                            "url",
                            "",
                        ),
                        "duration": result.get(
                            "duration",
                            0,
                        ),
                        "uploader": result.get(
                            "uploader",
                            "Unknown",
                        ),
                        "thumbnail": result.get(
                            "thumbnail",
                            "",
                        ),
                    }

        except Exception as e:
            logger.error(
                "Failed to search YouTube: %s",
                e,
                exc_info=True,
            )

        return None

    @music.command(
        name="join",
        description="Join the voice channel you're in",
    )
    async def join(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Join the user's voice channel."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return

        if not isinstance(
            interaction.user,
            discord.Member,
        ):
            await interaction.response.send_message(
                "❌ Unable to determine your voice state.",
                ephemeral=True,
            )
            return

        if not interaction.user.voice:
            await interaction.response.send_message(
                "❌ You need to be in a voice channel first!",
                ephemeral=True,
            )
            return

        channel = interaction.user.voice.channel

        if not isinstance(
            channel,
            discord.VoiceChannel,
        ):
            await interaction.response.send_message(
                "❌ You need to be in a regular voice channel.",
                ephemeral=True,
            )
            return

        voice_state = self.get_voice_state(
            interaction.guild.id,
        )

        if voice_state.voice_client and voice_state.voice_client.is_connected():
            await interaction.response.send_message(
                f"🎵 Already connected to {voice_state.voice_client.channel.name}",
            )
            return

        connected = await voice_state.connect(channel)

        if connected:
            await interaction.response.send_message(
                f"🔊 Joined {channel.name}",
            )
        else:
            await interaction.response.send_message(
                "❌ Failed to join voice channel.",
                ephemeral=True,
            )

    @music.command(
        name="leave",
        description="Leave the current voice channel",
    )
    async def leave(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Leave the current voice channel."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return

        voice_state = self.get_voice_state(
            interaction.guild.id,
        )

        if not voice_state.voice_client or not voice_state.voice_client.is_connected():
            await interaction.response.send_message(
                "❌ I'm not in a voice channel!",
                ephemeral=True,
            )
            return

        await voice_state.disconnect()

        await interaction.response.send_message(
            "👋 Left the voice channel.",
        )

    @music.command(
        name="play",
        description="Play a song from a URL or search query",
    )
    @app_commands.describe(
        query="YouTube URL or song search query",
    )
    async def play(
        self,
        interaction: discord.Interaction,
        query: str,
    ) -> None:
        """Play a song from a URL or search query."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return

        if not isinstance(
            interaction.user,
            discord.Member,
        ):
            await interaction.response.send_message(
                "❌ Unable to determine your voice state.",
                ephemeral=True,
            )
            return

        voice_state = self.get_voice_state(
            interaction.guild.id,
        )

        if not voice_state.voice_client or not voice_state.voice_client.is_connected():
            if not interaction.user.voice or not interaction.user.voice.channel:
                await interaction.response.send_message(
                    "❌ You need to be in a voice channel first!",
                    ephemeral=True,
                )
                return

            channel = interaction.user.voice.channel

            if not isinstance(
                channel,
                discord.VoiceChannel,
            ):
                await interaction.response.send_message(
                    "❌ You need to be in a regular voice channel.",
                    ephemeral=True,
                )
                return

            connected = await voice_state.connect(channel)

            if not connected:
                await interaction.response.send_message(
                    "❌ Failed to join voice channel.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer()

        if query.startswith(
            (
                "http://",
                "https://",
            )
        ):
            song_info = await self.fetch_video_info(query)
        else:
            song_info = await self.search_youtube(query)

        if not song_info:
            await interaction.followup.send(
                "❌ Could not find that song!",
            )
            return

        voice_state.queue.add(song_info)

        await interaction.followup.send(
            f"🎵 Added **{song_info['title']}** to the queue.",
        )

        if not voice_state.is_playing:
            next_song = voice_state.queue.next()

            if next_song:
                await voice_state.play(next_song)

                await interaction.followup.send(
                    f"▶️ Now playing: **{next_song['title']}**",
                )

    @music.command(
        name="pause",
        description="Pause the current song",
    )
    async def pause(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Pause the current song."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return

        voice_state = self.get_voice_state(
            interaction.guild.id,
        )

        if not voice_state.voice_client or not voice_state.voice_client.is_connected():
            await interaction.response.send_message(
                "❌ I'm not in a voice channel!",
                ephemeral=True,
            )
            return

        if voice_state.pause():
            await interaction.response.send_message(
                "⏸️ Playback paused.",
            )
        else:
            await interaction.response.send_message(
                "❌ Nothing is playing or playback is already paused.",
                ephemeral=True,
            )

    @music.command(
        name="resume",
        description="Resume paused playback",
    )
    async def resume(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Resume paused playback."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return

        voice_state = self.get_voice_state(
            interaction.guild.id,
        )

        if not voice_state.voice_client or not voice_state.voice_client.is_connected():
            await interaction.response.send_message(
                "❌ I'm not in a voice channel!",
                ephemeral=True,
            )
            return

        if voice_state.resume():
            await interaction.response.send_message(
                "▶️ Playback resumed.",
            )
        else:
            await interaction.response.send_message(
                "❌ Nothing is paused.",
                ephemeral=True,
            )

    @music.command(
        name="skip",
        description="Skip the current song",
    )
    async def skip(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Skip the current song."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return

        voice_state = self.get_voice_state(
            interaction.guild.id,
        )

        if not voice_state.voice_client or not voice_state.voice_client.is_connected():
            await interaction.response.send_message(
                "❌ I'm not in a voice channel!",
                ephemeral=True,
            )
            return

        if not voice_state.is_playing:
            await interaction.response.send_message(
                "❌ Nothing is playing!",
                ephemeral=True,
            )
            return

        next_song = voice_state.queue.skip()

        if next_song:
            await voice_state.play(next_song)

            await interaction.response.send_message(
                f"⏭️ Skipped to: **{next_song['title']}**",
            )
        else:
            voice_state.stop()

            await interaction.response.send_message(
                "⏹️ No more songs in queue, stopped playback.",
            )

    @music.command(
        name="stop",
        description="Stop playback and clear the queue",
    )
    async def stop(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Stop playback and clear queue."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return

        voice_state = self.get_voice_state(
            interaction.guild.id,
        )

        if not voice_state.voice_client or not voice_state.voice_client.is_connected():
            await interaction.response.send_message(
                "❌ I'm not in a voice channel!",
                ephemeral=True,
            )
            return

        voice_state.stop()

        await interaction.response.send_message(
            "⏹️ Stopped playback and cleared queue.",
        )

    @music.command(
        name="queue",
        description="Show the current song queue",
    )
    async def queue(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Show the current song queue."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return

        voice_state = self.get_voice_state(
            interaction.guild.id,
        )

        current = voice_state.queue.current
        queue_list = voice_state.queue.queue

        if not current and not queue_list:
            await interaction.response.send_message(
                "📭 The queue is empty!",
                ephemeral=True,
            )
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

            for i, song in enumerate(
                queue_list[:10],
                1,
            ):
                queue_str += f"{i}. **{song['title']}**\n"

            if len(queue_list) > 10:
                queue_str += f"... and {len(queue_list) - 10} more songs"

            embed.add_field(
                name=(f"📋 Upcoming ({len(queue_list)} songs)"),
                value=queue_str,
                inline=False,
            )

        await interaction.response.send_message(
            embed=embed,
        )

    @music.command(
        name="nowplaying",
        description="Show the currently playing song",
    )
    async def nowplaying(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Show the currently playing song."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return

        voice_state = self.get_voice_state(
            interaction.guild.id,
        )

        current = voice_state.queue.current

        if not current:
            await interaction.response.send_message(
                "🎵 Nothing is currently playing!",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"**{current['title']}**",
            color=discord.Color.green(),
        )

        if current.get("uploader"):
            embed.add_field(
                name="Artist",
                value=current["uploader"],
                inline=True,
            )

        if current.get("duration"):
            duration = current["duration"]

            minutes = int(duration // 60)
            seconds = int(duration % 60)

            embed.add_field(
                name="Duration",
                value=f"{minutes}:{seconds:02d}",
                inline=True,
            )

        if current.get("thumbnail"):
            embed.set_thumbnail(
                url=current["thumbnail"],
            )

        await interaction.response.send_message(
            embed=embed,
        )

    @music.command(
        name="remove",
        description="Remove a song from the queue",
    )
    @app_commands.describe(
        index="The queue position to remove",
    )
    async def remove(
        self,
        interaction: discord.Interaction,
        index: int,
    ) -> None:
        """Remove a song from the queue."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return

        voice_state = self.get_voice_state(
            interaction.guild.id,
        )

        if index < 1 or index > voice_state.queue.size:
            await interaction.response.send_message(
                f"❌ Invalid index! Queue has {voice_state.queue.size} songs.",
                ephemeral=True,
            )
            return

        removed = voice_state.queue.remove(
            index - 1,
        )

        if removed:
            await interaction.response.send_message(
                f"🗑️ Removed **{removed['title']}** from the queue.",
            )
        else:
            await interaction.response.send_message(
                "❌ Could not remove song.",
                ephemeral=True,
            )

    @music.command(
        name="volume",
        description="Set the playback volume",
    )
    @app_commands.describe(
        level="Volume percentage from 0 to 100",
    )
    async def volume(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 0, 100],
    ) -> None:
        """Set the playback volume."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return

        voice_state = self.get_voice_state(
            interaction.guild.id,
        )

        if not voice_state.voice_client or not voice_state.voice_client.is_connected():
            await interaction.response.send_message(
                "❌ I'm not in a voice channel!",
                ephemeral=True,
            )
            return

        if voice_state.voice_client.source:
            source = voice_state.voice_client.source

            if isinstance(
                source,
                discord.PCMVolumeTransformer,
            ):
                source.volume = level / 100

                await interaction.response.send_message(
                    f"🔊 Volume set to {level}%.",
                )
            else:
                await interaction.response.send_message(
                    "❌ Unable to change the current audio volume.",
                    ephemeral=True,
                )
        else:
            await interaction.response.send_message(
                "❌ Nothing is playing!",
                ephemeral=True,
            )

    async def cog_unload(self) -> None:
        """Cleanup when cog is unloaded."""
        logger.info(
            "Unloading music cog, disconnecting from all voice channels",
        )

        for voice_state in self.voice_states.values():
            await voice_state.disconnect()


async def setup(bot: commands.Bot) -> None:
    """Load the MusicCog cog."""
    await bot.add_cog(MusicCog(bot))
