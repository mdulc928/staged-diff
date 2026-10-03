#!/usr/bin/env node
// npm supplies the platform-specific command shim; the engine remains pure Python.
const { spawnSync } = require('node:child_process');
const { join } = require('node:path');
const candidates = process.platform === 'win32'
  ? [['py', '-3'], ['python3'], ['python']]
  : [['python3'], ['python']];
for (const [python, ...prefix] of candidates) {
  const probe = spawnSync(python, [...prefix, '-c', 'import sys; sys.exit(sys.version_info < (3, 8))'], { stdio: 'ignore' });
  if (probe.error || probe.status !== 0) continue;
  const result = spawnSync(python, [...prefix, join(__dirname, '..', 'staged'), ...process.argv.slice(2)], { stdio: 'inherit' });
  if (result.error) {
    console.error(`Could not start staged: ${result.error.message}`);
    process.exit(1);
  }
  process.exit(result.status === null ? 1 : result.status);
}
console.error('staged requires Python 3.8 or newer on PATH. Install Python, then retry.');
process.exit(1);
