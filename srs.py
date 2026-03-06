"""
Ebbinghaus Forgetting Curve - Spaced Repetition System

Intervals (in hours):
  Level 0: Immediate (0h) - just learned / forgot
  Level 1: 1 hour
  Level 2: 1 day (24h)
  Level 3: 2 days (48h)
  Level 4: 4 days (96h)
  Level 5: 7 days (168h)
  Level 6: 15 days (360h)
  Level 7: 30 days (720h) -> MASTERED

If incorrect at any level -> back to Level 0
"""

from datetime import datetime, timedelta

INTERVALS_HOURS = [0, 1, 24, 48, 96, 168, 360, 720]

LEVEL_NAMES = {
    0: "방금 학습",      # Just learned
    1: "1시간 후",       # 1 hour
    2: "1일 후",         # 1 day
    3: "2일 후",         # 2 days
    4: "4일 후",         # 4 days
    5: "7일 후",         # 7 days
    6: "15일 후",        # 15 days
    7: "완전 습득!",     # Mastered!
}


def get_next_review_time(level, from_time=None):
    if from_time is None:
        from_time = datetime.now()
    if level >= len(INTERVALS_HOURS):
        level = len(INTERVALS_HOURS) - 1
    hours = INTERVALS_HOURS[level]
    return from_time + timedelta(hours=hours)


def get_level_name(level):
    return LEVEL_NAMES.get(level, f"Level {level}")


def format_next_review(next_review_str):
    """Human-readable time until next review."""
    try:
        next_review = datetime.fromisoformat(next_review_str)
    except (ValueError, TypeError):
        return "지금"

    diff = next_review - datetime.now()
    total_seconds = diff.total_seconds()

    if total_seconds <= 0:
        return "지금 복습!"

    minutes = total_seconds / 60
    hours = minutes / 60
    days = hours / 24

    if minutes < 60:
        return f"{int(minutes)}분 후"
    elif hours < 24:
        return f"{int(hours)}시간 후"
    else:
        return f"{int(days)}일 후"
