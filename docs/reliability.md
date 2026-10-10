# Hardware reliability gate

Version 0.2.0-rc.1 is a candidate, not yet an eight-hour hardware-verified stable release. Automated checks cover fake collectors, startup delays, retry policy, process cleanup, recording queue/failure races, SIGINT/SIGTERM/SIGHUP, and abrupt writer termination. They cannot establish Bluetooth reliability on your adapter.

## Uninterrupted soak

1. Install this checkout (`./install_rspro_cli.sh --cli-only` when the patched library is already installed). Save `rspro --version`, the git commit, adapter model, OS/package versions, and the meter addresses with the test results.
2. Enable Bluetooth on every meter you intend to use together, disconnect other clients, and arrange safe, steady inputs. Ensure meter power and auto-off settings support the test. Set the intended count below; capacity is adapter-dependent.
3. Run `rspro dashboard --record ./soak.csv` for at least eight hours. Keep the host awake. Check that every expected meter appears, continues updating, and remains below the five-second stale threshold. Record any errors or reconnects and their times. Observe memory/process usage at the start and end; it should not grow continuously or accumulate capture processes.
4. Quit with q, keep `soak.csv` and `soak.csv.meta.json` together, then run `python3 tools/check_recording.py ./soak.csv --expect-meters 2 --json > soak-report.json` (replace 2 with the intended count).
5. Pass only if the audit succeeds, every expected meter has samples, no unplanned gaps exceed five seconds, and the observed duration is at least eight hours. The checker does not enforce duration or detect missing samples before a meter's first/after its last sample: check those against your start/end observations. Investigate every unexpected reconnect or stale period even if the CSV audit succeeds.

## Recovery session

Use a separate recording (`./recovery.csv`) so intentional interruptions do not hide soak failures:

- Turn on an additional meter during capture; verify automatic discovery and live data. All-meter recording must include it.
- Switch one meter off, then on. Its row must become disconnected/stale, retain its last reading, and resume automatically. Other meters must keep updating.
- Change voltage/current/resistance mode using an appropriate safe input. Verify displayed units, CSV normalization, and history reset; old-mode alerts must not carry over.
- Stop recording with r, start a selected-meter recording, and verify only selected addresses are recorded. Quit during recording and verify completion metadata.
- Test normal exit and Ctrl+C. No sigrok capture children should remain. Exercise a terminal resize and a longer reconnect wait; quitting must remain responsive.

Audit this intentional-outage session with `python3 tools/check_recording.py ./recovery.csv --allow-gaps --expect-meters 2`; retain the report and your interruption timeline. A passing audit with `--allow-gaps` does not prove uninterrupted capture.

## Failure interpretation and release evidence

A missing/false completion marker, rejected rows, count mismatch, or disk-sync error means the session is unverified/incomplete. Do not edit metadata to make an audit pass. A SIGKILL, host crash, or power loss can lose the last buffered samples; half-second sync checkpoints reduce exposure but are not a guarantee under failing hardware or storage. The sidecar is an integrity ledger, not a cryptographic tamper check.

Keep the command/version information, both recording files, reports, elapsed time, observations, and recovery timeline with your release evidence. Publish a stable version only after automated CI, the uninterrupted soak, and recovery checks pass on the intended Omarchy machine. Repeat the hardware gate after changes to capture timing, Bluetooth stack, sigrok, or adapter.
