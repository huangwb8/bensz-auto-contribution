import { build } from 'esbuild';
await build({ entryPoints: ['src/extension.ts'], bundle: true, platform: 'node', format: 'cjs', target: 'node20', external: ['vscode'], outfile: 'dist/extension.js' });
await build({ entryPoints: ['test/core.test.ts', 'test/integration.ts'], bundle: true, platform: 'node', format: 'cjs', target: 'node20', external: ['vscode'], outdir: 'dist/test' });
