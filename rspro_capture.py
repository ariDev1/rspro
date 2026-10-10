"""Capture subprocess supervision and reusable terminal-safe text handling."""
import os
import selectors
import signal
import subprocess
import threading
import time

RETRY_DELAY = 2.0
IDLE_TIMEOUT = 15.0
CONNECT_TIMEOUT = 30.0


def clean_text(value, limit=160):
    return ''.join(c if c.isprintable() else ' ' for c in str(value))[:limit]


class RetryBackoff:
    def __init__(self, base=RETRY_DELAY, maximum=30.0, reset_after=10.0):
        self.base, self.maximum, self.reset_after = base, maximum, reset_after
        self.next_delay = base

    def failed(self, healthy_duration=0):
        if healthy_duration >= self.reset_after:
            self.next_delay = self.base
        delay = self.next_delay
        self.next_delay = min(self.maximum, self.next_delay * 2)
        return delay


def stop_process(process):
    """Terminate the capture group, including helpers, then reap its leader."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    # A helper can outlive an already-reaped leader and ignore SIGTERM.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=1)


class Collector(threading.Thread):
    def __init__(self, state, command, env, retry_delay=RETRY_DELAY,
                 idle_timeout=IDLE_TIMEOUT, connect_timeout=CONNECT_TIMEOUT,
                 max_retry_delay=30.0, reset_after=10.0):
        super().__init__(name=f'meter-{state.address}', daemon=True)
        self.state, self.command, self.env = state, command, env
        self.retry_delay, self.idle_timeout = retry_delay, idle_timeout
        self.connect_timeout = connect_timeout
        self.backoff = RetryBackoff(retry_delay, max_retry_delay, reset_after)
        self.healthy_duration = 0.0
        self.stopping = threading.Event()

    def stop(self):
        self.stopping.set()

    def run(self):
        while not self.stopping.is_set():
            self.state.set_status('Connecting')
            self.healthy_duration = 0.0
            try:
                self.capture()
            except (OSError, subprocess.SubprocessError) as exc:
                self.state.set_status('Reconnecting', str(exc))
            if not self.stopping.is_set():
                self.state.set_status('Reconnecting')
                self.stopping.wait(self.backoff.failed(self.healthy_duration))
        self.state.set_status('Stopped')

    def capture(self):
        # sigrok treats EOF on stdin as a stop request. Keep a private pipe open
        # so only the dashboard reads terminal keys and captures keep running.
        process = subprocess.Popen(self.command, env=self.env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   process_group=0)
        started_at = last_sample = time.monotonic()
        first_sample = None
        buffers = {'out': b'', 'err': b''}
        try:
            with selectors.DefaultSelector() as poller:
                poller.register(process.stdout, selectors.EVENT_READ, 'out')
                poller.register(process.stderr, selectors.EVENT_READ, 'err')
                while not self.stopping.is_set():
                    for key, _ in poller.select(timeout=.2):
                        data = os.read(key.fileobj.fileno(), 4096)
                        channel = key.data
                        if not data:
                            poller.unregister(key.fileobj)
                            # sigrok normally emits complete lines, but preserve a final fragment.
                            data, buffers[channel] = buffers[channel] + b'\n', b''
                        else:
                            data = buffers[channel] + data
                        lines = data.split(b'\n')
                        buffers[channel] = lines.pop()[-4096:]
                        for raw in lines:
                            line = raw.decode('utf-8', errors='replace').strip()
                            if channel == 'out' and self.state.update_line(line):
                                last_sample = time.monotonic()
                                if first_sample is None:
                                    first_sample = last_sample
                                self.healthy_duration = last_sample - first_sample
                            elif channel == 'err' and line:
                                with self.state.lock:
                                    self.state.error = clean_text(line)
                        if channel == 'err' and buffers[channel]:
                            with self.state.lock:
                                self.state.error = clean_text(buffers[channel].decode('utf-8', 'replace'))
                    if process.poll() is not None and not poller.get_map():
                        with self.state.lock:
                            error = self.state.error or f'Capture exited ({process.returncode})'
                        self.state.set_status('Reconnecting', error)
                        break
                    budget = self.connect_timeout if first_sample is None else self.idle_timeout
                    reference = started_at if first_sample is None else last_sample
                    if time.monotonic() - reference >= budget:
                        reason = 'Connection setup timed out' if first_sample is None else 'No measurements'
                        self.state.set_status('Reconnecting', reason + '; restarting capture')
                        break
        finally:
            try:
                stop_process(process)
            finally:
                process.stdin.close()
                process.stdout.close()
                process.stderr.close()
