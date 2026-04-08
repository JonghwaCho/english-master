"""AI provider abstraction - Gemini, Claude, OpenAI.

Keys are operator-managed (server-side) and loaded from environment.
Per-user quota tracking happens in app.quota; this module is transport only.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

from app.config import get_settings

logger = logging.getLogger(__name__)


class AIError(Exception):
    """Raised when AI call fails after retries."""


def call_gemini(prompt: str, model: str = "gemini-2.5-flash") -> str:
    settings = get_settings()
    api_key = settings.gemini_api_key
    if not api_key:
        raise AIError("GEMINI_API_KEY is not configured")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048},
    }
    r = _post_with_retry(url, body)
    data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise AIError(f"Unexpected Gemini response: {e}")


def call_claude(prompt: str, model: str = "claude-sonnet-4-20250514") -> str:
    settings = get_settings()
    api_key = settings.claude_api_key
    if not api_key:
        raise AIError("CLAUDE_API_KEY is not configured")

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}],
    }
    r = _post_with_retry(url, body, headers=headers)
    data = r.json()
    try:
        return data["content"][0]["text"]
    except (KeyError, IndexError) as e:
        raise AIError(f"Unexpected Claude response: {e}")


def call_openai(prompt: str, model: str = "gpt-4o-mini") -> str:
    settings = get_settings()
    api_key = settings.openai_api_key
    if not api_key:
        raise AIError("OPENAI_API_KEY is not configured")

    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 2048,
    }
    r = _post_with_retry(url, body, headers=headers)
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise AIError(f"Unexpected OpenAI response: {e}")


def call_ai(prompt: str, provider: Optional[str] = None) -> str:
    """Call configured AI provider with retry on rate limits."""
    settings = get_settings()
    provider = provider or settings.ai_provider_default
    if provider == "gemini":
        return call_gemini(prompt)
    if provider == "claude":
        return call_claude(prompt)
    if provider == "openai":
        return call_openai(prompt)
    raise AIError(f"Unknown provider: {provider}")


def _post_with_retry(url: str, json_body: dict, headers: Optional[dict] = None, max_retries: int = 3):
    """POST with exponential backoff on 429/5xx."""
    delays = [5, 10, 20]
    last_error = None
    for attempt in range(max_retries):
        try:
            r = requests.post(url, json=json_body, headers=headers, timeout=60)
            if r.status_code == 429:
                logger.warning("AI rate-limited, retrying in %ss", delays[attempt])
                time.sleep(delays[attempt])
                continue
            if 500 <= r.status_code < 600:
                logger.warning("AI server error %s, retrying", r.status_code)
                time.sleep(delays[attempt])
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(delays[attempt])
                continue
            raise AIError(f"AI request failed: {e}")
    raise AIError(f"AI request exhausted retries: {last_error}")
