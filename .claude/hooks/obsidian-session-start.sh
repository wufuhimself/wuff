#!/bin/bash
# SessionStart hook: surface recent Obsidian (wufu vault) activity into context.
# Soft-fails if Obsidian app isn't running (socket absent) or obsidian-cli errors.

SOCK="$HOME/.obsidian-cli.sock"
BIN="/Applications/Obsidian.app/Contents/MacOS/obsidian-cli"

if [ ! -S "$SOCK" ] || [ ! -x "$BIN" ]; then
  jq -n '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:"Obsidian CLI unavailable (app not running) - skipped recents/daily check."}}'
  exit 0
fi

RECENTS=$("$BIN" vault=wufu recents 2>/dev/null | head -8)
DAILY=$("$BIN" vault=wufu daily:read 2>/dev/null)

if [ -z "$RECENTS" ] && [ -z "$DAILY" ]; then
  jq -n '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:"Obsidian CLI reachable but returned no data (recents/daily:read empty)."}}'
  exit 0
fi

CONTEXT=$(printf 'Obsidian wufu vault - recent activity:\n%s\n\nToday'"'"'s daily note:\n%s' "$RECENTS" "${DAILY:-(empty)}")

jq -n --arg ctx "$CONTEXT" '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:$ctx}}'
