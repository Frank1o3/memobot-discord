"""
AI module for the Discord AI chatbot.

This module handles all interactions with the Groq API, including:
- Streaming responses
- Conversation summarization
- Memory extraction
- Retry logic and rate limit handling
"""

import asyncio
import logging
from typing import TYPE_CHECKING, AsyncGenerator

from groq import Groq, APIError, RateLimitError, APITimeoutError

from server.prompts import (
    build_summary_prompt,
    build_memory_extraction_prompt,
)
from server.rate_limit import APIRateLimitHandler

if TYPE_CHECKING:
    from server.config import Config

logger = logging.getLogger(__name__)


class AIClient:
    """
    Client for interacting with the Groq AI API.

    Handles streaming responses, retries, and rate limiting for all AI operations.
    """

    def __init__(self, config: "Config") -> None:
        """
        Initialize the AI client.

        Args:
            config: Bot configuration containing API key and settings.
        """
        self._config = config
        self._client = Groq(api_key=config.apikey)
        self._rate_handler = APIRateLimitHandler()

        logger.info(f"AIClient initialized with model {config.model}")

    async def _make_api_call(
        self,
        messages: list[dict[str, str]],
        stream: bool = True,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncGenerator[str, None] | str:
        """
        Make an API call to Groq with retry logic.

        Args:
            messages: List of message dictionaries for the conversation.
            stream: Whether to stream the response.
            max_tokens: Maximum tokens in response.
            temperature: Temperature for generation.

        Returns:
            Streaming generator or complete response string.

        Raises:
            APIError: If the API call fails after retries.
        """
        await self._rate_handler.acquire()

        try:
            self._rate_handler.record_request()

            response = await asyncio.to_thread(
                self._client.chat.completions.create,
                model=self._config.model,
                messages=messages,
                stream=stream,
                max_tokens=max_tokens or self._config.max_output_tokens,
                temperature=temperature
                if temperature is not None
                else self._config.temperature,
            )

            if stream:
                return self._stream_response(response)  # type: ignore[arg-type]
            else:
                return response.choices[0].message.content or ""

        except RateLimitError as e:
            logger.warning(f"Groq rate limit hit: {e}")
            retry_after = self._extract_retry_after(e)
            self._rate_handler.handle_rate_limit_response(retry_after)
            raise

        except APITimeoutError as e:
            logger.error(f"Groq API timeout: {e}")
            raise

        except APIError as e:
            logger.error(f"Groq API error: {e}")
            raise

    def _extract_retry_after(self, error: RateLimitError) -> float | None:
        """
        Extract retry-after value from rate limit error.

        Args:
            error: The rate limit error.

        Returns:
            Retry-after seconds if available.
        """
        # Try to parse from error response
        if hasattr(error, "response") and error.response:
            retry_after = error.response.headers.get("retry-after")
            if retry_after:
                try:
                    return float(retry_after)
                except ValueError:
                    pass
        return None

    def _stream_response(
        self,
        response,
    ) -> AsyncGenerator[str, None]:
        """
        Stream response chunks from the API.

        Args:
            response: The API response stream.

        Yields:
            Response text chunks.
        """
        try:
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.error(f"Error streaming response: {e}")
            raise

    async def generate_response(
        self,
        system_prompt: str,
        user_messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        """
        Generate a streaming response from the AI.

        Args:
            system_prompt: The system prompt to use.
            user_messages: List of user messages in the conversation.

        Yields:
            Response text chunks as they arrive.
        """
        messages = [{"role": "system", "content": system_prompt}] + user_messages

        logger.debug(f"Generating response with {len(user_messages)} messages")

        try:
            stream = self._make_api_call(  # type: ignore[assignment]
                messages=messages,
                stream=True,
            )
            for chunk in stream:
                yield chunk

        except APIError as e:
            logger.error(f"Failed to generate response: {e}")
            yield f"[Error: Unable to process request - {type(e).__name__}]"

    async def summarize_conversation(
        self,
        conversation_text: str,
    ) -> str:
        """
        Summarize a conversation using the AI.

        Args:
            conversation_text: The full conversation text to summarize.

        Returns:
            The generated summary.
        """
        prompt = build_summary_prompt(conversation_text)
        messages = [{"role": "user", "content": prompt}]

        logger.info(f"Summarizing conversation ({len(conversation_text)} chars)")

        try:
            # Use lower temperature for summarization
            result = await self._make_api_call(
                messages=messages,
                stream=False,
                temperature=0.3,
                max_tokens=512,
            )
            return str(result)

        except APIError as e:
            logger.error(f"Failed to summarize conversation: {e}")
            # Return truncated original as fallback
            return conversation_text[:500] + "..."

    async def extract_memories(
        self,
        conversation_text: str,
    ) -> list[str]:
        """
        Extract potential memories from a conversation.

        Args:
            conversation_text: The conversation to analyze.

        Returns:
            List of extracted memory strings.
        """
        prompt = build_memory_extraction_prompt(conversation_text)
        messages = [{"role": "user", "content": prompt}]

        logger.debug(f"Extracting memories from conversation")

        try:
            result = await self._make_api_call(
                messages=messages,
                stream=False,
                temperature=0.1,  # Low temperature for consistent extraction
                max_tokens=256,
            )

            # Parse the result into individual memories
            if isinstance(result, str):
                lines = [line.strip() for line in result.split("\n") if line.strip()]
                # Filter out any meta-text
                memories = [
                    line
                    for line in lines
                    if not line.lower().startswith(("memories:", "here are", "i found"))
                ]
                return memories

            return []

        except APIError as e:
            logger.error(f"Failed to extract memories: {e}")
            return []

    async def decide_reply(
        self,
        context: str,
    ) -> bool:
        """
        Use AI to help decide whether to reply (optional enhancement).

        For now, this uses the decision module's logic. This method could
        be expanded to use AI for more nuanced decisions.

        Args:
            context: The conversation context.

        Returns:
            True if should reply.
        """
        # For now, we rely on the decision module's deterministic logic
        # This could be enhanced with AI-based decision making
        return True  # Placeholder - actual decision made by ReplyDecisionMaker

    def get_stats(self) -> dict:
        """
        Get statistics about API usage.

        Returns:
            Dictionary with API statistics.
        """
        return {
            "model": self._config.model,
            "rate_limits": self._rate_handler.get_stats(),
        }

    async def health_check(self) -> bool:
        """
        Check if the API is accessible.

        Returns:
            True if API is healthy.
        """
        try:
            messages = [{"role": "user", "content": "Respond with just 'OK'"}]
            result = await self._make_api_call(
                messages=messages,
                stream=False,
                max_tokens=5,
            )
            return isinstance(result, str) and len(result) > 0
        except Exception as e:
            logger.error(f"API health check failed: {e}")
            return False
