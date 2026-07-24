"""
Guild player for managing voice playback state.

Handles:
- Voice client management
- Track lifecycle (current, queue, history)
- Playback state (playing, paused, stopped)
- Volume control
- Repeat modes
- Previous/next track navigation
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import deque
from enum import Enum
from typing import Optional, TYPE_CHECKING, Deque

import discord
from discord.ext import commands

from .sources.base import Track

if TYPE_CHECKING:
    from .music import MusicCog

logger = logging.getLogger(__name__)


class RepeatMode(Enum):
    """Repeat mode for playback."""

    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"


class GuildPlayer:
    """
    Manages music playback for a single guild.
    
    Each guild has its own player instance with independent:
    - Voice connection
    - Queue
    - Playback history
    - Volume
    - Repeat mode
    """

    def __init__(self, bot: commands.Bot, cog: "MusicCog"):
        """
        Initialize the guild player.
        
        Args:
            bot: The Discord bot instance.
            cog: The music cog instance.
        """
        self._bot = bot
        self._cog = cog
        self._voice_client: Optional[discord.VoiceClient] = None
        
        # Track management
        self._queue: deque[Track] = deque()
        self._history: Deque[Track] = deque(maxlen=50)  # Keep last 50 tracks
        self._current_track: Optional[Track] = None
        
        # Playback state
        self._state = "stopped"  # stopped, playing, paused
        self._volume = 50  # 0-100
        self._repeat_mode = RepeatMode.OFF
        
        # Player message tracking
        self._player_message: Optional[discord.Message] = None
        
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

    @property
    def voice_client(self) -> Optional[discord.VoiceClient]:
        """Get the voice client."""
        return self._voice_client

    @property
    def current_track(self) -> Optional[Track]:
        """Get the currently playing track."""
        return self._current_track

    @property
    def queue(self) -> deque[Track]:
        """Get the upcoming queue."""
        return self._queue

    @property
    def history(self) -> Deque[Track]:
        """Get the playback history."""
        return self._history

    @property
    def state(self) -> str:
        """Get the current playback state."""
        return self._state

    @property
    def volume(self) -> int:
        """Get the current volume level."""
        return self._volume

    @property
    def repeat_mode(self) -> str:
        """Get the current repeat mode."""
        return self._repeat_mode.value

    async def connect(self, channel: discord.VoiceChannel) -> bool:
        """
        Connect to a voice channel.
        
        Args:
            channel: The voice channel to connect to.
            
        Returns:
            True if successful, False otherwise.
        """
        try:
            if self._voice_client and self._voice_client.is_connected():
                await self._voice_client.move_to(channel)
            else:
                self._voice_client = await channel.connect()

            logger.info("Player connected to voice channel %s", channel.name)
            return True

        except Exception as e:
            logger.error("Failed to connect to voice channel: %s", e, exc_info=True)
            return False

    async def disconnect(self) -> None:
        """Disconnect from voice channel and clear state."""
        if self._voice_client:
            await self._voice_client.disconnect()
            self._voice_client = None

        self._queue.clear()
        self._history.clear()
        self._current_track = None
        self._state = "stopped"
        self._player_message = None

        logger.info("Player disconnected from voice channel")

    async def play(self, track: Track) -> bool:
        """
        Play a track.
        
        Args:
            track: The track to play.
            
        Returns:
            True if playback started successfully.
        """
        if not self._voice_client:
            logger.warning("Cannot play: not connected to voice channel")
            return False

        async with self._lock:
            try:
                # Stop current playback if any
                if self._voice_client.is_playing():
                    self._voice_client.stop()

                # Create audio source
                ffmpeg_options = {
                    "before_options": (
                        "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
                    ),
                    "options": "-vn",
                }

                source = discord.FFmpegPCMAudio(
                    track.stream_url,
                    **ffmpeg_options,
                )

                # Apply volume
                source = discord.PCMVolumeTransformer(source, volume=self._volume / 100)

                def after_playing(error: Optional[Exception]) -> None:
                    """Callback when track finishes."""
                    if error:
                        logger.error("Error playing track: %s", error)

                    # Schedule next track in event loop
                    if not self._state == "paused":
                        asyncio.run_coroutine_threadsafe(
                            self._on_track_finished(),
                            self._bot.loop,
                        )

                self._voice_client.play(source, after=after_playing)
                
                # Update state
                self._current_track = track
                self._state = "playing"

                logger.info("Now playing: %s by %s", track.title, track.artist or "Unknown")

                # Update player message
                asyncio.create_task(self.update_player_message())

                return True

            except Exception as e:
                logger.error("Failed to play track: %s", e, exc_info=True)
                return False

    async def _on_track_finished(self) -> None:
        """Handle track completion."""
        async with self._lock:
            if self._state == "paused":
                return

            # Handle repeat modes
            if self._repeat_mode == RepeatMode.TRACK:
                # Replay current track
                if self._current_track:
                    await self.play(self._current_track)
                    return

            if self._repeat_mode == RepeatMode.QUEUE:
                # Move current to end of queue
                if self._current_track:
                    self._queue.append(self._current_track)

            # Add current to history before getting next
            if self._current_track:
                self._history.append(self._current_track)

            # Get next track
            if self._queue:
                next_track = self._queue.popleft()
                await self.play(next_track)
            else:
                # Queue empty
                self._state = "stopped"
                self._current_track = None
                logger.info("Queue empty, stopping playback")
                await self.update_player_message()

    async def play_next(self) -> bool:
        """
        Skip to the next track.
        
        Returns:
            True if there was a next track.
        """
        async with self._lock:
            # Add current to history
            if self._current_track:
                self._history.append(self._current_track)

            if self._queue:
                next_track = self._queue.popleft()
                await self.play(next_track)
                return True

            # No more tracks
            self._state = "stopped"
            self._current_track = None
            await self.update_player_message()
            return False

    async def play_previous(self) -> bool:
        """
        Play the previous track from history.
        
        Returns:
            True if there was a previous track.
        """
        async with self._lock:
            if not self._history:
                return False

            # Add current back to queue front
            if self._current_track:
                self._queue.appendleft(self._current_track)

            # Get previous from history
            previous_track = self._history.pop()
            await self.play(previous_track)
            return True

    def pause(self) -> bool:
        """
        Pause playback.
        
        Returns:
            True if paused successfully.
        """
        if self._voice_client and self._state == "playing":
            self._voice_client.pause()
            self._state = "paused"
            logger.info("Playback paused")
            return True
        return False

    def resume(self) -> bool:
        """
        Resume paused playback.
        
        Returns:
            True if resumed successfully.
        """
        if self._voice_client and self._state == "paused":
            self._voice_client.resume()
            self._state = "playing"
            logger.info("Playback resumed")
            return True
        return False

    def stop(self) -> None:
        """Stop playback and clear queue."""
        if self._voice_client:
            self._voice_client.stop()

        self._queue.clear()
        self._history.clear()
        self._current_track = None
        self._state = "stopped"

        logger.info("Playback stopped")

    def set_volume(self, level: int) -> None:
        """
        Set playback volume.
        
        Args:
            level: Volume level 0-100.
        """
        self._volume = max(0, min(100, level))

        # Update current source volume if playing
        if self._voice_client and self._voice_client.source:
            source = self._voice_client.source
            if isinstance(source, discord.PCMVolumeTransformer):
                source.volume = self._volume / 100

        logger.info("Volume set to %d%%", self._volume)

    def shuffle_queue(self) -> None:
        """Shuffle the upcoming queue."""
        queue_list = list(self._queue)
        random.shuffle(queue_list)
        self._queue = deque(queue_list)
        logger.info("Queue shuffled (%d tracks)", len(self._queue))

    def toggle_repeat(self) -> str:
        """
        Cycle through repeat modes.
        
        Returns:
            The new repeat mode.
        """
        modes = [RepeatMode.OFF, RepeatMode.TRACK, RepeatMode.QUEUE]
        current_index = modes.index(self._repeat_mode)
        next_index = (current_index + 1) % len(modes)
        self._repeat_mode = modes[next_index]

        logger.info("Repeat mode set to %s", self._repeat_mode.value)
        return self._repeat_mode.value

    def add_to_queue(self, track: Track) -> None:
        """
        Add a track to the queue.
        
        Args:
            track: The track to add.
        """
        self._queue.append(track)
        logger.info("Added '%s' to queue", track.title)

    def add_tracks_to_queue(self, tracks: list[Track]) -> None:
        """
        Add multiple tracks to the queue.
        
        Args:
            tracks: List of tracks to add.
        """
        for track in tracks:
            self._queue.append(track)
        logger.info("Added %d tracks to queue", len(tracks))

    def remove_from_queue(self, index: int) -> Optional[Track]:
        """
        Remove a track from the queue by index.
        
        Args:
            index: Zero-based index in the queue.
            
        Returns:
            The removed track, or None if index invalid.
        """
        if 0 <= index < len(self._queue):
            return self._queue[index]
        return None

    def clear_queue(self) -> None:
        """Clear the upcoming queue."""
        self._queue.clear()
        logger.info("Queue cleared")

    def set_player_message(self, message: discord.Message) -> None:
        """Set the persistent player message reference."""
        self._player_message = message

    async def update_player_message(self) -> None:
        """Update the player message embed."""
        if not self._player_message:
            return

        try:
            embed = self.build_embed()
            view = self._cog.get_player_view(self._player_message.guild.id)
            
            await self._player_message.edit(embed=embed, view=view)
        except discord.NotFound:
            # Message was deleted
            self._player_message = None
        except Exception as e:
            logger.debug("Failed to update player message: %s", e)

    def build_embed(self) -> discord.Embed:
        """Build the player embed."""
        from .ui.embeds import PlayerEmbed
        builder = PlayerEmbed()
        return builder.build(self)

    def build_queue_embed(self) -> discord.Embed:
        """Build the queue embed."""
        from .ui.embeds import PlayerEmbed
        return PlayerEmbed.build_queue_embed(self)
