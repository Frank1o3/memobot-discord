"""
Prompts module for the Discord AI chatbot.

This module contains all system prompts and prompt templates used for AI inference,
including conversation handling, summarization, and memory extraction.
"""

from typing import Final


SYSTEM_PROMPT: Final[
    str
] = """You are a helpful, friendly AI assistant integrated into a Discord server. 
You communicate in a casual, conversational tone appropriate for Discord chat.

Guidelines:
- Be concise but helpful - Discord messages should be brief and readable
- Use informal language and emojis when appropriate, but don't overdo it
- Stay on topic and respond naturally to the conversation flow
- If you don't know something, admit it rather than making things up
- Be respectful and follow Discord community guidelines
- Don't respond to every message - only engage when relevant or when directly addressed
- Reference previous messages in the conversation when helpful
- If someone shares files or images, acknowledge them appropriately

Remember: You're chatting with real people in real-time. Keep responses natural and engaging."""


SUMMARY_PROMPT: Final[
    str
] = """Summarize the following Discord conversation into a concise summary.
Focus on:
- Key topics discussed
- Important information shared (names, preferences, projects, links)
- Decisions made or conclusions reached
- Any action items mentioned

Keep the summary brief but capture essential details that would help someone understand what was discussed.

Conversation to summarize:
{conversation}

Summary:"""


MEMORY_EXTRACTION_PROMPT: Final[
    str
] = """Extract useful long-term memories from this conversation about the user.
Focus on:
- User preferences (favorite things, likes/dislikes)
- Recurring interests or hobbies mentioned
- Projects they're working on
- Personal information they've shared (name, location, etc.)
- Skills or expertise they have

Only extract facts that would be useful for future conversations. Ignore temporary topics, jokes, or one-off comments.

Format each memory as a single sentence. Return 0-5 memories maximum.

Conversation:
{conversation}

Memories (one per line, or empty if nothing worth remembering):"""


CONTEXT_BUILDING_INSTRUCTIONS: Final[
    str
] = """Build a chronological conversation context from these messages.

Format each message as:
[username]: message content

Include:
- Message author names
- Timestamps relative to now (e.g., "2 minutes ago")
- File attachments described as [File: filename] or [URL: link]
- Replies indicated with "Replying to @username:"

Exclude:
- Bot messages (except our own previous responses)
- Very old messages beyond the context limit
- Empty or system messages

The goal is to provide the AI with enough context to understand the conversation flow."""


REPLY_DECISION_PROMPT: Final[
    str
] = """Decide whether the AI should reply to this message.

Consider replying if:
- The AI is mentioned (@mentioned)
- The message is a reply to the AI's previous message
- The AI's name is mentioned in the text
- The conversation has recently involved the AI
- The message is a question or clearly seeks a response
- Random chance indicates a reply (to be occasionally proactive)

Do NOT reply if:
- The message is clearly directed at someone else
- It's just an emoji reaction or very short acknowledgment
- The conversation has moved on and the AI wasn't part of it
- It would be spammy or intrusive

Message context:
{context}

Should reply? Answer only YES or NO."""


GREETING_RESPONSES: Final[list[str]] = [
    "Hey! How's it going?",
    "Hello! What's up?",
    "Hi there! Need help with something?",
    "Yo! What's happening?",
    "Hey hey! Good to see you!",
]


FAREWELL_RESPONSES: Final[list[str]] = [
    "See ya later!",
    "Catch you later!",
    "Bye! Have a great day!",
    "Later! Come back soon!",
    "Peace out! 👋",
]


ERROR_RESPONSES: Final[list[str]] = [
    "Oops, something went wrong on my end. Let me try again!",
    "Hmm, I'm having trouble processing that. One moment...",
    "Sorry, I got a bit confused there. Could you rephrase?",
    "My bad! Having a small technical issue. Give me a sec...",
]


RATE_LIMIT_RESPONSE: Final[str] = (
    "I'm thinking about that! Give me a moment to process... 🤔"
)


def build_system_prompt() -> str:
    """
    Build the complete system prompt for AI inference.

    Returns:
        The formatted system prompt string.
    """
    return SYSTEM_PROMPT


def build_summary_prompt(conversation: str) -> str:
    """
    Build a prompt for summarizing a conversation.

    Args:
        conversation: The conversation text to summarize.

    Returns:
        The formatted summary prompt.
    """
    return SUMMARY_PROMPT.format(conversation=conversation)


def build_memory_extraction_prompt(conversation: str) -> str:
    """
    Build a prompt for extracting memories from a conversation.

    Args:
        conversation: The conversation text to analyze.

    Returns:
        The formatted memory extraction prompt.
    """
    return MEMORY_EXTRACTION_PROMPT.format(conversation=conversation)


def build_reply_decision_prompt(context: str) -> str:
    """
    Build a prompt for deciding whether to reply.

    Args:
        context: The conversation context to evaluate.

    Returns:
        The formatted reply decision prompt.
    """
    return REPLY_DECISION_PROMPT.format(context=context)
