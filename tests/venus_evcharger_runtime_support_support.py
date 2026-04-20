# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from types import SimpleNamespace


class RuntimeSupportTestCaseBase(unittest.TestCase):
    @staticmethod
    def _age_zero(_captured_at: float | int | None, _now: float | int | None) -> int:
        return 0

    @staticmethod
    def _age_five(_captured_at: float | int | None, _now: float | int | None) -> int:
        return 5

    @staticmethod
    def _health_zero(_reason: str) -> int:
        return 0

    @staticmethod
    def _health_nine(_reason: str) -> int:
        return 9

    @staticmethod
    def _health_ten(_reason: str) -> int:
        return 10

    @staticmethod
    def _never_stale(_now: float) -> bool:
        return False

    @staticmethod
    def _always_stale(_now: float) -> bool:
        return True


__all__ = ["RuntimeSupportTestCaseBase", "SimpleNamespace"]
