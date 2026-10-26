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
import time
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
        self.run_cli('summarize', '-f', 'existing.txt', '-m', 'Revise greeting.', '--session-message', 'Improve greetings.')
        result = json.loads(self.file_api('rename', '-f', 'existing.txt', '--to', 'nested/renamed.txt', '--json').stdout)
        self.assertEqual(result['operation'], 'rename')
        self.assertFalse(path.exists())
        self.assertTrue((self.repo / 'existing.txt').exists())
        self.assertEqual(json.loads((self.root / 'session-a/renames.json').read_text()), {'nested/renamed.txt': 'existing.txt'})
        summaries = json.loads(self.run_cli('--summary', '--json').stdout)
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
        self.run_cli('summarize', '-f', 'existing.txt', '-m', 'Change greeting.', '--session-message', 'Two changes.')
        result = json.loads(self.file_api('delete', '-f', 'existing.txt', '--json').stdout)
        self.assertEqual(result, {'operation': 'delete', 'path': 'existing.txt', 'mode': 'discard'})
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')
        self.assertFalse((self.root / 'session-a/staging/existing.txt').exists())
        data = json.loads(self.run_cli('--summary', '--json').stdout)
        self.assertEqual(data, {'session': 'Two changes.', 'files': {}})
        self.file_api('delete', '-f', 'keep.txt')
        self.assertEqual(json.loads(self.run_cli('--summary', '--json').stdout)['session'], '')
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
                              '--session-message', 'Improve greetings.', '--json')
        data = json.loads(result.stdout)
        self.assertEqual(data['session'], 'Improve greetings.')
        self.assertEqual(data['files'], {'existing.txt': 'Revise the greeting.', 'other.txt': 'Another change.'})
        self.assertEqual(data['extra'], 1)
        self.assertEqual(json.loads(self.run_cli('--summary', '--json').stdout), data)
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
        self.assertEqual(json.loads(self.run_cli('--summary', '--json').stdout)['session'], 'Improve café output.')
        for rel in ('existing', 'existing.txt', '../escape', '/tmp/escape', '.git/config', 'nested//existing.txt'):
            self.run_cli('summarize', '-f', rel, '-m', 'Invalid.', ok=False)
        self.run_cli('summarize', '--session-message', 'Invalid.', ok=False)
        self.run_cli('summarize', '-f', 'nested/existing.txt', '--session-message', 'Invalid.', ok=False)
        self.run_cli('summarize', '-m', 'Invalid.', '--clear', ok=False)
        self.assertEqual(json.loads(self.run_cli('--summary', '--json').stdout)['files'], {})

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
        self.assertIn('Improve greetings. Keep them concise.', output)
        self.assertIn('(no summary yet)', output)
        self.assertIn('\n\n', output)
        self.assertIn('Improve greetings.', self.run_cli('-R').stdout)
        self.assertEqual(set(self.run_cli('--complete-sessions').stdout.splitlines()), {'legacy', 'empty'})
        self.assertFalse((self.root / 'empty/summaries.json').exists())

    def test_missing_workspace_stays_listed_and_cleanable(self):
        gone = self.base / 'gone-workspace'
        gone.mkdir()
        self.run_cli('init', 'orphan', cwd=gone)
        self.run_cli('create', '-f', 'note.txt', '--text', 'keep me\n', cwd=gone)
        bound = dict(self.env, STAGED_SHELL_ID='orphan-shell')
        self.run_cli('use', '--session', 'orphan', cwd=gone, env=bound)
        other = self.base / 'other-workspace'
        other.mkdir()
        self.run_cli('init', 'foreign', cwd=other)
        self.init('kept')
        shutil.rmtree(gone)

        listed = self.run_cli('-R')
        self.assertIn('kept', listed.stdout)
        self.assertIn('orphan', listed.stdout)
        self.assertIn('foreign', listed.stdout)
        self.assertIn(str(gone.resolve()) + ' (workspace not found)', listed.stdout)
        self.assertNotIn('workspace not found', listed.stdout.replace(str(gone.resolve()) + ' (workspace not found)', ''))
        current = self.run_cli('--sessions')
        self.assertIn('kept', current.stdout)
        self.assertNotIn('orphan', current.stdout)
        self.assertNotIn('foreign', current.stdout)
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertNotIn('orphan', self.run_cli('--complete-sessions').stdout)

        blocked = self.run_cli(env=bound, ok=False)
        self.assertIn('Session workspace does not exist: ' + str(gone.resolve()), blocked.stderr)
        self.assertIn('orphan', self.run_cli('-R', env=bound).stdout)
        refused = self.run_cli('clean', '--session', 'foreign', '-y', ok=False)
        self.assertIn('Unknown session', refused.stderr)
        self.assertTrue((self.root / 'foreign').is_dir())

        removed = self.run_cli('clean', '--session', 'orphan', '-y')
        self.assertIn('Cleaned session orphan.', removed.stdout)
        self.assertFalse((self.root / 'orphan').exists())
        self.assertTrue((self.root / 'kept').is_dir())
        self.assertNotIn('orphan', self.run_cli('-R').stdout)

    def test_uuid_session_lookup_works_across_workspaces(self):
        foreign_id = 'd967289f-8157-4bcc-9161-f1d6c6ee21ce'
        other = self.base / 'other-workspace'
        other.mkdir()
        self.run_cli('init', foreign_id, cwd=other)
        self.run_cli('create', '-f', 'note.txt', '--text', 'from other\n', cwd=other)
        self.init('kept')

        self.run_cli('migrate', '--all', '--from', foreign_id, '--to', 'kept')
        self.assertEqual((self.root / 'kept/staging/note.txt').read_text(), 'from other\n')
        self.assertTrue((self.root / foreign_id).exists())

        other_id = 'cde71c03-78e7-45d0-9c5a-f909a30d0093'
        self.run_cli('init', other_id, cwd=other)
        removed = self.run_cli('clean', '--session', other_id, '-y')
        self.assertIn('Cleaned session ' + other_id + '.', removed.stdout)
        self.assertFalse((self.root / other_id).exists())

        self.run_cli('clean', '--session', foreign_id, '-y')
        self.assertFalse((self.root / foreign_id).exists())

    def test_migrate_without_to_inits_destination_session(self):
        foreign_id = 'aef557c7-1bf3-4245-b1c9-2ea804ffd439'
        other = self.base / 'other-workspace'
        other.mkdir()
        self.run_cli('init', foreign_id, cwd=other)
        self.run_cli('create', '-f', 'note.txt', '--text', 'migrate me\n', cwd=other)

        result = self.run_cli('migrate', '--all', '--from', foreign_id)
        self.assertIn('Migrated 1 change(s) from {} to '.format(foreign_id), result.stdout)
        dest_id = result.stdout.strip().rsplit(' to ', 1)[-1].rstrip('.')
        self.assertTrue((self.root / dest_id / 'staging/note.txt').exists())
        self.assertEqual((self.root / dest_id / '.workspace').read_text().strip(), str(self.repo.resolve()))

    def test_migrate_without_to_uses_active_workspace_session(self):
        foreign_id = '354adec6-dcdd-46ac-84f9-67ee18f14bb8'
        other = self.base / 'other-workspace'
        other.mkdir()
        self.run_cli('init', foreign_id, cwd=other)
        self.run_cli('create', '-f', 'note.txt', '--text', 'from other\n', cwd=other)
        self.init('kept')

        result = self.run_cli('migrate', '--all', '--from', foreign_id)
        self.assertIn(' to kept.', result.stdout)
        self.assertEqual((self.root / 'kept/staging/note.txt').read_text(), 'from other\n')

    def test_verbose_diff_overview_and_selected_review(self):
        self.propose()
        self.propose(rel='other.txt')
        self.run_cli('summarize', '-f', 'existing.txt', '-m', 'Revise the greeting.',
                     '--session-message', 'Improve greetings.')
        for flags in [('diff', '-v'), ('diff', '--verbose'), ('-v', 'diff')]:
            output = self.run_cli(*flags).stdout
            self.assertIn('Improve greetings.', output)
            self.assertIn('Revise the greeting.', output)
            self.assertIn('(no summary yet)', output)
            self.assertLess(output.index('Improve greetings.'), output.index('Revise the greeting.'))
            self.assertIn('\n\n', output)
            self.assertIn('Workspace:', output)
        self.assertNotIn('Revise the greeting.', self.run_cli('diff').stdout)
        output = self.run_cli('diff', '-v', '-f', 'existing.txt').stdout
        self.assertLess(output.index('Improve greetings.'), output.index('Revise the greeting.'))
        self.assertLess(output.index('Revise the greeting.'), output.index('-original'))
        self.assertIn('+proposed', output)
        self.assertNotIn('other.txt', output)
        self.assertNotIn('Revise the greeting.', self.run_cli('diff', '-f', 'existing.txt').stdout)
        self.assertIn('Revise the greeting.', self.run_cli('diff', '--all', '-v').stdout)
        colored = self.run_cli('-v', env=dict(self.env, FORCE_COLOR='1')).stdout
        self.assertIn('\x1b[36m', colored)
        self.assertIn('Revise the greeting.', colored)

    def test_summary_supports_rename_and_deletion_and_clean(self):
        self.propose(rel='renamed.txt')
        self.manifest({'renamed.txt': 'old.txt', '_deletions': ['existing.txt']})
        for rel in ('renamed.txt', 'existing.txt'):
            self.run_cli('summarize', '-f', rel, '-m', 'Explain ' + rel,
                         '--session-message', 'Restructure files.')
        output = self.run_cli('diff', '-v', '--all').stdout
        self.assertIn('Explain renamed.txt', output)
        self.assertIn('Explain existing.txt', output)
        self.run_cli('clean', '-f', 'renamed.txt', '-y')
        data = json.loads(self.run_cli('--summary', '--json').stdout)
        self.assertEqual(data['files'], {'existing.txt': 'Explain existing.txt'})
        self.assertEqual(data['session'], 'Restructure files.')
        self.run_cli('clean', '--all', '-y')
        self.assertEqual(json.loads(self.run_cli('--summary', '--json').stdout), {'session': '', 'files': {}})
        self.assertTrue((self.repo / 'existing.txt').exists())

    def test_verbose_cross_session_diff_labels_both_summaries(self):
        for key in ('one', 'two'):
            self.propose(key=key, text=key + '\n')
            self.run_cli('summarize', '--session', key, '-f', 'existing.txt', '-m', key + ' file.',
                         '--session-message', key + ' session.')
        for flags in [('--between', 'one', 'two'), ('--session', 'one', '--compare-session', 'two')]:
            output = self.run_cli('diff', '-v', '--all', *flags).stdout
            for key in ('one', 'two'):
                self.assertIn(key + ' session.', output)
                self.assertIn('[' + key + ']', output)
                self.assertIn(key + ' file.', output)
                self.assertLess(output.index(key + ' session.'), output.index(key + ' file.'))
            self.assertIn('-one', output)
            self.assertIn('+two', output)

    def test_migrate_summary_lifecycle(self):
        self.propose('source', rel='one.txt')
        self.propose('source', rel='two.txt')
        for rel in ('one.txt', 'two.txt'):
            self.run_cli('summarize', '--session', 'source', '-f', rel, '-m', 'Explain ' + rel,
                         '--session-message', 'Both files.')
        self.run_cli('migrate', '--from', 'source', '--to', 'whole', '--all')
        self.assertEqual(json.loads(self.run_cli('--session', 'whole', '--summary', '--json').stdout),
                         json.loads(self.run_cli('--session', 'source', '--summary', '--json').stdout))
        self.run_cli('migrate', '--from', 'source', '--to', 'partial', '-f', 'one.txt')
        data = json.loads(self.run_cli('--session', 'partial', '--summary', '--json').stdout)
        self.assertEqual(data, {'session': '', 'files': {'one.txt': 'Explain one.txt'}})
        self.run_cli('summarize', '--session', 'partial', '-m', 'My selection.')
        self.run_cli('summarize', '--session', 'source', '-f', 'one.txt', '--clear')
        self.run_cli('migrate', '--from', 'source', '--to', 'partial', '-f', 'one.txt', '--force')
        self.assertEqual(json.loads(self.run_cli('--session', 'partial', '--summary', '--json').stdout),
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
        for flag in ('-m', '--message', '--stdin', '--clear', '--session-message', '--json', '--file'):
            self.assertIn(flag, options)
        self.assertNotIn('--summary', options)
        self.assertIn('--summary', self.run_cli('--complete-options').stdout.splitlines())
        self.assertIn('summaries', self.run_cli('diff', '--help').stdout)

    def test_meta_is_accepted_everywhere_file_selection_is_supported(self):
        parser = staged.make_parser()[0]
        commands = [
            ['--summary', '--meta', '-f', 'existing.txt'],
            ['read', '--meta', '-f', 'notes.md'],
            ['write', '--meta', '-f', 'notes.md', '--text', 'notes'],
            ['create', '--meta', '-f', 'notes.md'],
            ['copy', '--meta', '-f', 'notes.md', '--to', 'copy.md'],
            ['rename', '--meta', '-f', 'notes.md', '--to', 'review.md'],
            ['delete', '--meta', '-f', 'notes.md'],
            ['summarize', '--meta', '-f', 'existing.txt', '-m', 'Summary.'],
            ['diff', '--meta', '-f', 'existing.txt'],
            ['apply', '--meta', '-f', 'existing.txt'],
            ['clean', '--meta', '-f', 'existing.txt'],
            ['migrate', '--meta', '--from', 'source', '-f', 'existing.txt'],
            ['path', '--meta', '-f', 'notes.md'],
            ['open', '--meta', '-f', 'notes.md'],
        ]
        for argv in commands:
            with self.subTest(argv=argv):
                self.assertTrue(parser.parse_args(argv).meta)
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            parser.parse_args(['set', '--meta'])

    def test_summary_view_is_separate_from_writing(self):
        self.propose()
        self.propose(rel='other.txt')
        self.run_cli('summarize', ok=False)
        self.run_cli('--json', ok=False)
        self.run_cli('--summary', '--sessions', ok=False)
        self.assertFalse((self.root / 'session-a/summaries.json').exists())
        message = 'Handle empty input without raising an error. ' * 4
        self.run_cli('summarize', '-f', 'existing.txt', '-m', message,
                     '--session-message', 'Tolerate empty values across the proposal.')
        plain = self.run_cli('--summary').stdout
        self.assertLess(plain.index('Tolerate empty values'), plain.index('existing.txt'))
        self.assertLess(plain.index('existing.txt'), plain.index('Handle empty input'))
        self.assertIn('\n\n', plain)
        self.assertIn('other.txt', plain)
        self.assertIn('(no summary yet)', plain)
        self.assertNotIn('\x1b[', plain)
        colored = self.run_cli('--summary', env=dict(self.env, FORCE_COLOR='1', COLUMNS='48')).stdout
        self.assertIn('\x1b[', colored)
        body = colored[colored.index('Handle empty input'):]
        first = body.split('\n', 1)[0]
        self.assertLess(len(first), len(message.strip()))
        self.assertIn('\n\n', body)
        self.run_cli('--summary', 'existing.txt', ok=False)
        one = self.run_cli('--summary', '-f', 'existing.txt').stdout
        self.assertIn('existing.txt', one)
        self.assertNotIn('other.txt', one)
        self.assertNotIn('Tolerate empty values', one)
        selected = json.loads(self.run_cli('--summary', '-f', 'exist', '--json').stdout)
        self.assertEqual(selected['path'], 'existing.txt')
        self.assertTrue(selected['summary'].startswith('Handle empty input'))
        self.assertIn('session', selected)

    def test_summary_and_overview_separate_status_groups(self):
        (self.repo / 'second.txt').write_text('original\n')
        self.git('add', 'second.txt')
        self.git('commit', '-qm', 'add second')
        self.propose()
        self.propose(rel='second.txt', text='changed\n')
        self.propose(rel='fresh.txt', text='new\n')
        self.run_cli('summarize', '-m', 'Group related proposals.')
        plain = self.run_cli('--summary').stdout
        self.assertLess(plain.index('Group related proposals.'), plain.index('fresh.txt'))
        self.assertLess(plain.index('fresh.txt'), plain.index('existing.txt'))
        self.assertLess(plain.index('existing.txt'), plain.index('second.txt'))
        self.assertIn('[NEW FILE ] fresh.txt\n    (no summary yet)\n\n\n[MODIFIED ] existing.txt', plain)
        self.assertIn('[MODIFIED ] existing.txt\n    (no summary yet)\n\n[MODIFIED ] second.txt', plain)
        self.assertNotIn('[MODIFIED ] existing.txt\n    (no summary yet)\n\n\n[MODIFIED ] second.txt', plain)
        overview = self.run_cli().stdout
        self.assertIn('[NEW FILE ] fresh.txt\n\n  [MODIFIED ] existing.txt\n  [MODIFIED ] second.txt', overview)
        verbose = self.run_cli('-v').stdout
        self.assertIn('(no summary yet)\n    Staged:', verbose)
        self.assertNotIn('(no summary yet)\n\n    Staged:', verbose)
        self.assertRegex(verbose, r'Size: \d+ bytes; modified: .+\n\n\n  \[MODIFIED \]')

    def test_meta_file_commands_edit_session_notes_only(self):
        import hashlib
        self.propose()
        notes = 'Session notes\nwith two lines\n'
        created = json.loads(self.file_api('create', '--meta', '-f', 'staged_changes.md', '--text', notes, '--json').stdout)
        dashboard = self.root / 'session-a/staged_changes.md'
        self.assertTrue(created['meta'])
        self.assertEqual(dashboard.read_text(), notes)
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'original\n')
        self.assertEqual((self.root / 'session-a/staging/existing.txt').read_text(), 'proposed\n')
        self.assertFalse((self.root / 'session-a/staging/staged_changes.md').exists())
        read = json.loads(self.file_api('read', '--meta', '-f', 'staged_changes.md', '--json').stdout)
        self.assertEqual(read['source'], 'meta')
        self.assertEqual(read['content'], notes)
        self.file_api('write', '--meta', '-f', 'staged_changes.md', '--append', '--text', 'more\n')
        self.assertEqual(dashboard.read_text(), notes + 'more\n')
        copied = json.loads(self.file_api('copy', '--meta', '-f', 'staged_changes.md', '--to', 'copy.md', '--json').stdout)
        self.assertEqual(copied['source_root'], 'meta')
        self.assertEqual((self.root / 'session-a/copy.md').read_text(), dashboard.read_text())
        self.file_api('copy', '--meta', '-f', 'staged_changes.md', ok=False)
        self.file_api('rename', '--meta', '-f', 'copy.md', '--to', 'review.md')
        self.assertFalse((self.root / 'session-a/copy.md').exists())
        self.assertTrue((self.root / 'session-a/review.md').is_file())
        removed = json.loads(self.file_api('delete', '--meta', '-f', 'review.md', '--json').stdout)
        self.assertEqual(removed['mode'], 'meta')
        self.assertFalse((self.root / 'session-a/review.md').exists())
        managed = self.root / 'session-a/summaries.json'
        managed.write_text('{}\n')
        before = managed.read_bytes()
        for args in [('write', '--meta', '-f', 'summaries.json', '--text', 'nope'),
                     ('write', '--meta', '-f', 'Summaries.json', '--text', 'nope'),
                     ('create', '--meta', '-f', 'session.json', '--text', '{}'),
                     ('delete', '--meta', '-f', 'renames.json'),
                     ('create', '--meta', '-f', 'nested/notes.md', '--text', 'x'),
                     ('write', '--meta', '--from-workspace', '-f', 'staged_changes.md', '--text', 'x'),
                     ('read', '--meta', '--workspace', '-f', 'staged_changes.md')]:
            self.file_api(*args, ok=False)
        self.assertEqual(managed.read_bytes(), before)
        self.assertEqual(dashboard.read_text(), notes + 'more\n')
        listed = self.run_cli('--complete-meta').stdout.splitlines()
        self.assertIn('staged_changes.md', listed)
        self.assertIn('diff_viewer.md', listed)
        self.assertNotIn('existing.txt', listed)
        path = self.run_cli('path', '--meta', '-f', 'diff_viewer.md').stdout.strip()
        self.assertEqual(path, str(self.root / 'session-a/diff_viewer.md'))
        self.assertFalse((self.root / 'session-a/diff_viewer.md').exists())
        digest = hashlib.sha256(dashboard.read_bytes()).hexdigest()
        self.assertEqual(json.loads(self.file_api('read', '--meta', '-f', 'staged_changes.md', '--json').stdout)['sha256'], digest)

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
        state = json.loads((self.config / 'state.json').read_text())
        self.assertIn(str(self.home / '.gemini/antigravity/brain'), state.get('session_roots', []))
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
        self.propose(key='session-b')
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
            ([''], ['--session', '--sessions', '--all-repos', '--verbose', '--summary', '--file'], []),
            (['--summary', '-f', 'exist'], ['existing.txt'], ['session-a', 'session-b']),
            (['--summary', '--meta', '-f', 'exist'], ['existing.txt'], ['staged_changes.md']),
            (['read', '-f', 'exist'], ['existing.txt'], ['staged_changes.md', 'session.json']),
            (['read', '--meta', '-f', ''], ['session.json', 'staged_changes.md'], ['existing.txt']),
            (['write', '--meta', '-f', ''], ['staged_changes.md'], ['session.json', 'existing.txt']),
            (['write', '--meta', ''], ['--file', '--text'], ['--from-workspace']),
            (['read', '--workspace', ''], ['--file', '--length'], ['--meta']),
            (['rename', '--meta', '--to', 'staged'], ['staged_changes.md'], ['session-a', 'session.json']),
            (['diff', '--meta', '-f', 'exist'], ['existing.txt'], ['staged_changes.md']),
            (['read', ''], ['--start-line', '--end-line', '--offset', '--length', '--workspace', '--json', '--meta'], ['--text']),
            (['write', ''], ['--stdin', '--text', '--from-workspace', '--delete-count', '--expect-sha256', '--meta'], ['--workspace']),
            (['create', ''], ['--text', '--stdin', '--json', '--meta'], ['--workspace', '--to']),
            (['copy', ''], ['--workspace', '--to', '--json', '--meta'], ['--text']),
            (['rename', ''], ['--to', '--file', '--json', '--meta'], ['--workspace']),
            (['delete', ''], ['--workspace', '--file', '--json', '--meta'], ['--to']),
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



    # ---------------------------------------------------------------------------
    # New Features CLI Integration Tests
    # ---------------------------------------------------------------------------

    def test_cli_feedback_add_list_prompt_resolve_and_delete(self):
        self.propose(rel='src/app.py', text='def calc(x):\n    return x + 42\n')
        res = json.loads(self.run_cli(
            'feedback', '--add', '-f', 'src/app.py',
            '--start-line', '1', '--end-line', '2',
            '--select', 'x + 42', '-m', 'Avoid magic number 42.', '--json'
        ).stdout)
        self.assertEqual(res['id'], 'fb-1')
        self.assertEqual(res['snippet_focus'], 'x + 42')

        # Listing human output and --prompt output
        human = self.run_cli('feedback').stdout
        self.assertIn('fb-1 [OPEN] (staged L1-L2) focus: `x + 42`', human)
        prompt = self.run_cli('feedback', '--prompt').stdout
        self.assertIn('# Code Review Feedback for staged session `session-a`', prompt)
        self.assertIn('Avoid magic number 42.', prompt)

        # Managed metadata protection: write --meta -f feedback.json is rejected
        self.file_api('write', '--meta', '-f', 'feedback.json', '--text', '{}', ok=False)

        # Resolve and clear
        self.run_cli('feedback', '--resolve', 'fb-1')
        data = json.loads(self.run_cli('feedback', '--json').stdout)
        self.assertEqual(data['comments'][0]['status'], 'resolved')
        self.run_cli('feedback', '--clear')
        data_after = json.loads(self.run_cli('feedback', '--json').stdout)
        self.assertEqual(data_after['comments'], [])

    def test_cli_3way_merge_conflict_protection_and_undo(self):
        (self.repo / 'shared.py').write_text('line1\nline2\nline3\nline4\nline5\n')
        self.init('session-a')
        self.file_api('copy', '--workspace', '-f', 'shared.py')
        self.file_api('write', '-f', 'shared.py', '--text', 'line1\nline2\nline3\nline4\nline5_agent\n')

        # User edits line 1 in workspace concurrently
        (self.repo / 'shared.py').write_text('line1_user\nline2\nline3\nline4\nline5\n')

        # Apply performs a clean 3-way merge automatically!
        out = self.run_cli('apply', '-f', 'shared.py').stdout
        self.assertIn('[MERGED] shared.py', out)
        self.assertEqual(
            (self.repo / 'shared.py').read_text(),
            'line1_user\nline2\nline3\nline4\nline5_agent\n'
        )

        # Undo restores the exact pre-apply workspace state (`line1_user...line5`)
        undo_out = json.loads(self.run_cli('undo', '--json').stdout)
        self.assertEqual(undo_out['operation'], 'undo')
        self.assertEqual(undo_out['restored'], ['shared.py'])
        self.assertEqual(
            (self.repo / 'shared.py').read_text(),
            'line1_user\nline2\nline3\nline4\nline5\n'
        )

    def test_cli_fmt_check_and_exec(self):
        self.init('session-a')
        (self.repo / 'helper_mod.py').write_text('MULT = 3\n')
        self.file_api('create', '-f', 'calc_mod.py', '--text', 'import helper_mod\n\ndef run(v):\n    return v * helper_mod.MULT\n')

        # `staged check` validates Python AST syntax + imports
        chk = json.loads(self.run_cli('check', '-f', 'calc_mod.py', '--json').stdout)
        self.assertTrue(chk['ok'])

        # `staged exec` runs a test inside the ephemeral overlay sandbox
        exec_res = json.loads(self.run_cli(
            'exec', '--json', '--',
            sys.executable, '-c', 'import calc_mod; assert calc_mod.run(7) == 21; print("EXEC_PASS")'
        ).stdout)
        self.assertTrue(exec_res['ok'])
        self.assertIn('EXEC_PASS', exec_res['stdout'])
        self.assertFalse((self.repo / 'calc_mod.py').exists())

    def test_cli_sandbox_auto_link_write_and_remove(self):
        self.init('session-a')
        (self.repo / 'node_modules/react').mkdir(parents=True)
        (self.repo / 'apps/web/node_modules/vite').mkdir(parents=True)
        (self.repo / 'apps/web/tsconfig.json').write_text('{}\n')
        self.file_api('create', '-f', 'apps/web/src/App.tsx', '--text', 'export {};\n')
        staging = self.root / 'session-a/staging'

        auto = json.loads(self.run_cli('sandbox', '--auto', '--json').stdout)
        self.assertEqual(sorted(auto['changed']), ['apps/web/node_modules', 'apps/web/tsconfig.json', 'node_modules'])
        self.assertTrue((staging / 'apps/web/node_modules/vite').is_dir())

        self.run_cli('create', '--sandbox', '-f', 'apps/web/vite.config.ts', '--text', 'export default {};\n')
        self.run_cli('create', '--sandbox', '-f', 'apps/web/vite.config.ts', ok=False)
        read = json.loads(self.run_cli('read', '--sandbox', '-f', 'apps/web/vite.config.ts', '--json').stdout)
        self.assertEqual(read['source'], 'sandbox')
        self.run_cli('write', '--sandbox', '-f', 'apps/web/vite.config.ts', '--start-line', '1', '--end-line', '1',
                     '--text', 'export default { alias: true };\n', '--expect-sha256', read['sha256'])
        self.assertIn('alias', (staging / 'apps/web/vite.config.ts').read_text())
        self.run_cli('write', '--sandbox', '-f', 'apps/web/tsconfig.json', '--from-workspace', '--text', '', ok=False)
        (self.repo / 'apps/web/vitest.config.ts').write_text('export default {};\n')
        self.run_cli('write', '--sandbox', '-f', 'apps/web/vitest.config.ts', '--from-workspace', '--append', '--text', '// test\n')
        self.assertEqual((staging / 'apps/web/vitest.config.ts').read_text(), 'export default {};\n// test\n')
        self.assertEqual((self.repo / 'apps/web/vitest.config.ts').read_text(), 'export default {};\n')
        self.run_cli('link', '--sandbox', '-f', 'shared', '--target', str(self.repo / 'node_modules'))
        self.run_cli('link', '-f', 'other', ok=False)
        self.assertEqual(self.run_cli('path', '--sandbox', '-f', 'shared').stdout.strip(), str(staging / 'shared'))

        self.assertEqual(self.run_cli('--list').stdout.split(), ['apps/web/src/App.tsx'])
        self.run_cli('write', '-f', 'apps/web/tsconfig.json', '--text', 'x', ok=False)
        self.run_cli('create', '-f', 'node_modules/react/index.js', ok=False)
        self.run_cli('write', '--sandbox', '-f', 'apps/web/src/App.tsx', '--text', 'x', ok=False)
        self.run_cli('write', '--sandbox', '-f', 'node_modules/react/index.js', '--text', 'x', ok=False)
        self.run_cli('link', '--sandbox', '-f', 'apps/web/src/App.tsx', ok=False)
        self.run_cli('read', '--sandbox', '--meta', '-f', 'shared', ok=False)

        self.run_cli('delete', '--sandbox', '-f', 'shared')
        self.assertFalse(os.path.lexists(str(staging / 'shared')))
        listed = json.loads(self.run_cli('sandbox', '--json').stdout)
        self.assertEqual(len(listed['entries']), 5)
        removed = json.loads(self.run_cli('sandbox', '--clear', '--json').stdout)
        self.assertEqual(len(removed['removed']), 5)
        self.assertEqual(sorted(p.relative_to(staging).as_posix() for p in staging.rglob('*') if p.is_file()),
                         ['apps/web/src/App.tsx'])
        self.assertTrue((self.repo / 'apps/web/node_modules/vite').is_dir())
        self.assertTrue((self.repo / 'node_modules/react').is_dir())

    def test_cli_exec_staging_with_cloned_entries(self):
        self.init('session-a')
        (self.repo / 'node_modules/pkg').mkdir(parents=True)
        (self.repo / 'node_modules/pkg/index.js').write_text('ORIG\n')
        (self.repo / '.lintrc').write_text('{}\n')
        self.file_api('create', '-f', 'apps/web/a.ts', '--text', 'export {};\n')
        staging = self.root / 'session-a/staging'

        self.run_cli('sandbox', '--clone', ok=False)
        self.assertEqual(json.loads(self.run_cli('sandbox', '--auto', '--clone', '--json').stdout)['changed'], ['node_modules'])
        copied = json.loads(self.run_cli('copy', '--sandbox', '-f', '.lintrc', '--to', 'apps/web/.lintrc', '--json').stdout)
        self.assertEqual((copied['path'], copied['kind']), ('apps/web/.lintrc', 'copy'))
        self.run_cli('copy', '--sandbox', '--workspace', '-f', '.lintrc', ok=False)
        self.run_cli('exec', '--staging', '--keep-sandbox', '--', sys.executable, '-c', 'pass', ok=False)

        script = ("import os; assert os.path.isfile('.lintrc'); open('../../node_modules/pkg/index.js', 'a').write('X'); "
                  "open('out.js', 'w').write('1'); open('a.ts', 'w').write('edited')")
        result = json.loads(self.run_cli('exec', '--staging', '--cwd', 'apps/web', '--json', '--',
                                         sys.executable, '-c', script).stdout)
        self.assertTrue(result['ok'], result)
        self.assertEqual((result['reverted'], result['discarded']), (['apps/web/a.ts'], ['apps/web/out.js']))
        self.assertEqual((self.repo / 'node_modules/pkg/index.js').read_text(), 'ORIG\n')
        self.assertEqual((staging / 'apps/web/a.ts').read_text(), 'export {};\n')
        self.assertEqual(self.run_cli('--list').stdout.split(), ['apps/web/a.ts'])


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



