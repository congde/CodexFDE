/* Browser drafts are editable input, never authority or execution state. */
var WorkbenchDrafts = (() => {
  const prefix = 'workbench-draft-v1:', age = 30 * 86400000, maximum = 100;
  const bindings = new Map();
  let runtime = '', storage, timer;
  function notify(message) {
    const el = document.getElementById('draft-status');
    if (el) el.textContent = message;
  }
  function scope(group, context) {
    return [runtime, context.project || '', context.item || 'new', group];
  }
  function key(group, context, base) {
    return prefix + JSON.stringify([...scope(group, context), base]);
  }
  function read(k) {
    const raw = storage.getItem(k);
    if (!raw) return null;
    const value = JSON.parse(raw);
    if (value.schema !== 1 || typeof value.fields !== 'object' || !value.fields ||
        typeof value.saved_at !== 'number') throw Error('草稿格式损坏');
    if (Date.now() - value.saved_at > age) { storage.removeItem(k); return null; }
    return value;
  }
  function cleanup(preferredKey='') {
    const entries = [];
    for (let i = 0; i < storage.length; i++) {
      const k = storage.key(i);
      if (k?.startsWith(prefix)) entries.push(k);
    }
    const kept = [];
    for (const k of entries) {
      try { const v = read(k); if (v) kept.push([k, v.saved_at]); }
      catch (error) { notify('有损坏的本地草稿，原文保留，可使用清理入口移除。'); }
    }
    kept.sort((a, b) => b[1] - a[1] || Number(b[0]===preferredKey) - Number(a[0]===preferredKey));
    kept.slice(maximum).forEach(([k]) => storage.removeItem(k));
  }
  function persist(binding) {
    if (!binding || !runtime) return false;
    if (!storage) { notify('浏览器存储不可用，草稿仅留在当前页面；刷新前请手动复制。提交重试仍使用同一提交键。'); return false; }
    try {
      const fields = binding.get();
      const encoded = JSON.stringify(fields);
      if (encoded === binding.last && !binding.submissionKey) return true;
      storage.setItem(binding.key, JSON.stringify({schema: 1, base: binding.base,
        saved_at: Date.now(), fields, submission_key: binding.submissionKey || ''}));
      binding.last = encoded;
      cleanup(binding.key);
      notify('输入已保存为本机草稿；提交、授权和验收仍需你操作。');
      return true;
    } catch (error) { notify('本地草稿未保存：' + error.message + '。请保留页面或手动复制。'); return false; }
  }
  function savedVersions(binding) {
    if (!storage) return [];
    const found = [];
    for (let i = 0; i < storage.length; i++) {
      const k = storage.key(i);
      if (!k?.startsWith(prefix)) continue;
      let parts;
      try { parts = JSON.parse(k.slice(prefix.length)); }
      catch (_) { continue; }
      if (!Array.isArray(parts) || parts.length < 4 ||
          JSON.stringify(parts.slice(0, 4)) !== JSON.stringify(binding.scope)) continue;
      const saved = read(k);
      if (saved) found.push({key: k, saved});
    }
    return found.sort((a, b) => b.saved.saved_at - a.saved.saved_at);
  }
  function prompt(binding, versions) {
    const host = document.getElementById('draft-' + binding.group);
    if (!host) return;
    host.replaceChildren();
    for (const entry of versions) {
      const {saved} = entry, matches = saved.base === binding.base;
      const section = document.createElement('div'), text = document.createElement('p');
      text.textContent = binding.group==='review' ? (matches ?
        '有当前候选与 Eval 的本机验收草稿。恢复仅填写意见，仍需重新核对并主动验收。' :
        '有旧候选或 Eval 的验收草稿，仅供查看比较；请核对当前证据并填写本轮意见。') :
        matches ? '有未提交的本机草稿。恢复仅填入可编辑字段。' :
        '有旧版本草稿。最新合同保持不变，可展开旧内容后手动复制。';
      section.append(text);
      if (matches) {
        const restore = document.createElement('button'); restore.type = 'button'; restore.textContent = '恢复草稿';
        restore.onclick = () => {
          if(bindings.get(binding.group)!==binding || saved.base!==binding.base)return;
          binding.set(saved.fields); binding.submissionKey = saved.submission_key || '';
          binding.last = JSON.stringify(binding.get()); section.replaceChildren();
        };
        section.append(restore);
      } else {
        const detail = document.createElement('details'), title = document.createElement('summary'), content = document.createElement('pre');
        title.textContent = '查看旧草稿'; content.textContent = JSON.stringify(saved.fields, null, 2);
        detail.append(title, content); section.append(detail);
      }
      const discard = document.createElement('button'); discard.type = 'button'; discard.textContent = '丢弃草稿';
      discard.onclick = () => { try {
        storage.removeItem(entry.key); if(matches)binding.submissionKey = ''; section.replaceChildren();
      } catch (error) { notify('草稿未清理：' + error.message); } };
      section.append(discard);host.append(section);
    }
  }
  function track(group, context, get, set, host) {
    if (!runtime) return;
    const base = JSON.stringify(context.base ?? null), k = key(group, context, base), old = bindings.get(group);
    if (old?.key === k && old.base === base) return;
    if (old?.dirty) persist(old);
    const binding = {group, key: k, scope: scope(group, context), base, get, set, host, dirty: false, last: JSON.stringify(get())};
    bindings.set(group, binding);
    try {
      const versions = savedVersions(binding), current = versions.find(entry => entry.saved.base === base);
      binding.submissionKey = current?.saved.submission_key || ''; prompt(binding, versions);
    }
    catch (error) { notify('本地草稿无法读取：' + error.message + '。原始草稿保留，填写仍可继续。'); }
  }
  function flush() { clearTimeout(timer); for (const b of bindings.values()) if (b.dirty) { if (persist(b)) b.dirty = false; } }
  function input(event) {
    for (const b of bindings.values()) if (b.host?.contains(event.target)) b.dirty = true;
    clearTimeout(timer); timer = setTimeout(flush, 500);
  }
  function detach(groups) { flush(); for (const group of groups) bindings.delete(group); }
  function clear(group) {
    const b = bindings.get(group); if (!b) return;
    b.dirty = false; b.submissionKey = ''; b.last = JSON.stringify(b.get());
    try {
      if (storage) {
        storage.removeItem(b.key);
        // A legacy four-part key may have been recovered. Only the same base
        // is submitted; previous versions remain available for comparison.
        for (const entry of savedVersions(b)) if (entry.saved.base === b.base) storage.removeItem(entry.key);
      }
      prompt(b, savedVersions(b));
    }
    catch (error) { notify('已提交，草稿清理失败：' + error.message); }
  }
  function capture(group) {
    const b = bindings.get(group);
    return b ? {group, key: b.key, scope: b.scope, base: b.base, encoded: JSON.stringify(b.get())} : null;
  }
  function clearSubmitted(snapshot, reset) {
    if (!snapshot) return false;
    const b = bindings.get(snapshot.group);
    const sameBinding = !!b && b.key === snapshot.key;
    const unchanged = sameBinding && JSON.stringify(b.get()) === snapshot.encoded;
    // New input belongs to the next submission even while the first response
    // is pending. Save it before any response-driven rendering changes fields.
    if (sameBinding && !unchanged) persist(b);
    try {
      if (storage) {
        const entries = savedVersions({scope: snapshot.scope});
        for (const entry of entries) if (entry.saved.base === snapshot.base &&
            JSON.stringify(entry.saved.fields) === snapshot.encoded) storage.removeItem(entry.key);
      }
      if (unchanged) {
        if (typeof reset === 'function') reset();
        b.dirty = false; b.submissionKey = ''; b.last = JSON.stringify(b.get());
      }
      if (b) prompt(b, savedVersions(b));
    } catch (error) { notify('已提交，草稿清理失败：' + error.message); }
    return unchanged;
  }
  function submissionKey(group) {
    const b = bindings.get(group);
    if (!b) return null;
    if (!b.submissionKey) b.submissionKey = crypto.randomUUID();
    persist(b); return b.submissionKey;
  }
  function init(id, customStorage) {
    if (!id) return;
    if (runtime && runtime !== id) detach([...bindings.keys()]);
    runtime = id;
    try { storage = customStorage || localStorage; cleanup(); }
    catch (error) { storage = null; notify('本地草稿未保存：' + error.message + '。请手动复制重要输入。'); }
  }
  function clearAll() {
    bindings.forEach(b => { b.dirty = false; b.submissionKey = ''; b.last = JSON.stringify(b.get());
      document.getElementById('draft-' + b.group)?.replaceChildren(); });
    try {
      clearTimeout(timer);const keys = [];
      for (let i = 0; storage && i < storage.length; i++) { const k = storage.key(i); if (k?.startsWith(prefix)) keys.push(k); }
      keys.forEach(k => storage.removeItem(k));
      notify('本机草稿已清理。已提交记录保留在工作台。');
    } catch (error) { notify('草稿未清理：' + error.message); }
  }
  if (typeof document !== 'undefined' && document.addEventListener) document.addEventListener('input', input);
  if (typeof window !== 'undefined' && window.addEventListener) window.addEventListener('pagehide', flush);
  return {init, track, flush, detach, clear, capture, clearSubmitted, clearAll, submissionKey};
})();

