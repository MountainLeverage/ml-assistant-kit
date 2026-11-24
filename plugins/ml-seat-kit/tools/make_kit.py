"""Build the kit zip that the assistant launcher downloads: a one-plugin local marketplace.

    python -I make_kit.py --out <kit.zip> [--source <https URL where this zip will be served>]

Zip layout (the launcher runs the zip itself, `python -I <zip> --kit-home <folder>`, which unpacks it into
%LOCALAPPDATA%\\seat-kit\\kits\\<version> and switches kit.txt to it; see scripts/kit_place.py. That folder is then
registered as a local folder marketplace named "seat-kit"):

    __main__.py                         runs plugins/ml-seat-kit/scripts/kit_place.py from inside the zip, or with
                                        "remove" first, seat_setup.py remove (the kit is then not put in place)
    .claude-plugin/marketplace.json     lists the one plugin, ml-seat-kit, at ./plugins/ml-seat-kit
    plugins/ml-seat-kit/...             the plugin folder, verbatim (no caches)
    Assistant.cmd, Assistant.ps1        the launchers, with KIT_SOURCE filled in when --source is given

Bump the version in plugin.json (and the company marketplace entry) for EVERY zip you hand out: an installed copy
with the same version is not refreshed. The zip is checked before it is written, or nothing is written:
- the kit's whole test suite must pass, run as a release build (SEAT_KIT_RELEASE_BUILD=1: the build-only tests of
  the kit's own runner run, and every one of them must have run) with the second full run under a short TEMP forced;
- every test, every set-up, tear-down, finding and end-of-run step, the inner short-TEMP run (against its own
  total, INNER_TOTAL) and the whole run (against TOTAL_SECONDS) must take at most a quarter of its limit on the build
  PC (MARGIN, 0.1.7). That is room for a PC whose CPU is four times slower; it does not model a fixed delay added to
  every process start (security software checking each start), which adds the same seconds to a short test as to a
  long one. The per-test limits are sized for that separately: each test's limit must be at least the sum of the
  subprocess time limits it used in this build (measured by the runner, WAITS lines);
- every file must pass the kit's privacy scan (tests/test_privacy.py).
"""
import argparse
import io
import json
import os
import pathlib
import sys
import zipfile

PLUGIN = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIX = (".pyc", ".pyo", ".tmp")
SKIP_NAMES = {"private_words.txt"}   # the private deny list (tests/test_privacy.py): never in a zip
PLACEHOLDER = b"PUT-KIT-ZIP-ADDRESS-HERE"
ZIP_MAIN = b'''"""Run by the assistant launcher: python -I <this zip> --kit-home <folder> puts this kit in place.
python -I <this zip> remove ... runs remove straight from the zip and never puts the kit in place (0.1.5)."""
import os
import sys

sys.path.insert(0, os.path.join(sys.argv[0], "plugins", "ml-seat-kit", "scripts"))
if sys.argv[1:2] == ["remove"]:
    import seat_setup  # noqa: E402
    raise SystemExit(seat_setup.main(sys.argv[1:]))
import kit_place  # noqa: E402

raise SystemExit(kit_place.main(sys.argv[1:] + ["--zip", os.path.abspath(sys.argv[0])]))
'''


def plugin_files():
    for p in sorted(PLUGIN.rglob("*")):
        if (p.is_file() and not (set(p.relative_to(PLUGIN).parts) & SKIP_DIRS)
                and not p.name.endswith(SKIP_SUFFIX)
                and p.name not in SKIP_NAMES):
            yield p


def marketplace(version):
    return {
        "name": "seat-kit",
        "owner": {"name": "Seat kit maintainers"},
        "description": "One plugin: the assistant seat kit, served from a local folder so no git is needed.",
        "plugins": [{
            "name": "ml-seat-kit",
            "source": "./plugins/ml-seat-kit",
            "version": version,
            "category": "productivity",
            "description": "A drafts-only personal email assistant seat for one person.",
        }],
    }


