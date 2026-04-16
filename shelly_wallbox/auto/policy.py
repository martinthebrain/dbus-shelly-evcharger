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
class AutoLearnChargePowerPolicy:
    """Learning policy for adapting thresholds to the measured charging power."""

    enabled: bool = True
    reference_power_watts: float = 1900.0
    min_watts: float = 500.0
    alpha: float = 0.2
    start_delay_seconds: float = 30.0
    window_seconds: float = 180.0
    max_age_seconds: float = 21600.0

    @staticmethod
    def _clamp_non_negative(value: float, label: str) -> float:
        """Clamp one learning setting to a non-negative value."""
        if value >= 0:
            return float(value)
        logging.warning("%s %s invalid, clamping to 0", label, value)
        return 0.0

    @staticmethod
    def _clamp_fraction(value: float, label: str, default: float) -> float:
        """Clamp one fractional learning setting to the safe (0, 1] range."""
        if 0 < value <= 1:
            return float(value)
        logging.warning("%s %s outside (0,1], clamping to %s", label, value, default)
        return float(default)

    def clamp(self) -> None:
        """Clamp invalid learning settings to safe, conservative defaults."""
        if self.reference_power_watts <= 0:
            logging.warning(
                "AutoReferenceChargePowerWatts %s invalid, clamping to 1900.0",
                self.reference_power_watts,
            )
            self.reference_power_watts = 1900.0
        self.min_watts = self._clamp_non_negative(self.min_watts, "AutoLearnChargePowerMinWatts")
        self.alpha = self._clamp_fraction(self.alpha, "AutoLearnChargePowerAlpha", 0.2)
        self.start_delay_seconds = self._clamp_non_negative(
            self.start_delay_seconds,
            "AutoLearnChargePowerStartDelaySeconds",
        )
        self.window_seconds = self._clamp_non_negative(
            self.window_seconds,
            "AutoLearnChargePowerWindowSeconds",
        )
        self.max_age_seconds = self._clamp_non_negative(
            self.max_age_seconds,
            "AutoLearnChargePowerMaxAgeSeconds",
        )
        self._warn_semantic_relationships()

    def _warn_semantic_relationships(self) -> None:
        """Warn about odd-but-valid learning combinations without changing them."""
        if not self.enabled:
            return
        if 0 < self.window_seconds < self.start_delay_seconds:
            logging.warning(
                "AutoLearnChargePowerWindowSeconds %s below AutoLearnChargePowerStartDelaySeconds %s, learning may never leave the initial delay window",
                self.window_seconds,
                self.start_delay_seconds,
            )
        if 0 < self.max_age_seconds < self.window_seconds:
            logging.warning(
                "AutoLearnChargePowerMaxAgeSeconds %s below AutoLearnChargePowerWindowSeconds %s, learned power may expire before a full learning window completes",
                self.max_age_seconds,
                self.window_seconds,
            )
        if self.min_watts >= self.reference_power_watts:
            logging.warning(
                "AutoLearnChargePowerMinWatts %s at or above AutoReferenceChargePowerWatts %s, learning may reject normal charging sessions",
                self.min_watts,
                self.reference_power_watts,
            )


