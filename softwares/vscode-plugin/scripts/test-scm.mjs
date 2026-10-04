import { runTests, downloadAndUnzipVSCode } from '@vscode/test-electron';
import { chromium } from 'playwright';
import { mkdir, writeFile, access, readFile } from 'node:fs/promises';
import { execFileSync, spawn } from 'node:child_process';
import path from 'node:path';
import net from 'node:net';
delete process.env.ELECTRON_RUN_AS_NODE;
delete process.env.VSCODE_IPC_HOOK_CLI;
for (const key of ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']) delete process.env[key];
process.env.NO_PROXY = 'localhost,127.0.0.1';
const project = path.resolve('../..');
const stamp = new Date().toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15);
const output = path.join(project, 'tmp/img-frontend', `run-${stamp}-scm`);
const root = path.join(output, 'repository');
await mkdir(path.join(root, 'docs'), { recursive: true });
await mkdir(path.join(root, 'src'));
function git(...args) { return execFileSync('git', args, { cwd: root }); }
function bac(...args) { return execFileSync('bac', ['--root', root, ...args], { cwd: root }); }
git('init'); git('config', 'user.name', 'BAC Test'); git('config', 'user.email', 'test@example.invalid');
await writeFile(path.join(root, 'src/main.ts'), 'export const value = 1;\n');
bac('init'); bac('record', '--event-type', 'human_instruction', '--source-type', 'human', '--summary', 'HEAD fixture', '--path', 'src/main.ts');
git('add', '.'); git('commit', '-m', 'Fixture');
bac('record', '--event-type', 'ai_generation', '--source-type', 'ai', '--summary', 'Index fixture');
git('add', 'docs/contribution.bac');
bac('record', '--event-type', 'test_result', '--source-type', 'tool', '--summary', 'Working tree fixture');
const server = net.createServer();
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;
await new Promise(resolve => server.close(resolve));
const executable = process.env.BAC_VSCODE_VERSION ? await downloadAndUnzipVSCode(process.env.BAC_VSCODE_VERSION) : process.env.BAC_VSCODE_EXECUTABLE || '/Applications/Visual Studio Code.app/Contents/MacOS/Code';
const untrusted = process.env.BAC_TEST_UNTRUSTED === '1' || process.argv.includes('--untrusted');
const profile = path.join(project, 'tmp', `scm-${Date.now().toString(36)}`);
await mkdir(path.join(profile, 'User'), { recursive: true });
await writeFile(path.join(profile, 'User/settings.json'), JSON.stringify({ 'security.workspace.trust.startupPrompt': 'never', 'security.workspace.trust.untrustedFiles': 'open', 'workbench.colorTheme': 'Default Dark Modern', 'workbench.startupEditor': 'none', 'workbench.secondarySideBar.defaultVisibility': 'hidden' }));
const testOptions = { vscodeExecutablePath: executable, extensionDevelopmentPath: path.resolve('.'), extensionTestsPath: path.resolve(`dist/test/${untrusted ? 'untrusted' : 'scm'}.integration.js`), extensionTestsEnv: { ...process.env, BAC_SCM_ROOT: root, BAC_TEST_OUTPUT: output }, launchArgs: [root, '--user-data-dir', profile, '--extensions-dir', path.join(output, 'extensions'), ...(untrusted ? [] : ['--disable-workspace-trust']), '--skip-welcome', '--skip-release-notes', '--disable-updates', '--disable-telemetry', '--disable-extension', 'github.copilot-chat', '--disable-extension', 'github.copilot', `--remote-debugging-port=${port}`] };
if (untrusted) {
  // test-electron always adds --disable-workspace-trust. Launch directly so this
  // regression exercises VS Code's real restricted-workspace policy.
  await new Promise((resolve, reject) => {
    const child = spawn(executable, [...testOptions.launchArgs, `--extensionDevelopmentPath=${testOptions.extensionDevelopmentPath}`, `--extensionTestsPath=${testOptions.extensionTestsPath}`], { env: testOptions.extensionTestsEnv, stdio: 'inherit' });
    child.on('error', reject);
    child.on('exit', code => code === 0 ? resolve() : reject(new Error(`Restricted workspace test exited ${code}`)));
  });
  console.log(`Restricted workspace tests passed. Evidence: ${output}`);
  process.exit(0);
}
const running = runTests(testOptions);
let testError;
running.catch(error => { testError = error; });
async function waitFile(name) {
  const end = Date.now() + 90_000;
  while (Date.now() < end) {
    if (testError) throw testError;
    try { return await readFile(path.join(output, name), 'utf8'); } catch { await new Promise(resolve => setTimeout(resolve, 100)); }
  }
  throw new Error(`Timed out: ${name}`);
}
let browser, page;
try {
  await waitFile('scm-ready');
  browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
  page = browser.contexts().flatMap(context => context.pages()).find(page => page.url().includes('workbench'));
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: path.join(output, 'before-scm.jpg'), type: 'jpeg', quality: 85 });
  // Click the real Source Control resource row, as a user would.
  const resources = page.locator('.scm-view .monaco-list-row').filter({ hasText: 'contribution.bac' });
  await resources.last().click();
  await writeFile(path.join(output, 'click-done'), 'done');
  await waitFile('command-done');
  await page.waitForTimeout(1200);
  await page.screenshot({ path: path.join(output, 'after-scm.jpg'), type: 'jpeg', quality: 85 });
  async function viewerFrames() {
    const result = [];
    for (const frame of page.frames()) if (await frame.locator('#source').count()) result.push(frame);
    return result;
  }
  async function assertViews(expected, absent = false) {
    const deadline = Date.now() + 15_000;
    while (Date.now() < deadline) {
      const frames = await viewerFrames();
      const counts = [];
      let missing = false;
      for (const frame of frames) {
        const count = await frame.locator('#count').textContent();
        const match = /^(\d+) events$/.exec(count);
        if (match) counts.push(Number(match[1]));
        if (count === 'Version absent') missing = true;
      }
      if (frames.length && expected.every(count => counts.includes(count)) && (!absent || missing)) return frames;
      await page.waitForTimeout(100);
    }
    throw new Error(`Expected SCM counts ${JSON.stringify(expected)}, absent=${absent}`);
  }
  const initial = await assertViews([3, 4]);
  const sources = await Promise.all(initial.map(frame => frame.locator('#source').textContent()));
  if (!sources.includes('Index') || !sources.includes('Working tree')) throw new Error(`SCM sides did not render: ${JSON.stringify(sources)}`);
  await writeFile(path.join(output, 'scm-done'), 'done');
  await waitFile('staged-ready');
  await page.locator('.scm-view .monaco-list-row').filter({ hasText: 'contribution.bac' }).first().click();
  const staged = await assertViews([2, 3]);
  for (const frame of staged) {
    await frame.locator('#verify').click();
    await frame.locator('#validation').filter({ hasText: /Verification passed|Verification warnings/ }).waitFor();
    await frame.locator('#verification > summary').click();
    await frame.locator('#verification-content').filter({ hasText: 'Snapshot digest sha256:' }).waitFor();
  }
  await page.screenshot({ path: path.join(output, 'after-staged-verified.jpg'), type: 'jpeg', quality: 85 });
  const indexView = (await Promise.all(staged.map(async frame => (await frame.locator('#source').textContent()) === 'Index' ? frame : undefined))).find(Boolean);
  await indexView.getByRole('button', { name: /^AI / }).click();
  if (await indexView.locator('.event-row:not([data-source="ai"])').count()) throw new Error('Per-side source filter failed.');
  await indexView.getByRole('button', { name: /^All / }).click();
  await page.setViewportSize({ width: 1000, height: 900 });
  await page.screenshot({ path: path.join(output, 'after-narrow.jpg'), type: 'jpeg', quality: 85 });
  await indexView.locator('#language').selectOption('zh-CN');
  async function localizedViews(language, source) {
    const deadline = Date.now() + 15_000;
    while (Date.now() < deadline) {
      const frames = await viewerFrames();
      for (const frame of frames) if (await frame.locator('html').getAttribute('lang') === language && await frame.locator('#source').textContent() === source) return frame;
      await page.waitForTimeout(100);
    }
    throw new Error('Source labels were not localized.');
  }
  const chineseIndex = await localizedViews('zh-CN', '暂存区');
  await page.screenshot({ path: path.join(output, 'after-chinese-sides.jpg'), type: 'jpeg', quality: 85 });
  await chineseIndex.locator('#language').selectOption('en');
  await localizedViews('en', 'Index');
  await page.setViewportSize({ width: 1600, height: 1000 });
  await writeFile(path.join(output, 'staged-done'), 'done');
  await waitFile('light-ready');
  await assertViews([2, 3]);
  await page.waitForTimeout(200);
  await page.screenshot({ path: path.join(output, 'after-light.jpg'), type: 'jpeg', quality: 85 });
  await writeFile(path.join(output, 'light-done'), 'done');
  await waitFile('index-refresh-ready');
  const updated = await assertViews([2, 4]);
  const indexFrame = (await Promise.all(updated.map(async frame => (await frame.locator('#source').textContent()) === 'Index' ? frame : undefined))).find(Boolean);
  await indexFrame.locator('#validation').filter({ hasText: 'Not verified' }).waitFor();
  const fixedFrame = (await Promise.all(updated.map(async frame => (await frame.locator('#source').textContent()).startsWith('HEAD') ? frame : undefined))).find(Boolean);
  await fixedFrame.locator('#validation').filter({ hasText: /Verification passed|Verification warnings/ }).waitFor();
  await page.screenshot({ path: path.join(output, 'after-index-refresh.jpg'), type: 'jpeg', quality: 85 });
  await writeFile(path.join(output, 'index-refresh-done'), 'done');
  for (const name of ['deleted', 'renamed', 'added', 'absent', 'corrupt']) {
    const expectations = JSON.parse(await waitFile(`${name}-ready`));
    if (name === 'renamed') {
      await page.locator('.scm-view .monaco-list-row').filter({ hasText: 'renamed.bac' }).first().click();
      await page.waitForTimeout(500);
    }
    let frames;
    if (expectations.error) {
      const deadline = Date.now() + 15_000;
      while (Date.now() < deadline) {
        frames = await viewerFrames();
        if (frames.length) break;
        await page.waitForTimeout(100);
      }
      if (!frames?.length) throw new Error('Error-state viewer did not open.');
    } else frames = await assertViews(expectations.counts, expectations.absent);
    if (expectations.error) {
      const frame = frames.at(-1);
      await frame.locator('#count').filter({ hasText: 'Unable to read' }).waitFor();
      await frame.locator('#notice').filter({ hasText: /ZIP container/ }).waitFor();
      if (!await frame.locator('#verify').isDisabled()) throw new Error('Corrupt ledger verification was enabled.');
    }
    if (expectations.names) {
      const names = await Promise.all(frames.map(frame => frame.locator('#filename').textContent()));
      if (!expectations.names.every(name => names.includes(name))) throw new Error('Rename paths are incorrect.');
    }
    await page.screenshot({ path: path.join(output, `after-${name}.jpg`), type: 'jpeg', quality: 85 });
    await writeFile(path.join(output, `${name}-done`), 'done');
  }
  await waitFile('all-done');
  await running;
  await writeFile(path.join(output, 'scm-ui-result.json'), JSON.stringify({ status: 'pass', realWorkingTreeClick: true, realIndexClick: true, perSideVerification: true, independentFilters: true, localizedSources: true, narrow: true, themes: true, mutableRefresh: true, addedDeletedRenamed: true, absentAndErrorUI: true }));
  console.log(`SCM UI tests passed. Screenshots: ${output}`);
} catch (error) {
  if (page) await page.screenshot({ path: path.join(output, 'failure.jpg'), type: 'jpeg', quality: 85 }).catch(() => {});
  await writeFile(path.join(output, 'click-done'), 'done');
  await writeFile(path.join(output, 'scm-done'), 'done');
  for (const name of ['staged', 'light', 'index-refresh', 'deleted', 'renamed', 'added', 'absent', 'corrupt']) await writeFile(path.join(output, `${name}-done`), 'done');
  await running.catch(() => {});
  throw error;
} finally { if (browser) await browser.close(); }
