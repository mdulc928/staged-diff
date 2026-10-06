"""CLI contract tests: every process uses an isolated home, staging root and Git repository."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'staged'
loader = importlib.machinery.SourceFileLoader('staged_module', str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
staged = importlib.util.module_from_spec(spec)
loader.exec_module(staged)


class CLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='staged-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / 'home'
        self.repo = self.base / 'repo'
        self.root = self.base / 'proposals'
        self.config = self.home / ('AppData/Roaming/staged' if os.name == 'nt' else '.config/staged')
        for path in (self.home, self.repo):
            path.mkdir()
        self.env = dict(os.environ)
        for key in list(self.env):
            if key.startswith(('STAGED_', 'STAGE_', 'GIT_')) or key in ('NO_COLOR', 'FORCE_COLOR'):
                self.env.pop(key)
        self.env.update(HOME=str(self.home), USERPROFILE=str(self.home),
                        XDG_CONFIG_HOME=str(self.home / '.config'), XDG_DATA_HOME=str(self.home / '.local/share'),
                        APPDATA=str(self.home / 'AppData/Roaming'), LOCALAPPDATA=str(self.home / 'AppData/Local'),
                        STAGED_ROOT=str(self.root), STAGED_TOOL='cli', STAGED_SHELL_ID='test-shell',
                        GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        self.git('init', '-q')
        self.git('symbolic-ref', 'HEAD', 'refs/heads/feature/test')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        (self.repo / 'existing.txt').write_text('original\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo)] + list(args), env=self.env,
                              capture_output=True, text=True, check=True)

    def run_cli(self, *args, ok=True, cwd=None, env=None):
        result = subprocess.run([sys.executable, str(SCRIPT)] + list(args), cwd=cwd or self.repo,
                                env=env or self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def init(self, key='session-a'):
        result = self.run_cli('init', key, '--json')
        return Path(json.loads(result.stdout)['staging'])

    def propose(self, key='session-a', rel='existing.txt', text='proposed\n'):
        path = self.init(key) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def manifest(self, data, key='session-a'):
        (self.root / key / 'renames.json').write_text(json.dumps(data))

    def test_init_creates_metadata_outside_workspace(self):
        self.init()
        self.assertEqual(self.git('status', '--porcelain').stdout, '')
        metadata = json.loads((self.root / 'session-a/session.json').read_text())
        self.assertEqual(metadata['branch'], 'feature/test')
        self.run_cli('init', '../escape', ok=False)
        self.run_cli('init', 'unsafe', '--root', str(self.repo / 'proposals'), ok=False)

    def test_help_never_runs_or_requires_arguments(self):
        self.propose()
        before = (self.root / 'session-a/staging/existing.txt').read_bytes()
        for cmd in staged.COMMANDS:
            self.assertIn('usage:', self.run_cli(cmd, '--help').stdout)
        self.assertIn('usage:', self.run_cli('clean', '-f', '--help').stdout)
        self.assertEqual((self.root / 'session-a/staging/existing.txt').read_bytes(), before)

    def test_unknown_and_missing_flags_fail_without_cleaning(self):
        path = self.propose()
        for args in [('clean', '-f'), ('clean', '--typo'), ('path',), ('migrate',), ('nonsense',)]:
            self.run_cli(*args, ok=False)
            self.assertTrue(path.exists())

    def test_exact_apply_does_not_apply_substrings(self):
        self.propose(rel='existing.txt')
        other = self.root / 'session-a/staging/existing.txt.backup'
        other.write_text('other')
        self.run_cli('diff', '-f', 'existing.txt', '-a')
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'proposed\n')
        self.assertFalse((self.repo / 'existing.txt.backup').exists())

    def test_ambiguous_apply_and_clean_do_not_mutate(self):
        self.propose(rel='a/file.txt')
        self.propose(rel='b/file.txt')
        for command in [('apply', '-f', 'file.txt'), ('clean', '-f', 'file.txt', '-y'), ('path', '-f', 'file.txt')]:
            self.assertIn('Ambiguous', self.run_cli(*command, ok=False).stderr)
        self.assertFalse((self.repo / 'a').exists())
        self.assertTrue((self.root / 'session-a/staging/a/file.txt').exists())

    def test_fuzzy_subsequence_and_typo(self):
        self.propose(rel='src/Player.svelte')
        for query in ['pysv', 'Playre.svelte']:
            self.assertEqual(Path(self.run_cli('path', '-f', query).stdout.strip()).parts[-2:], ('src', 'Player.svelte'))

    def test_whitespace_and_binary_changes_are_not_applied(self):
        path = self.propose(text='original\n\n')
        self.assertIn('MODIFIED', self.run_cli().stdout)
        path.write_bytes(b'\xff')
        (self.repo / 'existing.txt').write_bytes(b'\xfe')
        self.assertIn('MODIFIED', self.run_cli().stdout)

    def test_hidden_files_are_proposals(self):
        self.propose(rel='.env.example', text='FOO=bar\n')
        self.run_cli('apply', '-f', '.env.example')
        self.assertEqual((self.repo / '.env.example').read_text(), 'FOO=bar\n')

    def test_deletion_only_session_lists_reviews_and_applies(self):
        self.init()
        self.manifest({'_deletions': ['existing.txt']})
        self.assertIn('DELETED', self.run_cli().stdout)
        self.assertIn('-original', self.run_cli('diff', '-f', 'existing.txt').stdout)
        self.run_cli('apply', '-f', 'existing.txt')
        self.assertFalse((self.repo / 'existing.txt').exists())
        self.assertIn('APPLIED', self.run_cli().stdout)

    def test_rename_removes_source_even_when_destination_matches(self):
        self.propose(rel='renamed.txt')
        (self.repo / 'renamed.txt').write_text('proposed\n')
        self.manifest({'renamed.txt': 'existing.txt'})
        self.assertIn('RENAMED', self.run_cli().stdout)
        self.run_cli('apply', '--all')
        self.assertFalse((self.repo / 'existing.txt').exists())
        self.assertIn('APPLIED', self.run_cli().stdout)

    @unittest.skipIf(os.name == 'nt', 'Symlink privileges depend on host configuration')
    def test_apply_preflights_entire_batch(self):
        self.propose()
        (self.root / 'session-a/staging/zzz').symlink_to(self.repo / 'existing.txt')
        self.run_cli('apply', '--all', ok=False)
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')

    def test_path_traversal_manifest_is_rejected(self):
        self.propose()
        outside = self.base / 'outside'
        outside.write_text('safe')
        for manifest in [{'existing.txt': '../../outside'}, {'_deletions': ['../../outside']}, {'_deletions': ['/etc/passwd']}]:
            self.manifest(manifest)
            self.run_cli('apply', '--all', ok=False)
            self.assertEqual(outside.read_text(), 'safe')

    @unittest.skipIf(os.name == 'nt', 'Symlink privileges depend on host configuration')
    def test_workspace_symlink_cannot_be_overwritten(self):
        self.propose(rel='linked/file.txt')
        (self.repo / 'linked').symlink_to(self.home, target_is_directory=True)
        self.run_cli('apply', '--all', ok=False)
        self.assertFalse((self.home / 'file.txt').exists())

    def test_git_metadata_is_rejected(self):
        self.propose(rel='.git/config')
        self.run_cli('apply', '--all', ok=False)
        self.assertEqual(self.git('config', 'user.name').stdout.strip(), 'Test')

    def test_branch_protection_and_repo_override(self):
        self.propose()
        self.run_cli('set', '--protected-branch', 'main')
        self.git('checkout', '-qb', 'main')
        self.assertIn('Protected branch', self.run_cli('apply', '--all', ok=False).stderr)
        self.assertIn('Branch changed', self.run_cli('apply', '--all', '--force', ok=False).stderr)
        self.run_cli('apply', '--all', '--force', '--yes')
        self.run_cli('set', '--repo', '--branch-protection', 'false')
        self.propose(text='next')
        self.run_cli('apply', '--all', '--yes')
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'next')

    def test_custom_branch_patterns(self):
        self.propose()
        self.run_cli('set', '--repo', '--protected-branch', 'feature/*')
        self.run_cli('apply', '--all', ok=False)
        self.run_cli('apply', '--all', '--force')

    def test_clean_removes_tracking_not_workspace(self):
        path = self.propose(rel='renamed.txt')
        self.manifest({'renamed.txt': 'existing.txt', '_deletions': ['other.txt']})
        self.run_cli('clean', '-f', 'renamed.txt', ok=False)
        self.run_cli('clean', '-f', 'renamed.txt', '-y')
        self.assertFalse(path.exists())
        self.assertEqual(json.loads((self.root / 'session-a/renames.json').read_text()), {'_deletions': ['other.txt']})
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')
        self.run_cli('clean', '--all', '-y')
        self.assertEqual(json.loads((self.root / 'session-a/renames.json').read_text()), {})

    def test_clean_session_directory_and_bindings(self):
        path = self.propose(rel='file.txt')
        self.run_cli('clean', '--session', ok=False)
        self.assertTrue(path.exists())
        self.assertTrue((self.root / 'session-a').exists())

        self.run_cli('use', '--session', 'session-a')
        self.run_cli('set', '--repo', '--default-session', 'session-a')
        self.assertEqual(json.loads((self.repo / '.staged.json').read_text()).get('default_session'), 'session-a')
        shell_file = self.config / 'shell_sessions/test-shell.json'
        self.assertTrue(shell_file.exists())
        self.assertEqual(json.loads(shell_file.read_text()).get('session'), 'session-a')

        result = self.run_cli('clean', '--session', '-y')
        self.assertIn('Cleaned session session-a.', result.stdout)
        self.assertFalse((self.root / 'session-a').exists())
        self.assertFalse(shell_file.exists())
        self.assertFalse((self.repo / '.staged.json').exists())

        self.propose(key='session-b', rel='other.txt')
        self.assertTrue((self.root / 'session-b').exists())
        result = self.run_cli('clean', '-s', 'session-b', '-y')
        self.assertIn('Cleaned session session-b.', result.stdout)
        self.assertFalse((self.root / 'session-b').exists())

        self.init('session-c')
        self.assertTrue((self.root / 'session-c').exists())
        self.run_cli('clean', '-s', 'session-c', ok=False)
        self.assertTrue((self.root / 'session-c').exists())
        result = self.run_cli('clean', '-s', 'session-c', '-y')
        self.assertIn('Cleaned session session-c.', result.stdout)
        self.assertFalse((self.root / 'session-c').exists())

    def test_clean_session_clears_all_matching_shell_bindings(self):
        self.propose('discard')
        self.propose('keep')
        bound = dict(self.env, STAGED_SHELL_ID='bound-shell')
        tool_bound = dict(self.env, STAGED_SHELL_ID='tool-shell')
        self.run_cli('use', '--session', 'discard', env=bound)
        self.run_cli('use', '--session', 'discard', '--tool', 'cli', env=tool_bound)
        bindings = self.config / 'shell_sessions'
        unrelated = bindings / 'test-shell.json'
        original = unrelated.read_bytes()

        self.run_cli('clean', '--session', 'discard', ok=False)
        self.assertEqual(json.loads((bindings / 'bound-shell.json').read_text()), {'session': 'discard'})
        self.run_cli('clean', '--session', 'discard', '-y')

        self.assertFalse((self.root / 'discard').exists())
        self.assertEqual(json.loads((bindings / 'bound-shell.json').read_text()), {'deleted_session': 'discard'})
        self.assertEqual(json.loads((bindings / 'tool-shell.json').read_text()),
                         {'tool': 'cli', 'deleted_session': 'discard'})
        self.assertEqual(unrelated.read_bytes(), original)
        for env in (bound, tool_bound):
            for helper in ('--list', '--complete', '--complete-open', '--complete-meta'):
                self.assertEqual(self.run_cli(helper, env=env).stderr, '')
            explicit = self.run_cli('path', '--session', 'keep', '-f', 'existing.txt', env=env)
            self.assertEqual(explicit.stderr, '')
            result = self.run_cli('path', '-f', 'existing.txt', env=env)
            self.assertEqual(result.stdout.strip(), str(self.root / 'keep/staging/existing.txt'))
            self.assertIn("session 'discard' was deleted. Using session 'keep'.", result.stderr)
            self.assertEqual(self.run_cli(env=env).stderr, '')
        self.assertFalse((bindings / 'bound-shell.json').exists())
        self.assertEqual(json.loads((bindings / 'tool-shell.json').read_text()), {'tool': 'cli'})
        self.assertEqual(self.run_cli().stderr, '')

    def test_deleted_session_notice_without_replacement(self):
        self.propose('discard')
        other = dict(self.env, STAGED_SHELL_ID='other-shell')
        self.run_cli('use', '--session', 'discard', env=other)
        self.run_cli('clean', '--session', 'discard', '-y')
        result = self.run_cli(env=other, ok=False)
        self.assertIn("session 'discard' was deleted.", result.stderr)
        self.assertIn('No staging session', result.stderr)
        self.assertNotIn('was deleted', self.run_cli(env=other, ok=False).stderr)

    def test_explicit_binding_clears_deleted_session_notice(self):
        self.propose('discard')
        self.propose('keep')
        for command in ('use', 'init'):
            env = dict(self.env, STAGED_SHELL_ID=command + '-shell')
            self.run_cli('use', '--session', 'discard', env=env)
        self.run_cli('clean', '--session', 'discard', '-y')
        for command in ('use', 'init'):
            env = dict(self.env, STAGED_SHELL_ID=command + '-shell')
            self.run_cli(command, 'keep', env=env)
            self.assertEqual(self.run_cli(env=env).stderr, '')
            state = json.loads((self.config / ('shell_sessions/' + command + '-shell.json')).read_text())
            self.assertNotIn('deleted_session', state)

    def test_clean_explicit_session_ignores_stale_active_session(self):
        self.propose('discard')
        env = dict(self.env, STAGED_SESSION='missing-session')
        self.run_cli('clean', '--session', 'disc', '-y', env=env)
        self.assertFalse((self.root / 'discard').exists())
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')

    def test_clean_explicit_session_ignores_other_workspace_binding(self):
        self.propose('discard')
        other = self.base / 'other-workspace'
        other.mkdir()
        self.run_cli('init', 'other-session', cwd=other)
        binding = self.config / 'shell_sessions/test-shell.json'
        original = binding.read_bytes()

        self.run_cli('clean', '--session', 'discard', '-y')

        self.assertFalse((self.root / 'discard').exists())
        self.assertTrue((self.root / 'other-session').is_dir())
        self.assertEqual(binding.read_bytes(), original)
        self.run_cli(cwd=other)

    def test_clean_session_mutually_exclusive_with_file_and_all(self):
        self.propose()
        self.run_cli('clean', '-f', 'existing.txt', '--session', ok=False)
        self.run_cli('clean', '--all', '--session', ok=False)
        self.run_cli('clean', '-f', 'existing.txt', '-s', ok=False)
        self.run_cli('clean', '--all', '-s', ok=False)

    def test_path_flags_and_subdirectory_workspace(self):
        path = self.propose()
        nested = self.repo / 'nested'
        nested.mkdir()
        self.assertEqual(self.run_cli('path', '-s', '-f', 'existing.txt', cwd=nested).stdout.strip(), str(path))
        self.assertEqual(self.run_cli('path', '-w', '-f', 'existing.txt', cwd=nested).stdout.strip(), str(self.repo / 'existing.txt'))
        self.assertEqual(self.run_cli('-s', 'session-a', 'path', '-f', 'existing.txt', cwd=nested).stdout.strip(), str(path))

    def test_use_session_flag_persists_until_switched_or_cleared(self):
        self.propose('one', text='one')
        self.propose('two', text='two')
        self.run_cli('set', '--default-session', 'two')
        self.run_cli('use', '--session', 'one')
        for _ in range(2):
            self.assertIn('one', Path(self.run_cli('path', '-f', 'existing.txt').stdout.strip()).parts)
        self.run_cli('use', '--session', 'two')
        self.assertIn('two', Path(self.run_cli('path', '-f', 'existing.txt').stdout.strip()).parts)
        self.run_cli('use', '--session', 'one')
        self.run_cli('use', '--clear')
        self.assertIn('two', Path(self.run_cli('path', '-f', 'existing.txt').stdout.strip()).parts)

    def test_use_session_conflicts_preserve_binding(self):
        self.propose('one')
        self.propose('two')
        self.run_cli('use', '--session', 'one')
        for args in [('use', '--session', 'two', '--clear'),
                     ('use', 'one', '--session', 'two'), ('use', '--session', 'missing')]:
            self.run_cli(*args, ok=False)
            self.assertIn('one', Path(self.run_cli('path', '-f', 'existing.txt').stdout.strip()).parts)

    def test_state_precedence(self):
        self.propose('one', text='one')
        self.propose('two', text='two')
        env = dict(self.env, STAGED_SESSION='one')
        self.assertIn('one', Path(self.run_cli('path', '-f', 'existing.txt', env=env).stdout.strip()).parts)
        self.assertIn('two', Path(self.run_cli('path', '-f', 'existing.txt', '--session', 'two', env=env).stdout.strip()).parts)
        self.run_cli('use', '--clear')
        self.run_cli('set', '--default-session', 'one')
        self.assertIn('one', Path(self.run_cli('path', '-f', 'existing.txt').stdout.strip()).parts)
        self.run_cli('set', '--repo', '--default-session', 'two')
        self.assertIn('two', Path(self.run_cli('path', '-f', 'existing.txt').stdout.strip()).parts)

    def test_session_prefix_ambiguity_and_exact_match(self):
        self.propose('abc')
        self.propose('abcd')
        self.assertIn('abc', Path(self.run_cli('path', '-f', 'existing.txt', '--session', 'abc').stdout.strip()).parts)
        self.run_cli('path', '-f', 'existing.txt', '--session', 'ab', ok=False)

    def test_cross_session_compares_same_relative_file(self):
        self.propose('one', rel='a/file.txt', text='one\n')
        self.propose('two', rel='b/file.txt', text='two\n')
        self.run_cli('diff', '--between', 'one', 'two', '-f', 'file.txt', ok=False)
        self.propose('two', rel='a/file.txt', text='two\n')
        self.assertIn('-one', self.run_cli('diff', '--between', 'one', 'two', '-f', 'a/file.txt').stdout)
        self.assertIn('+two', self.run_cli('diff', '-f', 'a/file.txt', '-s', 'one', '--compare-session', 'two').stdout)
        self.run_cli('diff', '--between', 'one', 'two', '--all', '-a', ok=False)

    def test_migrate_files_renames_deletions_and_metadata(self):
        self.propose('source', rel='renamed.txt')
        self.manifest({'renamed.txt': 'existing.txt', '_deletions': ['obsolete.txt']}, 'source')
        self.run_cli('migrate', '--all', '--from', 'source', '--to', 'dest')
        self.assertTrue((self.root / 'dest/staging/renamed.txt').exists())
        self.assertEqual(json.loads((self.root / 'dest/renames.json').read_text()),
                         {'renamed.txt': 'existing.txt', '_deletions': ['obsolete.txt']})
        self.assertEqual(json.loads((self.root / 'dest/session.json').read_text())['migrated_from'][0]['session'], 'source')
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')

    def test_migrate_conflict_and_newer_workspace(self):
        path = self.propose('source', text='old proposal')
        self.propose('dest', text='new proposal')
        self.run_cli('migrate', '--all', '--from', 'source', '--to', 'dest', ok=False)
        self.assertEqual((self.root / 'dest/staging/existing.txt').read_text(), 'new proposal')
        self.run_cli('migrate', '--all', '--from', 'source', '--to', 'dest', '--force')
        os.utime(path, (1, 1))
        self.run_cli('migrate', '--all', '--from', 'source', '--to', 'fresh', ok=False)
        self.assertFalse((self.root / 'fresh/staging/existing.txt').exists())

    def test_custom_adapter_templates_and_failures(self):
        self.propose()
        recorder = self.base / 'record.py'
        log = self.base / 'args.json'
        recorder.write_text('import json,sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n')
        import shlex
        template = '{} {} {} "left={{orig}}" "right={{staged}}"'.format(
            shlex.quote(sys.executable), shlex.quote(str(recorder)), shlex.quote(str(log)))
        self.run_cli('set-tool', 'record', '--name', 'Recorder', '--diff-cmd', template,
                     '--open-cmd', '{} {}'.format(shlex.quote(sys.executable), '{staged}'))
        self.run_cli('diff', '-f', 'existing.txt', '--tool', 'record')
        args = json.loads(log.read_text())
        self.assertEqual(args[0], 'left=' + str(self.repo / 'existing.txt'))
        self.assertEqual(args[1], 'right=' + str(self.root / 'session-a/staging/existing.txt'))
        self.run_cli('--tool', 'missing', ok=False)

    def test_supported_skill_installation(self):
        for tool, path in [('cursor', '.cursor/skills'), ('codex', '.agents/skills'),
                           ('claudecode', '.claude/skills'), ('windsurf', '.codeium/windsurf/skills'),
                           ('antigravity', '.gemini/config/skills'), ('zed', '.agents/skills')]:
            self.run_cli('install-skill', '--tool', tool)
            self.assertTrue((self.home / path / 'stage/SKILL.md').is_file())
        self.run_cli('install-skill', '--tool', 'vscode', ok=False)
        self.run_cli('install-skill', '--tool', 'vscode', '--target-dir', str(self.home / 'custom/stage'))
        self.assertTrue((self.home / 'custom/stage/SKILL.md').is_file())

    def test_cursor_sandbox_merge_preserves_existing_config(self):
        path = self.home / '.cursor/sandbox.json'
        path.parent.mkdir()
        path.write_text(json.dumps({'networkPolicy': {'default': 'deny'}, 'additionalReadwritePaths': ['/existing']}))
        for _ in range(2):
            self.run_cli('install-skill', '--tool', 'cursor', '--configure-sandbox')
        data = json.loads(path.read_text())
        self.assertEqual(data['networkPolicy'], {'default': 'deny'})
        self.assertEqual(data['additionalReadwritePaths'], ['/existing', str(self.root)])
        cli = json.loads((path.parent / 'cli-config.json').read_text())
        self.assertEqual(len(cli['permissions']['allow']), 2)

    def test_help_and_completion_cover_public_options_without_config(self):
        (self.repo / '.staged.json').write_text('{broken')
        parser, commands = staged.make_parser()
        full_help = self.run_cli('--help-all').stdout
        for name, command in [(None, parser)] + list(commands.items()):
            with self.subTest(command=name):
                args = ['--complete-options'] + ([name] if name else [])
                choices = self.run_cli(*args).stdout.splitlines()
                for action in command._actions:
                    if action.help == staged.argparse.SUPPRESS:
                        continue
                    if action.option_strings:
                        self.assertTrue(action.help, action.option_strings)
                    for flag in action.option_strings:
                        self.assertIn(flag, choices)
                        self.assertIn(flag, full_help)
                self.assertNotIn('--complete-tools', choices)
        root = self.run_cli('--complete-options').stdout.splitlines()
        self.assertIn('--session', root)
        self.assertIn('--sessions', root)
        # A global option value matching a command must not switch contexts.
        options = self.run_cli('--complete-options', '--session', 'set', 'diff').stdout.splitlines()
        self.assertIn('--between', options)
        self.assertNotIn('--repo', options)

    @unittest.skipIf(os.name == 'nt', 'Requires POSIX shells')
    def test_shell_completion_public_options_and_session_values(self):
        self.init('session-a')
        self.init('session-b')
        env = dict(self.env, PATH=str(SCRIPT.parent) + os.pathsep + self.env['PATH'])
        scripts = {
            'bash': ('staged.bash', r'''source "$1"
shift
COMP_WORDS=(staged "$@")
COMP_CWORD=$((${#COMP_WORDS[@]} - 1))
_staged
printf '%s\n' "${COMPREPLY[@]}"
'''),
            'zsh': ('_staged', r'''completion_file=$1
shift
words=(staged "$@")
CURRENT=${#words[@]}
compadd() {
  while [[ "$1" != -- ]]; do shift; done
  shift
  print -rl -- "$@"
}
source "$completion_file"
'''),
        }
        cases = [
            ([''], ['--session', '--sessions', '--all-repos', '--verbose'], ['--file']),
            (['diff', ''], ['--between', '--compare-session', '--file'], ['--repo']),
            (['init', ''], ['--json'], ['--apply']),
            (['use', ''], ['session-a', '--clear', '--session'], ['--apply']),
            (['set', ''], ['--default-session', '--branch-protection', '--session-root'], ['--apply']),
            (['install-completion', ''], ['--shell', '--help'], ['--apply']),
            (['path', '-s', ''], ['--workspace', '--file'], ['session-a']),
            (['clean', '-s', ''], ['session-a', 'session-b'], ['--workspace']),
            (['diff', '--between', 'session-a', ''], ['session-b'], ['--all']),
            (['set', '--default-session', ''], ['session-a'], ['--all']),
            (['set', '--branch-protection', ''], ['true', 'false'], ['--all']),
        ]
        for shell, (filename, script) in scripts.items():
            if not shutil.which(shell):
                continue
            for words, included, excluded in cases:
                with self.subTest(shell=shell, words=words):
                    result = subprocess.run([shell, '-f', '-c', script, 'test',
                                             str(SCRIPT.parent / 'completions' / filename)] + words,
                                            cwd=self.repo, env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    choices = result.stdout.splitlines()
                    for choice in included:
                        self.assertIn(choice, choices)
                    for choice in excluded:
                        self.assertNotIn(choice, choices)

    def test_machine_commands_never_offer_completion_setup(self):
        self.init()
        self.run_cli('--sessions')
        self.run_cli('--help-all')
        self.run_cli('--complete-options')
        self.assertFalse((self.config / 'completion-setup.json').exists())

    def test_completion_scripts_are_idempotent_and_parse(self):
        for shell in (['powershell'] if os.name == 'nt' else ['bash', 'zsh', 'powershell']):
            for _ in range(2):
                self.run_cli('install-completion', '--shell', shell)
        if os.name != 'nt':
            self.assertEqual((self.home / '.bashrc').read_text().count(staged.COMPLETION_MARKER_START), 1)
            self.assertEqual((self.home / '.zshrc').read_text().count(staged.COMPLETION_MARKER_START), 1)
        self.assertFalse((self.config / 'bin/staged.cmd').exists())
        directory = self.config / 'completions'
        for shell, filename in [('bash', 'staged.bash'), ('zsh', '_staged')]:
            if os.name != 'nt' and shutil.which(shell):
                subprocess.run([shell, '-n', str(directory / filename)], check=True, capture_output=True)

    @unittest.skipIf(os.name == 'nt', 'Symlink privileges depend on Windows host settings')
    def test_completion_links_track_checkout_updates_without_reinstall(self):
        checkout = self.base / 'checkout'
        checkout.mkdir()
        shutil.copy2(SCRIPT, checkout / 'staged')
        shutil.copytree(SCRIPT.parent / 'completions', checkout / 'completions')
        for shell, name in [('bash', 'staged.bash'), ('zsh', '_staged'), ('powershell', 'staged.ps1')]:
            target = self.config / 'completions' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('old installed copy')
            command = [sys.executable, str(checkout / 'staged'), 'install-completion', '--shell', shell]
            subprocess.run(command, cwd=self.repo, env=self.env, capture_output=True, check=True)
            self.assertTrue(target.is_symlink())
            source = checkout / 'completions' / name
            self.assertEqual(target.resolve(), source.resolve())
            source.write_text(source.read_text() + '\n# updated in checkout\n')
            self.assertIn('# updated in checkout', target.read_text())
            subprocess.run(command, cwd=self.repo, env=self.env, capture_output=True, check=True)
            self.assertTrue(target.is_symlink())
            self.assertIn('# updated in checkout', target.read_text())

    @unittest.skipIf(os.name == 'nt' or not shutil.which('zsh'), 'Requires Zsh')
    def test_zsh_install_overrides_stale_cached_handler_and_completes_files(self):
        self.propose(rel='README with spaces.md')
        old = self.home / 'old-completions'
        old.mkdir()
        (old / '_stage').write_text('#compdef staged\nprint OLD_HANDLER\n')
        import shlex
        rc = self.home / '.zshrc'
        rc.write_text('fpath=(' + shlex.quote(str(old)) + ' $fpath)\nautoload -Uz compinit\ncompinit\n')
        subprocess.run(['zsh', '-f', '-c', 'source "$HOME/.zshrc"; [[ $_comps[staged] == _stage ]]'],
                       cwd=self.repo, env=self.env, capture_output=True, text=True, check=True)
        # Simulate an existing installation placed before another completion setup.
        self.run_cli('install-completion', '--shell', 'zsh')
        with rc.open('a') as stream:
            stream.write('\ncompdef _stage staged\n')
        self.run_cli('install-completion', '--shell', 'zsh')
        script = r'''_staged() { print OLD_LOADED_FUNCTION; }
source "$HOME/.zshrc"
[[ $_comps[staged] == _staged ]] || exit 2
words=(staged diff -f README)
CURRENT=4
compadd() { print -rl -- "$@"; }
_staged
'''
        env = dict(self.env, PATH=str(SCRIPT.parent) + os.pathsep + self.env['PATH'])
        result = subprocess.run(['zsh', '-f', '-c', script], cwd=self.repo, env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('README with spaces.md', result.stdout.splitlines())
        self.assertNotIn('OLD_HANDLER', result.stdout)
        self.assertNotIn('OLD_LOADED_FUNCTION', result.stdout)
        (self.root / 'session-a/staged_changes.md').write_text('# Notes')
        for words, current, expected, excluded in [
                ('staged open --meta -f sta', 5, 'staged_changes.md', 'README with spaces.md'),
                ('staged open -f REA', 4, 'README with spaces.md', 'staged_changes.md')]:
            completion = script.replace('words=(staged diff -f README)', 'words=(' + words + ')')
            completion = completion.replace('CURRENT=4', 'CURRENT=' + str(current))
            result = subprocess.run(['zsh', '-f', '-c', completion], cwd=self.repo, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(expected, result.stdout.splitlines())
            self.assertNotIn(excluded, result.stdout.splitlines())

    def test_overview_color_and_plain_machine_output(self):
        self.propose()
        env = dict(self.env, FORCE_COLOR='1')
        self.assertIn('\x1b[33m[MODIFIED', self.run_cli('diff', env=env).stdout)
        self.assertNotIn('\x1b[', self.run_cli('diff').stdout)
        self.assertNotIn('\x1b[', self.run_cli('diff', env=dict(env, NO_COLOR='1')).stdout)
        self.assertNotIn('\x1b[', self.run_cli('path', '-f', 'existing.txt', env=env).stdout)
        self.assertNotIn('\x1b[', self.run_cli('--list', env=env).stdout)

    @unittest.skipIf(os.name == 'nt' or not shutil.which('bash'), 'Requires Bash')
    def test_bash_completion_is_case_insensitive_and_restores_shell_option(self):
        self.propose(rel='README.md')
        self.run_cli('install-completion', '--shell', 'bash')
        env = dict(self.env, PATH=str(SCRIPT.parent) + os.pathsep + self.env['PATH'])
        script = 'source "$HOME/.config/staged/completions/staged.bash"; COMP_WORDS=(staged diff -f rea); COMP_CWORD=3; _staged; printf "%s\\n" "${COMPREPLY[@]}"; ! shopt -q nocasematch'
        result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script], env=env, cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('README.md', result.stdout.splitlines())

        (self.root / 'session-a/staged_changes.md').write_text('# notes')
        script_open = 'source "$HOME/.config/staged/completions/staged.bash"; COMP_WORDS=(staged open --meta -f sta); COMP_CWORD=4; _staged; printf "%s\\n" "${COMPREPLY[@]}"'
        result_open = subprocess.run(['bash', '--noprofile', '--norc', '-c', script_open], env=env, cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(result_open.returncode, 0, result_open.stderr)
        self.assertIn('staged_changes.md', result_open.stdout.splitlines())
        result_staged = subprocess.run(['bash', '--noprofile', '--norc', '-c',
                                       script_open.replace('open --meta -f sta', 'open -f sta').replace('COMP_CWORD=4', 'COMP_CWORD=3')],
                                      env=env, cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(result_staged.returncode, 0, result_staged.stderr)
        self.assertNotIn('staged_changes.md', result_staged.stdout.splitlines())

    @unittest.skipIf(os.name == 'nt' or not shutil.which('zsh'), 'Requires a POSIX terminal and Zsh')
    def test_zsh_real_tab_matches_lowercase_and_uppercase(self):
        import select
        import time
        self.propose(rel='README.md')
        self.propose(rel='completions/_staged')
        self.run_cli('install-completion', '--shell', 'zsh')
        master, slave = os.openpty()
        env = dict(self.env, PATH=str(SCRIPT.parent) + os.pathsep + self.env['PATH'], TERM='xterm')
        process = subprocess.Popen(['zsh', '-f'], stdin=slave, stdout=slave, stderr=slave,
                                   cwd=self.repo, env=env, start_new_session=True)
        os.close(slave)
        def read_until(marker):
            output = b''
            deadline = time.monotonic() + 15
            while marker not in output and time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    output += os.read(master, 65536)
            self.assertIn(marker, output)
        try:
            os.write(master, b'''source "$HOME/.zshrc"; PS1='READY> '; bindkey '^I' complete-word; _capture() { print -r -- "CAPTURE:$BUFFER"; zle send-break; }; zle -N _capture; bindkey '^X' _capture; print SETUP_DONE\n''')
            read_until(b'\r\nSETUP_DONE\r\n')
            for prefix, expected in [(b'rea', b'README.md'), (b'REA', b'README.md'),
                                     (b'_sta', b'completions/_staged'),
                                     (b'_STA', b'completions/_staged'),
                                     (b'pletions/_sta', b'completions/_staged'),
                                     (b'STAGED', b'completions/_staged'),
                                     (b'completions/_sta', b'completions/_staged')]:
                read_until(b'READY> ')
                os.write(master, b'staged diff -f ' + prefix + b'\t\x18')
                read_until(b'CAPTURE:staged diff -f ' + expected)
        finally:
            process.kill()
            process.wait(timeout=5)
            os.close(master)

    @unittest.skipIf(os.name == 'nt', 'Requires POSIX shells')
    def test_file_completion_matches_path_substrings(self):
        paths = ['completions/_staged', 'other/_staged', 'docs/Read me.md', 'src/[literal].txt']
        for path in paths:
            self.propose(rel=path)
        env = dict(self.env, PATH=str(SCRIPT.parent) + os.pathsep + self.env['PATH'])
        scripts = {
            'bash': ('staged.bash', r'''source "$1"
COMP_WORDS=(staged diff -f "$2")
COMP_CWORD=3
_staged
printf '%s\n' "${COMPREPLY[@]}"
! shopt -q nocasematch
'''),
            'zsh': ('_staged', r'''words=(staged diff -f "$2")
CURRENT=4
compadd() {
  while [[ "$1" != -- ]]; do shift; done
  shift
  print -rl -- "$@"
}
source "$1"
'''),
        }
        for shell, (filename, script) in scripts.items():
            if not shutil.which(shell):
                continue
            for query, expected in [('_sta', paths[:2]), ('_STA', paths[:2]),
                                    ('completions/_sta', paths[:1]), ('read ', [paths[2]]),
                                    ('PLETIONS/_sta', paths[:1]), ('staged', paths[:2]),
                                    ('me.md', [paths[2]]), ('rc/[lit', [paths[3]]),
                                    ('[lit', [paths[3]]), ('missing', []), ('', paths)]:
                with self.subTest(shell=shell, query=query):
                    result = subprocess.run([shell, '-f', '-c', script, 'completion-test',
                                             str(SCRIPT.parent / 'completions' / filename), query],
                                            env=env, cwd=self.repo, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(sorted(filter(None, result.stdout.splitlines())), sorted(expected))

    def test_corrupt_config_and_manifest_are_not_silently_ignored(self):
        self.propose()
        (self.root / 'session-a/renames.json').write_text('{invalid')
        self.assertIn('Cannot read', self.run_cli('apply', '--all', ok=False).stderr)
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')

    def test_no_implicit_discovery_of_old_roots(self):
        old = self.home / '.cursor/artifact-staging/old'
        (old / 'staging').mkdir(parents=True)
        (old / '.workspace').write_text(str(self.repo))
        (old / 'staging/existing.txt').write_text('old')
        self.assertNotIn('old', self.run_cli('--sessions').stdout)
        self.run_cli('set', '--session-root', str(old.parent))
        self.assertIn('old', self.run_cli('--sessions').stdout)

    def test_migration_preflights_target_before_copying(self):
        self.propose('source', rel='aaa.txt')
        self.propose('source', rel='nested/child.txt')
        target = self.init('dest')
        (target / 'nested').write_text('keep')
        self.run_cli('migrate', '--all', '--from', 'source', '--to', 'dest', '--force', ok=False)
        self.assertFalse((target / 'aaa.txt').exists())
        self.assertEqual((target / 'nested').read_text(), 'keep')

    def test_migration_glob_transfers_only_selected_files(self):
        self.propose('source', rel='src/a.py')
        self.propose('source', rel='src/b.py')
        self.propose('source', rel='docs.txt')
        self.run_cli('migrate', '--from', 'source', '--to', 'dest', '--file', 'src/*.py')
        self.assertEqual(sorted(p.name for p in (self.root / 'dest/staging/src').iterdir()), ['a.py', 'b.py'])
        self.assertFalse((self.root / 'dest/staging/docs.txt').exists())

    def test_migration_retains_origin_branch_guard(self):
        self.propose('source')
        self.git('checkout', '-qb', 'feature/other')
        self.run_cli('migrate', '--all', '--from', 'source', '--to', 'dest')
        result = self.run_cli('apply', '--all', '--session', 'dest', ok=False)
        self.assertIn('Branch changed', result.stderr)
        self.run_cli('apply', '--all', '--session', 'dest', '--yes')

    def test_rename_chain_is_rejected_before_any_copy(self):
        self.propose(rel='a.txt')
        self.propose(rel='b.txt')
        self.manifest({'a.txt': 'existing.txt', 'b.txt': 'a.txt'})
        self.run_cli('apply', '--all', ok=False)
        self.run_cli('apply', '-f', 'b.txt', ok=False)
        self.assertFalse((self.repo / 'a.txt').exists())
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')

    def test_contradictory_deletion_does_not_modify_workspace(self):
        self.propose()
        self.manifest({'_deletions': ['existing.txt']})
        self.run_cli('apply', '--all', ok=False)
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')

    def test_inventory_refreshes_after_external_edits(self):
        self.propose()
        self.run_cli()
        self.propose(rel='added.txt')
        (self.root / 'session-a/staging/existing.txt').unlink()
        self.assertEqual(self.run_cli('--list').stdout.strip(), 'added.txt')
        self.run_cli()
        records = json.loads((self.root / 'session-a/.index.json').read_text())['files']
        self.assertEqual([r['path'] for r in records], ['added.txt'])

    @unittest.skipIf(os.name == 'nt', 'POSIX executable bit semantics')
    def test_permission_changes_are_pending_and_preserved(self):
        path = self.propose(text='original\n')
        path.chmod(0o755)
        self.assertIn('MODIFIED', self.run_cli().stdout)
        self.run_cli('apply', '--all')
        self.assertEqual((self.repo / 'existing.txt').stat().st_mode & 0o777, 0o755)

    def test_generated_empty_review_file_survives_editor_launch(self):
        self.propose(rel='new.txt')
        self.run_cli('diff', '-f', 'new.txt')
        empty = self.root / 'session-a/.review/empty/new.txt'
        self.assertTrue(empty.is_file())
        self.assertEqual(empty.read_bytes(), b'')

    def test_missing_rename_contents_block_apply_but_can_be_cleaned(self):
        self.propose()
        self.manifest({'missing.txt': 'original-name.txt'})
        self.assertIn('MISSING', self.run_cli().stdout)
        self.run_cli('apply', '--all', ok=False)
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')
        self.run_cli('clean', '-f', 'missing.txt', '--yes')
        self.assertEqual(json.loads((self.root / 'session-a/renames.json').read_text()), {})

    def test_mutations_rescan_even_if_index_omits_a_file(self):
        self.propose()
        self.run_cli()
        index = self.root / 'session-a/.index.json'
        data = json.loads(index.read_text())
        data['files'] = []
        index.write_text(json.dumps(data))
        self.run_cli('apply', '--all')
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'proposed\n')

    def test_malformed_index_falls_back_to_filesystem(self):
        self.propose()
        (self.root / 'session-a/.index.json').write_text('{broken')
        self.assertEqual(self.run_cli('--list').stdout.strip(), 'existing.txt')

    def test_valid_index_reuses_inventory_without_tree_scan(self):
        self.propose()
        session = staged.Session(self.root / 'session-a')
        session.refresh_index()
        with patch.object(staged.os, 'walk', side_effect=AssertionError('unexpected scan')):
            self.assertEqual(list(session.files()), ['existing.txt'])

    def test_open_requires_argument_and_validates(self):
        self.propose()
        self.run_cli('open', ok=False)
        self.run_cli('open', '-f', 'nonexistent.txt', ok=False)

    def test_open_staged_and_session_files_and_aliases(self):
        self.propose()
        recorder = self.base / 'record_open.py'
        log = self.base / 'open_args.json'
        recorder.write_text('import json,sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n')
        import shlex
        template = '{} {} {} "target={}"'.format(
            shlex.quote(sys.executable), shlex.quote(str(recorder)), shlex.quote(str(log)), '{staged}')
        self.run_cli('set-tool', 'rec-open', '--name', 'RecOpen',
                     '--diff-cmd', '{} {} {}'.format(shlex.quote(sys.executable), shlex.quote(str(recorder)), '{orig} {staged}'),
                     '--open-cmd', template)
        self.run_cli('open', '-f', 'existing.txt', '--tool', 'rec-open')
        args = json.loads(log.read_text())
        self.assertEqual(args[0], 'target=' + str(self.root / 'session-a/staging/existing.txt'))

        dashboard = self.root / 'session-a/staged_changes.md'
        dashboard.write_text('# Staging Dashboard')
        self.run_cli('open', '--meta', '-f', 'staged_changes.md', '--tool', 'rec-open')
        args = json.loads(log.read_text())
        self.assertEqual(args[0], 'target=' + str(dashboard))

        # Test alias staging.md -> staged_changes.md
        self.run_cli('open', '--meta', '-f', 'staging.md', '--tool', 'rec-open')
        args = json.loads(log.read_text())
        self.assertEqual(args[0], 'target=' + str(dashboard))

        # Test alias diff.md -> diff_viewer.md
        diff_view = self.root / 'session-a/diff_viewer.md'
        diff_view.write_text('# Diff Viewer')
        self.run_cli('open', '--meta', '-f', 'diff.md', '--tool', 'rec-open')
        args = json.loads(log.read_text())
        self.assertEqual(args[0], 'target=' + str(diff_view))

        # Test fuzzy match
        self.run_cli('open', '--meta', '-f', 'staged_changes', '--tool', 'rec-open')
        args = json.loads(log.read_text())
        self.assertEqual(args[0], 'target=' + str(dashboard))

        # Identically named proposals and metadata stay in separate namespaces.
        proposal = self.propose(rel='session.json', text='{"application": true}')
        self.run_cli('open', '-f', 'session.json', '--tool', 'rec-open')
        self.assertEqual(json.loads(log.read_text()), ['target=' + str(proposal)])
        self.run_cli('open', '--meta', '-f', 'session.json', '--tool', 'rec-open')
        self.assertEqual(json.loads(log.read_text()), ['target=' + str(self.root / 'session-a/session.json')])
        self.run_cli('open', '-f', 'staged_changes.md', '--tool', 'rec-open', ok=False)
        self.run_cli('open', '--meta', '-f', 'existing.txt', '--tool', 'rec-open', ok=False)

    def test_complete_open_outputs_staged_and_session_files(self):
        self.propose(rel='src/foo.py')
        (self.root / 'session-a/staged_changes.md').write_text('# Notes')
        output = self.run_cli('--complete-open').stdout.splitlines()
        self.assertIn('src/foo.py', output)
        self.assertNotIn('staged_changes.md', output)
        metadata = self.run_cli('--complete-meta').stdout.splitlines()
        self.assertIn('staged_changes.md', metadata)
        self.assertNotIn('src/foo.py', metadata)

    def test_open_metadata_is_editable_on_protected_branch(self):
        self.propose()
        self.run_cli('set', '--repo', '--protected-branch', 'main')
        self.git('checkout', '-qb', 'main')
        dashboard = self.root / 'session-a/staged_changes.md'
        dashboard.write_text('before')
        editor = self.base / 'edit.py'
        editor.write_text('import sys\nfrom pathlib import Path\nPath(sys.argv[-1]).write_text("edited")\n')
        import shlex
        env = dict(self.env, EDITOR=shlex.quote(sys.executable) + ' ' + shlex.quote(str(editor)))
        self.run_cli('open', '--meta', '-f', 'staged_changes.md', env=env)
        self.assertEqual(dashboard.read_text(), 'edited')
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')

    def test_open_editor_exit_one_is_failure(self):
        self.propose()
        editor = self.base / 'fail.py'
        editor.write_text('import sys\nsys.exit(1)\n')
        import shlex
        env = dict(self.env, EDITOR=shlex.quote(sys.executable) + ' ' + shlex.quote(str(editor)))
        for tool in ('cli', 'codex', 'claudecode'):
            result = self.run_cli('open', '-f', 'existing.txt', '--tool', tool, env=env, ok=False)
            self.assertIn('failed with exit code 1', result.stderr)

    def test_explicit_selection_required_and_conflicts_do_not_mutate(self):
        proposal = self.propose()
        for command in ('apply', 'clean', 'path', 'open'):
            self.run_cli(command, ok=False)
            self.run_cli(command, 'existing.txt', ok=False)
            self.run_cli(command, 'all', ok=False)
            self.run_cli(command, '--all', '-f', 'existing.txt', ok=False)
        for args in [('diff', 'existing.txt'), ('diff', 'all'), ('diff', '-a'), ('diff', '-c'),
                     ('diff', '--all', '-f', 'existing.txt', '-a'),
                     ('diff', '--between', 'session-a', 'session-b'),
                     ('migrate', '--from', 'session-a', '--to', 'new'),
                     ('migrate', '--from', 'session-a', '--to', 'new', '--all', '-f', 'existing.txt')]:
            self.run_cli(*args, ok=False)
        self.assertEqual(proposal.read_text(), 'proposed\n')
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')
        self.assertFalse((self.root / 'new').exists())

    def test_file_named_all_is_not_bulk_selection(self):
        self.propose(rel='all', text='one file\n')
        other = self.propose(rel='other.txt', text='keep\n')
        result = self.run_cli('diff', '--file', 'all')
        self.assertIn('+one file', result.stdout)
        self.assertNotIn('other.txt', result.stdout)
        self.run_cli('apply', '--file', 'all')
        self.assertEqual((self.repo / 'all').read_text(), 'one file\n')
        self.assertFalse((self.repo / 'other.txt').exists())
        self.run_cli('migrate', '--from', 'session-a', '--to', 'copy', '--file', 'all')
        self.assertEqual((self.root / 'copy/staging/all').read_text(), 'one file\n')
        self.assertFalse((self.root / 'copy/staging/other.txt').exists())
        self.run_cli('clean', '--file', 'all', '-y')
        self.assertTrue(other.exists())

    def test_diff_all_reviews_and_applies_and_compares(self):
        self.propose('one', text='first\n')
        self.propose('one', rel='all', text='literal\n')
        self.propose('two', text='second\n')
        compared = self.run_cli('diff', '--all', '--between', 'one', 'two')
        self.assertIn('-first', compared.stdout)
        self.assertIn('+second', compared.stdout)
        for action in ('-a', '-c'):
            self.assertIn('cannot be combined', self.run_cli(
                'diff', '--all', '--between', 'one', 'two', action, ok=False).stderr)
        self.assertIn('+literal', self.run_cli('diff', '--all', '--session', 'one').stdout)
        self.run_cli('diff', '--all', '-a', '--session', 'one')
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'first\n')
        self.assertEqual((self.repo / 'all').read_text(), 'literal\n')
        self.assertEqual(self.run_cli('diff', '--all', '--session', 'one').stdout, '')

    def test_diff_clean_flag_and_file_clean(self):
        self.propose(rel='keep.txt', text='keep\n')
        self.propose(rel='discard.txt', text='discard\n')

        # Test diff -f <file> -c cleanly discards only that file
        self.run_cli('diff', '-f', 'discard.txt', '-c', '-y')
        self.assertFalse((self.root / 'session-a/staging/discard.txt').exists())
        self.assertTrue((self.root / 'session-a/staging/keep.txt').exists())

        # Test explicit file selection for clean
        self.propose(rel='pos.txt', text='pos\n')
        self.run_cli('clean', '-f', 'pos.txt', '-y')
        self.assertFalse((self.root / 'session-a/staging/pos.txt').exists())

        # Test diff --all -c discards remaining proposals
        self.run_cli('diff', '--all', '-c', '-y')
        self.assertFalse((self.root / 'session-a/staging/keep.txt').exists())
        self.assertEqual(len(list((self.root / 'session-a/staging').iterdir())), 0)

    def test_npm_launcher_uses_same_engine(self):
        if not shutil.which('node'):
            self.skipTest('Node is optional')
        result = subprocess.run(['node', str(SCRIPT.parent / 'bin/staged.cjs'), 'init', 'npm-session', '--json'],
                                env=self.env, cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['session'], 'npm-session')

    def test_non_git_workspace_and_spaces(self):
        workspace = self.base / 'workspace with spaces'
        workspace.mkdir()
        result = self.run_cli('init', 'non-git', '--json', cwd=workspace)
        directory = Path(json.loads(result.stdout)['staging'])
        (directory / 'file with spaces.txt').write_text('proposal')
        self.run_cli('apply', '-f', 'file with spaces.txt', cwd=workspace)
        self.assertEqual((workspace / 'file with spaces.txt').read_text(), 'proposal')


class Unit(unittest.TestCase):
    def test_pycharm_prefers_documented_launcher(self):
        with patch.object(staged.shutil, 'which', side_effect=lambda name: '/bin/' + name):
            self.assertEqual(staged.TOOL_REGISTRY['pycharm'].get_diff_command('old', 'new'),
                             ['/bin/pycharm', 'diff', 'old', 'new'])

    def test_pycharm_platform_launchers(self):
        for platform, executable in [('win32', 'pycharm64.exe'), ('win32', 'pycharm.bat'),
                                     ('linux', 'pycharm.sh'), ('linux', 'pycharm-community')]:
            with self.subTest(platform=platform, executable=executable):
                with patch.object(staged.sys, 'platform', platform), patch.object(
                        staged.shutil, 'which', side_effect=lambda name: '/bin/' + name if name == executable else None):
                    self.assertEqual(staged.detect_jetbrains_binary(), '/bin/' + executable)

    def test_pycharm_does_not_substitute_other_jetbrains_editors(self):
        with tempfile.TemporaryDirectory() as home, patch.object(staged, 'HOME', home), \
                patch.object(staged.sys, 'platform', 'linux'), patch.object(
                    staged.shutil, 'which', side_effect=lambda name: '/bin/' + name if name in ('idea', 'charm', 'webstorm') else None):
            self.assertIsNone(staged.detect_jetbrains_binary())

    def test_windows_batch_arguments_are_quoted_without_shell_interpolation(self):
        with patch.object(staged.os, 'name', 'nt'), patch.object(staged.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            staged.run_editor(['C:/Program Files/editor.cmd', 'C:/work/file with spaces.txt'])
            command = run.call_args[0][0]
            self.assertIsInstance(command, str)
            self.assertIn('/d /s /c ""C:/Program Files/editor.cmd" "C:/work/file with spaces.txt""', command)
            with self.assertRaises(staged.StagedError):
                staged.run_editor(['editor.cmd', '%UNTRUSTED%'])

    def test_shell_block_preserves_backslashes_on_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profile'
            block = staged.COMPLETION_MARKER_START + '\n' + r'C:\Users\Example\script' + '\n' + staged.COMPLETION_MARKER_END
            staged.upsert_shell_block(str(path), block)
            staged.upsert_shell_block(str(path), block)
            self.assertEqual(path.read_text().strip(), block)

    def test_open_command_uses_its_own_executable(self):
        adapter = staged.ToolAdapter('test', 'Test', ['diff-bin', '{orig}', '{staged}'], ['open-bin', '{staged}'])
        with patch.object(adapter, 'detect_binary', side_effect=lambda command=None: '/bin/' + command):
            self.assertEqual(adapter.get_open_command('file'), ['/bin/open-bin', 'file'])

    def test_terminal_diff_exit_one_is_success(self):
        with patch.object(staged.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            staged.run_editor(['git'], terminal=True)
            with self.assertRaises(staged.StagedError):
                staged.run_editor(['editor'])


class CompletionOnboarding(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='staged-setup-test-')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.config = self.home / 'config'
        self.state = self.config / 'completion-setup.json'
        for patcher in [patch.object(staged, 'HOME', str(self.home)),
                        patch.object(staged, 'CONFIG_DIR', self.config),
                        patch.object(staged, 'completion_shell', return_value='zsh'),
                        patch.dict(os.environ, {'CI': '', 'STAGED_NO_PROMPT': '', 'ZDOTDIR': str(self.home)})]:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.streams = []
        for name in ('stdin', 'stdout', 'stderr'):
            patcher = patch.object(staged.sys, name)
            stream = patcher.start()
            stream.isatty.return_value = True
            self.streams.append(stream)
            self.addCleanup(patcher.stop)
        self.streams[0].readline.return_value = 'n\n'

    def test_decline_is_remembered_without_shell_edits(self):
        staged.offer_completion_install()
        staged.offer_completion_install()
        self.streams[0].readline.assert_called_once()
        self.assertEqual(json.loads(self.state.read_text()), {'zsh': 'declined'})
        self.assertFalse((self.home / '.zshrc').exists())

    @unittest.skipIf(os.name == 'nt', 'Zsh installation requires symlink support')
    def test_accept_installs_and_does_not_offer_again(self):
        self.streams[0].readline.return_value = 'yes\n'
        staged.offer_completion_install()
        self.assertIn(staged.COMPLETION_MARKER_START, (self.home / '.zshrc').read_text())
        self.assertTrue((self.config / 'completions/_staged').is_file())
        self.assertEqual(json.loads(self.state.read_text()), {'zsh': 'installed'})
        # Also recognize an explicit installation with no onboarding state.
        self.state.unlink()
        staged.offer_completion_install()
        self.streams[0].readline.assert_called_once()

    def test_noninteractive_ci_and_opt_out_never_prompt(self):
        for stream in self.streams:
            stream.isatty.return_value = False
            staged.offer_completion_install()
            stream.isatty.return_value = True
        for variable in ('CI', 'STAGED_NO_PROMPT'):
            with patch.dict(os.environ, {variable: '1'}):
                staged.offer_completion_install()
        with patch.object(staged, 'completion_shell', return_value='fish'):
            staged.offer_completion_install()
        self.streams[0].readline.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_eof_does_not_record_a_decline(self):
        self.streams[0].readline.return_value = ''
        staged.offer_completion_install()
        self.assertFalse(self.state.exists())

    def test_setup_failure_is_nonfatal_and_can_be_retried(self):
        self.streams[0].readline.return_value = 'y\n'
        with patch.object(staged, 'install_completion', side_effect=OSError('read-only profile')):
            staged.offer_completion_install()
        self.assertFalse(self.state.exists())
        self.assertTrue(any('read-only profile' in str(call) for call in self.streams[2].write.call_args_list))


class LauncherOnboarding(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='staged-launcher-test-')
        self.addCleanup(temporary.cleanup)
        self.config = Path(temporary.name)
        self.launcher = self.config / 'bin/staged.cmd'
        self.state = self.config / 'launcher-setup.json'
        for patcher in [patch.object(staged, 'CONFIG_DIR', self.config),
                        patch.object(staged.sys, 'platform', 'win32'),
                        patch.object(staged.shutil, 'which', return_value=None),
                        patch.dict(os.environ, {'CI': '', 'STAGED_NO_PROMPT': ''})]:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.streams = []
        for name in ('stdin', 'stdout', 'stderr'):
            patcher = patch.object(staged.sys, name)
            stream = patcher.start()
            stream.isatty.return_value = True
            self.streams.append(stream)
            self.addCleanup(patcher.stop)
        self.streams[0].readline.return_value = 'n\n'

    def test_decline_is_remembered_without_installing(self):
        staged.offer_launcher_install()
        staged.offer_launcher_install()
        self.streams[0].readline.assert_called_once()
        self.assertFalse(self.launcher.exists())
        self.assertEqual(json.loads(self.state.read_text()), {'answered': True})
        # An explicit install remains available after declining.
        staged.install_launcher()
        self.assertTrue(self.launcher.is_file())

    def test_accept_installs_without_changing_path_or_completions(self):
        self.streams[0].readline.return_value = 'yes\n'
        original_path = os.environ.get('PATH')
        staged.offer_launcher_install()
        self.assertIn('%*', self.launcher.read_text())
        self.assertIn(str(SCRIPT), self.launcher.read_text())
        self.assertEqual(os.environ.get('PATH'), original_path)
        self.assertFalse((self.config / 'completions').exists())
        self.state.unlink()
        staged.offer_launcher_install()
        self.streams[0].readline.assert_called_once()

    def test_existing_command_shim_suppresses_offer(self):
        with patch.object(staged.shutil, 'which', return_value='C:/npm/staged.cmd'):
            staged.offer_launcher_install()
        self.streams[0].readline.assert_not_called()
        self.assertFalse(self.launcher.exists())
        self.assertFalse(self.state.exists())

    def test_noninteractive_ci_opt_out_and_other_platforms_skip_offer(self):
        for stream in self.streams:
            stream.isatty.return_value = False
            staged.offer_launcher_install()
            stream.isatty.return_value = True
        for variable in ('CI', 'STAGED_NO_PROMPT'):
            with patch.dict(os.environ, {variable: '1'}):
                staged.offer_launcher_install()
        with patch.object(staged.sys, 'platform', 'linux'):
            staged.offer_launcher_install()
            with self.assertRaises(staged.StagedError):
                staged.install_launcher()
        self.streams[0].readline.assert_not_called()
        self.assertFalse(self.launcher.exists())
        self.assertFalse(self.state.exists())

    def test_eof_and_write_failure_do_not_save_a_choice(self):
        self.streams[0].readline.return_value = ''
        staged.offer_launcher_install()
        self.assertFalse(self.state.exists())
        self.streams[0].readline.return_value = 'y\n'
        with patch.object(staged, 'install_launcher', side_effect=OSError('read-only directory')):
            staged.offer_launcher_install()
        self.assertFalse(self.launcher.exists())
        self.assertFalse(self.state.exists())

    def test_powershell_completion_install_does_not_write_launcher(self):
        staged.install_completion('powershell')
        self.assertTrue((self.config / 'completions/staged.ps1').is_file())
        self.assertFalse(self.launcher.exists())
        self.streams[0].readline.assert_not_called()

    def test_explicit_shell_routes_offer_separately_from_completion(self):
        with patch.object(staged, 'Context'), patch.object(staged, 'install_completion') as completion, \
                patch.object(staged, 'offer_launcher_install') as offer:
            staged.main(['install-completion', '--shell', 'powershell'])
            offer.assert_called_once_with()
            completion.assert_called_once_with('powershell')
            offer.reset_mock()
            staged.main(['--shell', 'powershell', '--sessions'])
            staged.main(['--shell', 'powershell', '--list'])
            staged.main(['--shell', 'powershell', '--help'])
            staged.main(['--shell', 'powershell', '--help-all'])
            staged.main(['install-completion', '--shell', 'bash'])
            offer.assert_not_called()
            staged.main(['--shell', 'powershell'])
            offer.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
