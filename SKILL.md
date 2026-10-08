---
name: stage
description: Prepare proposed code changes as isolated files for review with the staged CLI. Use when the user asks to stage agent changes first, compare a proposal, or keep proposed edits out of the working tree until approval. This is proposal staging, not Git index staging.
---

# Prepare a proposal with `/stage`

Never create, modify, rename, or delete files in the working tree unless the user has explicitly authorized those changes there. This applies to direct edits, `staged apply`, and indirect writes by formatters, generators, tests, or other commands. A request to stage, propose, review, or refine changes does not authorize working-tree writes.

Keep proposal files and session metadata outside the working tree; writing them is allowed as part of the staging workflow. If it is unclear whether the user authorized a working-tree write or what scope they authorized, stop before that write and ask for clarification. Never assume permission.

Preserve the user's existing explicit authorization: a request to apply is sufficient for its stated scope; do not request it again or expand it to unrelated changes.

## Discover available commands

First run `staged --help` to discover which commands and options are supported by the installed CLI. Every command can also be run with `--help` (e.g. `staged init --help`, `staged read --help`, `staged write --help`, `staged summarize --help`, `staged diff --help`, `staged apply --help`) or `staged --help-all` to output more helpful documentation, supported flags, and usage details. Never guess command names, arguments, or syntax when `--help` provides authoritative docs.

## Establish the session

From the target workspace, run `staged init <conversation-id> --json`. Use the actual internal conversation (chat/thread) ID supplied by the host, not a semantic name, task title, or descriptive slug. If the internal ID is unavailable, omit the argument and use the generated session ID; do not invent a semantic name. Reuse that session for refinements. The command returns the absolute `directory`, `staging`, and `workspace` paths and records the original Git branch.

Use the returned paths; do not construct editor-specific storage paths. If the harness denies access to the staging root, explain which path needs permission. Installing a skill does not itself grant filesystem access.

Run `staged use --session <id>` to select the session for the current working period. Subsequent commands use that binding until you select another session or run `staged use --clear`; you do not need to repeat `--session` on every command.

Keep subsequent commands in the same shell. For an agent harness that starts a fresh shell for each tool call, use the same `STAGED_SHELL_ID` environment value across calls, unique to this conversation. If that is unavailable, run `staged use --session <id>` at the start of each new shell before other staged commands. Explicit `STAGED_SESSION` or `--session` overrides the binding.

## Write the proposal

Use the managed `staged read`, `write`, `create`, `copy`, `rename`, and `delete` commands for file access when available, rather than requiring direct filesystem edits. They use exact relative paths; writes are confined to `<staging>/<relative-workspace-path>`. Host filesystem permissions still apply. Read originals with `--workspace`, then explicitly seed the first partial edit with `--from-workspace`:

```bash
staged read --workspace -f src/example.py --start-line 10 --end-line 20 --json
staged write -f src/example.py --from-workspace --start-line 10 --end-line 20 --stdin --expect-sha256 <hash> < replacement.txt
```

Use the full-file hash returned by `read --json` to guard edits. Omit `--from-workspace` once the staged copy exists; read that copy before subsequent edits. Line ranges are one-based and inclusive; omitting `--end-line` on write inserts before the start line. Byte edits use zero-based `--offset` and `--delete-count`; reads use `--offset` and `--length`. Preserve needed line endings in the replacement input.

For a complete file, use `staged write -f <path> --stdin` or `--text <content>`. `--append` writes at EOF. JSON reads identify UTF-8 or Base64 content in `encoding`. Files remain complete proposals, even when edited in ranges; workspace originals are unchanged. On older versions without these commands, edit only copies under the returned staging path and preserve executable permissions.

Keep metadata beside `staging/`, never inside it:

```text
<session>/
  .workspace
  session.json
  renames.json
  summaries.json
  staged_changes.md
  staging/
    src/example.py
```

Use these exact-path operations for proposal files. Immediately summarize each operation before running the next; the examples below are independent alternatives:

```bash
staged create -f src/new.py --text 'initial contents'  # Omit content flags for an empty file
staged copy --workspace -f src/example.py             # Workspace → staging, same relative path
staged copy -f src/example.py --to src/variant.py      # Staging → staging
staged rename -f src/example.py --to src/new-name.py   # Staged source only
staged delete -f src/example.py                       # Discard staged proposal / cancel pending deletion
staged delete -f src/obsolete.py --workspace           # Record deletion for later authorized apply
```

All destinations are inside staging. Copy accepts a staged source by default or a workspace source with `--workspace`; use `--to` for a different destination. Create also accepts binary contents through `--stdin`. Add `--json` for structured operation results. Create, copy, and rename refuse existing staged destinations and conflicting pending operations; rename also refuses an unrelated existing workspace destination. Use `write` to intentionally edit an existing proposal.

Rename moves the staged file and its summary, preserving its original workspace path in `renames.json` for later apply. Renaming a new file creates no workspace rename; repeated renames retain the original path. Plain delete removes only the staged proposal and its metadata. `delete --workspace` removes any staged replacement and records the exact workspace path in `_deletions`; it never deletes the real file immediately. If deletion intent is ambiguous, ask which behavior the user wants. Discard an overlapping rename proposal before recording a workspace deletion.

