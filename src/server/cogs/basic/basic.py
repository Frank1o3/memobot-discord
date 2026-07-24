"""
Basic cog for general bot commands.

Contains simple/default/general bot commands that don't belong to
specialized features like AI, music, or moderation.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)


class BasicCog(commands.Cog):
    """Basic cog responsible for simple/general bot commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        logger.info("BasicCog initialized")

    @app_commands.command(name="help", description="Get help with bot commands")
    async def help(self, interaction: discord.Interaction) -> None:
        """Show help information."""
        embed = discord.Embed(
            title="🤖 AI ChatBot Help",
            description="Here's how you can interact with me:",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="💬 Chatting",
            value=(
                "• Mention me (@BotName) to get my attention\n"
                "• Reply to my messages to continue conversation\n"
                "• I'll occasionally join conversations naturally\n"
                "• I remember things you tell me about yourself!"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎵 Music",
            value=(
                "• `/music join` - Join your voice channel\n"
                "• `/music play <song>` - Play a song\n"
                "• `/music pause` - Pause playback\n"
                "• `/music resume` - Resume playback\n"
                "• `/music skip` - Skip to next song\n"
                "• `/music stop` - Stop and clear queue\n"
                "• `/music queue` - Show current queue\n"
                "• `/music nowplaying` - Show currently playing\n"
                "• `/music remove <index>` - Remove from queue\n"
                "• `/music volume <0-100>` - Set volume"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚙️ Commands",
            value=(
                "• `/help` - Show this help message\n"
                "• `/ping` - Check bot latency\n"
                "• `/clean` - Clean all chat messages in the current channel\n"
                "• `/stats` - View bot statistics\n"
                "• `/clearmemories` - Clear your stored memories"
            ),
            inline=False,
        )
        embed.set_footer(text="Just talk naturally - I'll respond when appropriate!")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="ping", description="Check bot response time")
    async def ping(self, interaction: discord.Interaction) -> None:
        """Check bot latency."""
        latency = round(self.bot.latency * 1000)
        await interaction.response.send_message(
            f"🏓 Pong! Latency: {latency}ms",
            ephemeral=True,
        )

    @app_commands.command(
        name="clean",
        description="Clean all chat messages in the current channel",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clean(self, interaction: discord.Interaction) -> None:
        """Delete all messages in the current channel."""
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ This command can only be used in a text channel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            deleted_count = 0

            # Delete messages in batches.
            # Discord's bulk delete API only supports messages newer than 14 days,
            # so older messages are deleted individually.
            while True:
                messages = [
                    message
                    async for message in interaction.channel.history(limit=100)
                ]

                if not messages:
                    break

                recent_messages = [
                    message
                    for message in messages
                    if (discord.utils.utcnow() - message.created_at).days < 14
                ]

                old_messages = [
                    message
                    for message in messages
                    if (discord.utils.utcnow() - message.created_at).days >= 14
                ]

                if recent_messages:
                    deleted = await interaction.channel.purge(
                        limit=len(recent_messages),
                        check=lambda message: message in recent_messages,
                    )
                    deleted_count += len(deleted)

                for message in old_messages:
                    try:
                        await message.delete()
                        deleted_count += 1
                    except discord.HTTPException:
                        logger.warning(
                            "Failed to delete old message %s",
                            message.id,
                        )

                # If we only found old messages, the next iteration will
                # continue processing them until the channel is empty.
                if len(messages) < 100:
                    break

            await interaction.followup.send(
                f"🧹 Cleaned **{deleted_count}** messages from this channel.",
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I don't have permission to delete messages in this channel.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            logger.error("Failed to clean channel: %s", e)
            await interaction.followup.send(
                "❌ I couldn't clean the channel because Discord returned an error.",
                ephemeral=True,
            )

    @commands.command(name="ping", help="Check bot response time")
    async def prefix_ping(self, ctx: commands.Context) -> None:
        """Check bot latency via prefix command."""
        latency = round(self.bot.latency * 1000)
        await ctx.send(f"🏓 Pong! Latency: {latency}ms")


async def setup(bot: commands.Bot) -> None:
    """Load the BasicCog cog."""
    await bot.add_cog(BasicCog(bot))