import argparse
from contextlib import redirect_stdout, redirect_stderr
import io
import shlex
import stat


class FakeSession:
    """Minimal Session compatible with `staged` Session layout for isolated unit testing."""

    def __init__(self, root_dir, workspace_dir, session_id='test-sess'):
        self.directory = Path(root_dir) / session_id
        self.id = session_id
        self.staging = self.directory / 'staging'
        self.staging.mkdir(parents=True, exist_ok=True)
        self.workspace = Path(workspace_dir).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.directory / '.workspace').write_text(str(self.workspace) + '\n', encoding='utf-8')
        self.metadata = {'id': session_id, 'branch': 'feat/tui', 'created_at': 1700000000.0}
        self.manifest = {}
        self.deletions = []
        self._summaries = {'session': 'Add TUI viewer and feedback engine', 'files': {}}

    def summaries(self):
        return self._summaries

    def save_summaries(self, data):
        self._summaries = data

    def files(self, fresh=False):
        import os
        res = {}
        for root, _, filenames in os.walk(self.staging):
            for name in filenames:
                rel = (Path(root) / name).relative_to(self.staging).as_posix()
                res[rel] = Path(root) / name
        return dict(sorted(res.items()))

    def paths(self, fresh=False):
        return sorted(set(self.files(fresh=fresh)) | set(self.deletions) | (set(self.manifest) - {'_deletions'}))

    def original(self, rel):
        return self.workspace / self.manifest.get(rel, rel)

    def status(self, rel):
        if rel in self.deletions:
            return 'DELETED' if self.original(rel).exists() else 'APPLIED'
        staged_p = self.staging / rel
        if not staged_p.is_file():
            return 'MISSING'
        dest_p = self.workspace / rel
        if dest_p.is_file() and dest_p.read_bytes() == staged_p.read_bytes():
            return 'APPLIED'
        return 'MODIFIED' if dest_p.exists() else 'NEW FILE'

    def refresh_index(self):
        pass


