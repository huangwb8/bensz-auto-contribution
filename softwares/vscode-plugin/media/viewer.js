/* All ledger content is rendered as text. No HTML, links or commands from events are executed. */
(() => {
  const vscode = acquireVsCodeApi();
  const $ = id => document.getElementById(id);
  const { t, setLanguage } = BacI18n;
  setLanguage(document.documentElement.lang);
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  for (const attr of ['aria-label', 'placeholder']) {
    document.querySelectorAll(`[data-i18n-${attr}]`).forEach(el => el.setAttribute(attr, t(el.getAttribute(`data-i18n-${attr}`))));
  }
  $('language').value = document.documentElement.lang;
  $('language').addEventListener('change', () => post('language', { language: $('language').value }));
  const names = { human: t('Human'), ai: 'AI', tool: t('Tool'), system: t('System') };
  const kinds = { added: t('Added'), removed: t('Removed'), modified: t('Modified'), reordered: t('Reordered') };
  const eventNames = { genesis: t('Ledger created'), session_started: t('Session started'), human_instruction: t('Human instruction'), human_review: t('Human review'), human_approval: t('Human approval'), ai_plan: t('AI plan'), ai_generation: t('AI generation'), tool_command: t('Tool execution'), file_snapshot: t('File snapshot'), file_change: t('File change'), test_result: t('Test result'), checkpoint: t('Checkpoint'), verification: t('Verification') };
  let ledger;
  let comparison;
  let tab = 'timeline';
  let source = 'all';
  let selected;
  let pageSize = 100;
  let trusted = false;
  let restored = vscode.getState() || {};
  $('search').value = restored.search || '';
  const post = (type, data = {}) => vscode.postMessage({ type, ...data });
  function node(tag, className, text) { const el = document.createElement(tag); if (className) el.className = className; if (text !== undefined) el.textContent = String(text); return el; }
  function str(value) { return typeof value === 'string' ? value : value === undefined || value === null ? '' : JSON.stringify(value); }
  function date(value) { const parsed = new Date(value); return Number.isNaN(parsed.valueOf()) ? str(value) : parsed.toLocaleString(document.documentElement.lang, { hour12: false }); }
  function notice(text, error = false) { $('notice').hidden = !text; $('notice').textContent = text; $('notice').className = error ? 'error' : ''; }
  function save() { vscode.setState({ search: $('search').value, source, selected }); }
  function verification(report) {
    const labels = { pass: t('Verification passed'), warn: t('Verification warnings'), fail: t('Verification failed') };
    $('validation').textContent = report ? labels[report.status] || t('Not verified') : t('Not verified');
    $('validation').className = `validation ${report ? report.status : ''}`;
    $('verification').hidden = !report;
    $('verification-content').replaceChildren();
    if (!report) return;
    const signature = { unsigned: t('No event signatures'), invalid: t('Invalid or unsupported signature') };
    const anchor = { not_anchored: t('Not anchored'), local_checkpoint: t('Local checkpoint only'), receipt_valid: t('Valid remote receipt'), receipt_invalid: t('Invalid remote receipt') };
    $('verification-content').append(node('p', 'note', t('{0} · {1} · {2} events checked', signature[report.signature_status] || str(report.signature_status), anchor[report.anchor_status] || str(report.anchor_status), report.checked_events)));
    for (const error of report.errors || []) $('verification-content').append(node('p', '', t('Error: {0}', str(error))));
    for (const warning of report.warnings || []) $('verification-content').append(node('p', '', t('Warning: {0}', str(warning))));
  }
  function filters() {
    $('sources').replaceChildren();
    for (const key of ['all', 'human', 'ai', 'tool', 'system']) {
      const count = ledger ? ledger.events.filter(e => key === 'all' || e.source_type === key).length : 0;
      const button = node('button', `source-filter${source === key ? ' active' : ''}`, `${key === 'all' ? t('All') : names[key]} ${count}`);
      button.setAttribute('aria-pressed', String(source === key));
      button.addEventListener('click', () => { source = key; pageSize = 100; filters(); render(); save(); });
      $('sources').append(button);
    }
  }
  function rows() {
    if (!ledger) return [];
    const items = tab === 'timeline' ? [...ledger.events].reverse().map(event => ({ event })) : (comparison?.changes || []).map((change, index) => ({ event: { ...change.event, summary: change.event.payload?.summary, files: (Array.isArray(change.event.payload?.files) ? change.event.payload.files : []).filter(f => f && typeof f.path === 'string').map(f => f.path) }, change, index }));
    const query = $('search').value.toLocaleLowerCase();
    return items.filter(item => (source === 'all' || item.event.source_type === source) && [item.event.summary, item.event.event_type, item.event.event_id, ...(item.event.files || [])].map(str).join(' ').toLocaleLowerCase().includes(query));
  }
  function render() {
    const items = rows();
    $('results-label').textContent = `${items.length} ${tab === 'timeline' ? t('events · newest first') : t('changes')}`;
    $('events').replaceChildren();
    if (!items.length) $('events').append(node('p', 'no-results', tab === 'changes' && !comparison ? t('Click “Compare ledger” to read the Git versions.') : t('No matching events.')));
    for (const item of items.slice(0, pageSize)) {
      const event = item.event;
      const key = item.change ? `change:${item.index}` : event.event_id;
      const button = node('button', `event-row${selected === key ? ' selected' : ''}`);
      button.dataset.source = event.source_type;
      button.dataset.eventId = event.event_id;
      button.setAttribute('role', 'listitem');
      const top = node('div', 'row-top');
      top.append(node('span', 'source-label', names[event.source_type]), node('span', '', item.change ? kinds[item.change.kind] : date(event.created_at)));
      button.append(top, node('div', 'row-summary', str(event.summary) || eventNames[event.event_type] || event.event_type), node('div', 'row-type mono', event.event_type));
      button.addEventListener('click', () => {
        selected = key; render(); save();
        post(item.change ? 'change' : 'event', item.change ? { index: item.index } : { id: event.event_id });
      });
      $('events').append(button);
    }
    $('more').hidden = items.length <= pageSize;
  }
  function setTab(next) {
    tab = next;
    $('timeline-tab').classList.toggle('active', next === 'timeline');
    $('changes-tab').classList.toggle('active', next === 'changes');
    $('git-controls').hidden = next !== 'changes';
    pageSize = 100; render();
  }
  function details(event, change) {
    $('detail').replaceChildren();
    const head = node('div', 'detail-head');
    head.dataset.source = event.source_type;
    head.append(node('span', 'badge', names[event.source_type]), node('span', '', eventNames[event.event_type] || event.event_type), node('span', '', date(event.created_at)));
    if (change) head.append(node('span', 'badge', kinds[change.kind]));
    $('detail').append(head, node('h2', '', str(event.payload.summary) || t('No summary provided')));
    const meta = node('dl', 'metadata');
    for (const [label, value] of [[t('Declared actor'), event.actor.declared_name || event.actor.name || event.actor.actor_id || t('Not declared')], [t('Trust claim'), event.trust_level], [t('Git commit'), event.project.git_commit || t('None')], [t('Event ID'), event.event_id], [t('Event hash'), event.event_hash]]) {
      meta.append(node('dt', '', label), node('dd', label === t('Declared actor') ? '' : 'mono', str(value)));
    }
    $('detail').append(meta);
    if (change) {
      const button = node('button', '', t('Open event JSON diff'));
      button.addEventListener('click', () => post('eventDiff', { index: Number(selected?.split(':')[1]) }));
      $('detail').append(node('h3', '', t('Ledger changes')), button);
    }
    const files = Array.isArray(event.payload.files) ? event.payload.files.filter(f => f && typeof f.path === 'string') : [];
    if (files.length) {
      $('detail').append(node('h3', '', t('Related files · {0}', files.length)));
      for (const file of files) {
        const box = node('div', 'file');
        box.append(node('div', 'mono', file.path), node('div', 'note mono', file.after_hash || (file.exists === false ? t('File did not exist when recorded') : t('No file hash recorded'))));
        const actions = node('div', 'file-actions');
        for (const [action, label] of [['open', t('Open file')], ['worktree', t('HEAD → Working tree')], ['staged', t('HEAD → Index')], ['recorded', t('Recorded commit → Working tree')]]) {
          const button = node('button', '', label);
          button.disabled = !trusted;
          button.addEventListener('click', () => post('file', { id: event.event_id, path: file.path, action, changeIndex: change ? Number(selected?.split(':')[1]) : undefined }));
          actions.append(button);
        }
        box.append(actions); $('detail').append(box);
      }
      $('detail').append(node('p', 'note', t('Code comparisons use Git versions and current files; file hashes cannot reconstruct uncommitted content at recording time.')));
    }
    if (event.payload.command !== undefined) {
      $('detail').append(node('h3', '', t('Recorded command · Exit code {0}', str(event.payload.exit_code) || t('Not recorded'))), node('pre', '', str(event.payload.command)));
    }
    if (event.evidence.length) {
      $('detail').append(node('h3', '', t('Verification evidence · {0}', event.evidence.length)));
      for (const evidence of event.evidence) $('detail').append(node('pre', '', typeof evidence === 'string' ? evidence : JSON.stringify(evidence, null, 2)));
    }
    const raw = node('details');
    raw.append(node('summary', '', t('View raw event JSON')), node('pre', '', JSON.stringify(event, null, 2)));
    $('detail').append(raw);
  }
  $('refresh').addEventListener('click', () => post('refresh'));
  $('verify').addEventListener('click', () => post('verify'));
  $('compare').addEventListener('click', () => post('compare', { mode: $('baseline').value }));
  $('timeline-tab').addEventListener('click', () => setTab('timeline'));
  $('changes-tab').addEventListener('click', () => setTab('changes'));
  $('search').addEventListener('input', () => { pageSize = 100; render(); save(); });
  $('more').addEventListener('click', () => { pageSize += 100; render(); });
  window.addEventListener('message', ({ data }) => {
    if (!data || typeof data.type !== 'string') return;
    if (data.type === 'busy') notice(data.text);
    if (data.type === 'error') notice(data.message, true);
    if (data.type === 'loadError') {
      ledger = undefined; comparison = undefined; verification(undefined); filters(); render();
      $('count').textContent = t('Unable to read'); $('head').textContent = ''; $('detail').replaceChildren(node('h2', '', t('Unable to open ledger')), node('p', 'note', t('Check that the file is a BAC v2 ZIP container. Use bac verify to inspect it.')));
      notice(data.message, true);
    }
    if (data.type === 'ledger') {
      ledger = data.ledger; comparison = undefined; trusted = data.trusted;
      source = restored.source && ['all', 'human', 'ai', 'tool', 'system'].includes(restored.source) ? restored.source : source;
      $('filename').textContent = data.name; $('count').textContent = t('{0} events', ledger.count);
      $('head').textContent = t('Ledger head {0}', str(ledger.events.at(-1)?.hash).replace('sha256:', '').slice(0, 16));
      $('verify').disabled = !trusted; $('compare').disabled = !trusted;
      $('compare-summary').textContent = t('Select a range to view event changes');
      verification(data.verification); filters(); render();
      notice(trusted ? '' : t('This workspace is not trusted. Ledger viewing is available; Git, file navigation and verification are disabled.'));
      const event = ledger.events.find(e => e.event_id === (selected || restored.selected)) || ledger.events.at(-1);
      if (event) { selected = event.event_id; post('event', { id: event.event_id }); render(); }
      restored = {};
    }
    if (data.type === 'detail') details(data.event, data.change);
    if (data.type === 'comparison') {
      comparison = data.comparison;
      $('baseline').value = data.mode;
      const counts = {};
      for (const change of comparison.changes) counts[change.kind] = (counts[change.kind] || 0) + 1;
      const summary = Object.entries(kinds).map(([key, name]) => `${name} ${counts[key] || 0}`).join(' · ');
      $('compare-summary').textContent = t('{0} · {1} → {2} events · {3}', data.label, comparison.beforeCount, comparison.afterCount, summary);
      notice(data.missingTarget ? data.missingBaseline ? t('Neither Git version contains this ledger.') : t('The target Git version deleted this ledger. Removed events are shown below.') : comparison.projectChanged ? t('The project binding changed. Check whether the ledgers belong to the same project.') : comparison.manifestChanged ? t('The container manifest changed. Inspect the event JSON and run full verification.') : data.missingBaseline ? t('The baseline has no ledger. All current events are shown as added.') : comparison.appendOnly ? comparison.changes.length ? t('The history prefix is preserved and events are appended. Run verification to check integrity.') : t('The ledger matches the baseline.') : t('History includes modifications, deletions, insertions or reordering. Inspect the changes and run verification.'), comparison.projectChanged || !comparison.appendOnly);
      setTab('changes');
      if (comparison.changes.length) { selected = 'change:0'; render(); post('change', { index: 0 }); }
    }
    if (data.type === 'verification') { verification(data.report); notice(data.report ? t('Verification complete. The results apply to the current working tree ledger.') : t('The ledger changed. Previous verification results are no longer valid.')); }
  });
  post('ready');
})();
