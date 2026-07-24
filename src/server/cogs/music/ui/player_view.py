"""
Interactive player view with Discord UI buttons.

Provides button controls for the music player.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.enums import ButtonStyle
from discord.ui import Button, View

if TYPE_CHECKING:
    from ..music import MusicCog
    from ..player import GuildPlayer

logger = logging.getLogger(__name__)


class PlayerView(View):
    """
    Interactive view with music player control buttons.

    Buttons:
    - Previous track
    - Pause/Resume playback
    - Next track
    - Volume down
    - Volume up
    - Stop playback
    - Show queue
    - Shuffle queue
    - Toggle repeat mode
    """

    def __init__(
        self,
        cog: MusicCog,
        guild_id: int,
        timeout: float = 300,
    ):
        """
        Initialize the player view.

        Args:
            cog: The music cog instance.
            guild_id: The guild this view controls.
            timeout: Button interaction timeout in seconds.
        """
        super().__init__(timeout=timeout)
        self._cog = cog
        self._guild_id = guild_id

    def _add_buttons(self) -> None:
        """Add all control buttons to the view."""
        # Row 1: Previous, Pause/Resume, Next
        self.add_item(
            Button(
                style=ButtonStyle.secondary,
                emoji="⏮️",
                custom_id="music_previous",
                row=0,
            )
        )
        self.add_item(
            Button(
                style=ButtonStyle.primary,
                emoji="⏯️",
                custom_id="music_pause_resume",
                row=0,
            )
        )
        self.add_item(
            Button(
                style=ButtonStyle.secondary,
                emoji="⏭️",
                custom_id="music_next",
                row=0,
            )
        )

        # Row 2: Volume down, Volume up, Stop
        self.add_item(
            Button(
                style=ButtonStyle.secondary,
                emoji="🔉",
                custom_id="music_volume_down",
                row=1,
            )
        )
        self.add_item(
            Button(
                style=ButtonStyle.secondary,
                emoji="🔊",
                custom_id="music_volume_up",
                row=1,
            )
        )
        self.add_item(
            Button(
                style=ButtonStyle.danger,
                emoji="⏹️",
                custom_id="music_stop",
                row=1,
            )
        )

        # Row 3: Queue, Shuffle, Repeat
        self.add_item(
            Button(
                style=ButtonStyle.secondary,
                emoji="📋",
                custom_id="music_queue",
                row=2,
            )
        )
        self.add_item(
            Button(
                style=ButtonStyle.secondary,
                emoji="🔀",
                custom_id="music_shuffle",
                row=2,
            )
        )
        self.add_item(
            Button(
                style=ButtonStyle.secondary,
                emoji="🔁",
                custom_id="music_repeat",
                row=2,
            )
        )

    def get_player(self) -> GuildPlayer | None:
        """Get the guild player instance."""
        return self._cog.get_player(self._guild_id)

    async def check_permissions(self, interaction: discord.Interaction) -> bool:
        """
        Check if the user has permission to use player controls.

        Users must be in the same voice channel as the bot.

        Args:
            interaction: The button interaction.

        Returns:
            True if user has permission, False otherwise.
        """
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )
            return False

        player = self.get_player()
        if not player or not player.voice_client:
            await interaction.response.send_message(
                "❌ I'm not connected to a voice channel.",
                ephemeral=True,
            )
            return False

        # Check if user is in the same voice channel
        user_voice = interaction.user.voice
        bot_voice = player.voice_client.channel

        if not user_voice or user_voice.channel != bot_voice:
            await interaction.response.send_message(
                "❌ You must be in the same voice channel as the bot to use music controls.",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        style=ButtonStyle.secondary, emoji="⏮️", custom_id="music_previous", row=0
    )
    async def previous_button(
        self,
        interaction: discord.Interaction,
        button: Button,
    ) -> None:
        """Handle previous track button."""
        if not await self.check_permissions(interaction):
            return

        player = self.get_player()
        if not player:
            await interaction.response.send_message(
                "❌ No player active for this server.",
                ephemeral=True,
            )
            return

        if await player.play_previous():
            await interaction.response.edit_message(
                embed=player.build_embed(),
                view=self,
            )
        else:
            await interaction.response.send_message(
                "❌ No previous track in history.",
                ephemeral=True,
            )

    @discord.ui.button(
        style=ButtonStyle.primary, emoji="⏯️", custom_id="music_pause_resume", row=0
    )
    async def pause_resume_button(
        self,
        interaction: discord.Interaction,
        button: Button,
    ) -> None:
        """Handle pause/resume toggle button."""
        if not await self.check_permissions(interaction):
            return

        player = self.get_player()
        if not player:
            await interaction.response.send_message(
                "❌ No player active for this server.",
                ephemeral=True,
            )
            return

        if player.state == "paused":
            player.resume()
        else:
            player.pause()

        await interaction.response.edit_message(
            embed=player.build_embed(),
            view=self,
        )

    @discord.ui.button(
        style=ButtonStyle.secondary, emoji="⏭️", custom_id="music_next", row=0
    )
    async def next_button(
        self,
        interaction: discord.Interaction,
        button: Button,
    ) -> None:
        """Handle next track button."""
        if not await self.check_permissions(interaction):
            return

        player = self.get_player()
        if not player:
            await interaction.response.send_message(
                "❌ No player active for this server.",
                ephemeral=True,
            )
            return

        if await player.play_next():
            await interaction.response.edit_message(
                embed=player.build_embed(),
                view=self,
            )
        else:
            await interaction.response.send_message(
                "❌ No more tracks in queue.",
                ephemeral=True,
            )

    @discord.ui.button(
        style=ButtonStyle.secondary, emoji="🔉", custom_id="music_volume_down", row=1
    )
    async def volume_down_button(
        self,
        interaction: discord.Interaction,
        button: Button,
    ) -> None:
        """Handle volume down button."""
        if not await self.check_permissions(interaction):
            return

        player = self.get_player()
        if not player:
            await interaction.response.send_message(
                "❌ No player active for this server.",
                ephemeral=True,
            )
            return

        player.set_volume(max(0, player.volume - 10))
        await interaction.response.edit_message(
            embed=player.build_embed(),
            view=self,
        )

    @discord.ui.button(
        style=ButtonStyle.secondary, emoji="🔊", custom_id="music_volume_up", row=1
    )
    async def volume_up_button(
        self,
        interaction: discord.Interaction,
        button: Button,
    ) -> None:
        """Handle volume up button."""
        if not await self.check_permissions(interaction):
            return

        player = self.get_player()
        if not player:
            await interaction.response.send_message(
                "❌ No player active for this server.",
                ephemeral=True,
            )
            return

        player.set_volume(min(100, player.volume + 10))
        await interaction.response.edit_message(
            embed=player.build_embed(),
            view=self,
        )

    @discord.ui.button(
        style=ButtonStyle.danger, emoji="⏹️", custom_id="music_stop", row=1
    )
    async def stop_button(
        self,
        interaction: discord.Interaction,
        button: Button,
    ) -> None:
        """Handle stop button."""
        if not await self.check_permissions(interaction):
            return

        player = self.get_player()
        if not player:
            await interaction.response.send_message(
                "❌ No player active for this server.",
                ephemeral=True,
            )
            return

        player.stop()
        await interaction.response.edit_message(
            embed=player.build_embed(),
            view=self,
        )

    @discord.ui.button(
        style=ButtonStyle.secondary, emoji="📋", custom_id="music_queue", row=2
    )
    async def queue_button(
        self,
        interaction: discord.Interaction,
        button: Button,
    ) -> None:
        """Handle queue display button."""
        if not await self.check_permissions(interaction):
            return

        player = self.get_player()
        if not player:
            await interaction.response.send_message(
                "❌ No player active for this server.",
                ephemeral=True,
            )
            return

        queue_embed = player.build_queue_embed()
        await interaction.response.send_message(
            embed=queue_embed,
            ephemeral=True,
        )

    @discord.ui.button(
        style=ButtonStyle.secondary, emoji="🔀", custom_id="music_shuffle", row=2
    )
    async def shuffle_button(
        self,
        interaction: discord.Interaction,
        button: Button,
    ) -> None:
        """Handle shuffle button."""
        if not await self.check_permissions(interaction):
            return

        player = self.get_player()
        if not player:
            await interaction.response.send_message(
                "❌ No player active for this server.",
                ephemeral=True,
            )
            return

        player.shuffle_queue()
        await interaction.response.send_message(
            "🔀 Queue shuffled!",
            ephemeral=True,
        )

    @discord.ui.button(
        style=ButtonStyle.secondary, emoji="🔁", custom_id="music_repeat", row=2
    )
    async def repeat_button(
        self,
        interaction: discord.Interaction,
        button: Button,
    ) -> None:
        """Handle repeat mode toggle button."""
        if not await self.check_permissions(interaction):
            return

        player = self.get_player()
        if not player:
            await interaction.response.send_message(
                "❌ No player active for this server.",
                ephemeral=True,
            )
            return

        player.toggle_repeat()
        await interaction.response.edit_message(
            embed=player.build_embed(),
            view=self,
        )

    async def on_timeout(self) -> None:
        """Handle view timeout - disable all buttons."""
        for child in self.children:
            if isinstance(child, Button):
                child.disabled = True
