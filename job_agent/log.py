"""Messages for the user. Printed as usual, and also captured into a run's log
when a run is in progress (so the web dashboard can show them)."""
import sys
import threading

_local = threading.local()


def _record(msg):
    sink = getattr(_local, "sink", None)
    if sink is not None:
        sink.append(str(msg))


def warn(msg):
    print(msg, file=sys.stderr)
    _record(msg)


def info(msg):
    print(msg)
    _record(msg)


class capture:
    """with capture() as lines: ...  collects every warn/info in this thread."""

    def __enter__(self):
        self.lines = []
        self._previous = getattr(_local, "sink", None)
        _local.sink = self.lines
        return self.lines

    def __exit__(self, *exc):
        _local.sink = self._previous
        return False