After create, copy, rename, or `delete --workspace`, immediately update the affected file summary and session summary. After plain delete, its file summary is removed automatically; immediately update the session summary with `staged summarize -m <message>`.

On older CLIs without these operations, edit only staged copies and maintain `renames.json` directly: map each rename destination to its original workspace path, and put deletion proposals in `_deletions`. Preserve unrelated entries. Use forward-slash relative paths; symlinks, Git metadata, traversal, and overlapping rename chains are unsupported. A path cannot be both a staged file and a deletion.

## Keep summaries current after every modification

Immediately after each file modification, update that file's summary and the session summary before modifying another file or making another edit. For a discarded proposal whose file summary has been removed, update the session summary instead. Do the same after each rename or deletion manifest change. Do not modify all code files first and batch the summaries afterward.

Summaries describe the current cumulative proposal, not just the last edit or a chronological log. Prefer one sentence for each summary; use at most three short sentences when needed. State what changed and why. Keep validation details and longer review notes in `staged_changes.md`.

```bash
# Run immediately after modifying this file; updates both summaries together.
staged summarize -f src/example.py -m 'Handle empty input without raising an error.' --summary 'Make input handling tolerate empty values.'

# Inspect structured summaries, or set just the session summary.
staged summarize --json
staged summarize -m 'Make input handling tolerate empty values.'
```

`-f` requires an exact proposed path: the destination for a rename or the original path for a deletion. `--stdin` can replace `-m` / `--message`; `--clear` removes the selected summary. Summary updates preserve unrelated entries. After partial cleanup or migration, immediately revise the session summary to describe the remaining or combined proposal.

Summaries live in `<session>/summaries.json`, beside `staging/`:

```json
{
  "session": "Make input handling tolerate empty values.",
  "files": {
    "src/example.py": "Handle empty input without raising an error."
  }
}
```

If the installed CLI lacks `summarize`, update this JSON file directly after each modification, preserving unrelated entries. Do not place it inside `staging/`.

Run `staged` to inspect the selected session's staged changes. `staged diff -f <path>` opens review in the configured editor; pass `--tool cli` (e.g. `staged diff -f <path> --tool cli`) to output unified diffs directly in the terminal, which is ideal for programmatic inspection by agents. Never run `staged diff all` automatically, as that opens GUI editor tabs for all files at once. Use exact relative paths when filenames are ambiguous. `staged --sessions` includes each session summary. `staged diff -v` (or `--verbose`) shows the session and per-file summaries; add `-v` to `diff -f <path>` or `diff --all` to print summaries before opening diffs.

## Present and refine

Create or update `<session>/staged_changes.md`. Put the session ID prominently at the top, followed by the origin branch, staging path, changes and validation performed. Link the staged files and workspace originals using links supported by the host. Include deletions and renames in the review.

Include the session ID prominently in the response and offer the concrete review/apply commands:

```text
staged use --session <id>
staged diff -v
staged diff -v -f <relative-path>
staged apply --all
```

Explain tests that could not run against the isolated staged files. Do not run formatters or generators against the original workspace while staging. If full project validation needs a separate disposable copy, keep it outside the workspace and report where it ran.

If the user requested review before apply and has not authorized applying, present the proposal and await their decision. Refinements stay in the same staging session.

## Apply when authorized

Before every apply, inspect the current working tree, including Git-staged changes, unstaged edits, and untracked files (`git status --short`, `git diff`, and `git diff --cached` when Git is available). Read the current contents of each affected path and compare them with the proposal, including rename sources/destinations and deletion targets. Do not assume the workspace still matches the version used to prepare the proposal; `staged apply` copies complete files and does not merge concurrent edits.

Use `staged diff -f <path> --tool cli` to inspect the exact differences between the staged proposal and the current workspace directly in the terminal. Do not attempt to pass external staging directory paths to `git diff <commit> <path>`, as Git cannot mix commit references with external filesystem paths.

Reconcile any workspace changes in staging first, preserving the user's edits and incorporating the requested change into the combined proposal. Update the affected file and session summaries immediately after each reconciliation edit, then review and validate the reconciled result. Do not overwrite user changes merely because applying is authorized. Replace an existing change only when the user explicitly requests it or it is clearly required by the specific requested change; if intent or conflict resolution is uncertain, ask before applying. Leave unrelated files and Git index entries untouched.

Immediately before applying, recheck that the affected workspace files have not changed since reconciliation (for example, compare their hashes). If they changed, reconcile again in staging before proceeding. A clean Git status alone is insufficient: committed changes may also have occurred since the proposal was prepared.

With the intended session selected, run `staged apply -f <relative-path>` or `staged apply --all` for the authorized scope. Do not silently add `--force` or `--yes` when branch protection or a change from the staging session's original Git branch blocks application; explain the concrete branch condition. Existing explicit approval for that condition can be used without asking again.

Run the checks appropriate to the project after applying. Report the outcome. Keep the staged changes until the user requests cleanup; `staged clean -f <path>` or `staged clean --all` discards staged changes and never reverts workspace files.
