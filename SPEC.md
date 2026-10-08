# Unified staging engine and `/stage` skill specification

Version 2.0.0 · revised October 7, 2026 · package `@melchi/staged`

This specification defines the architectural and behavioral contract of `staged` and the `/stage` skill.

**Core Contract**: Proposed edits reside in isolated, inspectable files outside the Git working tree. The workspace remains unchanged until an explicit apply operation is executed.

---

## 1. Product Contract

`staged` enables a developer to work on a problem independently while an agent stages a proposal in parallel.

### Operational Lifecycle

1. **Initialize Session**: Create an isolated staging directory bound to a workspace path and Git branch.
2. **Stage Complete Files**: The agent writes complete proposed files outside the working tree.
3. **Inspect Changes**: View statuses, extract absolute paths, and open visual diffs in editor or terminal.
4. **Compare & Migrate**: Compare approaches across sessions; cherry-pick staged files between sessions.
5. **Apply Changes**: Explicitly copy selected files into the workspace, guarded by Git branch checks.
6. **Clean Up**: Discard staged proposals without modifying workspace files.

### Explicit Non-Goals

- **No Git Index Staging**: Does not interact with `git add` or the Git staging area.
- **No Git Worktrees**: Does not create, modify, or manage Git worktrees.
- **No Auto-Commits**: Does not generate or execute Git commits.
- **No 3-Way Auto-Merge**: Does not attempt automatic resolution of concurrent edits.

### Runtime Requirements

- **Python**: 3.8+ (standard library only; zero external pip dependencies).
- **Node.js**: 18+ (optional npm wrapper provides cross-platform command shims).

---

## 2. Storage and Ownership

All built-in adapters share a single staging root on disk.

### Default Storage Layout

| Data                | POSIX (macOS / Linux / WSL)                                                   | Windows Native                |
| ------------------- | ----------------------------------------------------------------------------- | ----------------------------- |
| Sessions            | `$XDG_DATA_HOME/staged`, otherwise `~/.local/share/staged`                    | `%LOCALAPPDATA%\staged`       |
| Global State        | `$XDG_CONFIG_HOME/staged/state.json`, otherwise `~/.config/staged/state.json` | `%APPDATA%\staged\state.json` |
| Shell State         | `shell_sessions/<id>.json` beside global state                                | Same layout                   |
| Repository Settings | `<workspace>/.staged.json`                                                    | Same layout                   |

**Root Precedence**:

1. `--root <path>` CLI flag
2. `STAGED_ROOT` environment variable
3. Repository configuration (`staging_root` in `<workspace>/.staged.json`)
4. Global configuration (`staging_root` in `state.json`)
5. Custom adapter root
6. Platform default directory

Additional discovery roots are configured via `session_roots`. Discovery is explicit; `staged` does not scan or infer staging roots from editor transcripts.

---

### Session Directory Structure

```text
<root>/<session-id>/
  .workspace          # Absolute workspace path (plain text)
  session.json        # Origin branch, creation timestamp, migration provenance
  renames.json        # Operation manifest (renames, relocations, deletions)
  summaries.json      # Agent-authored session string and per-path file summaries
  staged_changes.md   # Agent-maintained review dashboard
  .index.json         # Derived file inventory (not authoritative for apply)
  .review/            # Empty placeholder files for GUI diff viewers
  staging/            # Complete staged files, including dotfiles
    src/example.py
```

- `staged init [id] --json` outputs `session`, `workspace`, `directory`, `staging`, and `branch`.
- Reusing an existing session ID is valid only within the same workspace and preserves the origin branch.
- The staging root must reside outside the workspace.
- Empty sessions and deletion-only sessions remain fully selectable.

---

### Operation Manifest (`renames.json`)

```json
{
  "src/new-name.py": "src/old-name.py",
  "_deletions": ["src/obsolete.py"]
}
```

- **Path Format**: Normalized relative paths with forward slashes only.
- **Mutual Exclusivity**: A path cannot be staged for writing and listed in `_deletions`.
- **Validation Constraints**: Symlinks, `.git` metadata, directory traversal (`../`), and overlapping rename chains (`A -> B` and `B -> C` in one batch) are rejected.
- **Location**: Metadata files must reside outside `staging/`.

