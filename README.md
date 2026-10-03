# @melchi/staged

A staging engine and `/stage` agent skill for different harnesses and IDEs like Cursor, Claude Code, Codex, Antigravity IDE, Zed, Pycharm and CLI. The idea is derived from how we all learned to program: 
1. You have a puzzle.
2. You try to do the puzzle yourself.
3. _Then_ you go check the solution.

## Overview

Proposed work stays out of the workspace until you choose to apply it. The AI agent edits isolated copies on disk, allowing full visual inspection and native side-by-side graphical diffing in your active editor.

- **CLI Tool (**`staged`**):** Inspects, compares, and applies changes that are currently *staged*.
- **Agent Skill (**`/stage`**):** The slash command instructing AI assistants to prepare changes in staging.

## Quick Installation Steps

1. Clone the Repo:
```bash
git clone https://github.com/mdulc928/staged-diff.git
cd staged-diff
```

2. Link the command:

```bash
ln -sfn "$(pwd)/staged" ~/.local/bin/staged
ln -sfn "$(pwd)/staged" ~/.local/bin/stage
```

3. Install shell tab-completion (writes completion scripts to `~/.zfunc` / `~/.config/staged/completions` and adds a marked block to your shell rc):

```bash
staged --install-completion
# then restart the shell
```

Configure `/stage` skill and sandbox write permissions for your editor:

```bash
# For Cursor:
staged install-skill --tool cursor --default

# For PyCharm (command-line launcher `charm` must be on PATH):
staged install-skill --tool pycharm --default

# For Zed IDE:
staged install-skill --tool zed --default

# For Antigravity IDE:
staged install-skill --tool antigravity --default

```

## CLI Usage

```bash
# List all staged files and status ([APPLIED], [MODIFIED], [NEW FILE], [RENAMED], [RELOCATED]):
staged

# Open side-by-side diff in your IDE (fuzzy matched):
staged diff Player.svelte

# Apply a single staged file immediately to your workspace:
staged diff Player.svelte -a

# Open diffs for all modified files:
staged diff all

# Apply all staged files:
staged diff all -a

# Diff the same file between two conversation sessions:
staged diff --between <session-1> <session-2> Player.svelte

# Print raw absolute path on disk:
staged path Player.svelte

# Selectively clean only 1 staged file:
staged clean -f Player.svelte

# Purge the current session's staging directory:
staged clean

# Lock active session for current shell instance:
staged use <session-id>

```
