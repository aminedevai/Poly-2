"""utils/time_helpers.py — Time and slug helpers."""
import re, time
from datetime import datetime, timezone
from utils.colors import orange, yel, red, gray

def slug_to_ts(slug: str) -> int:
    """Extract open timestamp from slug. Close time = slug_ts + 300."""
    m = re.search(r'-(\d{9,11})$', slug)
    return int(m.group(1)) if m else 0

def slug_close_ts(slug: str) -> int:
    """Market CLOSES at open_ts + 300."""
    return slug_to_ts(slug) + 300

def time_left_from_ts(end_ts: int):
    """Returns (colored_str, seconds_left)."""
    secs = int(end_ts - time.time())
    if secs <= 0: return red("ENDED"), -1
    h, rem = divmod(secs, 3600)
    m, s   = divmod(rem, 60)
    if h > 0:   return yel(f"{h}h {m:02d}m"), secs
    elif m > 0: return orange(f"{m}m {s:02d}s"), secs
    else:       return red(f"{s}s!!"), secs

def time_left(end_str: str):
    if not end_str: return gray("Unknown"), 0
    try:
        end = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
        return time_left_from_ts(int(end.timestamp()))
    except:
        return gray("Unknown"), 0

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime('%H:%M:%S')

def fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%H:%M:%S')
