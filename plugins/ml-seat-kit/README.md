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

## Self test only

To see whether the kit works on a PC without setting anything up, run:

```
"%USERPROFILE%\Downloads\Assistant.cmd" selftest
```

It finds Claude Code and Python and gets the kit (steps 1 to 3; it installs nothing, and a Microsoft Store Python
or no Python stops it with one plain line), then runs the kit's self tests and prints the kit version, the Python
version and path, the Windows version, how many tests ran, passed, failed and were skipped, each test that did
not pass with its one-line reason, and where the full output is. It makes no plugin call, creates no assistant
folder, touches no folder of the person's, makes no shortcut and opens no session. It ends 0 when every test
passed and 1 otherwise. `Assistant.ps1 selftest` does the same.

Every self test run (this one, the first start and adopt) keeps unittest's full output in
`%LOCALAPPDATA%\seat-kit\selftest-last.txt`, overwritten each run. When the self tests stop a start or an adopt,
the stop line names the tests that did not pass (the first three, then "and N more") and that file.

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

- Kit: `%LOCALAPPDATA%\seat-kit\current` (the expanded zip, registered as the local marketplace `seat-kit`),
  plus `python.txt` (the interpreter the launcher found), `last-seat.txt`, `selftest-last.txt` (the last self
  test output) and a copy of the launcher.
- Seat: `%USERPROFILE%\Assistant\<assistant name>`: `.seat/seat.json` (the marker; names, backup folder, label
  ids), `state/`, `CLAUDE.md`, `.claude/settings.json` (the send gate hook, plus permission DENY rules for the known
  send tools as a second layer behind it; never an allow rule).
- Claude config: the person's normal one, changed only through `claude plugin marketplace add|update` and
  `claude plugin install|update`.

The code runs from the plugin folder, so a kit update never touches the person's files.

## Cutting a kit zip

1. Bump `version` in `.claude-plugin/plugin.json` and in this plugin's entry in the marketplace list. Every zip
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
