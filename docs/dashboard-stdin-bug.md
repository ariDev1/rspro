# Dashboard capture stopped immediately on stdin EOF

## Symptom and comparison

On 2026-10-10 the terminal dashboard showed no readings and repeatedly attempted to connect, while the original `rspro` command continued producing `27.57 V DC` from the same meter and address.

A verbose direct-vs-`stdbuf` comparison used the patched local library and redirected capture stdin to `/dev/null`. Both commands identified the meter as APPA S3, started acquisition, printed `Press any key to stop acquisition.`, and immediately stopped the session without a sample. Output buffering was not the cause: both variants stopped identically.

## Cause and fix

The dashboard collector used `stdin=subprocess.DEVNULL`. sigrok-cli monitors standard input to stop acquisition when a key is pressed, and it also stops when that input reaches EOF. `/dev/null` immediately reports EOF. Repeatedly restarting capture cannot repair that condition.

The collector now uses `stdin=subprocess.PIPE` and keeps its write end open for the capture's lifetime. Each meter has its own input pipe. The dashboard alone reads terminal keys; q, Ctrl+C, or shutdown terminate the collector process group, and the pipe is then closed.

## Verification

A subprocess regression fixture models sigrok's EOF behavior. The new test failed against the `/dev/null` implementation and passed after switching to an open pipe. All 19 project tests passed, including concurrent capture, retry, process cleanup, and curses scrolling/resize tests.

A real-meter collector check outside the sandbox then reported primary `27.57 V DC`, secondary `inf`, state `Connected`, and clean collector shutdown. Multi-meter physical testing remains separate.
