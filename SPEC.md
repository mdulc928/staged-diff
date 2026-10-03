# Unified staging engine and `/stage` skill

Version 2.0.0 · revised October 3, 2026 · package `@melchi/staged`

This revision incorporates the decision to remove old command aliases and implicit editor-specific storage. It preserves the core product promise: proposed changes are real, inspectable files outside the working tree, and enter the workspace only through an explicit apply operation. [IMPLEMENTATION.md](IMPLEMENTATION.md) records the original gaps and remaining release verification separately from the requirements below.

## 1. Product contract

The developer can attempt a solution independently, then compare it with an agent's proposal. The tool must support this sequence:

1. Initialize an isolated conversation session associated with a workspace and Git branch.
2. Have the agent create complete proposal files without modifying workspace originals.
3. Inspect statuses, absolute paths and editor or terminal diffs.
4. Compare approaches across sessions or migrate selected staged changes.
5. Explicitly apply selected files or all staged changes, subject to branch guards.
6. Discard staged files without reverting or modifying workspace files.

`staged` is the CLI; `/stage` is the skill. They do not stage Git's index, commit changes, or manage worktrees. The Python engine has no third-party dependencies and requires Python 3.8+. An optional npm launcher provides cross-platform command shims.

## 2. Storage and ownership

All built-in editors share a staging root. Defaults:

| Data                | macOS / Linux / WSL                                                           | Windows native                |
| ------------------- | ----------------------------------------------------------------------------- | ----------------------------- |
| Sessions            | `$XDG_DATA_HOME/staged`, otherwise `~/.local/share/staged`                    | `%LOCALAPPDATA%\staged`       |
| Global state        | `$XDG_CONFIG_HOME/staged/state.json`, otherwise `~/.config/staged/state.json` | `%APPDATA%\staged\state.json` |
| Shell state         | `shell_sessions/<id>.json` beside global state                                | Same layout                   |
| Repository settings | `<workspace>/.staged.json`                                                    | Same layout                   |

Root precedence: `--root`, `STAGED_ROOT`, repository `staging_root`, global `staging_root`, custom adapter root, platform default. Explicit `session_roots` add discovery locations. No transcript inference or implicit discovery of editor-specific staging directories is permitted.

```text
<root>/<session-id>/
  .workspace          # absolute workspace path, plain text
  session.json        # origin branch, creation time, migration provenance
  renames.json        # operation manifest
  staged_changes.md   # agent-maintained review dashboard
  .index.json         # derived inventory; never authoritative for apply
  .review/            # retained review-side empty files for GUI launchers
  staging/            # complete staged files, including dotfiles
    src/example.py
```

`staged init <id> --json` returns `session`, `workspace`, `directory`, `staging` and `branch`. Omit the ID to generate a UUID. Existing IDs can be reused only for the same workspace; initialization must not reset the origin branch. The staging root must be outside the workspace. Empty and deletion-only sessions must remain selectable.

The manifest maps destination paths to original paths, plus an optional deletion list:

```json
{
  "src/new-name.py": "src/old-name.py",
  "_deletions": ["src/obsolete.py"]
}
```

Paths must be normalized relative paths using forward slashes. Filesystem symlinks, Git metadata, traversal, absolute paths, conflicting file/directory destinations, duplicate rename sources and overlapping rename chains are rejected. Session metadata stays outside `staging/`. A file cannot be staged for both writing and deletion.

## 3. State and session selection

For the tool and active session, highest precedence wins:

1. Explicit `--tool` / `--session` flags.
2. `STAGED_TOOL` / `STAGED_SESSION` environment variables.
3. Shell binding saved by `staged use`.
4. Repository preferences (`default_tool`, `default_session`).
5. Global defaults.
6. Auto-detection: available editor or terminal fallback; newest session for the workspace.

`staged use --session <id>` selects a session until another `use` or `use --clear` changes the binding. The positional form `staged use <id>` is also accepted; supplying both forms is an error. Shell bindings use the invoking process's parent PID. Set `STAGED_SHELL_ID` to a stable shell-specific identifier when the harness uses fresh subprocesses. Explicit environment or CLI values override a binding; the tool cannot change its parent's environment.

