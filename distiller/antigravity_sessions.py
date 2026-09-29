#!/usr/bin/env python3
"""Summarize Antigravity agent conversation sessions.

Reads the SQLite conversation DBs under ~/.gemini/antigravity/conversations and
prints, per session, the workspace, the agent task titles, and the readable
conversation text (user prompts + agent reasoning) recovered from the step
blobs. Message text is protobuf-wrapped but the natural-language runs are
extractable; JSON/path/tag noise is filtered out and the daily summarizer
sifts the rest.

The same sentence is often recovered twice from different blob columns
(payload/render_info/task_details), each with a different protobuf-framing
prefix glued on the front (e.g. "dI will..." vs "J\\tview_fileBdI will...");
strip_noise_prefix() locates the real sentence start and slices the junk off
before the dedup check, so these no longer survive as false duplicates.
Narration lines are also filtered: a single-sentence, future-tense line like
"I will view file X to understand Y" is Antigravity's equivalent of Claude's
tool-call preamble — process narration, not a reply. Since reasoning and tool
steps alternate on nearly every row here, a text-then-tool-call adjacency
check (as used for Claude Code) doesn't work; instead this filters on tense +
sentence shape: "I will/I'll/Let me ..." single sentences are dropped, while
"I have/I've ..." reports, bolded reasoning blocks, and multi-sentence content
are kept. See Tech/Inbox/claude-transcript-type-structure.md for the writeup
(that file also covers the Claude-side transcript structure this mirrors).

Usage:
    python antigravity_sessions_v2.py                       # today's sessions (default)
    python antigravity_sessions_v2.py 2026-06-14            # a specific YYYY-MM-DD
    python antigravity_sessions_v2.py all                   # every session
    python antigravity_sessions_v2.py since:2026-06-19T14:30:00  # incremental: sessions
                                                         # modified after the watermark
                                                         # and not currently active

On this machine run with the msys2 interpreter:
    C:\\msys64\\ucrt64\\bin\\python.exe antigravity_sessions_v2.py
"""

import datetime
import glob
import os
import re
import sqlite3
import sys

CONV_DIR = os.path.expanduser(r"~/.gemini/antigravity/conversations")

# Windows drive paths (C:\ or d:/) and macOS/Linux home paths (/Users/, /home/)
# the POSIX lookbehind keeps relative paths like "tests/home/x.ts" from matching
ABS_PATH_START = re.compile(r"[A-Za-z]:[\\/]|(?<![\w.\-/])/(?:Users|home)/")
# lookbehinds skip "file:///d:/..." URIs (they carry trailing protobuf bytes)
ABS_PATH = re.compile(
    r"(?:(?<![A-Za-z/])[A-Za-z]:[\\/]|(?<![\w.\-/:])/(?:Users|home)/)[\w\\/ .\-]+"
)
# paths into the agent's own state or installed tooling say nothing about the
# project being worked on, so they never count as the session's workspace
TOOLING_PATH_MARKERS = (
    ".gemini",
    "AppData",
    "node_modules",
    "Library",
    "site-packages",
    "Program Files",
)


def runs(b, minlen=5):
    """Yield printable-ASCII runs of at least `minlen` chars from a blob."""
    out, cur = [], bytearray()
    for byte in b:
        if 32 <= byte < 127 or byte in (9, 10, 13):
            cur.append(byte)
        else:
            if len(cur) >= minlen:
                out.append(cur.decode("ascii", "ignore"))
            cur = bytearray()
    if len(cur) >= minlen:
        out.append(cur.decode("ascii", "ignore"))
    return out


def good(c):
    """Keep strings that look like natural-language task titles."""
    c = c.strip().lstrip("\"'").strip()
    if len(c) < 6 or " " not in c:
        return None
    if c.startswith("bot-") or re.fullmatch(r"[0-9a-f\-]{20,}", c):
        return None
    letters = sum(ch.isalpha() or ch == " " for ch in c)
    if letters / len(c) < 0.65:
        return None
    return c


