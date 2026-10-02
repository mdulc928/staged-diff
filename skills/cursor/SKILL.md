---
name: stage
description: >-
  Stages proposed code as real files under ~/.local/share/stage (or ~/.local/share/cursor-artifact-staging) before anything is written to the workspace, with side-by-side review via the 'staged' CLI tool.
  Use when the user asks to stage changes first, preview edits before applying them, review a patch without a git worktree, or keep the agent from touching the working tree until approved.
---

# Staging Workflow (Cursor) — `/stage`

The staging directory is the only place you edit proposed code. The git workspace stays read-only for writes until the user explicitly asks to apply.

**Do not switch to Plan mode.** **Do not ask the user to click Build.** Build would edit the real repo; this workflow forbids that until apply.

The user may stay in Agent mode (or any mode). Your job is to copy targets into staging, change the staged copies, and stop.

## Sandbox vs approvals (Cursor)

**Filesystem:** proposed files go under `~/.local/share/stage/...` (or legacy `~/.local/share/cursor-artifact-staging/...`).
Cursor write-protects `~/.cursor` except `rules/`, `commands/`, `worktrees/`, `skills/`, and `agents/`. Staging stays outside `~/.cursor` so a sandboxed shell can write it. That path must appear in `~/.cursor/sandbox.json` → `additionalReadwritePaths` (keep `type`: `workspace_readwrite`). After changes, start a **new agent session** so the sandbox reloads.

**Cursor CLI:** [CLI permissions](https://cursor.com/docs/cli/reference/permissions) in `~/.cursor/cli-config.json` need both `Read(...)` and `Write(...)`. Run `staged install-skill --tool cursor --default` to configure both automatically.

## Staging root

```
~/.local/share/stage/<conversation-id>/
├── .workspace                 # absolute git root, one line — write this first
├── renames.json               # optional: moves, renames, deletions
├── staged_changes.md          # review summary for the user (always include Session ID!)
└── staging/                   # mirrors workspace-relative paths — edit here only
    └── src/...
```

**Conversation id:** UUID from the current agent store (`.../cursor_agent_stores/<conversation-id>/files`) or transcript.

## Absolute paths only (critical)

The Write/StrReplace tool path must be a **full absolute path** starting with `/Users/.../.local/share/stage/` (or `.../cursor-artifact-staging/`).

- **Correct:** `/Users/<you>/.local/share/stage/<conversation-id>/staging/src/foo.ts`
- **Wrong:** `.local/share/stage/...` (creates `./.local` inside git repo)
- **Wrong:** `~/.local/...` if tool resolves relative to workspace

## Hard rules

1. **Read** workspace files with read tools only.
2. **Write and edit** only absolute paths under the staging directory.
3. **Never** create, modify, or delete files inside the workspace repo for this task — including after the user approves the approach in chat.
4. **Never** call SwitchMode to Plan.
5. **Never** run apply or copy staged files into the workspace unless the user clearly asks to apply.
6. **Mandatory Conversation ID:** Always print the Session ID in `staged_changes.md` and chat.

## Review and diffs

`staged` lists sessions and opens diffs with `cursor -r --diff <workspace-original> <staged>`:

```bash
staged                          # List staged files and status tags
staged diff <filename>          # Open side-by-side diff in Cursor
staged diff <filename> -a       # Apply this specific file's changes
staged diff all -a              # Apply all staged files
staged clean                    # Purge staging directory
```

## After explicit apply request

From the workspace git root:

1. `staged diff all -a` (or `staged apply all`)
2. Run project validation: `npm run format && npm run lint && npm run check`
3. Ask before committing
