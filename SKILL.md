---
name: stage
description: Prepare proposed code changes as isolated files for review with the staged CLI. Use when the user asks to stage agent changes first, compare a proposal, or keep proposed edits out of the working tree until approval. This is proposal staging, not Git index staging.
---

# Prepare a proposal with `/stage`

Keep proposed source changes outside the working tree until the user authorizes applying them. Preserve the user's existing authorization: a request to apply is sufficient; do not request it again.

## Establish the session

From the target workspace, run `staged init <conversation-id> --json`. Use the actual conversation ID when available; otherwise omit it and use the generated session ID. Reuse that session for refinements. The command returns the absolute `directory`, `staging`, and `workspace` paths and records the original Git branch.

Use the returned paths; do not construct editor-specific storage paths. If the harness denies access to the staging root, explain which path needs permission. Installing a skill does not itself grant filesystem access.

Run `staged use --session <id>` to select the session for the current working period. Subsequent commands use that binding until you select another session or run `staged use --clear`; you do not need to repeat `--session` on every command.

Keep subsequent commands in the same shell. For an agent harness that starts a fresh shell for each tool call, use the same `STAGED_SHELL_ID` environment value across calls, unique to this conversation. If that is unavailable, run `staged use --session <id>` at the start of each new shell before other staged commands. Explicit `STAGED_SESSION` or `--session` overrides the binding.

## Write the proposal

Read originals from the workspace. Write complete proposed files under `<staging>/<relative-workspace-path>`, including new files and dotfiles. Preserve executable permissions where relevant. Edit only these copies while preparing or refining the proposal.

Keep metadata beside `staging/`, never inside it:

```text
<session>/
  .workspace
  session.json
  renames.json
  staged_changes.md
  staging/
    src/example.py
```

For a rename, write the complete destination file and map its relative destination path to its original path in `renames.json`. For deletion, add the original relative path to `_deletions` without deleting the workspace file:

```json
{
  "src/new-name.py": "src/old-name.py",
  "_deletions": ["src/obsolete.py"]
}
```

Preserve unrelated entries when editing the manifest. Use forward-slash relative paths. Symlinks, Git metadata, path traversal and overlapping rename chains are unsupported. A path cannot be both a proposed file and a deletion.

Run `staged` to inspect the selected session's proposal. `staged diff <path>` opens review; `--tool cli` requests terminal output. Use exact relative paths when filenames are ambiguous.

## Present and refine

Create or update `<session>/staged_changes.md`. Put the session ID prominently at the top, followed by the origin branch, staging path, changes and validation performed. Link the proposed files and workspace originals using links supported by the host. Include deletions and renames in the review.

Include the session ID prominently in the response and offer the concrete review/apply commands:

```text
staged use --session <id>
staged
staged diff <relative-path>
staged apply all
```

Explain tests that could not run against the isolated proposal. Do not run formatters or generators against the original workspace while staging. If full project validation needs a separate disposable copy, keep it outside the workspace and report where it ran.

If the user requested review before apply and has not authorized applying, present the proposal and await their decision. Refinements stay in the same staging session.

## Apply when authorized

With the intended session selected, run `staged apply <relative-path|all>` for the authorized scope. Do not silently add `--force` or `--yes` when branch protection or a change from the proposal's original Git branch blocks application; explain the concrete branch condition. Existing explicit approval for that condition can be used without asking again.

Run the checks appropriate to the project after applying. Report the outcome. Keep the staged proposal until the user requests cleanup; `staged clean` discards proposals and never reverts workspace files.
