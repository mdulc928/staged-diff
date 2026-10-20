# Guide to staged

`staged` isolates an AI agent's proposed edits in a dedicated directory outside your Git working tree.

The workflow is straightforward:

- **Isolate**: The agent writes complete proposed files to a session directory on disk.
- **Engage**: You work through your own solution in your editor, unblocked and uninterrupted.
- **Review & Apply**: When you are ready, review a side-by-side diff in your IDE and apply only the files you want.

For a high-level summary, see [README.md](README.md). This guide covers the complete workflow, CLI commands, and configuration options.

---

## Contents

- [Installation and Setup](#installation-and-setup)
- [Preparing Proposals](#preparing-proposals)
- [Managing Sessions](#managing-sessions)
- [Reviewing Changes](#reviewing-changes)
- [Interactive Terminal Diff TUI](#interactive-terminal-diff-tui)
- [Managing Feedback & AI Agent Handoff](#managing-feedback--ai-agent-handoff)
- [Zero-Replication Formatting, Linting & Ephemeral Sandbox](#zero-replication-formatting-linting--ephemeral-sandbox)
- [Applying Changes, 3-Way Merge & Rollback (Undo)](#applying-changes-3-way-merge--rollback-undo)
- [Renames, Moves, and Deletions](#renames-moves-and-deletions)
- [Cross-Session Comparison & Migration](#cross-session-comparison--migration)
- [Cleaning Up](#cleaning-up)
- [Editor & Storage Configuration](#editor--storage-configuration)
- [Shell Completion](#shell-completion)
- [Command Reference](#command-reference)
- [Troubleshooting](#troubleshooting)
- [Development and Testing](#development-and-testing)

---

## Installation and Setup

### Prerequisites

- **Python 3.8+** (standard library only; no pip packages or virtual environment required).

---

### Installing the CLI

#### Quick Setup (npm)

If you prefer managing command shims automatically through Node/npm:

```bash
# Clone the repository
git clone https://github.com/mdulc928/staged-diff.git
cd staged-diff

# Install global launcher shim pointing to local engine
npm install -g .

# Verify CLI is available on PATH
staged --help
```

Requires Node.js 18+ and Python 3.8+. The wrapper locates Python on your PATH (or `py -3` on Windows) and forwards commands to the Python engine.

#### Manual Setup: macOS or Linux

```bash
# Clone the repository
git clone https://github.com/mdulc928/staged-diff.git
cd staged-diff

# Create user bin directory if needed
mkdir -p ~/.local/bin

# Symlink staged executable into user PATH
ln -s "$(pwd)/staged" ~/.local/bin/staged

# Verify CLI is available on PATH
staged --help
```

> [!NOTE]
> Ensure `~/.local/bin` is in your shell `PATH`. Keep the cloned repository intact; the symlink references this directory, and skill installation reads `SKILL.md` directly from it.

#### Manual Setup: Windows (PowerShell)

Windows uses a small `staged.cmd` launcher rather than a symlink. PowerShell is the supported Windows shell:

```powershell
# Clone the repository
git clone https://github.com/mdulc928/staged-diff.git
cd staged-diff

# Install the Windows launcher (%APPDATA%\staged\bin)
py -3 .\staged install-launcher

# Install PowerShell completions
py -3 .\staged install-completion --shell powershell
```

1. Add the printed launcher folder (usually `%APPDATA%\staged\bin`) to your user **Path** environment variable.
2. To use `staged` immediately in your active PowerShell session:
   ```powershell
   $env:Path += ";$env:APPDATA\staged\bin"
   ```
3. Add the printed completion loader command to your `$PROFILE` to load completions automatically.

> [!NOTE]
> On Windows, `--shell powershell` also offers launcher setup once if no launcher is available. Declines are remembered; PATH changes remain manual.

---

### Installing the Agent Skill

The `stage` skill teaches your AI harness how to create and manage proposal files outside your working tree. Install it for your editor:

```bash
# Cursor: install skill, set default review tool, and configure sandbox permissions
staged install-skill --tool cursor --default --configure-sandbox

# Codex: install skill into ~/.agents/skills/stage (terminal diff fallback)
staged install-skill --tool codex

# Claude Code: install skill into ~/.claude/skills/stage (terminal diff fallback)
staged install-skill --tool claudecode

# Windsurf / Cascade: install skill into ~/.codeium/windsurf/skills/stage
staged install-skill --tool windsurf

# Antigravity: install skill into ~/.gemini/config/skills/stage
staged install-skill --tool antigravity

# Zed: install skill into ~/.agents/skills/stage
staged install-skill --tool zed
```

- `--default`: Sets the selected harness as your default review tool.
- Codex and Claude Code default to terminal review (`--tool cli`).

| Harness          | Skill Destination                  |
| ---------------- | ---------------------------------- |
| Cursor           | `~/.cursor/skills/stage`           |
| Codex            | `~/.agents/skills/stage`           |
| Claude Code      | `~/.claude/skills/stage`           |
| Windsurf/Cascade | `~/.codeium/windsurf/skills/stage` |
| Antigravity      | `~/.gemini/config/skills/stage`    |
| Zed              | `~/.agents/skills/stage`           |

For VS Code, PyCharm, or custom harnesses, pass `--target-dir` pointing to the exact directory that will contain `SKILL.md`:

```bash
# Custom harness or VS Code: install SKILL.md into custom directory
staged install-skill --tool vscode --target-dir /absolute/path/to/skills/stage
```

---

### Sandbox & Storage Permissions

The installer prints the absolute path to your staging root. Your AI agent requires write permissions to this directory.

- **Cursor**: `--configure-sandbox` automatically adds the staging root to `~/.cursor/sandbox.json` and `~/.cursor/cli-config.json`.
- **Other harnesses**: Add the printed staging root to the harness's writable directory settings manually.

> [!IMPORTANT]
> Installing a skill instructs the agent to isolate files, but does not enforce a read-only workspace. Your editor's sandbox settings control filesystem permissions.

---

### Uninstalling

```bash
staged uninstall
```

Confirms removal of the CLI and installed integrations, then asks separately whether
to permanently delete sessions across all known roots and repositories. Sessions
are kept by default; deleting them discards unapplied proposals too.

For scripts, choose explicitly:

```bash
staged uninstall --yes --keep-sessions
staged uninstall --yes --remove-sessions
```

`--yes` confirms uninstall only. Without a terminal, it keeps sessions unless
`--remove-sessions` is supplied. Use `--root <path>` for an old session root that
is no longer configured. Session paths are listed before cleanup.

Uninstall removes the matching global npm package or manual command symlink,
the Windows launcher, completion hooks, installed skills, global settings, and
shell bindings. It preserves source checkouts, workspace files, unrelated files,
and modified installed skills. New installations record custom skill/profile
locations and sandbox permissions they add so uninstall can undo them. Older
unrecorded custom skills and sandbox permissions need manual cleanup.
Uninstall also removes the exact PowerShell loader from standard profile locations
and the dedicated Windows launcher directory from user PATH. Custom profile locations
need manual cleanup. Open a new terminal afterward to refresh PATH and completions.

## Preparing Proposals

### Using an AI Agent

In your editor or chat panel, instruct the agent:

> Use /stage to prepare these changes for review before applying them.

The agent initializes a session, writes complete proposed files to the staging directory, and reports the session ID.

Bind the session in your terminal:

```bash
cd /path/to/project
staged use --session <id>
staged
```

The agent maintains a `staged_changes.md` dashboard alongside the staged files with summary notes, file links, and test results.

---

### Manual Staging (Without an Agent)

You can also use `staged` manually to sandbox experimental changes or refactors:

```bash
staged init my-experiment --json
```

1. Write complete files to the returned `staging/` path, matching workspace-relative paths (e.g. `<staging-path>/src/example.py`).
2. Staging copies only the files you specify. It does not duplicate your entire repository, build artifacts, or dependencies.

---

### Reading & Writing Staged Files

Use `read` and `write` when an agent needs file access through the CLI:

```bash
# Read lines from the workspace original, including its full-file hash
staged read --workspace -f src/example.py --start-line 10 --end-line 20 --json

# Stage the original and replace those lines with input from a file
staged write -f src/example.py --from-workspace --start-line 10 --end-line 20 --stdin < replacement.txt

# Insert text before a staged line
printf '# Review this section\n' | staged write -f src/example.py --start-line 10 --stdin

# Read bytes, then replace a byte range in the staged file
staged read -f src/example.py --offset 0 --length 128
staged write -f src/example.py --offset 0 --delete-count 12 --text 'replacement'

# Replace a complete staged file, or append to it
staged write -f notes.txt --text 'Draft notes'
staged write -f notes.txt --append --text ' — revised'
```

- **Paths**: Exact relative paths; no fuzzy matching. Reads default to staging; `--workspace` reads originals. Writes stay inside `staging/`.
- **First Edit**: `--from-workspace` copies a missing staged file before editing and preserves permissions. Omit it for later edits.
- **Byte Ranges**: Offsets start at 0. `--length` limits reads; `--delete-count` selects bytes to replace. An offset without a delete count inserts bytes.
- **Line Ranges**: Lines start at 1; end lines are inclusive. Reads without an end line continue to EOF. Writes without an end line insert before the start line.
- **Input**: Supply `--text` or `--stdin`. No range replaces the entire file; empty input clears it. Newlines are never added automatically.
- **Conflict Check**: Pass `--expect-sha256 <hash>` from `read --json` to reject stale edits. This checks content; it does not lock other writers.
- **Output**: Reads emit raw bytes. `--json` adds range information and the full-file SHA-256; non-UTF-8 slices use Base64 with `encoding: "base64"`.
- **Preservation**: Edits preserve bytes outside the selected range, including CRLF. Line boundaries use LF; byte ranges can split UTF-8 characters.
- **Session metadata**: Add `--meta` to read or write a top-level session file such as `staged_changes.md`. `write --meta` creates the file when it is absent. `--meta` cannot be combined with `--workspace` or `--from-workspace`, and it cannot modify `session.json`, `renames.json`, or `summaries.json`.
- **Consistent selection**: Any command that accepts `-f` also accepts `--meta`. On metadata-capable commands it switches completion and file access to session metadata. On proposal-only commands (`--summary`, `summarize`, `diff`, `apply`, `clean`, and `migrate`) it is accepted as a no-op and `-f` continues to complete proposal paths.

```bash
staged write --meta -f staged_changes.md --text '# Session notes'
staged read --meta -f staged_changes.md
```

The bundled skill teaches agents these commands. Approve the staging directory for writes and workspace reads in the host sandbox; command approval alone does not grant filesystem access. Session setup and review metadata also need access to the session directory and staged configuration. Limit agent command approvals to the intended subcommands: approving all of `staged` also permits commands such as `apply`.

---

### Creating, Copying, Renaming, and Deleting Proposals

Use managed exact-path operations instead of direct filesystem access:

```bash
staged create -f src/new.py                          # New empty staged file
staged create -f src/new.py --stdin < contents.py    # Alternative: create with bytes
staged copy --workspace -f src/example.py            # Workspace → staging
staged copy --workspace -f src/example.py --to src/variant.py
staged copy -f src/example.py --to src/second.py      # Staging → staging
staged rename -f src/example.py --to src/renamed.py
staged delete -f src/second.py                       # Discard the staged proposal
staged delete -f src/obsolete.py --workspace          # Propose a workspace deletion
staged create --meta -f notes.md --text 'Review notes'
staged rename --meta -f notes.md --to review.md
staged delete --meta -f review.md
```

These examples are alternatives. `--meta` keeps the same commands on session files beside `staging/`. `copy --meta` requires `--to`. Metadata destinations that already exist must be edited with `write --meta`. After each operation, agents update summaries before making another modification. All four commands accept `--json`; create accepts `--text` or `--stdin`, or creates an empty file when neither is provided. JSON results identify `operation` and `path`; create/copy/rename also return `size` and `sha256`, with `source` and `source_root` for copy/rename. Delete returns `mode: "discard"`, `"propose"`, or `"meta"`.

All destinations are staged. `copy` reads from staging unless `--workspace` is present; `--to` defaults to the source's relative path. There is no staging-to-workspace copy option: use an explicitly authorized `apply` for workspace writes. Copies preserve bytes and permission bits, do not inherit rename mappings or summaries, and leave their source intact. `create` can stage a replacement for a real workspace file if that path is not already staged. To edit an existing proposal, use `write`.

`rename` requires a staged source and a new `--to` path. It moves the file's summary and preserves the original workspace path in the rename manifest, including across repeated renames. Renaming back to the original path removes the rename mapping. New proposals without a workspace original remain new files. The real workspace file stays in place until an authorized apply.

`delete` without `--workspace` discards the exact staged proposal, removes its file summary and manifest entry, or cancels a pending deletion. `delete --workspace` instead records `_deletions` for an existing real file and removes any staged replacement; the real file remains untouched until apply. It rejects overlaps with pending renames: discard those proposals first. After plain delete, revise only the session summary because the file summary was removed; after `delete --workspace`, summarize the deletion path and session as usual.

Create, copy, and rename refuse staged destination collisions and pending-operation conflicts. Rename also refuses unrelated workspace destination collisions. These APIs reject traversal, symlinks, special files, and staging inside the workspace. They validate relevant metadata before editing. Publishing new files uses filesystem hard links to avoid replacing an existing destination, so the staging filesystem must support hard links. There is no multi-file transaction or cross-process locking guarantee; do not run simultaneous edits to the same session.

---

### Maintaining Change Summaries

Immediately after each modification, update that file's summary and the session summary before making another edit. Summaries describe the cumulative proposal in one sentence when possible, with at most three short sentences each. Keep detailed review notes and validation in `staged_changes.md`.

```bash
staged summarize -f src/example.py -m 'Handle empty input without raising an error.' --session-message 'Make input handling tolerate empty values.'
staged summarize -m 'Make input handling tolerate empty values.'
staged summarize -f src/example.py --stdin < summary.txt
staged summarize -f src/example.py --clear
staged --summary                         # Read every summary
staged --summary -f src/example.py       # Read one file summary
staged --summary --json                  # Read the stored summaries object
```

`summarize` writes. `staged --summary` reads. `-m` is short for `--message`. Omit `-f` to update the session summary; use an exact relative path to update or read a file summary. For a rename, use the destination; for a deletion, use the original path. Pair a file update with `--session-message` to update both atomically. `--message`, `--stdin`, and `--clear` are mutually exclusive, and one of them is required. `--stdin` expects UTF-8 text on every platform; invalid bytes leave the summaries unchanged. Empty text clears the selected summary. Reading with `--summary` does not create metadata. `--json` after a write returns the complete object. `staged --summary --json` returns that same object; add `-f <path>` to return `session`, `path`, and `summary` for that file. File and path arguments consistently use `-f` / `--file` and support completion. Session metadata file access additionally requires `--meta`.

`staged --summary` prints the session id, then the session summary, then each file with its status. Files are grouped by status (`MISSING`, `NEW FILE`, `MODIFIED`, `RENAMED`, `RELOCATED`, `DELETED`, `APPLIED`), with a blank line between groups. Each summary is indented and wrapped, with a blank line after it, so several summaries do not run together. Status tokens and paths are colored when the terminal supports color. The overview uses the same status groups, with a blank line between them.

The CLI stores agent-authored summaries in `summaries.json` beside `staging/`, preserving unrelated entries:

```json
{
  "session": "Make input handling tolerate empty values.",
  "files": {
    "src/example.py": "Handle empty input without raising an error."
  }
}
```

The CLI does not generate summaries or enforce sentence counts. Older sessions display `(no summary yet)` until summaries are supplied. Agents using an older CLI can maintain this JSON directly after each edit.

Cleaning a file removes its summary; cleaning all changes or the last file also clears the session summary. Applying leaves summaries available for review. Migration carries the selected file summaries and removes stale destination summaries when an incoming file has none. A full migration into an empty session also copies the source session summary if the destination has none; partial migrations preserve the destination session summary. After partial cleanup or migration, revise the session summary immediately to describe the current proposal.

---

## Managing Sessions

### Shell Binding

Bind a session to your active terminal to avoid specifying `--session` on each command:

```bash
staged use --session my-conversation  # Bind session to current shell
staged                                # Overview of active session
staged diff -f src/example.py         # Diff file in active session
staged use --clear                    # Clear session and tool bindings
```

- **Prefix matching**: You can pass a unique prefix (e.g., `staged use --session abc`). Ambiguous prefixes list candidate sessions and exit safely.
- **Repository scoping**: Sessions are strictly scoped to the current repository root.

---

### Session Discovery

```bash
staged --sessions                       # List sessions with their change summaries
staged -R                               # List all sessions across all workspaces, including removed workspaces
staged --session other-id diff -f f.py  # One-time session override
```

Session IDs from `staged -R` are global: pass the full UUID (or at least eight hex characters) to `--session`, `--from`, or `--to` from any directory. Short names like `foreign` still resolve only in the current workspace.

---

### Persistent Defaults

```bash
staged set --default-session <id>         # Set global default session
staged set --repo --default-session <id>  # Set repository-level default
```

**Selection Precedence** (first match wins):

1. Command-line flag (`--session <id>`)
2. Environment variables (`STAGED_SESSION`, `STAGED_TOOL`)
3. Active shell binding (`staged use`)
4. Repository configuration (`<workspace>/.staged.json`)
5. Global configuration (`state.json`)
6. Auto-detection (newest session for the active workspace)

> [!TIP]
> If an AI agent creates a new subshell for each tool call, set a consistent `STAGED_SHELL_ID` environment variable across calls, or run `staged use --session <id>` at the beginning of each command.

---

## Reviewing Changes

### Inspecting Status & Diffs

```bash
staged                          # Concise overview
staged diff -v                  # Session and file summaries, paths, sizes, timestamps
staged diff -f src/example.py   # Open visual diff in configured editor
staged diff --all -v            # Print summaries and review all pending changes
staged diff -f src/example.py --tool cli # Open terminal diff
```

- `staged diff` without a selection shows the overview; add `-v` / `--verbose` for session and per-file summaries. With `-f <path>` or `--all`, it opens diffs in your configured editor, and `-v` prints summaries before each diff. Cross-session verbose review labels both sessions and their file summaries. It does not modify workspace files unless `-a` is passed.
- Additions and deletions are compared against an empty placeholder file so GUI editors can display a two-sided diff.
- `--tool cli` invokes `difft` (Difftastic) if available, falling back to `git diff --no-index`, then standard `diff`.

---

### Status Indicators

| Status      | Color    | Meaning                                                                  |
| ----------- | -------- | ------------------------------------------------------------------------ |
| `MODIFIED`  | Yellow   | Staged file differs from workspace file                                  |
| `NEW FILE`  | Cyan     | File exists only in staging                                              |
| `RENAMED`   | Magenta  | File is renamed in manifest                                              |
| `RELOCATED` | Magenta  | File is moved to another folder without renaming                         |
| `DELETED`   | Red      | Workspace file is marked for deletion                                    |
| `APPLIED`   | Green    | Workspace file matches staged file byte-for-byte (and POSIX permissions) |
| `MISSING`   | Bold Red | Rename recorded in manifest, but staged destination file is missing      |

Color support follows `NO_COLOR=1` (disable) and `FORCE_COLOR=1` (force enable).

---

### File Matching

When targeting a file (`-f <query>`), matching resolves in order:

1. Exact relative path
2. Exact filename (`Button.svelte`)
3. Subsequence or distinctive substring (`Btn.sve`, `nested/btn`)
4. Bounded typo match

Ambiguous queries display all candidate matches and exit without making changes. Quote paths containing spaces.

---

### Extracting Raw Paths

```bash
staged path -s -f src/example.py   # Staged file absolute path
staged path -w -f src/example.py   # Workspace file absolute path
```

_(Deletions have only a workspace path. Inside `path`, `-s` means `--staged`; use `--session <id>` to specify a session.)_

---

## Interactive Terminal Diff TUI

Launch the built-in, zero-dependency interactive terminal diff viewer:

```bash
staged tui                    # Open TUI for all files in active session
staged tui -f src/example.py  # Open TUI focused on a specific file
staged diff --tui             # Alias via diff command
```

### 2-Pane Architecture

- **Left Rail (Files)**: Toggle with `b`. Lists proposed files with status badges, line delta stats (`+X -Y`), and open feedback counts (`💬2`). Press `Tab` to switch focus between the file rail and the diff viewport. Press `j`/`k` to navigate files and `Enter` to open the diff for that file.
- **Right Viewport (Diff)**:
  - Defaults to a **Side-by-Side 2-Pane Diff** (`Workspace Original` on the left, `Staged Proposal` on the right) with aligned hunk opcodes and intraline character/word-level highlight spans.
  - Press `u` to toggle between **Side-by-Side** and **Unified Diff**.
  - Press `H` to toggle optional syntax highlighting via Pygments (clean standard diff highlighting is used by default).

### Navigation & Inline Commenting Shortcuts

| Shortcut | Action |
| --- | --- |
| `j` / `k` (or Down/Up) | Move cursor down/up line by line |
| `n` / `p` | Jump to next / previous diff hunk |
| `[` / `]` | Switch active side (`workspace` vs `staged`) or cycle sub-line tokens |
| `v` (or Space) | Toggle **Visual Multi-line Selection** mode |
| `w` | Select individual sub-line token / expression (`snippet_focus`) on the line |
| `c` (or Enter) | Open inline comment input box anchored to selection; saves to `feedback.json` |
| `r` | Toggle `[OPEN]` / `[RESOLVED]` status on the comment at the cursor |
| `x` | Delete the comment at the cursor |
| `a` | **Selectively apply the current hunk** directly to the workspace |
| `A` | Apply the entire current file to the workspace |
| `y` | Format and copy all open feedback to clipboard as an agent prompt |
| `H` | Toggle optional syntax highlighting (uses Pygments if installed) |
| `?` or `h` | **Open built-in command reference & help screen** |
| `q` | Quit TUI (or close help screen) |

---

## Managing Feedback & AI Agent Handoff

Review comments created via `staged tui` or the CLI are persisted in `<session>/feedback.json` (a managed session metadata file).

### Inspecting & Exporting Feedback

```bash
staged feedback                           # List comments grouped by file with line ranges & quotes
staged feedback -f src/example.py         # Filter comments for one file
staged feedback --json                    # Machine-readable JSON
staged feedback --prompt                  # Format as copy-pasteable Markdown prompt for AI agents
staged feedback --copy                    # Copy prompt directly to system clipboard
```

### Adding & Managing Comments from CLI

```bash
# Add a comment anchored to lines 10-14 and specific expression
staged feedback --add -f src/app.py --start-line 10 --end-line 14 --select 'return 42' -m 'Avoid magic number 42'

# Resolve comments
staged feedback --resolve fb-1            # Mark comment fb-1 resolved
staged feedback --resolve-all             # Mark all comments resolved

# Delete comments
staged feedback --delete fb-1             # Remove comment fb-1
staged feedback --clear                   # Delete all comments
```

---

## Zero-Replication Formatting, Linting & Ephemeral Sandbox

Verify code quality, syntax, and builds in staging without copying or mutating the working tree.

### 1. In-Place Formatting (`staged fmt`)

Formats staged files using the project's exact workspace configuration (`.prettierrc`, `biome.json`, `pyproject.toml`, `.clang-format`, `rustfmt.toml`) via the virtual stdin trick (`--stdin-filepath <workspace_file>`):

```bash
staged fmt -f src/index.ts                # Format a single staged file in-place
staged fmt --all                          # Format all staged files in-place
staged fmt --all --check                  # Dry-run check: exit 0 if formatted, exit 1 if changes needed
staged fmt --cmd 'ruff format --stdin-filename {workspace_file} -' # Custom formatter template
```

### 2. Linting & Typechecking (`staged check`)

Runs syntax, type, and import checks using virtual workspace overlays:

```bash
staged check -f src/app.py                # Python AST syntax check + workspace import validation
staged check --all                        # Check all staged files
staged check --sandbox                    # Force check to run in Tier 3 overlay sandbox
```

- **Python**: Zero-dependency `ast.parse` validates syntax immediately; imports are checked against workspace files and staged deletions; when `ruff`/`flake8`/`mypy` are available, `PYTHONPATH` and `MYPYPATH` overlays allow staged modules to override workspace modules.
- **TypeScript**: Generates `.review/tsconfig.staged.json` extending workspace `tsconfig.json` with `compilerOptions.rootDirs: [staging, workspace]`, resolving cross-file imports without copying. Diagnostics paths are rewritten to clean relative paths.
- **C/C++**: Virtual include flags (`-I<staging> -I<workspace>`) ensure staged headers shadow workspace headers cleanly.
- **Java**: `-sourcepath <staging>:<workspace>` allows staged and workspace classes to compile together seamlessly.

### 3. Ephemeral CoW / Hardlink Sandbox (`staged exec`)

Runs arbitrary build commands, compilers, or test suites inside `<session>/.sandbox/`:

```bash
staged exec -- npm test                   # Run full test suite against proposed code
staged exec -- cargo check                # Check Rust project
staged exec -- make test                  # Run Makefile test targets
staged exec -- pytest tests/              # Run Python test suite
staged exec --sync -- npm run lint:fix    # Sync modified files back into staging/
staged exec --keep-sandbox -- make build  # Retain .sandbox/ directory for debugging
```

- **Millisecond Materialization**: Heavy directories (`node_modules`, `.venv`, `target`, `.build`, `.gradle`) are directory-symlinked; unchanged workspace files are hardlinked; staged files are copied; deletions are omitted.
- **Transparent Output**: Sandbox directory paths in stdout/stderr are rewritten back to clean relative paths so build diagnostics match your workspace.
- **Automatic Teardown**: The sandbox is completely cleaned up after command execution unless `--keep-sandbox` is passed.

---

## Applying Changes, 3-Way Merge & Rollback (Undo)

### Applying Proposals

```bash
staged apply -f src/example.py     # Apply a single file
staged apply --all                 # Apply all staged additions, edits, and deletions
```

**Shortcuts via `diff`**:

```bash
staged diff -f src/example.py -a   # Apply directly instead of opening diff
staged diff --all -a              # Apply all changes
staged diff -f src/example.py -ac  # Apply file, then clean its staged copy
```

---

### Conflict Safety & Automatic 3-Way Merge

- **Base Snapshots**: The first time an existing workspace file is touched by a staging command (`write`, `create`, `copy`, `rename`, `delete --workspace`), `staged` records a base snapshot in `<session>/.base/` and `<session>/.base.json`.
- **Automatic 3-Way Merge**: If you edit a workspace file concurrently after staging begins, `staged apply` detects the drift. If the changes do not overlap with the staged proposal, it automatically performs a clean 3-way merge (`[MERGED]`) using `git merge-file -p` (or pure-Python `difflib.SequenceMatcher` fallback).
- **Conflict Preflight Guard**: If changes overlap and conflict, `staged apply` halts during preflight before mutating any workspace files, warning you of the conflict. Pass `--force` to overwrite or `--no-merge` to disable automatic merging.

---

### Instant Rollback (`staged undo`)

Before any file in the workspace is modified, `staged apply` records an atomic snapshot in `<session>/.backups/`:

```bash
staged undo                               # Atomically roll back the most recent apply
staged undo --force                       # Roll back even if workspace files were edited after apply
staged undo --json                        # Output machine-readable JSON result
```

- Restores modified, merged, and deleted files to their exact pre-apply state and unlinks files that were newly created by the apply.
- If files were modified in the workspace *after* the apply, `staged undo` guards against accidental data loss and prompts for confirmation unless `--force` or `-y` is passed.

---

### Git Branch Guards

Two distinct checks prevent applying changes to unintended branches:

1. **Protected Branches**: Opt-in (empty list by default). If your current branch matches a protected pattern, apply is blocked unless overridden with `--force`.
2. **Changed Branches (Drift)**: If you staged changes on `feature/a` but your workspace is now on `feature/b`, apply pauses for confirmation. In scripts, pass `--yes`.

```bash
staged apply -f src/example.py --force # Override protected branch
staged apply -f src/example.py --yes   # Confirm applying on drifted branch
```

**Configuring Branch Protection**:

```bash
staged set --repo --protected-branch main --protected-branch 'release/*'
staged set --repo --branch-protection true
```

---

## Renames, Moves, and Deletions

Tracked in `<session-directory>/renames.json` beside `staging/`:

```json
{
  "src/new-name.py": "src/old-name.py",
  "lib/helper.py": "src/helper.py",
  "_deletions": ["src/obsolete.py"]
}
```

- **Renames & Moves**: Put the complete new file under `staging/` (e.g. `staging/src/new-name.py`). Map destination to original workspace path in `renames.json`. Apply writes the destination before deleting the original.
- **Deletions**: Add the relative workspace path to `_deletions`. Do not remove the workspace file while staging.
- **Constraints**: Normalized relative paths with forward slashes only. No symlinks, `.git` metadata, traversal (`../`), or overlapping rename chains (`A -> B` and `B -> C` in the same batch).

---

## Cross-Session Comparison & Migration

### Comparing Sessions

Compare the same file across two independent approaches:

```bash
# Compare two specific sessions
staged diff --between approach-a approach-b -f src/example.py

# Compare active session against another
staged diff -f src/example.py --compare-session approach-b
```

Pass `-f <file>` or `--all`. Deletions cannot be compared across sessions. Cannot be combined with `--apply` or `--clean`.

---

### Migrating Staged Files

Copy staged files, renames, and deletions from one session to another:

```bash
staged migrate --from approach-a --to approach-b -f src/example.py
staged migrate --from approach-a --to approach-b -f 'src/*.py'      # Quote globs
staged migrate --from approach-a --to approach-b --all             # All files
```

- If `--to` is omitted, targets the active session; if the current workspace has none, `staged` creates a new session there (same as `staged init` with a generated id).
- Source session and workspace files remain unchanged.
- If destination files exist or workspace files are newer, migration pauses. Pass `--force` to deliberately overwrite. _(Timestamp checks flag potential staleness; they do not perform a 3-way merge)._

---

## Cleaning Up

```bash
staged clean -f src/example.py     # Discard one staged file
staged clean --all                 # Discard all staged files in this session
staged clean --session             # Delete active session folder from disk
staged clean --session <id>        # Delete specific session folder
staged clean -f src/example.py -y  # Confirm discard noninteractively
```

- `clean --all`: Clears staged files and manifest, preserving session ID and origin branch.
- `clean --session`: Removes the session folder from disk and clears shell bindings.
- **Safety**: `clean` only removes files under the staging area; it **never** modifies workspace files.

---

## Editor & Storage Configuration

### Selecting a Review Editor

```bash
staged use --tool cursor              # Set for current shell
staged set --tool cursor --default     # Set global default
staged set --repo --tool zed          # Set repository-level default
staged diff -f file.py --tool pycharm # One-time override
```

Built-in tools: `cursor`, `antigravity`, `windsurf`, `vscode`, `pycharm`, `zed`, `codex`, `claudecode`, `cli`.

---

### Editor Compatibility

| Adapter       | CLI Command                             | Documentation / Evidence                                                                           |
| ------------- | --------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `vscode`      | `code -r -d original staged`            | [VS Code CLI Docs](https://code.visualstudio.com/docs/configure/command-line)                      |
| `pycharm`     | `pycharm diff original staged`          | [PyCharm Diff Viewer](https://www.jetbrains.com/help/pycharm/command-line-differences-viewer.html) |
| `zed`         | `zed --diff original staged`            | [Zed CLI Reference](https://zed.dev/docs/reference/cli)                                            |
| `cursor`      | `cursor -r --diff original staged`      | Verified in local CLI parser                                                                       |
| `antigravity` | `antigravity-ide -r -d original staged` | Verified in local CLI parser                                                                       |
| `windsurf`    | `windsurf -r --diff original staged`    | Standard vendor CLI flags                                                                          |

Codex, Claude Code, and CLI use terminal diffs (`difft`, `git diff`, `diff`). For PyCharm, `staged` checks standard PATH names, JetBrains Toolbox scripts, macOS application bundles, and Linux Snap packages.

---

### Custom Editor Configuration

Register any diff tool that supports a command-line interface:

```bash
staged set-tool my-editor --name 'My Editor' \
  --diff-cmd 'my-editor --diff {orig} {staged}' \
  --open-cmd 'my-editor {staged}'
staged use --tool my-editor
```

Both `{orig}` and `{staged}` are required for `--diff-cmd`; `{staged}` is required for `--open-cmd`. Custom IDs cannot overwrite built-in adapters.

---

### Storage Locations

| Data                   | macOS / Linux / WSL           | Windows Native                |
| ---------------------- | ----------------------------- | ----------------------------- |
| Sessions               | `~/.local/share/staged`       | `%LOCALAPPDATA%\staged`       |
| Global preferences     | `~/.config/staged/state.json` | `%APPDATA%\staged\state.json` |
| Repository preferences | `<workspace>/.staged.json`    | Same layout                   |

POSIX systems respect `XDG_DATA_HOME` and `XDG_CONFIG_HOME`.

```bash
staged set --root /path/to/root               # Override default staging root
staged init my-conv --root /path/to/root      # Override root for this session only
staged set --session-root /extra/root         # Add additional discovery directory
```

---

## Shell Completion

Interactive `staged` and `staged init` offer completion setup on first use. You can also install it manually:

```bash
# Bash: configure ~/.bashrc and ~/.bash_profile
staged install-completion --shell bash

# Zsh: link completion script and configure ~/.zshrc
staged install-completion --shell zsh

# PowerShell: link completion script
staged install-completion --shell powershell
```

- **Bash & Zsh**: Symlinks completion scripts into your configuration folder and adds a managed block to `~/.bashrc` or `~/.zshrc`.
- **PowerShell**: Links the completion script and prints a loader for `$PROFILE`.
- **Activation**: Run the printed activation command or restart your shell after setup.
- **Setup Prompt**: Remembers declines per shell. Skips CI, redirected streams, help, and machine output. Set `STAGED_NO_PROMPT=1` to disable it.
- **Suggestions**: Commands, supported options, session IDs, and editor IDs.
- **Substring Matching**: Tab-completion matches any case-insensitive substring on `-f` (e.g., `staged diff -f btn<Tab>`).
- **Ambiguous Zsh Matches**: The first Tab lists matching files without replacing the typed query. Press Tab again to select the first match, and further Tabs to cycle. A unique match completes immediately.

---

## Command Reference

Syntax: `<value>` is required, `[value]` is optional, `|` indicates alternatives. Quote paths containing spaces.

```text
staged [global options] [command] [command options]
staged <command> --help
staged --help-all
```

### Global Options

| Option                      | Meaning                                                         |
| --------------------------- | --------------------------------------------------------------- |
| `-t <id>`, `--tool <id>`    | Select editor adapter; with `use` or `set`, saves in that scope |
| `-s <id>`, `--session <id>` | Select session by ID or prefix; with `use`, binds to shell      |
| `--root <path>`             | Override staging root; with `set`, saves as default             |
| `--shell <name>`            | Select Bash, Zsh, or PowerShell for setup                       |
| `-v`, `--verbose`           | Include session/file summaries and overview paths, sizes, timestamps              |
| `-h`, `--help`              | Show help without running an operation                          |

_(After `path`, `-s` means `--staged`; use `--session <id>` for session selection.)_

### Top-Level Options

| Option              | Meaning                                    |
| ------------------- | ------------------------------------------ |
| `--sessions`        | List sessions for the current repository   |
| `-R`, `--all-repos` | List sessions across repositories          |
| `--list`            | Print proposed file paths, one per line    |
| `--summary`         | Print session and file summaries; use `-f` for one file |
| `--json`            | With `--summary`, print structured summaries |
| `--help-all`        | Show the full command and option reference |

---

### Overview & Subcommands

| Command              | Key Options                                                          | Description                                                       |
| -------------------- | -------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `staged`             | `[-v]`, `[--sessions]`, `[-R]`                                       | Show overview of active session, workspace sessions, or all roots |
| `read` | `-f <path>`, `[--workspace\|--meta]`, `[--offset <n>]`, `[--length <n>]`, `[--start-line <n>]`, `[--end-line <n>]`, `[--json]` | Read exact file contents or ranges |
| `write` | `-f <path>`, `(--text <text>\|--stdin)`, `[--meta]`, `[--offset <n>]`, `[--delete-count <n>]`, `[--start-line <n>]`, `[--end-line <n>]`, `[--append]`, `[--from-workspace]`, `[--expect-sha256 <hash>]`, `[--json]` | Write complete files or edit ranges inside staging or session metadata |
| `create` | `-f <path>`, `[--meta]`, `[--text <contents>\|--stdin]`, `[--json]` | Create a new staged or metadata file, refusing existing destinations |
| `copy` | `-f <source>`, `[--workspace\|--meta]`, `[--to <destination>]`, `[--json]` | Copy workspace, staged, or metadata contents |
| `rename` | `-f <source>`, `--to <destination>`, `[--meta]`, `[--json]` | Move a staged proposal or a metadata file |
| `delete` | `-f <path>`, `[--workspace\|--meta]`, `[--json]` | Discard a staged proposal, propose a workspace deletion, or delete metadata |
| `summarize` | `[-f <path>]`, `(-m <message>\|--stdin\|--clear)`, `[--session-message <message>]`, `[--json]` | Write session and file summaries |
| `init`               | `[id]`, `[--json]`, `[--root <p>]`                                   | Initialize or reuse a staging session                             |
| `use`                | `--session <id>`, `--tool <id>`, `--clear`                           | Bind session or tool to current shell                             |
| `diff`               | `-f <f>`, `--all`, `-a`, `-c`, `-ac`, `--between <a> <b>`            | Review or apply visual diffs in editor                            |
| `apply`              | `-f <f>`, `--all`, `[--force]`, `[-y]`                               | Apply complete staged files to workspace                          |
| `path`               | `[-s\|-w\|--meta]`, `-f <f>`, `[--session <id>]`                     | Print raw absolute path without headers                           |
| `open`               | `[--meta]`, `-f <f>`                                                 | Open staged file or session metadata in editor                    |
| `clean`              | `-f <f>`, `--all`, `--session [<id>]`, `[-y]`                        | Discard staged files or delete session folder                     |
| `migrate`            | `--from <id>`, `[--to <id>]`, `(-f <q>\|--all)`, `[--force]`         | Copy staged files and metadata between sessions                   |
| `set`                | `[--repo]`, `--tool`, `--default-session`, `--root`                  | Save persistent preferences                                       |
| `set-tool`           | `<id>`, `--name`, `--diff-cmd`, `--open-cmd`                         | Register custom editor adapter                                    |
| `install-skill`      | `[--tool]`, `[--default]`, `[--target-dir]`, `[--configure-sandbox]` | Install `/stage` skill for agent harness                          |
| `install-completion` | `[--shell bash\|zsh\|powershell]`                                    | Install tab completion                                            |
| `uninstall` | `[--yes] [--keep-sessions\|--remove-sessions]` | Remove the CLI and integrations; optionally delete known sessions |
| `install-launcher`   |                                                                      | Install the standalone Windows launcher                           |

---

## Troubleshooting

| Situation                           | Resolution                                                                                   |
| ----------------------------------- | -------------------------------------------------------------------------------------------- |
| `staged` command not found          | Add `~/.local/bin` (or `%APPDATA%\staged\bin`) to your shell `PATH`.                         |
| No staging session found            | Confirm current directory is in the workspace. Check `staged --sessions` or `staged -R`.     |
| Session workspace does not exist    | The workspace directory was removed. `staged -R` still lists the session and marks the workspace not found. `staged clean --session <id> -y` removes that session only. |
| Session selection ignored           | Environment variables (`STAGED_SESSION`) or CLI flags take precedence over shell bindings.   |
| Session lost between agent calls    | Use persistent shells or pass `STAGED_SHELL_ID` across agent subprocess calls.               |
| File or session is ambiguous        | Provide exact relative path or full session ID.                                              |
| Unknown session from `staged -R`    | Use the full session UUID (or ≥8 hex chars), not a local alias, when the session belongs to another workspace. |
| Editor launcher missing             | Ensure editor CLI is in `PATH`, switch with `staged use --tool`, or use `--tool cli`.        |
| Agent denied write access           | Add printed staging root path to harness writable directory permissions.                     |
| Protected or changed branch warning | Use `--force` for protected branches; use `-y` for drifted branches.                         |
| `MISSING` status                    | Staged file missing for a tracked rename. Add destination file or clean the entry.           |
| Migration refuses to copy           | Destination file exists or workspace file is newer. Pass `--force` to overwrite.             |
| Apply fails mid-batch               | Inspect `staged`. Apply is atomic per-file, not a transaction; earlier files remain applied. |

---

## Development and Testing

```bash
python3 -m unittest discover -s tests -v
npm pack --dry-run
```

Tests execute against isolated temporary directories without modifying your user configuration or active working tree. See [SPEC.md](SPEC.md) for behavioral contracts and [IMPLEMENTATION.md](IMPLEMENTATION.md) for roadmap items.
