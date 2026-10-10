# Reliability baseline

User approved a reliability pass covering lifecycle hardening, recording failure tests, CI, and a documented hardware soak test. Preserve automatic S3 discovery, terminal theme colors, meter selection, trend charts and unit-aware alerts. Do not call this hardware-proven stable without the soak evidence.

## Runtime

Separate capture/process supervision and Bluetooth discovery from meter state/curses rendering without breaking existing entrypoints. Captures have a 30-second startup budget (the underlying BLE connection can take 20 seconds), then a 15-second idle timeout after readings begin. Failed captures retry at 2, 4, 8, 16, 30 seconds, capped at 30; ten seconds of sustained samples resets backoff. Stop remains interruptible throughout. Shutdown must reap process leaders/helpers and restore terminal state on q, SIGINT, SIGTERM, SIGHUP and exceptions. Tests use short configurable budgets, not long sleeps.

## Recording

Accepted samples must drain even when queue-empty and stop race. Flush and fsync CSV periodically and on clean close. A companion `<csv>.meta.json` records software version, UTC start/end, accepted/written/rejected counts, durability checkpoint, and completed/error state. Create sidecar exclusively; update only owned sidecars atomically. Completed requires successful final data sync, close, and all accepted rows written without errors. Interrupted runs retain incomplete metadata; no false success for disk errors or queue overflow. Original CSV column schema remains unchanged. Add a read-only checker for CSV/sidecar agreement and per-meter Measurement gaps; a nonzero result means incomplete/corrupt evidence or gaps over the chosen tolerance.

## Validation/release

Identify the baseline as version 0.2.0-rc.1, expose rspro --version, install every required module/version artifact, and include the version in recording metadata. CI pins actions/tooling and runs stdlib tests on Python 3.11/3.14, functional lint, syntax/compile checks and isolated CLI-only installation. Document an eight-hour uninterrupted hardware soak plus explicit meter/adapter interruption and mode-change tests, recording verification and evidence requirements. Do not disconnect the user's live instruments for tests without coordination. Record actual tests performed separately from future soak instructions.
