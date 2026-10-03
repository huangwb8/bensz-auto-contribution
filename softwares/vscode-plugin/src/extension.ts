import { t, setLanguage, getLanguage } from './i18n';
import * as vscode from 'vscode';
import * as path from 'node:path';
import * as os from 'node:os';
import { randomBytes } from 'node:crypto';
import { execFile } from 'node:child_process';
import { access, mkdtemp, writeFile, rm } from 'node:fs/promises';
import { compareLedgers, digest, isObject, Ledger, LedgerComparison, MAX_BAC_BYTES, readLedger, BacEvent } from './ledger';
import { inside, safeProjectPath } from './paths';

const VIEW = 'bac.viewer';
type CompareMode = 'head-worktree' | 'head-index' | 'index-worktree';
const LABELS: Record<CompareMode, string> = { 'head-worktree': 'HEAD → Working tree', 'head-index': 'HEAD → Index', 'index-worktree': 'Index → Working tree' };
interface Session {
  uri: vscode.Uri;
  panel: vscode.WebviewPanel;
  ledger?: Ledger;
  comparison?: LedgerComparison;
  mode?: CompareMode;
  verification?: Record<string, unknown>;
  generation: number;
}

export function runFile(command: string, args: string[], cwd: string, maxBuffer = MAX_BAC_BYTES): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    execFile(command, args, { cwd, encoding: 'buffer', timeout: 20_000, maxBuffer, windowsHide: true, env: { ...process.env, LC_ALL: 'C', GIT_TERMINAL_PROMPT: '0' } }, (error, stdout, stderr) => {
      if (error) {
        const detail = stderr.toString('utf8').trim();
        reject(Object.assign(new Error(detail || error.message), { code: error.code }));
      } else resolve(stdout);
    });
  });
}

class TextDocuments implements vscode.TextDocumentContentProvider {
  private counter = 0;
  private documents = new Map<string, string>();
  put(label: string, text: string): vscode.Uri {
    const uri = vscode.Uri.from({ scheme: 'bac-text', path: `/${++this.counter}/${label}` });
    this.documents.set(uri.toString(), text);
    return uri;
  }
  provideTextDocumentContent(uri: vscode.Uri): string { return this.documents.get(uri.toString()) ?? ''; }
  forget(uri: vscode.Uri): void { this.documents.delete(uri.toString()); }
}

class Viewer implements vscode.CustomReadonlyEditorProvider {
  private sessions = new Set<Session>();
  constructor(private context: vscode.ExtensionContext, private texts: TextDocuments) {}

  async openCustomDocument(uri: vscode.Uri): Promise<vscode.CustomDocument> {
    if (uri.scheme !== 'file') throw new Error(t('Open a local .bac file from Explorer. Use the BAC comparison button for Git versions.'));
    return { uri, dispose() {} };
  }