A session selector resolves an exact ID first, then a unique prefix. Ambiguous prefixes and unknown IDs fail; they must not silently select a different staging session. Sessions are filtered to the current repository root, including when invoked from a subdirectory. Explicit workspace markers also support non-Git directories.

## 4. Commands and observable behavior

Global selection options are accepted before or after the subcommand. Listing and completion installation switches are top-level options. `-a` means apply only, and `-R` means list all repositories.

| Command                                                                     | Contract                                                                                                  |
| --------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | --- | ----------------------- | --------------------------------------------- |
| `staged`, `staged diff`                                                     | Show workspace, session ID, tool, branch and file statuses                                                |
| `staged -v`                                                                 | Include absolute paths, sizes and modification times                                                      |
| `staged --sessions`, `staged -R`                                            | List local or all configured sessions                                                                     |
| `staged init [id] [--json]`                                                 | Create/reuse a session and bind it to this shell                                                          |
| `staged diff -f <file>`                                                     | Review one uniquely matched file                                                                          |
| `staged diff --all`                                                         | Review all pending files, additions and deletions                                                         |
| `staged diff -f <file> -a`                                                  | Apply one selected change                                                                                 |
| `staged diff --all -a`                                                      | Apply all staged changes                                                                                  |
| `staged diff -f <file> -c`                                                  | Discard one selected staged change                                                                        |
| `staged diff --all -c`                                                      | Discard all staged changes                                                                                |
| `staged apply -f <file>`, `staged apply --all`                              | Apply an explicitly selected file or all staged changes                                                   |
| `staged path [-s                                                            | --staged                                                                                                  | -w  | --workspace] -f <file>` | Print one raw absolute path, without a banner |
| `staged open [--meta] -f <file>`                                            | Open one staged file or session file in your editor                                                       |
| `staged clean -f <file>`, `staged clean --all`, `staged clean --session [<id>]` | Discard one staged change, all staged changes, or an entire session directory; `-y` confirms discard |
| `staged use --session <id>`, `staged use --tool <id>`, `staged use --clear` | Manage this shell's binding                                                                               |
| `staged set [--repo] ...`                                                   | Save preferences globally or in the repository                                                            |
| `staged set-tool <id> --name ... --diff-cmd ... --open-cmd ...`             | Register a custom editor                                                                                  |
| `staged migrate --from <id> [--to <id>] (--file <query-or-glob> \| --all)`  | Copy explicitly selected staged changes and operation metadata                                            |
| `staged install-skill [--tool <id>] [--target-dir <dir>]`                   | Install the skill for the selected harness                                                                |
| `staged install-completion [--shell <shell>]`                               | Install shell completions for bash, zsh, or powershell                                                    |

`path -s` is a staged-path switch, while global `-s` selects a session. Use `path --session <id> -s -f <file>` to combine them. Deletions have only a workspace path.

File selection uses `-f` / `--file`; bulk selection uses `--all`. For `clean`, selection may also be `-s` / `--session [<id>]` to discard the entire session directory on disk and unbind shell session bindings and default session preferences. These are mutually exclusive. Positional filenames and positional `all` are rejected. Apply, clean, migration, and diff actions/comparisons require explicit selection. Only bare `staged diff` retains its overview behavior. `path` and `open` require `-f` and operate on one file. `-f all` selects a file literally named `all`.

Apply and clean can be combined: `-ac`, `-ca`, `-a -c`, and `-c -a` (or their long forms) always apply the selected changes first, then clean the same selection. Clean runs only after apply succeeds; an apply failure retains all staged files and tracking.

Open matches only staged files under `staging/` by default. `open --meta` matches only non-hidden files directly in the session directory, including editable metadata and review notes. Metadata aliases apply only in that mode. Opening does not lock files or require branch overrides, and every nonzero editor exit status is an error.

Statuses: `APPLIED`, `MODIFIED`, `NEW FILE`, `RENAMED`, `RELOCATED`, `DELETED`, `MISSING`. A tracked rename without staged contents is `MISSING` and blocks apply until repaired or cleaned. `APPLIED` requires equal bytes and, on POSIX, equal permission bits. A rename remains pending while its original source exists, even if the destination already matches. A deletion is applied when its target is absent.

