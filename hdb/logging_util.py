"""Structured JSON logging plus human-readable text logging.

Every significant collection action is logged as one JSON object per line
(JSONL) *and* mirrored to a readable text log.  Screenshots are recorded through
the same interface so failures always leave a visual trail (when a screenshot
provider is supplied by the automation backend).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StructuredLogger:
    """Append-only JSONL + text logger, safe for concurrent use."""

    def __init__(
        self,
        logs_root: str,
        run_id: str | None = None,
        screenshot_provider: Callable[[str], str | None] | None = None,
        echo: bool = False,
    ) -> None:
        self.logs_root = logs_root
        self.run_id = run_id or "app"
        self.screenshot_provider = screenshot_provider
        self.echo = echo
        self._lock = threading.Lock()
        os.makedirs(logs_root, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")
        self.jsonl_path = os.path.join(logs_root, f"hdb_{stamp}.jsonl")
        self.text_path = os.path.join(logs_root, f"hdb_{stamp}.log")

    def log(self, event: str, level: str = "INFO", **fields: Any) -> dict[str, Any]:
        record: dict[str, Any] = {
            "ts": _utc_iso(),
            "level": level,
            "run_id": self.run_id,
            "event": event,
        }
        record.update(fields)
        line = json.dumps(record, default=str, sort_keys=False)
        text = "{ts} [{level}] {event} {extra}".format(
            ts=record["ts"],
            level=level,
            event=event,
            extra=json.dumps(fields, default=str) if fields else "",
        ).rstrip()
        with self._lock:
            with open(self.jsonl_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            with open(self.text_path, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        if self.echo:
            print(text)
        return record

    def info(self, event: str, **fields: Any) -> dict[str, Any]:
        return self.log(event, "INFO", **fields)

    def warn(self, event: str, **fields: Any) -> dict[str, Any]:
        return self.log(event, "WARN", **fields)

    def error(self, event: str, **fields: Any) -> dict[str, Any]:
        return self.log(event, "ERROR", **fields)

    def exception(self, event: str, exc: BaseException, **fields: Any) -> dict[str, Any]:
        return self.log(
            event,
            "ERROR",
            error_type=type(exc).__name__,
            error_message=str(exc),
            **fields,
        )

    def capture_screenshot(self, reason: str, **fields: Any) -> str | None:
        """Ask the automation backend for a screenshot and log the reference.

        Returns the screenshot path, or ``None`` if no provider is configured
        (e.g. on a headless/Linux dev machine).
        """
        path: str | None = None
        if self.screenshot_provider is not None:
            try:
                name = f"{reason}_{int(time.time() * 1000)}"
                path = self.screenshot_provider(name)
            except Exception as exc:  # pragma: no cover - defensive
                self.exception("screenshot_failed", exc, reason=reason)
                path = None
        # Use a distinct key so caller-supplied ``path`` fields never collide.
        fields.pop("screenshot_path", None)
        self.log("screenshot", reason=reason, screenshot_path=path, **fields)
        return path
