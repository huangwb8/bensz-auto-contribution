import { execFile } from 'node:child_process';
import * as path from 'node:path';
import { inside } from './paths';
import { isObject, MAX_BAC_BYTES } from './ledger';
import { t } from './i18n';

export function runFile(command: string, args: string[], cwd: string, maxBuffer = MAX_BAC_BYTES): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    execFile(command, args, { cwd, encoding: 'buffer', timeout: 20_000, maxBuffer, windowsHide: true, env: { ...process.env, LC_ALL: 'C', GIT_TERMINAL_PROMPT: '0' } }, (error, stdout, stderr) => {
      if (error) reject(Object.assign(new Error(stderr.toString('utf8').trim() || error.message), { code: error.code }));
      else resolve(stdout);
    });
  });
}

export interface GitResource { path: string; ref: string }
export function parseGitResource(query: string, uriPath: string): GitResource {
  let value: unknown;
  try { value = JSON.parse(query); } catch { throw new Error(t('Invalid Git ledger URI.')); }
  if (!isObject(value) || typeof value.path !== 'string' || typeof value.ref !== 'string' ||
      value.submoduleOf !== undefined || !path.isAbsolute(value.path) || value.path.includes('\0') ||
      path.normalize(value.path) !== value.path || path.normalize(uriPath) !== value.path ||
      !/\.bac$/i.test(value.path) || !/^(?:|~|~[123]|:[123]|HEAD|[a-f0-9]{40,64})$/.test(value.ref)) {
    throw new Error(t('Invalid or unsupported Git ledger URI.'));
  }
  return { path: value.path, ref: value.ref };
}

// Match VS Code Git's special ref semantics. '~' falls back to HEAD only when
// this path has no staged change; the empty ref always addresses index stage 0.
export function gitRef(ref: string, staged: boolean): string {
  if (ref === '') return ':';
  if (ref === '~') return staged ? ':' : 'HEAD';
  if (/^~[123]$/.test(ref)) return `:${ref[1]}`;
  return ref;
}

async function resolveTree(root: string, ref: string): Promise<string | undefined> {
  try {
    return (await runFile('git', ['rev-parse', '--verify', '--quiet', `${ref}^{tree}`], root, 1024)).toString('utf8').trim();
  } catch (error) {
    // An unborn HEAD is an absent side. An unavailable requested commit is an error.
    if (ref === 'HEAD' && (error as { code?: unknown }).code === 1) return undefined;
    throw new Error(`${t('The requested Git revision is unavailable: {0}', ref)} · ${error instanceof Error ? error.message : String(error)}`);
  }
}

export async function readBlob(root: string, ref: string, relative: string, limit = MAX_BAC_BYTES): Promise<Buffer | undefined> {
  if (!relative || relative.includes('\0') || relative.includes('\\') || path.isAbsolute(relative) || !inside(root, path.resolve(root, relative))) {
    throw new Error(t('The file path is outside the current project.'));
  }
  const index = /^:(?:[123])?$/.test(ref);
  if (!index && ref !== 'HEAD' && !/^[a-f0-9]{40,64}$/.test(ref)) throw new Error(t('Invalid or unsupported Git ledger URI.'));
  const tree = index ? undefined : await resolveTree(root, ref);
  if (!index && !tree) return undefined;
  const listing = await runFile('git', index
    ? ['--literal-pathspecs', 'ls-files', '--stage', '-z', '--', relative]
    : ['--literal-pathspecs', 'ls-tree', '-z', tree!, '--', relative], root);
  const lines = listing.toString('utf8').split('\0').filter(Boolean).filter(line => line.slice(line.indexOf('\t') + 1) === relative);
  if (!lines.length) {
    if (/^:[123]$/.test(ref)) throw new Error(t('The requested merge stage is unavailable: {0}', ref[1]));
    return undefined;
  }
  let object: string | undefined;
  if (index) {
    const stage = ref === ':' ? '0' : ref[1];
    if (stage === '0' && lines.some(line => !/^\d+ [a-f0-9]+ 0\t/.test(line))) throw new Error(t('This file has merge conflicts in the index. Resolve them first.'));
    object = lines.find(line => line.split('\t')[0].endsWith(` ${stage}`))?.split(' ')[1];
    if (!object) throw new Error(t('The requested merge stage is unavailable: {0}', stage));
  } else {
    const match = /^\d+ blob ([a-f0-9]+)\t/.exec(lines[0]);
    if (!match) throw new Error(t('The Git ledger path does not identify a file.'));
    object = match[1];
  }
  const size = Number((await runFile('git', ['cat-file', '-s', object], root, 1024)).toString('utf8'));
  if (!Number.isSafeInteger(size) || size < 0 || size > limit) throw new Error(t('The Git file exceeds the {0} MiB read limit.', limit / 1024 / 1024));
  // Raw object bytes: never invoke textconv/filters or decode/re-encode a ZIP.
  return runFile('git', ['cat-file', 'blob', object], root, limit);
}
