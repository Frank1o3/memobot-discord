"""
Music sources package for handling multi-source playback.

Provides source detection, extraction, and normalization for:
- YouTube (videos, playlists)
- Spotify (tracks, playlists, albums)
- Generic search queries
"""

from .base import Track
from .resolver import SourceResolver

__all__ = ["Track", "SourceResolver"]
