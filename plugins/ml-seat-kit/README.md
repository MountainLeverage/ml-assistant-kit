# ml-seat-kit: a drafts-only email assistant seat

For the person who maintains the kit. The person who USES it never reads this: they double-click one file.

A seat is one person's assistant: a Claude Code session in a folder of its own that works their Gmail one thread
at a time, keeps a list of what needs them, and checks again every 30 minutes (60 at night and on weekends) while
the window is open. Version 0.1 is drafts only: it never sends, replies, forwards or answers an invite.

## Install

The person never installs it by hand: `Assistant.cmd` does it from a local folder (no git). From a marketplace
that lists it, the same plugin installs with:

```
/plugin install ml-seat-kit --marketplace <owner>/<repo>
```

## Already have an assistant folder?

Adopt mode puts the kit onto a folder that already holds an assistant, in place. It keeps the folder, its name,
its `CLAUDE.md` (one fenced hard-limits block is appended) and its own launcher (a file named `assistant.cmd` or
`assistant.ps1` is never created, replaced, renamed or deleted). It installs nothing, makes no shortcut and opens
no session. First see what it would do (this writes nothing in the folder), then do it:

```
"%USERPROFILE%\Downloads\Assistant.cmd" adopt --check "C:\Example Folder"
"%USERPROFILE%\Downloads\Assistant.cmd" adopt "C:\Example Folder"
```