def titles(rows):
    res = []
    for idx, st, status, payload, render, task in rows:
        if st == 23:  # task step
            for blob in (task, payload, render):
                if blob:
                    hit = next((g for c in runs(blob, 6) if (g := good(c))), None)
                    if hit:
                        res.append((idx, hit))
                        break
    return res


# A session whose step-14 (input) text exceeds this is being driven by an
# injected agent prompt (a dispatched sub-agent / automated worker) rather than
# a person. We then keep only its step-15 (the agent's own words) and drop the
# injected template, instead of matching any specific workflow's wording.
# ponytail: threshold tuned to observed data (human turns ~hundreds of chars,
# injected prompts ~1.6k+); raise it if your own prompts run long.
HUMAN_INPUT_MAX = 1200

# Real sentence starts observed in the wild. A garbled protobuf-framing prefix
# (stray letters/digits/tabs, or a field-name token like "view_file") always
# sits before one of these, within a short lookahead — never past it.
_SENTENCE_START = re.compile(
    r"(I will\b|I'll\b|I'm\b|I've\b|I have\b|I am\b|I need\b|Let me\b|Let's\b|"
    r"Now\b|Okay,?\b|An \b|The \b|Spec \b|\*\*)"
)

# Antigravity's equivalent of Claude's tool-call preamble: a single-sentence,
# future-tense line describing the very next action ("I will view X to
# understand Y"). Deliberately excludes past-tense reporting ("I have
# dispatched...", "I've confirmed...") and bolded reasoning blocks, which are
# substantive even though they share the "I " opener.
_NARRATION = re.compile(
    r"^(I will\b|I'll\b|I am going to\b|I'm going to\b|Let me\b|Let's\b|Now,? I'll\b)"
)


def strip_noise_prefix(s, lookahead=25):
    """Slice off a leading protobuf-framing artifact so the same sentence
    recovered from two different blob columns normalizes to one string.

    Search the FULL string (not a truncated slice) — the noise prefix can
    itself run past `lookahead` chars (e.g. "replace_file_contentB~I will..."
    is 22 chars of junk), and slicing first would cut the match off mid-word.
    `lookahead` instead bounds how far into the string a match is still
    considered a prefix worth stripping, vs. a real sentence found later."""
    m = _SENTENCE_START.search(s)
    if m and 0 < m.start() <= lookahead:
        return s[m.start() :]
    return s


def is_narration(s):
    return bool(_NARRATION.match(s)) and "\n\n" not in s and len(s) < 220


def conversation(rows, minlen=40):
    """Recover sentence-like conversation text from a session, workflow-agnostic.

    step_type 15 is the agent's own generated reasoning/output; step_type 14 is
    input. A session whose step-14 volume is large was driven by an injected
    agent prompt (a dispatched worker, any framework) rather than a person, so we
    keep only its step-15 and trim it to a brief gist — the human-facing session
    that dispatched it carries the full story. Human-driven sessions keep their
    prompts + full reasoning. Generic noise (JSON, paths, protobuf id tails) is
    filtered; the summarizer condenses the rest.
    """
    step14_vol = sum(
        len(s)
        for idx, st, status, p, rn, t in rows
        if st == 14
        for blob in (p, rn, t)
        if blob
        for s in runs(blob, minlen)
    )
    automated = step14_vol > HUMAN_INPUT_MAX
    keep = (15,) if automated else (14, 15)
    cap = 12 if automated else 60
    seen, out = set(), []
    for idx, st, status, payload, render, task in rows:
        if st not in keep:
            continue
        for blob in (payload, render, task):
            if not blob:
                continue
            for s in runs(blob, minlen):
                # protobuf record id tails ("...text.2(bot-<uuid>B...text") duplicate
                # the sentence; cut at the marker so dedup collapses the pair.
                s = re.sub(r"\(bot-[0-9a-f-]{6,}.*$", "", s).rstrip(" `\t")
                s = re.sub(
                    r"(?<=[.!?])\d$", "", s
                ).strip()  # drop trailing wire-byte digit
                s = strip_noise_prefix(s)  # drop leading protobuf framing junk
                if s in seen:
                    continue
                if s[:1] in "{[<":  # JSON / tag
                    continue
                if '":' in s:  # JSON fragment
                    continue
                if ABS_PATH_START.search(s):  # file path, not prose
                    continue
                if len(s.split()) < 5:  # too short to be a sentence
                    continue
                if sum(ch.isalpha() or ch.isspace() for ch in s) / len(s) < 0.75:
                    continue
                if is_narration(s):  # "I will do X" preamble, not a reply
                    continue
                seen.add(s)
                out.append(s)
                if len(out) >= cap:
                    return out
    return out


