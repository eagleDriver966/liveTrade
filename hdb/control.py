"""Cancellation / pause / progress primitives shared by worker threads.

Worker threads never touch Tkinter widgets.  They push structured progress
events onto a queue and honour cooperative cancel/pause flags.  The GUI drains
the queue via ``root.after`` (see :mod:`Hdb`).
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class CancelledError(Exception):
    """Raised when a cooperative cancel is requested."""


@dataclass
class ProgressEvent:
    kind: str
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": self.message, "data": self.data, "ts": self.ts}


class CollectionControl:
    """Thread-safe cancel/pause switch + progress channel."""

    def __init__(self, progress_queue: "queue.Queue[ProgressEvent] | None" = None) -> None:
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self.progress_queue = progress_queue

    # -- cancellation --------------------------------------------------------
    def request_cancel(self) -> None:
        self._cancel.set()
        # Un-pause so a paused worker can observe the cancel and exit.
        self._pause.clear()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_cancel(self) -> None:
        if self._cancel.is_set():
            raise CancelledError()

    # -- pause / resume ------------------------------------------------------
    def request_pause(self) -> None:
        if not self._cancel.is_set():
            self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def is_paused(self) -> bool:
        return self._pause.is_set()

    def wait_if_paused(self, poll: float = 0.2) -> None:
        while self._pause.is_set() and not self._cancel.is_set():
            time.sleep(poll)
        self.check_cancel()

    # -- progress ------------------------------------------------------------
    def emit(self, kind: str, message: str = "", **data: Any) -> None:
        if self.progress_queue is not None:
            self.progress_queue.put(ProgressEvent(kind=kind, message=message, data=data))
