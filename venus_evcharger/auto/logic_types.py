# SPDX-License-Identifier: GPL-3.0-or-later
"""Small explicit types shared across Auto-mode workflow modules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RelayDecisionState:
    """Represent one intermediate Auto decision without relying on raw sentinels."""

    relay_on: bool | None

    @classmethod
    def pending(cls) -> RelayDecisionState:
        """Return the explicit "continue evaluating" state."""
        return cls(None)

    @classmethod
    def resolved(cls, relay_on: bool) -> RelayDecisionState:
        """Return one settled relay decision."""
        return cls(bool(relay_on))

    def resolved_value(self) -> bool:
        """Return the settled relay decision value."""
        assert self.relay_on is not None
        return bool(self.relay_on)

    @property
    def is_pending(self) -> bool:
        """Return True when later workflow stages must keep evaluating."""
        return self.relay_on is None

    def __bool__(self) -> bool:
        """Preserve legacy truthiness for tests and gradual call-site migration."""
        return bool(self.relay_on)


NO_RELAY_DECISION = RelayDecisionState.pending()