def normalize_path(p):
    return re.sub(r"[\\/]+", "/", p).lower().rstrip("/")


# set by distill.py from config.toml's project_dirs (os.pathsep-joined)
PROJECT_DIRS = [
    normalize_path(p)
    for p in os.environ.get("DISTILL_PROJECT_DIRS", "").split(os.pathsep)
    if p
]


def is_workspace_path(path):
    if PROJECT_DIRS:
        norm = normalize_path(path)
        return any(norm == d or norm.startswith(d + "/") for d in PROJECT_DIRS)
    return not any(k in path for k in TOOLING_PATH_MARKERS)


def workspace(rows):
    for idx, st, status, payload, render, task in rows:
        for blob in (payload, task, render):
            if blob:
                for c in runs(blob, 8):
                    m = ABS_PATH.search(c)
                    if m and is_workspace_path(m.group(0)):
                        return m.group(0)
    return "?"


def created(path):
    """File creation time. getctime is creation only on Windows; on POSIX it is
    the last metadata change, so prefer st_birthtime (macOS) when present."""
    st = os.stat(path)
    # ponytail: Linux has no birthtime -> falls back to ctime (metadata change)
    return getattr(st, "st_birthtime", st.st_ctime)


ACTIVE_BUFFER_SEC = 900  # a DB modified within this window is treated as a live session


def main():
    args = sys.argv[1:] or ["today"]
    since = day = until = None
    for arg in args:
        if arg.startswith("since:"):
            since = datetime.datetime.fromisoformat(arg[len("since:") :])
        elif arg.startswith("until:"):
            until = datetime.datetime.fromisoformat(arg[len("until:") :])
        elif arg == "today":
            day = datetime.date.today()
        elif arg == "all":
            day = None
        else:
            day = datetime.datetime.strptime(arg, "%Y-%m-%d").date()

    dbs = sorted(
        glob.glob(os.path.join(CONV_DIR, "*.db")), key=os.path.getmtime, reverse=True
    )

    now = datetime.datetime.now()
    shown = 0
    for db in dbs:
        mt = datetime.datetime.fromtimestamp(os.path.getmtime(db))
        if since is not None:
            if mt <= since:
                continue
            if (now - mt).total_seconds() < ACTIVE_BUFFER_SEC:
                continue  # still active — defer to the next run
        elif day is not None:
            # Attribute to the day the session STARTED (file creation), not last
            # write, so a session spanning midnight lands on the day its work began.
            # ponytail: ctime is the best proxy — the schema has no per-step timestamp.
            ct = datetime.datetime.fromtimestamp(created(db))
            if ct.date() != day:
                continue
        if until is not None and mt > until:
            continue  # session touched after the window end — out of range
        con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
        rows = con.execute(
            "SELECT idx, step_type, status, step_payload, render_info, task_details "
            "FROM steps ORDER BY idx"
        ).fetchall()
        con.close()
        shown += 1
        print(
            f"\n### {mt:%Y-%m-%d %H:%M}  ({len(rows)} steps)  "
            f"{os.path.basename(db)[:8]}  ws={workspace(rows)}"
        )
        for idx, t in titles(rows):
            print(f"   - {t[:200]}")
        convo = conversation(rows)
        if convo:
            print("   conversation:")
            for s in convo:
                print(f"   | {s[:500]}")

    label = "today" if args == ["today"] else " ".join(args)
    if not shown:
        print(f"No Antigravity sessions found for {label}.")


if __name__ == "__main__":
    main()
