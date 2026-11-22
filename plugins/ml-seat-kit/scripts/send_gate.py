"""Send gate: a PreToolUse hook that keeps an assistant seat to drafts only.

Wired in the plugin's hooks/hooks.json for every MCP tool whose name carries a send, reply, forward, respond or
RSVP word, or an event or invite word (an invitation is a send). Plugin hooks run in EVERY session of the person who installed the plugin, so the gate first asks one
question: is this session's project folder a seat folder (the marker .seat/seat.json, at the folder or above it)?

  Not a seat  -> does nothing: exit 0, no output, the normal permission flow continues.
  In a seat   -> denies (exit 2, the plain reason on stderr), unless the tool is plainly a read or a draft tool
                 (create_draft and friends). Any error, unreadable input or unknown shape inside a seat = deny.
  Can't tell  -> denies only when ANY place it can look (CLAUDE_PROJECT_DIR, the event's cwd, the hook's own
                 folder) is a seat; otherwise does nothing.

Calendar events: making an event with no guests passes (a block on her own calendar). Making one WITH guests,
or changing, moving or deleting any event, is denied: each can mail an invitation or a notice to other people,
and the gate cannot see who is already on an event.

There is no approval path in this version: the person sends from Gmail or answers the invite in Calendar, by hand.
A second layer sits in the seat's project settings (permission deny rules for the known connector send tools),
so a hook that cannot start at all (no Python) still cannot let a send through.

Standard library only, and nothing imported from the kit: a broken helper module must not open the gate.

    python -I send_gate.py            (the hook; reads the event JSON on stdin)
    python -I send_gate.py --probe    (feeds a fake send to the logic and prints the decision; sends nothing)
"""
import json
import os
import re
import sys
import time

MARKER = os.path.join(".seat", "seat.json")
SEND_VERBS = {"send", "reply", "forward", "respond", "rsvp"}
SAFE_VERBS = {"create", "update", "save", "list", "get", "search", "read", "find", "fetch"}
DRAFTS_ONLY = "Drafts only: I wrote the draft, you send it yourself from Gmail."
NO_RSVP = "Drafts only: I never answer invites. Open the invite in Google Calendar and answer it yourself."
NO_SEND = "Drafts only: this assistant never sends anything. Send it yourself if you want it sent."
NO_INVITE = ("Drafts only: I never invite people, and I never change or remove an event, because that can "
             "notify its guests. Do it yourself in Google Calendar.")
EVENT_WORDS = {"event", "events", "meeting", "meetings", "appointment", "appointments"}
INVITE_WORDS = {"invite", "invites", "invitation", "invitations", "invitee", "invitees"}
EVENT_READS = {"list", "get", "search", "read", "find", "fetch"}
EVENT_MAKES = {"create", "add", "insert", "new", "quick", "schedule", "book"}
GUEST_KEYS = ("attendee", "guest", "invitee", "participant", "recipient")


def has_guests(value, depth=0):
    """True when a tool input names anyone to invite, at any depth. An unreadable shape counts as guests."""
    if depth > 8:
        return True
    if isinstance(value, dict):
        for k, v in value.items():
            if any(g in str(k).lower() for g in GUEST_KEYS) and v not in (None, "", [], {}, False):
                return True
            if has_guests(v, depth + 1):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(has_guests(v, depth + 1) for v in value)
    return False


def decide_event(ws, event):
    """(deny, reason) for a tool whose name carries an event or invite word."""
    if any(w in INVITE_WORDS for w in ws):
        return True, NO_INVITE
    for w in ws:
        if w in EVENT_READS:
            return False, ""
        if w in EVENT_MAKES:
            inp = event.get("tool_input")
            if not isinstance(inp, dict) or has_guests(inp):
                return True, NO_INVITE
            return False, ""
    return True, NO_INVITE                 # update, move, delete, or a verb the gate does not know


def seat_of(start):
    """The nearest folder at or above `start` holding the seat marker, or None. Raises nothing."""
    try:
        cur = os.path.abspath(start)
    except Exception:  # noqa: BLE001
        return None
    for _ in range(64):
        try:
            if os.path.isfile(os.path.join(cur, MARKER)):
                return cur
        except Exception:  # noqa: BLE001
            return None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent
    return None


