# Diagnostics

This file collects the operational DBus paths, log locations, and quick checks
for live troubleshooting on Venus OS.

## Service Commands

```bash
svstat /service/dbus-venus-evcharger
svc -t /service/dbus-venus-evcharger
tail -f /var/volatile/log/dbus-venus-evcharger/current
tail -f /var/volatile/log/dbus-venus-evcharger/auto-reasons.log
```

## Backend Diagnostics

### Backend composition

- `/Auto/BackendMode`
- `/Auto/MeterBackend`
- `/Auto/SwitchBackend`
- `/Auto/ChargerBackend`

### Charger command health

- `/Auto/ChargerWriteErrors`
- `/Auto/ChargerCurrentTarget`
- `/Auto/ChargerCurrentTargetAge`
- `/Auto/ChargerEstimateActive`
- `/Auto/ChargerEstimateSource`
- `/Auto/LastChargerEstimateAge`

### Outward EVSE state

- `/Auto/StatusSource`
- `/Auto/FaultActive`
- `/Auto/FaultReason`
- `/Auto/RecoveryActive`

### Transport and retry

- `/Auto/ChargerTransportActive`
- `/Auto/ChargerTransportReason`
- `/Auto/ChargerTransportSource`
- `/Auto/ChargerTransportDetail`
- `/Auto/LastChargerTransportAge`
- `/Auto/ChargerRetryActive`
- `/Auto/ChargerRetryReason`
- `/Auto/ChargerRetrySource`
- `/Auto/ChargerRetryRemaining`

## Scheduled Diagnostics

- `/Auto/ScheduledState`
- `/Auto/ScheduledStateCode`
- `/Auto/ScheduledReason`
- `/Auto/ScheduledReasonCode`
- `/Auto/ScheduledNightBoostActive`
- `/Auto/ScheduledTargetDayEnabled`
- `/Auto/ScheduledTargetDay`
- `/Auto/ScheduledTargetDate`
- `/Auto/ScheduledFallbackStart`
- `/Auto/ScheduledBoostUntil`

## Phase And Contactor Diagnostics

- `/Auto/PhaseObserved`
- `/Auto/PhaseMismatchActive`
- `/Auto/PhaseLockoutActive`
- `/Auto/PhaseLockoutTarget`
- `/Auto/PhaseLockoutReason`
- `/Auto/PhaseSupportedConfigured`
- `/Auto/PhaseSupportedEffective`
- `/Auto/PhaseDegradedActive`
- `/Auto/ContactorFaultCount`
- `/Auto/ContactorLockoutActive`
- `/Auto/ContactorLockoutReason`
- `/Auto/ContactorLockoutSource`
- `/Auto/ContactorLockoutReset`
- `/Auto/PhaseLockoutReset`

## Switch Feedback And Interlock

- `/Auto/SwitchFeedbackClosed`
- `/Auto/SwitchInterlockOk`
- `/Auto/SwitchFeedbackMismatch`
- `/Auto/LastSwitchFeedbackAge`

## Runtime Override Diagnostics

- `/Auto/RuntimeOverridesActive`
- `/Auto/RuntimeOverridesPath`

## Software Update Diagnostics

- `/Auto/SoftwareUpdateAvailable`
- `/Auto/SoftwareUpdateState`
- `/Auto/SoftwareUpdateStateCode`
- `/Auto/SoftwareUpdateDetail`
- `/Auto/SoftwareUpdateCurrentVersion`
- `/Auto/SoftwareUpdateAvailableVersion`
- `/Auto/SoftwareUpdateNoUpdateActive`
- `/Auto/SoftwareUpdateRun`
- `/Auto/SoftwareUpdateLastCheckAge`
- `/Auto/SoftwareUpdateLastRunAge`

The outward update-state vocabulary is fixed and compact:

- `idle`
- `checking`
- `up-to-date`
- `available`
- `available-blocked`
- `running`
- `installed`
- `check-failed`
- `install-failed`
- `update-unavailable`

## Probe Commands

Validate a full wallbox configuration:

```bash
python3 -m venus_evcharger.backend.probe validate-wallbox deploy/venus/config.venus_evcharger.ini
```

Validate individual adapters:

```bash
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-meter.ini
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-switch.ini
python3 -m venus_evcharger.backend.probe validate /data/etc/wallbox-charger.ini
```
