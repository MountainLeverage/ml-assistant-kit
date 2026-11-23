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

## What to send back

For each numbered item: passed, failed, or what happened instead. For a failure, the last lines on screen.
