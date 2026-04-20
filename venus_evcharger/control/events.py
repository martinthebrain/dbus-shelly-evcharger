# SPDX-License-Identifier: GPL-3.0-or-later
"""Small in-memory event bus for the local HTTP API."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from venus_evcharger.core.contracts import normalized_control_api_event_fields


class ControlApiEventBus:
    """Store recent local API events and allow waiting for newer ones."""

    def __init__(self, *, history_limit: int = 50) -> None:
        self._history_limit = max(1, int(history_limit))
        self._events: deque[dict[str, Any]] = deque(maxlen=self._history_limit)
        self._next_seq = 1
        self._condition = threading.Condition()

    def publish(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Store and return one normalized event."""
        with self._condition:
            event = normalized_control_api_event_fields(
                {
                    "seq": self._next_seq,
                    "api_version": "v1",
                    "kind": kind,
                    "timestamp": time.time(),
                    "payload": payload,
                }
            )
            self._next_seq += 1
            self._events.append(event)
            self._condition.notify_all()
            return dict(event)

    def recent(self, *, limit: int = 20, after_seq: int = 0) -> list[dict[str, Any]]:
        """Return recent events after one sequence number."""
        capped_limit = max(0, int(limit))
        with self._condition:
            events = [dict(event) for event in self._events if int(event["seq"]) > int(after_seq)]
        return events[-capped_limit:] if capped_limit else []

    @staticmethod
    def _first_after_seq(events: deque[dict[str, Any]], after_seq: int) -> dict[str, Any] | None:
        for event in events:
            if int(event["seq"]) > int(after_seq):
                return dict(event)
        return None

    def wait_for_next(self, *, after_seq: int = 0, timeout: float = 30.0) -> dict[str, Any] | None:
        """Block until one newer event arrives or the timeout elapses."""
        with self._condition:
            ready = self._first_after_seq(self._events, after_seq)
            if ready is not None:
                return ready
            self._condition.wait(timeout=max(0.0, float(timeout)))
            ready = self._first_after_seq(self._events, after_seq)
            if ready is not None:
                return ready
        return None
