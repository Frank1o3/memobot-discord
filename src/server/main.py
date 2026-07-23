"""
Main module for the Discord AI chatbot.

This is the entry point for the bot, responsible for:
- Loading configuration
- Initializing all components
- Loading cogs
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
    all subsystems (AI, memory, decision making, cogs).
    """

    def __init__(self) -> None:
        """Initialize the bot and all its components."""
        self._config: Config | None = None
        self._bot: commands.Bot | None = None
        self._ai_client: AIClient | None = None
        self._decision_maker: ReplyDecisionMaker | None = None
        self._memory_manager: MemoryManager | None = None
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

    async def _setup_decision_maker(self) -> None:
        """Set up the reply decision maker after bot is ready."""
        if self._bot is None or self._config is None:
            raise RuntimeError("Bot and config must be set up first")

        self._decision_maker = ReplyDecisionMaker(
            self._config,
            self._bot.user,
        )
        logger.info(f"Decision maker initialized for {self._bot.user.name}")

    async def _load_cogs(self) -> None:
        """Load all cogs."""
        if self._bot is None:
            raise RuntimeError("Bot must be set up first")

        if self._config is None or self._ai_client is None or self._decision_maker is None or self._memory_manager is None:
            raise RuntimeError("All components must be initialized first")

        # Import and load cogs
        from server.cogs.basic import BasicCog
        from server.cogs.ai import AICog
        from server.cogs.music import MusicCog

        # Load BasicCog
        await self._bot.add_cog(BasicCog(self._bot))
        logger.info("BasicCog loaded")

        # Load AICog with dependencies
        ai_cog = AICog(
            self._bot,
            self._config,
            self._ai_client,
            self._decision_maker,
            self._memory_manager,
        )
        await self._bot.add_cog(ai_cog)
        logger.info("AICog loaded")

        # Load MusicCog
        await self._bot.add_cog(MusicCog(self._bot))
        logger.info("MusicCog loaded")

    async def _sync_commands(self) -> None:
        """Sync application commands with Discord."""
        if self._bot is None:
            raise RuntimeError("Bot must be set up first")

        synced = await self._bot.tree.sync()

        logger.info(
            "Synced commands: %s",
            [
                (
                    command.name,
                    type(command).__name__,
                    getattr(command, "commands", None),
                )
                for command in synced
            ],
        )

        # Log full command tree
        logger.info("Full command tree:")
        for command in self._bot.tree.walk_commands():
            logger.info(
                "Command: %s | Type: %s",
                command.qualified_name,
                type(command).__name__,
            )

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

        # Set up on_ready handler
        async def on_ready() -> None:
            """Initialize components when bot is ready."""
            await self._setup_decision_maker()
            await self._load_cogs()
            await self._sync_commands()

            # Load memories
            self._memory_manager.load()
            logger.info(
                f"Loaded {self._memory_manager.get_stats()['total_memories']} memories"
            )

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

        if self._memory_manager:
            self._memory_manager.save()

        # Unload music cog to disconnect from voice channels
        if self._bot:
            music_cog = self._bot.get_cog("MusicCog")
            if music_cog:
                await self._bot.remove_cog("MusicCog")
                logger.info("Music cog unloaded")

            ai_cog = self._bot.get_cog("AICog")
            if ai_cog:
                await self._bot.remove_cog("AICog")
                logger.info("AICog unloaded")

            basic_cog = self._bot.get_cog("BasicCog")
            if basic_cog:
                await self._bot.remove_cog("BasicCog")
                logger.info("BasicCog unloaded")

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
        bot_instance = None


if __name__ == "__main__":
    asyncio.run(run())
