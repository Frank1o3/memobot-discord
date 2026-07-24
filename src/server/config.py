"""
Configuration module for the Discord AI chatbot.

This module handles loading and validating configuration settings from config.json
using Pydantic v2 for robust validation and type safety.
"""

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """
    Configuration model for the Discord AI chatbot.

    All tunable values are stored in config.json and validated by this model.
    """

    # Discord settings
    token: str = Field(..., description="Discord bot token")
    prefix: str = Field("/", description="Command prefix for prefix commands")
    apikey: str = Field(..., description="Groq API key")

    # Groq AI settings
    model: str = Field(
        "llama-3.3-70b-versatile", description="Groq model to use for inference"
    )
    max_output_tokens: int = Field(
        1024, ge=1, le=8192, description="Maximum tokens in AI response"
    )
    temperature: float = Field(
        0.7,
        ge=0.0,
        le=2.0,
        description="Temperature for AI generation (higher = more creative)",
    )

    # Context handling settings
    max_context_messages: int = Field(
        50, ge=1, le=200, description="Maximum messages to include in context"
    )
    summary_trigger: int = Field(
        100,
        ge=10,
        le=500,
        description="Number of messages before triggering summarization",
    )

    # Reply decision settings
    random_reply_probability: float = Field(
        0.05,
        ge=0.0,
        le=1.0,
        description="Probability of random reply when no other trigger matches",
    )

    # Behavior settings
    typing_speed: float = Field(
        0.03,
        ge=0.001,
        le=0.5,
        description="Seconds per character for typing simulation",
    )
    cooldown_seconds: int = Field(
        3, ge=0, le=60, description="Cooldown between responses in seconds"
    )

    # Memory settings
    memory_limit: int = Field(
        100, ge=1, le=1000, description="Maximum number of memories to store per user"
    )

    @field_validator("prefix")
    @classmethod
    def validate_prefix(cls, v: str) -> str:
        """Ensure prefix is not empty."""
        if not v:
            raise ValueError("Prefix cannot be empty")
        return v

    @field_validator("token", "apikey")
    @classmethod
    def validate_not_empty(cls, v: str, info: Any) -> str:
        """Ensure required fields are not empty."""
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} cannot be empty")
        return v.strip()

    class Config:
        """Pydantic configuration."""

        json_schema_extra = {
            "example": {
                "token": "your_discord_token",
                "prefix": "/",
                "apikey": "your_groq_api_key",
                "model": "llama-3.3-70b-versatile",
                "max_context_messages": 50,
                "summary_trigger": 100,
                "random_reply_probability": 0.05,
                "typing_speed": 0.03,
                "cooldown_seconds": 3,
                "memory_limit": 100,
                "max_output_tokens": 1024,
                "temperature": 0.7,
            }
        }


class ConfigManager:
    """
    Manages loading and accessing configuration settings.

    This class provides a singleton-like interface for accessing configuration
    throughout the application without global mutable state.
    """

    _instance: ConfigManager | None = None
    _config: Config | None = None

    def __new__(cls) -> ConfigManager:
        """Create or return existing ConfigManager instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load(self, config_path: str = "config.json") -> Config:
        """
        Load configuration from JSON file.

        Args:
            config_path: Path to the configuration file.

        Returns:
            Validated Config object.

        Raises:
            FileNotFoundError: If config file doesn't exist.
            json.JSONDecodeError: If config file is invalid JSON.
            pydantic.ValidationError: If config values fail validation.
        """
        path = Path(config_path)
        logger.info(f"Loading configuration from {path.absolute()}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._config = Config(**data)
        logger.info("Configuration loaded successfully")
        return self._config

    @property
    def config(self) -> Config:
        """
        Get the current configuration.

        Returns:
            The loaded Config object.

        Raises:
            RuntimeError: If configuration hasn't been loaded yet.
        """
        if self._config is None:
            raise RuntimeError("Configuration not loaded. Call load() first.")
        return self._config

    def reload(self, config_path: str = "config.json") -> Config:
        """
        Reload configuration from file.

        Args:
            config_path: Path to the configuration file.

        Returns:
            Newly loaded Config object.
        """
        self._config = None
        return self.load(config_path)


# Global config manager instance (only instance, not mutable state)
config_manager = ConfigManager()


def get_config() -> Config:
    """
    Get the current configuration.

    Returns:
        The loaded Config object.

    Raises:
        RuntimeError: If configuration hasn't been loaded yet.
    """
    return config_manager.config
