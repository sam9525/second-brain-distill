# One-off backfill: rebuild old daily notes from the SAME conversation source
# the nightly job uses (claude_sessions.py + antigravity_sessions.py), one date
# at a time. Writes to scripts/backfill/<date>.md for review — does NOT touch
# raw/ (immutable) or Inbox/ (nightly cron's staging). Review, then promote.
# ponytail: review-folder output, not in-place; promote to Inbox/ manually once happy.
#
# Promote to memory/Inbox/, NOT memory/raw/. Copying straight into raw/ bypasses
# ingest entirely: the note lands in the vault but nothing ever synthesizes it into
# wiki/, which is the same read-and-dropped failure the 2026-08-17 ingest-rule fix
# was about. From Inbox/ the next run ingests it properly (Inbox -> raw, plus wiki
# synthesis and index/log updates) with no manual step.

param([string[]]$Dates = @(
  "2026-09-04" 
))

Set-Location "D:\Obsidian-valut"
$out = "D:\Obsidian-valut\scripts\backfill"
New-Item -ItemType Directory -Force $out | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$py = "C:\msys64\ucrt64\bin\python.exe"
$rulesFile = Join-Path (Split-Path $PSScriptRoot) "config\summary-rules.txt"

foreach ($date in $Dates) {
    $claudeOut = & $py "$PSScriptRoot\claude_sessions.py" $date 2>&1 | Out-String
    $antiOut   = & $py "$PSScriptRoot\antigravity_sessions.py" $date 2>&1 | Out-String
    Write-Host ("{0}: claude={1}c antigravity={2}c" -f $date, $claudeOut.Length, $antiOut.Length) -ForegroundColor DarkCyan

    $rules = Get-Content $rulesFile -Raw -Encoding utf8
    $prompt = @"
Rebuild the daily note for $date from the conversations below. Both extractors have ALREADY been run for you and their output is included verbatim below — do NOT re-run them and do NOT read raw transcripts.

=== Claude Code sessions ($date) ===
$claudeOut
=== end Claude Code sessions ===

=== Antigravity sessions ($date) ===
$antiOut
=== end Antigravity sessions ===

$rules

If BOTH sections above are empty or report no sessions, there is nothing to summarize: do NOT create or modify scripts/backfill/$date.md, and report "no new conversations". Otherwise summarize what I did split by project, add the Insights & Decisions section, and create or update the daily note at scripts/backfill/$date.md (append without duplicating existing bullets if it already exists).
"@

    Write-Host "Summarizing $date ..." -ForegroundColor Cyan
    # Prompt is 60-100k chars — too long for a cmdline arg on Windows (~32k cap).
    # Pipe via stdin: claude -p reads the prompt from stdin when no arg is given.
    $tmp = Join-Path $env:TEMP "backfill-$date.txt"
    [System.IO.File]::WriteAllText($tmp, $prompt, (New-Object System.Text.UTF8Encoding $false))  # UTF-8 no BOM
    try {
        Get-Content $tmp -Raw -Encoding utf8 | claude --dangerously-skip-permissions -p --model sonnet
    } finally {
        Remove-Item $tmp -ErrorAction SilentlyContinue
    }
}

Write-Host "`nDone. Review scripts\backfill\, then copy approved notes into memory\Inbox\ - NOT memory\raw\." -ForegroundColor Green
Write-Host "Inbox/ is what triggers ingest; a note dropped straight into raw/ never reaches wiki/." -ForegroundColor DarkGray
