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
