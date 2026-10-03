---
name: stage
description: >-
  Stages proposed code as real files under ~/.local/share/stage before anything is written to the workspace, with terminal diff review via the 'staged' CLI tool.
  Use when the user asks to stage changes first, preview proposed edits in terminal, review a patch without touching the git working tree, or keep Claude Code in an isolated staging sandbox until explicitly approved.
---

# Staging Workflow (Claude Code) — `/stage`

The staging directory is the only place you edit proposed code. The git workspace stays read-only for writes until the user explicitly asks to apply.

Your job is to copy targets into staging, write or edit the staged copies, and present them for review.

---

## 1. Staging Root & Paths

Files are staged under the user's local staging area:

```
~/.local/share/stage/<session-id>/
├── .workspace                 # Absolute git root (single line — write this first)
├── renames.json               # Optional tracking of moves, renames, and deletions
├── staged_changes.md          # Review summary for the user (always include Session ID!)
└── staging/                   # Mirrors workspace-relative paths — edit here only
    └── src/...
```

- **Session ID:** Use the current Claude conversation/session identifier or UUID.
- **Absolute Paths Only:** When modifying or writing files using file tools, always provide full absolute paths starting with `~/.local/share/stage/<session-id>/staging/...`.

---

## 2. Hard Rules

1. **Read-Only Workspace:** Read original repo files using read tools (`View`, `Grep`, `Glob`, `cat`). Never write directly to the working tree.
2. **Write Only in Staging:** Write new files or edits exclusively into `~/.local/share/stage/<session-id>/staging/<relative-path>`.
3. **Write `.workspace` First:** Ensure `~/.local/share/stage/<session-id>/.workspace` contains the absolute path to the repository root.
4. **No Git Worktree Needed:** Keep dev servers and the user's active editor untouched.
5. **Print Session ID:** Always display the Session ID prominently so the user can inspect or lock the session with `staged use <session-id>`.
6. **Explicit Apply Gating:** Never apply staged changes to the repository until the user explicitly requests it.

---

## 3. Terminal Diffing & Review

The user can inspect staged files using the `staged` CLI directly from their terminal:

```bash
staged                          # List all staged files and statuses
staged diff <filename>          # View terminal diff (git diff --no-index)
staged diff <filename> -a       # Apply this specific file
staged diff all -a              # Apply all staged files
staged clean                    # Purge staging directory
```

---

## 4. Applying Changes

Once the user explicitly confirms to apply:

1. Run `staged diff all -a` (or `staged apply all`).
2. Run project verification: `npm run format && npm run lint && npm run check` (or equivalent test runner).
3. Always ask for explicit confirmation before committing.
