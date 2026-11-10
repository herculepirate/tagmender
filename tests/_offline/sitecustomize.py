"""Loaded by every test process (run_tests.sh puts this folder on PYTHONPATH): no test reaches
the network. A connection that leaves this computer raises NetworkOffInTests at once, naming the
host. It is not an OSError on purpose: urllib would wrap an OSError as a network error, and
TagMender's clients retry those for minutes (the outage ladder); this one passes through every
retry, so a test that would ask a real server (MusicBrainz, Discogs, a shop) fails in
milliseconds and its traceback names it. Fix such a test by stubbing the look-up (FakeHttp, a
mock.patch of the provider) - never by allowing the network. This computer itself (a test's own
local server) is allowed. TAGMENDER_TESTS_NETWORK=1 allows the network for a live check."""
import builtins
import os
import socket

if os.environ.get("TAGMENDER_TESTS_NETWORK") != "1":
    class NetworkOffInTests(Exception):
        """A test tried to reach the network (tests/_offline/sitecustomize.py)."""

    builtins.NetworkOffInTests = NetworkOffInTests       # (a test may catch it by name)
    _LOCAL = ("localhost", "127.", "::1", "0.0.0.0")
    _connect = socket.socket.connect
    _connect_ex = socket.socket.connect_ex

    def _local(addr) -> bool:
        if not isinstance(addr, tuple):                  # (a Unix socket: a path, local)
            return True
        return str(addr[0]).startswith(_LOCAL)

    def _refuse(addr):
        raise NetworkOffInTests(f"a test tried to reach {addr[0]}:{addr[1]}: tests run without the "
                                "network (tests/_offline); stub this look-up")

    def connect(self, addr):
        if not _local(addr):
            _refuse(addr)
        return _connect(self, addr)

    def connect_ex(self, addr):
        if not _local(addr):
            _refuse(addr)
        return _connect_ex(self, addr)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
