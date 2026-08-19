#!/bin/bash
# SessionStart hook: auto-activate caveman mode by injecting the skill body
# directly into context, equivalent to the user typing /caveman at turn 1.

SKILL_FILE="$CLAUDE_PROJECT_DIR/.claude/skills/caveman/SKILL.md"

if [ ! -f "$SKILL_FILE" ]; then
  jq -n '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:"caveman-session-start hook: SKILL.md not found, skipped."}}'
  exit 0
fi

# Strip YAML frontmatter (between the two --- lines), keep body only.
BODY=$(awk '/^---$/{n++; next} n>=2' "$SKILL_FILE")

CONTEXT=$(printf 'User has caveman mode enabled by default for every session in this project. Apply these rules from turn one, no /caveman invocation needed:\n\n%s' "$BODY")

jq -n --arg ctx "$CONTEXT" '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:$ctx}}'