Add `--name "<name>"` to skip the one question. Before any write it copies every file it changes into
`<folder>\.seat\backup\<date-time>\`, with a list of the files it creates. To take it back:

```
"%USERPROFILE%\Downloads\Assistant.cmd" adopt --undo "C:\Example Folder"
```

Undo puts back the changed files from the backup and removes only the files adopt made that are still exactly as
adopt wrote them; anything changed since is left in place and named. Afterward the person starts the assistant
the same way as before. The send gate applies however the session is started, because it is in the folder's own
`.claude\settings.json` with the full path of Python. Adopt needs a Python from python.org: if the only one found
is the Microsoft Store one, or none, it stops and installs nothing.

## Taking it back

A first install (the launcher made the assistant's folder, the shortcut and the kit folder) is taken back with
remove. First see exactly what it would take away and what it would leave (this changes nothing at all: no folder,
no file, no shortcut, no call to Claude Code), then do it, from the copy of `Assistant.cmd` that was downloaded:

```
"%USERPROFILE%\Downloads\Assistant.cmd" remove --check
"%USERPROFILE%\Downloads\Assistant.cmd" remove
```

In this order, each on a plain line saying done, not there, or could not (with what Windows said):

1. The plugin: `claude plugin uninstall ml-seat-kit@seat-kit`, then `claude plugin marketplace remove seat-kit`.
   When Claude Code is not found, it says so, prints those two lines for later, and carries on.
2. The desktop shortcut the kit made, recognized by its target (the kit's own `Assistant.cmd` in the kit folder),
   never by its name. A shortcut that now points anywhere else is left and named.
3. The assistant's folder under `%USERPROFILE%\Assistant`. The person's notes are in it, so it is MOVED beside
   itself to `<name>.removed-<yyyymmdd-hhmmss>`, never deleted; deleting it later is theirs to do. With
   `--delete-my-notes` it is deleted instead, after the person types the folder's name (a wrong name changes
   nothing; never with `--check`). A folder moved aside this way is never picked up as an assistant again.
4. The kit folder `%LOCALAPPDATA%\seat-kit`: the kit's own files and folders in it, then the folder itself. A
   file there that is not the kit's (anything but its known names, or an `Assistant.cmd` that is not a kit
   launcher) is left, and so is the folder, and both are named.

Everything is checked before anything changes, and remove stops with nothing changed when: a file in the
assistant's folder or the kit folder is open, or a window sits in one of them (a running assistant: close its
window); the launcher running is the copy kept in the kit folder (a running file cannot take away its own folder:
run the downloaded copy, or copy that one out to `%TEMP%` and run the copy; the stop line prints both lines); the
assistant's folder holds a file named `assistant.cmd` or `assistant.ps1`; Claude Code does not answer when asked
what it has; or the kit folder or the assistants' folder it was given is a drive root, the user folder, the
Desktop or Documents folder itself, Downloads or anything in it, the Windows folder, or a kit folder with no kit in
it. A step that fails after others ran stops there, says what was already taken back and what is still there, and
running remove again finishes the job ("not there" is a normal line). Exit code 0 when done or nothing was there,
1 on a stop.

Remove never touches an adopted folder, Claude Code itself, Python, the person's Claude sign-in and connectors, or
anything in Downloads. A folder named on the remove line is refused (an adopted one with a pointer to
`adopt --undo`). Adopted folders are found under `%USERPROFILE%\Assistant` and in `<kit home>\adopted.txt`, which
adopt writes since 0.1.5. While one exists, remove takes only the first install's own things (its folder, its
shortcut, the kit's copy of `Assistant.cmd` and `last-seat.txt`) and keeps the plugin and the rest of the kit
folder, because that folder's send gate runs `<kit home>\send_gate.py`; it says so on a line naming the folder.
A folder adopted with 0.1.4 or older outside `%USERPROFILE%\Assistant` is not known to the kit: `remove --check`
says to take it back first with `adopt --undo`. If the kit folder goes anyway, that folder's gate fails closed
(every gated tool is refused).

An adopted folder is taken back with adopt's own undo (see above):

```
"%USERPROFILE%\Downloads\Assistant.cmd" adopt --undo "C:\Example Folder"
```

Remove never writes into the kit folder. It runs the kit already there when that is the launcher's `KIT_NEED`
(0.1.7 since 0.1.7) or newer; otherwise it downloads the newest kit to `%TEMP%` and runs it straight from the zip
(`python -I -B <zip> remove ...`), never putting it in place, and deletes the download after. If it cannot get
that kit and the kit on the PC is older, it stops with one line and prints the five steps to do by hand, exit
code 1. That line says the kit on the PC is older than the version this launcher needs for remove (since 0.1.6;
before, it said the older kit "has no remove", which was untrue of a 0.1.5 kit: 0.1.5 has remove, but a 0.1.6
launcher offline does not run it).

## Self test only

To see whether the kit works on a PC without setting anything up, run:

```
"%USERPROFILE%\Downloads\Assistant.cmd" selftest
```

It finds Claude Code and Python and gets the newest kit (steps 1 to 3; if the newest kit cannot be put in place it stops there and runs no older kit; it installs nothing, and a Microsoft Store Python
or no Python stops it with one plain line), then runs the kit's self tests and prints the kit version, the Python
version and path, the Windows version, how many tests ran, passed, failed and were skipped, which folders the
tests' before-and-after comparisons ignored (see 0.1.6 below), each test that did not pass with its one-line
reason, and where the full output is. It makes no plugin call, creates no assistant
folder, touches no folder of the person's, makes no shortcut and opens no session. It ends 0 when every test
passed and 1 otherwise. `Assistant.ps1 selftest` does the same.

Every self test run (this one, the first start and adopt) keeps unittest's full output in
`%LOCALAPPDATA%\seat-kit\selftest-last.txt`, overwritten each run. When the self tests stop a start or an adopt,
the stop line names the tests that did not pass (the first three, then "and N more") and that file. Since 0.1.7 the
file also has each test's seconds and, at its end, the ten slowest tests and the total by test file (see 0.1.7
below).

## What changed in 0.1.7

0.1.7 changes no behavior of setup, adopt or remove. It changes how the self tests run, after kit 0.1.6's self
test on a colleague's PC (Python 3.14.5, security software checking every PowerShell and cmd start) ran into its
single 600 second limit and said only "The self tests did not finish within 600 seconds.", after ten silent minutes:

- **The self test shows progress.** After the "Step 4 of 6" line (self test), during adopt's self tests and during
  a first start's step 5, a plain line about every 15 seconds: `   still running: test 120 of 224, 3 min 10 s so
  far`. A test running longer than 30 seconds is named: `   still running: test 121 of 224
  (WholeSuiteUnderShortTemp.test_the_whole_suite_passes_with_a_short_form_temp) has taken 45 s; 4 min 2 s so far`.
  A run that ends within 15 seconds prints none. When the run passes ten minutes, the next progress line is followed,
  once per run, by `   this PC is running the checks slowly, which is expected; if one check is stuck, the run stops
  by itself and names it`. When progress
  lines were printed, the result gets its own line: `Step 4 of 6: all passed` (and a first start's `Step 5 of 6:
  done (...)`). No line is ever rewritten in place.
- **It records how long each test took.** The tests run through the kit's own runner
  (`scripts/selftest_runner.py`), which prints what `unittest discover -v` prints plus one line before and one
  after every test (`SEAT-KIT-TEST-DONE 37/224 ok 2.41s tests.test_remove...`). `selftest-last.txt` keeps all of it
  and ends with "Slowest tests" (the ten slowest) and "Total by test file".
- **It stops a single stuck test and names it.** The limits are there to catch a hang, not to judge speed. Every
  step of the run has a limit of its own: each test may run 300 seconds, and a test that starts launchers,
  PowerShell or other programs asks for the sum of its own programs' time limits (`selftest_seconds = N` on that
  test's method, from 360 seconds up to 2280 seconds for the test that runs both launchers three times each; the
  release build measures each test's sum in its own run and refuses a build in which any is over the test's
  limit). Between two classes, the tear-down of the one before and the set-up of the next run back to back and
  count as one step, under the larger of the two classes' limits (300 seconds for every class today); the tear-down
  after the last test is a step of its own, and so is the end of the run after it (300 seconds each). Finding and
  loading the tests may take 300 seconds. On top of these the whole run has a backstop of three hours (10800
  seconds), for a run that keeps moving and never ends; a real hang is always stopped first by the limit of the
  step it is in. A run that passes a limit is stopped, and the console says which test: `Tests:        no count from
  unittest (The self tests were stopped: tests.test_x.Klass.test_y ran longer than 300 seconds (test 37 of 224). 36
  finished before it.)`, or, when unittest had already printed its counts, a line `Stopped:      The self tests
  were stopped: ...` after the Tests line. Tests that had already failed before the stop are listed under "Did not
  pass". The output so far is kept in the file, the Ignored line is still printed, and the exit codes are as before
  (self test 1; adopt stops with "nothing was changed"; a first start stops at step 5). `make_kit.py` refuses a
  build in which any test, any set-up, tear-down, finding or end-of-run step, the inner short-TEMP run (against its
  own total) or the whole run (against the three hours) took more than a quarter of its limit on the build PC. That
  is room for a CPU four times slower; a fixed delay on every program start (security software) is what the
  per-test sums above are for.
- **The checks of the kit's own test runner run when the kit is built, not on your PC.** About twenty tests check
  the runner and its limits themselves, with real waits, hung tests and programs left running on purpose. They
  test the kit, not the PC, so a colleague's self test does not load them, and its summary says so on a line of its
  own: `Not run here: 19 checks of the kit's own test runner, which are run when the kit is built`. They are not
  counted as run or as skipped, and `selftest-last.txt` names each once (`SEAT-KIT-TEST-NOTRUN` lines).
  `make_kit.py` runs them (`SEAT_KIT_RELEASE_BUILD=1`) and refuses a build in which any of them did not run.
