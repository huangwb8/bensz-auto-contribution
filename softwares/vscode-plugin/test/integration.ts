import * as vscode from 'vscode';
import * as assert from 'node:assert/strict';
import { writeFile, access, readFile } from 'node:fs/promises';
import * as path from 'node:path';
import { readLedger } from '../src/ledger';
import { zip } from './fixtures';

export async function run(): Promise<void> {
  const root = process.env.BAC_TEST_ROOT;
  const output = process.env.BAC_TEST_OUTPUT;
  if (!root || !output) throw new Error('Missing test environment.');
  assert.equal(vscode.workspace.getConfiguration('bacViewer').get('language'), 'en');
  const uri = vscode.Uri.file(path.join(root, 'docs', 'contribution.bac'));
  await vscode.commands.executeCommand('workbench.action.closeAllEditors');
  await vscode.commands.executeCommand('workbench.action.closePanel');
  await vscode.commands.executeCommand('workbench.action.closeAuxiliaryBar');
  // Baseline: opening the ZIP ledger in the ordinary editor is not readable.
  await vscode.commands.executeCommand('vscode.openWith', uri, 'default');
  await writeFile(path.join(output, 'baseline-ready'), 'ready');
  await waitFor(path.join(output, 'baseline-done'));
  await vscode.commands.executeCommand('workbench.action.closeAllEditors');
  await vscode.commands.executeCommand('vscode.openWith', uri, 'bac.viewer');
  assert.ok(vscode.window.tabGroups.all.some(group => group.tabs.some(tab => tab.input instanceof vscode.TabInputCustom && tab.input.viewType === 'bac.viewer')));
  await writeFile(path.join(output, 'viewer-ready'), 'ready');
  await waitFor(path.join(output, 'language-selected'));
  assert.equal(vscode.workspace.getConfiguration('bacViewer').get('language'), 'zh-CN');
  await vscode.commands.executeCommand('workbench.action.closeAllEditors');
  await vscode.commands.executeCommand('vscode.openWith', uri, 'bac.viewer');
  await writeFile(path.join(output, 'language-reopened'), 'ready');
  await waitFor(path.join(output, 'language-restored'));
  assert.equal(vscode.workspace.getConfiguration('bacViewer').get('language'), 'en');
  await waitFor(path.join(output, 'ui-done'));
  const commands = await vscode.commands.getCommands();
  for (const command of ['bacViewer.open', 'bacViewer.compare', 'bacViewer.verify']) assert.ok(commands.includes(command));
  const original = await readFile(uri.fsPath);
  const ledger = await readLedger(original);
  ledger.events.at(-1)!.payload.summary = '<img src=x onerror="window.injected=true"> 篡改测试';
  const fixture = path.join(output, 'changed.bac');
  await writeFile(fixture, zip([
    ['manifest.json', JSON.stringify(ledger.manifest)],
    ...ledger.events.map((event, index): [string, string] => [`events/${String(index + 1).padStart(12, '0')}.json`, JSON.stringify(event)]),
  ]));
  await vscode.commands.executeCommand('workbench.action.closeAllEditors');
  await vscode.commands.executeCommand('vscode.openWith', vscode.Uri.file(fixture), 'bac.viewer');
  await writeFile(path.join(output, 'tamper-ready'), 'ready');
  await waitFor(path.join(output, 'repair-request'));
  await writeFile(fixture, original);
  await waitFor(path.join(output, 'tamper-done'));
  let failed = false;
  try { await access(path.join(output, 'ui-failed')); failed = true; } catch { /* No UI failure marker. */ }
  assert.equal(failed, false, 'UI automation reported a failure.');
  await writeFile(path.join(output, 'integration-result.json'), JSON.stringify({ status: 'pass', customEditor: true, languageSwitchAndPersistence: true, commands: true, verificationFailure: true, safeText: true, refreshInvalidatesVerification: true }));
}

async function waitFor(file: string): Promise<void> {
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    try { await access(file); return; } catch { await new Promise(resolve => setTimeout(resolve, 200)); }
  }
  throw new Error(`Timed out waiting for ${path.basename(file)}`);
}
