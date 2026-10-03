import { createHash } from 'node:crypto';
import { BacEvent } from '../src/ledger';

export function event(id = 'genesis', source: BacEvent['source_type'] = 'system'): BacEvent {
  return {
    format: 'bac.event.v2', event_id: id, event_type: id === 'genesis' ? 'genesis' : 'file_change',
    source_type: source, trust_level: 'observed', created_at: '2026-10-03T00:00:00Z',
    project: { root_hash: `sha256:${'a'.repeat(64)}`, git_commit: 'b'.repeat(40) }, actor: { name: source },
    payload: { summary: `记录 ${id}` }, evidence: [], redactions: [], prev_event_hash: null,
    event_hash: `sha256:${createHash('sha256').update(id).digest('hex')}`, signature: null,
  };
}
function crc32(data: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of data) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
export function zip(entries: [string, string | Buffer][]): Buffer {
  const files: Buffer[] = [];
  const directory: Buffer[] = [];
  let offset = 0;
  for (const [name, content] of entries) {
    const filename = Buffer.from(name);
    const data = Buffer.isBuffer(content) ? content : Buffer.from(content);
    const header = Buffer.alloc(30);
    header.writeUInt32LE(0x04034b50); header.writeUInt16LE(20, 4); header.writeUInt16LE(0x800, 6);
    header.writeUInt32LE(crc32(data), 14); header.writeUInt32LE(data.length, 18); header.writeUInt32LE(data.length, 22); header.writeUInt16LE(filename.length, 26);
    files.push(header, filename, data);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50); central.writeUInt16LE(20, 4); central.writeUInt16LE(20, 6); central.writeUInt16LE(0x800, 8);
    central.writeUInt32LE(crc32(data), 16); central.writeUInt32LE(data.length, 20); central.writeUInt32LE(data.length, 24); central.writeUInt16LE(filename.length, 28); central.writeUInt32LE(offset, 42);
    directory.push(central, filename);
    offset += header.length + filename.length + data.length;
  }
  const central = Buffer.concat(directory);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50); end.writeUInt16LE(entries.length, 8); end.writeUInt16LE(entries.length, 10); end.writeUInt32LE(central.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...files, central, end]);
}
export function entries(events: BacEvent[] = [event()]): [string, string][] {
  return [
    ['manifest.json', JSON.stringify({ format: 'bac.container.v2', event_format: 'bac.event.v2', project: events[0]?.project })],
    ...events.map((item, index): [string, string] => [`events/${String(index + 1).padStart(12, '0')}.json`, JSON.stringify(item)]),
  ];
}
