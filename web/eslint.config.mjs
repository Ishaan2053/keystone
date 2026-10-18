import { defineConfig, globalIgnores } from 'eslint/config';
import nextVitals from 'eslint-config-next/core-web-vitals';
import nextTypescript from 'eslint-config-next/typescript';
import prettier from 'eslint-config-prettier/flat';

export default defineConfig([
  ...nextVitals,
  ...nextTypescript,
  prettier,
  {
    // Existing components use effects for request resets, subscriptions, and
    // graph animation. Keep their behavior while adopting linting; migrating
    // these components to React Compiler patterns is a separate change.
    files: [
      'components/ask-box.tsx',
      'components/code-block.tsx',
      'components/graph/force-canvas.tsx',
      'components/graph/graph-canvas.tsx',
      'components/live-runs.tsx',
      'components/trace-panel.tsx',
      'components/triage-card.tsx',
      'components/welcome-dialog.tsx',
      'components/workspace.tsx',
    ],
    rules: { 'react-hooks/set-state-in-effect': 'off' },
  },
  {
    // The workspace retains graph/request snapshots across async responses.
    files: ['components/workspace.tsx'],
    rules: { 'react-hooks/refs': 'off' },
  },
  {
    // These variables represent graph Module nodes, not the CommonJS module.
    files: [
      'app/harness/graph/page.tsx',
      'components/triage-card.tsx',
      'lib/run-insights.ts',
      'tests/graph-canvas.spec.ts',
    ],
    rules: { '@next/next/no-assign-module-variable': 'off' },
  },
  {
    // The standalone render harness runs directly in Node as CommonJS.
    files: ['tests/**/*.cjs'],
    rules: { '@typescript-eslint/no-require-imports': 'off' },
  },
  {
    // Test-only HTTP payloads and browser instrumentation are intentionally dynamic.
    files: ['tests/fake-api.ts', 'tests/graph-canvas.spec.ts'],
    rules: { '@typescript-eslint/no-explicit-any': 'off' },
  },
  globalIgnores([
    '.next/**',
    'out/**',
    'build/**',
    'coverage/**',
    'playwright-report/**',
    'test-results/**',
    '.empty-run/**',
    'next-env.d.ts',
  ]),
]);
