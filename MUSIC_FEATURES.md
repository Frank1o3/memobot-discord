# Discord AI ChatBot — Music Features

## 🎵 Music Commands (Slash Commands)

All music commands use Discord's slash-command interface (`/music ...`).

### Voice Channel Controls

| Command | Description |
|---------|-------------|
| `/music join` | Join the voice channel you're currently in |
| `/music leave` | Disconnect from the current voice channel |

### Playback Controls

| Command | Description |
|---------|-------------|
| `/music play <query>` | Play a song from a YouTube URL or search query |
| `/music playlist <url>` | Load an entire playlist from a YouTube (or Spotify) URL |
| `/music pause` | Pause the current track |
| `/music resume` | Resume paused playback |
| `/music skip` | Skip to the next track in the queue |
| `/music stop` | Stop playback and clear the queue |

### Queue Management

| Command | Description |
|---------|-------------|
| `/music queue` | Show the upcoming queue |
| `/music nowplaying` | Show the currently playing track with player controls |
| `/music remove <index>` | Remove a track from the queue by its 1-based position |
| `/music volume <0-100>` | Set the playback volume |

### Interactive Player Controls (Buttons)

When `/music play` or `/music nowplaying` is used, the bot sends an interactive
player message with button controls:

| Button | Action |
|--------|--------|
| ⏮️ | Play previous track (from history) |
| ⏯️ | Toggle pause / resume |
| ⏭️ | Skip to next track |
| 🔉 | Volume down (−10%) |
| 🔊 | Volume up (+10%) |
| ⏹️ | Stop playback and clear queue |
| 📋 | Show queue (ephemeral) |
| 🔀 | Shuffle the queue |
| 🔁 | Cycle repeat mode: Off → Track → Queue → Off |

> **Note:** Button controls are only usable by members in the same voice channel as the bot.

---

## Usage Examples

```
# Join your voice channel
/music join

# Play a song by searching
/music play never gonna give you up

# Play from a YouTube URL (single video)
/music play https://www.youtube.com/watch?v=dQw4w9WgXcQ

# Load an entire playlist
/music playlist https://www.youtube.com/playlist?list=PLxxxxxxxx

# Check the queue
/music queue

# Skip the current song
/music skip

# Adjust volume
/music volume 50

# Leave when done
/music leave
```

---

## Installation Requirements

### Python Dependencies

```toml
[project]
dependencies = [
    "discord-py[voice]>=2.7.1",
    "groq>=1.5.0",
    "pydantic>=2.13.4",
    "yt-dlp>=2024.1.0",
]
```

### System Requirements

**FFmpeg** must be installed and on `PATH`:

```bash
# Ubuntu/Debian
sudo apt-get install ffmpeg

# macOS
brew install ffmpeg

# Windows
# Download from https://ffmpeg.org/download.html and add to PATH
```

---

## How It Works

1. **Join**: Use `/music join` while in a voice channel, or just use `/music play` — the bot auto-joins.
2. **Play**: Search by name or paste a YouTube URL. Playlist URLs are redirected to `/music playlist`.
3. **Playlist**: `/music playlist <url>` defers immediately, extracts tracks **concurrently** (up to 5 at a time), and shows real-time progress (`Added 12/87 tracks...`).
4. **Queue**: Tracks queue automatically. Use button controls or slash commands to manage.
5. **History**: The bot keeps the last 50 played tracks in history so you can go back with ⏮️.
6. **Leave**: Use `/music leave` to disconnect.

---

## Supported Sources

| Source | Single Track | Playlist / Album |
|--------|-------------|-----------------|
| YouTube URL | ✅ | ✅ via `/music playlist` |
| YouTube search | ✅ | — |
| Spotify track | ✅ (resolved via YouTube) | — |
| Spotify playlist | — | ✅ via `/music playlist` |
| Spotify album | — | ✅ via `/music playlist` |

> **Note:** Spotify requires a `spotify_client` to be configured in `SourceResolver`.
> Without it, Spotify URLs fall back to YouTube search.

---

## Technical Notes

- Uses `yt-dlp` for audio extraction from YouTube and other supported sites.
- Audio playback uses `discord.FFmpegPCMAudio` — FFmpeg runs as a separate OS process per voice connection, completely off the main asyncio event loop.
- Each guild has its own isolated `GuildPlayer` instance (independent queue, history, volume, repeat mode).
- Playlist extraction is concurrently bounded by `PLAYLIST_EXTRACT_CONCURRENCY = 5` (configurable in `resolver.py`) to avoid saturating the network or thread pool.
- The bot requires permission to connect to voice channels in your server.
