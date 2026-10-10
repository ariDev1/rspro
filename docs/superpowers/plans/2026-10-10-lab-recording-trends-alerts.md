# Recording, Trends and Alerts Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement inline.

**Goal:** Add CSV recording, live terminal trends, and unit-aware alerts to the automatic S3 dashboard.
**Architecture:** Features module for numeric normalization/limits/CSV writer; dashboard state accumulates bounded trends and publishes actual channel events; curses controls recording and graph visibility.
**Tech Stack:** Python 3.11+ standard library, existing sigrok-cli and BlueZ.
**Spec:** ../specs/2026-10-10-lab-recording-trends-alerts.md

## Global Constraints

Keep automatic discovery and single-meter CLI. No additional Python dependencies. History max120 samples. Record UTC timestamps and individual real channel updates. Never overwrite files. No false threshold comparison across different measurement units/modes.

## Review Focus

Overrange/nonfinite readings; SI prefix or mode changes; files that already exist or become unwritable; concurrent collectors and stopping recording; terminal resize and changes in dynamically discovered meter count.

### Task 1: Numeric features and CSV recording
- [x] Write failing tests for reading parsing, unit-aware limits, flat/trending charts, and CSV stream/concurrency/error behavior.
- [x] Implement NumericReading, Limits, sparkline, CSVRecorder and RecordingSession in rspro_features.py.
- [x] Run tests and fix failures.

### Task 2: Collector state integration
- [x] Write failing tests for bounded history/reset on mode change/reconnect, per-meter limits, real channel events and recording toggles.
- [x] Add feature state and recording session hooks to MeterState, preserving original parsing strings.
- [x] Run all tests.

### Task 3: Terminal controls and deployment
- [x] Write failing PTY tests for CSV recording, graph toggle and threshold display with simulated streams.
- [x] Add CLI options, r/t controls, unit-aware alert highlighting and recorder state/errors to curses.
- [x] Install the features module alongside dashboard, document usage and threshold configuration.
- [x] Run all tests, syntax checks, review and live verification, then install and push using existing user authorization.

## Execution ledger

- Numeric/CSV tests failed before rspro_features existed, then passed. State/history/limit/recording tests failed before integration, then passed.
- PTY feature test failed against the prior CLI flags, then passed after UI integration. Tests cover automatic newly discovered meters with recording and LOW/HIGH limits, not just configured meters.
- Installer integration caught the missing features module; installer now installs both Python files together and its isolated CLI-only test passes.
- Recorder tests cover exclusive creation, concurrent updates, draining accepted samples, disk failure and queue overflow visibility.
- Review found an old alert could reactivate after reconnect on an auxiliary-only update; primary_current now requires a new Measurement reading. Regression failed before the fix and passed after it.
- Ruling: implement only the three recommended priorities; user explicitly confirmed that scope.
- Ruling: record individual channel events to preserve actual update timing, keep raw text, and use normalized numeric fields for later analysis.
- Ruling: visual limits require an explicit unit and optionally mode; no default alert bounds and no audible alerts.

- Final verification: all37 tests pass; Bash syntax, Python compilation and whitespace checks pass. The live no-config/default-permission dashboard recorded110 UTC channel updates fromboth physicalS3meters, displayed trends and LOW under the example24VDC limit, and exitedcleanly. Last values22.93VDC and27.57VDC. CLI-only deployment preserves the user configuration.
