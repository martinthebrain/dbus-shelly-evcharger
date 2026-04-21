# SPDX-License-Identifier: GPL-3.0-or-later
"""In-memory rate limiting for the local Control API."""

from __future__ import annotations

import threading
import time
from collections import deque


class ControlApiRateLimiter:
    """Protect local control endpoints against accidental command storms."""

    _CRITICAL_COMMANDS = frozenset(
        {
            "reset_contactor_lockout",
            "reset_phase_lockout",
            "trigger_software_update",
        }
    )

    def __init__(
        self,
        *,
        max_requests: int = 30,
        window_seconds: float = 5.0,
        critical_cooldown_seconds: float = 2.0,
    ) -> None:
        self._max_requests = max(1, int(max_requests))
        self._window_seconds = max(0.1, float(window_seconds))
        self._critical_cooldown_seconds = max(0.0, float(critical_cooldown_seconds))
        self._request_times: dict[str, deque[float]] = {}
        self._critical_deadlines: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def allow_request(self, client_key: str, *, now: float | None = None) -> tuple[bool, float]:
        """Return whether one client may issue another request in the window."""
        current = time.time() if now is None else float(now)
        with self._lock:
            request_times = self._request_times.setdefault(client_key, deque())
            self._trim_request_times(request_times, current)
            if len(request_times) >= self._max_requests:
                retry_after = max(0.0, self._window_seconds - (current - request_times[0]))
                return False, retry_after
            request_times.append(current)
            return True, 0.0

    def allow_command(self, client_key: str, command_name: str, *, now: float | None = None) -> tuple[bool, float]:
        """Return whether one critical command may run now for this client."""
        if command_name not in self._CRITICAL_COMMANDS or self._critical_cooldown_seconds <= 0.0:
            return True, 0.0
        current = time.time() if now is None else float(now)
        lookup_key = (client_key, command_name)
        with self._lock:
            deadline = self._critical_deadlines.get(lookup_key, 0.0)
            if current < deadline:
                return False, max(0.0, deadline - current)
            self._critical_deadlines[lookup_key] = current + self._critical_cooldown_seconds
            return True, 0.0

    def _trim_request_times(self, request_times: deque[float], current: float) -> None:
        while request_times and (current - request_times[0]) >= self._window_seconds:
            request_times.popleft()