Every terminal status token uses its shared color wherever emitted: `APPLIED` green, `MODIFIED` yellow, `NEW FILE` cyan, `RENAMED` and `RELOCATED` magenta, `DELETED` red, and `MISSING` bold red. This includes overview rows, review headers, and apply results. Honor `NO_COLOR` and `FORCE_COLOR`; machine-readable output remains plain.

All subcommands must render help when `-h` or `--help` appears, even with missing required values, without reading configuration or performing mutations. Invalid flags, missing values and malformed JSON must fail clearly. Operational failures return nonzero exit status. Terminal diff exit code 1 means differences, not an error.

### File matching

Resolve an exact relative path, then a case-insensitive exact path, then an exact basename, then subsequence matches, then a bounded Levenshtein typo match. Multiple candidates at any stage require an exact path. Apply, path and selective clean never expand an ambiguous query into multiple files. Only `--all` and migration globs deliberately select multiple paths.

The inventory records paths, basenames, tokens, sizes, mtimes and directory timestamps in `.index.json`. Unchanged directory timestamps allow inventory reuse; stale or malformed indexes trigger a scan. Mutating operations always rescan the real files, and status checks read current file contents and permissions. The original sub-5ms lookup goal remains a performance target, not a measured guarantee for this release.

### Cross-session review and migration

```bash
staged diff --between session-a session-b -f src/example.py
staged diff -f src/example.py --session session-a --compare-session session-b
```

Resolve the same relative path in both sessions, never unrelated files sharing a basename. Require `-f <file>` for one file or `--all` for all common staged files. Comparison cannot be combined with apply or clean. Pending deletions have no staged-file contents to compare.

Migration preserves the source, merges selected rename/deletion entries, and records provenance including source branch. An omitted destination uses the active session; a new explicit destination ID initializes a session for this workspace. Existing target staged files or source copies older than differing workspace files block migration unless `--force` explicitly permits replacement. Timestamp checks are advisory conflict detection, not a three-way merge or proof of freshness. Contradictory operations must still fail under `--force`.

## 5. Apply and clean safety

Protected branches are opt-in: the default `protected_branches` list is empty. Configure repository or global patterns with `staged set --protected-branch <pattern>` (repeat for multiple patterns; add `--repo` for repository scope). Repository or global `protected_branches` replaces this list. `branch_protection` is a boolean and can be set through `staged set --branch-protection true|false`; enabling the check does not add any patterns.

Apply must preflight paths and manifest operations for the whole selected batch before copying any files. It blocks protected branches unless `--force` is supplied and reports the override. Branch drift from the origin branch or branches recorded by migrated staged changes requires interactive confirmation; `--yes` explicitly confirms drift in scripts. `--force` does not confirm branch drift.

Each destination copy uses an atomic file replacement, preserving file metadata. Renamed sources are removed after their destination is written. Deletions operate on regular files only. The batch is not a filesystem transaction: an unexpected I/O failure after copying begins may leave earlier files applied. Completed files are reported individually; rerun inspection after a failure.

Apply is a file replacement operation, not an automatic merge. Review current workspace diffs before applying. An explicit apply can overwrite concurrent edits to that file; source isolation alone cannot prevent that.

Clean never modifies the workspace. It confirms discarding pending changes unless `--yes` is passed. Selective clean removes exactly one uniquely resolved file or deletion entry. Cleaning up all staged changes empties the staging folder and manifest but preserves the session identity and origin branch so an empty session can be reused. Session clean (`--session [<id>]`) prompts before discarding (or requires `--yes`), removes the entire session directory on disk, unbinds shell session bindings, and clears default session preferences matching that session.

## 6. Adapter and installation contract

Adapters expose `id`, `name`, `root`, binary detection, diff/open argument construction, skill installation and sandbox configuration capability. Supported diff adapters:

| ID                           | Review launcher                                                   |
| ---------------------------- | ----------------------------------------------------------------- |
| `cursor`                     | `cursor -r --diff`                                                |
| `antigravity`                | `antigravity-ide -r -d`                                           |
| `windsurf`                   | `windsurf -r --diff`                                              |
| `vscode`                     | `code -r -d`                                                      |
| `pycharm`                    | `pycharm diff`, with platform-specific PyCharm launcher discovery |
| `zed`                        | `zed --diff`                                                      |
| `codex`, `claudecode`, `cli` | `difft`, then `git diff --no-index`, then `diff -u`               |

