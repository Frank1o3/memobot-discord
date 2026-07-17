"""
Decision module for the Discord AI chatbot.

This module implements the intelligent reply decision system that determines
when the bot should respond to messages based on various triggers.
"""

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from server.config import Config

logger = logging.getLogger(__name__)


@dataclass
class ConversationEntry:
    """
    Tracks a message in the conversation history for reply decisions.

    Attributes:
        message_id: The Discord message ID.
        author_id: The user who sent the message.
        timestamp: When the message was sent.
        mentions_bot: Whether the message mentions the bot.
        replies_to_bot: Whether the message replies to the bot.
        contains_bot_name: Whether the message contains the bot's name.
    """

    message_id: int
    author_id: int
    timestamp: datetime
    mentions_bot: bool = False
    replies_to_bot: bool = False
    contains_bot_name: bool = False


@dataclass
class ChannelConversationState:
    """
    Tracks conversation state for a specific channel.

    Attributes:
        channel_id: The Discord channel ID.
        entries: List of recent conversation entries.
        last_bot_response: When the bot last responded in this channel.
        active_participants: Set of user IDs actively conversing.
    """

    channel_id: int
    entries: list[ConversationEntry] = field(default_factory=list)
    last_bot_response: datetime | None = None
    active_participants: set[int] = field(default_factory=set)

    def add_entry(self, entry: ConversationEntry, max_entries: int = 50) -> None:
        """
        Add a new conversation entry.

        Args:
            entry: The entry to add.
            max_entries: Maximum entries to retain.
        """
        self.entries.append(entry)
        if len(self.entries) > max_entries:
            self.entries = self.entries[-max_entries:]

        # Track active participants
        if entry.mentions_bot or entry.replies_to_bot or entry.contains_bot_name:
            self.active_participants.add(entry.author_id)

        # Clean old participants (older than 10 minutes)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=10)
        self.active_participants = {
            uid
            for uid in self.active_participants
            if any(e.author_id == uid and e.timestamp > cutoff for e in self.entries)
        }

    def is_bot_involved_recently(self, window_minutes: int = 5) -> bool:
        """
        Check if the bot has been involved in recent conversation.

        Args:
            window_minutes: Time window to check.

        Returns:
            True if bot was recently involved.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=window_minutes)

        return any(
            (e.mentions_bot or e.replies_to_bot or e.contains_bot_name)
            and e.timestamp > cutoff
            for e in self.entries
        )

    def clear_old_entries(self, max_age_minutes: int = 30) -> None:
        """
        Remove entries older than the specified age.

        Args:
            max_age_minutes: Maximum age of entries to keep.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=max_age_minutes)
        self.entries = [e for e in self.entries if e.timestamp > cutoff]


