import { build } from 'esbuild';
await build({ entryPoints: ['src/extension.ts'], bundle: true, platform: 'node', format: 'cjs', target: 'node18', external: ['vscode'], outfile: 'dist/extension.js' });
await build({ entryPoints: ['test/core.test.ts', 'test/integration.ts', 'test/scm.integration.ts', 'test/untrusted.integration.ts'], bundle: true, platform: 'node', format: 'cjs', target: 'node18', external: ['vscode'], outdir: 'dist/test' });