def crlf(data):
    """Batch files with bare LF line ends can break goto labels; whatever the checkout did, ship CRLF."""
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def stamp(data, source):
    data = crlf(data)
    if not source:
        return data
    if PLACEHOLDER not in data:
        raise SystemExit("launcher has no %s placeholder to fill" % PLACEHOLDER.decode())
    return data.replace(PLACEHOLDER, source.encode("utf-8"))


NO_PRIVATE_LIST = " ".join((
    "REFUSED: this is a release build (--source is set) and the private name list is missing",
    "(tests/private_words.txt). Without it the zip is only checked for generic things (paths,",
    "addresses), not for the names of real people, seats or the company.",
    "Put the list in place and run again. Only if you have checked the kit for names by hand,",
    "run again with --without-private-list."))


def require_private_list(words, source, without=False):
    """A release build (one with a download address) needs the private name list, unless the person
    cutting it says, in so many words, that they are going without. Never a silent skip."""
    if source and not words and not without:
        raise SystemExit(NO_PRIVATE_LIST)
    if source and not words:
        print("WARNING: release build WITHOUT the private name list; names were not scanned for.")


def kit_need_lines(version):
    """The line in each launcher that names the oldest kit self test and adopt may run: the kit's own version."""
    return {"Assistant.cmd": ('set "KIT_NEED=%s"' % version).encode("ascii"),
            "Assistant.ps1": ("$KitNeed = '%s'" % version).encode("ascii")}


def require_kit_need(version):
    """Refuse a zip whose launchers would accept an older kit than the one they ship with."""
    for name, line in kit_need_lines(version).items():
        if line not in (PLUGIN / "launcher" / name).read_bytes():
            raise SystemExit("REFUSED: launcher/%s does not say %s (it must name this kit's version, %s)"
                             % (name, line.decode("ascii"), version))


def run_tests():
    """The kit's whole suite in a fresh python -I, exactly as the self tests run it (seat_setup.run_suite), with
    progress lines, and with RELEASE_SUITE set: the build-only tests of the kit's own runner run (and the runner
    measures each test's subprocess time limits), and the second full run under a short-form TEMP
    (tests/test_short_paths.py), which runs only where TEMP really is short form, is forced (0.1.7)."""
    if str(PLUGIN / "scripts") not in sys.path:
        sys.path.insert(0, str(PLUGIN / "scripts"))
    import seat_setup   # noqa: E402
    return seat_setup.run_suite(None, progress=seat_setup.say_progress, env=dict(os.environ, **RELEASE_SUITE))


FORCE_SHORT_TEMP_RUN = {"SEAT_KIT_FORCE_SHORT_TEMP_RUN": "1"}
RELEASE_SUITE = dict(FORCE_SHORT_TEMP_RUN, SEAT_KIT_RELEASE_BUILD="1")
INNER_TEST = "tests.test_short_paths.WholeSuiteUnderShortTemp.test_the_whole_suite_passes_with_a_short_form_temp"


def _seat_setup():
    if str(PLUGIN / "scripts") not in sys.path:
        sys.path.insert(0, str(PLUGIN / "scripts"))
    import seat_setup   # noqa: E402
    return seat_setup


