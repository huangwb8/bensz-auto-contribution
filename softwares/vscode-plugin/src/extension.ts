import { t, setLanguage, getLanguage } from './i18n';
import * as vscode from 'vscode';
import * as path from 'node:path';
import * as os from 'node:os';
import { randomBytes } from 'node:crypto';
import { execFile } from 'node:child_process';
import { access, mkdtemp, writeFile, rm } from 'node:fs/promises';
import { compareLedgers, digest, isObject, Ledger, LedgerComparison, MAX_BAC_BYTES, readLedger, BacEvent } from './ledger';
import { inside, safeProjectPath } from './paths';
import { runFile, readBlob, parseGitResource, gitRef } from './git';
export { runFile } from './git';

const VIEW = 'bac.viewer';
type CompareMode = 'head-worktree' | 'head-index' | 'index-worktree';
const LABELS: Record<CompareMode, string> = { 'head-worktree': 'HEAD → Working tree', 'head-index': 'HEAD → Index', 'index-worktree': 'Index → Working tree' };
interface GitRepository {
  rootUri: vscode.Uri;
  state: { indexChanges: { uri: vscode.Uri }[]; onDidChange: vscode.Event<void> };
}
interface DocumentSource {
  localUri: vscode.Uri;
  label: string;
  ref?: string;
  originalRef?: string;
  root?: string;
  repository?: GitRepository;
  mutable: boolean;
}
interface Session {
  uri: vscode.Uri;
  panel: vscode.WebviewPanel;
  source?: DocumentSource;
  closed?: boolean;
  watchSource?: () => void;
  ledger?: Ledger;
  comparison?: LedgerComparison;
  mode?: CompareMode;
  verification?: Record<string, unknown>;
  generation: number;
}


export class TextDocuments implements vscode.TextDocumentContentProvider {
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

export class Viewer implements vscode.CustomReadonlyEditorProvider {
  private sessions = new Set<Session>();
  constructor(private context: vscode.ExtensionContext, private texts: TextDocuments) {}

  async openCustomDocument(uri: vscode.Uri): Promise<vscode.CustomDocument> {
    if (!['file', 'git'].includes(uri.scheme)) throw new Error(t('Only local files and Git ledger versions are supported.'));
    return { uri, dispose() {} };
  }

