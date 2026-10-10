# Terminal Dashboard Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan inline, task by task.

**Goal:** Display all configured lab meters in one terminal with independent live capture and recovery.
**Architecture:** Python standard-library module beside the existing Bash wrapper, a collector per meter, bounded state protected by a lock, and curses rendering snapshots.
**Tech Stack:** Python 3, curses, threading, subprocess, selectors; existing sigrok-cli/local libsigrok; coreutils stdbuf.
**Spec:** ../specs/2026-10-10-terminal-dashboard-design.md

## Global Constraints

Preserve single-meter CLI behavior. No fixed meter-count limit. Config is ~/.config/rspro/meters.json by default. Stale after 5s; retry after 2s; idle reconnect after 15s. Sudo prompts only before curses. No third-party Python dependencies.

## Review Focus

Invalid or duplicate addresses fail before capture. Dead/stalled meters recover independently. Both stdout and stderr must drain without unbounded memory. Terminal resize and long/control-character text remain safe. Quitting terminates capture processes.

### Task 1: Configuration and state
- [x] Write and run failing tests for config parsing, duplicate addresses, primary/secondary parsing, stale and disconnected states.
- [x] Implement `load_meters(path)`, `MeterState`, and sanitized snapshots in `rspro_dashboard.py`.
- [x] Run tests to verify behavior.

### Task 2: Collection lifecycle
- [x] Write failing integration tests using executable subprocess fixtures: simultaneous streams, one failure/retry, stderr noise, stalled streams and shutdown.
- [x] Implement per-meter collector, finite discovery, sudo preflight, bounded stream buffers and process cleanup.
- [x] Run the full suite; verify no subprocess survives shutdown.

### Task 3: User interface and installation
- [x] Write failing wrapper tests for dashboard help and invalid config; add a PTY smoke test.
- [x] Add dashboard dispatch, curses table, scrolling, resize, and clean exit.
- [x] Install dashboard beside rspro, add example config and README instructions.
- [x] Run all tests and syntax checks. Verify installer packaging in an isolated destination without rebuilding unchanged libsigrok. Review and fix defects.

## Execution ledger

- Configuration/state and collector tests failed before the module existed, then passed with implementation.
- Wrapper and installer integration tests failed before dashboard dispatch/CLI-only installation, then passed.
- PTY test verifies eight simulated meters, scrolling to the last meter, resize to 4x25, and clean quit.
- Ruling: execute inline in the current checkout following the user's "do it" instruction and preserve existing single-meter behavior.
- Ruling: auto mode probes the first configured meter with a six-second timeout, then chooses one permission mode for the collection session. Permission needs are shared by the adapter; no prompts are permitted within curses.
- Ruling: add --cli-only so existing installations can update Python/wrapper files without rebuilding the unchanged libsigrok source.

- Review fixes: retain the parent session with separate process groups for sudo timestamp compatibility; kill surviving process-group helpers even after the leader exits. Both regression tests failed first, then passed. Python minimum is 3.11 for process_group.

- Final verification: 18 tests pass, Bash syntax checks pass, diff whitespace checks pass. CLI-only install verified both in isolation and at ~/.local/bin. User config created with the known meter, without overwriting existing files.
- Hardware limitation: sandbox prevented libusb initialization. Outside the sandbox, normal-user capture produced no samples within seven seconds; sudo capture reported a password was required. A fresh authenticated live capture must be run from the user terminal; multiple physical meters have not been tested.
