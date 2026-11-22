# Assistant kit

A drafts-only email assistant that runs in Claude Code on a Windows PC. It reads mail and calendar through the
person's own connectors, keeps a short list of open items, and writes drafts. It never sends, replies, forwards,
answers an invitation, invites anyone, or changes an event. The person does those by hand.

## Install and start

1. Download `Assistant.cmd` from the latest release and run it.
2. It checks for Claude Code and Python, gets the kit, sets up the assistant's folder, and opens the assistant.
   Each step prints one plain line. If a step cannot finish, it stops and says what to do.
3. Run the same file again any time to start the assistant. It also brings the newest kit.

If Windows will not run the file, open PowerShell and run `Assistant.ps1` instead. It does the same steps.

Sign in to Claude, connect Gmail, Google Calendar and Google Drive, and answer each permission question yourself.
The kit never grants a permission for you.

## What is here

- `Assistant.cmd`, `Assistant.ps1`: the launcher, in two forms.
- `plugins/ml-seat-kit`: the Claude Code plugin with the scripts, the start skill, the templates and the tests.
  Its own `README.md` explains each part, and `HAND_TEST.md` is the checklist for a first real start.
- `.claude-plugin/marketplace.json`: lets Claude Code add this folder as a plugin marketplace.

## Tests

From `plugins/ml-seat-kit`: `python -m unittest discover -s tests`

## Releases

Each release carries `seat-kit.zip`, which is this repository's content in the shape the launcher expects. The
launcher always downloads the latest one. The version in `plugins/ml-seat-kit/.claude-plugin/plugin.json` goes up
with every release.
