# State And Priority Model

This file is the compact design spec for the wallbox outward state model.

Its purpose is to keep future features such as `Scheduled`, new charger
backends, or EVCC-like policy layers from weakening the current ordering by
accident.

## Core Rule

The service may observe multiple truths internally, but it must publish one
stable outward truth on DBus and therefore on the Venus GUI and MQTT bridge.

When signals disagree, the outward truth follows this priority order:

1. `manual override`
2. hard EVSE fault / lockout
3. safety / interlock / explicit feedback fault
4. retry / recovery / temporary blocked state
5. scheduled night boost
6. auto intent
7. heuristics / fallback inference

## Authoritative Sources

Use the most explicit source available.

- Explicit EVSE-side faults beat charger-native `ready` or `charging` text.
- Explicit switch feedback and interlock beat heuristic contactor suspicion.
- Confirmed external phase observation beats contradictory native charger phase.
- Native charger hard-fault text beats fallback relay/power inference.
- Fallback relay/power inference is only for the absence of stronger truth.

## Persistence Rules

Persistent:

- explicit runtime overrides for allowed policy/tuning keys
- phase-switch lockout state
- stable manual/user-facing runtime selections

Transient:

- charger retry backoff windows
- transport detail snapshots
- fresh readback-derived conflict states
- temporary staged scheduled snapshots

Rule of thumb:

- persist user intent and deliberate safety degradation
- do not persist transport noise or temporary recovery windows

## Supported Topologies

Allowed:

- `combined`
- `split` with separate `meter` and `switch`
- charger-native split setup
- charger plus external phase switch
- meterless charger-native split setup

Forbidden:

- `MeterType=none` in `combined`
- `SwitchType=none` in `combined`
- `MeterType=none` in `split` without charger backend
- `SwitchType=none` in `split` without charger backend
- runtime overrides changing structural backend selection or backend config paths

## Contracts For New Features

Before merging a bigger feature, answer these three questions explicitly:

1. Which truth is authoritative when signals disagree?
2. What may persist across restart, and what must stay transient?
3. Which configs should remain explicitly forbidden?

If those answers are unclear, the feature is not finished yet.

## Required Safeguards

Every feature that touches outward state should add at least one of:

- a priority test
- a topology/conflict-matrix test
- a restart/persistence invariant test
- a config-space validation test

The goal is not maximum abstraction. The goal is to keep the outward state
model explicit, local, and hard to erode accidentally.
