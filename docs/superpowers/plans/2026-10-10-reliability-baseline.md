# Reliability Baseline Implementation Plan

> **For agentic workers:** Execute the runtime/data tasks inline; CI infrastructure is independently delegated under the parallel-agent skill.

**Goal:** Harden process lifecycle and recording integrity and establish repeatable checks before declaring a stable release.
**Architecture:** Separate capture/discovery modules; recorder durability and integrity sidecar; read-only recording audit; pinned CI and documented hardware gate.
**Tech Stack:** Python 3.11+ stdlib, Bash, sigrok-cli/BlueZ, Ruff dev-only, GitHub Actions.
**Spec:** ../specs/2026-10-10-reliability-baseline.md

## Global Constraints

Keep existing CLI/UI behavior and data columns. No runtime Python dependencies. Startup30s, idle15s, retry2→30s, reset after10s of samples. Version0.2.0-rc.1 is a candidate until hardware soak passes. No broad code formatting or unrelated features.

## Review Focus

Accepted sample racing stop; fsync/metadata failure or sudden interruption; slow initial BLE connect; repeated early failures versus genuinely healthy streams; child processes surviving signals and CLI-only packaging of moved modules.

### Task 1: Capture lifecycle
- [x] Write failing tests for separate startup timeout and growing/resetting interruptible retry delay.
- [x] Move Collector/stop_process/sanitization to rspro_capture.py; discovery to rspro_discovery.py, preserving public imports used by tests.
- [x] Implement timeout/backoff fixes and update isolated packaging.

### Task 2: Recording integrity
- [x] Reproduce queue-empty/stop race and add regression.
- [x] Add fsync checkpoints and atomic recording metadata; cover failures, incomplete runs and clean completion.
- [x] Add tools/check_recording.py and tests for integrity/gap checks.
- [x] Fix numeric edge cases found by functional lint/testing.

### Task 3: Repeatable baseline checks
- [x] Integrate independently prepared pinned CI and functional lint.
- [x] Add VERSION/--version and install version artifact; include metadata version.
- [x] Document hardware soak and release gate, with current verification evidence.
- [x] Run full tests/lint/syntax/checker, independent review and local installation.
- [ ] Push and confirm hosted CI under existing authorization.

## Verification evidence

54 unittest tests pass on local Python 3.14; Ruff functional lint, Bash syntax, Python compilation and git diff checks pass. PTY tests verify SIGINT, SIGTERM and SIGHUP flush/reap; SIGKILL leaves incomplete evidence. Independent review found and resolved final metadata sync false-success and missing metadata timestamp validation, both with failing-first regressions. Installed CLI reports 0.2.0-rc.1; naming configuration preserved. The eight-hour physical soak is documented but has not been run for this candidate.
