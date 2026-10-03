# Guide to staged

Use `staged` to review proposed code without having an agent overwrite your working files first. The agent writes complete files in a separate session directory. You can try your own solution, compare it with the proposal, refine the proposal, and apply only what you want.

This guide covers the everyday workflow and the additional commands. For a short introduction, see [README.md](README.md).

## Contents

- [Command reference](#command-reference)
- [Installation and agent setup](#installation-and-agent-setup)
- [Prepare a proposal](#prepare-a-proposal)
- [Select and switch sessions](#select-and-switch-sessions)
- [Review changes](#review-changes)
- [Apply changes and handle branch checks](#apply-changes-and-handle-branch-checks)
- [Rename, move, or delete files](#rename-move-or-delete-files)
- [Compare and migrate sessions](#compare-and-migrate-sessions)
- [Discard proposals](#discard-proposals)
- [Configure editors and storage](#configure-editors-and-storage)
- [Shell completion](#shell-completion)
- [Troubleshooting](#troubleshooting)
- [Development and release status](#development-and-release-status)

## Installation and agent setup

### Install the CLI

Python 3.8 or newer is required. The engine uses only Python's standard library; no Python packages or virtual environment are needed.

On macOS or Linux:

```bash
git clone https://github.com/mdulc928/staged-diff.git
cd staged-diff
mkdir -p ~/.local/bin
ln -s "$(pwd)/staged" ~/.local/bin/staged
staged --help
```

If the last command is not found, add `~/.local/bin` to PATH in your shell configuration and open a new terminal. If a link already exists, inspect where it points before replacing it. Keep the checkout in place: the link points to its executable, and skill installation reads the bundled `SKILL.md` beside it.

#### Windows launcher

Windows uses a `staged.cmd` launcher in place of the Unix symlink. A symlink to the extensionless Python script alone does not provide the same command-launching behavior. PowerShell is the supported Windows shell. The installer creates this launcher and a PowerShell completion script; no Windows symbolic link is needed.

From PowerShell, clone the repository and generate the launcher:

```powershell
git clone https://github.com/mdulc928/staged-diff.git
cd staged-diff
py -3 .\staged --install-completion --shell powershell
```

If you already cloned the repository, start from its directory and run only the last command. If `py` is unavailable but Python 3.8+ is installed as `python`, use `python .\staged --install-completion --shell powershell`.

The command prints the launcher's directory, normally `%APPDATA%\staged\bin`. Make it available in future terminals:

1. Open **Edit environment variables for your account** from Windows Search.
2. Under user variables, edit **Path** and add the printed directory as a new entry. Preserve existing entries.
3. Save the change, open a new terminal, and run `staged --help`.

To try it immediately in the current PowerShell window, without waiting for a new terminal:

```powershell
$env:Path += ";$env:APPDATA\staged\bin"
staged --help
```

The temporary PATH update affects only the current terminal; use the user PATH steps above for future terminals. If the installer printed a different directory, substitute that path.

Load the completion script using the source command printed by the installer. Add that command to `$PROFILE` to load it in future PowerShell sessions. Completion matches filenames regardless of capitalization, so `rea<Tab>` can select `README.md`.

Keep the checkout and Python installation in place: the generated launcher references their absolute paths. Re-run the installer from the new location if you move the checkout or change Python installations.

#### npm launcher alternative

For an npm-managed launcher on any platform, run this from the cloned repository:

```bash
npm install -g .
staged --help
```

This alternative requires Node.js 18+ and npm as well as Python. The launcher looks for Python on PATH and also tries `py -3` on Windows. You can run `python3 /path/to/staged --help` directly on POSIX, or `py -3 C:\path\to\staged --help` on Windows, without installing a launcher.

These instructions install from the checkout. They do not assume `@melchi/staged` has been published to the npm registry.

### Install the agent skill

The CLI manages proposals; the `stage` skill tells an agent how to prepare them. Choose your agent harness:

```bash
staged install-skill --tool cursor --default --configure-sandbox
staged install-skill --tool codex
staged install-skill --tool claudecode
staged install-skill --tool windsurf
staged install-skill --tool antigravity
```

Run the command for the harness you use; you do not need to install all five. `--default` makes that tool the default review adapter. Codex and Claude Code adapters use terminal diffs; you can choose a graphical editor separately afterward.

| Harness | Installed skill directory |
| --- | --- |
| Cursor | `~/.cursor/skills/stage` |
| Codex | `~/.agents/skills/stage` |
| Claude Code | `~/.claude/skills/stage` |
| Windsurf/Cascade | `~/.codeium/windsurf/skills/stage` |
| Antigravity | `~/.gemini/config/skills/stage` |

For VS Code, PyCharm, Zed, or another editor, use the skill directory recognized by the agent harness inside that editor:

```bash
staged install-skill --tool zed --target-dir /absolute/path/to/skills/stage
```

`--target-dir` is the directory that will contain `SKILL.md`, including the final `stage` directory. It is not the proposal storage root. Reinstalling replaces that installed `SKILL.md` with the bundled copy.

### Grant staging-directory access

The installer prints the staging root. Your agent needs permission to write there.

For Cursor, `--configure-sandbox` merges that root into the supported sandbox and CLI permission settings, preserving unrelated settings. It is optional. Automatic configuration currently writes `~/.cursor/sandbox.json` and `~/.cursor/cli-config.json`; if you use `CURSOR_CONFIG_DIR` or Linux/BSD `XDG_CONFIG_HOME`, configure permissions in the active CLI config yourself (see [Cursor configuration](https://prod.cursor.com/docs/cli/reference/configuration)). Other harnesses report that automatic configuration is unavailable; add the printed root through their own writable-directory settings.

Installing a skill does not grant filesystem access by itself, and `staged` does not enforce a read-only workspace. The skill instructs the agent to keep proposals isolated; your harness controls actual permissions.

## Prepare a proposal

### With an agent

Open your project and ask:

> Use /stage to prepare these changes for review before applying them.

If the harness uses a skill picker or mentions instead of slash commands, select the `stage` skill there. The agent initializes a session, writes proposed files outside the workspace, and presents a review with the session ID. Select that ID in your terminal:

```bash
cd /path/to/project
staged use --session <id>
staged
```

Ask for refinements before applying if needed. The agent should update the same proposal files. The review dashboard, `staged_changes.md`, records the changes and validation results beside the session's `staging/` directory.

### Manually

Initialize a named session from your project:

```bash
staged init my-conversation --json
```

The output includes the absolute `staging`, `workspace`, and session `directory` paths. Initialization records the current Git branch and binds the session to your shell. Omit `my-conversation` to generate an ID.

Write complete proposed files under the returned staging path, mirroring workspace-relative paths. For example, a proposal for `src/example.py` goes at `<returned-staging-path>/src/example.py`. Copy the original there before editing if you want to start from its current contents. New files and dotfiles are supported; preserve executable permissions where relevant.

Keep the proposal root outside the workspace. Initializing an existing session for the same workspace reuses it without resetting its original branch. It does not copy your whole project, dependencies, or build environment. Run checks against an isolated project copy if they need the entire project; do not run generators against workspace originals while preparing the proposal.

## Select and switch sessions

Select a session once for your working period:

```bash
staged use --session my-conversation
staged
staged diff src/example.py
```

It stays selected in that shell until you switch it or clear the binding:

```bash
staged use --session another-conversation
staged use --clear
```

`use --clear` clears both the shell's session and tool bindings. Environment variables and persistent preferences still apply. Session IDs can be shortened to a unique prefix; ambiguous prefixes fail rather than choosing a session for you.

Find sessions or temporarily inspect a different one:

```bash
staged --sessions                    # sessions for this workspace
staged -R                            # sessions across configured roots
staged --session another-conversation # one-command override
```

Commands work from the repository root or its subdirectories. Session selection stays scoped to the current workspace. Running a command in a different repository while a session is bound may report that the session is unavailable there; switch or clear the binding.

For a persistent default across shells:

```bash
staged set --default-session my-conversation
# Or only for this workspace:
staged set --repo --default-session my-conversation
```

Selection precedence is: explicit flags, `STAGED_SESSION` / `STAGED_TOOL`, shell binding, repository preferences, global defaults, then automatic selection. Without a selected session, the newest available session for this workspace is used.

Agent harnesses sometimes start a new shell for every tool call. To share a binding across those calls, provide the same conversation-specific `STAGED_SHELL_ID` environment value to each shell. Otherwise, run `staged use --session <id>` at the start of each new shell. A selection in the agent's shell does not automatically select that session in your own terminal.

## Review changes

List the current proposal, then inspect a file:

```bash
staged
staged -v                       # also show absolute paths, sizes and times
staged diff src/example.py
staged diff all                  # skip already-applied files
staged diff src/example.py --tool cli
```

The overview uses color for statuses, the active session, and the Git branch in an interactive terminal. Set `NO_COLOR=1` to disable color, or `FORCE_COLOR=1` to retain it when capturing output. Raw path and completion output stays uncolored.

`diff` opens the configured editor, or terminal review when you select `cli`. It does not apply anything unless you add `-a` / `--apply`. New files and deletions are compared with an empty review file. Terminal diffs try `difft`, then Git, then `diff`.

| Status | Meaning |
| --- | --- |
| `MODIFIED` | The workspace file and proposal differ |
| `NEW FILE` | The proposal adds a file |
| `RENAMED` | The manifest changes the filename |
| `RELOCATED` | The manifest moves a file without changing its filename |
| `DELETED` | The manifest proposes deleting a workspace file |
| `APPLIED` | The workspace already matches the proposed operation |
| `MISSING` | A tracked rename has no proposed file contents; repair or clean it before applying |

File matching tries exact paths and filenames before fuzzy matching. You can use a distinctive filename fragment, subsequence, or small typo, but an ambiguous query fails with candidates. Use the full relative path to select exactly one. Paths containing spaces should be quoted.

To retrieve paths for another tool:

```bash
staged path -s src/example.py    # proposed-file path; -s is optional here
staged path -w src/example.py    # original workspace path
staged path --session other-id -s src/example.py
```

These commands print a raw absolute path. A deletion has no proposed-file path; use `-w`. In `path`, `-s` means “staged path”; use the long `--session` option to select a session there.

## Apply changes and handle branch checks

After reviewing, apply a specific file or the entire proposal:

```bash
staged apply src/example.py
staged apply all
```

Equivalent shortcuts:

```bash
staged diff src/example.py -a    # immediately apply that file
staged diff all -a               # immediately apply everything
```

Adding `-a` applies instead of opening a review. Applying all includes manifest renames and deletions. Apply copies complete files; it does not merge your edits with the proposal or commit anything to Git. Review again if the workspace changed since your last comparison. Run your project's appropriate tests after applying.

Two separate Git checks may stop an apply:

- **Protected branch:** the defaults are `main`, `master`, `production`, `release/*`, `staging`, and `develop`. Switch to an appropriate branch, or deliberately override the protection with `--force`.
- **Changed branch:** if you prepared the proposal on `feature/a` and switched to `feature/b`, the tool asks whether you intend to apply it on `feature/b`. This also accounts for branches recorded by migrated proposals. In scripts, `--yes` explicitly confirms that choice.

```bash
staged apply src/example.py --force # deliberately allow a protected branch
staged apply src/example.py --yes   # confirm applying on a different branch
```

Neither flag substitutes for the other. If both conditions apply, both must be addressed. A noninteractive process cannot answer a confirmation prompt.

Configure protection if your repository uses different branch names:

```bash
staged set --repo --protected-branch main --protected-branch 'release/*'
staged set --repo --branch-protection false
staged set --repo --branch-protection true
```

The supplied patterns replace the protected list; they do not append to it. Omit `--repo` for global preferences.

The tool checks selected paths before applying and replaces each destination file atomically. The entire batch is not one transaction: an unexpected disk or permission error can leave earlier files applied. Inspect `staged` and the workspace after a failure. The tool retains proposal files after apply so you can inspect them again.

## Rename, move, or delete files

Keep the operation manifest at `<session-directory>/renames.json`, beside `staging/`:

```json
{
  "src/new-name.py": "src/old-name.py",
  "lib/helper.py": "src/helper.py",
  "_deletions": ["src/obsolete.py"]
}
```

For each rename or move, put the complete proposed contents at its destination under `staging/`. In this example, create `staging/src/new-name.py` and `staging/lib/helper.py`. The manifest maps each destination to the original workspace path. Apply writes the destination before removing the original.

For deletion, add the relative workspace path to `_deletions`; do not remove the workspace file while preparing the proposal. A session can contain only deletions.

Preserve unrelated manifest entries when editing. Use normalized relative paths with forward slashes. A path cannot be both a proposal and a deletion. Symlinks, Git metadata, paths outside the workspace, and overlapping rename chains are rejected. Split a rename chain into separate reviewed operations if necessary.

## Compare and migrate sessions

### Compare approaches

Compare the same proposed file from two sessions in this workspace:

```bash
staged diff --between approach-a approach-b src/example.py
```

Or select the first session and compare against another:

```bash
staged use --session approach-a
staged diff src/example.py --compare-session approach-b
```

Omit the filename, or use `all`, to compare all common proposed files. The relative path must exist in both proposals; sharing a basename in different folders is not enough. Deletions have no proposed contents to compare. Cross-session comparison cannot be combined with `--apply`.

### Carry proposals into another session

```bash
staged migrate --from approach-a --to approach-b
staged migrate --from approach-a --to approach-b --file src/example.py
staged migrate --from approach-a --to approach-b --file 'src/*.py'
```

Quote globs so your shell does not expand them against workspace files. Migration copies selected proposal contents, renames, deletions and provenance. It leaves the source and workspace unchanged. If `--to` names a new session, the tool creates it; if `--to` is omitted, it uses the active session. Select the destination afterward with `staged use --session approach-b` when you want to work on it.

Migration stops if the target already has a proposal for that path, or if a differing workspace file is newer than the source proposal. Review the conflict before deliberately replacing with:

```bash
staged migrate --from approach-a --to approach-b --file src/example.py --force
```

Timestamp checks identify possible staleness; they do not perform a merge or establish which version is correct. `--force` does not allow contradictory rename/deletion operations.

## Discard proposals

Remove one selected proposal or all proposals in the active session:

```bash
staged clean -f src/example.py
staged clean
```

Clean removes the corresponding manifest entries too. It prompts before discarding pending changes. For a deliberate noninteractive cleanup:

```bash
staged clean -f src/example.py --yes
staged clean --yes
```

Clean never changes workspace files and does not undo an apply. Full cleanup preserves session identity and its original branch. An empty session can be reused.

## Configure editors and storage

### Choose a review editor

```bash
staged use --tool cursor          # for this shell
staged set --tool cursor --default # persistent global default
staged set --repo --tool zed      # default for this workspace
staged diff src/example.py --tool pycharm # one-command override
```

Built-in IDs are `cursor`, `antigravity`, `windsurf`, `vscode`, `pycharm`, `zed`, `codex`, `claudecode`, and `cli`. Graphical adapters need their editor launcher installed. If detection fails, install that launcher on PATH or use `--tool cli`.

### Editor compatibility

We recommend using an editor with a command-line launcher that accepts two file paths and opens a diff. Your agent and review editor can be chosen independently:

```bash
staged install-skill --tool claudecode
staged set --tool pycharm --default
staged diff src/example.py --tool pycharm
```

Claude Code supports [inline review of its proposed edits in VS Code](https://code.claude.com/docs/en/vs-code). Its [CLI reference](https://code.claude.com/docs/en/cli-reference) does not document a general two-file diff launcher. The `claudecode` adapter in `staged` uses terminal review (`difft`, then `git diff --no-index`, then `diff`); it does not invoke Claude's inline review UI. The `codex` and `cli` adapters use the same terminal fallback.

The following audit distinguishes documented commands from integrations that still need verification. Commands show the two file arguments as `original` and `proposal`.

| Adapter | Diff command | Evidence / status |
| --- | --- | --- |
| `vscode` | `code -r -d original proposal` | [Official CLI reference](https://code.visualstudio.com/docs/configure/command-line) |
| `pycharm` | `pycharm diff original proposal` | [Official diff reference](https://www.jetbrains.com/help/pycharm/command-line-differences-viewer.html); Windows also uses `pycharm64.exe` or `pycharm.bat`, Linux `pycharm.sh` |
| `zed` | `zed --diff original proposal` | [Official CLI reference](https://zed.dev/docs/reference/cli) |
| `cursor` | `cursor -r --diff original proposal` | Confirmed `diff` and `reuse-window` options in the locally installed vendor CLI parser (Cursor 3.23.12) |
| `antigravity` | `antigravity-ide -r -d original proposal` | Confirmed the same options in the installed IDE CLI parser (app package version 1.107.0); this is the IDE launcher |
| `windsurf` | `windsurf -r --diff original proposal` | Existing adapter retained; command syntax has not been independently verified against vendor documentation or an installed launcher |

Audit date: October 3, 2026. Local parser checks used each application's `Contents/Resources/app/out/cli.js`; these verify accepted options, not an end-to-end graphical launch. Native Windows and WSL editor launches still need testing. WSL path conversion exists for Windows launchers, but `.cmd`/`.bat` launchers are not currently routed through `cmd.exe` from WSL; use a native launcher or `--tool cli` there. For Windsurf, check `windsurf --help` for the flags above before relying on the adapter.

For PyCharm, follow JetBrains' [launcher setup](https://www.jetbrains.com/help/pycharm/working-with-the-ide-features-from-command-line.html). `staged` prefers `pycharm`, checks the platform-specific names above and standard Toolbox script directories, and checks macOS PyCharm application bundles. Linux Snap launchers `pycharm-professional` and `pycharm-community` are also recognized. It does not substitute IntelliJ IDEA or WebStorm for PyCharm. A custom Toolbox launcher name needs a custom adapter.

For another editor, register its actual command syntax:

```bash
staged set-tool my-editor --name 'My Editor' \
  --diff-cmd 'my-editor --diff {orig} {staged}' \
  --open-cmd 'my-editor {staged}'
staged use --tool my-editor
```

Replace `my-editor` with your executable. Both placeholders are required for the diff template; the open template requires `{staged}`. Templates are parsed as argument lists without shell interpolation. Pipelines and shell redirection are not interpreted. Custom IDs cannot replace built-in adapters.

### Choose where proposals live

| Data | macOS / Linux / WSL default | Windows default |
| --- | --- | --- |
| Sessions | `~/.local/share/staged` | `%LOCALAPPDATA%\staged` |
| Global preferences | `~/.config/staged/state.json` | `%APPDATA%\staged\state.json` |
| Repository preferences | `<workspace>/.staged.json` | Same layout |

On POSIX, `XDG_DATA_HOME` and `XDG_CONFIG_HOME` replace the default data/configuration parent directories. All built-in editors share one root.

```bash
staged set --root /absolute/path/to/proposals
staged init my-conversation --root /absolute/path/to/proposals
```

`set --root` saves a default; `init --root` changes the root for that invocation. Continue passing that root, set `STAGED_ROOT`, or save it if subsequent commands need to discover that session. Root precedence is explicit `--root`, `STAGED_ROOT`, repository preference, global preference, custom adapter root, then the platform default.

To discover additional existing session directories without moving them:

```bash
staged set --session-root /path/to/other/sessions
```

Repeat `--session-root` within the command to save multiple additional roots. The supplied list replaces the previous additional-root list. Each session must have `.workspace` containing its absolute workspace path and a `staging/` directory. Additional locations are opt-in; the tool does not infer them from editor transcripts.

## Shell completion

Run the command for your shell:

```bash
staged --install-completion --shell bash
staged --install-completion --shell zsh
staged --install-completion --shell powershell
```

Bash and Zsh installation symlinks the scripts from `completions/` in your checkout (or installed package) into the staging configuration directory and adds a managed block to the corresponding rc files. Updating the source scripts updates the installed links automatically. Keep the checkout in place; reinstall completion if you move it. Re-running installation replaces older copied scripts with links and updates the rc block rather than adding duplicates.

An open shell may already have the old function in memory. After an update, run `source ~/.zshrc` for Zsh, source the installed Bash completion script, or open a new terminal. The installer prints the appropriate activation command.

PowerShell installation also links to the bundled completion script and prints a command to load it. If Windows denies symlink creation, it installs a small loader that reads the bundled script instead of copying its contents. Add that source command to `$PROFILE` if you want it loaded in future sessions.

On Windows, PowerShell installation also writes a `staged.cmd` launcher and prints the directory to add to PATH. Bash, Zsh and PowerShell completion match filenames case-insensitively while preserving their actual spelling.

## Command reference

Syntax below uses `<value>` for a required value, `[value]` for an optional argument, and `|` for alternatives. Replace placeholders rather than typing the angle brackets. Quote paths with spaces and patterns containing shell wildcards.

```text
staged [global options] [command] [command options]
staged <command> --help
```

### Global options

These selection options work before or after a subcommand:

| Option | Meaning |
| --- | --- |
| `-t <id>`, `--tool <id>` | Choose the editor adapter for this invocation; with `use` or `set`, save the selection in that command's scope |
| `-s <id>`, `--session <id>` | Select a session by exact ID or unique prefix; with `use`, save the binding |
| `--root <path>` | Override the staging root; with `set`, save it as a preference; with `set-tool`, store the custom adapter's root |
| `-v`, `--verbose` | Include paths, file sizes and modification times in the proposal overview |
| `-h`, `--help` | Show help without executing an operation |

Exception: after `path`, `-s` means `--staged`, so use `--session <id>` to select a session there.

### List proposals and sessions

```text
staged [-v]
staged --sessions
staged -R
```

| Form | Result |
| --- | --- |
| `staged` or `staged diff` | Show the active session and proposed-file statuses |
| `staged --sessions` | List sessions belonging to the current workspace |
| `staged -R`, `staged --all-repos` | List sessions across workspaces in the configured roots |

Listing switches are top-level options, not subcommands. See [review changes](#review-changes) for the status meanings.

### init

```text
staged init [id] [--json]
```

Create or reuse a session for the current workspace and select it for this shell. Omit the ID to generate a UUID. `--json` prints machine-readable paths and session metadata. Reusing a session preserves its original branch. Use `--root <path>` to initialize under a different root. See [prepare a proposal](#prepare-a-proposal).

### use

```text
staged use --session <id> [--tool <id>]
staged use --tool <id>
staged use --clear
```

Select a session or tool for the current shell until you switch or clear the binding. The positional form `staged use <id>` is also accepted; do not combine it with `--session`. `--clear` clears both bindings and cannot be combined with a selection. See [select and switch sessions](#select-and-switch-sessions) for agent tool calls and environment-variable precedence.

### diff

```text
staged diff [<file>|all]
staged diff [<file>|all] -a [--force] [-y]
staged diff --between <session-a> <session-b> [<file>|all]
staged diff [<file>|all] --compare-session <id>
```

| Option or target | Behavior |
| --- | --- |
| No target, without `-a` | Show the proposal overview |
| `<file>` | Review one uniquely matched proposed file |
| `all` | Review all pending changes, skipping applied files |
| `-a`, `--apply` | Apply instead of opening review; an omitted target means all |
| `--between <a> <b>` | Compare the same relative file in two sessions; omitted target means all common proposed files |
| `--compare-session <id>` | Compare the active session with the specified session; omitted target means all common proposed files |
| `--force` | When applying, permit a protected branch |
| `-y`, `--yes` | When applying, confirm a change from the proposal's original branch |

`--between` and `--compare-session` are mutually exclusive and cannot be combined with apply. Use `--tool cli` for terminal review. See [review changes](#review-changes) and [compare and migrate sessions](#compare-and-migrate-sessions).

### apply

```text
staged apply [<file>|all] [--force] [-y]
staged apply -f <file> [--force] [-y]
```

Apply complete proposed files, renames and deletions. An omitted target means **all**. `-f <file>` / `--file <file>` is an alternative to the positional target; do not supply both.

`--force` permits a protected branch. `-y` / `--yes` confirms applying after the Git branch changed. They address separate checks. See [apply changes and handle branch checks](#apply-changes-and-handle-branch-checks).

### path

```text
staged path [-s|-w] <file>
```

Print one raw absolute path for the uniquely matched file.

| Option | Path printed |
| --- | --- |
| No option, `-s`, or `--staged` | Proposed-file path |
| `-w`, `--workspace` | Original workspace path, accounting for a tracked rename |

These path switches are mutually exclusive. A deletion has only a workspace path. To select another session, use `staged path --session <id> -s <file>`.

### clean

```text
staged clean [-f <file>] [-y]
```

Discard all proposals in the active session, or exactly one uniquely matched proposal with `-f <file>` / `--file <file>`. Remove corresponding operation-manifest entries too. `-y` / `--yes` confirms discarding pending changes without a prompt. Workspace files are never changed. See [discard proposals](#discard-proposals).

### migrate

```text
staged migrate --from <id> [--to <id>] [-f <query-or-glob>] [--force]
```

| Option | Meaning |
| --- | --- |
| `--from <id>` | Required source session in this workspace |
| `--to <id>` | Destination session; create it if the ID is new. If omitted, use the active session |
| `-f <query-or-glob>`, `--file <query-or-glob>` | Copy only a matched file or files matching a quoted glob; if omitted, copy all proposals |
| `--force` | Permit replacement despite existing target proposals or detected staleness; invalid operation conflicts still fail |

Source proposals and workspace files remain intact. See [compare and migrate sessions](#compare-and-migrate-sessions).

### set

```text
staged set [--repo|--default] [preference options]
```

Save preferences globally by default, or in the current workspace's `.staged.json` with `--repo`. `--default` explicitly selects global preferences; it cannot be combined with `--repo`.

| Preference option | Value saved |
| --- | --- |
| `--tool <id>` | Default editor adapter |
| `--default-session <id>` | Default session; must resolve to an existing session in this workspace |
| `--root <path>` | Default staging root |
| `--branch-protection true\|false` | Enable or disable the protected-branch check |
| `--protected-branch <pattern>` | Replace the protected patterns; repeat within the command to supply multiple patterns |
| `--session-root <path>` | Replace the additional discovery roots; repeat within the command to supply multiple roots |

Preferences not supplied are preserved. See [configure editors and storage](#configure-editors-and-storage).

### set-tool

```text
staged set-tool <id> --name <name> --diff-cmd <template> --open-cmd <template> [--root <path>]
```

Register a custom adapter in global configuration. The ID, display name and both command templates are required. Diff templates must contain `{orig}` and `{staged}`; open templates must contain `{staged}`. Quote each template as one argument. An optional root supplies the adapter's staging default when no higher-priority root is configured. Built-in IDs cannot be replaced. See [choose a review editor](#choose-a-review-editor) for an example.

### install-skill

```text
staged install-skill [--tool <id>] [--default] [--target-dir <path>] [--configure-sandbox]
```

| Option | Meaning |
| --- | --- |
| `--tool <id>` | Choose the harness; if omitted, use the active adapter |
| `--default` | Also save this adapter as the global default |
| `--target-dir <path>` | Install `SKILL.md` inside this exact directory; required for adapters without a configured skill registry |
| `--configure-sandbox` | Request supported staging-root permission configuration; currently implemented for Cursor |

Installation reports its destination and staging root. Reinstallation replaces the installed skill file. See [installation and agent setup](#installation-and-agent-setup) for harness paths and permission behavior.

### Install completion

```text
staged --install-completion [--shell bash|zsh|powershell]
```

This is a top-level operation. Omit `--shell` to use the detected POSIX shell, or PowerShell on Windows. See [shell completion](#shell-completion) for installed files and activation steps.

### Script and completion helpers

These top-level switches print plain, newline-separated values for scripts and generated completion:

| Switch | Output |
| --- | --- |
| `--list`, `--complete` | Relative proposal paths in the active session, including tracked deletions and missing rename destinations |
| `--complete-sessions` | Session IDs for this workspace |
| `--complete-tools` | Available built-in and registered adapter IDs |

### Exit status

Successful operations return `0`; validation and operational failures return nonzero. Interrupting the CLI returns `130`. Any command's `--help` is safe to invoke without completing its required arguments.

## Troubleshooting

| Situation | What to do |
| --- | --- |
| `staged` is not found | Check PATH and the installed link or launcher; try invoking the Python script directly |
| No staging session found | Run from the correct project, inspect `staged --sessions` / `staged -R`, and confirm the configured root; initialize a session if needed |
| Session selection seems ignored | Check `STAGED_SESSION`, `STAGED_TOOL`, and command-line overrides; they take priority over `use` |
| Selection disappears between agent calls | Reuse a shell or pass a consistent conversation-specific `STAGED_SHELL_ID`; otherwise bind at the start of the new shell |
| A file or session is ambiguous | Use its exact relative path or complete session ID |
| Editor executable is missing | Install the editor launcher on PATH, select another adapter, or review with `--tool cli` |
| Agent cannot write proposals | Grant the printed staging root through the harness's writable-path settings |
| Protected branch or changed-branch message | Review the destination branch and follow the [branch checks](#apply-changes-and-handle-branch-checks) instructions |
| `MISSING` status | Restore the complete proposed rename destination, or discard its tracking entry with selective clean |
| Migration refuses to copy | Compare the proposals and current workspace; use `--force` only if replacement is intended |
| JSON configuration error | Repair the identified JSON file; malformed state is not silently ignored |
| Apply fails after some files succeeded | Inspect statuses and workspace contents before retrying; batch apply is not a transaction |

Use `staged <command> --help` for the accepted flags. Help does not execute the operation, even if required arguments are missing.

## Development and release status

```bash
python3 -m unittest discover -s tests -v
npm pack --dry-run
```

Tests use isolated temporary homes, workspaces, and staging roots. See [SPEC.md](SPEC.md) for the behavioral contract and [IMPLEMENTATION.md](IMPLEMENTATION.md) for the comparison and outstanding release checks. Native Windows/WSL behavior, actual editor launches, non-Cursor automatic sandbox integration, and the lookup performance target have remaining verification or implementation work documented there.