class TestTuiAndFeedback(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='staged-tui-test-'))
        self.workspace = self.tmp / 'workspace'
        self.staging_root = self.tmp / 'sessions'
        self.session = FakeSession(self.staging_root, self.workspace, 'sess-1')

        # Seed workspace and staged files
        ws_py = self.workspace / 'src/example.py'
        ws_py.parent.mkdir(parents=True, exist_ok=True)
        ws_py.write_text(
            'def compute(x):\n'
            '    value = x + 1\n'
            '    return value\n'
            '\n'
            'def helper():\n'
            '    return "old"\n',
            encoding='utf-8',
        )

        st_py = self.session.staging / 'src/example.py'
        st_py.parent.mkdir(parents=True, exist_ok=True)
        st_py.write_text(
            'def compute(x):\n'
            '    # Calculate magic offset\n'
            '    value = x + 42\n'
            '    return value\n'
            '\n'
            'def helper():\n'
            '    return "new"\n',
            encoding='utf-8',
        )
        self.session._summaries['files']['src/example.py'] = 'Update compute offset and helper string.'

        st_new = self.session.staging / 'src/new_mod.py'
        st_new.write_text('CONST_VAL = 100\n', encoding='utf-8')
        self.session._summaries['files']['src/new_mod.py'] = 'Add new constants module.'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # 1. Managed Feedback Storage Tests
    # ------------------------------------------------------------------
    def test_managed_metadata_includes_feedback_json(self):
        self.assertIn('feedback.json', staged.MANAGED_METADATA)

    def test_feedback_storage_lifecycle(self):
        initial = staged.load_feedback(self.session)
        self.assertEqual(initial, {'version': 1, 'comments': []})

        c1 = staged.add_feedback_comment(
            self.session,
            file_path='src/example.py',
            comment='Use a named constant instead of magic number 42.',
            start_line=2,
            end_line=3,
            side='staged',
            snippet_focus='x + 42',
            created_at=1700000000.0,
        )
        self.assertEqual(c1['id'], 'fb-1')
        self.assertEqual(c1['status'], 'open')
        self.assertEqual(c1['snippet_focus'], 'x + 42')
        self.assertIn('value = x + 42', c1['selected_text'])

        c2 = staged.add_feedback_comment(
            self.session,
            file_path='src/example.py',
            comment='Keep old return value.',
            start_line=6,
            end_line=6,
            side='workspace',
        )
        self.assertEqual(c2['id'], 'fb-2')
        self.assertEqual(c2['side'], 'workspace')
        self.assertEqual(c2['selected_text'], '    return "old"')

        # Invalid substring focus should raise StagedError
        with self.assertRaises(staged.StagedError):
            staged.add_feedback_comment(
                self.session,
                file_path='src/example.py',
                comment='Bad focus',
                start_line=1,
                end_line=2,
                snippet_focus='non_existent_token',
            )

        # Resolve fb-1
        resolved = staged.resolve_feedback(self.session, comment_id='fb-1')
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]['status'], 'resolved')

        # Prompt with only_open=True should only include fb-2
        prompt_open = staged.format_feedback_prompt(self.session, only_open=True)
        self.assertNotIn('fb-1', prompt_open)
        self.assertIn('fb-2', prompt_open)

        # Prompt with only_open=False includes both
        prompt_all = staged.format_feedback_prompt(self.session, only_open=False)
        self.assertIn('fb-1', prompt_all)
        self.assertIn('fb-2', prompt_all)

        # Delete fb-1 and clear all
        deleted = staged.delete_feedback(self.session, comment_id='fb-1')
        self.assertEqual(len(deleted), 1)
        remaining = staged.load_feedback(self.session)['comments']
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]['id'], 'fb-2')

        staged.delete_feedback(self.session, clear_all=True)
        self.assertEqual(staged.load_feedback(self.session)['comments'], [])

    # ------------------------------------------------------------------
    # 2. `staged feedback` CLI Handler Tests
    # ------------------------------------------------------------------
    def test_cli_handle_feedback_actions_and_outputs(self):
        parser = staged.configure_feedback_parser(argparse.ArgumentParser())

        # --add
        args_add = parser.parse_args([
            '--add', '-f', 'src/example.py', '--start-line', '3', '--end-line', '4',
            '--select', 'x + 42', '-m', 'Extract 42 into MAGIC_OFFSET',
        ])
        buf = io.StringIO()
        with patch('sys.stdout', buf):
            staged.handle_feedback(None, self.session, args_add)
        self.assertIn('Added feedback fb-1 on src/example.py', buf.getvalue())

        # Human output
        args_list = parser.parse_args([])
        buf = io.StringIO()
        with patch('sys.stdout', buf):
            staged.handle_feedback(None, self.session, args_list)
        human_out = buf.getvalue()
        self.assertIn('fb-1 [OPEN] (staged L3-L4) focus: `x + 42`', human_out)
        self.assertIn('Extract 42 into MAGIC_OFFSET', human_out)

        # --json output
        args_json = parser.parse_args(['--json'])
        buf = io.StringIO()
        with patch('sys.stdout', buf):
            staged.handle_feedback(None, self.session, args_json)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(parsed['session'], 'sess-1')
        self.assertEqual(len(parsed['comments']), 1)
        self.assertEqual(parsed['comments'][0]['snippet_focus'], 'x + 42')

        # --prompt and --copy
        copied = {}

        def fake_runner(cmd, input=None, text=None, capture_output=None):
            copied['cmd'] = cmd
            copied['text'] = input
            res = argparse.Namespace(returncode=0)
            return res

        args_copy = parser.parse_args(['--prompt', '--copy'])
        buf = io.StringIO()
        with patch('sys.stdout', buf):
            staged.handle_feedback(None, self.session, args_copy, clipboard_runner=fake_runner)
        self.assertIn('# Code Review Feedback for staged session `sess-1`', copied['text'])
        self.assertIn('Copied feedback prompt to clipboard', buf.getvalue())

        # --resolve-all
        args_res = parser.parse_args(['--resolve-all', '--json'])
        buf = io.StringIO()
        with patch('sys.stdout', buf):
            staged.handle_feedback(None, self.session, args_res)
        res_json = json.loads(buf.getvalue())
        self.assertEqual(res_json['resolved'], ['fb-1'])

        # --clear
        args_clear = parser.parse_args(['--clear'])
        buf = io.StringIO()
        with patch('sys.stdout', buf):
            staged.handle_feedback(None, self.session, args_clear)
        self.assertEqual(staged.load_feedback(self.session)['comments'], [])

    # ------------------------------------------------------------------
    # 3. Syntax Highlighting, Intraline Diff & Hunk Patching Tests
    # ------------------------------------------------------------------
    def test_syntax_tokenizer_and_intraline_spans(self):
        # highlight_syntax gracefully returns spans if Pygments is installed, or [] if absent
        spans = staged.highlight_syntax('def compute(x): # comment "ignored"', filename='example.py')
        self.assertIsInstance(spans, list)
        try:
            import pygments
            self.assertTrue(len(spans) > 0)
            self.assertEqual(spans[0][2], 'keyword')
        except ImportError:
            self.assertEqual(spans, [])

        # Selectable tokens for inline comment targeting (w key)
        tokens = staged.extract_selectable_tokens('def compute(value): return "hello"')
        self.assertTrue(len(tokens) >= 3)
        self.assertIn('def', [t['text'] for t in tokens])
        self.assertIn('"hello"', [t['text'] for t in tokens])

        l_spans, r_spans = staged.compute_intraline_spans('    value = x + 1', '    value = x + 42')
        self.assertTrue(l_spans)
        self.assertTrue(r_spans)
        self.assertEqual('    value = x + 42'[r_spans[0][0]:r_spans[0][1]], '42')

    def test_selective_hunk_patching(self):
        # Verify src/example.py has 2 distinct diff hunks
        orig = staged.read_side_lines(self.session, 'src/example.py', 'workspace')
        staged_lines = staged.read_side_lines(self.session, 'src/example.py', 'staged')
        hunks = staged.compute_diff_hunks(orig, staged_lines)
        self.assertEqual(len(hunks), 2)

        # Apply only the first hunk (index 0: magic offset 42), leaving second hunk ("old" vs "new") unapplied
        staged.apply_hunk(self.session, 'src/example.py', hunk_index=0)
        updated_ws = (self.workspace / 'src/example.py').read_text(encoding='utf-8')
        self.assertIn('value = x + 42', updated_ws)
        self.assertIn('return "old"', updated_ws)

        # Now only 1 hunk remains
        orig_after = staged.read_side_lines(self.session, 'src/example.py', 'workspace')
        remaining_hunks = staged.compute_diff_hunks(orig_after, staged_lines)
        self.assertEqual(len(remaining_hunks), 1)

    # ------------------------------------------------------------------
    # 4. 2-Pane TUI State Machine & Inline Code-Selection Workflow Tests
    # ------------------------------------------------------------------
    def test_tui_two_pane_navigation_selection_and_inline_comment(self):
        copied_payload = {}

        def fake_clip(cmd, input=None, text=None, capture_output=None):
            copied_payload['text'] = input
            return argparse.Namespace(returncode=0)

        state = staged.TuiState(self.session, target_file='src/example.py', clipboard_runner=fake_clip)
        self.assertEqual(state.current_file, 'src/example.py')
        self.assertEqual(state.diff_mode, 'side-by-side')

        # Render initial 2-pane frame
        frame = state.render_frame(width=120, height=28)
        self.assertEqual(len(frame), 28)
        self.assertTrue(all(len(line) == 120 for line in frame))
        joined = '\n'.join(frame)
        self.assertIn('staged TUI', joined)
        self.assertIn('FILES (2)', joined)
        self.assertIn('Workspace Original', joined)
        self.assertIn('Staged Proposal', joined)

        # Jump to first hunk with `n`
        state.handle_key('n')
        self.assertEqual(state.cursor_row, state.hunks[0]['first_row'])

        # Move down one line into `value = x + 42` and test `w` sub-line token selection
        state.handle_key('j')
        state.handle_key('w')
        self.assertIsNotNone(state.token_index)
        # Cycle tokens with `]` and `[`
        state.handle_key(']')
        state.handle_key('[')
        selected_tok = state.token_candidates[state.token_index]['text']
        self.assertTrue(selected_tok)

        # Start visual multi-line selection with `v` and extend 1 line down
        state.handle_key('v')
        state.handle_key('j')
        side, s_line, e_line, sel_text, _ = state.current_selection_range()
        self.assertEqual(side, 'staged')
        self.assertEqual(s_line, 3)
        self.assertEqual(e_line, 4)

        # Select a token on the current line with `w` and open inline comment input with `c`
        state.handle_key('w')
        focus_tok = state.token_candidates[state.token_index]['text']
        state.handle_key('c')
        self.assertTrue(state.input_mode)

        for ch in 'Please refactor this return block':
            state.handle_key(ch)
        state.handle_key('BACKSPACE')
        state.handle_key('k')
        state.handle_key('ENTER')

        self.assertFalse(state.input_mode)
        self.assertEqual(state.comments_added_count, 1)
        self.assertEqual(state.open_comments_count(), 1)

        # Verify comment renders inline inside the diff viewport
        frame_with_comment = '\n'.join(state.render_frame(width=120, height=28))
        self.assertIn('💬 [fb-1 OPEN staged L3-L4]', frame_with_comment)
        self.assertIn('Please refactor this return bloc', frame_with_comment)

        # Yank prompt with `y`
        state.handle_key('y')
        self.assertIn('Please refactor this return block', copied_payload['text'])
        self.assertIn(focus_tok, copied_payload['text'])

        # Toggle resolve with `r`, then reopen with `r`, then delete with `x`
        state.handle_key('r')
        self.assertEqual(state.open_comments_count(), 0)
        state.handle_key('r')
        self.assertEqual(state.open_comments_count(), 1)
        state.handle_key('x')
        self.assertEqual(state.open_comments_count(), 0)

        # Toggle unified mode (`u`), sidebar (`b`), syntax (`H`), focus (`TAB`), and apply hunk (`a`)
        state.handle_key('u')
        self.assertEqual(state.diff_mode, 'unified')
        frame_unified = '\n'.join(state.render_frame(width=120, height=28))
        self.assertIn('Unified Diff', frame_unified)

        state.handle_key('b')
        self.assertFalse(state.show_sidebar)
        state.handle_key('TAB')
        self.assertTrue(state.show_sidebar)
        self.assertEqual(state.focus_pane, 'files')
        state.handle_key('ENTER')
        self.assertEqual(state.focus_pane, 'diff')

        # Jump to next hunk (hunk 2) with `n` and selectively apply it with `a`
        state.handle_key('n')
        state.handle_key('a')
        ws_text = (self.workspace / 'src/example.py').read_text(encoding='utf-8')
        self.assertIn('return "new"', ws_text)
        self.assertIn('value = x + 1', ws_text)

        # Apply entire file with `A`
        state.handle_key('A')
        ws_full = (self.workspace / 'src/example.py').read_text(encoding='utf-8')
        self.assertIn('value = x + 42', ws_full)
        self.assertIn('return "new"', ws_full)

        # Test help modal (? and h)
        state.handle_key('?')
        self.assertTrue(state.show_help)
        frame_help = '\n'.join(state.render_frame(width=120, height=28))
        self.assertIn('staged TUI Command Reference', frame_help)
        self.assertIn('NAVIGATION:', frame_help)
        self.assertIn('CODE REVIEW & INLINE FEEDBACK:', frame_help)
        # Close help with `?` or `q` without quitting TUI
        state.handle_key('?')
        self.assertFalse(state.show_help)
        state.handle_key('h')
        self.assertTrue(state.show_help)
        state.handle_key('q')
        self.assertFalse(state.show_help)
        self.assertFalse(state.should_quit)

        # Quit with `q`
        state.handle_key('q')
        self.assertTrue(state.should_quit)


