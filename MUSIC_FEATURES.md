# Discord AI ChatBot - Music Feature Upgrade

## 🎵 New Music Features

Your Discord bot has been upgraded with full music playback capabilities! The bot can now:

### Voice Channel Commands
- `!join` - Join your voice channel
- `!leave` - Leave the voice channel

### Playback Controls
- `!play <URL/search>` - Play a song from YouTube URL or search query
- `!pause` - Pause the current song
- `!resume` - Resume paused playback
- `!skip` - Skip to the next song
- `!stop` - Stop playback and clear the queue

### Queue Management
- `!queue` - Display the current song queue
- `!nowplaying` - Show the currently playing song
- `!remove <index>` - Remove a song from the queue by index
- `!volume <0-100>` - Set the playback volume

### Help
- `!music` - Display music command help

## Installation Requirements

The following dependencies have been added:

```toml
[project]
dependencies = [
    "discord-py[voice]>=2.7.1",
    "groq>=1.5.0",
    "pydantic>=2.13.4",
    "yt-dlp>=2024.1.0",  # NEW: For YouTube/music downloading
]
```

### System Requirements

Make sure you have **FFmpeg** installed on your system:

**Ubuntu/Debian:**
```bash
sudo apt-get install ffmpeg
```

**Windows:**
Download from https://ffmpeg.org/download.html and add to PATH

**macOS:**
```bash
brew install ffmpeg
```

## Usage Examples

```bash
# Join your voice channel
!join

# Play a song by searching
!play never gonna give you up

# Play from a YouTube URL
!play https://www.youtube.com/watch?v=dQw4w9WgXcQ

# Check the queue
!queue

# Skip the current song
!skip

# Adjust volume
!volume 50

# Leave when done
!leave
```

## How It Works

1. **Join**: Use `!join` while in a voice channel, or just use `!play` and the bot will auto-join
2. **Play**: Search by song name or paste a YouTube URL
3. **Queue**: Songs are queued automatically when multiple are requested
4. **Control**: Use pause, resume, skip, and volume commands to control playback
5. **Leave**: Use `!leave` to disconnect the bot

## Technical Details

- Uses `yt-dlp` for extracting audio from YouTube and other supported sites
- Implements a proper queue system with automatic song transitions
- Supports volume control (0-100%)
- Handles connection errors gracefully
- Automatically disconnects when the queue is empty

## Notes

- The bot needs permission to connect to voice channels in your server
- Audio quality depends on the source video
- Some regions may have restrictions on certain content
- The bot can only play audio from supported sites (YouTube, SoundCloud, etc.)
