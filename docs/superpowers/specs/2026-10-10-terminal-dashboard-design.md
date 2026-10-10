# Multi-meter terminal dashboard

The user wants all lab meters and their live readings together on screen, prefers a terminal, and does not yet know the meter count. The approved approach is `rspro dashboard`, stable named rows, a meter-address configuration, independent reconnects, visible stale data, scrolling, and clean shutdown. Keep existing single-meter usage working.

Use Python 3.11 or newer standard-library curses and one independently supervised sigrok-cli subprocess per meter. Run from the repository or install the dashboard beside the wrapper. Default configuration: `~/.config/rspro/meters.json`, overridable with `--config PATH` or `RSPRO_CONFIG`. Its JSON object contains a nonempty `meters` list with unique `address` values and nonempty `name` strings. Names and errors must be sanitized before terminal display. No hard-coded connection count; scrolling handles the screen limit, while adapter capacity remains a hardware test.

Show name/address, primary and secondary readings, status and primary reading age. Mark readings stale after five seconds. A transport exit triggers a two-second retry for that meter only; fifteen seconds without measurements triggers a reconnect. Preserve old readings with stale/disconnected status. Use monotonic time. Stop collectors and restore terminal state on q, Ctrl+C, terminal close, and UI errors. Resize and narrow terminals must not crash. Up/down and page keys scroll.

Reuse the patched local libsigrok prefix and RSPRO_SIGROK_BIN. `RSPRO_USE_SUDO=no` runs directly; `yes` validates sudo credentials once before curses; `auto` probes normal-user discovery with a finite timeout and requires a real device line. If sudo is required, validate once and use noninteractive sudo in all workers. No password prompts may appear inside curses. Stream both primary and secondary output using coreutils stdbuf to avoid pipe buffering; drain errors concurrently and retain only the latest diagnostic.

Configuration and dependencies are validated before capture. Missing/invalid configuration prints actionable errors without starting subprocesses. Add a safe template and instructions for locating addresses. Installer installs the Python dashboard and checks Python/stdbuf prerequisites. Existing driver patch remains unchanged.

Validate with standard-library unit tests, real subprocess stand-ins for simultaneous streams/reconnect/cleanup, and a PTY smoke test for curses, scrolling, and exit. Verify on the available real meter separately; multiple physical meters and adapter limits are not claimed tested without evidence.
