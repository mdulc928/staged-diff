---
name: stage
description: >-
  Stages proposed code as real files under ~/.local/share/stage before anything is written to the workspace, with diff review via the 'staged' CLI tool.
  Use when the user asks to stage changes first, preview proposed edits in Codex, review a patch without touching the git working tree, or keep the agent in an isolated staging sandbox until explicitly approved.
---

# Staging Workflow (Codex CLI) — `/stage`

The staging directory is the only place you edit proposed code. The git workspace stays read-only for writes until the user explicitly asks to apply.

Your job is to prepare target changes in staging, edit the staged copies, and stop for user review.

---

## 1. Staging Directory Structure

Files are staged under:

```
~/.local/share/stage/<session-id>/
├── .workspace                 # Absolute git root (single line — write this first)
├── renames.json               # Optional tracking of moves, renames, and deletions
├── staged_changes.md          # Review summary for the user (always include Session ID!)
└── staging/                   # Mirrors workspace-relative paths — edit here only
    └── src/...
```

- **Session ID:** Use the current session or task UUID.
- **Absolute Paths Only:** Always use full absolute paths when creating or modifying files in `staging/`.

---

## 2. Hard Rules

1. **Read-Only Workspace:** Never write directly to the working tree. Use read tools to inspect files.
2. **Write Only in Staging:** Create new files or edits in `~/.local/share/stage/<session-id>/staging/<relative-path>`.
3. **Workspace File:** Write `~/.local/share/stage/<session-id>/.workspace` first containing the absolute repository root.
4. **Mandatory Session ID:** Print the active Session ID in the summary response.
5. **Explicit Apply Gating:** Never apply staged changes to the repository until the user explicitly approves.

---

## 3. CLI Diffing & Review

Review staged files using `staged`:

```bash
staged                          # List staged files and statuses
staged diff <filename>          # Terminal diff (git diff --no-index)
staged diff <filename> -a       # Apply this specific file
staged diff all -a              # Apply all staged files
staged clean                    # Purge staging directory
```

---

## 4. Applying Changes

Once the user explicitly confirms to apply:

1. Run `staged diff all -a` (or `staged apply all`).
2. Run project verification and tests.
3. Always ask for explicit confirmation before committing.
