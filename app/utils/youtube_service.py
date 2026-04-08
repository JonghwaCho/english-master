"""Wrapper around the legacy youtube_service.py at the project root.

Re-exports the same functions so app.content can use a clean namespace.
The legacy file remains untouched to avoid regressions in v1.3.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path so we can import the legacy module
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from youtube_service import (  # noqa: E402
    extract_video_id,
    get_transcript,
    get_video_title,
    process_video,
    extract_playlist_id,
    fetch_playlist_feed,
    get_playlist_title,
)

__all__ = [
    "extract_video_id",
    "get_transcript",
    "get_video_title",
    "process_video",
    "extract_playlist_id",
    "fetch_playlist_feed",
    "get_playlist_title",
]
