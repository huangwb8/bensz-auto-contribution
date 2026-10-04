import * as vscode from 'vscode';
import * as assert from 'node:assert/strict';
import * as path from 'node:path';
import { writeFile } from 'node:fs/promises';
import { Viewer, TextDocuments } from '../src/extension';

export async function run(): Promise<void> {
  assert.equal(vscode.workspace.isTrusted, false, 'This regression must run in a real restricted workspace.');
  const uri = vscode.Uri.file(path.join(process.env.BAC_SCM_ROOT!, 'docs/contribution.bac'));
  const viewer = new Viewer({ extensionUri: vscode.extensions.getExtension('bensz.bac-viewer')!.extensionUri } as vscode.ExtensionContext, new TextDocuments());
  const disposals: vscode.EventEmitter<void>[] = [];
  async function load(resource: vscode.Uri) {
    const messages: any[] = [];
    const dispose = new vscode.EventEmitter<void>(); disposals.push(dispose);
    const receive = new vscode.EventEmitter<unknown>();
    const panel = { title: '', onDidDispose: dispose.event, webview: { cspSource: 'test', asWebviewUri: (uri: vscode.Uri) => uri, onDidReceiveMessage: receive.event, postMessage: async (message: unknown) => { messages.push(message); return true; } } } as unknown as vscode.WebviewPanel;
    await viewer.resolveCustomEditor(await viewer.openCustomDocument(resource), panel);
    const session = [...viewer['sessions']].at(-1)!;
    await viewer['refresh'](session);
    return { session, messages };
  }
  const local = await load(uri);
  assert.equal(local.messages.at(-1).type, 'ledger');
  assert.equal(local.messages.at(-1).trusted, false);
  await assert.rejects(viewer.verify(local.session), /Trust this workspace/);
  await assert.rejects(viewer.compare(local.session, 'head-worktree'), /Trust this workspace/);
  const git = await load(uri.with({ scheme: 'git', query: JSON.stringify({ path: uri.fsPath, ref: 'HEAD' }) }));
  assert.equal(git.messages.at(-1).type, 'loadError');
  assert.match(git.messages.at(-1).message, /Trust this workspace/);
  disposals.forEach(dispose => dispose.fire());
  await writeFile(path.join(process.env.BAC_TEST_OUTPUT!, 'untrusted-result.json'), JSON.stringify({ status: 'pass', version: vscode.version, trusted: false, localViewing: true, gitBlocked: true, verificationBlocked: true, comparisonBlocked: true }));
}