---

## 3. State and Session Selection

### Precedence Hierarchy

1. Explicit CLI flags (`--tool`, `--session`)
2. Environment variables (`STAGED_TOOL`, `STAGED_SESSION`)
3. Shell binding saved by `staged use`
4. Repository configuration (`default_tool`, `default_session` in `.staged.json`)
5. Global defaults (`state.json`)
6. Auto-detection (newest session for active workspace; available editor)

### Shell Bindings

- `staged use --session <id>` binds to the parent PID of the invoking shell process.
- The binding persists until explicitly changed or cleared via `staged use --clear`.
- Setting `STAGED_SHELL_ID` shares bindings across fresh subprocesses spawned by agent runners.
- Prefix matching resolves exact matches first, then unique prefixes. Ambiguous prefixes list candidate matches and fail safely.
- Session discovery is strictly scoped to the active repository root.

---

## 4. Commands and Observable Behavior

Global selection options (`-s`, `-t`, `--root`, `-v`, `-h`) are accepted before or after subcommands.

### Command Specifications

| Command                  | Action                   | Contract                                                                    |
| ------------------------ | ------------------------ | --------------------------------------------------------------------------- |
| `staged`, `staged diff`  | Overview                 | Print workspace, session ID, tool, branch, and status summary               |
| `staged -v`              | Verbose Overview         | Include session/file summaries, absolute paths, sizes, and timestamps             |
| `staged --sessions`      | Local List               | List sessions associated with current workspace, including session summaries                             |
| `staged --list`          | File List                | Print proposed file paths, one per line                                     |
| `staged --help-all`      | Full Help                | Show every command and public option                                        |
| `staged -R`              | Global List              | List all sessions across all configured workspaces and roots                |
| `read -f <path>` | Read File | Read exact byte or line ranges; `--workspace` selects originals |
| `write -f <path>` | Write File | Replace, insert, or append contents inside staging only |
| `create -f <path>` | Create File | Create an empty or populated staged file without replacement |
| `copy -f <source> [--workspace] [--to <path>]` | Copy File | Read staged/workspace contents and create a staged copy |
| `rename -f <source> --to <path>` | Rename File | Move a staged file and preserve its origin and summary |
| `delete -f <path> [--workspace]` | Delete Proposal | Discard a proposal by default; with `--workspace`, record deletion for later apply |
| `summarize [-f <path>] [-m <message>]` | Summaries | Read or update concise session or exact-path file summaries |
| `diff -v`, `diff --verbose` | Verbose Review | Show session and file summaries in the overview or before selected diffs |
| `init [id]`              | Initialize               | Create or reuse session; bind to active shell                               |
| `diff -f <f>`            | File Diff                | Open visual diff in configured editor                                       |
| `diff --all`             | Bulk Diff                | Open visual diff sequentially for all pending changes                       |
| `diff -f <f> -a`         | File Apply               | Apply single file immediately instead of opening diff                       |
| `diff --all -a`          | Bulk Apply               | Apply all staged additions, edits, and deletions                            |
| `diff -f <f> -c`         | File Discard             | Discard single staged file instead of opening diff                          |
| `diff --all -c`          | Bulk Discard             | Discard all staged files in session                                         |
| `apply -f <f> \| --all`  | Explicit Apply           | Copy staged files to workspace (requires `-f` or `--all`)                   |
| `path [-s\|-w] -f <f>`   | Print Path               | Print raw absolute path (`-s` staged, `-w` workspace)                       |
| `open [--meta] -f <f>`   | Open File                | Open staged file (or session root metadata with `--meta`)                   |
| `clean -f <f> \| --all`  | Discard Changes          | Discard staged changes; prompts unless `-y` is passed                       |
| `clean --session [<id>]` | Delete Session           | Remove session folder from disk and clear shell bindings                    |
| `use --session <id>`     | Bind Shell               | Bind session to current shell process                                       |
| `migrate --from <id>`    | Migrate                  | Copy staged files and manifest entries between sessions                     |
| `set [--repo]`           | Save Settings            | Store persistent configuration                                              |
| `set-tool <id>`          | Register Tool            | Register custom editor CLI adapter                                          |
| `install-skill`          | Install Skill            | Copy `SKILL.md` to harness skill directory                                  |
| `install-completion`     | Install Shell Completion | Configure Bash, Zsh, or PowerShell tab-completion; no launcher installation |
| `uninstall` | Uninstall | Confirm tool/integration removal; separately opt into deleting sessions across known roots and repositories. `--yes` alone keeps sessions in noninteractive runs. |
| `install-launcher`       | Install Windows Launcher | Write `staged.cmd`; leave PATH unchanged                                    |

