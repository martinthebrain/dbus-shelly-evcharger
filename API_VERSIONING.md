# API Versioning

The local HTTP API is versioned as `v1`.

For the high-level role split between Control, State, Events, and OpenAPI, see
[API_OVERVIEW.md](API_OVERVIEW.md).

## Stable within `v1`

The following are considered stable contracts within `v1`:

- endpoint paths listed in `versioning.stable_endpoints`
- canonical command names
- per-command OpenAPI request schemas for stable commands
- command result statuses
- stable semantic error-code enum
- normalized error-object outer shape
- normalized command-response outer shape
- normalized state-envelope outer shape
- auth header contract: `Authorization: Bearer <token>`

Breaking changes to stable `v1` contracts require a version bump.

Examples of breaking changes:

- removing a stable endpoint
- changing a stable endpoint path
- removing a stable required field
- changing the meaning of a stable command name
- widening or narrowing a stable command schema incompatibly
- changing status/error semantics in an incompatible way

## Experimental within `v1`

Endpoints listed in `versioning.experimental_endpoints` are explicitly
experimental.

Today this includes:

- `GET /v1/events`

Experimental endpoints may evolve within `v1` as long as the stable surface is
not broken.

## Forward-compatible additions

These changes are expected to be compatible within `v1`:

- adding new optional fields
- adding new endpoints and marking them stable or experimental
- adding new capability flags
- adding new state fields inside generic mapping payloads

## Machine-readable source

Clients should treat:

- `GET /v1/openapi.json`
- `GET /v1/capabilities`

as the machine-readable discovery surface for the current API contract and the
current stable-vs-experimental classification.
