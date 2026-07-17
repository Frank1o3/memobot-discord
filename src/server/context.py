"""
Context module for the Discord AI chatbot.

This module handles building conversation context from Discord messages,
including fetching channel history, formatting messages, and managing context limits.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class FormattedMessage:
    """
    Represents a formatted message for AI context.

    Attributes:
        author_name: The display name of the message author.
        content: The message content.
        timestamp: When the message was sent.
        is_bot: Whether the message is from a bot.
        attachments: List of attachment descriptions.
        reference: Reference to another message if this is a reply.
    """

    author_name: str
    content: str
    timestamp: datetime
    is_bot: bool
    attachments: list[str] = field(default_factory=list)
    reference: str | None = None

    def format(self, relative_to: datetime | None = None) -> str:
        """
        Format the message for AI context.

        Args:
            relative_to: Reference datetime for relative time display.

        Returns:
            Formatted message string.
        """
        parts = []

        # Add reference info if replying
        if self.reference:
            parts.append(f"Replying to {self.reference}:")

        # Add author and content
        parts.append(f"[{self.author_name}]: {self.content}")

        # Add attachment info
        for attachment in self.attachments:
            parts.append(f"  [Attachment: {attachment}]")

        # Add relative timestamp if provided
        if relative_to:
            delta = relative_to - self.timestamp
            time_str = _format_time_delta(delta)
            parts[-1] += f" ({time_str})"

        return "\n".join(parts)


def _format_time_delta(delta: datetime) -> str:
    """
    Format a timedelta into human-readable string.

    Args:
        delta: The time difference to format.

    Returns:
        Human-readable time string (e.g., "2 minutes ago").
    """
    total_seconds = int(delta.total_seconds())

    if total_seconds < 60:
        return "just now"
    elif total_seconds < 3600:
        minutes = total_seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    elif total_seconds < 86400:
        hours = total_seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    else:
        days = total_seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"


async def fetch_channel_history(
    channel: discord.TextChannel | discord.Thread | discord.DMChannel,
    limit: int = 100,
) -> list[discord.Message]:
    """
    Fetch recent message history from a channel.

    Args:
        channel: The Discord channel to fetch from.
        limit: Maximum number of messages to fetch.

    Returns:
        List of messages ordered from oldest to newest.
    """
    logger.debug(f"Fetching {limit} messages from channel {channel.id}")

    try:
        messages = []
        async for msg in channel.history(limit=limit):
            messages.append(msg)

        # Reverse to get chronological order (oldest first)
        messages.reverse()
        logger.debug(f"Fetched {len(messages)} messages")
        return messages

    except discord.Forbidden:
        logger.warning(f"No permission to read history in channel {channel.id}")
        return []
    except discord.HTTPException as e:
        logger.error(f"Failed to fetch channel history: {e}")
        return []


def format_message_for_context(
    message: discord.Message,
    bot_user: discord.ClientUser,
) -> FormattedMessage:
    """
    Format a Discord message for AI context.

    Args:
        message: The Discord message to format.
        bot_user: The bot's user object for identifying bot messages.

    Returns:
        FormattedMessage object ready for context building.
    """
    # Extract attachment info
    attachments = []
    for attachment in message.attachments:
        if attachment.url:
            attachments.append(f"{attachment.filename} - {attachment.url}")

    # Extract reference info if replying
    reference = None
    if message.reference and isinstance(message.reference, discord.MessageReference):
        if message.reference.resolved:
            resolved = message.reference.resolved
            reference = f"@{resolved.author.display_name}"

    # Build content with embed info
    content = message.content or ""

    # Add embed descriptions if present
    for embed in message.embeds:
        if embed.description:
            content += f"\n[Embed: {embed.description[:100]}...]"
        elif embed.title:
            content += f"\n[Embed: {embed.title}]"

    return FormattedMessage(
        author_name=message.author.display_name,
        content=content.strip() or "[Empty message]",
        timestamp=message.created_at.replace(tzinfo=timezone.utc),
        is_bot=message.author.bot,
        attachments=attachments,
        reference=reference,
    )


def build_context(
    messages: list[discord.Message],
    bot_user: discord.ClientUser,
    max_messages: int,
    include_bot_messages: bool = False,
) -> tuple[str, list[FormattedMessage]]:
    """
    Build conversation context from a list of messages.

    Args:
        messages: List of Discord messages (chronological order).
        bot_user: The bot's user object.
        max_messages: Maximum number of messages to include.
        include_bot_messages: Whether to include other bot messages.

    Returns:
        Tuple of (formatted context string, list of FormattedMessage objects).
    """
    formatted_messages: list[FormattedMessage] = []

    # Filter and format messages
    for msg in messages:
        # Skip our own messages for input context (they'll be in conversation history)
        if msg.author == bot_user:
            continue

        # Skip other bot messages unless explicitly included
        if msg.author.bot and not include_bot_messages:
            continue

        formatted = format_message_for_context(msg, bot_user)
        formatted_messages.append(formatted)

    # Trim to max messages (keep most recent)
    if len(formatted_messages) > max_messages:
        formatted_messages = formatted_messages[-max_messages:]

    # Build context string
    now = datetime.now(timezone.utc)
    context_lines = [msg.format(relative_to=now) for msg in formatted_messages]

    context_string = "\n\n".join(context_lines)

    logger.debug(f"Built context with {len(formatted_messages)} messages")
    return context_string, formatted_messages


def should_summarize(message_count: int, summary_trigger: int) -> bool:
    """
    Determine if context should be summarized.

    Args:
        message_count: Current number of messages in context.
        summary_trigger: Threshold for triggering summarization.

    Returns:
        True if summarization should occur.
    """
    return message_count >= summary_trigger


def extract_recent_conversation(
    formatted_messages: list[FormattedMessage],
    lookback: int = 20,
) -> str:
    """
    Extract recent conversation for memory operations.

    Args:
        formatted_messages: List of formatted messages.
        lookback: Number of recent messages to consider.

    Returns:
        String containing recent conversation.
    """
    recent = formatted_messages[-lookback:] if len(formatted_messages) > lookback else formatted_messages

    if not recent:
        return ""

    now = datetime.now(timezone.utc)
    return "\n\n".join(msg.format(relative_to=now) for msg in recent)
