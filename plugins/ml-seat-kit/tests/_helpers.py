"""Test helpers: put the kit's scripts on sys.path, make throwaway seat folders, pin a clock zone."""
import datetime
import json
import os
import pathlib
import sys
import tempfile

KIT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = KIT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def real(td):
    """The long spelling of a TemporaryDirectory. TEMP can be in 8.3 short form (a long user name with a dot), while
    the kit prints long paths; every expected path in a test is built from this one spelling."""
    return pathlib.Path(os.path.realpath(td.name if hasattr(td, "name") else td))


def norm(path):
    """For comparing a path a launcher printed with one a test built: the file system's long form, normal case."""
    return os.path.normcase(os.path.realpath(str(path).strip()))


def short_form(path):
    """The 8.3 short spelling of an existing path, or None when this volume makes no short names (then a test that
    needs one skips and says so)."""
    if os.name != "nt":
        return None
    import ctypes
    buf = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 32768)
    if not 0 < n < 32768 or os.path.normcase(buf.value) == os.path.normcase(str(path)):
        return None
    return buf.value


NO_SHORT_NAMES = "this PC's temp volume makes no 8.3 short names, so there is no short spelling to test with"


def make_seat(base=None, name="Seat"):
    """A temp folder holding a seat marker; returns (TemporaryDirectory, seat Path)."""
    td = tempfile.TemporaryDirectory()
    seat = real(td) / name
    (seat / ".seat").mkdir(parents=True)
    (seat / ".seat" / "seat.json").write_text(json.dumps({"seat_name": name}), encoding="utf-8")
    (seat / "state").mkdir()
    return td, seat


class EasternZone:
    """US Eastern by the 2007 rule, computed by hand (no tzdata needed), so the wake tests give the same answers on
    any laptop clock. The kit itself uses the laptop's own zone."""

    @staticmethod
    def offset(utc):
        y = utc.year
        mar1 = datetime.datetime(y, 3, 1, 7, tzinfo=datetime.timezone.utc)
        start = mar1 + datetime.timedelta(days=(6 - mar1.weekday()) % 7 + 7)
        nov1 = datetime.datetime(y, 11, 1, 6, tzinfo=datetime.timezone.utc)
        end = nov1 + datetime.timedelta(days=(6 - nov1.weekday()) % 7)
        return -4 if start <= utc < end else -5

    def to_dt(self, ts):
        u = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
        off = self.offset(u)
        return u.astimezone(datetime.timezone(datetime.timedelta(hours=off), "EDT" if off == -4 else "EST"))

    def to_ts(self, day, h, m):
        for off in (-4, -5):
            u = datetime.datetime(day.year, day.month, day.day, h, m, tzinfo=datetime.timezone.utc) - datetime.timedelta(hours=off)
            if self.offset(u) == off:
                return u.timestamp()
        return (datetime.datetime(day.year, day.month, day.day, h, m, tzinfo=datetime.timezone.utc)
                + datetime.timedelta(hours=5)).timestamp()
