import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import * as yauzl from 'yauzl';

export const MAX_BAC_BYTES = 50 * 1024 * 1024;
const MAX_MEMBER_BYTES = 2 * 1024 * 1024;
const MAX_TOTAL_BYTES = 256 * 1024 * 1024;
const MAX_EVENTS = 100_000;
export const SOURCES = ['human', 'ai', 'tool', 'system'] as const;
export type Source = typeof SOURCES[number];
export interface BacEvent {
  event_id: string;
  event_hash: string;
  prev_event_hash: string | null;
  event_type: string;
  source_type: Source;
  created_at: string;
  trust_level: string;
  project: Record<string, unknown>;
  actor: Record<string, unknown>;
  payload: Record<string, unknown>;
  evidence: unknown[];
  [key: string]: unknown;
}
export interface Ledger {
  manifest: Record<string, unknown>;
  events: BacEvent[];
  digest: string;
}
export type ChangeKind = 'added' | 'removed' | 'modified' | 'reordered';
export interface EventChange { kind: ChangeKind; event: BacEvent; before?: BacEvent; oldIndex?: number; newIndex?: number }
export interface LedgerComparison {
  changes: EventChange[];
  appendOnly: boolean;
  manifestChanged: boolean;
  projectChanged: boolean;
  beforeCount: number;
  afterCount: number;
}
export function digest(data: Uint8Array): string {
  return `sha256:${createHash('sha256').update(data).digest('hex')}`;
}
export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

// Bounded, lazy ZIP reads. Never extract paths to disk and never execute recorded commands.
export async function readLedger(data: Uint8Array): Promise<Ledger> {
  if (data.byteLength > MAX_BAC_BYTES) throw new Error('账本超过 50 MiB 读取上限。');
  const members = new Map<string, unknown>();
  await new Promise<void>((resolve, reject) => {
    yauzl.fromBuffer(Buffer.from(data), { lazyEntries: true, validateEntrySizes: true }, (error, zip) => {
      if (error || !zip) { reject(new Error(`无法读取 BAC ZIP 容器：${error?.message ?? '格式无效'}`)); return; }
      let total = 0;
      let count = 0;
      let finished = false;
      const names = new Set<string>();
      const fail = (reason: unknown) => {
        if (finished) return;
        finished = true;
        zip.close();
        reject(reason instanceof Error ? reason : new Error(String(reason)));
      };
      zip.on('error', fail);
      zip.on('end', () => { if (!finished) { finished = true; resolve(); } });
      zip.on('entry', (entry: yauzl.Entry) => {
        if (finished) return;
        if (names.has(entry.fileName)) { fail(new Error(`ZIP 包含重复条目：${entry.fileName}`)); return; }
        names.add(entry.fileName);
        if (++count > MAX_EVENTS + 1) { fail(new Error('ZIP 条目数量超过上限。')); return; }
        const relevant = entry.fileName === 'manifest.json' || /^events\/\d{12}\.json$/.test(entry.fileName);
        if (!relevant) { fail(new Error(`BAC v2 包含不支持的容器条目：${entry.fileName}`)); return; }
        total += entry.uncompressedSize;
        if (entry.uncompressedSize > MAX_MEMBER_BYTES || total > MAX_TOTAL_BYTES) {
          fail(new Error('ZIP 解压大小超过安全上限（单条 2 MiB，总计 256 MiB）。')); return;
        }
        zip.openReadStream(entry, (streamError, stream) => {
          if (streamError || !stream) { fail(streamError ?? new Error('无法读取 ZIP 条目。')); return; }
          const chunks: Buffer[] = [];
          let size = 0;
          stream.on('error', fail);
          stream.on('data', (chunk: Buffer) => {
            size += chunk.length;
            if (size > MAX_MEMBER_BYTES) { stream.destroy(); fail(new Error('ZIP 条目实际解压大小超过上限。')); return; }
            chunks.push(chunk);
          });
          stream.on('end', () => {
            if (finished) return;
            try {
              const text = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks));
              members.set(entry.fileName, JSON.parse(text));
              zip.readEntry();
            } catch (e) { fail(new Error(`${entry.fileName} 不是有效的 UTF-8 JSON：${String(e)}`)); }
          });
        });
      });
      zip.readEntry();
    });
  });
  const manifest = members.get('manifest.json');
  if (!isObject(manifest) || manifest.format !== 'bac.container.v2' || manifest.event_format !== 'bac.event.v2') {
    throw new Error('缺少有效的 BAC v2 manifest.json。');
  }
  const names = [...members.keys()].filter(name => name !== 'manifest.json').sort();
  if (!names.length) throw new Error('账本没有事件。');
  const ids = new Set<string>();
  const events = names.map((name, index) => {
    if (name !== `events/${String(index + 1).padStart(12, '0')}.json`) throw new Error('账本事件编号不连续。');
    const event = members.get(name);
    if (!isObject(event) || event.format !== 'bac.event.v2' || typeof event.event_id !== 'string' || !event.event_id ||
        typeof event.event_hash !== 'string' || typeof event.event_type !== 'string' ||
        !SOURCES.includes(event.source_type as Source) || typeof event.created_at !== 'string' ||
        typeof event.trust_level !== 'string' || !isObject(event.payload) || !isObject(event.project) ||
        !isObject(event.actor) || !Array.isArray(event.evidence)) {
      throw new Error(`${name} 的事件结构不受支持；请用 bac verify 检查。`);
    }
    if (ids.has(event.event_id)) throw new Error(`账本 event_id 重复：${event.event_id}`);
    ids.add(event.event_id);
    return event as BacEvent;
  });
  return { manifest, events, digest: digest(data) };
}

export function compareLedgers(before: Ledger | undefined, after: Ledger): LedgerComparison {
  const oldEvents = before?.events ?? [];
  const oldMap = new Map(oldEvents.map((event, index) => [event.event_id, { event, index }]));
  const newMap = new Map(after.events.map((event, index) => [event.event_id, { event, index }]));
  const changes: EventChange[] = [];
  for (const [id, current] of newMap) {
    const old = oldMap.get(id);
    if (!old) changes.push({ kind: 'added', event: current.event, newIndex: current.index });
    else if (!isDeepStrictEqual(old.event, current.event)) changes.push({ kind: 'modified', event: current.event, before: old.event, oldIndex: old.index, newIndex: current.index });
  }
  for (const [id, old] of oldMap) {
    if (!newMap.has(id)) changes.push({ kind: 'removed', event: old.event, oldIndex: old.index });
  }
  // Detect relative reordering, not index shifts caused only by insertion/removal.
  const commonOld = oldEvents.filter(event => newMap.has(event.event_id)).map(event => event.event_id);
  const commonNew = after.events.filter(event => oldMap.has(event.event_id)).map(event => event.event_id);
  commonNew.forEach((id, rank) => {
    if (id !== commonOld[rank]) {
      const old = oldMap.get(id)!;
      const current = newMap.get(id)!;
      changes.push({ kind: 'reordered', event: current.event, before: old.event, oldIndex: old.index, newIndex: current.index });
    }
  });
  const manifestChanged = !!before && !isDeepStrictEqual(before.manifest, after.manifest);
  const prefixMatches = oldEvents.every((event, index) => isDeepStrictEqual(event, after.events[index]));
  return {
    changes, manifestChanged,
    projectChanged: !!before && !isDeepStrictEqual(before.manifest.project, after.manifest.project),
    appendOnly: prefixMatches && !manifestChanged,
    beforeCount: oldEvents.length, afterCount: after.events.length,
  };
}
