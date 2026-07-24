# Audio Architecture Analysis

## Overview

This document analyzes how audio playback works in this Discord bot (discord.py 2.7.1) and identifies what actually blocks the event loop.

## How `discord.FFmpegPCMAudio` Works

### Current Implementation

When `discord.FFmpegPCMAudio` is instantiated (e.g., in `player.py:181` or `music.py:130`):

```python
source = discord.FFmpegPCMAudio(
    song["url"],  # or track.stream_url
    **ffmpeg_options,
)
```

**Key findings:**

1. **FFmpeg runs as a separate OS process** - discord.py spawns FFmpeg as a subprocess that pipes raw PCM audio data to the bot.

2. **Audio sending runs in a dedicated thread** - discord.py's voice client has its own internal playback loop that runs in a separate thread (not the main asyncio event loop). This thread:
   - Reads PCM frames from the FFmpeg subprocess stdout
   - Handles packet timing and jitter buffering
   - Sends UDP packets to Discord's voice server

3. **Per-guild isolation** - Each `GuildPlayer` (or legacy `VoiceState`) has its own:
   - `discord.VoiceClient` instance
   - `FFmpegPCMAudio` subprocess
   - discord.py's internal playback thread

### What Does NOT Block the Event Loop

- **Audio encoding**: Already handled by FFmpeg in a separate process
- **Audio packet sending**: Already handled by discord.py's voice thread
- **Playback callbacks**: The `after_playing` callback is scheduled via `asyncio.run_coroutine_threadsafe()` back onto the main loop, but this is a lightweight operation

### What DOES Block the Event Loop Today

The primary source of event loop blocking is **synchronous yt-dlp extraction calls**:

```python
# In resolver.py:190-193
loop = asyncio.get_event_loop()
info = await loop.run_in_executor(
    None,
    lambda: self._extract_info(url),  # <-- This is synchronous
)
```

While these are wrapped in `run_in_executor`, issues can arise when:
1. Multiple guilds simultaneously extract large playlists (sequential extraction within each playlist)
2. The default thread pool executor becomes saturated

## Recommendation: Do NOT Add Custom Multiprocessing for Audio

**Conclusion**: Since per-guild playback is already isolated (each `GuildPlayer` has its own `FFmpegPCMAudio` subprocess + discord.py's own playback thread), **do not add a custom multiprocessing pool for audio playback**.

### Reasons:

1. **Discord.py already handles isolation**: Each voice connection has its own thread and FFmpeg subprocess. Adding another layer would duplicate work.

2. **No shared-thread contention**: The audio sending path doesn't touch the main event loop at all. There's no cross-guild contention in the playback path.

3. **Added complexity without benefit**: A custom process pool for audio would require:
   - Managing process lifecycle
   - Serializing/deserializing audio state
   - Handling inter-process communication
   - All for zero latency gain

### Where Threading/Executor Tuning MAY Help

If profiling shows yt-dlp extraction is causing delays:

1. **Increase default executor thread pool size** (if extraction calls pile up):
   ```python
   loop.set_default_executor(
       concurrent.futures.ThreadPoolExecutor(max_workers=10)
   )
   ```

2. **Use asyncio.Semaphore for playlist extraction** (already planned in Phase 2) to bound concurrency.

## Verification Steps

To verify this analysis:

1. Check discord.py source: `discord/voice_client.py` shows the `VoiceClient._play_audio` method runs in a thread via `asyncio.get_running_loop().run_in_executor`.

2. Monitor CPU during playback: FFmpeg process should show CPU usage, not the main Python process.

3. Profile event loop lag: Use `asyncio` debug mode or tools like `aiomonitor` to measure actual event loop blocking during multi-guild playback.

---

**Last updated**: Phase 0 investigation
**discord.py version**: 2.7.1