class SafetyDummyContext:
    def __init__(self, workspace, staging_root, **kwargs):
        self.workspace = Path(workspace).resolve()
        self._staging_root = Path(staging_root).resolve()
        defaults = {
            'force': False,
            'yes': True,
            'no_merge': False,
            'prefer_pure_python_merge': False,
            'json': False,
            'file': None,
            'all': True,
            'clean_session': None,
        }
        defaults.update(kwargs)
        self.args = argparse.Namespace(**defaults)
        self.local = {}
        self.global_state = {}
        self.shell_path = self._staging_root / 'shell.json'
        self.shell = {}

    def staging_root(self):
        return self._staging_root

    def setting(self, key, default=None):
        return self.local.get(key, self.global_state.get(key, default))


class TestSafetyAndUndo(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix='staged-test-'))
        self.workspace = self.tmpdir / 'workspace'
        self.staging_root = self.tmpdir / 'sessions'
        self.workspace.mkdir(parents=True)
        self.staging_root.mkdir(parents=True)

        self.session_dir = self.staging_root / 'test-session'
        (self.session_dir / 'staging').mkdir(parents=True)
        staged.write_text_file(self.session_dir / '.workspace', str(self.workspace) + '\n')
        staged.save_json(self.session_dir / 'session.json', {'id': 'test-session', 'branch': None, 'created_at': 1.0})
        staged.save_json(self.session_dir / 'renames.json', {})
        self.session = staged.Session(self.session_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_base_snapshot_first_touch_only(self):
        ws_file = self.workspace / 'app.py'
        staged.write_text_file(ws_file, 'line1\nline2\n')
        os.chmod(ws_file, 0o755)

        entry1 = staged.record_base_snapshot(self.session, 'app.py')
        self.assertTrue(entry1['existed'])
        self.assertEqual(entry1['mode'], 0o755)
        orig_hash = entry1['sha256']

        # Modify workspace file later and call record_base_snapshot again without reset
        staged.write_text_file(ws_file, 'modified_in_workspace\n')
        entry2 = staged.record_base_snapshot(self.session, 'app.py')
        self.assertEqual(entry2['sha256'], orig_hash)
        self.assertEqual((self.session.directory / '.base/app.py').read_text(encoding='utf-8'), 'line1\nline2\n')

        # New file snapshot
        new_entry = staged.record_base_snapshot(self.session, 'new_file.py')
        self.assertFalse(new_entry['existed'])

        # Clean single file removes its base snapshot
        ctx = SafetyDummyContext(self.workspace, self.staging_root, file='app.py', all=False)
        staged.write_text_file(self.session.staging / 'app.py', 'staged_content\n')
        with redirect_stdout(io.StringIO()):
            staged.clean_session(ctx, self.session)
        base_meta = staged.load_base_metadata(self.session)
        self.assertNotIn('app.py', base_meta['files'])
        self.assertFalse((self.session.directory / '.base/app.py').exists())
        self.assertIn('new_file.py', base_meta['files'])

    def test_three_way_merge_git_and_difflib(self):
        base_text = 'line 1\nline 2\nline 3\nline 4\nline 5\n'
        ours_text = 'line 1 modified by user\nline 2\nline 3\nline 4\nline 5\n'
        theirs_text = 'line 1\nline 2\nline 3\nline 4\nline 5 modified by agent\n'
        expected = 'line 1 modified by user\nline 2\nline 3\nline 4\nline 5 modified by agent\n'

        # Pure python difflib merge
        merged_py, ok_py = staged.three_way_merge_difflib(ours_text, base_text, theirs_text)
        self.assertTrue(ok_py)
        self.assertEqual(merged_py, expected)

        # Overlapping conflict in pure python
        conflict_theirs = 'line 1 conflicting agent edit\nline 2\nline 3\nline 4\nline 5\n'
        _, ok_conflict = staged.three_way_merge_difflib(ours_text, base_text, conflict_theirs)
        self.assertFalse(ok_conflict)

    def test_clean_3way_merge_on_apply(self):
        ws_file = self.workspace / 'mod.py'
        base_content = 'def a():\n    return 1\n\ndef b():\n    return 2\n\ndef c():\n    return 3\n'
        staged.write_text_file(ws_file, base_content)

        # Agent seeds from workspace and edits def c()
        args_copy = argparse.Namespace(command='copy', file='mod.py', workspace=True, to=None, meta=False, json=False)
        with redirect_stdout(io.StringIO()):
            staged.file_operation(self.session, args_copy)

        staged_content = 'def a():\n    return 1\n\ndef b():\n    return 2\n\ndef c():\n    return 300\n'
        args_write = argparse.Namespace(
            command='write', file='mod.py', meta=False, from_workspace=False,
            expect_sha256=None, start_line=None, end_line=None, append=False,
            offset=None, delete_count=None, stdin=False, text=staged_content, json=False
        )
        with redirect_stdout(io.StringIO()):
            staged.write_file_range(self.session, args_write)

        # Meanwhile user edits def a() in workspace
        user_content = 'def a():\n    return 100\n\ndef b():\n    return 2\n\ndef c():\n    return 3\n'
        staged.write_text_file(ws_file, user_content)

        ctx = SafetyDummyContext(self.workspace, self.staging_root)
        buf = io.StringIO()
        with redirect_stdout(buf):
            staged.apply_changes(ctx, self.session, None)

        self.assertIn('[MERGED] mod.py', buf.getvalue())
        expected_merged = 'def a():\n    return 100\n\ndef b():\n    return 2\n\ndef c():\n    return 300\n'
        self.assertEqual(ws_file.read_text(encoding='utf-8'), expected_merged)
        self.assertEqual(self.session.status('mod.py'), 'APPLIED')

    def test_overlapping_conflict_blocks_batch_preflight(self):
        # File 1: clean
        # File 2: overlapping conflict
        f1 = self.workspace / 'f1.txt'
        f2 = self.workspace / 'f2.txt'
        staged.write_text_file(f1, 'original 1\n')
        staged.write_text_file(f2, 'original 2\n')

        for name, new_text in [('f1.txt', 'staged 1\n'), ('f2.txt', 'staged 2\n')]:
            args = argparse.Namespace(
                command='write', file=name, meta=False, from_workspace=True,
                expect_sha256=None, start_line=None, end_line=None, append=False,
                offset=None, delete_count=None, stdin=False, text=new_text, json=False
            )
            with redirect_stdout(io.StringIO()):
                staged.write_file_range(self.session, args)

        # Modify f2.txt in workspace on the same line
        staged.write_text_file(f2, 'concurrent conflicting workspace edit 2\n')

        ctx = SafetyDummyContext(self.workspace, self.staging_root, force=False)
        with self.assertRaises(staged.StagedError) as cm:
            staged.apply_changes(ctx, self.session, None)
        self.assertIn("Conflict in 'f2.txt'", str(cm.exception))

        # Verify f1.txt was NOT mutated because preflight planned all files first!
        self.assertEqual(f1.read_text(encoding='utf-8'), 'original 1\n')

    def test_deletion_conflict_and_force(self):
        ws_file = self.workspace / 'del.txt'
        staged.write_text_file(ws_file, 'initial\n')

        args_del = argparse.Namespace(command='delete', file='del.txt', workspace=True, meta=False, json=False)
        with redirect_stdout(io.StringIO()):
            staged.delete_file(self.session, args_del)

        # Modify del.txt in workspace after deletion was proposed
        staged.write_text_file(ws_file, 'modified before delete apply\n')

        ctx = SafetyDummyContext(self.workspace, self.staging_root, force=False)
        with self.assertRaises(staged.StagedError) as cm:
            staged.apply_changes(ctx, self.session, None)
        self.assertIn("Workspace file 'del.txt' was modified since staging began", str(cm.exception))
        self.assertTrue(ws_file.exists())

        # Now apply with --force
        ctx_force = SafetyDummyContext(self.workspace, self.staging_root, force=True)
        with redirect_stdout(io.StringIO()):
            staged.apply_changes(ctx_force, self.session, None)
        self.assertFalse(ws_file.exists())

    def test_backup_and_undo_modify_create_delete_rename(self):
        # 1. Existing file to modify
        f_mod = self.workspace / 'mod.txt'
        staged.write_text_file(f_mod, 'before mod\n')
        os.chmod(f_mod, 0o750)

        # 2. Existing file to delete
        f_del = self.workspace / 'del.txt'
        staged.write_text_file(f_del, 'before del\n')

        # 3. Existing file to rename
        f_ren_src = self.workspace / 'old_name.txt'
        staged.write_text_file(f_ren_src, 'before rename\n')

        # Stage modify
        with redirect_stdout(io.StringIO()):
            staged.write_file_range(self.session, argparse.Namespace(
                command='write', file='mod.txt', meta=False, from_workspace=True,
                expect_sha256=None, start_line=None, end_line=None, append=False,
                offset=None, delete_count=None, stdin=False, text='after mod\n', json=False
            ))
            # Stage new file
            staged.file_operation(self.session, argparse.Namespace(
                command='create', file='created.txt', text='brand new\n', stdin=False, meta=False, json=False
            ))
            # Stage delete
            staged.delete_file(self.session, argparse.Namespace(
                command='delete', file='del.txt', workspace=True, meta=False, json=False
            ))
            # Stage copy + rename
            staged.file_operation(self.session, argparse.Namespace(
                command='copy', file='old_name.txt', workspace=True, to=None, meta=False, json=False
            ))
            staged.file_operation(self.session, argparse.Namespace(
                command='rename', file='old_name.txt', to='new_name.txt', meta=False, json=False
            ))

        ctx = SafetyDummyContext(self.workspace, self.staging_root)
        with redirect_stdout(io.StringIO()):
            staged.apply_changes(ctx, self.session, None)

        self.assertEqual(f_mod.read_text(encoding='utf-8'), 'after mod\n')
        self.assertTrue((self.workspace / 'created.txt').is_file())
        self.assertFalse(f_del.exists())
        self.assertFalse(f_ren_src.exists())
        self.assertEqual((self.workspace / 'new_name.txt').read_text(encoding='utf-8'), 'before rename\n')

        # Now run undo_apply!
        undo_buf = io.StringIO()
        with redirect_stdout(undo_buf):
            staged.undo_apply(ctx, self.session)

        self.assertIn('[UNDONE]', undo_buf.getvalue())
        self.assertEqual(f_mod.read_text(encoding='utf-8'), 'before mod\n')
        self.assertEqual(stat.S_IMODE(f_mod.stat().st_mode), 0o750)
        self.assertFalse((self.workspace / 'created.txt').exists())
        self.assertTrue(f_del.is_file())
        self.assertEqual(f_del.read_text(encoding='utf-8'), 'before del\n')
        self.assertTrue(f_ren_src.is_file())
        self.assertEqual(f_ren_src.read_text(encoding='utf-8'), 'before rename\n')
        self.assertFalse((self.workspace / 'new_name.txt').exists())

        # Second undo raises StagedError since no backups remain
        with self.assertRaises(staged.StagedError):
            staged.undo_apply(ctx, self.session)

    def test_undo_post_apply_drift_guard(self):
        f_mod = self.workspace / 'mod.txt'
        staged.write_text_file(f_mod, 'v1\n')
        with redirect_stdout(io.StringIO()):
            staged.write_file_range(self.session, argparse.Namespace(
                command='write', file='mod.txt', meta=False, from_workspace=True,
                expect_sha256=None, start_line=None, end_line=None, append=False,
                offset=None, delete_count=None, stdin=False, text='v2\n', json=False
            ))
        ctx = SafetyDummyContext(self.workspace, self.staging_root)
        with redirect_stdout(io.StringIO()):
            staged.apply_changes(ctx, self.session, None)

        # User edits mod.txt AFTER apply
        staged.write_text_file(f_mod, 'v3 post-apply edit\n')

        # Without --yes or --force in non-interactive mode, confirm() raises StagedError
        ctx_no_yes = SafetyDummyContext(self.workspace, self.staging_root, yes=False, force=False)
        old_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO('n\n')
            with self.assertRaises(staged.StagedError):
                staged.undo_apply(ctx_no_yes, self.session)
        finally:
            sys.stdin = old_stdin
        self.assertEqual(f_mod.read_text(encoding='utf-8'), 'v3 post-apply edit\n')

        # With --force, undo succeeds and outputs JSON when requested
        ctx_force_json = SafetyDummyContext(self.workspace, self.staging_root, yes=False, force=True, json=True)
        out = io.StringIO()
        with redirect_stdout(out):
            staged.undo_apply(ctx_force_json, self.session)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload['operation'], 'undo')
        self.assertEqual(payload['restored'], ['mod.txt'])
        self.assertEqual(f_mod.read_text(encoding='utf-8'), 'v1\n')


