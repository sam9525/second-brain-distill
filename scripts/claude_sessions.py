#!/usr/bin/env python3
"""Extract readable conversation from Claude Code session transcripts.

Reads ~/.claude/projects/<proj>/<session>.jsonl and prints, per session, the
user prompts + assistant text, plus a one-line gist of each sub-agent (Task)
dispatch and its return. Drops tool_use inputs, tool_result bodies, thinking
blocks, and preamble narration — a pure-text assistant row immediately
followed (no user row in between) by an assistant row containing a tool_use
is Claude narrating right before it acts ("I'll run the backfill script."),
not a reply meant to reach the user, and is dropped via that adjacency check
rather than a regex/length heuristic on the text itself. Also skips the
daily-ingest automation's own session entirely, since its `claude -p`
invocation embeds the full prior extractor output in its first prompt and
would otherwise get re-harvested as real conversation on the next run,
nesting duplicate content run over run. Together this drops the ~97% of
transcript volume a daily summary doesn't need (on a busy day that is ~960k
tokens of raw JSONL vs ~29k tokens of actual talk). See
Tech/Inbox/claude-transcript-type-structure.md for the writeup.

Usage mirrors antigravity_sessions.py:
    python claude_sessions_v2.py                          # today
    python claude_sessions_v2.py 2026-06-26               # a specific YYYY-MM-DD
    python claude_sessions_v2.py all                       # every session
    python claude_sessions_v2.py since:2026-06-27T00:00:00Z  # incremental (UTC)

On this machine run with the msys2 interpreter:
    C:\\msys64\\ucrt64\\bin\\python.exe claude_sessions_v2.py
"""

import datetime
import glob
import json
import os
import sys

PROJ_DIR = os.path.expanduser("~/.claude/projects")
ACTIVE_BUFFER_SEC = 900  # a transcript written within this window is treated as live
MAX_TURN = 2000  # cap chars per message so a giant paste can't dominate
MAX_RESULT = 300  # cap chars of a sub-agent's returned gist


