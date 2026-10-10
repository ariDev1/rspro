# RS PRO / APPA S3 BLE discovery timing bug

## Observed on 2026-10-10

On an Omarchy x86_64 machine, an RS PRO S3 connected through the APPA BLE transport but discovery reported `APPA N/A`, inconsistent version numbers, and model ID `32764`. Continuous acquisition then stopped immediately:

```text
appa-dmm: Aborted in appadmm_receive, result -10
```

The environment used sigrok-cli 0.7.2, BlueZ 5.87, and the Cymaphore libsigrok fork at `8aa4dcd2973ae2a0f961afa61e0ecbcb4e3a92dc` (runtime version 0.6.0). The system CLI was compiled against another libsigrok version; this observation does not establish compatibility with every future sigrok-cli package.

## Packet evidence and cause

Temporary receive tracing showed a device-information response beginning with `55 55 00 34` arriving in separate BLE notifications. The first two reads each supplied 20 bytes, at approximately 0.412 s and 0.512 s. Discovery proceeded at 0.512 s before the complete response was available. A remaining 19-byte fragment arrived at about 0.563 s, after the acquisition command had been sent. Acquisition then aborted.

`sr_tp_appa_send_receive()` returns `TRUE` for a complete response, `FALSE` when its receive attempts are exhausted, or a negative error. `appadmm_rere_read_information()` previously returned that `FALSE` directly. Because `FALSE` and `SR_OK` are both zero, the caller interpreted a timeout as success and consumed information that had not been decoded. The garbage device metadata is consistent with this uninitialized-response path.

The default receive budget was 500 ms, implemented as ten receive attempts with a 50 ms packet timing. The trace shows that budget was too short for this meter's fragmented information response in the tested setup. The leftover fragment entering acquisition is consistent with the subsequent parser failure; this was not a measurement-format problem.

## Repository fix

[The patch](../patches/libsigrok-appa-s3-timing.patch) makes two changes:

1. Increase `SR_TP_APPA_RECEIVE_TIMEOUT` from 500 to 1000 ms (twenty receive attempts).
2. Preserve negative transport errors and map `FALSE` to `SR_ERR_TIMEOUT` before decoding device information.

The timeout constant is shared by APPA transport users, so the change can increase their failed-request wait time too. The information-response error correction is independent of the larger timing budget. Temporary raw packet tracing is excluded from the patch.

## Result and limits

With both fixes, discovery reported `APPA S3 20.16`, model ID `11`, with Primary and Secondary channels. The roughly ten-second continuous capture repeatedly produced:

```text
Primary: 27.57 V DC
Secondary: inf
```

Discovery exited with status `0`. The continuous command was deliberately bounded by `timeout` and exited with `124`; that status records the test's time limit, not a capture failure.

This confirms the combined changes worked for one RS PRO S3 on the tested machine. It does not establish a worst-case BLE response bound or validate all measurement modes, adapters, or APPA models. The receive budget can still expire; the corrected return handling makes that a real error instead of false discovery success.

## Reproduce and verify

Install from the README instructions, use your meter's address, and run both discovery and a bounded capture with the patched prefix:

```bash
sudo env LD_LIBRARY_PATH="$HOME/.local/share/rspro-sigrok/prefix/lib" \
  sigrok-cli -l 5 -d 'appa-dmm:conn=bt/appa-dmm/AA-BB-CC-DD-EE-FF' --scan
sudo env LD_LIBRARY_PATH="$HOME/.local/share/rspro-sigrok/prefix/lib" \
  timeout 10s sigrok-cli -l 5 \
  -d 'appa-dmm:conn=bt/appa-dmm/AA-BB-CC-DD-EE-FF' --continuous
```

Expect stable S3 identification and repeated primary readings. Status `124` is expected when `timeout` ends an otherwise continuing capture. For comparison with unpatched behavior, use a separate checkout and prefix at the pinned revision without applying the patch; do not overwrite the working installation.

The original evidence remains in the originating machine's `~/.local/share/rspro-sigrok/logs/`: `rspro-debug-20261010-155450.log`, `rspro-raw-packets-20261010-155725.log`, and `rspro-timing-fix-20261010-155943.log`. Those logs are not required to build or run the repository and are not bundled here; the relevant observations are transcribed above.
