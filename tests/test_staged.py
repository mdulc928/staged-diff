"""CLI contract tests: every process uses an isolated home, staging root and Git repository."""
import atexit
import importlib.machinery
import importlib.util
import json
import marshal
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

# Executing a script never uses a .pyc cache, so each `python staged` recompiles ~2k lines.
# Compile once per test process and run the cached code object with the real __file__,
# so REPO_ROOT and tracebacks match running SCRIPT directly.
LAUNCHER_SOURCE = '''import marshal, sys, types
# Integration tests never access the real Windows registry. Registry behavior
# is covered separately with a mock containing an explicit PATH fixture.
registry = types.ModuleType('winreg')
registry.HKEY_CURRENT_USER = 0
def missing_registry_key(*args, **kwargs):
    raise FileNotFoundError('No registry keys in the test sandbox')
registry.OpenKey = missing_registry_key
sys.modules['winreg'] = registry
source, cache = sys.argv[1], sys.argv[2]
sys.argv = [source] + sys.argv[3:]
with open(cache, 'rb') as handle:
    code = marshal.load(handle)
exec(code, {'__name__': '__main__', '__file__': source, '__builtins__': __builtins__})
'''
SHIM_TEMPLATES = {
    # A regular-file `staged` shadows any real install: uninstall only removes symlinks to the checkout.
    'staged': '#!/bin/sh\nexec {python} {launcher} {script} {cache} "$@"\n',
    # A fake npm keeps tests from inspecting or uninstalling a real global package.
    'npm': '#!/bin/sh\nif [ "$1" = root ]; then echo {npm_root}; exit 0; fi\necho "npm is disabled in tests" >&2\nexit 1\n',
}
WINDOWS_SHIM_TEMPLATES = {
    'staged.cmd': '@echo off\r\n"{python}" "{launcher}" "{script}" "{cache}" %*\r\n',
    'npm.cmd': '@echo off\r\nif "%~1"=="root" (echo {npm_root}& exit /b 0)\r\necho npm is disabled in tests 1>&2\r\nexit /b 1\r\n',
}
TEMPLATE_BRANCH = 'feature/test'


def isolated_env(base_env=None, home=None):
    """Strip variables that would let a test read or change the developer's real configuration."""
    env = dict(os.environ if base_env is None else base_env)
    for key in list(env):
        if key.upper().startswith(('STAGED_', 'STAGE_', 'GIT_')) or key.upper() in (
                'NO_COLOR', 'FORCE_COLOR', 'ZDOTDIR', 'ONEDRIVE', 'ONEDRIVECONSUMER', 'ONEDRIVECOMMERCIAL'):
            env.pop(key)
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    if home is not None:
        home = Path(home)
        env.update(HOME=str(home), USERPROFILE=str(home),
                   XDG_CONFIG_HOME=str(home / '.config'), XDG_DATA_HOME=str(home / '.local/share'),
                   APPDATA=str(home / 'AppData/Roaming'), LOCALAPPDATA=str(home / 'AppData/Local'))
    return env


class _Support:
    """Process-wide fixtures, built lazily once and removed at interpreter exit."""
    _ready = False

    @classmethod
    def ensure(cls):
        if cls._ready:
            return cls
        cls.directory = Path(tempfile.mkdtemp(prefix='staged-test-support-')).resolve()
        atexit.register(shutil.rmtree, str(cls.directory), True)
        cls.cache = cls.directory / 'staged.code'
        cls.cache.write_bytes(marshal.dumps(compile(SCRIPT.read_bytes(), str(SCRIPT), 'exec')))
        cls.launcher = cls.directory / 'launch.py'
        cls.launcher.write_text(LAUNCHER_SOURCE)
        cls.bin = cls.directory / 'bin'
        cls.bin.mkdir()
        values = {'python': sys.executable, 'launcher': cls.launcher, 'script': SCRIPT,
                  'cache': cls.cache, 'npm_root': cls.directory / 'npm-global'}
        templates = WINDOWS_SHIM_TEMPLATES if os.name == 'nt' else SHIM_TEMPLATES
        for name, template in templates.items():
            shim = cls.bin / name
            shim.write_bytes(template.format(**values).encode('utf-8'))
            shim.chmod(0o755)
        cls.repo = cls.directory / 'template-repo'
        cls.repo.mkdir()
        env = isolated_env()
        for args in (['init', '-q'], ['symbolic-ref', 'HEAD', 'refs/heads/' + TEMPLATE_BRANCH],
                     # Keep background Git processes from changing the fixture while it is copied.
                     ['config', 'maintenance.auto', 'false'], ['config', 'gc.auto', '0'],
                     ['config', 'user.name', 'Test'], ['config', 'user.email', 'test@example.invalid']):
            subprocess.run(['git', '-C', str(cls.repo)] + args, env=env, capture_output=True, check=True)
        (cls.repo / 'existing.txt').write_bytes(b'original\n')
        subprocess.run(['git', '-C', str(cls.repo), 'add', '.'], env=env, capture_output=True, check=True)
        subprocess.run(['git', '-C', str(cls.repo), 'commit', '-qm', 'initial'], env=env,
                       capture_output=True, check=True)
        cls._ready = True
        return cls