  async resolveCustomEditor(document: vscode.CustomDocument, panel: vscode.WebviewPanel): Promise<void> {
    const session: Session = { uri: document.uri, panel, generation: 0 };
    this.sessions.add(session);
    panel.webview.options = { enableScripts: true, localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, 'media')] };
    panel.webview.html = await this.html(panel.webview);
    const message = panel.webview.onDidReceiveMessage(async (value: unknown) => {
      if (!isObject(value) || typeof value.type !== 'string') return;
      try {
        if (value.type === 'language' && (value.language === 'en' || value.language === 'zh-CN')) {
          await vscode.workspace.getConfiguration('bacViewer').update('language', value.language, vscode.ConfigurationTarget.Global);
        }
        else if (value.type === 'ready' || value.type === 'refresh') await this.refresh(session);
        else if (value.type === 'compare' && ['head-worktree', 'head-index', 'index-worktree'].includes(String(value.mode))) await this.compare(session, value.mode as CompareMode);
        else if (value.type === 'verify') await this.verify(session);
        else if (value.type === 'event' && typeof value.id === 'string') {
          const event = session.ledger?.events.find(item => item.event_id === value.id);
          if (event) await panel.webview.postMessage({ type: 'detail', event });
        } else if (value.type === 'change' && Number.isInteger(value.index)) {
          const change = session.comparison?.changes[value.index as number];
          if (change) await panel.webview.postMessage({ type: 'detail', event: change.event, change });
        } else if (value.type === 'eventDiff' && Number.isInteger(value.index)) {
          const change = session.comparison?.changes[value.index as number];
          if (change) {
            const left = this.texts.put('before.json', change.before ? JSON.stringify(change.before, null, 2) : change.kind === 'removed' ? JSON.stringify(change.event, null, 2) : '');
            const right = this.texts.put('after.json', change.kind === 'removed' ? '' : JSON.stringify(change.event, null, 2));
            await vscode.commands.executeCommand('vscode.diff', left, right, t('BAC event: {0}', change.event.event_id));
          }
        } else if (value.type === 'file' && typeof value.id === 'string' && typeof value.path === 'string') {
          const changedEvent = Number.isInteger(value.changeIndex) ? session.comparison?.changes[value.changeIndex as number]?.event : undefined;
          const candidates = changedEvent ? [changedEvent] : session.ledger?.events ?? [];
          const event = candidates.find(item => item.event_id === value.id && this.filePaths(item).includes(value.path as string));
          if (event && ['open', 'worktree', 'staged', 'recorded'].includes(String(value.action))) await this.openFile(session, event, value.path, String(value.action));
        }
      } catch (error) { await this.error(session, error); }
    });
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(path.dirname(document.uri.fsPath), path.basename(document.uri.fsPath)));
    let timer: ReturnType<typeof setTimeout> | undefined;
    const changed = () => { clearTimeout(timer); timer = setTimeout(() => { void this.refresh(session).catch(e => this.error(session, e)); }, 200); };
    const subscriptions = [watcher.onDidChange(changed), watcher.onDidCreate(changed), watcher.onDidDelete(changed)];
    panel.onDidDispose(() => {
      clearTimeout(timer);
      session.generation++;
      this.sessions.delete(session);
      message.dispose(); watcher.dispose(); subscriptions.forEach(item => item.dispose());
    });
  }

  private async html(webview: vscode.Webview): Promise<string> {
    const template = await vscode.workspace.fs.readFile(vscode.Uri.joinPath(this.context.extensionUri, 'media', 'viewer.html'));
    const nonce = randomBytes(24).toString('hex');
    return Buffer.from(template).toString('utf8')
      .replaceAll('{{language}}', getLanguage())
      .replaceAll('{{cspSource}}', webview.cspSource).replaceAll('{{nonce}}', nonce)
      .replaceAll('{{styleUri}}', webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, 'media', 'viewer.css')).toString())
      .replaceAll('{{i18nUri}}', webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, 'media', 'i18n.js')).toString())
      .replaceAll('{{scriptUri}}', webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, 'media', 'viewer.js')).toString());
  }

  async updateLanguage(): Promise<void> {
    setLanguage(vscode.workspace.getConfiguration('bacViewer').get('language', 'en'));
    await Promise.all([...this.sessions].map(async session => {
      // Invalidate pending operations before the webview requests its new localized state.
      session.generation++;
      session.panel.webview.html = await this.html(session.panel.webview);
    }));
  }

  private async read(uri: vscode.Uri): Promise<Ledger> {
    const stat = await vscode.workspace.fs.stat(uri);
    if (stat.size > MAX_BAC_BYTES) throw new Error(t('The ledger exceeds the 50 MiB read limit.'));
    return readLedger(await vscode.workspace.fs.readFile(uri));
  }

  private async refresh(session: Session): Promise<void> {
    const generation = ++session.generation;
    await session.panel.webview.postMessage({ type: 'busy', text: t('Reading ledger…') });
    try {
      const ledger = await this.read(session.uri);
      if (generation !== session.generation) return;
      if (session.ledger?.digest !== ledger.digest) session.verification = undefined;
      session.ledger = ledger;
      session.comparison = undefined;
      session.mode = undefined;
      await this.postLedger(session);
    } catch (error) {
      if (generation !== session.generation) return;
      session.ledger = undefined;
      session.comparison = undefined;
      session.verification = undefined;
      await session.panel.webview.postMessage({ type: 'loadError', message: String(error instanceof Error ? error.message : error) });
    }
  }

  private async postLedger(session: Session): Promise<void> {
    const ledger = session.ledger;
    if (!ledger) return;
    await session.panel.webview.postMessage({ type: 'ledger', name: path.basename(session.uri.fsPath), ledger: { manifest: ledger.manifest, count: ledger.events.length, digest: ledger.digest, events: ledger.events.map(event => ({ event_id: event.event_id, event_type: event.event_type, source_type: event.source_type, trust_level: event.trust_level, created_at: event.created_at, summary: event.payload.summary, files: this.filePaths(event), hash: event.event_hash })) }, verification: session.verification, trusted: vscode.workspace.isTrusted });
  }

  private trust(): void {
    if (!vscode.workspace.isTrusted) throw new Error(t('Trust this workspace before using Git, file navigation or verification.'));
  }

  private async root(uri: vscode.Uri): Promise<string> {
    this.trust();
    const extension = vscode.extensions.getExtension<{ getAPI(version: number): { repositories: { rootUri: vscode.Uri }[] } }>('vscode.git');
    if (extension) {
      const api = await extension.activate();
      const repositories = api.getAPI(1).repositories.filter(repo => inside(repo.rootUri.fsPath, uri.fsPath)).sort((a, b) => b.rootUri.fsPath.length - a.rootUri.fsPath.length);
      if (repositories.length) return repositories[0].rootUri.fsPath;
    }
    try { return (await runFile('git', ['rev-parse', '--show-toplevel'], path.dirname(uri.fsPath), 1024 * 1024)).toString('utf8').trim(); }
    catch { throw new Error(t('The ledger directory is not a Git repository. Version comparison is unavailable.')); }
  }

  private async blob(root: string, ref: string, relative: string): Promise<Buffer | undefined> {
    const index = ref === ':';
    if (ref === 'HEAD') {
      try { await runFile('git', ['rev-parse', '--verify', '--quiet', 'HEAD'], root, 1024 * 1024); }
      catch (error) { if ((error as { code?: unknown }).code === 1) return undefined; throw error; }
    }
    const listing = await runFile('git', index ? ['--literal-pathspecs', 'ls-files', '--stage', '-z', '--', relative] : ['--literal-pathspecs', 'ls-tree', '-z', ref, '--', relative], root);
    if (!listing.length) return undefined;
    if (index && listing.toString('utf8').split('\0').filter(Boolean).some(line => !/^\d+ [a-f0-9]+ 0\t/.test(line))) throw new Error(t('This file has merge conflicts in the index. Resolve them first.'));
    return runFile('git', ['cat-file', 'blob', index ? `:${relative}` : `${ref}:${relative}`], root);
  }

  async compare(session: Session, mode: CompareMode): Promise<void> {
    const root = await this.root(session.uri);
    const relative = path.relative(root, session.uri.fsPath).split(path.sep).join('/');
    const generation = ++session.generation;
    await session.panel.webview.postMessage({ type: 'busy', text: t('Comparing {0}…', t(LABELS[mode])) });
    const oldData = await this.blob(root, mode === 'index-worktree' ? ':' : 'HEAD', relative);
    const loaded = mode === 'head-index' ? await this.blob(root, ':', relative).then(data => data ? readLedger(data) : undefined) : await this.read(session.uri);
    const oldLedger = oldData ? await readLedger(oldData) : undefined;
    const newLedger = loaded ?? { manifest: oldLedger?.manifest ?? {}, events: [], digest: digest(new Uint8Array()) };
    if (generation !== session.generation) return;
    const comparison = compareLedgers(oldLedger, newLedger);
    if (mode !== 'head-index' && session.ledger?.digest !== newLedger.digest) {
      session.ledger = newLedger;
      session.verification = undefined;
      await this.postLedger(session);
    }
    session.comparison = comparison;
    session.mode = mode;
    await session.panel.webview.postMessage({ type: 'comparison', mode, label: t(LABELS[mode]), missingBaseline: !oldLedger, missingTarget: !loaded, comparison });
  }

  async verify(session: Session): Promise<void> {
    this.trust();
    if (!session.ledger) throw new Error(t('Load a valid ledger first.'));
    const generation = session.generation;
    const expectedDigest = session.ledger.digest;
    const folder = vscode.workspace.getWorkspaceFolder(session.uri);
    let root = folder?.uri.fsPath ?? path.dirname(session.uri.fsPath);
    try { root = await this.root(session.uri); } catch { /* Verification also works outside Git. */ }
    let executable = vscode.workspace.getConfiguration('bacViewer').get<string>('bacExecutable', 'bac');
    if (executable === 'bac') {
      const local = path.join(os.homedir(), '.local', 'bin', process.platform === 'win32' ? 'bac.exe' : 'bac');
      try { await access(local); executable = local; } catch { /* Use PATH. */ }
    }
    await session.panel.webview.postMessage({ type: 'busy', text: t('Running full BAC verification…') });
    const bytes = await vscode.workspace.fs.readFile(session.uri);
    if (digest(bytes) !== expectedDigest) throw new Error(t('The ledger changed. Refresh and verify again.'));
    // Verify a private immutable snapshot, so a concurrent replacement cannot validate other bytes.
    const temp = await mkdtemp(path.join(os.tmpdir(), 'bac-viewer-verify-'));
    let report: Record<string, unknown>;
    try {
      const snapshot = path.join(temp, 'snapshot.bac');
      await writeFile(snapshot, bytes, { mode: 0o600 });
      report = await new Promise<Record<string, unknown>>((resolve, reject) => {
        execFile(executable, ['--root', root, '--bac-file', snapshot, 'verify', '--json'], { cwd: root, encoding: 'utf8', timeout: 30_000, maxBuffer: 8 * 1024 * 1024, windowsHide: true }, (error, stdout, stderr) => {
          try {
            const parsed: unknown = JSON.parse(stdout);
            if (!isObject(parsed) || !['pass', 'warn', 'fail'].includes(String(parsed.status)) || !Array.isArray(parsed.errors) || !Array.isArray(parsed.warnings)) throw new Error(t('Unsupported verifier output.'));
            if (error && (error as { code?: unknown }).code !== 1) throw error;
            resolve(parsed);
          } catch (e) {
            reject(new Error(t('Unable to run the BAC verifier. Install bensz-auto-contribution or set bacViewer.bacExecutable.{0} {1}', stderr.trim() ? ` ${stderr.trim()}` : '', String(e instanceof Error ? e.message : e))));
          }
        });
      });
    } finally { await rm(temp, { recursive: true, force: true }); }
    const current = await vscode.workspace.fs.readFile(session.uri);
    if (generation !== session.generation || digest(current) !== expectedDigest) throw new Error(t('The ledger changed during verification. Refresh and verify again.'));
    if (report.head_hash !== session.ledger.events.at(-1)?.event_hash || report.checked_events !== session.ledger.events.length) throw new Error(t('The verification report does not match the current ledger. Refresh and try again.'));
    session.verification = report;
    await session.panel.webview.postMessage({ type: 'verification', report });
  }

  private filePaths(event: BacEvent): string[] {
    return Array.isArray(event.payload.files) ? event.payload.files.filter(isObject).map(file => file.path).filter((item): item is string => typeof item === 'string') : [];
  }

  private async openFile(session: Session, event: BacEvent, relative: string, action: string): Promise<void> {
    this.trust();
    let root: string;
    try { root = await this.root(session.uri); }
    catch {
      if (action !== 'open') throw new Error(t('Code diffs require a Git repository.'));
      const folder = vscode.workspace.getWorkspaceFolder(session.uri);
      if (!folder) throw new Error(t('Open a project folder to locate related files.'));
      root = folder.uri.fsPath;
    }
    const target = await safeProjectPath(root, relative);
    if (action === 'open') { await vscode.commands.executeCommand('vscode.open', vscode.Uri.file(target)); return; }
    const ref = action === 'recorded' ? event.project.git_commit : 'HEAD';
    if (typeof ref !== 'string' || (action === 'recorded' && !/^[a-f0-9]{40,64}$/.test(ref))) throw new Error(t('This event has no usable recorded commit.'));
    const leftData = await this.blob(root, ref, relative);
    const leftText = this.text(leftData);
    let right: vscode.Uri;
    if (action === 'staged') {
      const staged = await this.blob(root, ':', relative);
      right = this.texts.put(`INDEX/${path.basename(relative)}`, this.text(staged));
    } else {
      try {
        const stat = await vscode.workspace.fs.stat(vscode.Uri.file(target));
        if (stat.size > 5 * 1024 * 1024) throw new Error(t('The code file exceeds the 5 MiB comparison limit.'));
        this.text(await vscode.workspace.fs.readFile(vscode.Uri.file(target)));
        right = vscode.Uri.file(target);
      } catch (error) {
        if (error instanceof vscode.FileSystemError && error.code === 'FileNotFound') right = this.texts.put(`deleted/${path.basename(relative)}`, '');
        else throw error;
      }
    }
    const baseline = action === 'recorded' ? t('Recorded commit {0}', ref.slice(0, 8)) : 'HEAD';
    const left = this.texts.put(`${action === 'recorded' ? ref.slice(0, 8) : 'HEAD'}/${path.basename(relative)}`, leftText);
    await vscode.commands.executeCommand('vscode.diff', left, right, `${relative} · ${baseline} → ${action === 'staged' ? t('Index') : t('Working tree')}`);
  }

  private text(data: Uint8Array | undefined): string {
    if (!data) return '';
    if (data.length > 5 * 1024 * 1024) throw new Error(t('The code file exceeds the 5 MiB comparison limit.'));
    if (data.includes(0)) throw new Error(t('This file is binary. Code text comparison is unavailable.'));
    try { return new TextDecoder('utf-8', { fatal: true }).decode(data); }
    catch { throw new Error(t('This file is not UTF-8 text. Code text comparison is unavailable.')); }
  }

  private async error(session: Session, error: unknown): Promise<void> {
    await session.panel.webview.postMessage({ type: 'error', message: error instanceof Error ? error.message : String(error) });
  }

  async command(action: 'open' | 'compare' | 'verify', input?: unknown): Promise<void> {
    let uri = input instanceof vscode.Uri ? input : isObject(input) && input.resourceUri instanceof vscode.Uri ? input.resourceUri : undefined;
    let session = [...this.sessions].find(item => item.panel.active) ?? [...this.sessions].at(-1);
    uri ??= session?.uri;
    if (!uri) {
      uri = (await vscode.window.showOpenDialog({ canSelectMany: false, filters: { [t('BAC Contribution Ledger')]: ['bac'] } }))?.[0];
    }
    if (!uri) return;
    if (!session || session.uri.toString() !== uri.toString()) {
      await vscode.commands.executeCommand('vscode.openWith', uri, VIEW);
      session = [...this.sessions].find(item => item.uri.toString() === uri!.toString());
    } else session.panel.reveal();
    if (session && action !== 'open') {
      try {
        if (!session.ledger) await this.refresh(session);
        if (action === 'compare') await this.compare(session, 'head-worktree');
        else await this.verify(session);
      } catch (error) { await this.error(session, error); }
    }
  }
}

export function activate(context: vscode.ExtensionContext): void {
  setLanguage(vscode.workspace.getConfiguration('bacViewer').get('language', 'en'));
  const texts = new TextDocuments();
  const viewer = new Viewer(context, texts);
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration(event => {
      if (event.affectsConfiguration('bacViewer.language')) void viewer.updateLanguage().catch(error => vscode.window.showErrorMessage(String(error)));
    }),
    vscode.workspace.registerTextDocumentContentProvider('bac-text', texts),
    vscode.workspace.onDidCloseTextDocument(document => { if (document.uri.scheme === 'bac-text') texts.forget(document.uri); }),
    vscode.window.registerCustomEditorProvider(VIEW, viewer, { webviewOptions: { retainContextWhenHidden: true }, supportsMultipleEditorsPerDocument: false }),
    ...(['open', 'compare', 'verify'] as const).map(action => vscode.commands.registerCommand(`bacViewer.${action}`, (uri?: unknown) => viewer.command(action, uri))),
  );
}
