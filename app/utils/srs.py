"""Ebbinghaus forgetting curve spaced repetition algorithm.

Wraps legacy srs.py for use from the new app package.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from srs import (  # noqa: E402
    get_next_review_time,
    get_level_name,
    format_next_review,
)

__all__ = ["get_next_review_time", "get_level_name", "format_next_review"]
