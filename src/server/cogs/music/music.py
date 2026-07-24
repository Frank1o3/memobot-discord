"""
Music cog for Discord AI chatbot.

Provides slash commands for voice channel and music playback.
Uses GuildPlayer for per-guild playback state management.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from .player import GuildPlayer
from .sources.resolver import SourceResolver, SourceType, ExtractionResult
from .ui.player_view import PlayerView
from .ui.embeds import PlayerEmbed

logger = logging.getLogger(__name__)

# YouTube playlist URL pattern — used to hint users toward /music playlist
_PLAYLIST_URL_HINTS = ("list=", "playlist?", "/playlist/")


def _looks_like_playlist(query: str) -> bool:
    """Return True if the query looks like a multi-track playlist URL."""
    return any(hint in query for hint in _PLAYLIST_URL_HINTS)


class MusicCog(commands.Cog):
    """Music slash-command cog using GuildPlayer architecture."""

    music = app_commands.Group(
        name="music",
        description="Music playback and voice controls.",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._players: dict[int, GuildPlayer] = {}
        self._resolver = SourceResolver()

        bot.tree.add_command(self.music)

        super().__init__()

    def get_player(self, guild_id: int) -> GuildPlayer:
        """Get or create a guild player instance."""
        if guild_id not in self._players:
            self._players[guild_id] = GuildPlayer(self.bot, self)
        return self._players[guild_id]

    def get_player_view(self, guild_id: int) -> PlayerView:
        """Get the player view for button interactions."""
        return PlayerView(self, guild_id)

    # ------------------------------------------------------------------
    # Helper: validate guild + member context and return (guild, member)
    # ------------------------------------------------------------------

    async def _check_guild_member(
        self,
        interaction: discord.Interaction,
    ) -> tuple[discord.Guild, discord.Member] | None:
        """
        Validate that the interaction is from a guild member.

        Returns (guild, member) on success, or None after sending an error reply.
        """
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return None

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ Unable to determine your voice state.",
                ephemeral=True,
            )
            return None

        return interaction.guild, interaction.user

    async def _ensure_voice_connected(
        self,
        interaction: discord.Interaction,
        player: GuildPlayer,
        member: discord.Member,
        *,
        already_responded: bool = False,
    ) -> bool:
        """
        Ensure the player is connected to a voice channel.

        If not connected, attempts to join the member's current voice channel.
        Returns True if connected (or already was), False after sending an error.
        """
        if player.voice_client and player.voice_client.is_connected():
            return True

        if not member.voice or not member.voice.channel:
            msg = "❌ You need to be in a voice channel first!"
            if already_responded:
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return False

        channel = member.voice.channel
        if not isinstance(channel, discord.VoiceChannel):
            msg = "❌ You need to be in a regular voice channel."
            if already_responded:
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return False

        connected = await player.connect(channel)
        if not connected:
            msg = "❌ Failed to join voice channel."
            if already_responded:
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return False

        return True

    # ------------------------------------------------------------------
    # /music join
    # ------------------------------------------------------------------

    @music.command(
        name="join",
        description="Join the voice channel you're in",
    )
    async def join(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Join the user's voice channel."""
        ctx = await self._check_guild_member(interaction)
        if ctx is None:
            return
        guild, member = ctx

        if not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                "❌ You need to be in a voice channel first!",
                ephemeral=True,
            )
            return

        channel = member.voice.channel
        if not isinstance(channel, discord.VoiceChannel):
            await interaction.response.send_message(
                "❌ You need to be in a regular voice channel.",
                ephemeral=True,
            )
            return

        player = self.get_player(guild.id)

        if player.voice_client and player.voice_client.is_connected():
            await interaction.response.send_message(
                f"🎵 Already connected to {player.voice_client.channel.name}",
            )
            return

        connected = await player.connect(channel)

        if connected:
            await interaction.response.send_message(f"🔊 Joined {channel.name}")
        else:
            await interaction.response.send_message(
                "❌ Failed to join voice channel.",
                ephemeral=True,
            )

    # ------------------------------------------------------------------
    # /music leave
    # ------------------------------------------------------------------

    @music.command(
        name="leave",
        description="Leave the current voice channel",
    )
    async def leave(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Leave the current voice channel."""
        ctx = await self._check_guild_member(interaction)
        if ctx is None:
            return
        guild, _ = ctx

        player = self.get_player(guild.id)

        if not player.voice_client or not player.voice_client.is_connected():
            await interaction.response.send_message(
                "❌ I'm not in a voice channel!",
                ephemeral=True,
            )
            return

        await player.disconnect()
        await interaction.response.send_message("👋 Left the voice channel.")

    # ------------------------------------------------------------------
    # /music play
    # ------------------------------------------------------------------

    @music.command(
        name="play",
        description="Play a song from a URL or search query (single tracks only — use /music playlist for playlists)",
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
        ctx = await self._check_guild_member(interaction)
        if ctx is None:
            return
        guild, member = ctx

        # Detect playlist URLs early and redirect before doing any work
        if _looks_like_playlist(query):
            await interaction.response.send_message(
                "📋 That looks like a playlist URL! "
                "Use `/music playlist <url>` to load an entire playlist.",
                ephemeral=True,
            )
            return

        player = self.get_player(guild.id)

        # Auto-join if not connected
        if not player.voice_client or not player.voice_client.is_connected():
            if not await self._ensure_voice_connected(interaction, player, member):
                return

        await interaction.response.defer()

        # Resolve the query
        result: ExtractionResult = await self._resolver.resolve(
            query,
            requested_by=member,
        )

        if not result.tracks:
            error_detail = f": {result.errors[0]}" if result.errors else ""
            await interaction.followup.send(
                f"❌ Could not find that song{error_detail}",
            )
            return

        # If we got multiple tracks (shouldn't happen via /play, but be safe)
        if len(result.tracks) > 1:
            await interaction.followup.send(
                "📋 Multiple tracks detected. Use `/music playlist <url>` to load a playlist.",
                ephemeral=True,
            )
            return

        track = result.tracks[0]
        player.add_to_queue(track)

        was_idle = player.state == "stopped"

        if was_idle:
            # Start playback immediately
            success = await player.play(track)
            # Remove from queue since play() doesn't pop it
            if success and track in player.queue:
                player.queue.remove(track)

            if success:
                embed = player.build_embed()
                view = self.get_player_view(guild.id)
                msg = await interaction.followup.send(embed=embed, view=view)
                player.set_player_message(msg)
            else:
                await interaction.followup.send(
                    f"❌ Failed to start playback of **{track.title}**.",
                )
        else:
            await interaction.followup.send(
                f"📋 Added **{track.title}** to queue "
                f"(position {len(player.queue)}).",
            )

    # ------------------------------------------------------------------
    # /music playlist
    # ------------------------------------------------------------------

    @music.command(
        name="playlist",
        description="Load an entire playlist from a URL",
    )
    @app_commands.describe(
        url="YouTube playlist URL",
    )
    async def playlist(
        self,
        interaction: discord.Interaction,
        url: str,
    ) -> None:
        """Load a YouTube (or Spotify) playlist into the queue."""
        ctx = await self._check_guild_member(interaction)
        if ctx is None:
            return
        guild, member = ctx

        player = self.get_player(guild.id)

        # Auto-join if not connected
        if not player.voice_client or not player.voice_client.is_connected():
            if not await self._ensure_voice_connected(interaction, player, member):
                return

        await interaction.response.defer()

        tracks_added = 0
        total_tracks = 0
        last_progress_update = 0

        async def progress_callback(added: int, total: int) -> None:
            """Send periodic progress updates to the deferred response."""
            nonlocal last_progress_update
            last_progress_update = added
            try:
                await interaction.edit_original_response(
                    content=f"⏳ Loading playlist... Added **{added}/{total}** tracks.",
                )
            except Exception:
                pass  # Ignore transient edit failures

        result: ExtractionResult = await self._resolver.extract_playlist_concurrent(
            url,
            requested_by=member,
            progress_callback=progress_callback,
        )

        if not result.tracks:
            error_detail = f": {result.errors[0]}" if result.errors else ""
            await interaction.edit_original_response(
                content=f"❌ Could not load playlist{error_detail}",
            )
            return

        player.add_tracks_to_queue(result.tracks)
        total_tracks = len(result.tracks)

        was_idle = player.state == "stopped"
        if was_idle and player.queue:
            first_track = player.queue.popleft()
            await player.play(first_track)

        title_str = (
            f"**{result.playlist_title}**" if result.playlist_title else "playlist"
        )
        error_str = (
            f"\n⚠️ {len(result.errors)} track(s) failed to load."
            if result.errors
            else ""
        )

        embed = discord.Embed(
            title="📋 Playlist Loaded",
            description=(
                f"Added **{total_tracks}** tracks from {title_str} to the queue.{error_str}"
            ),
            color=discord.Color.green(),
        )

        if result.playlist_title:
            embed.set_footer(text=result.playlist_title)

        # Also update the player message if one exists
        await player.update_player_message()

        await interaction.edit_original_response(content=None, embed=embed)

    # ------------------------------------------------------------------
    # /music pause
    # ------------------------------------------------------------------

    @music.command(
        name="pause",
        description="Pause the current song",
    )
    async def pause(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Pause the current song."""
        ctx = await self._check_guild_member(interaction)
        if ctx is None:
            return
        guild, _ = ctx

        player = self.get_player(guild.id)

        if not player.voice_client or not player.voice_client.is_connected():
            await interaction.response.send_message(
                "❌ I'm not in a voice channel!",
                ephemeral=True,
            )
            return

        if player.pause():
            await interaction.response.send_message("⏸️ Playback paused.")
        else:
            await interaction.response.send_message(
                "❌ Nothing is playing or playback is already paused.",
                ephemeral=True,
            )

    # ------------------------------------------------------------------
    # /music resume
    # ------------------------------------------------------------------

    @music.command(
        name="resume",
        description="Resume paused playback",
    )
    async def resume(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Resume paused playback."""
        ctx = await self._check_guild_member(interaction)
        if ctx is None:
            return
        guild, _ = ctx

        player = self.get_player(guild.id)

        if not player.voice_client or not player.voice_client.is_connected():
            await interaction.response.send_message(
                "❌ I'm not in a voice channel!",
                ephemeral=True,
            )
            return

        if player.resume():
            await interaction.response.send_message("▶️ Playback resumed.")
        else:
            await interaction.response.send_message(
                "❌ Nothing is paused.",
                ephemeral=True,
            )

    # ------------------------------------------------------------------
    # /music skip
    # ------------------------------------------------------------------

    @music.command(
        name="skip",
        description="Skip the current song",
    )
    async def skip(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Skip the current song."""
        ctx = await self._check_guild_member(interaction)
        if ctx is None:
            return
        guild, _ = ctx

        player = self.get_player(guild.id)

        if not player.voice_client or not player.voice_client.is_connected():
            await interaction.response.send_message(
                "❌ I'm not in a voice channel!",
                ephemeral=True,
            )
            return

        if player.state == "stopped" or not player.current_track:
            await interaction.response.send_message(
                "❌ Nothing is playing!",
                ephemeral=True,
            )
            return

        had_next = await player.play_next()

        if had_next:
            await interaction.response.send_message(
                f"⏭️ Skipped! Now playing: **{player.current_track.title if player.current_track else 'Unknown'}**",
            )
        else:
            await interaction.response.send_message(
                "⏹️ No more songs in queue, stopped playback.",
            )

    # ------------------------------------------------------------------
    # /music stop
    # ------------------------------------------------------------------

    @music.command(
        name="stop",
        description="Stop playback and clear the queue",
    )
    async def stop(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Stop playback and clear queue."""
        ctx = await self._check_guild_member(interaction)
        if ctx is None:
            return
        guild, _ = ctx

        player = self.get_player(guild.id)

        if not player.voice_client or not player.voice_client.is_connected():
            await interaction.response.send_message(
                "❌ I'm not in a voice channel!",
                ephemeral=True,
            )
            return

        player.stop()
        await interaction.response.send_message("⏹️ Stopped playback and cleared queue.")

    # ------------------------------------------------------------------
    # /music queue
    # ------------------------------------------------------------------

    @music.command(
        name="queue",
        description="Show the current song queue",
    )
    async def queue(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Show the current song queue."""
        ctx = await self._check_guild_member(interaction)
        if ctx is None:
            return
        guild, _ = ctx

        player = self.get_player(guild.id)

        if not player.current_track and not player.queue:
            await interaction.response.send_message(
                "📭 The queue is empty!",
                ephemeral=True,
            )
            return

        embed = player.build_queue_embed()
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # /music nowplaying
    # ------------------------------------------------------------------

    @music.command(
        name="nowplaying",
        description="Show the currently playing song",
    )
    async def nowplaying(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Show the currently playing song."""
        ctx = await self._check_guild_member(interaction)
        if ctx is None:
            return
        guild, _ = ctx

        player = self.get_player(guild.id)

        if not player.current_track:
            await interaction.response.send_message(
                "🎵 Nothing is currently playing!",
                ephemeral=True,
            )
            return

        embed = player.build_embed()
        view = self.get_player_view(guild.id)
        await interaction.response.send_message(embed=embed, view=view)

    # ------------------------------------------------------------------
    # /music remove
    # ------------------------------------------------------------------

    @music.command(
        name="remove",
        description="Remove a song from the queue by position",
    )
    @app_commands.describe(
        index="The 1-based queue position to remove",
    )
    async def remove(
        self,
        interaction: discord.Interaction,
        index: int,
    ) -> None:
        """Remove a song from the queue."""
        ctx = await self._check_guild_member(interaction)
        if ctx is None:
            return
        guild, _ = ctx

        player = self.get_player(guild.id)
        queue_size = len(player.queue)

        if queue_size == 0:
            await interaction.response.send_message(
                "📭 The queue is empty!",
                ephemeral=True,
            )
            return

        if index < 1 or index > queue_size:
            await interaction.response.send_message(
                f"❌ Invalid index! Queue has {queue_size} upcoming song(s).",
                ephemeral=True,
            )
            return

        removed = player.remove_from_queue(index - 1)  # Convert to 0-based

        if removed:
            await interaction.response.send_message(
                f"🗑️ Removed **{removed.title}** from the queue.",
            )
        else:
            await interaction.response.send_message(
                "❌ Could not remove song.",
                ephemeral=True,
            )

    # ------------------------------------------------------------------
    # /music volume
    # ------------------------------------------------------------------

    @music.command(
        name="volume",
        description="Set the playback volume (0–100)",
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
        ctx = await self._check_guild_member(interaction)
        if ctx is None:
            return
        guild, _ = ctx

        player = self.get_player(guild.id)

        if not player.voice_client or not player.voice_client.is_connected():
            await interaction.response.send_message(
                "❌ I'm not in a voice channel!",
                ephemeral=True,
            )
            return

        player.set_volume(level)
        await interaction.response.send_message(f"🔊 Volume set to **{level}%**.")

    # ------------------------------------------------------------------
    # Cog lifecycle
    # ------------------------------------------------------------------

    async def cog_unload(self) -> None:
        """Cleanup when cog is unloaded — disconnect all voice clients."""
        logger.info("Unloading music cog, disconnecting from all voice channels")

        for player in self._players.values():
            await player.disconnect()


async def setup(bot: commands.Bot) -> None:
    """Load the MusicCog cog."""
    await bot.add_cog(MusicCog(bot))
