# @melchi/staged

`staged` keeps an agent's proposed edits in real files outside your working tree. Review the differences in your editor, then apply the changes you choose. `/stage` is the accompanying agent skill; neither changes Git's index.

## Install

Requires **Python 3.8+**. First, clone the repository:

```bash
git clone https://github.com/mdulc928/staged-diff.git
cd staged-diff
```

On **macOS or Linux**, create a symlink:

```bash
mkdir -p ~/.local/bin
ln -s "$(pwd)/staged" ~/.local/bin/staged
```

Ensure `~/.local/bin` is on PATH.

On **Windows** (PowerShell or CMD), create the command launcher instead of a symlink:

```powershell
py -3 .\staged --install-completion --shell cmd
```

Add the printed directory (normally `%APPDATA%\staged\bin`) to your **user PATH**, then open a new terminal and run `staged --help`. See [Windows setup](GUIDE.md#windows-launcher) for the steps. Alternatively, `npm install -g .` installs a cross-platform launcher; Python is still required.

Install the agent skill, using Cursor as an example:

```bash
staged install-skill --tool cursor --default --configure-sandbox
```

For other editors and agent harnesses, see [setup in the guide](GUIDE.md#installation-and-agent-setup).

## Review and apply

From your project, ask your agent to **“Use /stage to prepare these changes for review.”** It will create the proposal and report its session ID. Select that session in your terminal:

```bash
staged use --session <id>
staged                                # see what's proposed
staged diff src/example.py             # review a file in your editor
staged diff src/example.py -a          # apply that file when you're ready
staged diff all                        # review the remaining changes
staged apply all                       # apply the entire proposal

# Prefer reviewing in the terminal?
staged diff src/example.py --tool cli
```

Applying to protected branches, or after switching Git branches, requires an explicit override or confirmation; [the guide explains both](GUIDE.md#apply-changes-and-handle-branch-checks).

**The tool does more:** compare sessions, migrate proposals, configure editors, clean up files, and more. See [GUIDE.md](GUIDE.md) for detailed instructions, examples, and troubleshooting, or run `staged --help`.

<!-- Add your three thank-you links below. -->
