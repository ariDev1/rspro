# Recording, trends and limits for the lab dashboard

The user approved the three recommended priorities: CSV recording, live trend charts and threshold alerts. Preserve automatic S3 discovery, the Measurement/Additional reading column names, device-derived labels, and existing single-meter CLI usage.

Use Python 3.11+ standard library only. A small `rspro_features.py` module owns reading normalization, limits, chart generation, and asynchronous CSV writing. The existing dashboard owns capture state and interaction. Install both modules beside rspro.

Record actual channel updates rather than pairing asynchronously received readings. CSV columns: timestamp_utc, meter_address, meter_name, channel, reading, value, unit, mode. Use UTC ISO timestamps, channel measurement/additional, original reading text, and normalized SI numeric values. Preserve nonnumeric/infinite readings with a blank numeric cell. Start with `--record PATH` or press r for a unique file under XDG_DATA_HOME/rspro/recordings (default ~/.local/share). Never overwrite an existing file. A bounded writer queue avoids disk I/O in collector threads; write/queue errors stop recording visibly instead of silently dropping data. Close drains accepted updates and flushes the file.

Each meter retains at most 120 numeric Measurement samples. Normalize SI prefixes; clear history when unit/mode changes, when readings become nonnumeric, and across reconnects. Terminal sparklines show recent samples with unit/mode and range. Flat histories use a flat middle-height line. t toggles graphs; resize and scrolling remain safe.

CLI limits: --below VALUE, --above VALUE, --unit UNIT, optional --mode MODE. Below and above are strict comparisons (equality is in range). A unit is mandatory when a bound is supplied; bounds must be finite and ordered. Optional per-meter configuration `limits` accepts below, above, unit, mode and overrides CLI defaults. Prefix-normalized comparison permits V/mV but rejects incompatible units or specified modes. Stale/disconnected/nonnumeric readings cannot appear as fresh active alerts. Active limits show a LOW/HIGH label and highlighted row. No audible alerts by default.

Recording state/path and errors remain visible. Startup validates arguments/configuration before capture. Test parsing/prefixes, nonfinite values, unit changes, limits, bounded history, CSV concurrency/exclusive creation/error handling, and PTY recording/chart/alert controls with fake meters. Verify actual recording with available physical meters without claiming synchronization or calibration.
