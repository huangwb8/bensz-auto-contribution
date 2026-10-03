import { t, setLanguage, getLanguage } from '../src/i18n';
import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import { mkdtemp, mkdir, symlink, rm, readFile } from 'node:fs/promises';
import * as path from 'node:path';
import * as os from 'node:os';
import { readLedger, compareLedgers, MAX_BAC_BYTES } from '../src/ledger';
import { safeProjectPath } from '../src/paths';
import { event, entries, zip } from './fixtures';

test('reads BAC v2 without presenting parsing as verification', async () => {
  const ledger = await readLedger(zip(entries()));
  assert.equal(ledger.events.length, 1);
  assert.equal(ledger.manifest.format, 'bac.container.v2');
  assert.equal('verification' in ledger, false);
});
test('reads the real project ledger', async () => {
  const ledger = await readLedger(await readFile(path.resolve('..', '..', 'docs', 'contribution.bac')));
  assert.ok(ledger.events.length > 1);
  assert.deepEqual([...new Set(ledger.events.map(item => item.source_type))].sort(), ['ai', 'human', 'system', 'tool']);
});
test('rejects malformed, duplicate, traversing, invalid UTF-8 and oversized containers', async () => {
  await assert.rejects(readLedger(Buffer.from('not a ZIP')));
  const list = entries();
  await assert.rejects(readLedger(zip([...list, list[1]])), /duplicate/);
  await assert.rejects(readLedger(zip([...list, ['../escape.json', '{}']])), /invalid relative path|unsupported/);
  await assert.rejects(readLedger(zip([list[0], [list[1][0], Buffer.from([0xff])]])), /UTF-8/);
  await assert.rejects(readLedger(Buffer.alloc(MAX_BAC_BYTES + 1)), /50 MiB/);
  await assert.rejects(readLedger(zip([list[0], [list[1][0], ' '.repeat(2 * 1024 * 1024 + 1)]])), /decompress/);
});
test('rejects unsupported schema, duplicate IDs and sequence gaps', async () => {
  await assert.rejects(readLedger(zip([entries()[0], ['events/000000000002.json', JSON.stringify(event())]])), /not contiguous/);
  await assert.rejects(readLedger(zip(entries([event(), event()]))), /Duplicate ledger event_id/);
  const invalid = event(); invalid.source_type = 'unknown' as typeof invalid.source_type;
  await assert.rejects(readLedger(zip(entries([invalid]))), /unsupported event structure/);
});
test('detects appended events, changed content despite identical claimed hash and deletion', async () => {
  const before = await readLedger(zip(entries()));
  const appended = await readLedger(zip(entries([event(), event('next', 'ai')])));
  const addition = compareLedgers(before, appended);
  assert.equal(addition.appendOnly, true); assert.deepEqual(addition.changes.map(c => c.kind), ['added']);
  const changed = event(); changed.payload.summary = '篡改来源以外的内容';
  const modified = compareLedgers(before, await readLedger(zip(entries([changed]))));
  assert.deepEqual(modified.changes.map(c => c.kind), ['modified']); assert.equal(modified.appendOnly, false);
  assert.equal(modified.changes[0].event.event_hash, modified.changes[0].before?.event_hash);
  assert.deepEqual(compareLedgers(appended, before).changes.map(c => c.kind), ['removed']);
});
test('identifies reordering and insertion, without treating deletion index shifts as reordering', async () => {
  const before = await readLedger(zip(entries([event(), event('a'), event('b')])));
  const reordered = await readLedger(zip(entries([event(), event('b'), event('a')])));
  assert.deepEqual(compareLedgers(before, reordered).changes.map(c => c.kind), ['reordered', 'reordered']);
  const deletion = await readLedger(zip(entries([event(), event('b')])));
  assert.deepEqual(compareLedgers(before, deletion).changes.map(c => c.kind), ['removed']);
  const inserted = await readLedger(zip(entries([event(), event('new'), event('a'), event('b')])));
  assert.equal(compareLedgers(before, inserted).appendOnly, false);
});
test('detects project/manifest changes and missing Git baseline', async () => {
  const before = await readLedger(zip(entries()));
  const after = await readLedger(zip(entries())); after.manifest = { ...after.manifest, project: { different: true } };
  assert.equal(compareLedgers(before, after).projectChanged, true);
  assert.equal(compareLedgers(before, after).appendOnly, false);
  assert.deepEqual(compareLedgers(undefined, before).changes.map(c => c.kind), ['added']);
});
test('confines file navigation, including deleted targets behind an escaping symlink', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'bac-viewer-test-'));
  const outside = await mkdtemp(path.join(os.tmpdir(), 'bac-viewer-outside-'));
  try {
    await mkdir(path.join(root, 'src'));
    await symlink(outside, path.join(root, 'escape'), 'dir');
    assert.equal(await safeProjectPath(root, 'src/new.ts'), path.join(root, 'src/new.ts'));
    for (const target of ['../outside', '/etc/passwd', 'C:\\Windows\\file', 'src\\file', 'escape/missing.ts']) await assert.rejects(safeProjectPath(root, target));
  } finally { await rm(root, { recursive: true, force: true }); await rm(outside, { recursive: true, force: true }); }
});

test('defaults to English, supports explicit Chinese and keeps inserted evidence literal', async () => {
  assert.equal(getLanguage(), 'en');
  assert.equal(t('Verify ledger'), 'Verify ledger');
  setLanguage('zh-CN');
  try {
    assert.equal(t('Verify ledger'), '验证账本');
    assert.equal(t('Error: {0}', '$& {1} 原始证据'), '错误：$& {1} 原始证据');
    assert.equal(t('Unknown verifier message'), 'Unknown verifier message');
    await assert.rejects(readLedger(Buffer.alloc(MAX_BAC_BYTES + 1)), /账本超过/);
  } finally { setLanguage('en'); }
  setLanguage('unexpected-locale');
  assert.equal(getLanguage(), 'en');
  await assert.rejects(readLedger(Buffer.alloc(MAX_BAC_BYTES + 1)), /ledger exceeds/);
});