class ReplyDecisionMaker:
    """
    Intelligent system for deciding when the bot should reply.

    This class evaluates multiple factors to determine if a response is appropriate:
    - Direct mentions (@bot)
    - Replies to bot messages
    - Bot name mentioned in text
    - Recent conversation involvement
    - Random probability for occasional engagement
    """

    def __init__(self, config: "Config", bot_user: discord.ClientUser) -> None:
        """
        Initialize the reply decision maker.

        Args:
            config: Bot configuration.
            bot_user: The bot's Discord user object.
        """
        self._config = config
        self._bot_user = bot_user
        self._channel_states: dict[int, ChannelConversationState] = {}
        self._cooldowns: dict[int, datetime] = {}  # user_id -> last_response_time

        logger.info(f"ReplyDecisionMaker initialized for bot {bot_user.name}")

    def _get_channel_state(self, channel_id: int) -> ChannelConversationState:
        """
        Get or create conversation state for a channel.

        Args:
            channel_id: The channel ID.

        Returns:
            The channel's conversation state.
        """
        if channel_id not in self._channel_states:
            self._channel_states[channel_id] = ChannelConversationState(
                channel_id=channel_id
            )
        return self._channel_states[channel_id]

    def record_message(self, message: discord.Message) -> ConversationEntry:
        """
        Record a message in the conversation tracking system.

        Args:
            message: The Discord message to record.

        Returns:
            The created ConversationEntry.
        """
        content_lower = (message.content or "").lower()
        bot_name_lower = self._bot_user.name.lower()
        bot_mention_str = f"<@{self._bot_user.id}>"
        bot_nickname_mention = f"<@!{self._bot_user.id}>"

        # Check for mentions
        mentions_bot = self._bot_user in message.mentions

        # Check for reply to bot
        replies_to_bot = False
        if message.reference and isinstance(
            message.reference, discord.MessageReference
        ):
            if message.reference.resolved:
                resolved = message.reference.resolved
                replies_to_bot = resolved.author == self._bot_user

        # Check for bot name in text
        contains_bot_name = (
            bot_name_lower in content_lower
            or bot_mention_str in content_lower
            or bot_nickname_mention in content_lower
        )

        entry = ConversationEntry(
            message_id=message.id,
            author_id=message.author.id,
            timestamp=message.created_at.replace(tzinfo=timezone.utc),
            mentions_bot=mentions_bot,
            replies_to_bot=replies_to_bot,
            contains_bot_name=contains_bot_name,
        )

        # Add to channel state
        state = self._get_channel_state(message.channel.id)
        state.add_entry(entry, max_entries=self._config.max_context_messages)

        logger.debug(
            f"Recorded message {message.id} from {message.author}: "
            f"mentions={mentions_bot}, replies={replies_to_bot}, name={contains_bot_name}"
        )

        return entry

    def should_reply(
        self,
        message: discord.Message,
        entry: ConversationEntry | None = None,
    ) -> tuple[bool, str]:
        """
        Determine if the bot should reply to a message.

        Args:
            message: The Discord message to evaluate.
            entry: Pre-computed conversation entry (optional).

        Returns:
            Tuple of (should_reply: bool, reason: str).
        """
        # Record the message if not already done
        if entry is None:
            entry = self.record_message(message)

        # Check cooldown
        if self._is_on_cooldown(message.author.id):
            return False, "cooldown"

        # Priority 1: Direct mention
        if entry.mentions_bot:
            return True, "mentioned"

        # Priority 2: Reply to bot
        if entry.replies_to_bot:
            return True, "reply_to_bot"

        # Priority 3: Bot name in message
        if entry.contains_bot_name:
            return True, "bot_name"

        # Priority 4: Bot recently involved in conversation
        state = self._get_channel_state(message.channel.id)
        if state.is_bot_involved_recently():
            return True, "recent_involvement"

        # Priority 5: Random chance (only for non-bot users)
        if not message.author.bot:
            if random.random() < self._config.random_reply_probability:
                return True, "random"

        return False, "no_trigger"

    def _is_on_cooldown(self, user_id: int) -> bool:
        """
        Check if a user is on cooldown.

        Args:
            user_id: The user's Discord ID.

        Returns:
            True if the user is on cooldown.
        """
        if user_id not in self._cooldowns:
            return False

        last_response = self._cooldowns[user_id]
        now = datetime.now(timezone.utc)
        elapsed = (now - last_response).total_seconds()

        return elapsed < self._config.cooldown_seconds

    def record_response(self, user_id: int) -> None:
        """
        Record that the bot responded to a user.

        Args:
            user_id: The user's Discord ID.
        """
        self._cooldowns[user_id] = datetime.now(timezone.utc)
        logger.debug(f"Recorded response cooldown for user {user_id}")

    def record_bot_response_in_channel(self, channel_id: int) -> None:
        """
        Record that the bot responded in a channel.

        Args:
            channel_id: The channel ID where the bot responded.
        """
        state = self._get_channel_state(channel_id)
        state.last_bot_response = datetime.now(timezone.utc)

    def cleanup(self) -> None:
        """Clean up old conversation entries across all channels."""
        for state in self._channel_states.values():
            state.clear_old_entries(max_age_minutes=30)

        # Clean old cooldowns (older than 1 minute)
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=60)
        self._cooldowns = {
            uid: ts for uid, ts in self._cooldowns.items() if ts > cutoff
        }

    def get_active_conversation_summary(self, channel_id: int) -> str:
        """
        Get a summary of active conversation in a channel.

        Args:
            channel_id: The channel ID.

        Returns:
            Summary string of recent conversation.
        """
        state = self._get_channel_state(channel_id)

        if not state.entries:
            return "No recent conversation."

        recent = state.entries[-10:]  # Last 10 entries
        participants = len(state.active_participants)

        return (
            f"Active conversation with {participants} participant(s). "
            f"Last {len(recent)} messages tracked."
        )