---

### Selection Rules

- **Strict Flags**: File operations require `-f <file>` / `--file <file>`. Bulk operations require `--all`. Session deletion requires `--session [<id>]`.
- **Positional Rejection**: Positional filenames and positional `all` are rejected.
- **Combined Flags**: `-ac`, `-ca`, `-a -c`, and `-c -a` apply first, then clean. Clean runs only if apply succeeds.
- **Path Flag Scope**: In `staged path`, `-s` denotes `--staged`. Session overrides require `--session <id>`.

---

### File Access Contract

- **Scope**: Exact normalized relative paths. Reads select staging or workspace; writes select staging only. Symlinks, reparse points, special files, traversal, and Git metadata are rejected.
- **Ranges**: Zero-based bytes or one-based inclusive lines. Modes cannot be mixed. Invalid write ranges fail without replacing the target.
- **Write Modes**: No range replaces the file. `--offset` inserts unless `--delete-count` is supplied. `--start-line` inserts unless `--end-line` is supplied. `--append` writes at EOF.
- **Input**: Explicit `--text` (UTF-8) or `--stdin` (bytes). Newlines are preserved, never synthesized; lines are LF-delimited.
- **Workspace Seed**: `--from-workspace` copies a missing staged file before editing. Existing staged files and deletion entries are not overwritten implicitly.
- **Integrity**: Atomic target replacement; preserve existing file permissions. Optional `--expect-sha256` rejects stale content. No cross-process locking guarantee.
- **Machine Output**: Raw read bytes or `--json` with the full-file SHA-256. JSON reads return UTF-8 text or Base64 with an encoding marker. No setup prompts or binding updates.
- **Permissions**: Host filesystem rules remain authoritative. The skill describes usage; it does not grant access or restrict other CLI commands.

---

### Managed File Operations

- **Boundaries**: `create`, `copy`, `rename`, and `delete` accept exact `-f` paths and `--json`. Physical writes and deletions are confined to staging and session metadata. `copy --workspace` reads workspace contents; its destination remains staged. There is no option to copy into the workspace. All workspace writes use authorized apply.
- **Create**: Optional, mutually exclusive `--text` or `--stdin` supplies contents; neither means empty. Refuse an existing staged destination or pending operation, but allow creating a staged replacement for a workspace file.
- **Copy**: Source defaults to staging; `--workspace` reads from the workspace. Optional `--to` names the new staged destination and defaults to the source path. Preserve bytes and permission bits; leave the source intact. Do not copy rename mappings or summaries.
- **Rename**: Require a staged source and `--to`. Refuse existing staged destinations, unrelated workspace destination files, and operation conflicts. Preserve the original workspace source in `renames.json`; chained staging moves collapse to that original. Moving back to the original path removes the mapping. New files without an original stay additions. Move the file summary to the new path.
- **Delete**: Without `--workspace`, discard the exact staged proposal or cancel a pending deletion, remove its manifest entry and file summary, and clear the session summary if no proposals remain. With `--workspace`, require an existing regular workspace file, discard any staged replacement, and record its path in `_deletions` for later apply. Never unlink the workspace file in this command. Reject overlaps with pending renames. Summarize deletion proposals afterward; revise the session summary after discarding a proposal.
- **Validation**: Reject traversal, symlinks/reparse points, special files, staging inside the workspace, and relevant malformed metadata before mutation. Create/copy publish completed files using hard links without overwriting existing destinations; rename uses link/unlink to avoid clobbering a destination. The staging filesystem must support hard links. Atomicity does not extend across file and metadata updates, and concurrent edits to one session are unsupported.
- **Output**: JSON contains `operation` and `path`; create/copy/rename include `size` and `sha256`; copy/rename also include `source` and `source_root` (`staged` or `workspace`). Delete includes `mode` (`discard` or `propose`). Commands emit no setup prompts.

