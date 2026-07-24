"""
Player embed builder for the music player UI.

Creates rich embeds displaying current track information and player state.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from ..player import GuildPlayer


class PlayerEmbed:
    """
    Builder class for creating player embeds.
    
    Creates consistent embeds for the music player showing:
    - Current track info
    - Player state (playing, paused, stopped)
    - Volume level
    - Queue status
    - Repeat mode
    """

    def __init__(self):
        """Initialize the embed builder."""
        self._embed: Optional[discord.Embed] = None

    def build(
        self,
        player: "GuildPlayer",
        show_thumbnail: bool = True,
    ) -> discord.Embed:
        """
        Build a player embed from guild player state.
        
        Args:
            player: The guild player instance.
            show_thumbnail: Whether to include track thumbnail.
            
        Returns:
            A configured discord.Embed.
        """
        current_track = player.current_track

        if current_track:
            self._build_now_playing_embed(player, current_track, show_thumbnail)
        else:
            self._build_idle_embed(player)

        return self._embed  # type: ignore

    def _build_now_playing_embed(
        self,
        player: "GuildPlayer",
        track,
        show_thumbnail: bool,
    ) -> None:
        """Build embed for when a track is playing."""
        self._embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"**{track.title}**",
            color=self._get_state_color(player),
        )

        # Add artist/uploader
        if track.artist:
            self._embed.add_field(
                name="Artist",
                value=track.artist,
                inline=True,
            )

        # Add duration
        if track.duration:
            self._embed.add_field(
                name="Duration",
                value=track.duration_str,
                inline=True,
            )

        # Add source indicator
        source_emoji = {
            "youtube": "📺",
            "spotify": "🟢",
            "search": "🔍",
        }.get(track.source, "🎵")
        self._embed.add_field(
            name="Source",
            value=f"{source_emoji} {track.source.title()}",
            inline=True,
        )

        # Add requested by
        if track.requested_by:
            self._embed.add_field(
                name="Requested by",
                value=track.requested_by.display_name,
                inline=True,
            )

        # Add player state
        state_emoji = {
            "playing": "▶️",
            "paused": "⏸️",
            "stopped": "⏹️",
        }.get(player.state, "⏹️")
        self._embed.add_field(
            name="State",
            value=f"{state_emoji} {player.state.title()}",
            inline=True,
        )

        # Add volume
        self._embed.add_field(
            name="Volume",
            value=f"🔊 {player.volume}%",
            inline=True,
        )

        # Add queue count
        queue_count = len(player.queue)
        self._embed.add_field(
            name="Queue",
            value=f"📋 {queue_count} track{'s' if queue_count != 1 else ''}",
            inline=True,
        )

        # Add repeat mode
        repeat_emoji = {
            "off": "🔁 Off",
            "track": "🔂 Track",
            "queue": "🔁 Queue",
        }.get(player.repeat_mode, "🔁 Off")
        self._embed.add_field(
            name="Repeat",
            value=repeat_emoji,
            inline=True,
        )

        # Set thumbnail if available and requested
        if show_thumbnail and track.thumbnail:
            self._embed.set_thumbnail(url=track.thumbnail)

        # Add footer with webpage URL if available
        if track.webpage_url:
            self._embed.set_footer(text=track.webpage_url[:50] + ("..." if len(track.webpage_url) > 50 else ""))

    def _build_idle_embed(self, player: "GuildPlayer") -> None:
        """Build embed for when nothing is playing."""
        self._embed = discord.Embed(
            title="🎵 Music Player",
            description="No track currently playing",
            color=discord.Color.gray(),
        )

        # Add volume
        self._embed.add_field(
            name="Volume",
            value=f"🔊 {player.volume}%",
            inline=True,
        )

        # Add queue count
        queue_count = len(player.queue)
        self._embed.add_field(
            name="Queue",
            value=f"📋 {queue_count} track{'s' if queue_count != 1 else ''}",
            inline=True,
        )

        # Add repeat mode
        repeat_emoji = {
            "off": "🔁 Off",
            "track": "🔂 Track",
            "queue": "🔁 Queue",
        }.get(player.repeat_mode, "🔁 Off")
        self._embed.add_field(
            name="Repeat",
            value=repeat_emoji,
            inline=True,
        )

    def _get_state_color(self, player: "GuildPlayer") -> discord.Color:
        """Get embed color based on player state."""
        colors = {
            "playing": discord.Color.green(),
            "paused": discord.Color.orange(),
            "stopped": discord.Color.gray(),
        }
        return colors.get(player.state, discord.Color.blue())

    @staticmethod
    def build_queue_embed(
        player: "GuildPlayer",
        max_tracks: int = 10,
    ) -> discord.Embed:
        """
        Build an embed showing the current queue.
        
        Args:
            player: The guild player instance.
            max_tracks: Maximum number of tracks to display.
            
        Returns:
            A configured discord.Embed.
        """
        current_track = player.current_track
        queue_list = list(player.queue)

        embed = discord.Embed(
            title="🎵 Current Queue",
            color=discord.Color.blue(),
        )

        if current_track:
            embed.add_field(
                name="▶️ Now Playing",
                value=f"**{current_track.title}**",
                inline=False,
            )

        if queue_list:
            queue_str = ""
            for i, track in enumerate(queue_list[:max_tracks], 1):
                queue_str += f"{i}. **{track.title}**"
                if track.artist:
                    queue_str += f" - {track.artist}"
                queue_str += "\n"

            if len(queue_list) > max_tracks:
                queue_str += f"... and {len(queue_list) - max_tracks} more tracks"

            embed.add_field(
                name=f"📋 Upcoming ({len(queue_list)} tracks)",
                value=queue_str,
                inline=False,
            )
        else:
            embed.add_field(
                name="📋 Upcoming",
                value="Queue is empty",
                inline=False,
            )

        return embed
