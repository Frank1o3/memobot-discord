"""
Source resolver for detecting and extracting music from various sources.

Handles:
- YouTube videos and playlists
- Spotify tracks, playlists, and albums (with YouTube resolution for playback)
- Generic search queries
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

import yt_dlp

from .base import Track

logger = logging.getLogger(__name__)


class SourceType(Enum):
    """Type of music source."""

    YOUTUBE_VIDEO = auto()
    YOUTUBE_PLAYLIST = auto()
    SPOTIFY_TRACK = auto()
    SPOTIFY_PLAYLIST = auto()
    SPOTIFY_ALBUM = auto()
    SEARCH = auto()
    UNKNOWN = auto()


@dataclass
class ExtractionResult:
    """Result of source extraction."""

    source_type: SourceType
    tracks: list[Track]
    playlist_title: Optional[str] = None
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class SourceResolver:
    """
    Resolves music from various sources into playable tracks.
    
    This class handles detection of input type and extraction using yt_dlp
    and spotipy for Spotify metadata.
    """

    # URL patterns for source detection
    YOUTUBE_VIDEO_PATTERN = re.compile(
        r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})"
    )
    YOUTUBE_PLAYLIST_PATTERN = re.compile(
        r"(?:https?://)?(?:www\.)?youtube\.com/playlist\?list=([a-zA-Z0-9_-]+)"
    )
    SPOTIFY_TRACK_PATTERN = re.compile(
        r"(?:https?://)?open\.spotify\.com/track/([a-zA-Z0-9]+)"
    )
    SPOTIFY_PLAYLIST_PATTERN = re.compile(
        r"(?:https?://)?open\.spotify\.com/playlist/([a-zA-Z0-9]+)"
    )
    SPOTIFY_ALBUM_PATTERN = re.compile(
        r"(?:https?://)?open\.spotify\.com/album/([a-zA-Z0-9]+)"
    )

    def __init__(self, spotify_client=None):
        """
        Initialize the source resolver.
        
        Args:
            spotify_client: Optional spotipy client for Spotify metadata.
                           If not provided, Spotify URLs will be resolved
                           by searching YouTube with track metadata.
        """
        self._spotify_client = spotify_client
        self._ydl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "noplaylist": False,
        }

    def detect_source(self, query: str) -> SourceType:
        """
        Detect the type of source from a query string.
        
        Args:
            query: The input query (URL or search term).
            
        Returns:
            The detected SourceType.
        """
        if not query:
            return SourceType.UNKNOWN

        # Check for YouTube video
        if self.YOUTUBE_VIDEO_PATTERN.match(query):
            return SourceType.YOUTUBE_VIDEO

        # Check for YouTube playlist
        if self.YOUTUBE_PLAYLIST_PATTERN.match(query):
            return SourceType.YOUTUBE_PLAYLIST

        # Check for Spotify track
        if self.SPOTIFY_TRACK_PATTERN.match(query):
            return SourceType.SPOTIFY_TRACK

        # Check for Spotify playlist
        if self.SPOTIFY_PLAYLIST_PATTERN.match(query):
            return SourceType.SPOTIFY_PLAYLIST

        # Check for Spotify album
        if self.SPOTIFY_ALBUM_PATTERN.match(query):
            return SourceType.SPOTIFY_ALBUM

        # Check if it's any other http(s) URL - might be unsupported
        if query.startswith(("http://", "https://")):
            # Could be a YouTube URL we didn't match, or another service
            # Let yt_dlp handle it
            return SourceType.YOUTUBE_VIDEO  # Default to YouTube extractor

        # Default to search
        return SourceType.SEARCH

    async def resolve(
        self,
        query: str,
        requested_by=None,
    ) -> ExtractionResult:
        """
        Resolve a query into playable tracks.
        
        Args:
            query: The input query (URL or search term).
            requested_by: Discord member who made the request.
            
        Returns:
            ExtractionResult with tracks and metadata.
        """
        source_type = self.detect_source(query)
        logger.info("Detected source type: %s for query: %s", source_type.name, query[:50] if len(query) > 50 else query)

        try:
            if source_type == SourceType.YOUTUBE_VIDEO:
                return await self._extract_youtube_video(query, requested_by)
            elif source_type == SourceType.YOUTUBE_PLAYLIST:
                return await self._extract_youtube_playlist(query, requested_by)
            elif source_type == SourceType.SPOTIFY_TRACK:
                return await self._extract_spotify_track(query, requested_by)
            elif source_type == SourceType.SPOTIFY_PLAYLIST:
                return await self._extract_spotify_playlist(query, requested_by)
            elif source_type == SourceType.SPOTIFY_ALBUM:
                return await self._extract_spotify_album(query, requested_by)
            elif source_type == SourceType.SEARCH:
                return await self._search_youtube(query, requested_by)
            else:
                return ExtractionResult(
                    source_type=SourceType.UNKNOWN,
                    tracks=[],
                    errors=["Unknown or unsupported source"],
                )
        except Exception as e:
            logger.error("Failed to resolve query: %s", e, exc_info=True)
            return ExtractionResult(
                source_type=source_type,
                tracks=[],
                errors=[str(e)],
            )

    async def _extract_youtube_video(
        self,
        url: str,
        requested_by=None,
    ) -> ExtractionResult:
        """Extract a single YouTube video."""
        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(
                None,
                lambda: self._extract_info(url),
            )

            if info:
                track = Track.from_youtube_info(info, requested_by)
                return ExtractionResult(
                    source_type=SourceType.YOUTUBE_VIDEO,
                    tracks=[track],
                )

            return ExtractionResult(
                source_type=SourceType.YOUTUBE_VIDEO,
                tracks=[],
                errors=["Failed to extract video info"],
            )
        except Exception as e:
            logger.error("Failed to extract YouTube video: %s", e, exc_info=True)
            return ExtractionResult(
                source_type=SourceType.YOUTUBE_VIDEO,
                tracks=[],
                errors=[f"Failed to extract video: {e}"],
            )

    async def _extract_youtube_playlist(
        self,
        url: str,
        requested_by=None,
    ) -> ExtractionResult:
        """Extract a YouTube playlist."""
        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(
                None,
                lambda: self._extract_info(url, playlist_extract=True),
            )

            if not info:
                return ExtractionResult(
                    source_type=SourceType.YOUTUBE_PLAYLIST,
                    tracks=[],
                    errors=["Failed to extract playlist info"],
                )

            tracks = []
            errors = []
            playlist_title = info.get("title", "Unknown Playlist")

            entries = info.get("entries", [])
            for entry in entries:
                if not entry:
                    continue

                try:
                    # For playlist entries, we need to get the actual stream URL
                    # Extract each video individually to get stream URL
                    video_url = entry.get("url", entry.get("webpage_url", ""))
                    if video_url:
                        video_info = self._extract_info(video_url)
                        if video_info:
                            track = Track.from_youtube_info(video_info, requested_by)
                            tracks.append(track)
                except Exception as e:
                    logger.warning("Failed to extract playlist entry: %s", e)
                    errors.append(f"Failed to extract entry: {e}")

            return ExtractionResult(
                source_type=SourceType.YOUTUBE_PLAYLIST,
                tracks=tracks,
                playlist_title=playlist_title,
                errors=errors,
            )
        except Exception as e:
            logger.error("Failed to extract YouTube playlist: %s", e, exc_info=True)
            return ExtractionResult(
                source_type=SourceType.YOUTUBE_PLAYLIST,
                tracks=[],
                errors=[f"Failed to extract playlist: {e}"],
            )

    async def _extract_spotify_track(
        self,
        url: str,
        requested_by=None,
    ) -> ExtractionResult:
        """
        Extract a Spotify track and resolve to YouTube for playback.
        
        Since Spotify doesn't provide direct audio streams, we:
        1. Get track metadata from Spotify (if client available)
        2. Search YouTube for matching track
        3. Return YouTube stream with Spotify metadata
        """
        try:
            # Try to get Spotify metadata
            spotify_metadata = await self._get_spotify_track_metadata(url)

            if spotify_metadata:
                # Search YouTube for this track
                search_query = f"{spotify_metadata.get('name', '')} {spotify_metadata.get('artist', '')}"
                youtube_result = await self._search_youtube(search_query, requested_by)

                if youtube_result.tracks:
                    # Use YouTube stream but keep Spotify metadata
                    track = youtube_result.tracks[0]
                    # Override with Spotify metadata
                    track.source = "spotify"
                    track.webpage_url = url
                    if spotify_metadata.get("artist"):
                        track.artist = spotify_metadata["artist"]
                    if spotify_metadata.get("duration"):
                        track.duration = spotify_metadata["duration"]

                    return ExtractionResult(
                        source_type=SourceType.SPOTIFY_TRACK,
                        tracks=[track],
                    )

            # Fallback: treat the URL as a search query
            return await self._search_youtube(url, requested_by)

        except Exception as e:
            logger.error("Failed to extract Spotify track: %s", e, exc_info=True)
            # Fallback to search
            return await self._search_youtube(url, requested_by)

    async def _extract_spotify_playlist(
        self,
        url: str,
        requested_by=None,
    ) -> ExtractionResult:
        """
        Extract a Spotify playlist and resolve tracks to YouTube.
        """
        try:
            # Get Spotify playlist tracks
            spotify_tracks = await self._get_spotify_playlist_tracks(url)

            if not spotify_tracks:
                # Fallback to search
                return await self._search_youtube(url, requested_by)

            tracks = []
            errors = []
            playlist_title = None

            for sp_track in spotify_tracks:
                try:
                    search_query = f"{sp_track.get('name', '')} {sp_track.get('artist', '')}"
                    youtube_result = await self._search_youtube(search_query, requested_by)

                    if youtube_result.tracks:
                        track = youtube_result.tracks[0]
                        track.source = "spotify"
                        track.webpage_url = sp_track.get("spotify_url", url)
                        tracks.append(track)
                    else:
                        errors.append(f"Could not resolve: {search_query}")
                except Exception as e:
                    logger.warning("Failed to resolve Spotify track: %s", e)
                    errors.append(f"Failed to resolve track: {e}")

            if spotify_tracks and spotify_tracks[0].get("playlist_name"):
                playlist_title = spotify_tracks[0]["playlist_name"]

            return ExtractionResult(
                source_type=SourceType.SPOTIFY_PLAYLIST,
                tracks=tracks,
                playlist_title=playlist_title,
                errors=errors,
            )

        except Exception as e:
            logger.error("Failed to extract Spotify playlist: %s", e, exc_info=True)
            return ExtractionResult(
                source_type=SourceType.SPOTIFY_PLAYLIST,
                tracks=[],
                errors=[f"Failed to extract playlist: {e}"],
            )

    async def _extract_spotify_album(
        self,
        url: str,
        requested_by=None,
    ) -> ExtractionResult:
        """
        Extract a Spotify album and resolve tracks to YouTube.
        """
        try:
            # Get Spotify album tracks
            spotify_tracks = await self._get_spotify_album_tracks(url)

            if not spotify_tracks:
                # Fallback to search
                return await self._search_youtube(url, requested_by)

            tracks = []
            errors = []
            album_title = None

            for sp_track in spotify_tracks:
                try:
                    search_query = f"{sp_track.get('name', '')} {sp_track.get('artist', '')}"
                    youtube_result = await self._search_youtube(search_query, requested_by)

                    if youtube_result.tracks:
                        track = youtube_result.tracks[0]
                        track.source = "spotify"
                        track.webpage_url = sp_track.get("spotify_url", url)
                        track.album = sp_track.get("album_name")
                        tracks.append(track)
                    else:
                        errors.append(f"Could not resolve: {search_query}")
                except Exception as e:
                    logger.warning("Failed to resolve album track: %s", e)
                    errors.append(f"Failed to resolve track: {e}")

            if spotify_tracks and spotify_tracks[0].get("album_name"):
                album_title = spotify_tracks[0]["album_name"]

            return ExtractionResult(
                source_type=SourceType.SPOTIFY_ALBUM,
                tracks=tracks,
                playlist_title=album_title,
                errors=errors,
            )

        except Exception as e:
            logger.error("Failed to extract Spotify album: %s", e, exc_info=True)
            return ExtractionResult(
                source_type=SourceType.SPOTIFY_ALBUM,
                tracks=[],
                errors=[f"Failed to extract album: {e}"],
            )

    async def _search_youtube(
        self,
        query: str,
        requested_by=None,
    ) -> ExtractionResult:
        """Search YouTube for a query."""
        try:
            search_opts = {
                **self._ydl_opts,
                "default_search": "ytsearch1",
                "extract_flat": False,
            }

            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(
                None,
                lambda: self._extract_info(f"ytsearch:{query}", search_opts),
            )

            if info and "entries" in info and info["entries"]:
                result = info["entries"][0]
                track = Track.from_youtube_info(result, requested_by)
                return ExtractionResult(
                    source_type=SourceType.SEARCH,
                    tracks=[track],
                )

            return ExtractionResult(
                source_type=SourceType.SEARCH,
                tracks=[],
                errors=["No results found"],
            )
        except Exception as e:
            logger.error("Failed to search YouTube: %s", e, exc_info=True)
            return ExtractionResult(
                source_type=SourceType.SEARCH,
                tracks=[],
                errors=[f"Search failed: {e}"],
            )

    def _extract_info(self, url: str, opts: dict = None, playlist_extract: bool = False) -> Optional[dict]:
        """
        Extract info using yt_dlp synchronously.
        
        Args:
            url: URL to extract.
            opts: Optional yt_dlp options override.
            playlist_extract: Whether to extract full playlist.
            
        Returns:
            Extracted info dictionary or None.
        """
        options = {**self._ydl_opts}
        if opts:
            options.update(opts)

        if playlist_extract:
            options["playlist_extract"] = True

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as e:
            logger.debug("yt_dlp extraction failed for %s: %s", url[:50], e)
            return None

    async def _get_spotify_track_metadata(self, url: str) -> Optional[dict]:
        """
        Get track metadata from Spotify.
        
        Returns None if Spotify client is not available.
        """
        if not self._spotify_client:
            return None

        try:
            match = self.SPOTIFY_TRACK_PATTERN.search(url)
            if not match:
                return None

            track_id = match.group(1)
            track = await asyncio.to_thread(self._spotify_client.track, track_id)

            if track:
                artists = [a["name"] for a in track.get("artists", [])]
                return {
                    "name": track.get("name"),
                    "artist": ", ".join(artists),
                    "duration": track.get("duration_ms", 0) // 1000,
                    "album": track.get("album", {}).get("name"),
                }
        except Exception as e:
            logger.debug("Spotify track metadata failed: %s", e)

        return None

    async def _get_spotify_playlist_tracks(self, url: str) -> list[dict]:
        """
        Get tracks from a Spotify playlist.
        
        Returns empty list if Spotify client is not available.
        """
        if not self._spotify_client:
            return []

        try:
            match = self.SPOTIFY_PLAYLIST_PATTERN.search(url)
            if not match:
                return []

            playlist_id = match.group(1)
            playlist = await asyncio.to_thread(self._spotify_client.playlist, playlist_id)

            if not playlist:
                return []

            tracks = []
            playlist_name = playlist.get("name", "Unknown Playlist")

            for item in playlist.get("tracks", {}).get("items", []):
                track = item.get("track")
                if track:
                    artists = [a["name"] for a in track.get("artists", [])]
                    tracks.append({
                        "name": track.get("name"),
                        "artist": ", ".join(artists),
                        "album_name": track.get("album", {}).get("name"),
                        "spotify_url": track.get("external_urls", {}).get("spotify"),
                        "playlist_name": playlist_name,
                    })

            return tracks
        except Exception as e:
            logger.debug("Spotify playlist fetch failed: %s", e)
            return []

    async def _get_spotify_album_tracks(self, url: str) -> list[dict]:
        """
        Get tracks from a Spotify album.
        
        Returns empty list if Spotify client is not available.
        """
        if not self._spotify_client:
            return []

        try:
            match = self.SPOTIFY_ALBUM_PATTERN.search(url)
            if not match:
                return []

            album_id = match.group(1)
            album = await asyncio.to_thread(self._spotify_client.album, album_id)

            if not album:
                return []

            tracks = []
            album_name = album.get("name", "Unknown Album")

            for track in album.get("tracks", {}).get("items", []):
                artists = [a["name"] for a in track.get("artists", [])]
                tracks.append({
                    "name": track.get("name"),
                    "artist": ", ".join(artists),
                    "album_name": album_name,
                    "spotify_url": track.get("external_urls", {}).get("spotify"),
                })

            return tracks
        except Exception as e:
            logger.debug("Spotify album fetch failed: %s", e)
            return []
