---
name: stage
description: >-
  Stages proposed code as real files under ~/.local/share/stage before anything is written to the workspace, with native JetBrains graphical diff review via the 'staged' CLI tool in PyCharm.
  Use when the user asks to stage changes first, preview proposed edits in PyCharm / JetBrains, review a patch without touching the git working tree, or keep the AI assistant in an isolated staging sandbox until explicitly approved.
---

# Staging Workflow (PyCharm / JetBrains) — `/stage`

The staging directory is the only place you edit proposed code. The git workspace stays read-only for writes until the user explicitly asks to apply.

Your job is to prepare target changes in staging, edit the staged copies, and present them for review.

---

## 1. Staging Root & Structure

Files are staged under:

```
~/.local/share/stage/<conversation-id>/
├── .workspace                 # Absolute git root (single line — write this first)
├── renames.json               # Optional tracking of moves, renames, and deletions
├── staged_changes.md          # Review summary for the user (always include Session ID!)
└── staging/                   # Mirrors workspace-relative paths — edit here only
    └── src/...
```

- **Conversation ID:** Current JetBrains AI Assistant / Junie session ID or UUID.
- **Absolute Paths Only:** Always use full absolute paths starting with `~/.local/share/stage/<conversation-id>/staging/...`.

---

## 2. Hard Rules

1. **Read-Only Workspace:** Never edit workspace files directly. Inspect files with read tools only.
2. **Write Exclusively in Staging:** All file writes and modifications must target `~/.local/share/stage/<conversation-id>/staging/<relative-path>`.
3. **Workspace File:** Ensure `~/.local/share/stage/<conversation-id>/.workspace` exists with the absolute repository path.
4. **Print Session ID:** Always display the Session ID prominently so the user can inspect or lock the session with `staged use <conversation-id>`.
5. **Explicit Apply Gating:** Never apply staged changes to the workspace until the user explicitly requests it.

---

## 3. Graphical Diffing in PyCharm

`staged` invokes the native JetBrains two-way diff window via the `pycharm` launcher (ensure `pycharm` is in PATH via PyCharm > Tools > Create Command-line Launcher):

```bash
staged                          # List staged files and status tags
staged diff <filename>          # Open side-by-side graphical diff in PyCharm
staged diff <filename> -a       # Apply this specific file
staged diff all -a              # Apply all staged files
staged clean                    # Purge staging directory
```

---

## 4. Applying Changes

Once the user explicitly confirms to apply:

1. Run `staged diff all -a` (or `staged apply all`).
2. Run project verification: `npm run format && npm run lint && npm run check`.
3. Always ask for confirmation before committing.