def inner_total():
    """INNER_TOTAL from tests/test_short_paths.py (the inner short-TEMP run's own total), read from its source."""
    import ast
    tree = ast.parse((PLUGIN / "tests" / "test_short_paths.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets] == ["INNER_TOTAL"]:
            return int(ast.literal_eval(node.value))
    raise SystemExit("REFUSED: tests/test_short_paths.py names no INNER_TOTAL")


def require_build_only(res):
    """Refuse the build unless the build-only tests ran, every one of them: the runner names each it found
    (BUILDONLY lines); each must have a DONE line that is not "skipped". None found, or any left out here (NOTRUN
    lines), means the release switch did not reach the runner."""
    found = res.get("build_only") or []
    ran = {tid for tid, _, outcome in res.get("times") or [] if outcome != "skipped"}
    done = [tid for tid in found if tid in ran]
    if not found or res.get("not_run") or len(done) < len(found):
        missing = [tid for tid in found if tid not in ran]
        raise SystemExit("REFUSED: the build-only tests of the kit's own runner did not all run (%d found, %d ran, "
                         "%d left out as not run here), so nothing was written. They run only with "
                         "SEAT_KIT_RELEASE_BUILD=1, which this build sets for its suite run.%s" % (
                             len(found), len(done), len(res.get("not_run") or []),
                             "\n  did not run: " + "\n  did not run: ".join(missing) if missing else ""))
    return "%d build-only tests ran, all of them" % len(done)


def require_waits(res):
    """Refuse the build when any test (or set-up and tear-down step) used subprocess time limits that add up to more
    than its own limit: on a slow PC its limit would stop it while its own programs were still within theirs. The
    sums come from the runner's WAITS lines, measured in this run; there must be one for every test that ran."""
    waits = res.get("waits") or []
    tests = {w[0] for w in waits}
    unmeasured = [tid for tid, _, _ in res.get("times") or [] if tid not in tests]
    if not waits or unmeasured:
        raise SystemExit("REFUSED: the runner did not measure every test's subprocess time limits (%d tests without "
                         "a WAITS line), so the limits cannot be checked; nothing was written." % len(unmeasured))
    over = [w for w in waits if w[1] > w[2]]
    if over:
        raise SystemExit("REFUSED: %d %s used subprocess time limits adding up to more than %s own limit; nothing was "
                         "written. Give the test selftest_seconds of at least that sum:\n%s" % (
                             len(over), "step" if len(over) == 1 else "steps", "its" if len(over) == 1 else "their",
                             "\n".join("  %d s of time limits under a %d s limit: %s" % (s, lim, what)
                                       for what, s, lim, _ in over)))
    what, s, lim, _ = max(waits, key=lambda w: (w[1] / float(w[2] or 1), w[1]))
    seat_setup = _seat_setup()
    own = ["  %5d s of time limits under its %d s limit: %s" % (s2, lim2, what2)
           for what2, s2, lim2, _ in sorted(waits, key=lambda w: -w[2]) if lim2 > seat_setup.PER_TEST_SECONDS]
    return ("every test's subprocess time limits fit in its own limit (%d measured; closest: %d s of %d s, %s)%s"
            % (len(waits), s, lim, what, "".join("\n" + ln for ln in own)))


def require_passing(res):
    """Refuse the build unless every test passed. The names come from unittest's own FAIL and ERROR lines."""
    if res.get("ok"):
        return "%s tests ran, all passed (%d skipped)" % (res.get("ran"), res.get("skipped", 0))
    names = []
    for _, name, _ in res.get("failed", []):
        if name not in names:
            names.append(name)
    tail = "\n".join((res.get("output") or "").splitlines()[-40:])
    raise SystemExit("REFUSED: the kit's tests did not all pass, so nothing was written (%s%s).\n%s" % (
        res.get("summary") or "no result", ": " + ", ".join(names) if names else "", tail))


MARGIN = 4   # every step must finish on the build PC within a quarter of its limit (0.1.7; CPU time only)
PHASE_WORDS = {"find": "finding the tests", "setup": "set-up", "teardown": "tear-down", "end": "end of the run",
               "after": "between tests"}


def require_margin(res, margin=MARGIN, inner=None):
    """Refuse the build when, measured on the build PC, any of these took more than 1/margin of its limit:
    - a test (from its DONE line), against the limit it ran under (its START line's: PER_TEST_SECONDS, or what it
      asked for with selftest_seconds);
    - a step that is not a test (finding the tests, a set-up, a tear-down, the time between tests, the end of the
      run after the last test), against the limit the runner's lines gave it;
    - the inner short-TEMP run (the time of INNER_TEST, which must have run), against INNER_TOTAL;
    - the whole run, against TOTAL_SECONDS.
    A slow PC must not be stopped by a limit set for this one."""
    seat_setup = _seat_setup()
    limits = res.get("limits") or {}
    rows = [(t, limits.get(tid, seat_setup.PER_TEST_SECONDS), tid) for tid, t, _ in res.get("times") or []]
    rows += [(t, lim, "%s%s" % (PHASE_WORDS.get(kind, kind), " (%s)" % name if name else ""))
             for kind, name, t, lim in res.get("phases") or [] if kind in PHASE_WORDS]
    if res.get("wall") is not None:
        rows.append((res["wall"], seat_setup.TOTAL_SECONDS, "the whole run"))
    if inner is not False:
        took = [t for tid, t, outcome in res.get("times") or [] if tid == INNER_TEST and outcome == "ok"]
        if not took:
            raise SystemExit("REFUSED: the second full run under a short-form TEMP (%s) did not run and pass, so "
                             "nothing was written." % INNER_TEST)
        rows.append((took[0], inner or inner_total(), "the inner short-TEMP run, against INNER_TOTAL"))
    tight = [(t, lim, what) for t, lim, what in rows if t * margin > lim]
    if tight:
        raise SystemExit("REFUSED: %d %s took more than a quarter of %s limit on this PC, so a PC %d times slower "
                         "would be stopped; nothing was written. Raise the limit (to at least %d times its time "
                         "here) or make it faster:\n%s" % (
                             len(tight), "step" if len(tight) == 1 else "steps",
                             "its" if len(tight) == 1 else "their", margin, margin,
                             "\n".join("  %.1f s of a %d s limit: %s" % (t, lim, what) for t, lim, what in tight)))
    if not rows:
        return "no times to check against their limits"
    t, lim, what = max(rows, key=lambda r: r[0] / float(r[1] or 1))
    return "every step finished within a quarter of its limit (closest: %.1f s of %d s, %s)" % (t, lim, what)


def build(out, source=None, without_private_list=False, tests=run_tests):
    sys.path.insert(0, str(PLUGIN / "tests"))
    import test_privacy   # noqa: E402  (the same scan the tests run)
    require_private_list(test_privacy.private_words(), source, without_private_list)
    print("running the kit's tests first ...")
    res = tests()
    print(require_passing(res))
    print((res.get("timing") or "").strip("\n"))
    print(require_build_only(res))
    print(require_waits(res))
    print(require_margin(res))
    version = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    entries = {".claude-plugin/marketplace.json": (json.dumps(marketplace(version), indent=2) + "\n").encode("utf-8"),
               "__main__.py": ZIP_MAIN}
    for p in plugin_files():
        entries["plugins/ml-seat-kit/" + p.relative_to(PLUGIN).as_posix()] = p.read_bytes()
    require_kit_need(version)
    for name in ("Assistant.cmd", "Assistant.ps1"):
        entries[name] = stamp((PLUGIN / "launcher" / name).read_bytes(), source)
    hits = []
    for name, data in entries.items():
        # the download address is the one thing allowed to name its host: it is public by definition
        seen = data.replace(source.encode("utf-8"), PLACEHOLDER) if source else data
        hits += ["%s: %s" % (name, h) for h in test_privacy.scan_text(seen.decode("utf-8", "replace"))]
    if hits:
        raise SystemExit("privacy scan failed, nothing written:\n  " + "\n  ".join(hits[:20]))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(entries):
            z.writestr(name, entries[name])
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())
    return version, len(entries)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="make_kit.py")
    ap.add_argument("--out", required=True)
    ap.add_argument("--source", default=None, help="the https address the zip will be served from")
    ap.add_argument("--without-private-list", action="store_true",
                    help="cut a release with no private name list present (you checked for names by hand)")
    args = ap.parse_args(argv)
    version, n = build(args.out, args.source, args.without_private_list)
    print("kit %s: %d files -> %s" % (version, n, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
