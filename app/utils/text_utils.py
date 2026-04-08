"""Wrapper around the legacy text_utils.py at the project root."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from text_utils import (  # noqa: E402
    extract_text_from_html,
    extract_text_from_file,
    extract_title_from_html,
    fetch_url_content,
    clean_pasted_text,
    filter_junk_sentences,
    split_into_sentences,
    group_into_paragraphs,
    generate_title,
)

__all__ = [
    "extract_text_from_html",
    "extract_text_from_file",
    "extract_title_from_html",
    "fetch_url_content",
    "clean_pasted_text",
    "filter_junk_sentences",
    "split_into_sentences",
    "group_into_paragraphs",
    "generate_title",
]
