"""
Base track model for normalized music playback.

Provides a unified interface for tracks from different sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import discord


@dataclass
class Track:
    """
    Normalized track model for music playback.
    
    This model abstracts away the source of the track (YouTube, Spotify, etc.)
    and provides a consistent interface for the player.
    """

    title: str
    """Track title."""

    stream_url: str
    """Direct URL to the playable audio stream."""

    source: str
    """Source identifier (e.g., 'youtube', 'spotify', 'search')."""

    artist: Optional[str] = None
    """Artist/uploader name if available."""

    duration: Optional[int] = None
    """Duration in seconds if available."""

    thumbnail: Optional[str] = None
    """URL to thumbnail/artwork image if available."""

    webpage_url: Optional[str] = None
    """Original URL/webpage for the track if available."""

    requested_by: Optional[discord.Member] = None
    """Discord member who requested this track."""

    album: Optional[str] = None
    """Album name if available (mainly for Spotify)."""

    is_playlist: bool = False
    """Whether this track represents a playlist entry point."""

    playlist_title: Optional[str] = None
    """Title of the playlist if this is a playlist."""

    playlist_tracks: list[Track] = field(default_factory=list)
    """List of tracks if this represents a playlist."""

    @property
    def duration_str(self) -> str:
        """Get duration as a formatted string (MM:SS)."""
        if self.duration is None:
            return "Unknown"

        minutes = int(self.duration // 60)
        seconds = int(self.duration % 60)
        return f"{minutes}:{seconds:02d}"

    @classmethod
    def from_youtube_info(cls, info: dict, requested_by: Optional[discord.Member] = None) -> Track:
        """
        Create a Track from YouTube video info dict.
        
        Args:
            info: Dictionary from yt_dlp extraction.
            requested_by: Discord member who requested this track.
            
        Returns:
            A Track instance.
        """
        return cls(
            title=info.get("title", "Unknown"),
            stream_url=info.get("url", ""),
            source="youtube",
            artist=info.get("uploader", info.get("channel", None)),
            duration=info.get("duration"),
            thumbnail=info.get("thumbnail", None),
            webpage_url=info.get("webpage_url", info.get("url", None)),
            requested_by=requested_by,
        )

    @classmethod
    def from_spotify_track(
        cls,
        track_info: dict,
        stream_url: str,
        requested_by: Optional[discord.Member] = None,
    ) -> Track:
        """
        Create a Track from Spotify track metadata and resolved stream URL.
        
        Args:
            track_info: Dictionary with Spotify track metadata.
            stream_url: Resolved playable stream URL (usually from YouTube).
            requested_by: Discord member who requested this track.
            
        Returns:
            A Track instance.
        """
        artists = track_info.get("artists", [])
        artist_names = [a.get("name", "") for a in artists] if isinstance(artists, list) else []

        return cls(
            title=track_info.get("name", "Unknown"),
            stream_url=stream_url,
            source="spotify",
            artist=", ".join(artist_names) if artist_names else None,
            duration=track_info.get("duration_ms", 0) // 1000 if track_info.get("duration_ms") else None,
            thumbnail=None,
            webpage_url=track_info.get("external_urls", {}).get("spotify", None),
            requested_by=requested_by,
            album=track_info.get("album", {}).get("name", None) if track_info.get("album") else None,
        )

    def to_dict(self) -> dict:
        """
        Convert track to a JSON-serializable dictionary.

        ``requested_by`` is a ``discord.Member`` and is not JSON-safe, so only
        the member's integer ID is stored under the key ``requested_by_id``.
        To reconstruct the full ``Member`` object from ``from_dict()``, resolve
        the ID lazily at the call site using ``guild.get_member(requested_by_id)``
        or ``await guild.fetch_member(requested_by_id)``.
        """
        return {
            "title": self.title,
            "stream_url": self.stream_url,
            "source": self.source,
            "artist": self.artist,
            "duration": self.duration,
            "thumbnail": self.thumbnail,
            "webpage_url": self.webpage_url,
            # Store int ID only — discord.Member is not JSON-serializable.
            "requested_by_id": self.requested_by.id if self.requested_by else None,
            "album": self.album,
            "is_playlist": self.is_playlist,
            "playlist_title": self.playlist_title,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        """
        Create a Track from a dictionary produced by ``to_dict()``.

        ``requested_by`` is set to ``None`` here because resolving a
        ``discord.Member`` from an integer ID requires a ``guild`` reference
        that is not available in this class-method context.

        Callers that need the full ``Member`` object should resolve it lazily::

            track = Track.from_dict(data)
            member = guild.get_member(data.get("requested_by_id"))
        """
        return cls(
            title=data.get("title", "Unknown"),
            stream_url=data.get("stream_url", ""),
            source=data.get("source", "unknown"),
            artist=data.get("artist"),
            duration=data.get("duration"),
            thumbnail=data.get("thumbnail"),
            webpage_url=data.get("webpage_url"),
            # requested_by cannot be reconstructed without a guild reference;
            # callers must resolve requested_by_id themselves.
            requested_by=None,
            album=data.get("album"),
            is_playlist=data.get("is_playlist", False),
            playlist_title=data.get("playlist_title"),
        )
