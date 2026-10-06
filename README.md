# @melchi/staged

Below is my insight, and everything else following that is syntax.

💡 For things that I want to know and understand (like when I'm accountable to someone for the code I'm generating):

- Have the agent work outside the working tree.
- I pursue my own understanding during that time.
- _Later_, bring the changes.

This is how we learned to code in the first place, and I imagine this is how we get better too when using AI a lot.

`staged` gives the agent its own working space outside my tree. My IDE stays mine and I apply only what I want to keep.

My hope is that you can use this as **a starting point** for your own workflow setup. Let's get to it.

## The "Syntax"

- **CLI Tool:** `staged`
  - Tracks an agent's proposed edits in real files outside your working tree.
  - `staged diff`- open a visual diff with working tree in your favorite IDE,
  - `staged apply` - apply the changes you like.
- **Agent SKILL:** `/stage` - Informs agent of this workflow

> **Note:** I recommend pairing `staged` with an editor that can open a visual two-file diff from the command line: VS Code(-ish) , PyCharm, or Zed. See [editor setup](GUIDE.md#editor-compatibility). Your diff tool does not need to be same as your agent.

**P.S.** I would love to learn what you think, if you'll just leave a comment me a message on the social platform of your choice. You can find my links in my profile 🙏.

## Install and Setup

> Requires **Python 3.8+**.

First, clone the repository:

```bash
git clone https://github.com/mdulc928/staged-diff.git
cd staged-diff
```

### Quick Setup

> Requires `Node.js >= 18` / `npm`

Installs the cross-platform command launcher automatically:

```bash
npm install -g .
```

### Manual Setup

If you prefer not using `npm`, configure the CLI manually for your platform:

#### macOS or Linux

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/staged" ~/.local/bin/staged
```

⭐️ Ensure `~/.local/bin` is in `PATH`.

#### Windows

⭐️ Use `PowerShell`

```powershell
# Create the command launcher (%APPDATA%\staged\bin):
py -3 .\staged install-launcher

# Add to user PATH permanently:
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";$env:APPDATA\staged\bin", "User")
```

### Shell Completion (Optional)

Enable tab-completion for commands and staged filenames (also prompted to install on first run):

```bash
staged install-completion --shell zsh        # macOS / Linux (Zsh)
staged install-completion --shell bash       # Linux / Git Bash
staged install-completion --shell powershell # Windows (PowerShell)
```

### Install Agent Skill

Install the `/stage` skill into your AI agent harness (e.g. Cursor, Antigravity, Claude Code, Windsurf, Codex, Zed):

```bash
# Example with Cursor:
staged install-skill --tool cursor --default --configure-sandbox
```

For other editors and agent harnesses, see [setup in the guide](GUIDE.md#installation-and-agent-setup).

## Use

In your agent, enter **“/stage solve all of life's mysteries”**. It will stage the changes, and report its session ID.

```bash
# staged use --session <id>               # if you need to switch sessions
staged                                    # see everything the AI staged
staged diff -f example.py                 # review a file in your editor
staged diff -f example.py -a              # apply that file when you're ready
staged diff -f example.py -c              # clean/discard that file if not needed


# Useful

staged open -f example.py                 # edit the staged file
staged open --meta -f staged_changes.md   # edit review notes or dashboards
staged diff --all                         # review the remaining changes
staged apply --all                        # apply all staged changes
staged clean --session                    # discard the active session directory

# Prefer reviewing in the terminal?
staged diff -f src/example.py --tool cli
staged open --meta -f staged_changes.md --tool cli
```

Use `-f` / `--file` for one file and `--all` for bulk operations (or `--session` with `clean` to discard a session). Positional filenames and positional `all` are no longer accepted. Apply, clean, migrate, and diff actions/comparisons require a selection; `staged diff` alone shows the overview. Open searches staged files by default; `--meta` searches editable session metadata only.

**The tool does more:** compare sessions, migrate staged changes, clean up files, configure your favorite editor, and more.

See [GUIDE.md](GUIDE.md) for detailed instructions, examples, and troubleshooting, or run `staged --help`. Use `staged <command> --help` for one command or `staged --help-all` for the complete option reference.

## License

[MIT](LICENSE) © 2026 [Melchisedek Dulcio](https://github.com/mdulc928). See [LICENSE](LICENSE) for details.

#### Ways to Say Thank You and Support

- Follow on Socials (checkout[ Github profile](https://github.com/mdulc928))
- [Say Thank you: $3 (Stripe)](https://buy.stripe.com/14AdR91jf8Vj2wu7ur1Jm01)
- [Say Big Thank you: $10 (Stripe)](https://buy.stripe.com/bJe7sL6Dz2wVb30bKH1Jm02)
- [Go Crazy: (you choose) (Stripe)](https://buy.stripe.com/8x2fZhfa5gnL5IG9Cz1Jm03)
