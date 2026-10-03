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
- [Applying Changes & Branch Guards](#applying-changes--branch-guards)
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

# Generate Windows launcher (%APPDATA%\staged\bin) and PowerShell completion script
py -3 .\staged install-completion --shell powershell
```

1. Add the printed launcher folder (usually `%APPDATA%\staged\bin`) to your user **Path** environment variable.
2. To use `staged` immediately in your active PowerShell session:
   ```powershell
   $env:Path += ";$env:APPDATA\staged\bin"
   ```
3. Add the printed completion loader command to your `$PROFILE` to load completions automatically.

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
staged --sessions                       # List sessions for current workspace
staged -R                               # List all sessions across all workspaces
staged --session other-id diff -f f.py  # One-time session override
```

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
staged -v                       # Verbose overview (paths, sizes, timestamps)
staged diff -f src/example.py   # Open visual diff in configured editor
staged diff --all               # Review all pending changes sequentially
staged diff -f src/example.py --tool cli # Open terminal diff
```

- `staged diff` opens a side-by-side diff in your configured GUI editor. It does not modify workspace files unless `-a` is passed.
- Additions and deletions are compared against an empty placeholder file so GUI editors can display a two-sided diff.
- `--tool cli` invokes `difft` (Difftastic) if available, falling back to `git diff --no-index`, then standard `diff`.

---

### Status Indicators

| Status      | Color    | Meaning                                                                 |
| ----------- | -------- | ----------------------------------------------------------------------- |
| `MODIFIED`  | Yellow   | Staged file differs from workspace file                                 |
| `NEW FILE`  | Cyan     | File exists only in staging                                             |
| `RENAMED`   | Magenta  | File is renamed in manifest                                             |
| `RELOCATED` | Magenta  | File is moved to another folder without renaming                        |
| `DELETED`   | Red      | Workspace file is marked for deletion                                   |
| `APPLIED`   | Green    | Workspace file matches staged file byte-for-byte (and POSIX permissions)|
| `MISSING`   | Bold Red | Rename recorded in manifest, but staged destination file is missing     |

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

*(Deletions have only a workspace path. Inside `path`, `-s` means `--staged`; use `--session <id>` to specify a session.)*

---

## Applying Changes & Branch Guards

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

Combining `-ac` applies first; if apply fails, staged files are preserved.

---

### Operational Guarantees & Trade-offs

- **Atomic File Replacement**: Files are replaced atomically, preserving file permissions.
- **No 3-Way Merge**: Applying overwrites workspace files with staged contents. It does not merge concurrent workspace edits.
- **No Git Commits**: Changes remain uncommitted in your working tree for your final review.
- **Partial Batches**: Batch apply is not an ACID transaction. If an error occurs on file 8 of 10, files 1–7 remain applied. Always inspect `staged` after a failure.

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

- If `--to` is omitted, targets the active session.
- Source session and workspace files remain unchanged.
- If destination files exist or workspace files are newer, migration pauses. Pass `--force` to deliberately overwrite. *(Timestamp checks flag potential staleness; they do not perform a 3-way merge).*

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

| Adapter       | CLI Command                             | Documentation / Evidence                                   |
| ------------- | --------------------------------------- | ---------------------------------------------------------- |
| `vscode`      | `code -r -d original staged`            | [VS Code CLI Docs](https://code.visualstudio.com/docs/configure/command-line) |
| `pycharm`     | `pycharm diff original staged`          | [PyCharm Diff Viewer](https://www.jetbrains.com/help/pycharm/command-line-differences-viewer.html) |
| `zed`         | `zed --diff original staged`            | [Zed CLI Reference](https://zed.dev/docs/reference/cli)   |
| `cursor`      | `cursor -r --diff original staged`      | Verified in local CLI parser                               |
| `antigravity` | `antigravity-ide -r -d original staged` | Verified in local CLI parser                               |
| `windsurf`    | `windsurf -r --diff original staged`    | Standard vendor CLI flags                                  |

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

```bash
# Bash: link completion script and configure ~/.bashrc
staged install-completion --shell bash

# Zsh: link completion script and configure ~/.zshrc
staged install-completion --shell zsh

# PowerShell: link completion script and generate Windows launcher
staged install-completion --shell powershell
```

- **Bash & Zsh**: Symlinks completion scripts into your configuration folder and adds a managed block to `~/.bashrc` or `~/.zshrc`.
- **PowerShell**: Links script and generates the `staged.cmd` launcher.
- **Substring Matching**: Tab-completion matches any case-insensitive substring on `-f` (e.g., `staged diff -f btn<Tab>`).

---

## Command Reference

Syntax: `<value>` is required, `[value]` is optional, `|` indicates alternatives. Quote paths containing spaces.

```text
staged [global options] [command] [command options]
staged <command> --help
```

### Global Options

| Option                      | Meaning                                                                 |
| --------------------------- | ----------------------------------------------------------------------- |
| `-t <id>`, `--tool <id>`    | Select editor adapter; with `use` or `set`, saves in that scope         |
| `-s <id>`, `--session <id>` | Select session by ID or prefix; with `use`, binds to shell              |
| `--root <path>`             | Override staging root; with `set`, saves as default                     |
| `-v`, `--verbose`           | Include absolute paths, file sizes, and timestamps                      |
| `-h`, `--help`              | Show help without running an operation                                  |

*(After `path`, `-s` means `--staged`; use `--session <id>` for session selection.)*

---

### Overview & Subcommands

| Command | Key Options | Description |
| ------- | ----------- | ----------- |
| `staged` | `[-v]`, `[--sessions]`, `[-R]` | Show overview of active session, workspace sessions, or all roots |
| `init` | `[id]`, `[--json]`, `[--root <p>]` | Initialize or reuse a staging session |
| `use` | `--session <id>`, `--tool <id>`, `--clear` | Bind session or tool to current shell |
| `diff` | `-f <f>`, `--all`, `-a`, `-c`, `-ac`, `--between <a> <b>` | Review or apply visual diffs in editor |
| `apply` | `-f <f>`, `--all`, `[--force]`, `[-y]` | Apply complete staged files to workspace |
| `path` | `[-s\|-w]`, `-f <f>`, `[--session <id>]` | Print raw absolute path without headers |
| `open` | `[--meta]`, `-f <f>` | Open staged file or session metadata in editor |
| `clean` | `-f <f>`, `--all`, `--session [<id>]`, `[-y]` | Discard staged files or delete session folder |
| `migrate` | `--from <id>`, `[--to <id>]`, `(-f <q>\|--all)`, `[--force]` | Copy staged files and metadata between sessions |
| `set` | `[--repo]`, `--tool`, `--default-session`, `--root` | Save persistent preferences |
| `set-tool` | `<id>`, `--name`, `--diff-cmd`, `--open-cmd` | Register custom editor adapter |
| `install-skill` | `[--tool]`, `[--default]`, `[--target-dir]`, `[--configure-sandbox]` | Install `/stage` skill for agent harness |
| `install-completion` | `[--shell bash\|zsh\|powershell]` | Install tab completion |

---

## Troubleshooting

| Situation | Resolution |
| --------- | ---------- |
| `staged` command not found | Add `~/.local/bin` (or `%APPDATA%\staged\bin`) to your shell `PATH`. |
| No staging session found | Confirm current directory is in the workspace. Check `staged --sessions` or `staged -R`. |
| Session selection ignored | Environment variables (`STAGED_SESSION`) or CLI flags take precedence over shell bindings. |
| Session lost between agent calls | Use persistent shells or pass `STAGED_SHELL_ID` across agent subprocess calls. |
| File or session is ambiguous | Provide exact relative path or full session ID. |
| Editor launcher missing | Ensure editor CLI is in `PATH`, switch with `staged use --tool`, or use `--tool cli`. |
| Agent denied write access | Add printed staging root path to harness writable directory permissions. |
| Protected or changed branch warning | Use `--force` for protected branches; use `-y` for drifted branches. |
| `MISSING` status | Staged file missing for a tracked rename. Add destination file or clean the entry. |
| Migration refuses to copy | Destination file exists or workspace file is newer. Pass `--force` to overwrite. |
| Apply fails mid-batch | Inspect `staged`. Apply is atomic per-file, not a transaction; earlier files remain applied. |

---

## Development and Testing

```bash
python3 -m unittest discover -s tests -v
npm pack --dry-run
```

All 74 tests execute against isolated temporary directories without modifying your user configuration or active working tree. See [SPEC.md](SPEC.md) for behavioral contracts and [IMPLEMENTATION.md](IMPLEMENTATION.md) for roadmap items.
