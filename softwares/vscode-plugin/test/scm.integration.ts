import * as vscode from 'vscode';
import * as assert from 'node:assert/strict';
import * as path from 'node:path';
import { access, writeFile, readFile, rename, mkdir, rm, chmod } from 'node:fs/promises';
import { Viewer, TextDocuments } from '../src/extension';
import { runFile } from '../src/git';
import { digest } from '../src/ledger';

export async function run(): Promise<void> {
  const root = process.env.BAC_SCM_ROOT!;
  const output = process.env.BAC_TEST_OUTPUT!;
  const extension = await vscode.extensions.getExtension('vscode.git')!.activate();
  const api = extension.getAPI(1);
  for (let i = 0; !api.repositories.length && i < 100; i++) await new Promise(resolve => setTimeout(resolve, 100));
  let repository = api.repositories.find((repo: { rootUri: vscode.Uri }) => repo.rootUri.fsPath === root);
  assert.ok(repository, 'Isolated Git repository must be detected.');
  await repository.status();
  const uri = vscode.Uri.file(path.join(root, 'docs/contribution.bac'));
  await vscode.commands.executeCommand('workbench.action.closeAllEditors');
  await vscode.commands.executeCommand('vscode.openWith', uri, 'bac.viewer', { preview: false });
  await vscode.commands.executeCommand('workbench.view.scm');
  await writeFile(path.join(output, 'scm-ready'), JSON.stringify({ version: vscode.version }));
  await waitFor(path.join(output, 'click-done'));
  await vscode.commands.executeCommand('git.openChange', uri);
  await writeFile(path.join(output, 'command-done'), 'done');
  await waitFor(path.join(output, 'scm-done'));
  const gitUri = (ref: string, filename = uri.fsPath) => vscode.Uri.file(filename).with({ scheme: 'git', query: JSON.stringify({ path: filename, ref }) });
  const git = (...args: string[]) => runFile('git', args, root);
  const original = await readFile(uri.fsPath);
  const extensionUri = vscode.extensions.getExtension('bensz.bac-viewer')!.extensionUri;
  const texts = new TextDocuments();
  const viewer = new Viewer({ extensionUri } as vscode.ExtensionContext, texts);
  const panels: ReturnType<typeof mockPanel>[] = [];
  const earlyClose = mockPanel();
  const opening = viewer.resolveCustomEditor(await viewer.openCustomDocument(gitUri('HEAD')), earlyClose.panel);
  earlyClose.dispose.fire();
  await opening;
  assert.equal([...viewer['sessions']].some(session => session.panel === earlyClose.panel), false, 'Closing during initialization must not retain a session.');
  async function load(resource: vscode.Uri) {
    const panel = mockPanel(); panels.push(panel);
    const document = await viewer.openCustomDocument(resource);
    await viewer.resolveCustomEditor(document, panel.panel);
    const session = [...viewer['sessions']].at(-1)!;
    await viewer['refresh'](session);
    return { panel, session, last: () => panel.messages.at(-1)! };
  }
  // Exercise production provider methods with actual VS Code Git and CLI services.
  const worktree = await load(uri);
  const index = await load(gitUri(''));
  const baseline = await load(gitUri('~'));
  const head = await load(gitUri('HEAD'));
  assert.equal(index.last().ledger.count, 3); assert.equal(baseline.last().ledger.count, 3);
  assert.equal(worktree.last().ledger.count, 4); assert.equal(head.last().ledger.count, 2);
  await viewer.verify(index.session); await viewer.verify(head.session);
  const unchangedReport = index.session.verification;
  await repository.status();
  await new Promise(resolve => setTimeout(resolve, 400));
  assert.equal(index.session.verification, unchangedReport, 'Unrelated Git status must preserve a valid snapshot report.');
  assert.equal(index.session.verification!.checked_events, 3);
  assert.equal(index.session.verification!.ledger_digest, digest(await git('show', ':docs/contribution.bac')));
  assert.equal(head.session.verification!.checked_events, 2);
  assert.equal(index.session.verification!.resource, gitUri('').toString());
  const initialIndex = await git('show', ':docs/contribution.bac');
  const validReport = index.session.verification!;
  const verifier = path.join(output, 'test-verifier');
  const captured = path.join(output, 'snapshot.json');
  const bacSettings = vscode.workspace.getConfiguration('bacViewer');
  const originalExecutable = bacSettings.get<string>('bacExecutable', 'bac');
  try {
    await writeFile(verifier, `#!/usr/bin/env node\nconst fs = require('node:fs');\nconst snapshot = process.argv[process.argv.indexOf('--bac-file') + 1];\nfs.writeFileSync(${JSON.stringify(captured)}, JSON.stringify({ path: snapshot, mode: fs.statSync(snapshot).mode & 511 }));\nsetTimeout(() => process.stdout.write(${JSON.stringify(JSON.stringify(validReport))}), 600);\n`);
    await chmod(verifier, 0o700);
    await bacSettings.update('bacExecutable', verifier, vscode.ConfigurationTarget.Global);
    const pending = viewer.verify(index.session);
    // Attach rejection handling before the concurrent update to avoid unhandled promises.
    const rejected = assert.rejects(pending, /changed during verification/);
    await waitFor(captured);
    const snapshot = JSON.parse(await readFile(captured, 'utf8'));
    assert.equal(snapshot.mode, 0o600);
    await git('add', 'docs/contribution.bac'); await repository.status();
    await rejected;
    assert.equal(index.session.verification, undefined);
    await assert.rejects(access(snapshot.path), /ENOENT/);
    await writeFile(uri.fsPath, initialIndex); await git('add', 'docs/contribution.bac'); await writeFile(uri.fsPath, original); await repository.status();
    await viewer['refresh'](index.session);
    await writeFile(verifier, `#!/usr/bin/env node\nprocess.stdout.write(${JSON.stringify(JSON.stringify({ ...validReport, checked_events: 999 }))});\n`);
    await assert.rejects(viewer.verify(index.session), /report does not match/);
  } finally { await bacSettings.update('bacExecutable', originalExecutable, vscode.ConfigurationTarget.Global); }
  await viewer.compare(index.session, 'head-worktree');
  assert.equal(index.session.ledger!.events.length, 3, 'Comparisons must retain the viewed version for verification.');
  // Historical file navigation must open that revision rather than the worktree.
  await writeFile(path.join(root, 'src/main.ts'), 'export const value = 99;\n');
  const relatedEvent = head.session.ledger!.events.find(event => Array.isArray(event.payload.files))!;
  await viewer['openFile'](head.session, relatedEvent, 'src/main.ts', 'open');
  assert.equal(texts.provideTextDocumentContent(vscode.window.activeTextEditor!.document.uri), 'export const value = 1;\n');
  await assert.rejects(viewer['openFile'](head.session, relatedEvent, '../escape.ts', 'open'), /outside/);
  await assert.rejects(viewer.openCustomDocument(vscode.Uri.parse('https://example.invalid/file.bac')), /Only local/);
  const malformed = await load(uri.with({ scheme: 'git', query: '{' }));
  assert.equal(malformed.last().type, 'loadError');
  const mismatch = await load(gitUri('HEAD', path.join(root, 'other.bac')).with({ path: uri.path }));
  assert.equal(mismatch.last().type, 'loadError');
  const outside = await load(gitUri('HEAD', path.join(output, 'outside.bac')));
  assert.equal(outside.last().type, 'loadError');
  const unknown = await load(gitUri('f'.repeat(40)));
  assert.equal(unknown.last().type, 'loadError');
  const missing = await load(gitUri('HEAD', path.join(root, 'absent.bac')));
  assert.equal(missing.last().type, 'missing');
  const corruptFile = path.join(root, 'bad.bac');
  await writeFile(corruptFile, 'corrupt ZIP'); await git('add', 'bad.bac');
  const corrupt = await load(gitUri('', corruptFile));
  assert.equal(corrupt.last().type, 'loadError');
  await git('reset', '--', 'bad.bac'); await rm(corruptFile);
  const settings = vscode.workspace.getConfiguration('git');
  await settings.update('enabled', false, vscode.ConfigurationTarget.Workspace);
  await viewer['refresh'](index.session);
  assert.equal(index.last().type, 'loadError');
  await settings.update('enabled', true, vscode.ConfigurationTarget.Workspace);
  await waitUntil(() => api.repositories.some((repo: { rootUri: vscode.Uri }) => repo.rootUri.fsPath === root));
  repository = api.repositories.find((repo: { rootUri: vscode.Uri }) => repo.rootUri.fsPath === root);
  await repository.status();
  await viewer['refresh'](index.session);
  await viewer['refresh'](head.session);
  await viewer.verify(head.session);
  // UI checks staged SCM click, per-side verification and language/width/theme.
  await vscode.commands.executeCommand('workbench.action.closeAllEditors');
  await vscode.commands.executeCommand('workbench.view.scm');
  await step('staged', { counts: [2, 3] });
  await vscode.workspace.getConfiguration('workbench').update('colorTheme', 'Default Light Modern', vscode.ConfigurationTarget.Global);
  await step('light', { counts: [2, 3] });
  await vscode.workspace.getConfiguration('workbench').update('colorTheme', 'Default Dark Modern', vscode.ConfigurationTarget.Global);
  // Git status updates refresh only mutable sources and invalidate verification.
  const fixedDigest = head.session.ledger!.digest;
  await viewer.verify(index.session);
  await git('add', 'docs/contribution.bac'); await repository.status();
  await waitUntil(() => index.session.ledger?.events.length === 4 && !index.session.verification);
  assert.equal(head.session.ledger!.digest, fixedDigest);
  assert.equal(head.session.verification!.checked_events, 2);
  await step('index-refresh', { counts: [2, 4] });
  // Closing one view must release its listeners and leave other sessions alive.
  index.panel.dispose.fire();
  const closedMessages = index.panel.messages.length;
  await git('reset', '--', 'docs/contribution.bac'); await repository.status();
  await waitUntil(() => baseline.session.ledger?.events.length === 2);
  await new Promise(resolve => setTimeout(resolve, 300));
  assert.equal(index.panel.messages.length, closedMessages);
  assert.equal(index.session.closed, true);
  assert.ok(viewer['sessions'].has(head.session));
  // Real native diff for deletion: absent file side is represented explicitly.
  await vscode.commands.executeCommand('workbench.action.closeAllEditors');
  await rm(uri.fsPath); await repository.status();
  await vscode.commands.executeCommand('git.openChange', uri);
  await step('deleted', { counts: [2] });
  await writeFile(uri.fsPath, original);
  // Rename uses each URI's own path, including the removed old path.
  const renamed = path.join(root, 'docs/renamed.bac');
  await git('mv', 'docs/contribution.bac', 'docs/renamed.bac'); await repository.status();
  await vscode.commands.executeCommand('workbench.action.closeAllEditors');
  await vscode.commands.executeCommand('git.openChange', vscode.Uri.file(renamed));
  await step('renamed', { counts: [2, 2], names: ['contribution.bac', 'renamed.bac'] });
  await git('reset', '--', 'docs/contribution.bac', 'docs/renamed.bac');
  await rename(renamed, uri.fsPath); await repository.status();
  // Added ledger and unborn HEAD are distinct from corrupt/unavailable versions.
  const added = path.join(root, 'docs/added.bac');
  await writeFile(added, original); await git('add', 'docs/added.bac'); await repository.status();
  await vscode.commands.executeCommand('workbench.action.closeAllEditors');
  await vscode.commands.executeCommand('git.openChange', vscode.Uri.file(added));
  await step('added', { counts: [4] });
  await vscode.commands.executeCommand('workbench.action.closeAllEditors');
  await vscode.commands.executeCommand('vscode.openWith', gitUri('HEAD', added), 'bac.viewer');
  await step('absent', { counts: [], absent: true });
  await vscode.commands.executeCommand('workbench.action.closeAllEditors');
  await writeFile(corruptFile, 'corrupt ZIP'); await git('add', 'bad.bac'); await repository.status();
  await vscode.commands.executeCommand('vscode.openWith', gitUri('', corruptFile), 'bac.viewer');
  await step('corrupt', { error: true });
  const unbornRoot = path.join(root, 'unborn'); await mkdir(unbornRoot);
  await runFile('git', ['init'], unbornRoot);
  await writeFile(path.join(unbornRoot, 'new.bac'), original);
  await api.openRepository(vscode.Uri.file(unbornRoot));
  const unborn = await load(gitUri('HEAD', path.join(unbornRoot, 'new.bac')));
  assert.equal(unborn.last().type, 'missing');
  // Merge stage requests retain their exact stage; absent stages stay errors.
  const oid = (await git('rev-parse', 'HEAD:docs/contribution.bac')).toString('utf8').trim();
  const { execFileSync } = await import('node:child_process');
  execFileSync('git', ['update-index', '--index-info'], { cwd: root, input: `0 ${'0'.repeat(40)}\tdocs/contribution.bac\n100644 ${oid} 1\tdocs/contribution.bac\n100644 ${oid} 2\tdocs/contribution.bac\n` });
  await repository.status();
  const conflict = await load(gitUri(''));
  assert.equal(conflict.last().type, 'loadError');
  const stage = await load(gitUri('~2'));
  assert.equal(stage.last().ledger.count, 2); assert.equal(stage.last().source, 'Merge stage 2');
  const absentStage = await load(gitUri(':3'));
  assert.equal(absentStage.last().type, 'loadError');
  panels.forEach(panel => panel.dispose.fire());
  await writeFile(path.join(output, 'scm-result.json'), JSON.stringify({ status: 'pass', version: vscode.version, realScmClicks: true, perSideSnapshots: true, snapshotPermissionsAndCleanup: true, concurrentVerificationRejected: true, mismatchedReportRejected: true, unrelatedStatusPreservesVerification: true, mutableRefresh: true, fixedCommit: true, dispose: true, absentVsCorrupt: true, renamed: true, added: true, unborn: true, mergeStages: true, pathBoundaries: true, gitDisabled: true, narrowWindowAndThemes: true, localizedSources: true }));
  await writeFile(path.join(output, 'all-done'), 'done');

  async function step(name: string, expectations: unknown) {
    await writeFile(path.join(output, `${name}-ready`), JSON.stringify(expectations));
    await waitFor(path.join(output, `${name}-done`));
  }
}

function mockPanel() {
  const messages: any[] = [];
  const dispose = new vscode.EventEmitter<void>();
  const receive = new vscode.EventEmitter<unknown>();
  const panel = { title: '', onDidDispose: dispose.event, webview: { options: {}, html: '', cspSource: 'test', asWebviewUri: (uri: vscode.Uri) => uri, onDidReceiveMessage: receive.event, postMessage: async (message: unknown) => { messages.push(message); return true; } } } as unknown as vscode.WebviewPanel;
  return { panel, messages, dispose, receive };
}

async function waitUntil(predicate: () => boolean): Promise<void> {
  const end = Date.now() + 10_000;
  while (Date.now() < end) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('Timed out waiting for viewer state.');
}

async function waitFor(file: string): Promise<void> {
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    try { await access(file); return; } catch { await new Promise(resolve => setTimeout(resolve, 100)); }
  }
  throw new Error(`Timed out waiting for ${path.basename(file)}`);
}
