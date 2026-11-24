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

## What to send back

For each numbered item: passed, failed, or what happened instead. For a failure, the last lines on screen.