class DummyContext:
    """Lightweight Context test double compatible with `staged.Context`."""

    def __init__(self, workspace, staging_root, local=None, **arg_overrides):
        self.workspace = Path(workspace).resolve()
        self._staging_root = Path(staging_root).resolve()
        self.local = dict(local or {})
        self.global_state = {}
        defaults = {
            'file': None,
            'all': False,
            'check': False,
            'cmd': None,
            'json': False,
            'sandbox': False,
            'sync': False,
            'keep_sandbox': False,
        }
        defaults.update(arg_overrides)
        self.args = argparse.Namespace(**defaults)

    def setting(self, key, default=None):
        return self.local.get(key, self.global_state.get(key, default))

    def staging_root(self):
        return self._staging_root


def create_test_session(workspace, staging_root, session_id='test-session'):
    """Create an isolated `staged.Session` structure on disk without touching global state."""
    workspace = Path(workspace).resolve()
    staging_root = Path(staging_root).resolve()
    session_dir = staging_root / session_id
    (session_dir / 'staging').mkdir(parents=True, exist_ok=True)
    staged.write_text_file(session_dir / '.workspace', str(workspace) + '\n')
    staged.write_text_file(
        session_dir / 'session.json',
        json.dumps({'id': session_id, 'branch': 'main', 'created_at': 1700000000.0}, indent=2) + '\n',
    )
    staged.write_text_file(session_dir / 'renames.json', '{}\n')
    return staged.Session(session_dir)