---

### Summary Contract

- **Storage**: Optional `summaries.json` beside `staging/`, with `{"session": "...", "files": {"relative/path": "..."}}`. Missing fields default to an empty string/object. Session and file summaries must be strings; paths follow the same normalized, symlink-free relative-path rules as proposals. Malformed metadata is reported rather than silently replaced. Unrelated keys and file entries survive updates.
- **Editing**: `staged summarize -m <message>` updates the session; `staged summarize -f <path> -m <message>` updates a file. `--message` aliases `-m`; `--stdin` reads UTF-8 text and rejects invalid bytes without changing metadata; `--clear` or empty text clears the selected summary. `-f` must name an existing proposal exactly, including rename destinations and deletion originals. `--summary <message>` may accompany a file update to replace both summaries in one atomic metadata write. No cross-process locking guarantee.
- **Reading**: With no update flags, print the session summary and selected file summaries. `--json` returns the entire summaries object, including after an update. Reading missing metadata does not create it.
- **Display**: `--sessions` and `-R` always show session summaries. `diff -v` / `--verbose` shows session and per-file summaries, including with `-f` or `--all`; cross-session comparisons label both sides. Normal diffs remain unchanged. Missing or blank summaries display `(no summary yet)`. Completion remains machine-readable.
- **Lifecycle**: Apply retains summaries. Clean removes selected file summaries and clears the session summary when all changes are removed. Migration copies selected file summaries, removing replaced destination entries if the source has none. A full migration into an empty destination copies the session summary only when the destination has no summary; partial or merged migrations retain the destination summary for the author to revise.
- **Agent Workflow**: Immediately after every file modification or rename/deletion manifest change, update the affected file summary and the session summary before any further modification. Summaries reflect the current cumulative proposal, generally in one sentence and at most three short sentences each; do not defer them to a batch at the end. Immediately revise session summaries after partial cleanup or migration. Sentence length guidance belongs to the skill; the CLI does not infer summaries or enforce sentence counts.

---

### Status Definitions and Terminal Colors

| Status      | Color    | Criteria                                                                    |
| ----------- | -------- | --------------------------------------------------------------------------- |
| `MODIFIED`  | Yellow   | Staged file content differs from workspace file                             |
| `NEW FILE`  | Cyan     | Staged file does not exist in workspace                                     |
| `RENAMED`   | Magenta  | File is renamed in manifest                                                 |
| `RELOCATED` | Magenta  | File is moved to a different directory without renaming                     |
| `DELETED`   | Red      | Manifest specifies deletion of workspace file                               |
| `APPLIED`   | Green    | Workspace file matches staged file byte-for-byte (and permissions on POSIX) |
| `MISSING`   | Bold Red | Manifest records rename/move, but staged destination file is missing        |

Terminal output respects `NO_COLOR=1` and `FORCE_COLOR=1`. Machine-readable output (`--json`, `path`) remains uncolored.

---

### File Matching Order

1. Exact workspace-relative path
2. Case-insensitive relative path
3. Exact basename
4. Subsequence match
5. Bounded Levenshtein typo match

If a query matches multiple files at any step, resolution halts and outputs all candidate matches. Mutating operations always bypass the `.index.json` inventory cache to inspect active filesystem state.

---

## 5. Apply and Clean Safety

### Git Branch Protections

- **Protected Branches**: Opt-in (default list is empty). Configured with `staged set --protected-branch <pattern>`. Applying on a matching branch requires `--force`.
- **Branch Drift**: If the current Git branch differs from the session origin branch, apply pauses for confirmation. In automated scripts, pass `--yes`.
- `--force` and `--yes` serve distinct functions; neither flag satisfies the other.

### Apply Guarantees

