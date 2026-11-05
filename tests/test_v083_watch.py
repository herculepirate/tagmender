# 0.8.3 watcher fixes (no network):
# - a profile that asks no database (the web only): an outage of every web source is an
#   outage, tried again soon - not "nothing found" for days;
# - a profile saved with a watch interval over a day (allowed before 0.5.0) still loads, as a day.
import os as _os, tempfile as _tempfile
_os.environ["XDG_CONFIG_HOME"] = _tempfile.mkdtemp(prefix="tm-config-")
_os.environ["XDG_STATE_HOME"] = _tempfile.mkdtemp(prefix="tm-state-")
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import profiles as pf  # noqa: E402
import watcher  # noqa: E402


def prof(web_only):
    off = {"enabled": True, "identify": not web_only}
    return pf.from_dict({"name": "W", "providers": {"musicbrainz": off, "discogs": dict(off),
                                                  "web": {"enabled": True}}})


class WebOnlyOutage(unittest.TestCase):
    def status(self, p, errs):
        w = watcher.Watcher.__new__(watcher.Watcher)
        prop = types.SimpleNamespace(status="unresolved", provider_errors=errs)
        return w._status_of({"status": "unresolved"}, prop, "unresolved", p)

    def test_web_only_profile_retries_soon(self):
        self.assertEqual(self.status(prof(True), {"web": "outage"}), "provider_error")

    def test_with_a_database_the_web_alone_keeps_the_unresolved_schedule(self):
        self.assertEqual(self.status(prof(False), {"web": "outage"}), "unresolved")
        self.assertEqual(self.status(prof(False), {"musicbrainz": "outage"}), "provider_error")


class LongInterval(unittest.TestCase):
    def test_an_older_profile_with_two_days_loads_as_one(self):
        p = pf.from_dict({"name": "Slow", "watch": {"settle_minutes": 10, "interval_seconds": 172800}})
        self.assertEqual(p["watch"]["interval_seconds"], 86400)
        pf.save(p)
        self.assertEqual(pf.load("Slow")["watch"]["interval_seconds"], 86400)


if __name__ == "__main__":
    unittest.main()