  async resolveCustomEditor(document: vscode.CustomDocument, panel: vscode.WebviewPanel): Promise<void> {
    const session: Session = { uri: document.uri, panel, generation: 0 };
    this.sessions.add(session);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const subscriptions: vscode.Disposable[] = [];
    subscriptions.push(panel.onDidDispose(() => {
      clearTimeout(timer);
      session.closed = true;
      session.generation++;
      this.sessions.delete(session);
      subscriptions.forEach(item => item.dispose());
    }));
    try { session.source = await this.source(document.uri); }
    catch { /* Refresh renders source errors inside the viewer. */ }
    if (session.closed) return;
    panel.webview.options = { enableScripts: true, localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, 'media')] };
    const html = await this.html(panel.webview);
    if (session.closed) return;
    panel.webview.html = html;
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
    subscriptions.push(message);
    const changed = () => {
      if (session.closed) return;
      session.generation++;
      session.verification = undefined;
      void panel.webview.postMessage({ type: 'verification' });
      clearTimeout(timer);
      timer = setTimeout(() => { void this.refresh(session).catch(e => this.error(session, e)); }, 200);
    };
    const gitChanged = () => {
      if (session.closed) return;
      clearTimeout(timer);
      timer = setTimeout(() => { void this.checkForChanges(session); }, 200);
    };
    if (document.uri.scheme === 'file') {
      const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(path.dirname(document.uri.fsPath), path.basename(document.uri.fsPath)));
      subscriptions.push(watcher, watcher.onDidChange(gitChanged), watcher.onDidCreate(gitChanged), watcher.onDidDelete(gitChanged));
    }
    let watchedRepository: GitRepository | undefined;
    let repositorySubscription: vscode.Disposable | undefined;
    session.watchSource = () => {
      if (session.source?.mutable && session.source.repository && watchedRepository !== session.source.repository && !session.closed) {
        repositorySubscription?.dispose();
        watchedRepository = session.source.repository;
        repositorySubscription = watchedRepository.state.onDidChange(gitChanged);
        subscriptions.push(repositorySubscription);
      }
    };
    session.watchSource();
    subscriptions.push(vscode.workspace.onDidGrantWorkspaceTrust(changed), vscode.workspace.onDidChangeConfiguration(event => {
      if (event.affectsConfiguration('git.enabled')) changed();
    }));

  }

  private async source(uri: vscode.Uri): Promise<DocumentSource> {
    if (uri.scheme === 'file') return { localUri: uri, label: t('Working tree'), mutable: true };
    this.trust();
    if (uri.scheme !== 'git' || uri.authority || uri.fragment) throw new Error(t('Invalid Git ledger URI.'));
    const resource = parseGitResource(uri.query, uri.fsPath);
    const localUri = vscode.Uri.file(resource.path);
    const folder = vscode.workspace.getWorkspaceFolder(localUri);
    if (!folder || folder.uri.scheme !== 'file') throw new Error(t('The Git ledger must belong to an open project folder.'));
    const extension = vscode.extensions.getExtension<{ enabled: boolean; getAPI(version: number): { repositories: GitRepository[] } }>('vscode.git');
    if (!extension) throw new Error(t('Enable the built-in Git extension to view Git ledger versions.'));
    const api = await extension.activate();
    if (!api.enabled || !vscode.workspace.getConfiguration('git', localUri).get('enabled', true)) throw new Error(t('Enable the built-in Git extension to view Git ledger versions.'));
    const repository = api.getAPI(1).repositories.filter(repo => inside(repo.rootUri.fsPath, resource.path)).sort((a, b) => b.rootUri.fsPath.length - a.rootUri.fsPath.length)[0];
    if (!repository) throw new Error(t('The ledger directory is not a Git repository. Version comparison is unavailable.'));
    const root = repository.rootUri.fsPath;
    await safeProjectPath(root, path.relative(root, resource.path).split(path.sep).join('/'));
    const mutable = ['', '~', ':1', ':2', ':3', '~1', '~2', '~3'].includes(resource.ref);
    let ref = resource.ref;
    if (ref === 'HEAD') {
      try { ref = (await runFile('git', ['rev-parse', '--verify', '--quiet', 'HEAD'], root, 1024)).toString('utf8').trim(); }
      catch (error) { if ((error as { code?: unknown }).code !== 1) throw error; }
    }
    return { localUri, label: '', root, repository, ref, originalRef: resource.ref, mutable };
  }

  private ref(source: DocumentSource): string {
    const staged = source.repository?.state.indexChanges.some(change => change.uri.toString() === source.localUri.toString()) ?? false;
    const ref = gitRef(source.ref!, staged);
    source.label = ref === ':' ? t('Index') : /^:[123]$/.test(ref) ? t('Merge stage {0}', ref[1]) : source.originalRef === '~' ? t('Index baseline · HEAD') : source.originalRef === 'HEAD' ? `HEAD · ${ref.slice(0, 8)}` : t('Commit {0}', ref.slice(0, 8));
    return ref;
  }

  private async bytes(source: DocumentSource): Promise<Uint8Array | undefined> {
    if (source.root) {
      this.trust();
      if (!vscode.workspace.getConfiguration('git', source.localUri).get('enabled', true)) throw new Error(t('Enable the built-in Git extension to view Git ledger versions.'));
      const api = vscode.extensions.getExtension<{ enabled: boolean; getAPI(version: number): { repositories: GitRepository[] } }>('vscode.git')?.exports;
      const repository = api?.enabled ? api.getAPI(1).repositories.find(repo => repo.rootUri.fsPath === source.root) : undefined;
      if (!repository) throw new Error(t('Enable the built-in Git extension to view Git ledger versions.'));
      source.repository = repository;
      return this.blob(source.root, this.ref(source), path.relative(source.root, source.localUri.fsPath).split(path.sep).join('/'));
    }
    source.label = t('Working tree');
    try {
      const stat = await vscode.workspace.fs.stat(source.localUri);
      if (stat.size > MAX_BAC_BYTES) throw new Error(t('The ledger exceeds the 50 MiB read limit.'));
      const data = await vscode.workspace.fs.readFile(source.localUri);
      if (data.byteLength > MAX_BAC_BYTES) throw new Error(t('The ledger exceeds the 50 MiB read limit.'));
      return data;
    } catch (error) {
      if (error instanceof vscode.FileSystemError && error.code === 'FileNotFound') return undefined;
      throw error;
    }
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
      const html = await this.html(session.panel.webview);
      if (!session.closed) session.panel.webview.html = html;
    }));
  }

  private async read(uri: vscode.Uri): Promise<Ledger | undefined> {
    const bytes = await this.bytes(await this.source(uri));
    return bytes ? readLedger(bytes) : undefined;
  }

  private async checkForChanges(session: Session): Promise<void> {
    const generation = session.generation;
    try {
      if (!session.source || session.closed) return;
      const label = session.source.label;
      const bytes = await this.bytes(session.source);
      session.watchSource?.();
      if (session.closed || generation !== session.generation) return;
      if ((bytes ? digest(bytes) : undefined) === session.ledger?.digest && label === session.source.label) return;
    } catch { /* Refresh reports unavailable Git resources inside this side. */ }
    if (!session.closed && generation === session.generation) {
      session.verification = undefined;
      await session.panel.webview.postMessage({ type: 'verification' });
      await this.refresh(session);
    }
  }

  private async refresh(session: Session): Promise<void> {
    if (session.closed) return;
    const generation = ++session.generation;
    await session.panel.webview.postMessage({ type: 'busy', text: t('Reading ledger…') });
    try {
      if (!session.source) session.source = await this.source(session.uri);
      session.watchSource?.();
      const bytes = await this.bytes(session.source);
      session.watchSource?.();
      const ledger = bytes ? await readLedger(bytes) : undefined;
      if (generation !== session.generation) return;
      if (session.ledger?.digest !== ledger?.digest) session.verification = undefined;
      session.ledger = ledger;
      session.comparison = undefined;
      session.mode = undefined;
      session.panel.title = `${path.basename(session.source.localUri.fsPath)} · ${session.source.label}`;
      if (ledger) await this.postLedger(session);
      else await session.panel.webview.postMessage({ type: 'missing', name: path.basename(session.source.localUri.fsPath), source: session.source.label, trusted: vscode.workspace.isTrusted });
    } catch (error) {
      if (generation !== session.generation) return;
      session.ledger = undefined;
      session.comparison = undefined;
      session.verification = undefined;
      await session.panel.webview.postMessage({ type: 'loadError', name: path.basename(session.source?.localUri.fsPath ?? session.uri.fsPath), source: session.source?.label || t('Git version'), message: String(error instanceof Error ? error.message : error) });
    }
  }

  private async postLedger(session: Session): Promise<void> {
    const ledger = session.ledger;
    if (!ledger) return;
    await session.panel.webview.postMessage({ type: 'ledger', name: path.basename(session.source!.localUri.fsPath), source: session.source!.label, historical: session.uri.scheme === 'git', ledger: { manifest: ledger.manifest, count: ledger.events.length, digest: ledger.digest, events: ledger.events.map(event => ({ event_id: event.event_id, event_type: event.event_type, source_type: event.source_type, trust_level: event.trust_level, created_at: event.created_at, summary: event.payload.summary, files: this.filePaths(event), hash: event.event_hash })) }, verification: session.verification, trusted: vscode.workspace.isTrusted });
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

  private blob(root: string, ref: string, relative: string, limit = MAX_BAC_BYTES): Promise<Buffer | undefined> {
    return readBlob(root, ref, relative, limit);
  }

  async compare(session: Session, mode: CompareMode): Promise<void> {
    const generation = ++session.generation;
    if (!session.source) session.source = await this.source(session.uri);
    const localUri = session.source.localUri;
    const root = await this.root(localUri);
    if (generation !== session.generation) return;
    const relative = path.relative(root, localUri.fsPath).split(path.sep).join('/');
    await session.panel.webview.postMessage({ type: 'busy', text: t('Comparing {0}…', t(LABELS[mode])) });
    const oldData = await this.blob(root, mode === 'index-worktree' ? ':' : 'HEAD', relative);
    const loaded = mode === 'head-index' ? await this.blob(root, ':', relative).then(data => data ? readLedger(data) : undefined) : await this.read(localUri);
    const oldLedger = oldData ? await readLedger(oldData) : undefined;
    const newLedger = loaded ?? { manifest: oldLedger?.manifest ?? {}, events: [], digest: digest(new Uint8Array()) };
    if (generation !== session.generation) return;
    const comparison = compareLedgers(oldLedger, newLedger);
    if (session.uri.scheme === 'file' && mode !== 'head-index' && session.ledger?.digest !== newLedger.digest) {
      session.ledger = loaded;
      session.verification = undefined;
      if (loaded) await this.postLedger(session);
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
    const folder = vscode.workspace.getWorkspaceFolder(session.source?.localUri ?? session.uri);
    let root = session.source?.root ?? folder?.uri.fsPath ?? path.dirname((session.source?.localUri ?? session.uri).fsPath);
    if (!session.source?.root) {
      try { root = await this.root(session.source?.localUri ?? session.uri); } catch { /* Verification also works outside Git. */ }
    }
    let executable = vscode.workspace.getConfiguration('bacViewer').get<string>('bacExecutable', 'bac');
    if (executable === 'bac') {
      const local = path.join(os.homedir(), '.local', 'bin', process.platform === 'win32' ? 'bac.exe' : 'bac');
      try { await access(local); executable = local; } catch { /* Use PATH. */ }
    }
    await session.panel.webview.postMessage({ type: 'busy', text: t('Running full BAC verification…') });
    const bytes = await this.bytes(session.source!);
    if (!bytes || digest(bytes) !== expectedDigest) throw new Error(t('The ledger changed. Refresh and verify again.'));
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
    const current = await this.bytes(session.source!);
    if (generation !== session.generation || !current || digest(current) !== expectedDigest) throw new Error(t('The ledger changed during verification. Refresh and verify again.'));
    if (report.head_hash !== session.ledger.events.at(-1)?.event_hash || report.checked_events !== session.ledger.events.length) throw new Error(t('The verification report does not match the current ledger. Refresh and try again.'));
    session.verification = { ...report, ledger_digest: expectedDigest, resource: session.uri.toString(), source: session.source!.label };
    await session.panel.webview.postMessage({ type: 'verification', report: session.verification });
  }

  private filePaths(event: BacEvent): string[] {
    return Array.isArray(event.payload.files) ? event.payload.files.filter(isObject).map(file => file.path).filter((item): item is string => typeof item === 'string') : [];
  }

  private async openFile(session: Session, event: BacEvent, relative: string, action: string): Promise<void> {
    this.trust();
    let root: string;
    try { root = session.source?.root ?? await this.root(session.source?.localUri ?? session.uri); }
    catch {
      if (action !== 'open') throw new Error(t('Code diffs require a Git repository.'));
      const folder = vscode.workspace.getWorkspaceFolder(session.source?.localUri ?? session.uri);
      if (!folder) throw new Error(t('Open a project folder to locate related files.'));
      root = folder.uri.fsPath;
    }
    const target = await safeProjectPath(root, relative);
    if (action === 'open') {
      if (session.uri.scheme === 'git' && session.source) {
        const data = await this.blob(root, this.ref(session.source), relative, 5 * 1024 * 1024);
        if (!data) throw new Error(t('The related file does not exist in this Git version.'));
        await vscode.commands.executeCommand('vscode.open', this.texts.put(`${session.source.label}/${path.basename(relative)}`, this.text(data)));
      } else await vscode.commands.executeCommand('vscode.open', vscode.Uri.file(target));
      return;
    }
    const ref = action === 'recorded' ? event.project.git_commit : 'HEAD';
    if (typeof ref !== 'string' || (action === 'recorded' && !/^[a-f0-9]{40,64}$/.test(ref))) throw new Error(t('This event has no usable recorded commit.'));
    const leftData = await this.blob(root, ref, relative, 5 * 1024 * 1024);
    const leftText = this.text(leftData);
    let right: vscode.Uri;
    if (action === 'staged') {
      const staged = await this.blob(root, ':', relative, 5 * 1024 * 1024);
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
    if (session.closed) return;
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
    vscode.window.registerCustomEditorProvider(VIEW, viewer, { webviewOptions: { retainContextWhenHidden: true }, supportsMultipleEditorsPerDocument: true }),
    ...(['open', 'compare', 'verify'] as const).map(action => vscode.commands.registerCommand(`bacViewer.${action}`, (uri?: unknown) => viewer.command(action, uri))),
  );
}