def parse_ts(s):
    if not isinstance(s, str):
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def iter_lines(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            ln = ln.strip()
            if ln:
                try:
                    yield json.loads(ln)
                except json.JSONDecodeError:
                    pass


def is_injected(text):
    """Claude Code injects context as user turns (system reminders, command
    wrappers, hook output). Those are not things the human said — drop them."""
    t = text.lstrip()
    return (
        t.startswith("<")
        or "<system-reminder>" in t
        or "<command-name>" in t
        or "<command-message>" in t
        or "<task-notification>" in t
        or "<local-command-stdout>" in t
        or t.startswith("Caveat:")
    )


def is_ingest_session(rows):
    """The daily-ingest automation's own `claude -p` invocation is itself a
    Claude Code session, and its initiating prompt embeds the full prior
    extractor output verbatim (that's the whole point of summarizing it). If
    not skipped, the next run's extraction re-harvests that embedded dump as
    if it were real conversation, nesting duplicate content run over run —
    on 2026-06-30 this one turn alone was 192KB. Detect it by the fixed
    prompt prefix Run-Claude-Daily.ps1 always uses."""
    for o in rows:
        if o.get("type") != "user" or o.get("isMeta"):
            continue
        content = (o.get("message") or {}).get("content")
        if isinstance(content, str):
            return content.lstrip().startswith("Incremental daily ingest")
        return False
    return False


def is_preamble(rows, i):
    """True if rows[i] is a pure-text assistant row immediately followed (no
    user row in between) by an assistant row containing a tool_use — Claude
    narrating right before it acts, not a reply meant for the user."""
    o = rows[i]
    if o.get("type") != "assistant":
        return False
    content = (o.get("message") or {}).get("content")
    if not (
        isinstance(content, list)
        and len(content) == 1
        and isinstance(content[0], dict)
        and content[0].get("type") == "text"
    ):
        return False
    if i + 1 >= len(rows):
        return False
    nxt = rows[i + 1]
    if nxt.get("type") != "assistant":
        return False
    nc = (nxt.get("message") or {}).get("content")
    return isinstance(nc, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_use" for b in nc
    )


def session(path, day, until=None):
    """Return [(ts, role, text)] for in-scope messages: human prompts, assistant
    text, sub-agent dispatch descriptions, and sub-agent return gists.

    No per-message `since` filter: a file reaches here only because its mtime
    passed the (buffered) watermark in main(), so we emit the WHOLE session and
    let the summarizer dedup overlap. Per-message dropping would lose the early
    messages of a session that was still live at the previous run (its file was
    deferred then, and those messages sit below the next watermark) — the same
    whole-file emission antigravity_sessions.py relies on."""
    task_ids = set()
    out = []
    rows = list(iter_lines(path))
    if is_ingest_session(rows):
        return []
    for i, o in enumerate(rows):
        typ = o.get("type")
        if typ not in ("user", "assistant"):
            continue
        if o.get("isMeta"):  # skill dumps, command expansions, caveats
            continue
        ts = parse_ts(o.get("timestamp"))
        if until is not None and (ts is None or ts > until):
            continue
        if day is not None and (ts is None or ts.date() != day):
            continue
        if is_preamble(rows, i):  # narration right before a tool call — skip
            continue
        content = (o.get("message") or {}).get("content")
        if isinstance(content, str):
            t = content.strip()
            if t and not is_injected(t):
                out.append((ts, "you", t[:MAX_TURN]))
            continue
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                t = b.get("text", "").strip()
                if not t:
                    continue
                role = "you" if typ == "user" else "claude"
                if role == "you" and is_injected(t):
                    continue
                out.append((ts, role, t[:MAX_TURN]))
            elif bt == "tool_use" and b.get("name") == "Task":
                inp = b.get("input") or {}
                desc = (inp.get("description") or "").strip()
                sub = (inp.get("subagent_type") or "").strip()
                if b.get("id"):
                    task_ids.add(b["id"])
                if desc:
                    out.append((ts, "task", f"[{sub}] {desc}" if sub else desc))
            elif bt == "tool_result" and b.get("tool_use_id") in task_ids:
                c = b.get("content", "")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                c = " ".join(str(c).split())
                if c:
                    out.append((ts, "result", c[:MAX_RESULT]))
    return out


def main():
    try:  # transcripts contain emoji; console may be cp950
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    args = sys.argv[1:] or ["today"]
    since = day = until = None
    for arg in args:
        if arg.startswith("since:") or arg.startswith("until:"):
            t = parse_ts(arg[6:]) or datetime.datetime.fromisoformat(arg[6:])
            if t.tzinfo is None:
                t = t.replace(tzinfo=datetime.timezone.utc)
            if arg.startswith("since:"):
                since = t
            else:
                until = t
        elif arg == "today":
            day = datetime.date.today()
        elif arg == "all":
            day = None
        else:
            day = datetime.datetime.strptime(arg, "%Y-%m-%d").date()

    files = sorted(
        glob.glob(os.path.join(PROJ_DIR, "*", "*.jsonl")),
        key=os.path.getmtime,
        reverse=True,
    )
    now = datetime.datetime.now()
    shown = 0
    for f in files:
        mt = datetime.datetime.fromtimestamp(os.path.getmtime(f))
        if since is not None:
            since_naive = since.astimezone().replace(tzinfo=None)
            if mt <= since_naive:
                continue  # no writes after the watermark
            if (now - mt).total_seconds() < ACTIVE_BUFFER_SEC:
                continue  # still live (e.g. this ingest run)
        rows = session(f, day, until)
        if not rows:
            continue
        shown += 1
        proj = os.path.basename(os.path.dirname(f))
        sid = os.path.basename(f)[:8]
        print(f"\n### {proj}  {sid}  ({len(rows)} msgs)")
        for ts, role, text in rows:
            stamp = ts.strftime("%H:%M") if ts else "--:--"
            prefix = {
                "you": "you",
                "claude": "claude",
                "task": "  → task",
                "result": "    ↳ result",
            }[role]
            text = text.replace("\n", "\n      ")
            print(f"[{stamp}] {prefix}: {text}")

    label = "today" if arg == "today" else arg
    if not shown:
        print(f"No Claude Code sessions found for {label}.")


if __name__ == "__main__":
    main()