class TestDiffLineStats(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='staged-test-stats-')).resolve()
        self.workspace = self.tmp / 'workspace'
        self.staging_root = self.tmp / 'staging_root'
        self.workspace.mkdir(parents=True)
        self.staging_root.mkdir(parents=True)
        self.session = create_test_session(self.workspace, self.staging_root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_file_stats(self):
        staged.write_text_file(self.session.staging / 'new.py', 'a = 1\nb = 2\nc = 3\n')
        stats = staged.file_diff_stats(self.session, 'new.py')
        added, removed = stats
        self.assertEqual((added, removed), (3, 0))
        self.assertEqual(stats.added, 3)
        self.assertEqual(stats.removed, 0)
        self.assertFalse(stats.binary)
        self.assertEqual(stats.status, 'NEW FILE')
        self.assertEqual(stats['added'], 3)

    def test_deleted_file_stats(self):
        staged.write_text_file(self.workspace / 'old.py', 'line1\nline2\nline3\nline4\n')
        self.session.deletions = ['old.py']
        self.session.manifest['_deletions'] = ['old.py']
        self.session.save_manifest()
        stats = staged.file_diff_stats(self.session, 'old.py')
        self.assertEqual(tuple(stats), (0, 4))
        self.assertEqual(stats.status, 'DELETED')
        self.assertFalse(stats.binary)

    def test_modified_file_stats(self):
        staged.write_text_file(self.workspace / 'mod.py', 'a\nb\nc\nd\n')
        staged.write_text_file(self.session.staging / 'mod.py', 'a\nb_changed\nc\ne\nf\n')
        stats = staged.file_diff_stats(self.session, 'mod.py')
        # 'b' -> 'b_changed' (+1 -1), 'd' -> 'e', 'f' (+2 -1) => total +3 -2
        self.assertEqual(tuple(stats), (3, 2))
        self.assertEqual(stats.status, 'MODIFIED')

    def test_renamed_file_stats(self):
        staged.write_text_file(self.workspace / 'src/orig.py', 'x = 1\ny = 2\n')
        staged.write_text_file(self.session.staging / 'src/renamed.py', 'x = 1\ny = 20\nz = 30\n')
        self.session.manifest['src/renamed.py'] = 'src/orig.py'
        self.session.save_manifest()
        stats = staged.file_diff_stats(self.session, 'src/renamed.py')
        self.assertEqual(tuple(stats), (2, 1))
        self.assertEqual(stats.status, 'RENAMED')

    def test_binary_file_stats_and_formatting(self):
        bin_path = self.session.staging / 'image.bin'
        bin_path.parent.mkdir(parents=True, exist_ok=True)
        bin_path.write_bytes(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR')
        stats = staged.file_diff_stats(self.session, 'image.bin')
        self.assertEqual(tuple(stats), (0, 0))
        self.assertTrue(stats.binary)

        old_env = os.environ.get('NO_COLOR')
        os.environ['NO_COLOR'] = '1'
        try:
            self.assertEqual(staged.format_diff_stats(stats), 'binary')
            self.assertEqual(staged.format_diff_stats(staged.DiffStats(14, 3)), '+14 -3')
        finally:
            if old_env is None:
                os.environ.pop('NO_COLOR', None)
            else:
                os.environ['NO_COLOR'] = old_env

    def test_session_diff_stats_aggregation(self):
        staged.write_text_file(self.session.staging / 'a.txt', '1\n2\n')
        staged.write_text_file(self.workspace / 'b.txt', 'old1\nold2\nold3\n')
        self.session.deletions = ['b.txt']
        self.session.manifest['_deletions'] = ['b.txt']
        self.session.save_manifest()
        total = staged.session_diff_stats(self.session)
        self.assertEqual(tuple(total), (2, 3))


class TestTier1VirtualBufferFormatting(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='staged-test-fmt-')).resolve()
        self.workspace = self.tmp / 'workspace'
        self.staging_root = self.tmp / 'staging_root'
        self.workspace.mkdir(parents=True)
        self.staging_root.mkdir(parents=True)
        self.session = create_test_session(self.workspace, self.staging_root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_auto_detect_formatter_commands(self):
        bin_dir = self.workspace / 'node_modules' / '.bin'
        bin_dir.mkdir(parents=True)
        prettier_mock = bin_dir / 'prettier'
        prettier_mock.write_text('#!/bin/sh\ncat\n', encoding='utf-8')
        prettier_mock.chmod(0o755)

        cmd, uses_stdin = staged.detect_formatter_command(self.workspace, 'src/index.ts')
        self.assertTrue(uses_stdin)
        self.assertEqual(cmd, [str(prettier_mock), '--stdin-filepath', str(self.workspace / 'src/index.ts')])

        # When biome.json is present in workspace and biome binary exists, prefer biome
        staged.write_text_file(self.workspace / 'biome.json', '{"formatter": {"enabled": true}}\n')
        biome_mock = bin_dir / 'biome'
        biome_mock.write_text('#!/bin/sh\ncat\n', encoding='utf-8')
        biome_mock.chmod(0o755)

        cmd_biome, uses_stdin = staged.detect_formatter_command(self.workspace, 'src/index.ts')
        self.assertTrue(uses_stdin)
        self.assertEqual(
            cmd_biome,
            [str(biome_mock), 'format', '--stdin-file-path=' + str(self.workspace / 'src/index.ts')],
        )

    def test_virtual_stdin_filepath_formatting_and_dry_run_check(self):
        staged.write_text_file(self.workspace / '.prettierrc', '{"semi": true}\n')
        bin_dir = self.workspace / 'node_modules' / '.bin'
        bin_dir.mkdir(parents=True)
        prettier_script = bin_dir / 'prettier'
        prettier_script.write_text(
            '#!{}\n'
            'import json, os, sys\n'
            'assert os.getcwd() == {!r}, f"wrong cwd: {{os.getcwd()}}"\n'
            'assert "--stdin-filepath" in sys.argv\n'
            'fp = sys.argv[sys.argv.index("--stdin-filepath") + 1]\n'
            'assert fp == {!r}, f"wrong stdin-filepath: {{fp}}"\n'
            'cfg = json.loads(open(".prettierrc").read())\n'
            'data = sys.stdin.read()\n'
            'if cfg.get("semi"):\n'
            '    lines = [l.rstrip() + (";" if l.strip() and not l.rstrip().endswith(";") else "") for l in data.splitlines()]\n'
            '    sys.stdout.write("\\n".join(lines) + "\\n")\n'
            'else:\n'
            '    sys.stdout.write(data)\n'.format(
                sys.executable,
                str(self.workspace),
                str(self.workspace / 'src/app.js'),
            ),
            encoding='utf-8',
        )
        prettier_script.chmod(0o755)

        staged_file = self.session.staging / 'src/app.js'
        staged.write_text_file(staged_file, 'const x = 1\nconst y = 2\n')

        # 1. Run with --check (dry-run): should report would_format and NOT mutate staged_file
        ctx_check = DummyContext(self.workspace, self.staging_root, check=True, json=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            report_check = staged.format_staged(ctx_check, self.session)
        self.assertFalse(report_check['ok'])
        self.assertEqual(report_check['changed'], 1)
        self.assertEqual(report_check['would_format'], 1)
        self.assertEqual(staged_file.read_text(encoding='utf-8'), 'const x = 1\nconst y = 2\n')

        # 2. Run actual format: should atomically update staged_file and never touch workspace
        ctx_fmt = DummyContext(self.workspace, self.staging_root, check=False, json=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            report_fmt = staged.format_staged(ctx_fmt, self.session)
        self.assertTrue(report_fmt['ok'])
        self.assertEqual(report_fmt['formatted'], 1)
        self.assertEqual(staged_file.read_text(encoding='utf-8'), 'const x = 1;\nconst y = 2;\n')
        self.assertFalse((self.workspace / 'src/app.js').exists())

        # 3. Re-running format when already formatted reports unchanged
        buf = io.StringIO()
        with redirect_stdout(buf):
            report_again = staged.format_staged(ctx_fmt, self.session)
        self.assertTrue(report_again['ok'])
        self.assertEqual(report_again['unchanged'], 1)
        self.assertEqual(report_again['changed'], 0)

    def test_custom_cmd_template_with_placeholders(self):
        staged_file = self.session.staging / 'hello.txt'
        staged.write_text_file(staged_file, 'hello   world\n')
        cmd_template = (
            '{} -c "import pathlib, sys; '
            'p = pathlib.Path(sys.argv[1]); '
            'ws = sys.argv[2]; '
            'assert ws.endswith(\'hello.txt\'); '
            'p.write_text(\' \'.join(p.read_text().split()) + \'\\n\')" '
            '{{staged_file}} {{workspace_file}}'
        ).format(shlex.quote(sys.executable))

        # First test --check mode with {staged_file} placeholder (must not modify staged_file!)
        ctx_check = DummyContext(self.workspace, self.staging_root, check=True, cmd=cmd_template, json=True)
        with redirect_stdout(io.StringIO()):
            res_check = staged.format_staged(ctx_check, self.session)
        self.assertFalse(res_check['ok'])
        self.assertEqual(staged_file.read_text(encoding='utf-8'), 'hello   world\n')

        # Now format in-place
        ctx_fmt = DummyContext(self.workspace, self.staging_root, check=False, cmd=cmd_template, json=True)
        with redirect_stdout(io.StringIO()):
            res_fmt = staged.format_staged(ctx_fmt, self.session)
        self.assertTrue(res_fmt['ok'])
        self.assertEqual(staged_file.read_text(encoding='utf-8'), 'hello world\n')


class TestTier1And2CheckStaged(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='staged-test-check-')).resolve()
        self.workspace = self.tmp / 'workspace'
        self.staging_root = self.tmp / 'staging_root'
        self.workspace.mkdir(parents=True)
        self.staging_root.mkdir(parents=True)
        self.session = create_test_session(self.workspace, self.staging_root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_python_ast_syntax_check_and_deleted_import_detection(self):
        # 1. Invalid Python syntax in staging is caught with 0 external tools
        staged.write_text_file(self.session.staging / 'broken.py', 'def foo(\n    return 42\n')
        ctx = DummyContext(self.workspace, self.staging_root, file='broken.py', json=True)
        with redirect_stdout(io.StringIO()):
            report = staged.check_staged(ctx, self.session)
        self.assertFalse(report['ok'])
        self.assertEqual(report['failed'], 1)
        self.assertTrue(any('SyntaxError' in d for d in report['diagnostics']))

        # 2. Valid syntax but imports a workspace module that is staged for deletion
        staged.write_text_file(self.workspace / 'legacy_helper.py', 'VALUE = 10\n')
        self.session.deletions = ['legacy_helper.py']
        self.session.manifest['_deletions'] = ['legacy_helper.py']
        self.session.save_manifest()
        staged.write_text_file(self.session.staging / 'consumer.py', 'import legacy_helper\nx = legacy_helper.VALUE\n')

        ctx2 = DummyContext(self.workspace, self.staging_root, file='consumer.py', json=True)
        with redirect_stdout(io.StringIO()):
            report2 = staged.check_staged(ctx2, self.session)
        self.assertFalse(report2['ok'])
        self.assertTrue(any('legacy_helper' in d for d in report2['diagnostics']))

        # 3. Valid syntax with valid workspace import passes
        staged.write_text_file(self.workspace / 'good_helper.py', 'VALUE = 99\n')
        staged.write_text_file(self.session.staging / 'valid.py', 'import good_helper\nx = good_helper.VALUE\n')
        ctx3 = DummyContext(self.workspace, self.staging_root, file='valid.py', json=True)
        with redirect_stdout(io.StringIO()):
            report3 = staged.check_staged(ctx3, self.session)
        self.assertTrue(report3['ok'])

    def test_tsconfig_overlay_generation_and_path_rewriting(self):
        staged.write_text_file(
            self.workspace / 'tsconfig.json',
            json.dumps({'compilerOptions': {'strict': True, 'target': 'ES2020'}}, indent=2) + '\n',
        )
        staged.write_text_file(self.workspace / 'src/types.ts', 'export interface User { id: string; }\n')
        staged.write_text_file(self.workspace / 'src/app.ts', 'export const old = 1;\n')
        staged.write_text_file(self.session.staging / 'src/app.ts', 'import { User } from "./types";\nexport const u: User = { id: "1" };\n')

        overlay = staged.generate_tsconfig_overlay(self.session)
        self.assertTrue(overlay.is_file())
        data = json.loads(overlay.read_text(encoding='utf-8'))
        self.assertEqual(data['extends'], str(self.workspace / 'tsconfig.json'))
        self.assertTrue(data['compilerOptions']['noEmit'])
        self.assertEqual(
            data['compilerOptions']['rootDirs'],
            [str(self.session.staging), str(self.workspace)],
        )
        # Overridden workspace src/app.ts must be in exclude so tsc only compiles staging/src/app.ts
        self.assertIn((self.workspace / 'src/app.ts').as_posix(), data['exclude'])

        # Test diagnostic path rewriting
        raw_diag = '{}/src/app.ts(2,14): error TS2322: Type number is not assignable to string.'.format(
            self.session.staging
        )
        cleaned = staged.rewrite_diagnostic_paths(raw_diag, self.session)
        self.assertEqual(cleaned, 'src/app.ts(2,14): error TS2322: Type number is not assignable to string.')

    def test_c_include_overlay_command_and_live_clang_check(self):
        staged.write_text_file(self.workspace / 'include/config.h', '#define OLD_CONST 1\n')
        staged.write_text_file(self.workspace / 'include/utils.h', '#define UTIL_ADD(a, b) ((a) + (b))\n')
        staged.write_text_file(self.session.staging / 'include/config.h', '#define NEW_CONST 42\n')
        staged.write_text_file(
            self.session.staging / 'src/main.c',
            '#include "include/config.h"\n#include "include/utils.h"\nint main(void) { return UTIL_ADD(NEW_CONST, 1); }\n',
        )

        cmd = staged.build_c_check_command(self.session, 'src/main.c', compiler='clang')
        staging_inc_idx = cmd.index('-I' + str(self.session.staging))
        ws_inc_idx = cmd.index('-I' + str(self.workspace))
        self.assertLess(staging_inc_idx, ws_inc_idx)

        if shutil.which('clang') or shutil.which('gcc'):
            ctx = DummyContext(self.workspace, self.staging_root, file='src/main.c', json=True)
            with redirect_stdout(io.StringIO()):
                report = staged.check_staged(ctx, self.session)
            self.assertTrue(report['ok'], f"C overlay check failed: {report}")

    def test_java_sourcepath_overlay_command_builder(self):
        staged.write_text_file(self.workspace / 'pkg/Helper.java', 'package pkg;\npublic class Helper { public static int val() { return 7; } }\n')
        staged.write_text_file(self.session.staging / 'pkg/Main.java', 'package pkg;\npublic class Main { public static int run() { return Helper.val(); } }\n')
        tmp_out = self.session.directory / '.review/javac-out'
        cmd = staged.build_java_check_command(self.session, ['pkg/Main.java'], tmp_out, javac_bin='/usr/bin/javac')
        expected_sp = os.pathsep.join([str(self.session.staging), str(self.workspace)])
        self.assertIn('-sourcepath', cmd)
        self.assertEqual(cmd[cmd.index('-sourcepath') + 1], expected_sp)


class TestTier3OverlaySandboxExec(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='staged-test-exec-')).resolve()
        self.workspace = self.tmp / 'workspace'
        self.staging_root = self.tmp / 'staging_root'
        self.workspace.mkdir(parents=True)
        self.staging_root.mkdir(parents=True)
        self.session = create_test_session(self.workspace, self.staging_root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sandbox_copies_symlinks_deletions_renames_and_cleanup(self):
        nm_pkg = self.workspace / 'node_modules' / 'my-pkg' / 'index.js'
        staged.write_text_file(nm_pkg, 'module.exports = 123;\n')

        ws_math = self.workspace / 'lib' / 'math_utils.py'
        staged.write_text_file(ws_math, 'def add(a, b):\n    return a + b\n')

        ws_obsolete = self.workspace / 'lib' / 'obsolete.py'
        staged.write_text_file(ws_obsolete, 'OBSOLETE = True\n')
        self.session.deletions = ['lib/obsolete.py']
        self.session.manifest['_deletions'] = ['lib/obsolete.py']

        ws_old = self.workspace / 'lib' / 'old_name.py'
        staged.write_text_file(ws_old, 'FACTOR = 2\n')
        st_new = self.session.staging / 'lib' / 'new_name.py'
        staged.write_text_file(st_new, 'FACTOR = 10\n')
        self.session.manifest['lib/new_name.py'] = 'lib/old_name.py'
        self.session.save_manifest()

        st_test = self.session.staging / 'test_calc.py'
        staged.write_text_file(
            st_test,
            'import os, pathlib\n'
            'from lib.math_utils import add\n'
            'from lib.new_name import FACTOR\n'
            'assert not pathlib.Path("lib/obsolete.py").exists(), "deleted file should not exist in sandbox"\n'
            'assert not pathlib.Path("lib/old_name.py").exists(), "renamed source should not exist in sandbox"\n'
            'assert not pathlib.Path("node_modules").is_symlink(), "node_modules should be cloned"\n'
            'assert add(3, 4) * FACTOR == 70\n'
            'print("SANDBOX_CHECK_OK:", os.path.abspath("test_calc.py"))\n',
        )

        sb_dir = staged.materialize_sandbox(self.session)
        self.assertFalse((sb_dir / 'node_modules').is_symlink())
        self.assertNotEqual(os.stat(nm_pkg).st_ino, os.stat(sb_dir / 'node_modules/my-pkg/index.js').st_ino)
        self.assertNotEqual(
            os.stat(ws_math).st_ino,
            os.stat(sb_dir / 'lib/math_utils.py').st_ino,
            'Unchanged workspace file must not share an inode with the sandbox',
        )
        self.assertEqual((sb_dir / 'lib/math_utils.py').read_bytes(), ws_math.read_bytes())
        self.assertEqual(os.stat(ws_math).st_mtime_ns, os.stat(sb_dir / 'lib/math_utils.py').st_mtime_ns)
        self.assertNotEqual(
            os.stat(st_new).st_ino,
            os.stat(sb_dir / 'lib/new_name.py').st_ino,
            'Staged file must be copied (not hardlinked) to protect staging from unintended mutation',
        )
        self.assertFalse((sb_dir / 'lib/obsolete.py').exists())
        self.assertFalse((sb_dir / 'lib/old_name.py').exists())

        ctx = DummyContext(self.workspace, self.staging_root, json=True)
        with redirect_stdout(io.StringIO()):
            res = staged.exec_in_sandbox(ctx, self.session, [sys.executable, 'test_calc.py'], as_json=True)
        self.assertTrue(res['ok'], f"Sandbox execution failed: {res}")
        self.assertIn('SANDBOX_CHECK_OK: test_calc.py', res['stdout'])
        self.assertNotIn(str(sb_dir), res['stdout'])
        self.assertFalse(sb_dir.exists())
        self.assertTrue(nm_pkg.is_file())

    def test_in_place_sandbox_writes_never_reach_the_workspace(self):
        ws_file = self.workspace / 'lib/util.txt'
        staged.write_text_file(ws_file, 'ORIGINAL\n')
        os.symlink(str(ws_file), str(self.workspace / 'abs_link.txt'))
        cmd = [
            sys.executable, '-c',
            'for p in ("lib/util.txt", "abs_link.txt"):\n'
            '    with open(p, "a") as f: f.write("SANDBOX\\n")\n',
        ]
        ctx = DummyContext(self.workspace, self.staging_root, json=True)
        with redirect_stdout(io.StringIO()):
            res = staged.exec_in_sandbox(ctx, self.session, cmd, as_json=True)
        self.assertTrue(res['ok'], res)
        self.assertEqual(ws_file.read_text(encoding='utf-8'), 'ORIGINAL\n')

    def test_writes_into_dependency_and_build_dirs_stay_in_the_sandbox(self):
        package = self.workspace / 'node_modules/pkg/index.js'
        staged.write_text_file(package, 'ORIGINAL\n')
        staged.write_text_file(self.workspace / 'dist/out.js', 'ORIGINAL\n')
        (self.workspace / 'apps/web/node_modules').mkdir(parents=True)
        os.symlink('../../../node_modules/pkg', str(self.workspace / 'apps/web/node_modules/pkg'))
        cmd = [
            sys.executable, '-c',
            'for p in ("node_modules/pkg/index.js", "dist/out.js", "apps/web/node_modules/pkg/index.js"):\n'
            '    with open(p, "a") as f: f.write("SANDBOX\\n")\n'
            'print(open("node_modules/pkg/index.js").read().count("SANDBOX"))\n',
        ]
        ctx = DummyContext(self.workspace, self.staging_root, json=True)
        with redirect_stdout(io.StringIO()):
            res = staged.exec_in_sandbox(ctx, self.session, cmd, as_json=True)
        self.assertTrue(res['ok'], res)
        self.assertEqual(res['stdout'].strip(), '2', 'relative links must resolve inside the sandbox clone')
        self.assertEqual(package.read_text(encoding='utf-8'), 'ORIGINAL\n')
        self.assertEqual((self.workspace / 'dist/out.js').read_text(encoding='utf-8'), 'ORIGINAL\n')
        self.assertFalse((self.session.directory / '.sandbox').exists())

    def test_clone_tree_falls_back_to_per_file_copies(self):
        staged.write_text_file(self.workspace / 'tree/a/b.txt', 'data\n')
        os.symlink('a/b.txt', str(self.workspace / 'tree/link'))
        target = self.tmp / 'clone'
        with patch.object(staged.sys, 'platform', 'unsupported'):
            staged.clone_tree(self.workspace / 'tree', target)
        self.assertEqual((target / 'a/b.txt').read_text(encoding='utf-8'), 'data\n')
        self.assertEqual(os.readlink(str(target / 'link')), 'a/b.txt')
        self.assertNotEqual(os.stat(self.workspace / 'tree/a/b.txt').st_ino, os.stat(target / 'a/b.txt').st_ino)

    def test_discard_moves_the_sandbox_aside_immediately(self):
        sandbox = self.session.directory / '.sandbox'
        staged.write_text_file(sandbox / 'big/file.txt', 'x\n')
        with patch.object(staged, 'delete_detached') as delete:
            staged.discard_sandbox(sandbox)
        self.assertFalse(sandbox.exists())
        trash = delete.call_args[0][0]
        self.assertTrue(trash.name.startswith('.sandbox-trash-'))

        # Trash with a live deleter is left alone; trash whose deleter died is retried once.
        with patch.object(staged, 'delete_detached') as delete:
            staged.discard_sandbox(sandbox)
            delete.assert_not_called()
            old = time.time() - staged.SANDBOX_TRASH_STALE_SECONDS - 60
            os.utime(str(trash), (old, old))
            staged.discard_sandbox(sandbox)
            staged.discard_sandbox(sandbox)
        self.assertEqual([call[0][0] for call in delete.call_args_list], [trash])
        staged.rmtree_ignoring_races(trash)
        self.assertFalse(trash.exists())

    def test_clone_file_falls_back_to_a_plain_copy(self):
        source = self.workspace / 'a.txt'
        staged.write_text_file(source, 'data\n')
        target = self.tmp / 'b.txt'
        with patch.object(staged.sys, 'platform', 'unsupported'):
            staged.clone_file(source, target)
        self.assertEqual(target.read_text(encoding='utf-8'), 'data\n')
        self.assertNotEqual(os.stat(source).st_ino, os.stat(target).st_ino)

    def test_sandbox_sync_back_updates_modified_staged_files(self):
        st_file = self.session.staging / 'codegen.py'
        staged.write_text_file(st_file, 'VERSION = 1\n')

        ctx = DummyContext(self.workspace, self.staging_root, sync=True, json=True)
        cmd = [
            sys.executable,
            '-c',
            'import pathlib; p = pathlib.Path("codegen.py"); p.write_text(p.read_text().replace("1", "2"))',
        ]
        with redirect_stdout(io.StringIO()):
            res = staged.exec_in_sandbox(ctx, self.session, cmd, sync_back=True, as_json=True)

        self.assertTrue(res['ok'])
        self.assertEqual(res['synced'], ['codegen.py'])
        self.assertEqual(st_file.read_text(encoding='utf-8'), 'VERSION = 2\n')


class TestSandboxEntries(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='staged-test-sandbox-entries-')).resolve()
        self.workspace = self.tmp / 'workspace'
        self.staging_root = self.tmp / 'staging_root'
        self.workspace.mkdir(parents=True)
        self.staging_root.mkdir(parents=True)
        self.session = create_test_session(self.workspace, self.staging_root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_auto_mirrors_node_modules_and_nearest_config_outside_the_diff(self):
        types = self.workspace / 'node_modules/@types/react/index.d.ts'
        staged.write_text_file(types, 'export {};\n')
        staged.write_text_file(self.workspace / 'apps/web/node_modules/vite/index.js', '\n')
        staged.write_text_file(self.workspace / 'apps/web/tsconfig.json', '{"compilerOptions": {"jsx": "react-jsx"}}\n')
        staged.write_text_file(self.workspace / 'tsconfig.json', '{}\n')
        staged.write_text_file(self.session.staging / 'apps/web/src/App.tsx', 'export const App = () => <div />;\n')

        self.assertEqual(sorted(staged.auto_sandbox(self.session)),
                         ['apps/web/node_modules', 'apps/web/tsconfig.json', 'node_modules'])
        self.assertTrue((self.session.staging / 'node_modules/@types/react/index.d.ts').is_file())
        config = json.loads((self.session.staging / 'apps/web/tsconfig.json').read_text(encoding='utf-8'))
        self.assertEqual(config['extends'], str(self.workspace / 'apps/web/tsconfig.json'))
        self.assertEqual(config['compilerOptions']['rootDirs'], ['../..', str(self.workspace)])
        self.assertFalse((self.session.staging / 'tsconfig.json').exists(), 'only the nearest config is wrapped')

        self.assertEqual(self.session.paths(fresh=True), ['apps/web/src/App.tsx'])
        self.session.refresh_index()
        self.assertEqual(list(self.session.files()), ['apps/web/src/App.tsx'])
        overlay = json.loads(staged.generate_tsconfig_overlay(self.session).read_text(encoding='utf-8'))
        self.assertIn((self.session.staging / 'apps/web/tsconfig.json').as_posix(), overlay['exclude'])
        self.assertEqual(staged.auto_sandbox(self.session), [])

    def test_staged_config_is_used_as_is(self):
        staged.write_text_file(self.workspace / 'tsconfig.json', '{}\n')
        staged.write_text_file(self.session.staging / 'tsconfig.json', '{"proposal": true}\n')
        staged.write_text_file(self.session.staging / 'src/a.ts', 'export {};\n')
        self.assertEqual(staged.auto_sandbox(self.session), [])
        self.assertEqual(self.session.paths(fresh=True), ['src/a.ts', 'tsconfig.json'])

    def test_entries_and_proposals_never_overwrite_each_other(self):
        staged.write_text_file(self.workspace / 'lib/node_modules/pkg/index.js', '\n')
        staged.write_text_file(self.session.staging / 'lib/a.ts', 'export {};\n')
        with self.assertRaises(staged.StagedError):
            staged.add_sandbox_link(self.session, 'lib/a.ts', 'lib/node_modules')
        with self.assertRaises(staged.StagedError):
            staged.write_sandbox_file(self.session, 'lib/a.ts', b'x')
        with self.assertRaises(staged.StagedError):
            staged.add_sandbox_link(self.session, 'missing', 'does/not/exist')

        staged.add_sandbox_link(self.session, 'lib/node_modules')
        staged.write_sandbox_file(self.session, 'lib/deep/vite.config.ts', b'export default {};\n')
        staged.write_sandbox_file(self.session, 'lib/deep/vite.config.ts', b'export default { v: 2 };\n')
        for rel in ('lib/node_modules/pkg/index.js', 'lib/deep/vite.config.ts'):
            with self.assertRaises(staged.StagedError):
                staged.range_path(self.session, rel, writable=True)
        with self.assertRaises(staged.StagedError):
            staged.write_sandbox_file(self.session, 'lib/node_modules', b'x')
        with self.assertRaises(staged.StagedError):
            staged.remove_sandbox_entries(self.session, ['lib/a.ts'])

        self.assertEqual(staged.remove_sandbox_entries(self.session),
                         ['lib/deep/vite.config.ts', 'lib/node_modules'])
        self.assertFalse((self.session.staging / 'lib/deep').exists())
        self.assertTrue((self.session.staging / 'lib/a.ts').is_file())
        self.assertTrue((self.workspace / 'lib/node_modules/pkg/index.js').is_file())
        self.assertFalse((self.session.directory / staged.SANDBOX_RECORD).exists())

    def test_rejects_record_entries_that_escape_staging(self):
        staged.save_json(self.session.directory / staged.SANDBOX_RECORD, {'entries': {'../x': {'kind': 'file'}}})
        with self.assertRaises(staged.StagedError):
            self.session.files(fresh=True)

    def test_run_in_staging_discards_new_files_and_reverts_unless_synced(self):
        staged.write_text_file(self.workspace / 'node_modules/pkg/index.js', 'ORIG\n')
        staged.write_text_file(self.session.staging / 'apps/web/a.ts', 'export const a = 2;\n')
        staged.add_sandbox_copy(self.session, 'node_modules')
        staged.write_sandbox_file(self.session, 'apps/web/vite.config.ts', b'export default {};\n')
        script = ("import os; open('../../node_modules/pkg/index.js', 'a').write('X'); open('out.js', 'w').write('1'); "
                  "os.makedirs('dist/x'); open('dist/x/y.js', 'w').write('1'); open('a.ts', 'w').write('edited'); "
                  "open('vite.config.ts', 'a').write('// tool')")

        rc, _, err, changes = staged.run_in_staging(self.session, [sys.executable, '-c', script], 'apps/web')
        self.assertEqual(rc, 0, err)
        self.assertEqual(changes, {'synced': [], 'reverted': ['apps/web/a.ts'],
                                   'discarded': ['apps/web/dist/x/y.js', 'apps/web/out.js']})
        self.assertEqual((self.session.staging / 'apps/web/a.ts').read_text(), 'export const a = 2;\n')
        self.assertFalse((self.session.staging / 'apps/web/dist').exists())
        self.assertEqual((self.workspace / 'node_modules/pkg/index.js').read_text(), 'ORIG\n')
        self.assertEqual((self.session.staging / 'node_modules/pkg/index.js').read_text(), 'ORIG\nX')
        self.assertTrue((self.session.staging / 'apps/web/vite.config.ts').read_text().endswith('// tool'))

        rc, _, err, changes = staged.run_in_staging(
            self.session, [sys.executable, '-c', "open('apps/web/a.ts', 'w').write('kept')"], sync_back=True)
        self.assertEqual((rc, changes['synced']), (0, ['apps/web/a.ts']), err)
        self.assertEqual((self.session.staging / 'apps/web/a.ts').read_text(), 'kept')
        self.assertEqual(sorted(self.session.files(fresh=True)), ['apps/web/a.ts'])

    def test_copy_entries_are_isolated_clones_with_anchored_workspace_links(self):
        if not hasattr(os, 'symlink'):
            self.skipTest('symlinks unavailable')
        staged.write_text_file(self.workspace / 'packages/shared/index.ts', 'export {};\n')
        staged.write_text_file(self.workspace / 'node_modules/.pnpm/dep@1/node_modules/dep/index.js', 'ORIG\n')
        os.symlink('.pnpm/dep@1/node_modules/dep', str(self.workspace / 'node_modules/dep'))
        (self.workspace / 'node_modules/@vs').mkdir()
        os.symlink('../../packages/shared', str(self.workspace / 'node_modules/@vs/shared'))
        staged.write_text_file(self.session.staging / 'src/a.ts', 'export {};\n')

        self.assertEqual(sorted(staged.auto_sandbox(self.session, clone=True)), ['node_modules'])
        clone = self.session.staging / 'node_modules'
        self.assertFalse(clone.is_symlink())
        self.assertEqual(self.session.sandbox_entries()['node_modules']['kind'], 'copy')
        self.assertEqual(os.readlink(str(clone / 'dep')), '.pnpm/dep@1/node_modules/dep')
        self.assertEqual(os.readlink(str(clone / '@vs/shared')), str(self.workspace / 'packages/shared'))
        self.assertTrue((clone / '@vs/shared/index.ts').is_file())

        staged.write_text_file(clone / 'dep/index.js', 'EDITED\n')
        self.assertEqual((self.workspace / 'node_modules/dep/index.js').read_text(), 'ORIG\n')
        staged.range_path(self.session, 'node_modules/.pnpm/dep@1/node_modules/dep/index.js', writable=True, sandbox=True)
        staged.add_sandbox_copy(self.session, 'apps/web/.oxlintrc.json', str(self.workspace / 'node_modules/dep/index.js'))
        self.assertEqual((self.session.staging / 'apps/web/.oxlintrc.json').read_text(), 'ORIG\n')
        with self.assertRaises(staged.StagedError):
            staged.add_sandbox_copy(self.session, 'missing', 'does/not/exist')

        self.assertEqual(staged.remove_sandbox_entries(self.session), ['apps/web/.oxlintrc.json', 'node_modules'])
        self.assertFalse(clone.exists())
        self.assertEqual(sorted(self.session.files(fresh=True)), ['src/a.ts'])
        self.assertTrue((self.workspace / 'node_modules/@vs/shared/index.ts').is_file())
        self.assertEqual((self.workspace / 'node_modules/dep/index.js').read_text(), 'ORIG\n')

    def test_clean_session_removes_link_without_following_it(self):
        package = self.workspace / 'node_modules/pkg/index.js'
        staged.write_text_file(package, 'module.exports = 1;\n')
        staged.add_sandbox_link(self.session, 'node_modules')
        shutil.rmtree(self.session.directory)
        self.assertTrue(package.is_file())

if __name__ == '__main__':
    unittest.main()
