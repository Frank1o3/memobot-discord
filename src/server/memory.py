"""
Memory module for the Discord AI chatbot.

This module handles long-term memory storage and retrieval for users,
including saving useful facts and retrieving relevant memories before inference.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class Memory:
    """
    Represents a single memory entry about a user.

    Attributes:
        content: The memory text/fact.
        created_at: When the memory was created.
        last_accessed: When the memory was last accessed.
        access_count: How many times this memory has been accessed.
        category: Optional category for the memory (e.g., "preference", "project").
    """

    content: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    access_count: int = 0
    category: str | None = None

    def to_dict(self) -> dict:
        """Convert memory to dictionary for JSON serialization."""
        return {
            "content": self.content,
            "created_at": self.created_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat(),
            "access_count": self.access_count,
            "category": self.category,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Memory":
        """Create a Memory from a dictionary."""
        return cls(
            content=data["content"],
            created_at=datetime.fromisoformat(data["created_at"]),
            last_accessed=datetime.fromisoformat(data["last_accessed"]),
            access_count=data.get("access_count", 0),
            category=data.get("category"),
        )


@dataclass
class UserMemories:
    """
    Collection of memories for a single user.

    Attributes:
        user_id: The Discord user ID.
        username: The user's current display name.
        memories: List of memory entries.
    """

    user_id: int
    username: str
    memories: list[Memory] = field(default_factory=list)

    def add_memory(self, content: str, category: str | None = None) -> None:
        """
        Add a new memory.

        Args:
            content: The memory content.
            category: Optional category for the memory.
        """
        # Check for duplicates
        existing = next(
            (m for m in self.memories if m.content.lower() == content.lower()), None
        )
        if existing:
            logger.debug(f"Memory already exists for user {self.user_id}: {content}")
            return

        memory = Memory(content=content, category=category)
        self.memories.append(memory)
        logger.info(f"Added memory for user {self.user_id}: {content}")

    def get_relevant_memories(self, query: str, limit: int = 5) -> list[Memory]:
        """
        Get memories relevant to a query.

        Simple keyword-based relevance scoring.

        Args:
            query: The search query/context.
            limit: Maximum number of memories to return.

        Returns:
            List of relevant memories sorted by relevance.
        """
        query_lower = query.lower()
        query_words = set(query_lower.split())

        scored_memories: list[tuple[Memory, int]] = []

        for memory in self.memories:
            content_lower = memory.content.lower()
            score = sum(1 for word in query_words if word in content_lower)

            # Boost recently accessed memories
            now = datetime.now(timezone.utc)
            days_since_access = (now - memory.last_accessed).days
            if days_since_access < 7:
                score += 2
            elif days_since_access < 30:
                score += 1

            # Boost frequently accessed memories
            score += min(memory.access_count, 5)

            if score > 0:
                scored_memories.append((memory, score))

        # Sort by score descending
        scored_memories.sort(key=lambda x: x[1], reverse=True)

        # Update access count for returned memories
        result = [m[0] for m in scored_memories[:limit]]
        for memory in result:
            memory.access_count += 1
            memory.last_accessed = datetime.now(timezone.utc)

        return result

    def trim_to_limit(self, max_memories: int) -> int:
        """
        Trim memories to stay within the limit.

        Removes oldest/least accessed memories first.

        Args:
            max_memories: Maximum number of memories to keep.

        Returns:
            Number of memories removed.
        """
        if len(self.memories) <= max_memories:
            return 0

        # Sort by access count (ascending) then by created_at (oldest first)
        self.memories.sort(key=lambda m: (m.access_count, m.created_at))

        removed_count = len(self.memories) - max_memories
        self.memories = self.memories[-max_memories:]

        logger.info(
            f"Trimmed {removed_count} memories for user {self.user_id}, "
            f"keeping {len(self.memories)}"
        )

        return removed_count

    def to_dict(self) -> dict:
        """Convert user memories to dictionary for JSON serialization."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "memories": [m.to_dict() for m in self.memories],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UserMemories":
        """Create UserMemories from a dictionary."""
        return cls(
            user_id=data["user_id"],
            username=data["username"],
            memories=[Memory.from_dict(m) for m in data.get("memories", [])],
        )


