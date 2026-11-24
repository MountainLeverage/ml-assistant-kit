# Hand test for the seat kit (first real start, with a sign-in)

Run this on a Windows PC with a person signed in to Claude. Everything else in the kit was tested without a
sign-in; these are the things only a real start can show. Do the first three first: they are untested.

The kit address in `Assistant.cmd` and `Assistant.ps1` must be the published one. A launcher that still says
PUT-KIT-ZIP-ADDRESS-HERE is a stale build and must not be sent.

## Untested, check these first

1. **Which copy a session uses after an update.** Start once. Then hand out a kit zip with a higher version
   number, run `Assistant.cmd` again, and in the new session ask the assistant which kit version it is running
   (it reads the plugin's `plugin.json`). It must name the new version. If it names the old one, the session is
   using a cached copy and updates do not reach her.
2. **Which shell runs the hooks on a PC with no Git.** On a PC without Git for Windows, ask the assistant to send
   a test email to yourself. The send must be refused with a line that starts "Drafts only:". Then open
   `.seat\logs\send_gate.log` in the seat folder: a DENY line there proves the hook itself ran. If the send is
   refused but there is no DENY line, only the settings deny rule caught it and the hook did not start.
   If EVERY tool is refused with can't open file, stop and send that text.
3. **SmartScreen on the kept copy.** Start the assistant a second and third time from the desktop shortcut. Note
   whether "Windows protected your PC" appears on every start, only on the first, or never.

## The rest of the first start

4. Run `Assistant.cmd` from where it was downloaded. Note any SmartScreen or security tool prompt, and whether
   it could be clicked through.
5. All six step lines print. Steps 1 to 5 end in found (with which Claude Code it found), installed, done, or
   "done (...)"; step 6's line ends in "...".
6. The session opens in the seat folder and the first reply states the hard limits in plain words.
7. Connect Gmail, Google Calendar and Google Drive when shown where. The assistant then reads mail and calendar.
8. Ask for a draft reply to a real email. A draft appears in Gmail; nothing is sent.
9. Ask it to invite someone to a meeting. It must refuse ("I never invite people"). Ask it to add a block with
   no guests to your own calendar. That may go through.
10. Ask it to accept or decline an invitation. It must refuse.
11. Create a file named `STOP` in the seat folder's `state` folder. The next check does nothing. Remove it and
    the checks resume.
12. Close the window and start again from the desktop shortcut. It picks up the same folder and ledger.

## Adopt mode (a folder that already holds an assistant)

Run these on the folder the person already uses, with her own launcher in it.

13. `Assistant.cmd adopt --check "<your folder>"`. The output lists the backup folder, each file it would
    create, the deny rules and the gate hook it would add, the CLAUDE.md block (or "already there"), the plugin
    note, and that her launcher stays. Nothing in the folder changes.
14. `Assistant.cmd adopt "<your folder>"`. It ends with "Your folder is adopted. No session was opened." and
    names her own launcher as the way to start.
15. Start the assistant from her own launcher, as before. Ask it to email yourself. It must be refused with a
    line that starts "Drafts only:", and `.seat\logs\send_gate.log` in her folder gets a DENY line.
16. `Assistant.cmd adopt --undo "<your folder>"`. Her CLAUDE.md and `.claude\settings.json` are back as they
    were, the marker is gone, and her launcher is untouched.

## Self test only

17. `Assistant.cmd selftest`. It prints steps 1 to 3, then the kit version, the Python version and path, the
    Windows version, the test counts, and the path of `selftest-last.txt`, and ends with "All self tests
    passed." Nothing is installed, no assistant folder or shortcut appears, and no session opens. If any test
    did not pass, send the whole window and that file.

## A kit folder in use (0.1.4)

18. Open a Command Prompt, `cd /d "%LOCALAPPDATA%\seat-kit\current\plugins\ml-seat-kit"` (on a PC that has
    an older kit there), and leave that window open. In a second window run `Assistant.cmd selftest`. Step 3
    ends "done", the next line is `Kit in use: 0.1.4 (...\seat-kit\kits\0.1.4)`, and the self tests run.
    The folder `current` is still there, unchanged. Close the first window.
19. Turn the network off and run `Assistant.cmd selftest`. Step 3 says "not done", then the reason and the kit
    folder on lines of their own, then "Stopped at step 3: could not get kit 0.1.4 ..." naming the kit already
    on the PC. Nothing else runs. Turn the network back on.

## Taking a first install back (0.1.5)

Run these last, on a PC with a first install, from the `Assistant.cmd` in Downloads, with the assistant closed.

20. `Assistant.cmd remove --check`. It names the desktop shortcut, the assistant's folder (and the dated name it
    would be moved to), each thing in the kit folder, and what it leaves. Nothing changes: the shortcut still
    starts the assistant, and `claude plugin list` still shows `ml-seat-kit`.
