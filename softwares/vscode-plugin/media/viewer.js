/* All ledger content is rendered as text. No HTML, links or commands from events are executed. */
(() => {
  const vscode = acquireVsCodeApi();
  const $ = id => document.getElementById(id);
  const names = { human: '人类', ai: 'AI', tool: '工具', system: '系统' };
  const kinds = { added: '新增', removed: '删除', modified: '修改', reordered: '顺序变化' };
  const eventNames = { genesis: '创建账本', session_started: '会话开始', human_instruction: '人类需求', human_review: '人类审阅', human_approval: '人类批准', ai_plan: 'AI 方案', ai_generation: 'AI 生成', tool_command: '工具执行', file_snapshot: '文件快照', file_change: '文件变化', test_result: '测试结果', checkpoint: '检查点', verification: '验证' };
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
  function date(value) { const parsed = new Date(value); return Number.isNaN(parsed.valueOf()) ? str(value) : parsed.toLocaleString('zh-CN', { hour12: false }); }
  function notice(text, error = false) { $('notice').hidden = !text; $('notice').textContent = text; $('notice').className = error ? 'error' : ''; }
  function save() { vscode.setState({ search: $('search').value, source, selected }); }
  function verification(report) {
    const labels = { pass: '验证通过', warn: '验证有警告', fail: '验证失败' };
    $('validation').textContent = report ? labels[report.status] || '未验证' : '未验证';
    $('validation').className = `validation ${report ? report.status : ''}`;
    $('verification').hidden = !report;
    $('verification-content').replaceChildren();
    if (!report) return;
    const signature = { unsigned: '无事件签名', invalid: '签名无效或暂不支持' };
    const anchor = { not_anchored: '未锚定', local_checkpoint: '仅本地检查点', receipt_valid: '远程 receipt 有效', receipt_invalid: '远程 receipt 无效' };
    $('verification-content').append(node('p', 'note', `${signature[report.signature_status] || str(report.signature_status)} · ${anchor[report.anchor_status] || str(report.anchor_status)} · 检查 ${report.checked_events} 条事件`));
    for (const error of report.errors || []) $('verification-content').append(node('p', '', `错误：${str(error)}`));
    for (const warning of report.warnings || []) $('verification-content').append(node('p', '', `警告：${str(warning)}`));
  }
  function filters() {
    $('sources').replaceChildren();
    for (const key of ['all', 'human', 'ai', 'tool', 'system']) {
      const count = ledger ? ledger.events.filter(e => key === 'all' || e.source_type === key).length : 0;
      const button = node('button', `source-filter${source === key ? ' active' : ''}`, `${key === 'all' ? '全部' : names[key]} ${count}`);
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
    $('results-label').textContent = `${items.length} ${tab === 'timeline' ? '条记录 · 最新在前' : '项变化'}`;
    $('events').replaceChildren();
    if (!items.length) $('events').append(node('p', 'no-results', tab === 'changes' && !comparison ? '点击“比较账本”，读取 Git 中的版本。' : '没有匹配的记录。'));
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
    $('detail').append(head, node('h2', '', str(event.payload.summary) || '未提供摘要'));
    const meta = node('dl', 'metadata');
    for (const [label, value] of [['记录者声明', event.actor.declared_name || event.actor.name || event.actor.actor_id || '未声明'], ['信任声明', event.trust_level], ['Git 提交', event.project.git_commit || '无'], ['事件 ID', event.event_id], ['事件 hash', event.event_hash]]) {
      meta.append(node('dt', '', label), node('dd', label === '记录者声明' ? '' : 'mono', str(value)));
    }
    $('detail').append(meta);
    if (change) {
      const button = node('button', '', '打开事件 JSON 差异');
      button.addEventListener('click', () => post('eventDiff', { index: Number(selected?.split(':')[1]) }));
      $('detail').append(node('h3', '', '账本变化'), button);
    }
    const files = Array.isArray(event.payload.files) ? event.payload.files.filter(f => f && typeof f.path === 'string') : [];
    if (files.length) {
      $('detail').append(node('h3', '', `关联文件 · ${files.length}`));
      for (const file of files) {
        const box = node('div', 'file');
        box.append(node('div', 'mono', file.path), node('div', 'note mono', file.after_hash || (file.exists === false ? '记录时文件不存在' : '未记录文件 hash')));
        const actions = node('div', 'file-actions');
        for (const [action, label] of [['open', '打开文件'], ['worktree', 'HEAD → 工作区'], ['staged', 'HEAD → 暂存区'], ['recorded', '记录时提交 → 工作区']]) {
          const button = node('button', '', label);
          button.disabled = !trusted;
          button.addEventListener('click', () => post('file', { id: event.event_id, path: file.path, action, changeIndex: change ? Number(selected?.split(':')[1]) : undefined }));
          actions.append(button);
        }
        box.append(actions); $('detail').append(box);
      }
      $('detail').append(node('p', 'note', '代码比较使用 Git 版本与当前文件；文件 hash 无法还原记录时未提交的代码内容。'));
    }
    if (event.payload.command !== undefined) {
      $('detail').append(node('h3', '', `已记录命令 · 退出码 ${str(event.payload.exit_code) || '未记录'}`), node('pre', '', str(event.payload.command)));
    }
    if (event.evidence.length) {
      $('detail').append(node('h3', '', `验证证据 · ${event.evidence.length}`));
      for (const evidence of event.evidence) $('detail').append(node('pre', '', typeof evidence === 'string' ? evidence : JSON.stringify(evidence, null, 2)));
    }
    const raw = node('details');
    raw.append(node('summary', '', '查看原始事件 JSON'), node('pre', '', JSON.stringify(event, null, 2)));
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
      $('count').textContent = '无法读取'; $('head').textContent = ''; $('detail').replaceChildren(node('h2', '', '无法打开账本'), node('p', 'note', '请确认文件为 BAC v2 ZIP 容器，可使用 bac verify 检查。'));
      notice(data.message, true);
    }
    if (data.type === 'ledger') {
      ledger = data.ledger; comparison = undefined; trusted = data.trusted;
      source = restored.source && ['all', 'human', 'ai', 'tool', 'system'].includes(restored.source) ? restored.source : source;
      $('filename').textContent = data.name; $('count').textContent = `${ledger.count} 条记录`;
      $('head').textContent = `账本 head ${str(ledger.events.at(-1)?.hash).replace('sha256:', '').slice(0, 16)}`;
      $('verify').disabled = !trusted; $('compare').disabled = !trusted;
      $('compare-summary').textContent = '选择范围后查看事件变化';
      verification(data.verification); filters(); render();
      notice(trusted ? '' : '当前工作区未受信任：可查看账本，Git、文件跳转和验证暂不可用。');
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
      $('compare-summary').textContent = `${data.label} · ${comparison.beforeCount} → ${comparison.afterCount} 条 · ${summary}`;
      notice(data.missingTarget ? data.missingBaseline ? '两个 Git 版本中均没有此账本。' : '目标 Git 版本已删除此账本，下面显示删除事件。' : comparison.projectChanged ? '项目绑定发生变化，请检查账本是否来自同一项目。' : comparison.manifestChanged ? '容器 manifest 发生变化，请结合事件 JSON 与完整验证检查。' : data.missingBaseline ? '基线版本没有此账本，当前事件全部显示为新增。' : comparison.appendOnly ? comparison.changes.length ? '变化保持历史前缀，仅追加事件；完整性请另行验证。' : '账本与基线版本一致。' : '历史记录存在修改、删除、插入或重排，请检查变化并运行验证。', comparison.projectChanged || !comparison.appendOnly);
      setTab('changes');
      if (comparison.changes.length) { selected = 'change:0'; render(); post('change', { index: 0 }); }
    }
    if (data.type === 'verification') { verification(data.report); notice(data.report ? '验证完成，结果对应当前工作区账本。' : '账本内容已变化，原验证结果失效。'); }
  });
  post('ready');
})();