- **Atomic Replacement**: Files are replaced atomically, preserving file mode and permissions.
- **Ordered Rename Operations**: Renamed destinations are written before source files are deleted.
- **No Multi-File Transaction**: Batch apply does not provide an all-or-nothing filesystem transaction. If an error occurs mid-batch, previously written files remain in the workspace.

### Clean Guarantees

- `clean` operations **never** modify or revert workspace files.
- `staged clean -f <file>` removes the staged file and its corresponding manifest entry.
- `staged clean --all` clears all staged files and manifest entries while preserving the session directory, ID, and origin branch.
- `staged clean --session [<id>]` removes the session directory from disk, unbinds shell bindings, and clears default session preferences.

---

## 6. Adapter and Installation Contract

### Supported Review Adapters

| Adapter                      | Diff Command Line                                                 |
| ---------------------------- | ----------------------------------------------------------------- |
| `cursor`                     | `cursor -r --diff original staged`                                |
| `antigravity`                | `antigravity-ide -r -d original staged`                           |
| `windsurf`                   | `windsurf -r --diff original staged`                              |
| `vscode`                     | `code -r -d original staged`                                      |
| `pycharm`                    | `pycharm diff original staged` (with platform launcher detection) |
| `zed`                        | `zed --diff original staged`                                      |
| `codex`, `claudecode`, `cli` | `difft`, then `git diff --no-index`, then `diff -u`               |

- Templates use `{orig}` and `{staged}` placeholders. Arguments are passed directly as argument vectors without subshell evaluation.
- GUI additions and deletions use an empty placeholder file in `.review/` so GUI diff viewers can render a two-sided view.

### Harness Skill Directories

| Harness                        | Default Destination Directory      |
| ------------------------------ | ---------------------------------- |
| Cursor                         | `~/.cursor/skills/stage`           |
| Codex                          | `~/.agents/skills/stage`           |
| Claude Code                    | `~/.claude/skills/stage`           |
| Windsurf/Cascade               | `~/.codeium/windsurf/skills/stage` |
| Antigravity                    | `~/.gemini/config/skills/stage`    |
| Zed                            | `~/.agents/skills/stage`           |
| Other editor or custom harness | `--target-dir <path>` required     |

`--configure-sandbox` configures Cursor permissions (`~/.cursor/sandbox.json` and `cli-config.json`). For other harnesses, `staged` prints the staging root path for manual configuration.

---

## 7. Skill Behavior Contract

The bundled `SKILL.md` requires agents to:

1. Initialize or reuse a staging session and record returned paths.
2. Write complete proposal files to `staging/`, matching workspace-relative paths.
3. Record file renames, relocations, and deletions in `renames.json`.
4. Maintain `staged_changes.md` with session ID, origin branch, file links, and verification output.
5. Present the proposal and await explicit authorization before applying.
6. Run tests or formatters against an isolated copy during staging, or in the workspace only after authorization.

---

## 8. Shells, Operating Systems, and Distribution

- **Completions**: Public options follow the CLI parser. Bash, Zsh, and PowerShell support session/tool IDs and case-insensitive substring completion on `-f`. In Zsh, the first Tab on an ambiguous file query lists matches while preserving the input; subsequent Tabs start menu selection and cycle through matches. Unique matches complete immediately.
- **Completion Setup**: Interactive `staged` and `init` offer missing completion setup once per shell. Bash/Zsh update startup files; PowerShell prints a profile loader.
- **Launcher Setup**: On Windows, explicit `--shell powershell` offers `staged.cmd` once when no launcher is available. Installation requires acceptance or `install-launcher`; PATH is unchanged.
- **Setup Prompts**: Remember declines. Skip help, machine output, CI, redirected streams, and `STAGED_NO_PROMPT=1`.
- **Subprocesses**: Argument vector execution. Windows batch scripts execute via `cmd.exe` with safe metacharacter quoting. WSL translates paths for Windows binaries via `wslpath`.
- **Distribution**: Packaged as `@melchi/staged` with a Node binary launcher wrapping the Python engine. The engine runs standalone with Python 3.8+.
- **Test Suite**: Comprehensive automated test coverage validating branch protections, manifest handling, prefix matching, cross-session diffs, and shell completion parsing.