Custom templates use `{orig}` and `{staged}` replacements within argument tokens. Open and diff commands resolve their own executables independently. No shell interpolation is performed. GUI additions/deletions use a retained empty review file so asynchronous editor launchers can still read both sides.

Skill installation uses the selected adapter and reports the actual destination:

| Harness                        | Default skill directory            |
| ------------------------------ | ---------------------------------- |
| Cursor                         | `~/.cursor/skills/stage`           |
| Codex                          | `~/.agents/skills/stage`           |
| Claude Code                    | `~/.claude/skills/stage`           |
| Windsurf/Cascade               | `~/.codeium/windsurf/skills/stage` |
| Antigravity                    | `~/.gemini/config/skills/stage`    |
| Zed                            | `~/.agents/skills/stage`           |
| Other editor or custom harness | Explicit `--target-dir` required   |

`--default` saves the selected tool. Skill installation and sandbox permission changes are distinct capabilities. `--configure-sandbox` opts into merging Cursor staging-root permissions into `sandbox.json` and `cli-config.json`, preserving unrelated settings. Unsupported harnesses must report that automatic permission configuration is unavailable and identify the root needing manual access. No undocumented allowlist files should be invented.

The tool does not enforce a repository read-only sandbox. A harness or administrator may impose additional restrictions; installing the skill cannot override them. A read-only listing must not prompt for or grant new sandbox permissions.

Installation paths and Cursor settings are based on [Cursor skills](https://prod.cursor.com/docs/skills), [Cursor sandbox configuration](https://prod.cursor.com/docs/reference/sandbox), [Cursor CLI permissions](https://prod.cursor.com/docs/cli/reference/permissions), [Codex skills](https://learn.chatgpt.com/docs/build-skills), [Claude Code skills](https://code.claude.com/docs/en/skills), [Cascade skills](https://docs.devin.ai/desktop/cascade/skills), [Antigravity skills](https://antigravity.google/docs/skills?app=antigravity-ide), and [Zed skills](https://zed.dev/docs/ai/skills).

## 7. Skill behavior

The bundled `SKILL.md` initializes or reuses the actual conversation session, uses returned paths, writes complete staged files, and keeps metadata beside `staging/`. It maintains a review file with prominently displayed session ID, origin branch, paths, links to originals/staged files, operation summaries and validation results.

When the user requested review before apply, the agent presents the proposal and awaits authorization. Existing explicit authorization remains valid. Refinements stay in the session. Project checks run in an isolated copy while staging, or in the workspace after authorized apply. The skill must not prescribe a particular project's formatter/test commands for every repository.

## 8. Shells, operating systems and distribution

`staged install-completion [--shell <shell>]` links versioned completion files from the checkout or installed package. Bash and Zsh use idempotent marked rc blocks. Bash 3.2 is supported. PowerShell links its `Register-ArgumentCompleter` script and prints how to source it from the profile; when Windows denies symlinks, a loader sources the bundled script instead. Open shells must reload previously loaded functions after updates. PowerShell is the supported Windows shell; its installer also creates the Windows `.cmd` launcher. Completion is case-insensitive in Bash, Zsh and PowerShell.

Native executables use argument-list subprocess execution. Windows batch launchers require `cmd.exe`; arguments containing unsafe batch metacharacters are rejected. WSL can translate paths for Windows launchers using `wslpath`; host binaries must be on PATH. Automatic Windows Registry discovery is not implemented.

The npm package contains the engine, skill, documentation and a Node launcher that locates Python 3.8+. Direct Python invocation remains independent of npm. Publishing to the registry is a separate release operation.

Local validation must cover temporary repositories, branch guards, manifest operations, isolated homes, malformed inputs, cross-session comparisons, migration conflicts, custom adapters and generated shell syntax. Native Windows/PowerShell, Linux/WSL and real editor launches require platform acceptance testing before claiming verified support on those surfaces.
