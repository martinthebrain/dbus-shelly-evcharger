# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared state-controller specs and parser helpers."""

from __future__ import annotations

import configparser
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeOverrideSpec:
    """One DBus-writable runtime setting that can persist in an override file."""

    dbus_path: str
    config_key: str
    attr_name: str
    value_kind: str


RUNTIME_OVERRIDE_SPECS: tuple[RuntimeOverrideSpec, ...] = (
    RuntimeOverrideSpec("/Mode", "Mode", "virtual_mode", "int"),
    RuntimeOverrideSpec("/AutoStart", "AutoStart", "virtual_autostart", "bool"),
    RuntimeOverrideSpec("/SetCurrent", "SetCurrent", "virtual_set_current", "float"),
    RuntimeOverrideSpec("/MinCurrent", "MinCurrent", "min_current", "float"),
    RuntimeOverrideSpec("/MaxCurrent", "MaxCurrent", "max_current", "float"),
    RuntimeOverrideSpec("/PhaseSelection", "PhaseSelection", "requested_phase_selection", "phase"),
    RuntimeOverrideSpec("/Auto/StartSurplusWatts", "AutoStartSurplusWatts", "auto_start_surplus_watts", "float"),
    RuntimeOverrideSpec("/Auto/StopSurplusWatts", "AutoStopSurplusWatts", "auto_stop_surplus_watts", "float"),
    RuntimeOverrideSpec("/Auto/MinSoc", "AutoMinSoc", "auto_min_soc", "float"),
    RuntimeOverrideSpec("/Auto/ResumeSoc", "AutoResumeSoc", "auto_resume_soc", "float"),
    RuntimeOverrideSpec("/Auto/StartDelaySeconds", "AutoStartDelaySeconds", "auto_start_delay_seconds", "float"),
    RuntimeOverrideSpec("/Auto/StopDelaySeconds", "AutoStopDelaySeconds", "auto_stop_delay_seconds", "float"),
    RuntimeOverrideSpec("/Auto/ScheduledEnabledDays", "AutoScheduledEnabledDays", "auto_scheduled_enabled_days", "weekday_set"),
    RuntimeOverrideSpec(
        "/Auto/ScheduledFallbackDelaySeconds",
        "AutoScheduledNightStartDelaySeconds",
        "auto_scheduled_night_start_delay_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/ScheduledLatestEndTime",
        "AutoScheduledLatestEndTime",
        "auto_scheduled_latest_end_time",
        "hhmm",
    ),
    RuntimeOverrideSpec(
        "/Auto/ScheduledNightCurrent",
        "AutoScheduledNightCurrentAmps",
        "auto_scheduled_night_current_amps",
        "float",
    ),
    RuntimeOverrideSpec("/Auto/DbusBackoffBaseSeconds", "AutoDbusBackoffBaseSeconds", "auto_dbus_backoff_base_seconds", "float"),
    RuntimeOverrideSpec("/Auto/DbusBackoffMaxSeconds", "AutoDbusBackoffMaxSeconds", "auto_dbus_backoff_max_seconds", "float"),
    RuntimeOverrideSpec(
        "/Auto/GridRecoveryStartSeconds",
        "AutoGridRecoveryStartSeconds",
        "auto_grid_recovery_start_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/StopSurplusDelaySeconds",
        "AutoStopSurplusDelaySeconds",
        "auto_stop_surplus_delay_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/StopSurplusVolatilityLowWatts",
        "AutoStopSurplusVolatilityLowWatts",
        "auto_stop_surplus_volatility_low_watts",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/StopSurplusVolatilityHighWatts",
        "AutoStopSurplusVolatilityHighWatts",
        "auto_stop_surplus_volatility_high_watts",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/ReferenceChargePowerWatts",
        "AutoReferenceChargePowerWatts",
        "auto_reference_charge_power_watts",
        "float",
    ),
    RuntimeOverrideSpec("/Auto/LearnChargePowerEnabled", "AutoLearnChargePower", "auto_learn_charge_power_enabled", "bool"),
    RuntimeOverrideSpec(
        "/Auto/LearnChargePowerMinWatts",
        "AutoLearnChargePowerMinWatts",
        "auto_learn_charge_power_min_watts",
        "float",
    ),
    RuntimeOverrideSpec("/Auto/LearnChargePowerAlpha", "AutoLearnChargePowerAlpha", "auto_learn_charge_power_alpha", "float"),
    RuntimeOverrideSpec(
        "/Auto/LearnChargePowerStartDelaySeconds",
        "AutoLearnChargePowerStartDelaySeconds",
        "auto_learn_charge_power_start_delay_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/LearnChargePowerWindowSeconds",
        "AutoLearnChargePowerWindowSeconds",
        "auto_learn_charge_power_window_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/LearnChargePowerMaxAgeSeconds",
        "AutoLearnChargePowerMaxAgeSeconds",
        "auto_learn_charge_power_max_age_seconds",
        "float",
    ),
    RuntimeOverrideSpec("/Auto/PhaseSwitching", "AutoPhaseSwitching", "auto_phase_switching_enabled", "bool"),
    RuntimeOverrideSpec(
        "/Auto/PhasePreferLowestWhenIdle",
        "AutoPhasePreferLowestWhenIdle",
        "auto_phase_prefer_lowest_when_idle",
        "bool",
    ),
    RuntimeOverrideSpec(
        "/Auto/PhaseUpshiftDelaySeconds",
        "AutoPhaseUpshiftDelaySeconds",
        "auto_phase_upshift_delay_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/PhaseDownshiftDelaySeconds",
        "AutoPhaseDownshiftDelaySeconds",
        "auto_phase_downshift_delay_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/PhaseUpshiftHeadroomWatts",
        "AutoPhaseUpshiftHeadroomWatts",
        "auto_phase_upshift_headroom_watts",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/PhaseDownshiftMarginWatts",
        "AutoPhaseDownshiftMarginWatts",
        "auto_phase_downshift_margin_watts",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/PhaseMismatchRetrySeconds",
        "AutoPhaseMismatchRetrySeconds",
        "auto_phase_mismatch_retry_seconds",
        "float",
    ),
    RuntimeOverrideSpec(
        "/Auto/PhaseMismatchLockoutCount",
        "AutoPhaseMismatchLockoutCount",
        "auto_phase_mismatch_lockout_count",
        "int",
    ),
    RuntimeOverrideSpec(
        "/Auto/PhaseMismatchLockoutSeconds",
        "AutoPhaseMismatchLockoutSeconds",
        "auto_phase_mismatch_lockout_seconds",
        "float",
    ),
)

RUNTIME_OVERRIDE_BY_PATH: dict[str, RuntimeOverrideSpec] = {
    spec.dbus_path: spec for spec in RUNTIME_OVERRIDE_SPECS
}
RUNTIME_OVERRIDE_BY_CONFIG_KEY: dict[str, RuntimeOverrideSpec] = {
    spec.config_key: spec for spec in RUNTIME_OVERRIDE_SPECS
}
RUNTIME_OVERRIDE_SECTION = "RuntimeOverrides"


class _CasePreservingConfigParser(configparser.ConfigParser):
    """Config parser that keeps option names exactly as written."""

    def optionxform(self, optionstr: str) -> str:
        return optionstr
