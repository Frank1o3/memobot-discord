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
from typing import TYPE_CHECKING

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

    Locking notes
    -------------
    ``self._lock`` is a plain (non-reentrant) asyncio.Lock used only for
    queue mutations.  Callers that need to mutate the queue and then call
    ``play()`` must release the lock first; ``play()`` does NOT acquire the
    lock itself so it can be called from both locked and unlocked contexts.

    ``self._playback_generation`` is a monotonically increasing counter
    incremented at the start of every ``play()`` call.  The ``after_playing``
    FFmpeg callback captures the generation at the time of the call; when the
    resulting ``_on_track_finished`` coroutine runs it bails out immediately if
    the generation no longer matches — preventing a stale callback from
    advancing the queue after a manual skip/previous/play call has already
    moved on.
    """

    def __init__(self, bot: commands.Bot, cog: MusicCog):
        """
        Initialize the guild player.

        Args:
            bot: The Discord bot instance.
            cog: The music cog instance.
        """
        self._bot = bot
        self._cog = cog
        self._voice_client: discord.VoiceClient | None = None

        # Track management
        self._queue: deque[Track] = deque()
        self._history: deque[Track] = deque(maxlen=50)  # Keep last 50 tracks
        self._current_track: Track | None = None

        # Playback state
        self._state = "stopped"  # stopped, playing, paused
        self._volume = 100  # 0-100
        self._repeat_mode = RepeatMode.OFF

        # Player message tracking
        self._player_message: discord.Message | None = None

        # Lock for queue-mutation critical sections only.
        # play() must NOT be called while holding this lock.
        self._lock = asyncio.Lock()

        # Playback generation counter — incremented on every play() call.
        # Captured by after_playing closure; _on_track_finished bails if stale.
        self._playback_generation: int = 0

    @property
    def voice_client(self) -> discord.VoiceClient | None:
        """Get the voice client."""
        return self._voice_client

    @property
    def current_track(self) -> Track | None:
        """Get the currently playing track."""
        return self._current_track

    @property
    def queue(self) -> deque[Track]:
        """Get the upcoming queue."""
        return self._queue

    @property
    def history(self) -> deque[Track]:
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
        Play a track immediately, replacing any currently playing audio.

        Increments ``_playback_generation`` so that any in-flight
        ``after_playing`` callback from the previous track is invalidated and
        will not advance the queue when it eventually fires.

        Args:
            track: The track to play.

        Returns:
            True if playback started successfully.
        """
        if not self._voice_client:
            logger.warning("Cannot play: not connected to voice channel")
            return False

        try:
            # Increment generation BEFORE stopping the old source so that
            # the old after_playing callback captures the stale value.
            self._playback_generation += 1
            captured_generation = self._playback_generation

            # Stop current playback if any (fires after_playing with stale gen)
            if self._voice_client.is_playing() or self._voice_client.is_paused():
                self._voice_client.stop()

            # Create audio source
            ffmpeg_options = {
                "before_options": (
                    "-reconnect 1 "
                    "-reconnect_streamed 1 "
                    "-reconnect_on_network_error 1 "
                    "-reconnect_on_http_error 4xx,5xx "
                    "-reconnect_at_eof 1 "
                    "-reconnect_delay_max 5 "
                    "-rw_timeout 15000000"
                ),
                "options": "-vn",
            }

            source = discord.FFmpegPCMAudio(
                track.stream_url,
                **ffmpeg_options,
            )

            # Apply volume
            source = discord.PCMVolumeTransformer(source, volume=self._volume / 100)

            def after_playing(error: Exception | None) -> None:
                """
                Callback invoked by discord.py's voice thread when a track ends.

                Runs in discord.py's audio thread — NOT the asyncio event loop.
                Uses run_coroutine_threadsafe to schedule work back on the loop.
                The captured_generation check prevents a stale callback (from a
                track that was stopped by skip/previous/play) from advancing the
                queue.
                """
                if error:
                    logger.error("Error playing track: %s", error)

                # Bail if this callback is stale (play() was called again since)
                if captured_generation != self._playback_generation:
                    logger.debug(
                        "after_playing: stale generation %d (current %d), ignoring",
                        captured_generation,
                        self._playback_generation,
                    )
                    return

                # Schedule next track on the event loop
                if self._state != "paused":
                    asyncio.run_coroutine_threadsafe(
                        self._on_track_finished(captured_generation),
                        self._bot.loop,
                    )

            self._voice_client.play(source, after=after_playing)

            # Update state
            self._current_track = track
            self._state = "playing"

            logger.info("Now playing: %s by %s", track.title, track.artist or "Unknown")

            # Update player message (fire-and-forget)
            asyncio.create_task(self.update_player_message())

            return True

        except Exception as e:
            logger.error("Failed to play track: %s", e, exc_info=True)
            return False

    async def _on_track_finished(self, generation: int) -> None:
        """
        Handle track completion — advance to the next track.

        Guards against the stale-callback race by comparing ``generation``
        with the current ``_playback_generation``.

        Queue mutations are performed inside ``self._lock``; ``play()`` is
        called *outside* the lock to avoid a deadlock (asyncio.Lock is not
        reentrant).
        """
        # Guard: bail if a newer play() call has already taken over
        if generation != self._playback_generation:
            logger.debug(
                "_on_track_finished: stale generation %d (current %d), bailing",
                generation,
                self._playback_generation,
            )
            return

        next_track: Track | None = None

        async with self._lock:
            if self._state == "paused":
                return

            # Handle repeat modes
            if self._repeat_mode == RepeatMode.TRACK and self._current_track:
                next_track = self._current_track
            else:
                if self._repeat_mode == RepeatMode.QUEUE and self._current_track:
                    # Move current track to the back of the queue
                    self._queue.append(self._current_track)

                # Archive current to history
                if self._current_track:
                    self._history.append(self._current_track)

                # Pop the next track
                if self._queue:
                    next_track = self._queue.popleft()

        # Play outside the lock — play() is not reentrant-safe with the lock
        if next_track is not None:
            await self.play(next_track)
        else:
            self._state = "stopped"
            self._current_track = None
            logger.info("Queue empty, stopping playback")
            await self.update_player_message()

    async def play_next(self) -> bool:
        """
        Skip to the next track.

        Returns:
            True if there was a next track to play.
        """
        next_track: Track | None = None

        async with self._lock:
            # Archive current to history
            if self._current_track:
                self._history.append(self._current_track)

            if self._queue:
                next_track = self._queue.popleft()

        # Call play() outside the lock
        if next_track is not None:
            await self.play(next_track)
            return True

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
        previous_track: Track | None = None

        async with self._lock:
            if not self._history:
                return False

            # Return current track to the front of the queue
            if self._current_track:
                self._queue.appendleft(self._current_track)

            previous_track = self._history.pop()

        # Call play() outside the lock
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
        # Increment generation so any pending after_playing callback is invalidated
        self._playback_generation += 1

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
        Cycle through repeat modes: OFF → TRACK → QUEUE → OFF.

        Returns:
            The new repeat mode value string.
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

    def remove_from_queue(self, index: int) -> Track | None:
        """
        Remove a track from the queue by index and return it.

        Args:
            index: Zero-based index in the upcoming queue.

        Returns:
            The removed track, or None if the index is out of range.
        """
        if 0 <= index < len(self._queue):
            # Convert to list, remove, rebuild deque
            queue_list = list(self._queue)
            removed = queue_list.pop(index)
            self._queue = deque(queue_list)
            return removed
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
