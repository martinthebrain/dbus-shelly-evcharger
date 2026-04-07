# SPDX-License-Identifier: GPL-3.0-or-later
"""Structured Auto-mode policy objects for thresholds, SOC, and smoothing.

The wallbox used to carry many independent Auto-mode attributes directly on the
service object. Grouping them into dataclasses keeps config loading, runtime
validation, and Auto-mode execution in sync and avoids duplicated policy logic.
"""

from __future__ import annotations

from configparser import SectionProxy
from dataclasses import dataclass, field
import logging
from typing import Any


def _config_value(defaults: SectionProxy, key: str, fallback: Any) -> str:
    """Return a config value while keeping SectionProxy.get() mypy-friendly."""
    return defaults.get(key, str(fallback))


@dataclass
class AutoThresholdProfile:
    """Start/stop surplus thresholds for one Auto charging profile."""

    start_surplus_watts: float
    stop_surplus_watts: float

    def clamp(self, start_label: str, stop_label: str) -> None:
        """Keep the stop threshold at or below the corresponding start threshold."""
        if self.stop_surplus_watts <= self.start_surplus_watts:
            return
        logging.warning(
            "%s %s above %s %s, clamping",
            stop_label,
            self.stop_surplus_watts,
            start_label,
            self.start_surplus_watts,
        )
        self.stop_surplus_watts = float(self.start_surplus_watts)


@dataclass
class AutoStopEwmaPolicy:
    """EWMA smoothing policy for Auto stop behavior."""

    base_alpha: float = 0.35
    stable_alpha: float = 0.55
    volatile_alpha: float = 0.15
    volatility_low_watts: float = 150.0
    volatility_high_watts: float = 400.0

    @staticmethod
    def _clamp_fraction(value: float, label: str, default: float) -> float:
        if 0 < value <= 1:
            return float(value)
        logging.warning("%s %s outside (0,1], clamping to %s", label, value, default)
        return float(default)

    def clamp(self) -> None:
        """Clamp invalid EWMA and volatility settings to safe ranges."""
        self.base_alpha = self._clamp_fraction(self.base_alpha, "AutoStopEwmaAlpha", 0.35)
        self.stable_alpha = self._clamp_fraction(self.stable_alpha, "AutoStopEwmaAlphaStable", 0.55)
        self.volatile_alpha = self._clamp_fraction(self.volatile_alpha, "AutoStopEwmaAlphaVolatile", 0.15)
        if self.volatility_low_watts < 0:
            logging.warning(
                "AutoStopSurplusVolatilityLowWatts %s invalid, clamping to 0",
                self.volatility_low_watts,
            )
            self.volatility_low_watts = 0.0
        if self.volatility_high_watts < 0:
            logging.warning(
                "AutoStopSurplusVolatilityHighWatts %s invalid, clamping to 0",
                self.volatility_high_watts,
            )
            self.volatility_high_watts = 0.0
        if self.volatility_high_watts < self.volatility_low_watts:
            logging.warning(
                "AutoStopSurplusVolatilityHighWatts %s below AutoStopSurplusVolatilityLowWatts %s, clamping",
                self.volatility_high_watts,
                self.volatility_low_watts,
            )
            self.volatility_high_watts = float(self.volatility_low_watts)

    def adaptive_alpha(self, volatility: float | None) -> tuple[float, str, float | None]:
        """Return a transparent alpha stage based on recent surplus volatility."""
        if volatility is None:
            return float(self.base_alpha), "base", None
        if volatility <= self.volatility_low_watts:
            return float(self.stable_alpha), "stable", float(volatility)
        if volatility >= self.volatility_high_watts:
            return float(self.volatile_alpha), "volatile", float(volatility)
        return float(self.base_alpha), "medium", float(volatility)