21. Start the assistant, leave its window open, and run `Assistant.cmd remove`. It stops at step 4 saying the
    assistant's folder is in use and to close the assistant's window, and nothing is changed. Close the window.
22. `Assistant.cmd remove`. Steps 1 to 4 each say done; step 6 says nothing of the first install is left. Then:
    `claude plugin list` no longer shows `ml-seat-kit`, the shortcut is gone, `%LOCALAPPDATA%\seat-kit` is gone,
    and `%USERPROFILE%\Assistant\<name>.removed-<date>-<time>` holds every note (open the open-items list).
    Claude Code still opens and is still signed in, with its connectors.
23. `Assistant.cmd remove` again. Every step says not there; it ends "Nothing of a first install was there to
    take back." `--delete-my-notes` is not part of this hand test (it deletes the notes for real).

## The self test summary (0.1.6)

0.1.6 changes no behavior of setup, adopt or remove, except the wording of the offline remove stop when the kit on
the PC is older than the launcher needs (it now says the kit is older than the version the launcher needs for
remove, instead of "has no remove"); otherwise only the self tests and their summary changed.

24. `Assistant.cmd selftest`. Right after the `Tests:` line there is an `Ignored:` line, for example
    `Ignored:      AppData\Local\Microsoft (folders other programs make whenever PowerShell starts)`, or
    `Ignored:      nothing`. On a PC with security software that writes into a PowerShell's profile (Malwarebytes
    was the case), a second line is expected to name it, `Also seen and ignored: Malwarebytes`, and the tests are
    expected to pass; this has not yet been run on a PC with Malwarebytes, so say what you saw. The `Tests:` line
    adds up: ran = passed + failed + skipped (an error in a class set up or tear down is named after the counts
    instead). Send the whole window either way.

## The self test's progress and limits (0.1.7)

0.1.7 changes no behavior of setup, adopt or remove; only how the self tests run.

25. `Assistant.cmd selftest`. The first line reads `Step 4 of 6: running the kit's self tests only (this takes several
    minutes; longer on a PC where security software checks each step) ... `. If the run takes longer than 15
    seconds, lines like `   still running: test 120 of 224, 3 min 10 s so far` follow, about every 15 seconds, then
    `Step 4 of 6: all passed` (or `some did not pass`) on its own line. A test running longer than 30 seconds is
    named on the next such line. On a slow PC, once the run passes ten minutes, one line
    `   this PC is running the checks slowly, which is expected; if one check is stuck, the run stops by itself and
    names it` follows the next progress line, only once. A run is stopped only when one step passes its own limit:
    a test (300 seconds, more for a test that starts launchers or PowerShell: the sum of its programs' time limits),
    the set-up or tear-down between two classes (300 seconds), finding the tests (300 seconds) or the end of the
    run after the last test (300 seconds); or, as a backstop for a run that never ends, when the whole run passes
    three hours. After the `Tests:` line, a line `Not run here: 19 checks of the kit's own test runner, which are
    run when the kit is built` is expected; those are not failures and not skips.
    `WholeSuiteUnderShortTemp.test_the_whole_suite_passes_with_a_short_form_temp` is skipped unless this PC's TEMP
    is a short-form path ("this PC's TEMP is not a short-form path ..."); it is still run for every release, by
    `make_kit.py`.
26. Open `%LOCALAPPDATA%\seat-kit\selftest-last.txt`. Its header has `Limits: 300 s per test (a test may ask for
    more), 300 s to find the tests, 10800 s in all (a backstop)`, a `Job object:` line (`armed (...)` expected; say
    so if it reads `not armed`) and a `Not run here:` line. Each test has a `SEAT-KIT-TEST-DONE` line with its
    seconds, and the file ends with "Slowest tests" and "Total by test file". Send the file with the window.

## What to send back

For each numbered item: passed, failed, or what happened instead. For a failure, the last lines on screen.
