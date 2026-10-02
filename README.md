# @melchi/staged

Unified staging engine and `/stage` agent workflow for Cursor, Antigravity IDE, Windsurf, VS Code, JetBrains, Zed, and CLI.

## Overview

Proposed work stays out of the workspace until you choose to apply it. The AI agent edits isolated copies on disk, allowing full visual inspection and native side-by-side graphical diffing in your active editor.

- **CLI Tool (`staged`):** Inspects, compares, and applies changes that are currently _staged_.
- **Agent Skill (`/stage`):** The slash command instructing AI assistants to prepare changes in staging.

| Host            | Staging root                                         | Skill Path                                         | Diff Command                            |
| :-------------- | :--------------------------------------------------- | :------------------------------------------------- | :-------------------------------------- |
| **Cursor**      | `~/.local/share/stage/<conv_id>/staging/`            | [`skills/cursor/SKILL.md`](skills/cursor/SKILL.md) | `cursor -r --diff <orig> <staged>`      |
| **Antigravity** | `~/.gemini/antigravity-ide/brain/<conv_id>/staging/` | [`SKILL.md`](SKILL.md)                             | `antigravity-ide -r -d <orig> <staged>` |
| **Windsurf**    | `~/.local/share/stage/<conv_id>/staging/`            | [`SKILL.md`](SKILL.md)                             | `windsurf -r --diff <orig> <staged>`    |
| **VS Code**     | `~/.local/share/stage/<conv_id>/staging/`            | [`SKILL.md`](SKILL.md)                             | `code -r -d <orig> <staged>`            |

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

## Quick Installation & Symlinks

Link the command and completions:

```bash
ln -sfn "$(pwd)/scripts/staged" ~/.local/bin/staged
ln -sfn "$(pwd)/scripts/staged" ~/.local/bin/stage
```

Install shell tab-completion:

```bash
staged --install-completion
```

Configure `/stage` skill and sandbox write permissions for your editor:

```bash
# For Cursor:
staged install-skill --tool cursor --default

# For Antigravity IDE:
staged install-skill --tool antigravity --default
```