@dataclass
class AutoPolicy:
    """Centralized Auto-mode policy used by bootstrap, validation, and logic."""

    normal_profile: AutoThresholdProfile = field(
        default_factory=lambda: AutoThresholdProfile(1500.0, 1100.0)
    )
    high_soc_profile: AutoThresholdProfile = field(
        default_factory=lambda: AutoThresholdProfile(1500.0, 1100.0)
    )
    high_soc_threshold: float = 50.0
    high_soc_release_threshold: float = 50.0
    min_soc: float = 30.0
    resume_soc: float = 33.0
    start_max_grid_import_watts: float = 50.0
    stop_grid_import_watts: float = 300.0
    grid_recovery_start_seconds: float = 10.0
    stop_surplus_delay_seconds: float = 10.0
    ewma: AutoStopEwmaPolicy = field(default_factory=AutoStopEwmaPolicy)

    @classmethod
    def from_config(cls, defaults: SectionProxy) -> AutoPolicy:
        """Build an AutoPolicy from config defaults."""
        normal_start = float(_config_value(defaults, "AutoStartSurplusWatts", 1500))
        normal_stop = float(_config_value(defaults, "AutoStopSurplusWatts", normal_start - 400))
        min_soc = float(_config_value(defaults, "AutoMinSoc", 30))
        return cls(
            normal_profile=AutoThresholdProfile(normal_start, normal_stop),
            high_soc_profile=AutoThresholdProfile(
                float(_config_value(defaults, "AutoHighSocStartSurplusWatts", normal_start)),
                float(_config_value(defaults, "AutoHighSocStopSurplusWatts", normal_stop)),
            ),
            high_soc_threshold=float(_config_value(defaults, "AutoHighSocThreshold", 50)),
            high_soc_release_threshold=float(
                _config_value(defaults, "AutoHighSocReleaseThreshold", _config_value(defaults, "AutoHighSocThreshold", 50))
            ),
            min_soc=min_soc,
            resume_soc=float(_config_value(defaults, "AutoResumeSoc", min_soc + 3)),
            start_max_grid_import_watts=float(_config_value(defaults, "AutoStartMaxGridImportWatts", 50)),
            stop_grid_import_watts=float(_config_value(defaults, "AutoStopGridImportWatts", 300)),
            grid_recovery_start_seconds=float(
                _config_value(defaults, "AutoGridRecoveryStartSeconds", _config_value(defaults, "AutoStartDelaySeconds", 10))
            ),
            stop_surplus_delay_seconds=float(
                _config_value(defaults, "AutoStopSurplusDelaySeconds", _config_value(defaults, "AutoStopDelaySeconds", 10))
            ),
            ewma=AutoStopEwmaPolicy(
                base_alpha=float(_config_value(defaults, "AutoStopEwmaAlpha", 0.35)),
                stable_alpha=float(_config_value(defaults, "AutoStopEwmaAlphaStable", 0.55)),
                volatile_alpha=float(_config_value(defaults, "AutoStopEwmaAlphaVolatile", 0.15)),
                volatility_low_watts=float(_config_value(defaults, "AutoStopSurplusVolatilityLowWatts", 150)),
                volatility_high_watts=float(_config_value(defaults, "AutoStopSurplusVolatilityHighWatts", 400)),
            ),
        )

    @classmethod
    def from_service(cls, svc: Any) -> AutoPolicy:
        """Build an AutoPolicy from legacy flat service attributes."""
        return cls(
            normal_profile=AutoThresholdProfile(
                float(getattr(svc, "auto_start_surplus_watts", 1500.0)),
                float(getattr(svc, "auto_stop_surplus_watts", 1100.0)),
            ),
            high_soc_profile=AutoThresholdProfile(
                float(getattr(svc, "auto_high_soc_start_surplus_watts", getattr(svc, "auto_start_surplus_watts", 1500.0))),
                float(getattr(svc, "auto_high_soc_stop_surplus_watts", getattr(svc, "auto_stop_surplus_watts", 1100.0))),
            ),
            high_soc_threshold=float(getattr(svc, "auto_high_soc_threshold", 50.0)),
            high_soc_release_threshold=float(
                getattr(svc, "auto_high_soc_release_threshold", getattr(svc, "auto_high_soc_threshold", 50.0))
            ),
            min_soc=float(getattr(svc, "auto_min_soc", 30.0)),
            resume_soc=float(getattr(svc, "auto_resume_soc", 33.0)),
            start_max_grid_import_watts=float(getattr(svc, "auto_start_max_grid_import_watts", 50.0)),
            stop_grid_import_watts=float(getattr(svc, "auto_stop_grid_import_watts", 300.0)),
            grid_recovery_start_seconds=float(getattr(svc, "auto_grid_recovery_start_seconds", 10.0)),
            stop_surplus_delay_seconds=float(getattr(svc, "auto_stop_surplus_delay_seconds", 10.0)),
            ewma=AutoStopEwmaPolicy(
                base_alpha=float(getattr(svc, "auto_stop_ewma_alpha", 0.35)),
                stable_alpha=float(getattr(svc, "auto_stop_ewma_alpha_stable", 0.55)),
                volatile_alpha=float(getattr(svc, "auto_stop_ewma_alpha_volatile", 0.15)),
                volatility_low_watts=float(getattr(svc, "auto_stop_surplus_volatility_low_watts", 150.0)),
                volatility_high_watts=float(getattr(svc, "auto_stop_surplus_volatility_high_watts", 400.0)),
            ),
        )

    @staticmethod
    def _clamp_percentage(value: float, label: str) -> float:
        if 0 <= value <= 100:
            return float(value)
        logging.warning("%s %s outside 0..100, clamping", label, value)
        return min(100.0, max(0.0, float(value)))

    @staticmethod
    def _clamp_non_negative(value: float, label: str) -> float:
        if value >= 0:
            return float(value)
        logging.warning("%s %s invalid, clamping to 0", label, value)
        return 0.0

    def clamp(self) -> None:
        """Clamp invalid policy values while keeping semantic relationships intact."""
        self.min_soc = self._clamp_percentage(self.min_soc, "AutoMinSoc")
        self.resume_soc = self._clamp_percentage(self.resume_soc, "AutoResumeSoc")
        self.high_soc_threshold = self._clamp_percentage(self.high_soc_threshold, "AutoHighSocThreshold")
        self.high_soc_release_threshold = self._clamp_percentage(
            self.high_soc_release_threshold,
            "AutoHighSocReleaseThreshold",
        )
        if self.high_soc_release_threshold > self.high_soc_threshold:
            logging.warning(
                "AutoHighSocReleaseThreshold %s above AutoHighSocThreshold %s, clamping",
                self.high_soc_release_threshold,
                self.high_soc_threshold,
            )
            self.high_soc_release_threshold = float(self.high_soc_threshold)
        if self.resume_soc < self.min_soc:
            logging.warning(
                "AutoResumeSoc %s below AutoMinSoc %s, clamping to AutoMinSoc",
                self.resume_soc,
                self.min_soc,
            )
            self.resume_soc = float(self.min_soc)

        self.grid_recovery_start_seconds = self._clamp_non_negative(
            self.grid_recovery_start_seconds,
            "auto_grid_recovery_start_seconds",
        )
        self.stop_surplus_delay_seconds = self._clamp_non_negative(
            self.stop_surplus_delay_seconds,
            "auto_stop_surplus_delay_seconds",
        )
        self.normal_profile.clamp("AutoStartSurplusWatts", "AutoStopSurplusWatts")
        self.high_soc_profile.clamp("AutoHighSocStartSurplusWatts", "AutoHighSocStopSurplusWatts")
        self.ewma.clamp()

    def apply_to_service(self, svc: Any) -> None:
        """Mirror the policy onto legacy flat service attributes for compatibility."""
        svc.auto_policy = self
        svc.auto_start_surplus_watts = float(self.normal_profile.start_surplus_watts)
        svc.auto_stop_surplus_watts = float(self.normal_profile.stop_surplus_watts)
        svc.auto_high_soc_threshold = float(self.high_soc_threshold)
        svc.auto_high_soc_release_threshold = float(self.high_soc_release_threshold)
        svc.auto_high_soc_start_surplus_watts = float(self.high_soc_profile.start_surplus_watts)
        svc.auto_high_soc_stop_surplus_watts = float(self.high_soc_profile.stop_surplus_watts)
        svc.auto_min_soc = float(self.min_soc)
        svc.auto_resume_soc = float(self.resume_soc)
        svc.auto_start_max_grid_import_watts = float(self.start_max_grid_import_watts)
        svc.auto_stop_grid_import_watts = float(self.stop_grid_import_watts)
        svc.auto_grid_recovery_start_seconds = float(self.grid_recovery_start_seconds)
        svc.auto_stop_surplus_delay_seconds = float(self.stop_surplus_delay_seconds)
        svc.auto_stop_ewma_alpha = float(self.ewma.base_alpha)
        svc.auto_stop_ewma_alpha_stable = float(self.ewma.stable_alpha)
        svc.auto_stop_ewma_alpha_volatile = float(self.ewma.volatile_alpha)
        svc.auto_stop_surplus_volatility_low_watts = float(self.ewma.volatility_low_watts)
        svc.auto_stop_surplus_volatility_high_watts = float(self.ewma.volatility_high_watts)

    def resolve_threshold_profile(
        self,
        battery_soc: float,
        was_high_soc_active: bool | None,
    ) -> tuple[AutoThresholdProfile, bool, str]:
        """Return the active threshold profile with SOC hysteresis."""
        soc_value = float(battery_soc)
        active = was_high_soc_active
        if active is None:
            active = soc_value > self.high_soc_threshold
        elif active and soc_value < self.high_soc_release_threshold:
            active = False
        elif not active and soc_value > self.high_soc_threshold:
            active = True
        if active:
            return self.high_soc_profile, True, "high-soc"
        return self.normal_profile, False, "normal"


def validate_auto_policy(policy: AutoPolicy, svc: Any | None = None) -> AutoPolicy:
    """Clamp one AutoPolicy and optionally sync it back onto a service."""
    policy.clamp()
    if svc is not None:
        policy.apply_to_service(svc)
    return policy


def load_auto_policy_from_config(defaults: SectionProxy, svc: Any | None = None) -> AutoPolicy:
    """Create, validate, and optionally apply an AutoPolicy from config defaults."""
    policy = AutoPolicy.from_config(defaults)
    return validate_auto_policy(policy, svc)
