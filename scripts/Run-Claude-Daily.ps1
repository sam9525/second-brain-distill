param(
    [string]$Since,   # ISO local override for window start; default = watermark
    [string]$Until    # ISO local override for window end; default = now (open-ended)
)

Set-Location "D:\Obsidian-valut"

$repoRoot      = Split-Path $PSScriptRoot
$watermarkFile = Join-Path $repoRoot ".ingest-watermark"
$bufferMin     = 15
$now  = Get-Date
$end  = if ($Until) { [datetime]$Until } else { $now }
$date = $end.ToString("yyyy-MM-dd")

# --- window start: explicit -Since, else watermark (last run), else bootstrap 24h ---
if ($Since) {
    $histSince = $Since
} elseif (Test-Path $watermarkFile) {
    $histSince = (Get-Content $watermarkFile -Raw).Trim()
} else {
    $histSince = $now.AddDays(-1).ToString("s")
    Write-Host "No watermark found - bootstrapping from $histSince" -ForegroundColor Yellow
}
# Both extractors get the SAME lookback buffer. The buffer MUST cover the
# extractors' active-file window (ACTIVE_BUFFER_SEC=900s): a session still live at
# the previous run was deferred then, so the next run must reach back far enough to
# re-read it whole. Without this, claude_sessions silently dropped those sessions.
$bufStart  = ([datetime]$histSince).AddMinutes(-$bufferMin)
$antiSince = $bufStart.ToString("s")
# session transcripts timestamp in UTC ISO (e.g. 2026-06-27T07:04:12Z); watermark is local, so convert
$claudeSince = $bufStart.ToUniversalTime().ToString("s") + "Z"

# Run BOTH extractors here, not from inside the prompt. Asking the LLM to run them
# meant a source could be silently skipped while the watermark still advanced - the
# failure that orphaned the 2026-06-28 Antigravity sessions below the watermark.
# Now extraction is deterministic and the prompt only summarizes what PS captured.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8   # keep emoji/arrows from the extractors intact
$claudeArgs = @("$PSScriptRoot\claude_sessions.py", "since:$claudeSince")
$antiArgs   = @("$PSScriptRoot\antigravity_sessions.py", "since:$antiSince")
if ($Until) {                                              # cap the window end (claude=UTC, antigravity=local)
    $claudeArgs += "until:$($end.ToUniversalTime().ToString('s'))Z"
    $antiArgs   += "until:$($end.ToString('s'))"
}
$claudeOut = & C:\msys64\ucrt64\bin\python.exe $claudeArgs 2>&1 | Out-String
$antiOut   = & C:\msys64\ucrt64\bin\python.exe $antiArgs 2>&1 | Out-String
Write-Host ("Claude output: {0} chars | Antigravity output: {1} chars" -f $claudeOut.Length, $antiOut.Length) -ForegroundColor DarkCyan

$rules = Get-Content (Join-Path $repoRoot "config\summary-rules.txt") -Raw -Encoding utf8
$prompt = @"
Incremental daily ingest. Process ONLY work done after the last successful run. Both extractors have ALREADY been run for you and their output is included verbatim below — do NOT re-run them and do NOT read raw transcripts.

=== Claude Code sessions ($date) ===
$claudeOut
=== end Claude Code sessions ===

=== Antigravity sessions ($date) ===
$antiOut
=== end Antigravity sessions ===

$rules

If BOTH sections above are empty or report no sessions, there is nothing to summarize: do NOT create or modify memory/Inbox/$date.md, skip straight to the ingest step below, and report "no new conversations". Otherwise summarize what I did split by project, and create or update the daily note at memory/Inbox/$date.md (append without duplicating existing bullets if it already exists).

Finally, ingest every domain by processing each domain's Inbox/. For each domain, check its Inbox/ for staged files and run that domain's ingest workflow (per its CLAUDE.md) on whatever is there. If a domain's Inbox/ is empty, skip it. Check all three Inbox folders independently, even if only one has files.

IMPORTANT: writing the daily note to memory/Inbox/$date.md above is staging it, NOT ingesting it — they are separate steps. memory/Inbox/ almost always has at least the daily note you just wrote (or an already-existing one), so the memory domain is virtually never "empty" and must go through its own ingest workflow every run: move memory/Inbox/$date.md to memory/raw/, synthesize into memory/wiki/, ingesting the WHOLE daily note including its "## Insights & Decisions" section (project-specific insights go into the matching project concept page; insights that belong to no project go into a topic concept page per memory/CLAUDE.md - never read-and-dropped just because they aren't about a project), and update memory_index.md and memory_log.md. Do this even when Tech/ and personal-notes/ Inboxes are both empty — memory's ingest does not depend on the other two domains having anything to process.

When you have finished ALL of the above — the daily note AND ingest for every domain's Inbox — print this exact line as your very last line of output, on its own:
INGEST_COMPLETE
Do not print that line if you stop for any reason before finishing everything above.
"@

Write-Host "Daily note task - $date (since $histSince)" -ForegroundColor Cyan
# Prompt can be hundreds of KB (session transcripts inlined). Windows caps a command
# line at ~32KB, so pass it via stdin from a temp file instead of as an argument.
$promptFile = Join-Path $env:TEMP "claude-daily-prompt-$($now.ToString('yyyyMMddHHmmss')).txt"
[System.IO.File]::WriteAllText($promptFile, $prompt, (New-Object System.Text.UTF8Encoding $false))  # UTF-8 no BOM
try {
    # Tee-Object streams to console (same as before) AND captures into $claudeResult,
    # so we can check for the completion sentinel without changing what you see live.
    Get-Content $promptFile -Raw -Encoding utf8 | claude --dangerously-skip-permissions -p --model sonnet | Tee-Object -Variable claudeResult
} finally {
    Remove-Item $promptFile -ErrorAction SilentlyContinue
}

# The 07-05 run exited 0 (claude CLI didn't crash) but the model stopped after writing
# the daily note, never reaching the ingest step - watermark advanced anyway, and the
# skipped ingest went unnoticed for a day. Require the explicit sentinel too, not just
# exit code, before trusting that ingest actually ran end-to-end.
$sawSentinel = ($claudeResult -join "`n") -match 'INGEST_COMPLETE'
if ($LASTEXITCODE -eq 0 -and $sawSentinel) {
    $end.ToString("s") | Set-Content $watermarkFile -Encoding ascii -NoNewline
    Write-Host "Watermark advanced to $($end.ToString('s'))." -ForegroundColor Green
} elseif ($LASTEXITCODE -eq 0) {
    Write-Host "Claude exited 0 but never printed INGEST_COMPLETE - ingest likely incomplete; watermark NOT advanced." -ForegroundColor Red
} else {
    Write-Host "Ingest exited $LASTEXITCODE - watermark NOT advanced; this range retries next run." -ForegroundColor Yellow
}

Write-Host "`nDone." -ForegroundColor Green
# Only block for a keypress when launched interactively; under Task Scheduler
# ([Environment]::UserInteractive is false) Read-Host would hang the nightly run.
if ([Environment]::UserInteractive) { Read-Host "Press Enter to close" }