class CLI(unittest.TestCase):
    def setUp(self):
        support = _Support.ensure()
        self.tmp = tempfile.TemporaryDirectory(prefix='staged-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / 'home'
        self.repo = self.base / 'repo'
        self.root = self.base / 'proposals'
        self.config = self.home / ('AppData/Roaming/staged' if os.name == 'nt' else '.config/staged')
        self.home.mkdir()
        # Copying a prebuilt repository replaces six git processes per test.
        shutil.copytree(str(support.repo), str(self.repo), symlinks=True)
        self.env = isolated_env(home=self.home)
        self.env.update(STAGED_ROOT=str(self.root), STAGED_TOOL='cli', STAGED_SHELL_ID='test-shell',
                        PATH=str(support.bin) + os.pathsep + os.environ.get('PATH', ''))
        for command in ('npm', 'staged'):
            expected = support.bin / (command + '.cmd' if os.name == 'nt' else command)
            found = shutil.which(command, path=self.env['PATH'])
            self.assertEqual(Path(found).resolve() if found else None, expected.resolve(),
                             'Refusing to run tests with a real ' + command + ' command')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo)] + list(args), env=self.env,
                              capture_output=True, text=True, check=True)

    def run_cli(self, *args, ok=True, cwd=None, env=None):
        support = _Support.ensure()
        command = [sys.executable, str(support.launcher), str(SCRIPT), str(support.cache)]
        result = subprocess.run(command + list(args), cwd=cwd or self.repo,
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
        # Byte-range tests need the same UTF-8/LF fixtures on every platform.
        path.write_bytes(text.encode('utf-8'))
        return path

    def manifest(self, data, key='session-a'):
        (self.root / key / 'renames.json').write_text(json.dumps(data))

    def file_api(self, *args, data=None, ok=True):
        support = _Support.ensure()
        command = [sys.executable, str(support.launcher), str(SCRIPT), str(support.cache)]
        result = subprocess.run(command + list(args), cwd=self.repo, env=self.env,
                                input=data if data is not None else b'', capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr.decode())
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_managed_create_empty_text_binary_and_no_overwrite(self):
        import hashlib
        self.init()
        empty = json.loads(self.file_api('create', '-f', 'empty', '--json').stdout)
        self.assertEqual(empty['operation'], 'create')
        self.assertEqual(empty['size'], 0)
        self.file_api('create', '-f', 'existing.txt', '--text', 'proposal')
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')
        binary = b'\x00\xff\r\n'
        result = json.loads(self.file_api('create', '-f', 'dir/data', '--stdin', '--json', data=binary).stdout)
        self.assertEqual(result['sha256'], hashlib.sha256(binary).hexdigest())
        self.assertEqual((self.root / 'session-a/staging/dir/data').read_bytes(), binary)
        self.file_api('create', '-f', 'existing.txt', '--text', 'replace', ok=False)
        self.assertEqual((self.root / 'session-a/staging/existing.txt').read_text(), 'proposal')
        self.assertIn('dir/data', self.run_cli('--list').stdout)
        self.assertEqual(self.git('status', '--porcelain').stdout, '')

    def test_managed_copy_workspace_and_staged_binary_permissions(self):
        self.init()
        source = self.repo / 'binary'
        source.write_bytes(b'\x00\xff\n')
        source.chmod(0o755)
        result = json.loads(self.file_api('copy', '--workspace', '-f', 'binary', '--json').stdout)
        self.assertEqual(result['source_root'], 'workspace')
        self.assertEqual(result['path'], 'binary')
        copied = self.root / 'session-a/staging/binary'
        self.assertEqual(copied.read_bytes(), source.read_bytes())
        if os.name != 'nt':
            self.assertEqual(copied.stat().st_mode & 0o777, 0o755)
        result = json.loads(self.file_api('copy', '-f', 'binary', '--to', 'nested/second', '--json').stdout)
        self.assertEqual(result['source_root'], 'staged')
        self.assertEqual((self.root / 'session-a/staging/nested/second').read_bytes(), source.read_bytes())
        self.file_api('copy', '--workspace', '-f', 'existing.txt', '--to', 'renamed-copy')
        self.assertEqual(json.loads((self.root / 'session-a/renames.json').read_text()), {})
        self.file_api('copy', '-f', 'existing.txt', '--to', 'missing-source', ok=False)
        self.file_api('copy', '--workspace', '-f', 'existing.txt', '--to', 'binary', ok=False)
        self.file_api('copy', '-f', 'binary', '--to', 'binary', ok=False)
        self.assertEqual(copied.read_bytes(), source.read_bytes())
        self.assertFalse((self.repo / 'nested').exists())

    def test_managed_rename_tracks_origin_summary_and_can_move_back(self):
        path = self.propose()
        self.run_cli('summarize', '-f', 'existing.txt', '-m', 'Revise greeting.', '--summary', 'Improve greetings.')
        result = json.loads(self.file_api('rename', '-f', 'existing.txt', '--to', 'nested/renamed.txt', '--json').stdout)
        self.assertEqual(result['operation'], 'rename')
        self.assertFalse(path.exists())
        self.assertTrue((self.repo / 'existing.txt').exists())
        self.assertEqual(json.loads((self.root / 'session-a/renames.json').read_text()), {'nested/renamed.txt': 'existing.txt'})
        summaries = json.loads(self.run_cli('summarize', '--json').stdout)
        self.assertEqual(summaries['files'], {'nested/renamed.txt': 'Revise greeting.'})
        self.file_api('rename', '-f', 'nested/renamed.txt', '--to', 'second.txt')
        self.assertEqual(json.loads((self.root / 'session-a/renames.json').read_text()), {'second.txt': 'existing.txt'})
        self.file_api('rename', '-f', 'second.txt', '--to', 'existing.txt')
        self.assertEqual(json.loads((self.root / 'session-a/renames.json').read_text()), {})
        self.assertEqual(path.read_text(), 'proposed\n')
        self.file_api('rename', '-f', 'existing.txt', '--to', 'final.txt')
        self.run_cli('apply', '-f', 'final.txt')
        self.assertFalse((self.repo / 'existing.txt').exists())
        self.assertEqual((self.repo / 'final.txt').read_text(), 'proposed\n')

    def test_managed_rename_new_proposal_and_collision_failures(self):
        self.propose(rel='new.txt')
        self.file_api('rename', '-f', 'new.txt', '--to', 'dir/new.txt')
        self.assertEqual(json.loads((self.root / 'session-a/renames.json').read_text()), {})
        self.propose(rel='collision.txt')
        for destination in ('collision.txt', 'existing.txt', 'dir/new.txt'):
            self.file_api('rename', '-f', 'dir/new.txt', '--to', destination, ok=False)
            self.assertTrue((self.root / 'session-a/staging/dir/new.txt').exists())
        self.file_api('rename', '-f', 'new', '--to', 'fuzzy', ok=False)
        self.file_api('rename', '-f', 'dir/new.txt', '--workspace', '--to', 'oops', ok=False)
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')

    def test_managed_delete_discards_without_workspace_deletion(self):
        self.propose()
        self.propose(rel='keep.txt')
        self.run_cli('summarize', '-f', 'existing.txt', '-m', 'Change greeting.', '--summary', 'Two changes.')
        result = json.loads(self.file_api('delete', '-f', 'existing.txt', '--json').stdout)
        self.assertEqual(result, {'operation': 'delete', 'path': 'existing.txt', 'mode': 'discard'})
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')
        self.assertFalse((self.root / 'session-a/staging/existing.txt').exists())
        data = json.loads(self.run_cli('summarize', '--json').stdout)
        self.assertEqual(data, {'session': 'Two changes.', 'files': {}})
        self.file_api('delete', '-f', 'keep.txt')
        self.assertEqual(json.loads(self.run_cli('summarize', '--json').stdout)['session'], '')
        self.file_api('delete', '-f', 'existing.txt', ok=False)

    def test_managed_delete_proposes_and_cancels_or_applies(self):
        self.propose()
        self.propose(rel='keep.txt')
        result = json.loads(self.file_api('delete', '-f', 'existing.txt', '--workspace', '--json').stdout)
        self.assertEqual(result['mode'], 'propose')
        self.assertEqual(json.loads((self.root / 'session-a/renames.json').read_text()), {'_deletions': ['existing.txt']})
        self.assertTrue((self.repo / 'existing.txt').exists())
        self.assertFalse((self.root / 'session-a/staging/existing.txt').exists())
        self.run_cli('summarize', '-f', 'existing.txt', '-m', 'Remove obsolete greeting.')
        self.file_api('delete', '-f', 'existing.txt')
        self.assertEqual(json.loads((self.root / 'session-a/renames.json').read_text()), {})
        self.assertTrue((self.repo / 'existing.txt').exists())
        self.file_api('delete', '-f', 'existing.txt', '--workspace')
        self.file_api('delete', '-f', 'existing.txt', '--workspace')
        self.run_cli('apply', '-f', 'existing.txt')
        self.assertFalse((self.repo / 'existing.txt').exists())
        self.assertTrue((self.root / 'session-a/staging/keep.txt').exists())

    def test_managed_file_operations_reject_manifest_conflicts(self):
        self.propose()
        self.file_api('rename', '-f', 'existing.txt', '--to', 'renamed.txt')
        for args in [('create', '-f', 'existing.txt'),
                     ('copy', '-f', 'renamed.txt', '--to', 'existing.txt'),
                     ('delete', '-f', 'existing.txt', '--workspace')]:
            self.file_api(*args, ok=False)
        self.assertTrue((self.root / 'session-a/staging/renamed.txt').exists())
        self.file_api('delete', '-f', 'renamed.txt')
        self.assertEqual(json.loads((self.root / 'session-a/renames.json').read_text()), {})
        self.file_api('delete', '-f', 'existing.txt', '--workspace')
        for args in [('create', '-f', 'existing.txt'),
                     ('copy', '--workspace', '-f', 'existing.txt'),
                     ('rename', '-f', 'existing.txt', '--to', 'new.txt')]:
            self.file_api(*args, ok=False)
        self.assertTrue((self.repo / 'existing.txt').exists())

    def test_managed_file_operations_validate_before_mutation(self):
        path = self.propose()
        metadata = self.root / 'session-a/summaries.json'
        metadata.write_text('{')
        for args in [('create', '-f', 'new.txt'), ('copy', '-f', 'existing.txt', '--to', 'new.txt'),
                     ('rename', '-f', 'existing.txt', '--to', 'new.txt'), ('delete', '-f', 'existing.txt')]:
            self.file_api(*args, ok=False)
            self.assertEqual(path.read_text(), 'proposed\n')
            self.assertFalse((path.parent / 'new.txt').exists())
        metadata.unlink()
        for rel in ('../outside', '/tmp/outside', '.git/config', 'a/../existing.txt', 'C:/outside', 'a\\b'):
            for args in [('create', '-f', rel), ('copy', '-f', 'existing.txt', '--to', rel),
                         ('copy', '--workspace', '-f', rel, '--to', 'new.txt'),
                         ('rename', '-f', 'existing.txt', '--to', rel), ('delete', '-f', rel),
                         ('delete', '-f', rel, '--workspace')]:
                self.file_api(*args, ok=False)
        self.assertEqual(path.read_text(), 'proposed\n')
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')

    @unittest.skipIf(os.name == 'nt', 'Symlink privileges depend on Windows host settings')
    def test_managed_file_operations_reject_symlinks_and_special_files(self):
        path = self.propose()
        (path.parent / 'linked').symlink_to(self.repo, target_is_directory=True)
        for args in [('create', '-f', 'linked/new.txt'), ('copy', '--workspace', '-f', 'existing.txt', '--to', 'linked/new.txt'),
                     ('rename', '-f', 'existing.txt', '--to', 'linked/new.txt'), ('delete', '-f', 'linked/existing.txt')]:
            self.file_api(*args, ok=False)
        (path.parent / 'linked').unlink()
        os.mkfifo(path.parent / 'pipe')
        self.file_api('copy', '-f', 'pipe', '--to', 'new.txt', ok=False)
        self.file_api('delete', '-f', 'pipe', ok=False)
        self.file_api('rename', '-f', 'pipe', '--to', 'new.txt', ok=False)
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')

    def test_managed_file_operations_require_staging_outside_workspace(self):
        self.propose()
        nested = self.repo / 'forged-session'
        shutil.copytree(self.root / 'session-a', nested)
        session = staged.Session(nested)
        for command in [('create', '-f', 'new.txt'), ('copy', '--workspace', '-f', 'existing.txt'),
                        ('rename', '-f', 'existing.txt', '--to', 'new.txt'), ('delete', '-f', 'existing.txt')]:
            args = staged.make_parser()[0].parse_args(list(command))
            with self.assertRaises(staged.StagedError):
                (staged.delete_file if command[0] == 'delete' else staged.file_operation)(session, args)
        self.assertEqual((nested / 'staging/existing.txt').read_text(), 'proposed\n')

    def test_summary_updates_preserve_unrelated_metadata_and_workspace(self):
        self.propose()
        self.propose(rel='other.txt')
        path = self.root / 'session-a/summaries.json'
        path.write_text(json.dumps({'extra': 1, 'files': {'other.txt': 'Another change.'}}))
        result = self.run_cli('summarize', '-f', 'existing.txt', '-m', 'Revise the greeting.',
                              '--summary', 'Improve greetings.', '--json')
        data = json.loads(result.stdout)
        self.assertEqual(data['session'], 'Improve greetings.')
        self.assertEqual(data['files'], {'existing.txt': 'Revise the greeting.', 'other.txt': 'Another change.'})
        self.assertEqual(data['extra'], 1)
        self.assertEqual(json.loads(self.run_cli('summarize', '--json').stdout), data)
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')
        self.assertFalse((self.root / 'session-a/staging/summaries.json').exists())
        self.run_cli('summarize', '-f', 'existing.txt', '--clear')
        self.run_cli('summarize', '--clear')
        data = json.loads(path.read_text())
        self.assertEqual(data['session'], '')
        self.assertEqual(data['files'], {'other.txt': 'Another change.'})

    def test_summary_stdin_and_exact_paths_and_invalid_combinations(self):
        self.propose(rel='nested/existing.txt')
        self.file_api('summarize', '--stdin', data='  Improve café output.\n'.encode())
        self.assertEqual(json.loads(self.run_cli('summarize', '--json').stdout)['session'], 'Improve café output.')
        for rel in ('existing', 'existing.txt', '../escape', '/tmp/escape', '.git/config', 'nested//existing.txt'):
            self.run_cli('summarize', '-f', rel, '-m', 'Invalid.', ok=False)
        self.run_cli('summarize', '--summary', 'Invalid.', ok=False)
        self.run_cli('summarize', '-f', 'nested/existing.txt', '--summary', 'Invalid.', ok=False)
        self.run_cli('summarize', '-m', 'Invalid.', '--clear', ok=False)
        self.assertEqual(json.loads(self.run_cli('summarize', '--json').stdout)['files'], {})

    def test_summary_stdin_uses_utf8_and_rejects_invalid_bytes_without_mutation(self):
        self.propose()
        # Reproduce Windows redirected stdin even on UTF-8 hosts.
        with patch.dict(self.env, PYTHONIOENCODING='cp1252'):
            result = self.file_api('summarize', '--stdin', '--json',
                                   data='  Improve café output.\r\n'.encode('utf-8'))
        self.assertEqual(json.loads(result.stdout)['session'], 'Improve café output.')
        metadata = self.root / 'session-a/summaries.json'
        before = metadata.read_bytes()
        result = self.file_api('summarize', '--stdin', data=b'\xff', ok=False)
        self.assertIn(b'Summary input must be UTF-8', result.stderr)
        self.assertEqual(metadata.read_bytes(), before)

    def test_sessions_show_summaries_without_polluting_completion(self):
        self.init('legacy')
        self.run_cli('summarize', '--session', 'legacy', '-m', 'Improve greetings.\nKeep them concise.')
        self.init('empty')
        output = self.run_cli('--sessions').stdout
        self.assertIn('Summary: Improve greetings. Keep them concise.', output)
        self.assertIn('Summary: (no summary yet)', output)
        self.assertIn('Improve greetings.', self.run_cli('-R').stdout)
        self.assertEqual(set(self.run_cli('--complete-sessions').stdout.splitlines()), {'legacy', 'empty'})
        self.assertFalse((self.root / 'empty/summaries.json').exists())

    def test_verbose_diff_overview_and_selected_review(self):
        self.propose()
        self.propose(rel='other.txt')
        self.run_cli('summarize', '-f', 'existing.txt', '-m', 'Revise the greeting.',
                     '--summary', 'Improve greetings.')
        for flags in [('diff', '-v'), ('diff', '--verbose'), ('-v', 'diff')]:
            output = self.run_cli(*flags).stdout
            self.assertIn('Session summary (session-a): Improve greetings.', output)
            self.assertIn('Summary: Revise the greeting.', output)
            self.assertIn('Summary: (no summary yet)', output)
            self.assertIn('Workspace:', output)
        self.assertNotIn('Revise the greeting.', self.run_cli('diff').stdout)
        output = self.run_cli('diff', '-v', '-f', 'existing.txt').stdout
        self.assertLess(output.index('Improve greetings.'), output.index('Revise the greeting.'))
        self.assertLess(output.index('Revise the greeting.'), output.index('-original'))
        self.assertIn('+proposed', output)
        self.assertNotIn('other.txt', output)
        self.assertNotIn('Revise the greeting.', self.run_cli('diff', '-f', 'existing.txt').stdout)
        self.assertIn('Revise the greeting.', self.run_cli('diff', '--all', '-v').stdout)

    def test_summary_supports_rename_and_deletion_and_clean(self):
        self.propose(rel='renamed.txt')
        self.manifest({'renamed.txt': 'old.txt', '_deletions': ['existing.txt']})
        for rel in ('renamed.txt', 'existing.txt'):
            self.run_cli('summarize', '-f', rel, '-m', 'Explain ' + rel,
                         '--summary', 'Restructure files.')
        output = self.run_cli('diff', '-v', '--all').stdout
        self.assertIn('Explain renamed.txt', output)
        self.assertIn('Explain existing.txt', output)
        self.run_cli('clean', '-f', 'renamed.txt', '-y')
        data = json.loads(self.run_cli('summarize', '--json').stdout)
        self.assertEqual(data['files'], {'existing.txt': 'Explain existing.txt'})
        self.assertEqual(data['session'], 'Restructure files.')
        self.run_cli('clean', '--all', '-y')
        self.assertEqual(json.loads(self.run_cli('summarize', '--json').stdout), {'session': '', 'files': {}})
        self.assertTrue((self.repo / 'existing.txt').exists())

    def test_verbose_cross_session_diff_labels_both_summaries(self):
        for key in ('one', 'two'):
            self.propose(key=key, text=key + '\n')
            self.run_cli('summarize', '--session', key, '-f', 'existing.txt', '-m', key + ' file.',
                         '--summary', key + ' session.')
        for flags in [('--between', 'one', 'two'), ('--session', 'one', '--compare-session', 'two')]:
            output = self.run_cli('diff', '-v', '--all', *flags).stdout
            for key in ('one', 'two'):
                self.assertIn('Session summary ({}): {} session.'.format(key, key), output)
                self.assertIn('existing.txt [{}]: {} file.'.format(key, key), output)
            self.assertIn('-one', output)
            self.assertIn('+two', output)

    def test_migrate_summary_lifecycle(self):
        self.propose('source', rel='one.txt')
        self.propose('source', rel='two.txt')
        for rel in ('one.txt', 'two.txt'):
            self.run_cli('summarize', '--session', 'source', '-f', rel, '-m', 'Explain ' + rel,
                         '--summary', 'Both files.')
        self.run_cli('migrate', '--from', 'source', '--to', 'whole', '--all')
        self.assertEqual(json.loads(self.run_cli('summarize', '--session', 'whole', '--json').stdout),
                         json.loads(self.run_cli('summarize', '--session', 'source', '--json').stdout))
        self.run_cli('migrate', '--from', 'source', '--to', 'partial', '-f', 'one.txt')
        data = json.loads(self.run_cli('summarize', '--session', 'partial', '--json').stdout)
        self.assertEqual(data, {'session': '', 'files': {'one.txt': 'Explain one.txt'}})
        self.run_cli('summarize', '--session', 'partial', '-m', 'My selection.')
        self.run_cli('summarize', '--session', 'source', '-f', 'one.txt', '--clear')
        self.run_cli('migrate', '--from', 'source', '--to', 'partial', '-f', 'one.txt', '--force')
        self.assertEqual(json.loads(self.run_cli('summarize', '--session', 'partial', '--json').stdout),
                         {'session': 'My selection.', 'files': {}})

    def test_malformed_summary_and_symlink_are_rejected_before_mutation(self):
        proposal = self.propose()
        path = self.root / 'session-a/summaries.json'
        for data in ('{', '[]', '{"session": 1}', '{"files": []}',
                     '{"files": {"../escape": "oops"}}', '{"files": {"existing.txt": false}}'):
            path.write_text(data)
            self.run_cli('summarize', '-m', 'Replace.', ok=False)
            self.run_cli('diff', '-v', ok=False)
            self.run_cli('clean', '--all', '-y', ok=False)
            self.assertTrue(proposal.exists())
            self.assertEqual(path.read_text(), data)
        path.unlink()
        outside = self.base / 'outside.json'
        outside.write_text('{}')
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest('Symlinks unavailable')
        self.run_cli('summarize', '-m', 'Escape.', ok=False)
        self.assertEqual(outside.read_text(), '{}')

    def test_summary_help_and_completion(self):
        self.assertIn('summarize', self.run_cli('--complete-options').stdout.splitlines())
        options = self.run_cli('--complete-options', 'summarize').stdout.splitlines()
        for flag in ('-m', '--message', '--stdin', '--clear', '--summary', '--json', '--file'):
            self.assertIn(flag, options)
        self.assertIn('summaries', self.run_cli('diff', '--help').stdout)

    def test_read_exact_byte_ranges_and_binary_json(self):
        import base64
        import hashlib
        path = self.propose(rel='binary.dat')
        data = b'zero\x00\xff\r\nlast'
        path.write_bytes(data)
        self.assertEqual(self.file_api('read', '-f', 'binary.dat', '--offset', '4', '--length', '3').stdout, data[4:7])
        result = json.loads(self.file_api('read', '-f', 'binary.dat', '--offset', '4', '--length', '3', '--json').stdout)
        self.assertEqual(base64.b64decode(result['content']), data[4:7])
        self.assertEqual(result['encoding'], 'base64')
        self.assertEqual(result['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(result['next_offset'], 7)
        self.assertFalse(result['eof'])
        self.assertEqual(self.file_api('read', '-f', 'binary.dat', '--offset', str(len(data))).stdout, b'')
        self.file_api('read', '-f', 'binary.dat', '--offset', str(len(data) + 1), ok=False)
        self.assertEqual(self.file_api('read', '-f', 'binary.dat', '--length', '100').stdout, data)

    def test_read_workspace_and_lines_preserves_bytes_and_has_no_writes(self):
        self.init()
        source = self.repo / 'existing.txt'
        source.write_bytes('one\r\ntwø\nlast'.encode())
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        self.assertEqual(self.file_api('read', '--workspace', '-f', 'existing.txt', '--start-line', '2', '--end-line', '2').stdout,
                         'twø\n'.encode())
        self.assertEqual(self.file_api('read', '--workspace', '-f', 'existing.txt', '--start-line', '3').stdout, b'last')
        self.file_api('read', '--workspace', '-f', 'existing.txt', '--start-line', '4', '--end-line', '4', ok=False)
        self.file_api('read', '--workspace', '-f', 'EXISTING', ok=False)
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))

    def test_write_whole_file_and_explicit_empty_input(self):
        self.init()
        result = json.loads(self.file_api('write', '-f', 'src/new.txt', '--text', 'héllo', '--json').stdout)
        path = self.root / 'session-a/staging/src/new.txt'
        self.assertEqual(path.read_bytes(), 'héllo'.encode())
        self.assertTrue(result['created'])
        self.file_api('write', '-f', 'src/new.txt', ok=False)
        self.assertEqual(path.read_bytes(), 'héllo'.encode())
        self.file_api('write', '-f', 'src/new.txt', '--text', '')
        self.assertEqual(path.read_bytes(), b'')
        self.assertFalse((self.repo / 'src').exists())

    def test_write_splices_binary_bytes_and_appends(self):
        path = self.propose(text='abcdef')
        self.file_api('write', '-f', 'existing.txt', '--offset', '2', '--delete-count', '2', '--stdin', data=b'\x00\xff')
        self.assertEqual(path.read_bytes(), b'ab\x00\xffef')
        self.file_api('write', '-f', 'existing.txt', '--offset', '2', '--text', 'insert')
        self.assertEqual(path.read_bytes(), b'abinsert\x00\xffef')
        self.file_api('write', '-f', 'existing.txt', '--offset', '2', '--delete-count', '6', '--text', '')
        self.assertEqual(path.read_bytes(), b'ab\x00\xffef')
        self.file_api('write', '-f', 'existing.txt', '--append', '--stdin', data=b'\r\n')
        self.assertEqual(path.read_bytes(), b'ab\x00\xffef\r\n')
        self.assertEqual((self.repo / 'existing.txt').read_bytes(), b'original\n')

    def test_write_line_replace_insert_and_eof(self):
        path = self.propose()
        path.write_bytes(b'one\r\ntwo\r\nlast')
        self.file_api('write', '-f', 'existing.txt', '--start-line', '2', '--end-line', '2', '--stdin', data=b'new\r\n')
        self.assertEqual(path.read_bytes(), b'one\r\nnew\r\nlast')
        self.file_api('write', '-f', 'existing.txt', '--start-line', '2', '--text', 'insert\n')
        self.assertEqual(path.read_bytes(), b'one\r\ninsert\nnew\r\nlast')
        self.file_api('write', '-f', 'existing.txt', '--start-line', '5', '--text', '!')
        self.assertEqual(path.read_bytes(), b'one\r\ninsert\nnew\r\nlast!')
        # No newlines are synthesized; an unterminated final line stays one line.
        self.file_api('write', '-f', 'existing.txt', '--start-line', '6', '--text', 'bad', ok=False)

    def test_first_partial_write_requires_explicit_workspace_seed(self):
        import hashlib
        self.init()
        original = self.repo / 'existing.txt'
        if os.name != 'nt':
            original.chmod(0o755)
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        self.file_api('write', '-f', 'existing.txt', '--offset', '2', '--text', '!', ok=False)
        target = self.root / 'session-a/staging/existing.txt'
        self.assertFalse(target.exists())
        self.file_api('write', '-f', 'existing.txt', '--from-workspace', '--offset', '0', '--delete-count', '8',
                      '--text', 'changed', '--expect-sha256', digest)
        self.assertEqual(target.read_bytes(), b'changed\n')
        self.assertEqual(original.read_bytes(), b'original\n')
        if os.name != 'nt':
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)
        self.file_api('write', '-f', 'existing.txt', '--from-workspace', '--text', 'overwrite', ok=False)
        self.assertEqual(target.read_bytes(), b'changed\n')

    def test_stale_hash_rejects_edit_without_side_effects(self):
        path = self.propose(text='before')
        snapshot = json.loads(self.file_api('read', '-f', 'existing.txt', '--json').stdout)
        self.file_api('write', '-f', 'existing.txt', '--text', 'after')
        self.file_api('write', '-f', 'existing.txt', '--text', 'stale', '--expect-sha256', snapshot['sha256'], ok=False)
        self.assertEqual(path.read_bytes(), b'after')
        self.assertFalse(list(path.parent.glob('.staged-write-*')))

    def test_failed_atomic_write_keeps_original_and_cleans_temporary_file(self):
        path = self.propose(text='before')
        args = staged.make_parser()[0].parse_args(['write', '-f', 'existing.txt', '--text', 'after'])
        with patch.object(staged.os, 'replace', side_effect=OSError('replacement failed')):
            with self.assertRaises(OSError):
                staged.write_file_range(staged.Session(path.parent.parent), args)
        self.assertEqual(path.read_bytes(), b'before')
        self.assertFalse(list(path.parent.glob('.staged-write-*')))

    def test_change_during_stdin_collection_is_not_overwritten(self):
        import io
        path = self.propose(text='before')
        args = staged.make_parser()[0].parse_args(['write', '-f', 'existing.txt', '--stdin'])
        def changed_during_input(source, target, length):
            target.write(source.read())
            path.write_bytes(b'another writer')
        with patch.object(staged.sys, 'stdin') as stdin, \
                patch.object(staged.shutil, 'copyfileobj', side_effect=changed_during_input):
            stdin.buffer = io.BytesIO(b'our edit')
            stdin.isatty.return_value = False
            with self.assertRaises(staged.StagedError):
                staged.write_file_range(staged.Session(path.parent.parent), args)
        self.assertEqual(path.read_bytes(), b'another writer')
        self.assertFalse(list(path.parent.glob('.staged-write-*')))

    def test_file_api_rejects_staging_inside_workspace(self):
        self.init()
        nested = self.repo / 'forged-session'
        shutil.copytree(self.root / 'session-a', nested)
        args = staged.make_parser()[0].parse_args(['write', '-f', 'existing.txt', '--text', 'oops'])
        with self.assertRaises(staged.StagedError):
            staged.write_file_range(staged.Session(nested), args)
        self.assertEqual((self.repo / 'existing.txt').read_bytes(), b'original\n')
        self.assertFalse((nested / 'staging/existing.txt').exists())

    def test_invalid_ranges_never_modify_target(self):
        path = self.propose(text='one\ntwo\n')
        for flags in [('--offset', '-1'), ('--offset', '100'), ('--offset', '3', '--delete-count', '100'),
                      ('--delete-count', '1'), ('--start-line', '0'), ('--start-line', '3', '--end-line', '3'),
                      ('--start-line', '2', '--end-line', '1'), ('--end-line', '2'),
                      ('--start-line', '1', '--offset', '0'), ('--append', '--offset', '0'),
                      ('--start-line', '1', '--append'), ('--expect-sha256', 'not-a-hash')]:
            with self.subTest(flags=flags):
                self.file_api('write', '-f', 'existing.txt', '--text', 'oops', *flags, ok=False)
                self.assertEqual(path.read_bytes(), b'one\ntwo\n')
        self.file_api('read', '-f', 'existing.txt', '--start-line', '1', '--length', '1', ok=False)

    def test_file_api_rejects_escape_paths_and_deletion_conflicts(self):
        self.init()
        for rel in ('../outside', '/tmp/outside', '.git/config', 'a/../existing.txt', 'C:/outside', 'a\\b'):
            for command in ('read', 'write'):
                flags = ['--text', 'oops'] if command == 'write' else []
                self.file_api(command, '-f', rel, *flags, ok=False)
        self.manifest({'_deletions': ['existing.txt']})
        self.file_api('write', '-f', 'existing.txt', '--text', 'oops', ok=False)
        self.assertEqual((self.repo / 'existing.txt').read_bytes(), b'original\n')
        self.assertFalse((self.root / 'session-a/staging/existing.txt').exists())

    @unittest.skipIf(os.name == 'nt', 'Symlink privileges depend on Windows host settings')
    def test_file_api_rejects_symlinks_and_special_files(self):
        staging = self.init()
        (staging / 'linked').symlink_to(self.repo, target_is_directory=True)
        for command in ('read', 'write'):
            flags = ['--text', 'oops'] if command == 'write' else []
            self.file_api(command, '-f', 'linked/existing.txt', *flags, ok=False)
        (self.repo / 'outside-link').symlink_to(self.home, target_is_directory=True)
        self.file_api('read', '--workspace', '-f', 'outside-link/secret', ok=False)
        os.mkfifo(staging / 'pipe')
        self.file_api('read', '-f', 'pipe', ok=False)
        self.file_api('write', '-f', 'pipe', '--text', 'oops', ok=False)

    def test_file_api_uses_explicit_session_and_updates_discoverable_files(self):
        self.init('first')
        self.init('second')
        self.file_api('--session', 'first', 'write', '-f', '.hidden/new.txt', '--text', 'first')
        self.assertFalse((self.root / 'second/staging/.hidden').exists())
        self.assertIn('.hidden/new.txt', self.run_cli('--session', 'first', '--list').stdout)
        self.assertEqual(self.file_api('read', '--session', 'first', '-f', '.hidden/new.txt').stdout, b'first')

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
typeset -A compstate=()
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
            (['read', ''], ['--start-line', '--end-line', '--offset', '--length', '--workspace', '--json'], ['--text']),
            (['write', ''], ['--stdin', '--text', '--from-workspace', '--delete-count', '--expect-sha256'], ['--workspace']),
            (['create', ''], ['--text', '--stdin', '--json'], ['--workspace', '--to']),
            (['copy', ''], ['--workspace', '--to', '--json'], ['--text']),
            (['rename', ''], ['--to', '--file', '--json'], ['--workspace']),
            (['delete', ''], ['--workspace', '--file', '--json'], ['--to']),
            (['copy', '-f', 'existing.txt', '--to', ''], [], ['session-a', 'session-b']),
            (['rename', '-f', 'existing.txt', '--to', ''], [], ['session-a', 'session-b']),
            (['migrate', '--from', 'session-a', '--to', ''], ['session-a', 'session-b'], ['--all']),
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
typeset -A compstate=()
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
        support = _Support.ensure()
        env = dict(self.env, PATH=str(support.bin) + os.pathsep + str(SCRIPT.parent) + os.pathsep + self.env['PATH'], TERM='xterm')
        process = subprocess.Popen(['zsh', '-f'], stdin=slave, stdout=slave, stderr=slave,
                                   cwd=self.repo, env=env, start_new_session=True)
        os.close(slave)
        def read_until(marker):
            output = b''
            deadline = time.monotonic() + 15
            while marker not in output and time.monotonic() < deadline:
                if select.select([master], [], [], 0.02)[0]:
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

    @unittest.skipIf(os.name == 'nt' or not shutil.which('zsh'), 'Requires a POSIX terminal and Zsh')
    def test_zsh_ambiguous_tab_preserves_input_and_lists_matches(self):
        import re
        import select
        import time
        for rel in ('alpha/_staged', 'beta/_staged', 'alpha/shared-one.txt', 'alpha/shared-two.txt'):
            self.propose(rel=rel)
        self.run_cli('install-completion', '--shell', 'zsh')
        master, slave = os.openpty()
        support = _Support.ensure()
        env = dict(self.env, PATH=str(support.bin) + os.pathsep + str(SCRIPT.parent) + os.pathsep + self.env['PATH'], TERM='xterm')
        process = subprocess.Popen(['zsh', '-f'], stdin=slave, stdout=slave, stderr=slave,
                                   cwd=self.repo, env=env, start_new_session=True)
        os.close(slave)
        pending = b''
        def read_until(marker):
            nonlocal pending
            deadline = time.monotonic() + 10
            while marker not in pending and time.monotonic() < deadline:
                if select.select([master], [], [], 0.02)[0]:
                    pending += os.read(master, 65536)
            self.assertIn(marker, pending)
            end = pending.index(marker) + len(marker)
            output, pending = pending[:end], pending[end:]
            return output
        try:
            os.write(master, b'''source "$HOME/.zshrc"; PS1='READY> '; bindkey '^I' complete-word; bindkey '^Y' menu-complete; bindkey '^B' backward-char; _capture() { print -r -- "CAPTURE_START${BUFFER}CAPTURE_END"; zle send-break; }; zle -N _capture; bindkey '^X' _capture; print SETUP_DONE\n''')
            read_until(b'\r\nSETUP_DONE\r\n')
            cases = [
                (b'_sta', b'\t', b'_sta', (b'alpha/_staged', b'beta/_staged')),
                (b'_STA', b'\t', b'_STA', (b'alpha/_staged', b'beta/_staged')),
                (b'alpha/sh', b'\t', b'alpha/sh', (b'alpha/shared-one.txt', b'alpha/shared-two.txt')),
                (b'_sta', b'\t\t', b'alpha/_staged', (b'alpha/_staged', b'beta/_staged')),
                (b'_sta', b'\t\t\t', b'beta/_staged', ()),
                (b'missing', b'\t', b'missing', ()),
                (b'alpha/_sta', b'\t', b'alpha/_staged ', ()),
            ]
            for query, keys, expected, choices in cases:
                with self.subTest(query=query, keys=keys):
                    read_until(b'READY> ')
                    command = b'staged diff -f '
                    os.write(master, command + query + keys + b'\x18')
                    output = read_until(b'CAPTURE_END')
                    captured = re.search(b'CAPTURE_START(.*?)CAPTURE_END', output).group(1)
                    self.assertEqual(captured, command + expected)
                    for choice in choices:
                        self.assertIn(choice, output)
            # Completing in the middle of a command keeps its trailing arguments.
            read_until(b'READY> ')
            command = b'staged --session session-a diff -f _sta --tool cli'
            os.write(master, command + b'\x02' * len(b' --tool cli') + b'\t\x18')
            output = read_until(b'CAPTURE_END')
            self.assertEqual(re.search(b'CAPTURE_START(.*?)CAPTURE_END', output).group(1), command)
            # Deliberate menu selection remains available for ambiguous matches.
            read_until(b'READY> ')
            os.write(master, b'staged diff -f _sta\x19\x18')
            output = read_until(b'CAPTURE_END')
            captured = re.search(b'CAPTURE_START(.*?)CAPTURE_END', output).group(1)
            self.assertIn(captured.rstrip(), (b'staged diff -f alpha/_staged', b'staged diff -f beta/_staged'))
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
typeset -A compstate=()
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
    def test_cli_test_launcher_uses_an_empty_registry(self):
        support = _Support.ensure()
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'probe.code'
            probe = "import winreg\nwinreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment')\n"
            cache.write_bytes(marshal.dumps(compile(probe, '<registry-probe>', 'exec')))
            result = subprocess.run([sys.executable, str(support.launcher), str(SCRIPT), str(cache)],
                                    env=isolated_env(home=tmp), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('No registry keys in the test sandbox', result.stderr)

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



class Uninstall(unittest.TestCase):
    setUp = CLI.setUp
    git = CLI.git
    run_cli = CLI.run_cli
    init = CLI.init
    propose = CLI.propose

    def test_inherited_profile_locations_are_not_touched(self):
        self.propose()
        self.run_cli('install-completion', '--shell', 'bash')
        host = self.base / 'simulated-real-home'
        profiles = [host / 'zsh/.zshrc',
                    host / 'OneDrive/Documents/PowerShell/Microsoft.PowerShell_profile.ps1',
                    host / 'xdg/powershell/Microsoft.PowerShell_profile.ps1']
        content = (staged.COMPLETION_MARKER_START + '\nkeep this real installation\n' +
                   staged.COMPLETION_MARKER_END + '\n').encode('utf-8')
        for profile in profiles:
            profile.parent.mkdir(parents=True, exist_ok=True)
            profile.write_bytes(content)
        inherited = dict(self.env, ZDOTDIR=str(host / 'zsh'), OneDrive=str(host / 'OneDrive'),
                         XDG_CONFIG_HOME=str(host / 'xdg'))
        env = isolated_env(inherited, home=self.home)
        self.run_cli('uninstall', '-y', '--keep-sessions', env=env)
        self.assertFalse((self.config / 'completions/staged.bash').exists())
        for profile in profiles:
            self.assertEqual(profile.read_bytes(), content)

    def test_uninstall_requires_confirmation_without_mutations(self):
        proposal = self.propose()
        self.run_cli('install-completion', '--shell', 'bash')
        result = self.run_cli('uninstall', ok=False)
        self.assertIn('Re-run interactively', result.stderr)
        self.assertTrue(proposal.exists())
        self.assertTrue((self.config / 'completions/staged.bash').exists())

    def test_uninstall_keeps_sessions_and_cleans_integrations(self):
        proposal = self.propose()
        custom = self.home / 'custom/stage'
        self.run_cli('install-skill', '--tool', 'codex', '--target-dir', str(custom))
        (custom / 'notes.txt').write_text('mine')
        rc = self.home / '.bashrc'
        rc.write_text('export KEEP=1\n')
        self.run_cli('install-completion', '--shell', 'bash')
        self.run_cli('use', '--session', 'session-a')
        result = self.run_cli('uninstall', '--yes', '--keep-sessions')
        self.assertIn('Kept 1 sessions', result.stdout)
        self.assertTrue(proposal.exists())
        self.assertEqual(rc.read_text().strip(), 'export KEEP=1')
        self.assertFalse((custom / 'SKILL.md').exists())
        self.assertEqual((custom / 'notes.txt').read_text(), 'mine')
        self.assertFalse((self.config / 'state.json').exists())
        self.assertFalse((self.config / 'shell_sessions').exists())
        self.assertFalse((self.config / 'completions').exists())
        self.assertTrue(SCRIPT.exists())
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')

    def test_yes_alone_does_not_delete_sessions(self):
        proposal = self.propose()
        self.run_cli('uninstall', '-y')
        self.assertTrue(proposal.exists())

    def test_uninstall_deletes_sessions_across_roots_and_repositories(self):
        self.propose()
        other_repo = self.base / 'other-repo'
        other_repo.mkdir()
        self.run_cli('init', 'session-b', cwd=other_repo)
        other_root = self.base / 'other-root'
        self.run_cli('init', 'session-c', '--root', str(other_root))
        default_root = self.home / ('AppData/Local/staged' if os.name == 'nt' else '.local/share/staged')
        self.run_cli('init', 'session-d', '--root', str(default_root))
        unrelated = self.root / 'unrelated'
        unrelated.mkdir()
        (unrelated / 'keep').write_text('keep')
        self.run_cli('uninstall', '-y', '--remove-sessions')
        for directory in (self.root / 'session-a', self.root / 'session-b', other_root / 'session-c', default_root / 'session-d'):
            self.assertFalse(directory.exists(), directory)
        self.assertTrue((unrelated / 'keep').exists())
        self.assertTrue((self.repo / 'existing.txt').exists())

    @unittest.skipIf(os.name == 'nt', 'Requires POSIX symlinks')
    def test_manual_symlink_removed_but_source_and_unrelated_command_kept(self):
        command = self.home / '.local/bin/staged'
        command.parent.mkdir(parents=True)
        command.symlink_to(SCRIPT)
        self.run_cli('uninstall', '-y', '--keep-sessions')
        self.assertFalse(command.is_symlink())
        self.assertTrue(SCRIPT.exists())
        command.symlink_to(self.repo / 'existing.txt')
        self.run_cli('uninstall', '-y', '--keep-sessions')
        self.assertTrue(command.is_symlink())

    @unittest.skipIf(os.name == 'nt', 'Requires POSIX symlinks')
    def test_linked_session_and_workspace_ancestor_are_never_deleted(self):
        self.propose()
        protected = self.base / 'protected'
        protected.mkdir()
        (protected / '.workspace').write_text(str(self.repo))
        (protected / 'staging').mkdir()
        (self.root / 'linked').symlink_to(protected, target_is_directory=True)
        self.run_cli('uninstall', '-y', '--remove-sessions')
        self.assertTrue(protected.exists())
        self.assertTrue((self.root / 'linked').is_symlink())

    def test_modified_skill_is_preserved(self):
        self.run_cli('install-skill', '--tool', 'codex')
        skill = self.home / '.agents/skills/stage/SKILL.md'
        skill.write_text('customized')
        result = self.run_cli('uninstall', '-y', '--keep-sessions')
        self.assertIn('Keeping modified installation', result.stdout)
        self.assertEqual(skill.read_text(), 'customized')

    def test_sandbox_cleanup_only_removes_permissions_added_by_installer(self):
        sandbox = self.home / '.cursor/sandbox.json'
        sandbox.parent.mkdir()
        sandbox.write_text(json.dumps({'additionalReadwritePaths': ['existing', str(self.root)]}))
        self.run_cli('install-skill', '--tool', 'cursor', '--configure-sandbox')
        self.run_cli('uninstall', '-y', '--keep-sessions')
        self.assertEqual(json.loads(sandbox.read_text())['additionalReadwritePaths'], ['existing', str(self.root)])
        cli = json.loads((self.home / '.cursor/cli-config.json').read_text())
        self.assertEqual(cli['permissions']['allow'], [])

    def test_uninstall_options_are_mutually_exclusive(self):
        self.run_cli('uninstall', '--keep-sessions', '--remove-sessions', ok=False)


    def test_powershell_loader_cleanup_preserves_other_profile_lines(self):
        self.run_cli('install-completion', '--shell', 'powershell')
        profile = self.home / 'Documents/PowerShell/Microsoft.PowerShell_profile.ps1'
        profile.parent.mkdir(parents=True)
        loader = ". '{}'".format(str(self.config / 'completions/staged.ps1').replace("'", "''"))
        profile.write_text('Write-Output keep\n' + loader + '\n')
        self.run_cli('uninstall', '-y', '--keep-sessions')
        self.assertEqual(profile.read_text(), 'Write-Output keep\n')

    @unittest.skipIf(os.name == 'nt', 'Requires POSIX symlinks')
    def test_recorded_zdotdir_and_legacy_installations(self):
        custom = self.home / 'zsh-config'
        custom.mkdir()
        env = dict(self.env, ZDOTDIR=str(custom))
        self.run_cli('install-completion', '--shell', 'zsh', env=env)
        skill = self.home / '.agents/skills/stage/SKILL.md'
        skill.parent.mkdir(parents=True)
        shutil.copy2(SCRIPT.parent / 'SKILL.md', skill)
        self.run_cli('uninstall', '-y', '--keep-sessions')
        self.assertNotIn(staged.COMPLETION_MARKER_START, (custom / '.zshrc').read_text())
        self.assertFalse(skill.exists())

    def test_windows_launcher_is_removed(self):
        with patch.object(staged, 'CONFIG_DIR', self.config), patch.object(staged.sys, 'platform', 'win32'):
            staged.install_launcher()
        launcher = self.config / 'bin/staged.cmd'
        self.assertTrue(launcher.exists())
        (self.config / 'installations.json').unlink()  # Legacy launcher without a receipt.
        self.run_cli('uninstall', '-y', '--keep-sessions')
        self.assertFalse(launcher.exists())

    def test_workspace_ancestor_in_discovery_root_is_preserved(self):
        root = self.base / 'discovery'
        ancestor = root / 'ancestor'
        workspace = ancestor / 'workspace'
        workspace.mkdir(parents=True)
        (ancestor / '.workspace').write_text(str(workspace))
        (ancestor / 'staging').mkdir()
        (workspace / 'keep').write_text('keep')
        self.run_cli('uninstall', '-y', '--remove-sessions', '--root', str(root), cwd=workspace)
        self.assertTrue((workspace / 'keep').exists())


class UninstallPrompts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='staged-uninstall-env-')
        self.addCleanup(temporary.cleanup)
        # These tests call uninstall in-process; environment lookups must also
        # stay isolated when individual tests patch HOME and CONFIG_DIR.
        registry = unittest.mock.Mock(spec=['HKEY_CURRENT_USER', 'OpenKey'])
        registry.OpenKey.side_effect = FileNotFoundError
        for patcher in (patch.dict(os.environ, isolated_env(home=temporary.name), clear=True),
                        patch.dict(sys.modules, {'winreg': registry})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_eof_aborts_unconfirmed_uninstall_but_keeps_sessions_with_yes(self):
        # Windows NUL can report isatty() while input() immediately raises EOFError.
        for yes in (False, True):
            with self.subTest(yes=yes), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                config = home / 'config'
                config.mkdir()
                state = config / 'state.json'
                state.write_bytes(b'{}')
                session = home / 'session'
                session.mkdir()
                proposal = session / 'proposal.txt'
                proposal.write_bytes(b'keep this proposal')
                ctx = unittest.mock.Mock()
                ctx.args = staged.make_parser()[0].parse_args(['uninstall'] + (['--yes'] if yes else []))
                with patch.object(staged, 'CONFIG_DIR', config), patch.object(staged, 'HOME', tmp), \
                        patch.object(staged, 'zsh_rc', return_value=home / '.zshrc'), \
                        patch.object(staged, 'uninstall_sessions', return_value=[session]), \
                        patch.object(staged, 'npm_uninstall_command', return_value=None), \
                        patch.object(staged, 'windows_launcher_path_edit', return_value=None), \
                        patch.object(staged.shutil, 'which', return_value=None), \
                        patch.object(staged.sys.stdin, 'isatty', return_value=True), \
                        patch('builtins.input', side_effect=EOFError) as prompt:
                    if yes:
                        staged.uninstall(ctx)
                    else:
                        with self.assertRaisesRegex(staged.StagedError, 'Re-run interactively'):
                            staged.uninstall(ctx)
                    prompt.assert_called_once()
                self.assertEqual(proposal.read_bytes(), b'keep this proposal')
                if yes:
                    self.assertFalse(state.exists())
                else:
                    self.assertEqual(state.read_bytes(), b'{}')

    def test_session_prompt_is_separate_and_defaults_to_keep(self):
        for answer, deleted in [('n', False), ('', False), ('yes', True)]:
            with self.subTest(answer=answer), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                config = home / 'config'
                session = home / 'session'
                session.mkdir()
                parser, _ = staged.make_parser()
                ctx = unittest.mock.Mock()
                ctx.args = parser.parse_args(['uninstall', '--yes'])
                with patch.object(staged, 'CONFIG_DIR', config), patch.object(staged, 'HOME', tmp), \
                        patch.object(staged, 'zsh_rc', return_value=home / '.zshrc'), \
                        patch.object(staged, 'uninstall_sessions', return_value=[session]), \
                        patch.object(staged, 'npm_uninstall_command', return_value=None), \
                        patch.object(staged.shutil, 'which', return_value=None), \
                        patch.object(staged.sys.stdin, 'isatty', return_value=True), \
                        patch('builtins.input', return_value=answer) as prompt:
                    staged.uninstall(ctx)
                self.assertEqual(session.exists(), not deleted)
                self.assertIn('Also permanently delete', prompt.call_args[0][0])

    def test_npm_detection_and_failure_leave_sessions_and_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            package = home / 'node_modules/@melchi/staged'
            package.mkdir(parents=True)
            config = home / 'config'
            config.mkdir()
            (config / 'state.json').write_text('{}')
            session = home / 'session'
            session.mkdir()
            with patch.object(staged, 'REPO_ROOT', package.resolve()), \
                    patch.object(staged.shutil, 'which', return_value='/usr/bin/npm'), \
                    patch.object(staged.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, str(home / 'node_modules') + '\n', '')):
                command = staged.npm_uninstall_command()
                self.assertEqual(command, ['/usr/bin/npm', 'uninstall', '--global', '--ignore-scripts', '@melchi/staged'])
            parser, _ = staged.make_parser()
            ctx = unittest.mock.Mock()
            ctx.args = parser.parse_args(['uninstall', '-y', '--remove-sessions'])
            with patch.object(staged, 'CONFIG_DIR', config), patch.object(staged, 'HOME', tmp), \
                    patch.object(staged, 'zsh_rc', return_value=home / '.zshrc'), \
                    patch.object(staged, 'uninstall_sessions', return_value=[session]), \
                    patch.object(staged, 'npm_uninstall_command', return_value=command), \
                    patch.object(staged.shutil, 'which', return_value=None), \
                    patch.object(staged.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
                with self.assertRaises(staged.StagedError):
                    staged.uninstall(ctx)
            self.assertTrue(session.exists())
            self.assertTrue((config / 'state.json').exists())


    def test_windows_path_removes_only_dedicated_launcher_entry(self):
        registry = unittest.mock.MagicMock()
        registry.QueryValueEx.return_value = ('C:\\Other;C:\\Users\\Test\\staged\\bin;C:\\Keep', 2)
        with patch.dict(sys.modules, {'winreg': registry}), \
                patch.object(staged.sys, 'platform', 'win32'), \
                patch.object(staged, 'CONFIG_DIR', Path('C:/Users/Test/staged')):
            self.assertEqual(staged.windows_launcher_path_edit(), ('C:\\Other;C:\\Keep', 2))

    def test_npm_unrelated_global_install_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(staged.shutil, 'which', return_value='/usr/bin/npm'), \
                patch.object(staged.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, tmp, '')):
            self.assertIsNone(staged.npm_uninstall_command())

    def test_successful_npm_uninstall_then_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / 'config'
            config.mkdir()
            (config / 'state.json').write_text('{}')
            parser, _ = staged.make_parser()
            ctx = unittest.mock.Mock()
            ctx.args = parser.parse_args(['uninstall', '-y', '--keep-sessions'])
            with patch.object(staged, 'CONFIG_DIR', config), patch.object(staged, 'HOME', tmp), \
                    patch.object(staged, 'zsh_rc', return_value=home / '.zshrc'), \
                    patch.object(staged, 'uninstall_sessions', return_value=[]), \
                    patch.object(staged, 'npm_uninstall_command', return_value=['npm', 'uninstall']), \
                    patch.object(staged.shutil, 'which', return_value=None), \
                    patch.object(staged.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
                staged.uninstall(ctx)
                run.assert_called_once_with(['npm', 'uninstall'])
            self.assertFalse((config / 'state.json').exists())


if __name__ == '__main__':
    unittest.main()
