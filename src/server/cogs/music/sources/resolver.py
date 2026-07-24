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
from typing import Callable, Coroutine, Optional, Any

import yt_dlp

from .base import Track

logger = logging.getLogger(__name__)

# Maximum number of concurrent yt-dlp extractions during playlist loading.
# Raising this speeds up large playlists but increases memory and network load.
PLAYLIST_EXTRACT_CONCURRENCY = 5


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

        For single tracks/searches only. Playlist URLs should go through
        extract_playlist_concurrent() instead (called from /music playlist).

        Args:
            query: The input query (URL or search term).
            requested_by: Discord member who made the request.

        Returns:
            ExtractionResult with tracks and metadata.
        """
        source_type = self.detect_source(query)
        logger.info(
            "Detected source type: %s for query: %s",
            source_type.name,
            query[:50] if len(query) > 50 else query,
        )

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

    async def extract_playlist_concurrent(
        self,
        url: str,
        requested_by=None,
        progress_callback: Optional[
            Callable[[int, int], Coroutine[Any, Any, None]]
        ] = None,
    ) -> ExtractionResult:
        """
        Extract all tracks from a playlist URL concurrently.

        Uses an asyncio.Semaphore bounded to PLAYLIST_EXTRACT_CONCURRENCY to
        avoid saturating the thread pool or the network. Failures for individual
        entries are collected in ExtractionResult.errors and do not abort the
        whole gather.

        Args:
            url: The playlist URL (YouTube or Spotify).
            requested_by: Discord member who requested the playlist.
            progress_callback: Optional async callable(added, total) called
                every ~10 tracks or on every completion to report progress.
                Errors in the callback are swallowed.

        Returns:
            ExtractionResult with all successfully extracted tracks.
        """
        source_type = self.detect_source(url)

        if source_type == SourceType.SPOTIFY_PLAYLIST:
            return await self._extract_spotify_playlist_concurrent(
                url, requested_by, progress_callback
            )
        elif source_type == SourceType.SPOTIFY_ALBUM:
            return await self._extract_spotify_album_concurrent(
                url, requested_by, progress_callback
            )
        else:
            # Default: treat as YouTube playlist (or let yt_dlp figure it out)
            return await self._extract_youtube_playlist_concurrent(
                url, requested_by, progress_callback
            )

    # ------------------------------------------------------------------
    # YouTube
    # ------------------------------------------------------------------

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
        """
        Extract a YouTube playlist sequentially.

        For large playlists prefer extract_playlist_concurrent() which uses
        asyncio.gather + Semaphore for bounded concurrency.
        """
        return await self._extract_youtube_playlist_concurrent(
            url, requested_by, progress_callback=None
        )

    async def _extract_youtube_playlist_concurrent(
        self,
        url: str,
        requested_by=None,
        progress_callback: Optional[
            Callable[[int, int], Coroutine[Any, Any, None]]
        ] = None,
    ) -> ExtractionResult:
        """
        Extract a YouTube playlist with bounded concurrent extraction.

        Fetches the playlist metadata (flat) first to get the entry list, then
        extracts each video's stream URL concurrently using an asyncio.Semaphore
        capped at PLAYLIST_EXTRACT_CONCURRENCY.
        """
        try:
            loop = asyncio.get_event_loop()

            # Step 1: Get flat playlist metadata (fast — no per-video extraction)
            flat_opts = {
                **self._ydl_opts,
                "extract_flat": True,
                "noplaylist": False,
            }
            info = await loop.run_in_executor(
                None,
                lambda: self._extract_info(url, opts=flat_opts),
            )

            if not info:
                return ExtractionResult(
                    source_type=SourceType.YOUTUBE_PLAYLIST,
                    tracks=[],
                    errors=["Failed to extract playlist info"],
                )

            playlist_title = info.get("title", "Unknown Playlist")
            entries = [e for e in info.get("entries", []) if e]
            total = len(entries)

            if total == 0:
                return ExtractionResult(
                    source_type=SourceType.YOUTUBE_PLAYLIST,
                    tracks=[],
                    playlist_title=playlist_title,
                    errors=["Playlist appears to be empty"],
                )

            logger.info(
                "Extracting %d tracks from playlist '%s' (concurrency=%d)",
                total,
                playlist_title,
                PLAYLIST_EXTRACT_CONCURRENCY,
            )

            tracks: list[Optional[Track]] = []
            errors: list[str] = []
            sem = asyncio.Semaphore(PLAYLIST_EXTRACT_CONCURRENCY)
            completed = 0

            async def extract_one(entry: dict) -> Optional[Track]:
                """Extract a single playlist entry inside the semaphore."""
                nonlocal completed
                async with sem:
                    video_url = entry.get("url", entry.get("webpage_url", ""))
                    if not video_url:
                        errors.append(f"Entry '{entry.get('title', '?')}' has no URL")
                        completed += 1
                        return None
                    try:
                        video_info = await loop.run_in_executor(
                            None,
                            lambda u=video_url: self._extract_info(u),
                        )
                        if video_info:
                            completed += 1
                            # Fire progress callback every 10 tracks
                            if (
                                progress_callback is not None
                                and completed % 10 == 0
                            ):
                                try:
                                    await progress_callback(completed, total)
                                except Exception:
                                    pass
                            return Track.from_youtube_info(video_info, requested_by)
                        else:
                            errors.append(
                                f"Could not extract: {entry.get('title', video_url)}"
                            )
                            completed += 1
                            return None
                    except Exception as exc:
                        logger.warning(
                            "Failed to extract playlist entry '%s': %s",
                            entry.get("title", video_url),
                            exc,
                        )
                        errors.append(
                            f"Failed to extract '{entry.get('title', '?')}': {exc}"
                        )
                        completed += 1
                        return None

            # Step 2: Gather all entries concurrently (failures return None)
            results = await asyncio.gather(
                *(extract_one(e) for e in entries),
                return_exceptions=False,
            )

            resolved_tracks = [t for t in results if t is not None]

            # Final progress update
            if progress_callback is not None:
                try:
                    await progress_callback(len(resolved_tracks), total)
                except Exception:
                    pass

            return ExtractionResult(
                source_type=SourceType.YOUTUBE_PLAYLIST,
                tracks=resolved_tracks,
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

    # ------------------------------------------------------------------
    # Spotify
    # ------------------------------------------------------------------

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
        Extract a Spotify playlist sequentially.

        For large playlists use extract_playlist_concurrent() instead.
        """
        return await self._extract_spotify_playlist_concurrent(
            url, requested_by, progress_callback=None
        )

    async def _extract_spotify_playlist_concurrent(
        self,
        url: str,
        requested_by=None,
        progress_callback: Optional[
            Callable[[int, int], Coroutine[Any, Any, None]]
        ] = None,
    ) -> ExtractionResult:
        """Extract a Spotify playlist with bounded concurrent YouTube resolution."""
        try:
            spotify_tracks = await self._get_spotify_playlist_tracks(url)

            if not spotify_tracks:
                return await self._search_youtube(url, requested_by)

            total = len(spotify_tracks)
            playlist_title: Optional[str] = (
                spotify_tracks[0].get("playlist_name") if spotify_tracks else None
            )

            tracks: list[Track] = []
            errors: list[str] = []
            sem = asyncio.Semaphore(PLAYLIST_EXTRACT_CONCURRENCY)
            completed = 0

            async def resolve_one(sp_track: dict) -> Optional[Track]:
                nonlocal completed
                async with sem:
                    try:
                        search_query = (
                            f"{sp_track.get('name', '')} {sp_track.get('artist', '')}"
                        )
                        youtube_result = await self._search_youtube(
                            search_query, requested_by
                        )
                        completed += 1
                        if progress_callback is not None and completed % 10 == 0:
                            try:
                                await progress_callback(completed, total)
                            except Exception:
                                pass
                        if youtube_result.tracks:
                            track = youtube_result.tracks[0]
                            track.source = "spotify"
                            track.webpage_url = sp_track.get("spotify_url", url)
                            return track
                        else:
                            errors.append(f"Could not resolve: {search_query}")
                            return None
                    except Exception as exc:
                        logger.warning("Failed to resolve Spotify track: %s", exc)
                        errors.append(f"Failed to resolve track: {exc}")
                        completed += 1
                        return None

            results = await asyncio.gather(
                *(resolve_one(t) for t in spotify_tracks),
                return_exceptions=False,
            )

            resolved_tracks = [t for t in results if t is not None]

            if progress_callback is not None:
                try:
                    await progress_callback(len(resolved_tracks), total)
                except Exception:
                    pass

            return ExtractionResult(
                source_type=SourceType.SPOTIFY_PLAYLIST,
                tracks=resolved_tracks,
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
        Extract a Spotify album sequentially.

        For large albums use extract_playlist_concurrent() instead.
        """
        return await self._extract_spotify_album_concurrent(
            url, requested_by, progress_callback=None
        )

    async def _extract_spotify_album_concurrent(
        self,
        url: str,
        requested_by=None,
        progress_callback: Optional[
            Callable[[int, int], Coroutine[Any, Any, None]]
        ] = None,
    ) -> ExtractionResult:
        """Extract a Spotify album with bounded concurrent YouTube resolution."""
        try:
            spotify_tracks = await self._get_spotify_album_tracks(url)

            if not spotify_tracks:
                return await self._search_youtube(url, requested_by)

            total = len(spotify_tracks)
            album_title: Optional[str] = (
                spotify_tracks[0].get("album_name") if spotify_tracks else None
            )

            errors: list[str] = []
            sem = asyncio.Semaphore(PLAYLIST_EXTRACT_CONCURRENCY)
            completed = 0

            async def resolve_one(sp_track: dict) -> Optional[Track]:
                nonlocal completed
                async with sem:
                    try:
                        search_query = (
                            f"{sp_track.get('name', '')} {sp_track.get('artist', '')}"
                        )
                        youtube_result = await self._search_youtube(
                            search_query, requested_by
                        )
                        completed += 1
                        if progress_callback is not None and completed % 10 == 0:
                            try:
                                await progress_callback(completed, total)
                            except Exception:
                                pass
                        if youtube_result.tracks:
                            track = youtube_result.tracks[0]
                            track.source = "spotify"
                            track.webpage_url = sp_track.get("spotify_url", url)
                            track.album = sp_track.get("album_name")
                            return track
                        else:
                            errors.append(f"Could not resolve: {search_query}")
                            return None
                    except Exception as exc:
                        logger.warning("Failed to resolve album track: %s", exc)
                        errors.append(f"Failed to resolve track: {exc}")
                        completed += 1
                        return None

            results = await asyncio.gather(
                *(resolve_one(t) for t in spotify_tracks),
                return_exceptions=False,
            )

            resolved_tracks = [t for t in results if t is not None]

            if progress_callback is not None:
                try:
                    await progress_callback(len(resolved_tracks), total)
                except Exception:
                    pass

            return ExtractionResult(
                source_type=SourceType.SPOTIFY_ALBUM,
                tracks=resolved_tracks,
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

        This is always called via loop.run_in_executor() to avoid blocking
        the main asyncio event loop.

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

    # ------------------------------------------------------------------
    # Spotify metadata helpers
    # ------------------------------------------------------------------

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
