"""File watcher — detect code changes in real-time using watchdog.

Monitors filesystem for .py file changes, debounces rapid writes,
and triggers the DesignScribe pipeline.
"""
from __future__ import annotations

import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent


class _DebouncedHandler(FileSystemEventHandler):
    """Collects file changes and fires callback after debounce period."""

    def __init__(self, callback, debounce_ms: int = 2000, exclude: list[str] | None = None):
        super().__init__()
        self.callback = callback
        self.debounce_sec = debounce_ms / 1000.0
        self.exclude = exclude or ["__pycache__", ".git", "node_modules", ".venv", "venv", ".designscribe"]
        self._pending: set[str] = set()
        self._last_event: float = 0

    def _should_skip(self, path: str) -> bool:
        """Check if path should be excluded."""
        parts = Path(path).parts
        return any(ex in parts for ex in self.exclude)

    def on_modified(self, event):
        if event.is_directory:
            return
        if not event.src_path.endswith(".py"):
            return
        if self._should_skip(event.src_path):
            return
        self._pending.add(str(Path(event.src_path).resolve()))
        self._last_event = time.time()

    def on_created(self, event):
        if event.is_directory:
            return
        if not event.src_path.endswith(".py"):
            return
        if self._should_skip(event.src_path):
            return
        self._pending.add(str(Path(event.src_path).resolve()))
        self._last_event = time.time()

    def check_flush(self):
        """If debounce period has passed since last event, flush pending changes."""
        if not self._pending:
            return
        if time.time() - self._last_event < self.debounce_sec:
            return
        files = sorted(self._pending)
        self._pending.clear()
        self.callback(files)


def watch(
    path: str,
    callback,
    debounce_ms: int = 2000,
    exclude: list[str] | None = None,
):
    """Watch for file changes and call callback with list of changed files.

    Args:
        path: Directory to watch
        callback: Function called with list of changed file paths
        debounce_ms: Wait this long after last change before triggering
        exclude: Directory names to exclude
    """
    handler = _DebouncedHandler(callback, debounce_ms=debounce_ms, exclude=exclude)
    observer = Observer()
    observer.schedule(handler, path, recursive=True)
    observer.start()

    try:
        while True:
            handler.check_flush()
            time.sleep(0.5)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
