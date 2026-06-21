"""Event log — append-only JSONL log of all DesignScribe events."""
import json
from datetime import datetime, timezone
from pathlib import Path


EVENT_LOG_FILE = ".designscribe/events.jsonl"


def _ensure_dir(path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def append(event_type: str, data: dict, path: str = EVENT_LOG_FILE):
    """Append an event to the JSONL log.

    Args:
        event_type: One of "change", "narration", "render", "init"
        data: Event payload
        path: Path to the JSONL file
    """
    _ensure_dir(path)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "data": data,
    }
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def read_all(path: str = EVENT_LOG_FILE) -> list[dict]:
    """Read all events from the log."""
    p = Path(path)
    if not p.exists():
        return []
    events = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def read_since(since: str | None = None, path: str = EVENT_LOG_FILE) -> list[dict]:
    """Read events, optionally filtering by timestamp (ISO format)."""
    events = read_all(path)
    if since:
        events = [e for e in events if e["timestamp"] >= since]
    return events


def clear(path: str = EVENT_LOG_FILE):
    """Clear the event log."""
    _ensure_dir(path)
    Path(path).write_text("")