- **What the tests started is ended with the run, where Windows allows it.** The runner puts itself in a Windows
  job object; when that worked, the file's header says `Job object: armed (...)`, and when the runner's own
  process ends (by itself, or stopped by the self test), Windows ends every program the tests started and left
  running, and nothing else. A program that broke away from the job is not ended. Where the job could not be made,
  the header says `Job object: not armed (why)`, such programs keep running, and the self test still finishes: it
  is done when the runner itself has ended (a few seconds after, for what is left in its output), never when the
  last leftover program lets go of the output. `SEAT_KIT_TEST_NO_JOB=1` switches the job object off; it is for the
  kit's own tests only.
- **The second full run under a short-form TEMP runs only where it can matter.** One test ran the whole suite a
  second time with TEMP in 8.3 short form; on that PC the limit was hit inside it. It now runs only when the PC's
  own TEMP or TMP really is a short-form spelling (it differs from its long spelling other than in letter case or
  a trailing backslash), and skips with that reason everywhere else; the short-spelling code stays covered by the
  focused tests in `tests/test_short_paths.py`. `make_kit.py` forces it for every build
  (`SEAT_KIT_FORCE_SHORT_TEMP_RUN=1`).
- **One test no longer fails on a PC that really has a scanner.** The test that simulates a script scanner planted
  a log under the real product's folder name, `AppData\Local\Malwarebytes`; on a PC with Malwarebytes the real
  scanner wrote that folder by itself, so the check that a bare PowerShell start leaves no such folder could not
  hold. The simulation now uses an invented name no software makes (`SeatKitPretendScanner`), and the same tests
  run again with a real-looking scanner folder written on every PowerShell start.

