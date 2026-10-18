const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const root = process.cwd();
const outputDir = path.join(root, '.empty-run');
const configFile = 'tsconfig.empty.json';
const hookPath = path.join(root, 'tests', 'empty-alias.cjs');
const entryPath = path.join(outputDir, 'tests', 'render-empty.js');

function cleanup() {
  fs.rmSync(outputDir, { recursive: true, force: true });
}

try {
  cleanup();
  const tscPath = require.resolve('typescript/bin/tsc', { paths: [root] });
  const compile = spawnSync(process.execPath, [tscPath, '-p', configFile], {
    cwd: root,
    stdio: 'inherit',
  });
  if (compile.status !== 0) {
    process.exit(compile.status === null ? 1 : compile.status);
  }

  const run = spawnSync(process.execPath, ['-r', hookPath, entryPath], {
    cwd: root,
    stdio: 'inherit',
  });
  process.exitCode = run.status === null ? 1 : run.status;
} finally {
  cleanup();
}
