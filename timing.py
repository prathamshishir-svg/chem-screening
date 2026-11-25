"""Best-effort terminal timing diagnostics for screening operations."""

import time


def start_timer():
    """Get a monotonic start value without allowing diagnostics to disrupt work."""
    try:
        return time.perf_counter()
    except Exception:
        return None


def log_duration(label, started):
    """Log an elapsed duration; timing and output failures are intentionally ignored."""
    try:
        if started is None:
            return
        elapsed = max(0.0, time.perf_counter() - started)
        print(f"[timing] {label}: {elapsed:.3f}s", flush=True)
    except Exception:
        pass


def timed_sleep(seconds, label):
    """Preserve a synchronous wait and report its elapsed time when possible."""
    started = start_timer()
    try:
        time.sleep(seconds)
    finally:
        log_duration(label, started)
