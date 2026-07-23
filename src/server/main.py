"""
Main module for the Discord AI chatbot.

This is the entry point for the bot, responsible for:
- Loading configuration
- Initializing all components
- Setting up slash and prefix commands
- Running the bot with graceful shutdown handling
"""

import asyncio
import logging
import signal
import sys

import discord
from discord.ext import commands

from server.ai import AIClient
from server.config import config_manager, Config
from server.decision import ReplyDecisionMaker
from server.events import setup_event_handlers, EventHandler
from server.memory import MemoryManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)


class DiscordAIChatBot:
    """
    Main bot class that orchestrates all components.

    This class manages the lifecycle of the bot and coordinates between
    all subsystems (AI, memory, decision making, events).
    """

    def __init__(self) -> None:
        """Initialize the bot and all its components."""
        self._config: Config | None = None
        self._bot: commands.Bot | None = None
        self._ai_client: AIClient | None = None
        self._decision_maker: ReplyDecisionMaker | None = None
        self._memory_manager: MemoryManager | None = None
        self._event_handler: EventHandler | None = None
        self._shutdown_event: asyncio.Event | None = None

        logger.info("DiscordAIChatBot initialized")

    def load_config(self, config_path: str = "config.json") -> Config:
        """
        Load bot configuration from file.

        Args:
            config_path: Path to the configuration file.

        Returns:
            The loaded configuration.
        """
        self._config = config_manager.load(config_path)
        logger.info(f"Configuration loaded from {config_path}")
        return self._config

    def _setup_bot(self) -> None:
        """Set up the Discord bot with appropriate intents."""
        if self._config is None:
            raise RuntimeError("Configuration must be loaded first")

        # Set up intents - we need message content for processing
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.guilds = True
        intents.members = True

        self._bot = commands.Bot(
            command_prefix=self._config.prefix,
            intents=intents,
            help_command=None,  # We'll implement custom help
        )

        logger.info("Discord bot instance created")

    def _initialize_components(self) -> None:
        """Initialize all bot subsystems."""
        if self._config is None or self._bot is None:
            raise RuntimeError("Bot and config must be set up first")

        # Initialize AI client
        self._ai_client = AIClient(self._config)
        logger.info("AI client initialized")

        # Initialize memory manager
        self._memory_manager = MemoryManager(self._config)
        logger.info("Memory manager initialized")

        # Decision maker will be initialized after bot is ready
        logger.info("All components initialized")

    def _setup_commands(self) -> None:
        """Set up slash commands and prefix commands."""
        if self._bot is None:
            raise RuntimeError("Bot must be set up first")

        # Slash Commands
        @self._bot.tree.command(name="help", description="Get help with bot commands")
        async def slash_help(interaction: discord.Interaction) -> None:
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
                name="⚙️ Commands",
                value=(
                    f"• `{self._config.prefix}ping` - Check bot latency\n"
                    f"• `{self._config.prefix}stats` - View bot statistics\n"
                    f"• `{self._config.prefix}clearmemories` - Clear your stored memories\n"
                    f"• `/help` - Show this help message"
                ),
                inline=False,
            )
            embed.set_footer(
                text="Just talk naturally - I'll respond when appropriate!"
            )

            await interaction.response.send_message(embed=embed)

        @self._bot.tree.command(name="ping", description="Check bot response time")
        async def slash_ping(interaction: discord.Interaction) -> None:
            """Check bot latency."""
            latency = round(self._bot.latency * 1000)
            await interaction.response.send_message(
                f"🏓 Pong! Latency: {latency}ms",
                ephemeral=True,
            )

        @self._bot.tree.command(
            name="clean",
            description="Clean all chat messages in the current channel",
        )
        @discord.app_commands.checks.has_permissions(manage_messages=True)
        async def slash_clean(interaction: discord.Interaction) -> None:
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

        @self._bot.tree.command(
            name="stats",
            description="View bot statistics and status",
        )
        async def slash_stats(interaction: discord.Interaction) -> None:
            """Show bot statistics."""
            if self._event_handler:
                stats = self._event_handler.get_stats()
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
                embed.add_field(
                    name="Active Tasks",
                    value=str(stats["active_tasks"]),
                    inline=True,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(
                    "Stats not available yet",
                    ephemeral=True,
                )

        @self._bot.tree.command(
            name="clearmemories",
            description="Clear all stored memories about you",
        )
        async def slash_clear_memories(interaction: discord.Interaction) -> None:
            """Clear user's stored memories."""
            if self._memory_manager and interaction.user:
                count = self._memory_manager.clear_user_memories(interaction.user.id)
                await interaction.response.send_message(
                    f"🗑️ Cleared {count} memories about you.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Unable to clear memories at this time.",
                    ephemeral=True,
                )

        # Prefix Commands
        @self._bot.command(name="ping", help="Check bot response time")
        async def prefix_ping(ctx: commands.Context) -> None:
            """Check bot latency via prefix command."""
            latency = round(self._bot.latency * 1000)
            await ctx.send(f"🏓 Pong! Latency: {latency}ms")

        @self._bot.command(name="stats", help="View bot statistics")
        async def prefix_stats(ctx: commands.Context) -> None:
            """Show bot statistics via prefix command."""
            if self._event_handler:
                stats = self._event_handler.get_stats()
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
            else:
                await ctx.send("Stats not available yet")

        @self._bot.command(
            name="clearmemories",
            help="Clear all stored memories about you",
        )
        async def prefix_clear_memories(ctx: commands.Context) -> None:
            """Clear user's stored memories via prefix command."""
            if self._memory_manager and ctx.author:
                count = self._memory_manager.clear_user_memories(ctx.author.id)
                await ctx.send(f"🗑️ Cleared {count} memories about you.")
            else:
                await ctx.send("Unable to clear memories at this time")

        logger.info("Commands registered")

    async def _setup_decision_maker(self) -> None:
        """Set up the reply decision maker after bot is ready."""
        if self._bot is None or self._config is None:
            raise RuntimeError("Bot and config must be set up first")

        self._decision_maker = ReplyDecisionMaker(
            self._config,
            self._bot.user,
        )
        logger.info(f"Decision maker initialized for {self._bot.user.name}")

    def _setup_event_handlers(self) -> None:
        """Set up event handlers."""
        if (
            self._bot is None
            or self._config is None
            or self._ai_client is None
            or self._decision_maker is None
            or self._memory_manager is None
        ):
            raise RuntimeError("All components must be initialized first")

        self._event_handler = setup_event_handlers(
            self._bot,
            self._config,
            self._ai_client,
            self._decision_maker,
            self._memory_manager,
        )
        logger.info("Event handlers set up")

    async def run(self, config_path: str = "config.json") -> None:
        """
        Run the bot with graceful shutdown handling.

        Args:
            config_path: Path to the configuration file.

        Raises:
            RuntimeError: If configuration is invalid.
        """
        # Load configuration
        self.load_config(config_path)

        # Set up bot and components
        self._setup_bot()
        self._initialize_components()
        self._setup_commands()

        # Set up shutdown handling
        self._shutdown_event = asyncio.Event()

        loop = asyncio.get_running_loop()

        def handle_signal() -> None:
            """Handle shutdown signals."""
            logger.info("Shutdown signal received")
            self._shutdown_event.set()

        # Register signal handlers
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, handle_signal)

        # Set up event handlers (will be called after bot is ready)
        async def on_ready() -> None:
            """Initialize components when bot is ready."""
            await self._setup_decision_maker()
            self._setup_event_handlers()
            logger.info(f"Bot is ready as {self._bot.user}")

        self._bot.event(on_ready)

        # Create shutdown task
        async def watch_shutdown() -> None:
            """Watch for shutdown event."""
            await self._shutdown_event.wait()
            await self._cleanup()
            await self._bot.close()  # type: ignore[union-attr]

        shutdown_task = asyncio.create_task(watch_shutdown())

        try:
            # Run the bot
            logger.info("Starting bot...")
            await self._bot.start(self._config.token)  # type: ignore[union-attr]
        finally:
            # Ensure cleanup
            await shutdown_task

    async def _cleanup(self) -> None:
        """Clean up all resources before shutdown."""
        logger.info("Cleaning up resources...")

        if self._event_handler:
            await self._event_handler.cleanup()

        if self._memory_manager:
            self._memory_manager.save()

        logger.info("Cleanup complete")


# Global bot instance
bot_instance: DiscordAIChatBot | None = None


async def run() -> None:
    """
    Main entry point for the bot.

    Creates and runs the bot instance.
    """
    global bot_instance

    bot_instance = DiscordAIChatBot()

    try:
        await bot_instance.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise
    finally:
        if bot_instance:
            await bot_instance._cleanup()


def main() -> None:
    asyncio.run(main=run())


if __name__ == "__main__":
    asyncio.run(main=run())
