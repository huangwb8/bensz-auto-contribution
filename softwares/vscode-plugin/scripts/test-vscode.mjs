import { runTests } from '@vscode/test-electron';
import { chromium } from 'playwright';
import { mkdir, writeFile, access } from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
delete process.env.ELECTRON_RUN_AS_NODE;
delete process.env.VSCODE_IPC_HOOK_CLI;
for (const key of ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']) delete process.env[key];
process.env.NO_PROXY = 'localhost,127.0.0.1';
const root = path.resolve('../..');
const stamp = new Date().toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15);
const output = path.join(root, 'tmp', 'img-frontend', `run-${stamp}`);
// macOS Unix-domain socket paths must fit within 103 characters.
const profile = path.join(root, 'tmp', `vs-${Date.now().toString(36)}`);
await mkdir(output, { recursive: true });
const server = net.createServer();
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;
await new Promise(resolve => server.close(resolve));
const running = runTests({
  vscodeExecutablePath: process.env.BAC_VSCODE_EXECUTABLE || '/Applications/Visual Studio Code.app/Contents/MacOS/Code',
  extensionDevelopmentPath: path.resolve('.'), extensionTestsPath: path.resolve('dist/test/integration.js'),
  extensionTestsEnv: { ...process.env, BAC_TEST_ROOT: root, BAC_TEST_OUTPUT: output },
  launchArgs: [root, '--user-data-dir', profile, '--extensions-dir', path.join(output, 'extensions'), '--skip-welcome', '--skip-release-notes', '--disable-workspace-trust', '--disable-telemetry', '--disable-updates', '--disable-extension', 'github.copilot-chat', '--disable-extension', 'github.copilot', `--remote-debugging-port=${port}`],
});
let testError;
running.catch(error => { testError = error; });
async function waitFile(name) {
  const end = Date.now() + 120_000;
  while (Date.now() < end) {
    if (testError) throw testError;
    try { await access(path.join(output, name)); return; } catch { await new Promise(resolve => setTimeout(resolve, 250)); }
  }
  throw new Error(`Timed out: ${name}`);
}
let browser;
try {
  await waitFile('baseline-ready');
  browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
  const page = browser.contexts().flatMap(context => context.pages()).find(page => page.url().includes('workbench')) || browser.contexts()[0].pages()[0];
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(output, 'before.jpg'), type: 'jpeg', quality: 85 });
  await writeFile(path.join(output, 'baseline-done'), 'done');
  await waitFile('viewer-ready');
  let frame;
  const end = Date.now() + 30_000;
  while (Date.now() < end) {
    for (const candidate of page.frames()) {
      if (await candidate.locator('#timeline-tab').count()) { frame = candidate; break; }
    }
    if (frame) break;
    await page.waitForTimeout(250);
  }
  if (!frame) throw new Error('BAC Webview frame was not found.');
  await frame.locator('.event-row').first().waitFor();
  await page.screenshot({ path: path.join(output, 'after-timeline.jpg'), type: 'jpeg', quality: 85 });
  const count = await frame.locator('.event-row').count();
  if (!count) throw new Error('No events rendered.');
  await frame.getByRole('button', { name: /^人类 / }).click();
  if (await frame.locator('.event-row:not([data-source="human"])').count()) throw new Error('Source filter failed.');
  await frame.locator('#search').fill('vscode');
  await frame.locator('.event-row').first().click();
  await frame.locator('#detail h2').filter({ hasText: /VS Code|vscode/i }).waitFor();
  await frame.locator('#search').fill('');
  await frame.getByRole('button', { name: /^全部 / }).click();
  await frame.locator('#changes-tab').click();
  await frame.locator('#compare').click();
  await frame.locator('#compare-summary').filter({ hasText: /HEAD → 工作区 · \d+ → \d+ 条/ }).waitFor();
  await frame.locator('.event-row').first().click();
  await frame.getByRole('button', { name: '打开事件 JSON 差异' }).waitFor();
  await page.screenshot({ path: path.join(output, 'after-git.jpg'), type: 'jpeg', quality: 85 });
  await frame.getByRole('button', { name: '打开事件 JSON 差异' }).click();
  await page.locator('.monaco-diff-editor').first().waitFor();
  await page.keyboard.press(process.platform === 'darwin' ? 'Meta+w' : 'Control+w');
  await frame.locator('#baseline').selectOption('head-index');
  await frame.locator('#compare').click();
  await frame.locator('#compare-summary').filter({ hasText: /HEAD → 暂存区 · \d+ → \d+ 条/ }).waitFor();
  await frame.locator('#baseline').selectOption('index-worktree');
  await frame.locator('#compare').click();
  await frame.locator('#compare-summary').filter({ hasText: /暂存区 → 工作区 · \d+ → \d+ 条/ }).waitFor();
  await frame.locator('#verify').click();
  await frame.locator('#validation').filter({ hasText: /验证通过|验证有警告/ }).waitFor();
  await frame.locator('#timeline-tab').click();
  // Exercise native code diff on an existing recorded file without changing the repository.
  await frame.locator('#search').fill('src/');
  const fileRow = frame.locator('.event-row').first();
  await fileRow.click();
  await frame.getByRole('button', { name: 'HEAD → 工作区', exact: true }).first().waitFor();
  await page.screenshot({ path: path.join(output, 'after-files.jpg'), type: 'jpeg', quality: 85 });
  await frame.getByRole('button', { name: 'HEAD → 工作区', exact: true }).first().click();
  await page.waitForTimeout(1000);
  if (!(await page.locator('.monaco-diff-editor').count())) throw new Error('Native code diff did not open.');
  await page.screenshot({ path: path.join(output, 'after-code-diff.jpg'), type: 'jpeg', quality: 85 });
  await writeFile(path.join(output, 'ui-done'), 'done');
  await waitFile('tamper-ready');
  frame = undefined;
  const fixtureEnd = Date.now() + 15_000;
  while (Date.now() < fixtureEnd) {
    for (const candidate of page.frames()) {
      if (await candidate.locator('#filename').filter({ hasText: 'changed.bac' }).count()) { frame = candidate; break; }
    }
    if (frame) break;
    await page.waitForTimeout(100);
  }
  if (!frame) throw new Error('Tampered fixture Webview was not found.');
  await frame.locator('#detail h2').filter({ hasText: '篡改测试' }).waitFor();
  if (await frame.locator('img').count()) throw new Error('Event HTML was interpreted.');
  await frame.locator('#verify').click();
  await frame.locator('#validation').filter({ hasText: '验证失败' }).waitFor();
  await frame.locator('#verification > summary').click();
  await frame.locator('#verification-content').filter({ hasText: 'event_hash mismatch' }).waitFor();
  await writeFile(path.join(output, 'repair-request'), 'done');
  await frame.locator('#validation').filter({ hasText: '未验证' }).waitFor({ timeout: 15_000 });
  await writeFile(path.join(output, 'tamper-done'), 'done');
  await browser.close(); browser = undefined;
  await running;
  console.log(`VS Code UI tests passed. Screenshots: ${output}`);
} catch (error) {
  await writeFile(path.join(output, 'ui-failed'), String(error));
  if (browser) {
    for (const page of browser.contexts().flatMap(context => context.pages())) {
      await page.screenshot({ path: path.join(output, 'failure.jpg'), type: 'jpeg', quality: 85 }).catch(() => {});
    }
    await browser.close();
  }
  await writeFile(path.join(output, 'baseline-done'), 'done');
  await writeFile(path.join(output, 'ui-done'), 'done');
  await writeFile(path.join(output, 'repair-request'), 'done');
  await writeFile(path.join(output, 'tamper-done'), 'done');
  await running.catch(() => {});
  throw error;
}