Python 3.14 has been run only on that colleague's PC; this kit is developed and tested on 3.12.

## What changed in 0.1.6

0.1.6 changes no behavior of setup, adopt or remove, with one exception: the wording of the offline remove stop
when the kit on the PC is older than the launcher needs (above). Otherwise it changes the self tests and the self
test summary only, after kit 0.1.5's self test failed on a colleague's PC (198 ran, 12 failures) although the kit
did nothing wrong:

- **Security software should no longer fail the self test.** Several tests start PowerShell under a made-up
  profile and compare every file in it before and after. On that PC Malwarebytes, inside such a PowerShell, wrote
  `AppData\Local\Malwarebytes\Logs\MBAMSI.log` into the made-up profile, so 10 tests saw a folder they did not
  expect. Each test run now first probes a scratch profile with three PowerShell starts shaped like the kit's own:
  a bare start, the kit's own read-only desktop script (as remove reads shortcuts) on an empty folder, and a
  `-File` script the way the `.ps1` launcher is started. It notes which top-level folders appear under
  `AppData\Local` and `AppData\Roaming`, right after the starts and again a second later, and the comparisons leave
  out exactly those folders. On the build PC that is `Microsoft` (PowerShell's own caches); on a PC with
  Malwarebytes `Malwarebytes` is expected as well, but this has not been run on a PC with Malwarebytes. Never the
  kit's own `seat-kit`, `Programs` or `npm`, and never a file inside an ignored folder that carries one of the
  kit's own names. Anything else, anywhere in the profile, still fails the test.
- **The summary names what it ignored, every run:** a line `Ignored:      AppData\Local\Microsoft (folders other
  programs make whenever PowerShell starts)` (or `Ignored:      nothing`), and, when something other than
  PowerShell's own folder was found, for example `Also seen and ignored: Malwarebytes`.
- **The Tests line adds up: ran = passed + failed + skipped.** "failed" and "skipped" count tests, not sub-cases;
  when sub-cases make unittest's own count larger it is added in words: `198 ran, 189 passed, 9 failed (12
  failures counting sub-cases), 0 skipped`, or `3 ran, 1 passed, 0 failed, 2 skipped (3 skips counting
  sub-cases)`. An error in a class or module set up or tear down is not a test: it is not taken off "passed" and
  is named after the counts, for example `10 ran, 10 passed, 0 failed, 0 skipped; 1 error outside any test
  (Klass.tearDownClass)`, and the self test still fails. If the counts cannot add up, the line shows unittest's
  own counts and says so.
- **Tests no longer hand PowerShell or the launchers the person's real profile variables** (`USERPROFILE`, `HOME`,
  `LOCALAPPDATA`, `APPDATA`); before, the remove tests faked only `USERPROFILE`. The tests still use the person's
  real `TEMP` (normally inside the profile), and Windows can find some folders from the registry rather than these
  variables, so this does not make it impossible for a test to touch the real profile.
- One launcher test (adopt never installs Claude Code, run with `SystemRoot` pointed at an empty folder) printed
  nothing at all on that PC, likely stopped by security software. When the launcher prints nothing, the test now
  runs it again with the real `SystemRoot`: it skips only if that run printed "Step 1 of 6", and fails, with both
  exit codes, if that run printed nothing either. The branch order is also checked by reading both launchers on
  every PC.

Python 3.14 has been run only on that colleague's PC (3.14.5); this kit is developed and tested on 3.12.

## Short and long spellings of a folder

Windows can give the kit a folder in its 8.3 short form (for a long user name with a dot, TEMP often looks like
`...\ABCDEF~1.XYZ\AppData\Local\Temp`) or in its long form. The kit never compares two spellings as text: it asks
the file system (`samefile`, `realpath`). It prints the long form, the one Explorer shows. The launchers print
step 1's Claude Code path as Windows gave it to them (cmd has no simple way to expand a short name).

## What each file is

| File | What it does |
|---|---|
| `launcher/Assistant.cmd` | The one file the person gets. Six steps, each one plain line: Claude Code, Python, the kit, the plugin, the seat folder, open. Running it again is how they start each day. Step 1 uses a Claude Code that is already there (the native `%USERPROFILE%\.local\bin\claude.exe`, then npm's `%APPDATA%\npm\claude.cmd`, then `where claude`) and downloads one only when none is found. |
| `launcher/Assistant.ps1` | The same six steps and lines, for pasting into PowerShell when a downloaded `.cmd` is blocked. |
| `scripts/seat_setup.py` | Steps 4 and 5: registers the kit folder as a local marketplace, installs or updates the plugin, makes or checks the seat folder, asks three optional questions on the first run, runs the self tests (once per kit version) and a dry loop check, makes one desktop shortcut. Also adopt mode (`adopt`, `adopt --check`, `adopt --undo`) for a folder the person already has. |
| `scripts/seat_remove.py` | Remove mode (`remove`, `remove --check`, `--delete-my-notes`), run through `seat_setup.py remove`: takes back a first install, never an adopted folder. |
| `scripts/kit_place.py` | Step 3 since 0.1.4, run from inside the downloaded zip: puts the kit into `kits\<version>` and switches `kit.txt`, then removes older folders best effort. Never renames or deletes anything in use. |
| `scripts/gate_shim.py` | Copied to `<kit home>\send_gate.py`, the gate's fixed address: runs the gate of the kit in `kit.txt`, refuses (exit 2) when it cannot. |
| `scripts/send_gate.py` | The send gate. A PreToolUse hook on every MCP tool named with send, reply, forward, respond or RSVP. Inside a seat folder it denies (exit 2, plain reason); outside one it does nothing. Fails closed inside a seat. Since 0.1.1 it is wired in the seat's `.claude/settings.json` (by `seat_setup.py`), not in `hooks/hooks.json` (now empty): exec form with the absolute Python the launcher found, so it runs in a session started without the launcher, and `onFailure: "block"` (Claude Code 2.1.295 or later) so a vanished Python blocks the call. |
| `scripts/tick.py` | The loop: `check` (one loop per seat), `begin` (STOP, the search epoch, due promises, backup due), `end`, `sleep` (the background sleeper; refuses a second arm). |
| `scripts/wakes.py` | Promises for later: one dated row each, fired by the sleeper, closed with a line. |
| `scripts/ledger.py` | One line per thread in `state/ledger.jsonl`. |
| `scripts/backup.py` | Zips the seat's own state into a folder the person picks (a Drive folder). |
| `scripts/read_thread.py`, `scripts/summarize_search.py` | Compact views of saved Gmail results. |
| `scripts/seat_common.py` | Finds the seat folder: `--seat`, else `CLAUDE_PROJECT_DIR`, else the current folder, walked up to the marker `.seat/seat.json`. |
| `skills/seat-start/SKILL.md` | The start skill: open items, Brain inbox through the person's own connector, STOP, start the loop, and the first-run walk through what only the person can do. |
| `skills/seat-start/loop.md` | What one tick does. Ships with the kit (no blanks to fill), so a kit update improves it. |
| `templates/` | Filled into the seat once: `CLAUDE.md` (names and leadership from the three questions), and empty `open-items.md`, `corrections.md`, `senders.md`, `decisions.md`. |
| `tests/` | Standard-library `unittest`; also runs under pytest. `test_privacy.py` fails on any private word in any kit file. |
| `tools/make_kit.py` | Builds the zip the launcher downloads. |

## Where things live on the person's laptop

- Kit: `%LOCALAPPDATA%\seat-kit\` (the kit home), laid out since 0.1.4 as:
  - `kits\<version>\`: each kit version, unpacked into a folder of its own that did not exist a moment before,
    and never renamed or changed after. The one in use is registered as the local marketplace `seat-kit`.
  - `kit.txt`: one line, the name of the folder under `kits\` that is in use.
  - `send_gate.py`: the send gate's fixed address. A seat's settings name this file, never a file inside a kit
    version; it runs the send gate of the kit named in `kit.txt`, and refuses the tool (exit 2) when `kit.txt`
    is missing, unreadable or names a folder that is not there. A broken gate never lets a send through.
  - `market.txt`: the kit folder last registered with Claude Code. When the kit in use moves to a new version
    folder, the next start or adopt removes the registration and adds the new folder.
  - `current\`: the folder kit 0.1.3 and older used. 0.1.4 never touches it: a seat adopted with an older kit
    has its send gate there, and it keeps working. A normal start with no newer kit and no `kit.txt` runs it.
  - `python.txt` (the interpreter the launcher found), `last-seat.txt`, `selftest-last.txt` (the last self test
    output) and a copy of the launcher.
  - `adopted.txt` (since 0.1.5): each folder adopt put the kit onto, one a line, so remove leaves the send gate
    they use.
- Seat: `%USERPROFILE%\Assistant\<assistant name>`: `.seat/seat.json` (the marker; names, backup folder, label
  ids), `state/`, `CLAUDE.md`, `.claude/settings.json` (the send gate hook, plus permission DENY rules for the known
  send tools as a second layer behind it; never an allow rule).
- Claude config: the person's normal one, changed only through `claude plugin marketplace add|update|remove` and
  `claude plugin install|update`.

The code runs from the plugin folder, so a kit update never touches the person's files.

### What "in use" means, and why step 3 never renames

Windows refuses to rename or delete a folder while any program has its working folder inside it or a file in it
open (an Explorer-style folder watch does not count). A Command Prompt sitting in the folder, a Python left
running by an earlier self test (its working folder is inside the kit), an editor, or a security scanner
reading a file are all enough. Kit 0.1.3 and older renamed `current` to swap in a new kit, and unpacked with
`tar.exe`; a held folder, or a security product refusing `tar.exe`, made step 3 print a bare "Access is denied."
and fall back to the older kit.

Since 0.1.4 step 3 renames, overwrites and deletes nothing that may be in use, and uses no `tar.exe`:

1. The download goes to a new file name (`dl-<number>.zip`) each run.
2. Python runs the zip itself (`python -I <zip> --kit-home <kit home>`; `scripts/kit_place.py`), which unpacks it
   with Python's own zipfile into `kits\<version>` (or `<version>-2` and so on when that name is taken by
   something else), then switches `kit.txt`. A version already unpacked from the very same zip is used as it is.
   A refused step is tried again three times over three seconds, each unpack into a fresh folder.
3. Older version folders and the leftovers of 0.1.3 and older (`incoming`, `previous`, `kit.zip`) are removed
   afterward, best effort and silently; one that is in use stays until a later run, and that is never an error.
4. When it cannot finish, step 3 prints "not done" and then, each on a line of its own, what could not be done,
   what Windows (or curl) said, and the kit folder.

Each launcher names the oldest kit it runs for self test, adopt and remove (`KIT_NEED` in the `.cmd`, `$KitNeed`
in the `.ps1`: the kit's own version, 0.1.7 for all three since 0.1.7; `make_kit.py` refuses a zip where they
differ). Every mode prints
`Kit in use: <version> (<folder>)`, read from the kit it is about to run, before running it. Self test and adopt
run only a kit just put in place and at least that version; otherwise they stop with one line, naming the kit
already on the PC, exit code 1, and run nothing. A normal start with no newer kit uses the kit it has (being
offline must not stop the assistant) and says so; a `kit.txt` naming a folder that is not there stops it.

## Cutting a kit zip

1. Bump `version` in `.claude-plugin/plugin.json` and in this plugin's entry in the marketplace list, and the same version in `KIT_NEED` (launcher/Assistant.cmd) and `$KitNeed` (launcher/Assistant.ps1). Every zip
   you hand out needs a new version, or an installed copy is not refreshed.
2. `python -I -m unittest discover -s tests -t .` from this folder.
3. `python -I tools/make_kit.py --out <folder>/seat-kit.zip --source <the https address it will be served from>`.
   It runs the whole test suite again first and writes nothing if any test fails.
   `--source` fills `KIT_SOURCE` in the launcher copies inside the zip; the copies in this repo keep the
   placeholder. The build refuses to write a zip that fails the privacy scan.
4. Put the zip at that address, and send the person `Assistant.cmd` with the same address in its `KIT_SOURCE`
   line (or the `Assistant.cmd` from inside the zip).

## Testing on a PC that already has a real Claude config

Never run a `claude plugin` command without a scratch `CLAUDE_CONFIG_DIR`. In a scratch folder:

```
set CLAUDE_CONFIG_DIR=<scratch>\config
set SEAT_KIT_SOURCE=<scratch>\seat-kit.zip
set SEAT_KIT_HOME=<scratch>\kit-home
set SEAT_HOME=<scratch>\seats
set SEAT_DESKTOP=<scratch>\desktop
set SEAT_KIT_DRY_OPEN=1
set SEAT_ANSWERS=Pat^|Juniper^|Lee Park
Assistant.cmd
```

`SEAT_KIT_DRY_OPEN=1` prints the final `claude` command instead of opening a session (a real start needs a
person's sign-in). `SEAT_KIT_SIMULATE_MISSING=claude,python,winget` makes those checks report "missing" and print
the install command they would run, without running it.

## Where the code came from

The scripts are copies of a working assistant seat's own files (its `tools/` folder), with the absolute paths,
the phone link, the peer rails and the outbound message bus taken out, git commits removed from the loop, and the
clock changed from one fixed zone to the laptop's own. That seat still runs its own copies. Until it moves onto
this kit, a fix made in one place must be carried to the other by hand.

## Not in this version

Sending with an approval (the gate is deny-all), the phone surface, the memory server, starting at Windows
sign-in, Windows auto sign-in, the open-items sidebar, a terminal color profile, reattaching to a backgrounded
session.

Calendar events are gated too, because an invitation is a send: an event made with guests is refused, and so is any
change, move or removal of an event (the gate cannot see who is already on it). An event with no guests, such as a
block on her own calendar, passes.

## The private name list

The privacy scan uses two lists. The generic one (paths from one PC, addresses, an internal host shape) is in
`tests/test_privacy.py` and ships with the kit. The other is a list of real names to keep out. It is not in any
repository, because a list of names to hide is itself those names. It lives only on the PC that cuts releases, as
`tests/private_words.txt` (one `label<TAB>regular expression` per line), which git ignores and `tools/make_kit.py`
never packs.

The cost is that the name scan runs only on that PC. So `make_kit.py` refuses a release build (one with `--source`)
when the list is missing, unless `--without-private-list` is given on purpose.