@dataclass
class AutoPhasePolicy:
    """Policy for conservative automatic multi-phase selection in Auto mode."""

    enabled: bool = True
    upshift_delay_seconds: float = 120.0
    downshift_delay_seconds: float = 30.0
    upshift_headroom_watts: float = 250.0
    downshift_margin_watts: float = 150.0
    prefer_lowest_phase_when_idle: bool = True

    @staticmethod
    def _clamp_non_negative(value: float, label: str) -> float:
        if value >= 0:
            return float(value)
        logging.warning("%s %s invalid, clamping to 0", label, value)
        return 0.0

    def clamp(self) -> None:
        """Clamp invalid phase-switch settings to safe conservative defaults."""
        self.upshift_delay_seconds = self._clamp_non_negative(
            self.upshift_delay_seconds,
            "AutoPhaseUpshiftDelaySeconds",
        )
        self.downshift_delay_seconds = self._clamp_non_negative(
            self.downshift_delay_seconds,
            "AutoPhaseDownshiftDelaySeconds",
        )
        self.upshift_headroom_watts = self._clamp_non_negative(
            self.upshift_headroom_watts,
            "AutoPhaseUpshiftHeadroomWatts",
        )
        self.downshift_margin_watts = self._clamp_non_negative(
            self.downshift_margin_watts,
            "AutoPhaseDownshiftMarginWatts",
        )


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
    learn_charge_power: AutoLearnChargePowerPolicy = field(default_factory=AutoLearnChargePowerPolicy)
    phase: AutoPhasePolicy = field(default_factory=AutoPhasePolicy)

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
            learn_charge_power=AutoLearnChargePowerPolicy(
                enabled=defaults.get("AutoLearnChargePower", "1").strip().lower() in ("1", "true", "yes", "on"),
                reference_power_watts=float(_config_value(defaults, "AutoReferenceChargePowerWatts", 1900)),
                min_watts=float(_config_value(defaults, "AutoLearnChargePowerMinWatts", 500)),
                alpha=float(_config_value(defaults, "AutoLearnChargePowerAlpha", 0.2)),
                start_delay_seconds=float(_config_value(defaults, "AutoLearnChargePowerStartDelaySeconds", 30)),
                window_seconds=float(_config_value(defaults, "AutoLearnChargePowerWindowSeconds", 180)),
                max_age_seconds=float(_config_value(defaults, "AutoLearnChargePowerMaxAgeSeconds", 21600)),
            ),
            phase=AutoPhasePolicy(
                enabled=defaults.get("AutoPhaseSwitching", "1").strip().lower() in ("1", "true", "yes", "on"),
                upshift_delay_seconds=float(_config_value(defaults, "AutoPhaseUpshiftDelaySeconds", 120)),
                downshift_delay_seconds=float(_config_value(defaults, "AutoPhaseDownshiftDelaySeconds", 30)),
                upshift_headroom_watts=float(_config_value(defaults, "AutoPhaseUpshiftHeadroomWatts", 250)),
                downshift_margin_watts=float(_config_value(defaults, "AutoPhaseDownshiftMarginWatts", 150)),
                prefer_lowest_phase_when_idle=defaults.get(
                    "AutoPhasePreferLowestWhenIdle",
                    "1",
                ).strip().lower() in ("1", "true", "yes", "on"),
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
            learn_charge_power=AutoLearnChargePowerPolicy(
                enabled=bool(getattr(svc, "auto_learn_charge_power_enabled", True)),
                reference_power_watts=float(getattr(svc, "auto_reference_charge_power_watts", 1900.0)),
                min_watts=float(getattr(svc, "auto_learn_charge_power_min_watts", 500.0)),
                alpha=float(getattr(svc, "auto_learn_charge_power_alpha", 0.2)),
                start_delay_seconds=float(getattr(svc, "auto_learn_charge_power_start_delay_seconds", 30.0)),
                window_seconds=float(getattr(svc, "auto_learn_charge_power_window_seconds", 180.0)),
                max_age_seconds=float(getattr(svc, "auto_learn_charge_power_max_age_seconds", 21600.0)),
            ),
            phase=AutoPhasePolicy(
                enabled=bool(getattr(svc, "auto_phase_switching_enabled", True)),
                upshift_delay_seconds=float(getattr(svc, "auto_phase_upshift_delay_seconds", 120.0)),
                downshift_delay_seconds=float(getattr(svc, "auto_phase_downshift_delay_seconds", 30.0)),
                upshift_headroom_watts=float(getattr(svc, "auto_phase_upshift_headroom_watts", 250.0)),
                downshift_margin_watts=float(getattr(svc, "auto_phase_downshift_margin_watts", 150.0)),
                prefer_lowest_phase_when_idle=bool(getattr(svc, "auto_phase_prefer_lowest_when_idle", True)),
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

    @staticmethod
    def _warn_small_threshold_gap(
        profile: AutoThresholdProfile,
        start_label: str,
        stop_label: str,
        profile_label: str,
    ) -> None:
        gap_watts = float(profile.start_surplus_watts - profile.stop_surplus_watts)
        recommended_gap_watts = max(100.0, float(profile.start_surplus_watts) * 0.08)
        if gap_watts >= recommended_gap_watts:
            return
        logging.warning(
            "%s surplus gap %.1f W between %s %.1f and %s %.1f is very small, relay chatter risk may increase",
            profile_label,
            gap_watts,
            start_label,
            profile.start_surplus_watts,
            stop_label,
            profile.stop_surplus_watts,
        )

    def _warn_semantic_relationships(self) -> None:
        """Warn about unusual-but-valid policy combinations without clamping them."""
        self._warn_small_threshold_gap(
            self.normal_profile,
            "AutoStartSurplusWatts",
            "AutoStopSurplusWatts",
            "Normal Auto profile",
        )
        self._warn_small_threshold_gap(
            self.high_soc_profile,
            "AutoHighSocStartSurplusWatts",
            "AutoHighSocStopSurplusWatts",
            "High-SOC Auto profile",
        )
        if (self.high_soc_threshold - self.high_soc_release_threshold) < 2.0:
            logging.warning(
                "AutoHighSocThreshold %s and AutoHighSocReleaseThreshold %s leave very little SOC hysteresis, profile switching may flap",
                self.high_soc_threshold,
                self.high_soc_release_threshold,
            )
        if (self.resume_soc - self.min_soc) < 1.0:
            logging.warning(
                "AutoResumeSoc %s is very close to AutoMinSoc %s, SOC-based start/stop hysteresis is very small",
                self.resume_soc,
                self.min_soc,
            )
        if self.stop_grid_import_watts <= self.start_max_grid_import_watts:
            logging.warning(
                "AutoStopGridImportWatts %s at or below AutoStartMaxGridImportWatts %s, grid-import hysteresis is zero or negative",
                self.stop_grid_import_watts,
                self.start_max_grid_import_watts,
            )

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
        self.learn_charge_power.clamp()
        self.phase.clamp()
        self._warn_semantic_relationships()

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
        svc.auto_learn_charge_power_enabled = bool(self.learn_charge_power.enabled)
        svc.auto_reference_charge_power_watts = float(self.learn_charge_power.reference_power_watts)
        svc.auto_learn_charge_power_min_watts = float(self.learn_charge_power.min_watts)
        svc.auto_learn_charge_power_alpha = float(self.learn_charge_power.alpha)
        svc.auto_learn_charge_power_start_delay_seconds = float(self.learn_charge_power.start_delay_seconds)
        svc.auto_learn_charge_power_window_seconds = float(self.learn_charge_power.window_seconds)
        svc.auto_learn_charge_power_max_age_seconds = float(self.learn_charge_power.max_age_seconds)
        svc.auto_phase_switching_enabled = bool(self.phase.enabled)
        svc.auto_phase_upshift_delay_seconds = float(self.phase.upshift_delay_seconds)
        svc.auto_phase_downshift_delay_seconds = float(self.phase.downshift_delay_seconds)
        svc.auto_phase_upshift_headroom_watts = float(self.phase.upshift_headroom_watts)
        svc.auto_phase_downshift_margin_watts = float(self.phase.downshift_margin_watts)
        svc.auto_phase_prefer_lowest_when_idle = bool(self.phase.prefer_lowest_phase_when_idle)

    def resolve_threshold_profile(
        self,
        battery_soc: float,
        was_high_soc_active: bool | None,
    ) -> tuple[AutoThresholdProfile, bool, str]:
        """Return the active threshold profile with SOC hysteresis."""
        soc_value = float(battery_soc)
        active = self._resolved_high_soc_active(soc_value, was_high_soc_active)
        if active:
            return self.high_soc_profile, True, "high-soc"
        return self.normal_profile, False, "normal"

    def _resolved_high_soc_active(self, soc_value: float, was_high_soc_active: bool | None) -> bool:
        """Return whether the high-SOC profile should be active after hysteresis."""
        if was_high_soc_active is None:
            return soc_value > self.high_soc_threshold
        if was_high_soc_active:
            return soc_value >= self.high_soc_release_threshold
        return soc_value > self.high_soc_threshold


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
