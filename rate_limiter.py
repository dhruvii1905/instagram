import time
import random
from database import actions_today, log_action

# Safe daily caps per action type
DAILY_LIMITS = {
    "follow":      150,
    "unfollow":    150,
    "like":        300,
    "comment":      50,
    "dm":           25,
    "story_view":   50,
    "scrape":     1000,
}

# Human-like delay ranges (seconds) between consecutive actions
ACTION_DELAYS = {
    "follow":     (25, 65),
    "unfollow":   (25, 65),
    "like":       (8,  25),
    "comment":    (60, 180),
    "dm":         (90, 300),
    "story_view": (3,  12),
    "scrape":     (1,   4),
}


def can_do(action: str) -> bool:
    limit = DAILY_LIMITS.get(action, 0)
    done = actions_today(action)
    return done < limit


def remaining(action: str) -> int:
    return max(0, DAILY_LIMITS.get(action, 0) - actions_today(action))


def human_delay(action: str):
    lo, hi = ACTION_DELAYS.get(action, (5, 15))
    # occasionally add a longer "distraction" pause (10% chance)
    if random.random() < 0.10:
        hi += random.randint(30, 120)
    delay = random.uniform(lo, hi)
    time.sleep(delay)


def check_and_delay(action: str) -> bool:
    """
    Returns True if the action is allowed.
    Sleeps a human-like delay before returning.
    """
    if not can_do(action):
        return False
    human_delay(action)
    return True
