---
name: stage
description: >-
  Safely stage code changes, refactors, and new files directly into an isolated disk directory before applying them to the workspace repository.
  Supports graphical side-by-side diff review via the 'staged' CLI tool (e.g. 'staged diff <file>', 'staged diff <file> -a').
  Use when the user asks to stage changes first, preview proposed edits, review a patch without touching the git working tree, or keep the agent in an isolated staging sandbox until explicitly approved.
---

# Staging Workflow (`/stage`)

This skill defines a safety-first, non-intrusive development workflow where the **session artifacts/staging directory serves as the isolated workspace**. All proposed source code modifications, new files, and refactors are physically created and edited inside the staging directory first, allowing full review of real files before anything is applied to the workspace.

---

## 1. Core Principles

- **Isolated Staging Folder as Workspace**: The staging folder acts as the working directory during the staging phase. Real files (`.svelte`, `.ts`, `.js`, `.py`, `.css`, etc.) are created and modified there on disk, mirroring relative project paths.
  - Antigravity staging path: `<appDataDir>/brain/<conversation-id>/staging/`
  - Cursor staging path: `~/.local/share/cursor-artifact-staging/<conversation-id>/staging/` (or `~/.local/share/stage/<conversation-id>/staging/`)
- **No Git Worktree Needed**: The staging directory provides complete file-level isolation without allocating or switching git worktrees. Running dev servers (`npm run dev`, Vite) and editor buffers remain completely undisturbed.
- **Real Files, Not Markdown Text Diffs**: Never output raw markdown diff blocks (` ```diff `) as a substitute for code changes. Write the actual updated or new source files to the staging directory so they are valid, inspectable files with syntax highlighting and language tooling.
- **Side-by-Side Review Dashboard (`staged_changes.md`)**: A dedicated review artifact is maintained with direct clickable links (`file:///`) to both the **staged file** and the **original workspace file**.
- **Mandatory Conversation ID Output**: Every `/stage` review dashboard (`staged_changes.md`) and summary response **MUST prominently print the active Conversation ID** at the top so the user can easily copy and switch between sessions or use `staged use <session_id>`.
- **Side-by-Side Graphical Diffing (`staged diff <file>`)**: The user or agent can launch graphical diff tabs directly in their editor with `staged diff <file>` and apply them directly with `staged diff <file> -a`.
- **Explicit Apply Gating**: No workspace files may be touched until the user explicitly approves or asks to apply.

---

## 2. Directory Structure

Inside the conversation's storage directory (`<appDataDir>/brain/<conversation-id>/`):

```
<conversation-id>/
├── staged_changes.md          # Review dashboard artifact (RequestFeedback: true)
├── .workspace                 # Absolute path to repository root
├── renames.json               # Optional tracking of moves, renames, and deletions
└── staging/                   # Staging workspace root (mirrors project paths)
    ├── src/
    │   ├── lib/
    │   │   └── theme.ts       # Staged file on disk
    │   └── routes/
    │       └── layout.css     # Staged file on disk
    └── ...
```

---

## 3. Step-by-Step Procedure

### Step 1: Research & Setup Staging Target

1. Read the target workspace files using read tools (`view_file`, `grep_search`, `list_dir`).
2. Identify the active conversation artifact directory:
   `<appDataDir>/brain/<conversation-id>/`
3. Identify the target paths in the staging directory:
   `<appDataDir>/brain/<conversation-id>/staging/<relative-workspace-path>`

### Step 2: Implement Changes in the Staging Workspace

1. **Modified Files**: Write the complete updated file to `staging/<relative-path>`. For incremental adjustments, edit only that staged path.
2. **New Files**: Write the new file directly into `staging/<relative-path>`.
3. **Renames / Moves**: Update `renames.json` so the new relative path maps to the old workspace path.
4. **Deletions**: Note pending deletions in `renames.json` under `"_deletions": ["path/..."]`. Do not delete workspace files yet.

### Step 3: Create the Review Dashboard Artifact (`staged_changes.md`)

1. Create or update `staged_changes.md` in `<appDataDir>/brain/<conversation-id>/staged_changes.md`.
2. Set `ArtifactMetadata` with `UserFacing: true` and `RequestFeedback: true`.
3. Include the mandatory header with the Session ID:

   ```markdown
   # Staged Changes Review

   > **Session ID:** `<conversation-id>`  
   > **Active Branch:** `<branch>`  
   > **Staging Path:** `<staging-dir>`

   Quick Switch: `staged use <conversation-id>`  
   Quick Apply: `staged diff <filename> -a`
   ```

4. Provide a clickable table of all staged files and workspace originals.

### Step 4: Await Review & Refine

1. Notify the user that changes are staged, provide the clickable links, and stop.
2. If refinements are requested, edit files in `staging/` and update `staged_changes.md`.
3. **Never apply changes to the workspace** until the user explicitly requests it.

### Step 5: Clean Apply

Once approved by the user:

1. Run `staged diff all -a` (or `staged apply all`).
2. Run project formatting and linting: `npm run format && npm run lint && npm run check`.