class MemoryManager:
    """
    Manages long-term memory storage and retrieval for all users.

    Memories are persisted to a JSON file and loaded on startup.
    """

    def __init__(self, config: "Config", storage_path: str = "memories.json") -> None:
        """
        Initialize the memory manager.

        Args:
            config: Bot configuration.
            storage_path: Path to the JSON file for persisting memories.
        """
        self._config = config
        self._storage_path = Path(storage_path)
        self._user_memories: dict[int, UserMemories] = {}

        logger.info(f"MemoryManager initialized with storage at {self._storage_path}")

    def load(self) -> None:
        """Load memories from the JSON storage file."""
        if not self._storage_path.exists():
            logger.info("No existing memory file found, starting fresh")
            return

        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for user_data in data.get("users", []):
                user_mem = UserMemories.from_dict(user_data)
                self._user_memories[user_mem.user_id] = user_mem

            logger.info(f"Loaded memories for {len(self._user_memories)} users")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse memory file: {e}")
        except Exception as e:
            logger.error(f"Failed to load memories: {e}")

    def save(self) -> None:
        """Save memories to the JSON storage file."""
        try:
            data = {
                "version": 1,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "users": [um.to_dict() for um in self._user_memories.values()],
            }

            with open(self._storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            logger.info(f"Saved memories for {len(self._user_memories)} users")

        except Exception as e:
            logger.error(f"Failed to save memories: {e}")

    def get_user_memories(self, user_id: int, username: str = "") -> UserMemories:
        """
        Get or create memory collection for a user.

        Args:
            user_id: The Discord user ID.
            username: The user's display name (used for new users).

        Returns:
            The user's memory collection.
        """
        if user_id not in self._user_memories:
            self._user_memories[user_id] = UserMemories(
                user_id=user_id, username=username
            )
            logger.debug(f"Created new memory collection for user {user_id}")

        # Update username if provided
        if username:
            self._user_memories[user_id].username = username

        return self._user_memories[user_id]

    def add_memories_from_text(
        self,
        user_id: int,
        username: str,
        conversation_text: str,
        extracted_memories: list[str],
    ) -> list[str]:
        """
        Add extracted memories for a user.

        Args:
            user_id: The user's Discord ID.
            username: The user's display name.
            conversation_text: The conversation text memories were extracted from.
            extracted_memories: List of memory strings to add.

        Returns:
            List of successfully added memories.
        """
        user_mem = self.get_user_memories(user_id, username)
        added: list[str] = []

        for memory_text in extracted_memories:
            memory_text = memory_text.strip()
            if memory_text:
                user_mem.add_memory(memory_text)
                added.append(memory_text)

        # Trim to limit
        user_mem.trim_to_limit(self._config.memory_limit)

        # Save after adding
        self.save()

        if added:
            logger.info(f"Added {len(added)} memories for user {user_id}")

        return added

    def get_relevant_context(
        self,
        user_id: int,
        current_context: str,
        username: str = "",
    ) -> str:
        """
        Get relevant memories formatted as context for AI.

        Args:
            user_id: The user's Discord ID.
            current_context: Current conversation context for relevance matching.
            username: The user's display name.

        Returns:
            Formatted string of relevant memories.
        """
        user_mem = self.get_user_memories(user_id, username)
        relevant = user_mem.get_relevant_memories(current_context, limit=5)

        if not relevant:
            return ""

        lines = ["Relevant memories about this user:"]
        for i, memory in enumerate(relevant, 1):
            lines.append(f"  {i}. {memory.content}")

        return "\n".join(lines)

    def get_all_memories_for_user(self, user_id: int) -> list[str]:
        """
        Get all memories for a user as a list of strings.

        Args:
            user_id: The user's Discord ID.

        Returns:
            List of memory contents.
        """
        if user_id not in self._user_memories:
            return []

        return [m.content for m in self._user_memories[user_id].memories]

    def clear_user_memories(self, user_id: int) -> int:
        """
        Clear all memories for a user.

        Args:
            user_id: The user's Discord ID.

        Returns:
            Number of memories cleared.
        """
        if user_id not in self._user_memories:
            return 0

        count = len(self._user_memories[user_id].memories)
        del self._user_memories[user_id]
        self.save()

        logger.info(f"Cleared {count} memories for user {user_id}")
        return count

    def get_stats(self) -> dict:
        """
        Get statistics about stored memories.

        Returns:
            Dictionary with memory statistics.
        """
        total_memories = sum(len(um.memories) for um in self._user_memories.values())

        return {
            "total_users": len(self._user_memories),
            "total_memories": total_memories,
            "average_per_user": (
                total_memories / len(self._user_memories) if self._user_memories else 0
            ),
        }