def places(event):
    out = []
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        out.append(os.environ["CLAUDE_PROJECT_DIR"])
    if isinstance(event, dict) and isinstance(event.get("cwd"), str) and event.get("cwd"):
        out.append(event["cwd"])
    try:
        out.append(os.getcwd())
    except OSError:
        pass
    return out


def in_seat(event):
    """The seat folder if any place this session could be is a seat, else None."""
    for p in places(event):
        s = seat_of(p)
        if s:
            return s
    return None


def reason_for(tool):
    t = (tool or "").lower()
    if "calendar" in t or "respond" in t or "rsvp" in t or "event" in t:
        return NO_RSVP
    if "mail" in t or "gmail" in t or "outlook" in t:
        return DRAFTS_ONLY
    return NO_SEND


def words(action):
    """send_message, sendMessage and send-message all become ['send', 'message']."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", action)
    return [w for w in re.split(r"[_\-\s.]+", spaced.lower()) if w]


def decide(event):
    """(deny: bool, reason). Never raises.

    The first verb in the tool's own name decides: a send verb (send, reply, forward, respond, rsvp) denies; a read
    or draft verb (create, update, save, list, get, search, read, find, fetch) passes, unless a later word is send,
    respond or rsvp (create_and_send, update_rsvp). No verb at all is an unknown shape: deny."""
    try:
        if not isinstance(event, dict):
            return True, "Drafts only: the send gate could not read this request, so it refused it."
        tool = event.get("tool_name")
        if not isinstance(tool, str) or not tool.strip():
            return True, "Drafts only: the send gate could not tell which tool this is, so it refused it."
        ws = words(tool.rsplit("__", 1)[-1])
        if any(w in SEND_VERBS for w in ws[:1]) or any(w in ("send", "respond", "rsvp") for w in ws):
            return True, reason_for(tool)
        if any(w in EVENT_WORDS or w in INVITE_WORDS for w in ws):
            return decide_event(ws, event)
        for i, w in enumerate(ws):
            if w in SEND_VERBS:
                return True, reason_for(tool)
            if w in SAFE_VERBS:
                if any(x in ("send", "respond", "rsvp") for x in ws[i + 1:]):
                    return True, reason_for(tool)
                return False, ""
        return True, reason_for(tool)
    except Exception as e:  # noqa: BLE001
        return True, "Drafts only: the send gate hit an error (%s), so it refused." % type(e).__name__


def _log(seat, line):
    try:
        d = os.path.join(seat, ".seat", "logs")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "send_gate.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%SZ ", time.gmtime()) + line + "\n")
    except Exception:  # noqa: BLE001
        pass


def _deny(reason):
    sys.stderr.write(reason + "\n")
    return 2


def read_event_text(stdin=None):
    """The hook's stdin as text. Bytes are decoded as UTF-8 with any byte-order mark dropped (a PowerShell 5.1 pipe
    adds one), so a request is never misread as unreadable."""
    if stdin is not None:
        raw = stdin.read()
    else:
        raw = getattr(sys.stdin, "buffer", sys.stdin).read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig", errors="replace")
    return raw.lstrip("﻿")


def main(stdin=None):
    seat = None
    try:
        raw = read_event_text(stdin)
        try:
            event = json.loads(raw) if raw and raw.strip() else None
        except ValueError:
            event = None
        seat = in_seat(event)
        if not seat:
            return 0                       # not a seat: do nothing at all
        deny, reason = decide(event)
        tool = event.get("tool_name") if isinstance(event, dict) else "?"
        _log(seat, "%s %s" % ("DENY" if deny else "PASS", tool))
        return _deny(reason) if deny else 0
    except BaseException as e:  # noqa: BLE001
        # Inside a seat, or when we could not even tell: refuse. Outside a seat we would have returned above.
        try:
            if seat or in_seat(None):
                return _deny("Drafts only: the send gate hit an error (%s), so it refused." % type(e).__name__)
        except BaseException:  # noqa: BLE001
            return _deny("Drafts only: the send gate hit an error, so it refused.")
        return 0


def probe():
    deny, reason = decide({"tool_name": "mcp__claude_ai_Gmail__send_message", "tool_input": {"to": ["a@example.invalid"]}})
    print(("DENY " if deny else "PASS ") + reason)
    return 0


if __name__ == "__main__":
    if "--probe" in sys.argv:
        raise SystemExit(probe())
    raise SystemExit(main())
