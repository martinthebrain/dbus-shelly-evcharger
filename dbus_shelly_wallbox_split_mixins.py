# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared typing contracts for split controller mixins.

The wallbox controllers are intentionally composed from several small mixins so
that each production file stays comfortably below the size budget. Mypy cannot
infer those cross-file compositions on its own, so these lightweight base
classes describe the "assembled elsewhere" contract without affecting runtime
behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


class _ComposableControllerMixin:
    """Type-checking contract for mixins completed by a concrete controller."""

    if TYPE_CHECKING:  # pragma: no cover
        service: Any

        def __getattr__(self, name: str) -> Any: ...
