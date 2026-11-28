"""Non-blocking media playback check for the temporary game lighting override."""
import asyncio
import threading
import time

_lock = threading.Lock()
_pending = False
_playing = True
_checked_at = float('-inf')


async def _query_playing():
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
    )
    manager = await Manager.request_async()
    for session in manager.get_sessions():
        source = (session.source_app_user_model_id or '').lower()
        if any(name in source for name in ('valorant', 'riot', 'twitch')):
            continue
        playback = session.get_playback_info()
        if playback and playback.playback_status == Status.PLAYING:
            return True
    return False


def _refresh():
    global _playing, _checked_at, _pending
    try:
        playing = asyncio.run(asyncio.wait_for(_query_playing(), timeout=2.0))
    except Exception:
        playing = True  # Preserve music effects when playback state is unknown.
    with _lock:
        _playing = playing
        _checked_at = time.monotonic()
        _pending = False


def is_playing():
    """Refresh at most once per second without blocking the LED animation."""
    global _pending
    with _lock:
        age = time.monotonic() - _checked_at
        if not _pending and age >= 1.0:
            _pending = True
            threading.Thread(target=_refresh, daemon=True).start()
        return _playing if age < 4.0 else True
