# SPDX-License-Identifier: GPL-3.0-or-later
"""Builder helpers for Auto policy loading and compatibility syncing."""

from __future__ import annotations

from configparser import SectionProxy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shelly_wallbox.auto.policy import AutoPolicy  # pragma: no cover


def _config_value(defaults: SectionProxy, key: str, fallback: Any) -> str:
    return defaults.get(key, str(fallback))


def build_auto_policy_from_config(policy_cls: type["AutoPolicy"], defaults: SectionProxy) -> "AutoPolicy":
    from shelly_wallbox.auto.policy import AutoLearnChargePowerPolicy, AutoPhasePolicy, AutoStopEwmaPolicy, AutoThresholdProfile

    normal_start = float(_config_value(defaults, "AutoStartSurplusWatts", 1500))
    normal_stop = float(_config_value(defaults, "AutoStopSurplusWatts", normal_start - 400))
    min_soc = float(_config_value(defaults, "AutoMinSoc", 30))
    return policy_cls(
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
            mismatch_retry_seconds=float(_config_value(defaults, "AutoPhaseMismatchRetrySeconds", 300)),
            mismatch_lockout_count=int(_config_value(defaults, "AutoPhaseMismatchLockoutCount", 3)),
            mismatch_lockout_seconds=float(_config_value(defaults, "AutoPhaseMismatchLockoutSeconds", 1800)),
            prefer_lowest_phase_when_idle=defaults.get(
                "AutoPhasePreferLowestWhenIdle",
                "1",
            ).strip().lower() in ("1", "true", "yes", "on"),
        ),
    )


def build_auto_policy_from_service(policy_cls: type["AutoPolicy"], svc: Any) -> "AutoPolicy":
    from shelly_wallbox.auto.policy import AutoLearnChargePowerPolicy, AutoPhasePolicy, AutoStopEwmaPolicy, AutoThresholdProfile

    return policy_cls(
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
            mismatch_retry_seconds=float(getattr(svc, "auto_phase_mismatch_retry_seconds", 300.0)),
            mismatch_lockout_count=int(getattr(svc, "auto_phase_mismatch_lockout_count", 3)),
            mismatch_lockout_seconds=float(getattr(svc, "auto_phase_mismatch_lockout_seconds", 1800.0)),
            prefer_lowest_phase_when_idle=bool(getattr(svc, "auto_phase_prefer_lowest_when_idle", True)),
        ),
    )


def validate_auto_policy(policy: "AutoPolicy", svc: Any | None = None) -> "AutoPolicy":
    policy.clamp()
    if svc is not None:
        policy.apply_to_service(svc)
    return policy


def load_auto_policy_from_config(defaults: SectionProxy, svc: Any | None = None) -> "AutoPolicy":
    from shelly_wallbox.auto.policy import AutoPolicy

    return validate_auto_policy(AutoPolicy.from_config(defaults), svc)