function draftFields(ids) {
  return Object.fromEntries(ids.map(id => [id, document.getElementById(id)?.value || '']));
}
function restoreDraftFields(fields) {
  for (const [id, value] of Object.entries(fields)) {
    const el = document.getElementById(id);
    if (el && !el.disabled && typeof value === 'string') { el.value = value; el.dispatchEvent(new Event('input', {bubbles: true})); }
  }
}
function trackInitiativeDraft(item) {
  const ids = ['title','raw','metric','source','problem','goal','nongoals','acceptance','evidence','area'].map(k => 'init-' + k);
  const context = {project: document.getElementById('init-project').value, item: item?.id || 'new', base: item?.version || 0};
  WorkbenchDrafts.track('initiative', context, () => draftFields(ids), restoreDraftFields, document.getElementById('initiative-form'));
}
function trackWorkflowDrafts(data) {
  const context = {project: data.project?.id || currentInitiative?.project_id || '', item: data.id};
  const track = (group, base, ids, host) => WorkbenchDrafts.track(group, {...context, base}, () => draftFields(ids), restoreDraftFields, document.getElementById(host));
  WorkbenchDrafts.track('discussion', {...context, base: [data.document_version || 0, data.proposal?.questions || []]},
    () => ({message: iw('message').value, answers: Object.fromEntries([...iw('answers').querySelectorAll('textarea')].map(e => [e.dataset.question, e.value]))}),
    fields => { if (!iw('message').disabled) iw('message').value = fields.message || '';
      for (const e of iw('answers').querySelectorAll('textarea')) if (!e.disabled) e.value = fields.answers?.[e.dataset.question] || ''; }, iw('pane-action'));
  track('review', initiativeReviewBase(data), ['iw-note'], 'iw-result');
  track('delivery-route', [data.document_version || 0,data.delivery_policy?.mode || 'standard'], ['iw-route-mode','iw-route-note'], 'iw-route-panel');
  track('rework-decision', [data.active_task_id,data.document_version || 0], ['iw-rework-reason','iw-rework-note'], 'iw-rework-panel');
  track('authorization-renewal', data.plan?.plan_id || '', ['iw-renew-note'], 'iw-renew-panel');
  track('v0', data.initiative_version, ['v0-spec','v0-workspace','v0-files'], 'v0-entry');
  const candidateIds = ['task','feedback','supersedes','title','content','applies','excludes','boundary','conflict','parameters','paths','contains','precheck','implement','eval','review','outputs','stop','rollback','guidance','generation-id','evidence-refs','schema','check-preconditions','check-precheck','check-implement','check-eval','required-implement','required-eval'].map(k => 'iw-learning-' + k);
  WorkbenchDrafts.track('learning-candidate', {...context, base: data.active_task_id},
    () => ({...draftFields(candidateIds), 'iw-learning-kind': iw('learning-kind').value}),
    fields => { restoreDraftFields({'iw-learning-task':fields['iw-learning-task'] || ''});
      iw('learning-task').onchange?.();restoreDraftFields(fields); iw('learning-kind').onchange();iw('learning-schema').onchange?.(); }, iw('learning-candidate'));
  WorkbenchDrafts.track('learning-decisions', {...context, base: data.learning_recall?.id || ''},
    () => Object.fromEntries([...iw('learning-matches').querySelectorAll('article[data-asset-id]')].map(card => [card.dataset.assetId,
      {adopt:card.querySelector('[data-choice="adopt"]').value, reason:card.querySelector('[data-choice="reason"]').value,
       parameters:Object.fromEntries([...card.querySelectorAll('[data-parameter]')].map(e => [e.dataset.parameter, e.value]))}])),
    fields => { for (const card of iw('learning-matches').querySelectorAll('article[data-asset-id]')) {
      const f = fields[card.dataset.assetId]; if (!f) continue;
      for (const name of ['adopt','reason']) { const e = card.querySelector('[data-choice="' + name + '"]'); if (!e.disabled) e.value = f[name] || ''; }
      for (const e of card.querySelectorAll('[data-parameter]')) if (!e.disabled) e.value = f.parameters?.[e.dataset.parameter] || '';
    } }, iw('learning-matches'));
}
