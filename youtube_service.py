"""YouTube transcript extraction service."""

import re
from urllib.parse import urlparse, parse_qs


def extract_video_id(url):
    """Extract YouTube video ID from various URL formats."""
    url = url.strip()
    # youtu.be/VIDEO_ID
    if "youtu.be/" in url:
        path = urlparse(url).path
        return path.strip("/").split("/")[0]
    # youtube.com/watch?v=VIDEO_ID
    parsed = urlparse(url)
    if "youtube.com" in (parsed.hostname or "") or "youtube.co" in (parsed.hostname or ""):
        qs = parse_qs(parsed.query)
        if "v" in qs:
            return qs["v"][0]
        # youtube.com/embed/VIDEO_ID
        if "/embed/" in parsed.path:
            return parsed.path.split("/embed/")[1].split("/")[0]
    # Might be just the ID
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url):
        return url
    return None


def get_transcript(url):
    """
    Get transcript from a YouTube video.
    Returns: (video_id, title, list_of_entries)
    Each entry: {"text": str, "start": float, "duration": float}
    """
    from youtube_transcript_api import YouTubeTranscriptApi

    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError(f"Invalid YouTube URL: {url}")

    title = f"YouTube - {video_id}"

    try:
        ytt = YouTubeTranscriptApi()
        # Try English transcript first
        try:
            transcript = ytt.fetch(video_id, languages=['en', 'en-US', 'en-GB'])
        except Exception:
            # Fallback: fetch any available transcript
            transcript = ytt.fetch(video_id)

        # Convert to list of dicts
        normalized = []
        for snippet in transcript:
            normalized.append({
                "text": snippet.text if hasattr(snippet, 'text') else str(snippet.get('text', '')),
                "start": snippet.start if hasattr(snippet, 'start') else float(snippet.get('start', 0)),
                "duration": snippet.duration if hasattr(snippet, 'duration') else float(snippet.get('duration', 0)),
            })

        return video_id, title, normalized
    except Exception as e:
        raise RuntimeError(f"Could not get transcript for {video_id}: {e}")


def _clean_text(text):
    """Remove annotations like [Music], [Applause] etc."""
    text = re.sub(r'\[.*?\]', '', text).strip()
    return text


def _build_sentences(entries, max_words=15, gap_threshold=1.2):
    """
    Build sentence-level units from transcript entries.

    Each transcript entry is typically a short phrase (2-5 seconds).
    We group consecutive entries into sentences based on:
    1. Punctuation endings (.!?)
    2. Time gaps between entries (> gap_threshold seconds)
    3. Maximum word count per sentence

    Returns list of (text, start_time, end_time).
    """
    if not entries:
        return []

    sentences = []
    current_words = []
    current_start = 0.0
    current_end = 0.0

    for i, entry in enumerate(entries):
        text = _clean_text(entry.get("text", ""))
        if not text:
            continue

        entry_start = entry.get("start", 0)
        entry_end = entry_start + entry.get("duration", 0)

        # Check if we should start a new sentence
        should_break = False

        if current_words:
            # Check time gap from previous entry end to this entry start
            gap = entry_start - current_end
            if gap > gap_threshold:
                should_break = True

            # Check if current sentence already has enough words
            word_count = len(" ".join(current_words).split())
            if word_count >= max_words:
                should_break = True

            # Check if previous text ended with sentence-ending punctuation
            prev_text = " ".join(current_words)
            if prev_text and prev_text[-1] in '.!?':
                should_break = True

        if should_break and current_words:
            sent_text = " ".join(current_words).strip()
            if sent_text:
                sentences.append((sent_text, current_start, current_end))
            current_words = []
            current_start = entry_start

        if not current_words:
            current_start = entry_start

        current_end = entry_end
        current_words.append(text)

    # Flush remaining
    if current_words:
        sent_text = " ".join(current_words).strip()
        if sent_text:
            sentences.append((sent_text, current_start, current_end))

    return sentences


def _group_into_paragraphs(sentences, para_gap=4.0):
    """
    Group sentences into paragraphs based on larger time gaps.
    Returns list of (paragraph_idx, [(text, start, end), ...]).
    """
    if not sentences:
        return []

    paragraphs = []
    current_para = [sentences[0]]
    para_idx = 0

    for i in range(1, len(sentences)):
        prev_end = sentences[i - 1][2]
        curr_start = sentences[i][1]
        gap = curr_start - prev_end

        if gap > para_gap:
            paragraphs.append((para_idx, current_para))
            current_para = []
            para_idx += 1

        current_para.append(sentences[i])

    if current_para:
        paragraphs.append((para_idx, current_para))

    return paragraphs


def _capitalize_first(text):
    """Capitalize first letter of text."""
    if text:
        return text[0].upper() + text[1:]
    return text


def process_video(url):
    """
    Full pipeline: URL -> structured sentences with timing.
    Returns: (video_id, title, [(paragraph_idx, sentence_idx, text, start_time, end_time), ...])
    """
    video_id, title, entries = get_transcript(url)

    # Build individual sentences from transcript entries
    sentences = _build_sentences(entries, max_words=15, gap_threshold=1.2)

    # Group sentences into paragraphs
    paragraphs = _group_into_paragraphs(sentences, para_gap=4.0)

    # Build final output
    all_sentences = []
    for para_idx, para_sentences in paragraphs:
        for s_idx, (text, start, end) in enumerate(para_sentences):
            clean = _capitalize_first(text.strip())
            all_sentences.append((para_idx, s_idx, clean, round(start, 2), round(end, 2)))

    # Auto-generate title from first sentence of transcript
    if all_sentences and title.startswith("YouTube - "):
        from text_utils import generate_title
        title = generate_title(all_sentences[0][2])

    return video_id, title, all_sentences
