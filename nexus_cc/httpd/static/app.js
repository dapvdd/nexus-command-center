'use strict';
/* NEXUS Command Center — single-page ops console.
 * All data comes from the real HTTP API. No fake or stubbed operational data.
 */
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const esc = s => s == null ? '' : String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
const fmt = (s, n) => s == null ? '—' : String(s).slice(0, n != null ? n : 19);

async function api(path, opts = {}) {
  const r = await fetch('/api' + path, { headers: {'Content-Type':'application/json'}, ...opts });
  let j = null;
  try { j = await r.json(); } catch (_) { j = { ok:false, error:'non-JSON response' }; }
  if (!j.ok) { const e = new Error(j.error || j.code || ('HTTP ' + r.status)); e.status = r.status; throw e; }
  return j;
}

function toast(msg, type='ok') {
  const t = $('#toast');
  t.textContent = msg; t.className = type; t.style.display = 'block';
  clearTimeout(t._t); t._t = setTimeout(() => t.style.display = 'none', 4000);
}

/* --- status badge helper --- */
const S_LABEL = { created:'CREATED', queued:'QUEUED', waiting_approval:'WAITING', running:'RUNNING', done:'DONE', failed:'FAILED', cancelled:'CANCELLED' };
function statusBadge(s) { return `<span class="status s-${esc(s)}">${S_LABEL[s] || esc(s)}</span>`; }

/* --- navigation --- */
const PAGES = [
  ['overview','Overview','◈'],
  ['registry','Agent Registry','◍'],
  ['mission','Mission Control','▣'],
  ['queue','Task Queue','▤'],
  ['timeline','Execution Timeline','♧'],
  ['routing','Model Routing','⇄'],
  ['usage','Usage Analytics','◮'],
  ['health','System Health','◉'],
  ['audit','Audit Logs','⚿'],
  ['settings','Settings','⚙'],
];
let CURRENT = 'overview';

function renderNav() {
  $('#nav').innerHTML =
    `<div class="brand">NEXUS<small>COMMAND CENTER</small></div>` +
    PAGES.map(([id,label,dot]) =>
      `<a class="navlink ${id===CURRENT?'active':''}" data-page="${id}"><span class="dot">${dot}</span>${label}</a>`
    ).join('');
  $$('.navlink').forEach(a => a.addEventListener('click', () => go(a.dataset.page)));
}

function go(page) {
  CURRENT = page;
  location.hash = '/' + page;
  renderNav();
  if (page === 'overview') renderOverview();
  else if (page === 'registry') renderRegistry();
  else if (page === 'mission') renderMission();
  else if (page === 'queue') renderQueue();
  else if (page === 'timeline') renderTimeline();
  else if (page === 'routing') renderRouting();
  else if (page === 'usage') renderUsage();
  else if (page === 'health') renderHealth();
  else if (page === 'audit') renderAudit();
  else if (page === 'settings') renderSettings();
}

/* ====================================================================
 * OVERVIEW
 * =================================================================== */
let OV_TICK = null;
async function renderOverview() {
  $('#main').innerHTML =
    `<h1 class="page-title">OPERATION NEXUS — COMMAND VIEW</h1>
     <div class="page-sub">Live control-plane telemetry from the local SQLite store.</div>
     <div id="ov-body"><div class="empty">Loading…</div></div>`;
  clearInterval(OV_TICK); OV_TICK = setInterval(loadOverview, 5000);
  loadOverview();
}

async function loadOverview() {
  let st;
  try { const j = await api('/health'); st = j.data; }
  catch (e) { $('#ov-body').innerHTML = `<div class="empty">Cannot reach API: ${esc(e.message)}</div>`; return; }
  let tasks, engine;
  try { tasks = (await api('/tasks?limit=8')).data; engine = (await api('/engine')).data; }
  catch (e) { toast('overview: ' + e.message, 'err'); return; }
  const c = engine.counts || {};
  const total = Object.values(c).reduce((a,b)=>a+b,0) || 0;
  const ok = st.ok;
  $('#ov-body').innerHTML = `
    <div class="grid g4 mb">
      <div class="panel p12"><div class="stat"><span class="n">${total}</span><span class="l">Total Tasks</span></div>
        <div class="progress mt"><i style="width:${total?100:0}%"></i></div></div>
      <div class="panel p12"><div class="stat"><span class="n" style="color:#d29922">${c.queued||0}</span><span class="l">Queued</span></div></div>
      <div class="panel p12"><div class="stat"><span class="n" style="color:#3fb950">${c.running||0}</span><span class="l">Running</span></div></div>
      <div class="panel p12"><div class="stat"><span class="n" style="color:${ok?'#3fb950':'#f85149'}">${ok?'ONLINE':'DEGRADED'}</span><span class="l">Store</span></div></div>
    </div>
    <div class="grid g2">
      <div class="panel">
        <div class="panel-head"><span>Engine Status</span><span class="live ${engine.state==='running'?'on':'wait'}"></span></div>
        <div class="panel-body kv">
          <dt>State</dt><dd>${esc(engine.state || '—')}</dd>
          <dt>Workers</dt><dd>${engine.workers} (busy ${engine.busy})</dd>
          <dt>Workspace</dt><dd class="fs11">${esc(engine.workspace || '—')}</dd>
          <dt>Store</dt><dd class="${engine.store_ok?'k':'k'}">${engine.store_ok?'healthy':'unhealthy'}</dd>
          <dt>Reap orphans</dt><dd><button class="btn btn-sm" onclick="recoverOrphans()">Reconcile</button></dd>
        </div>
      </div>
      <div class="panel">
        <div class="panel-head"><span>Open Tasks</span><a class="fs10" href="#/queue">view all →</a></div>
        <div class="panel-body" style="padding:0">
          ${tasks.tasks && tasks.tasks.length ? `<table class="tight"><thead><tr><th>Status</th><th>Title</th><th>Agent</th><th>Updated</th></tr></thead><tbody>
            ${tasks.tasks.slice(0,8).map(t=>`<tr><td>${statusBadge(t.status)}</td><td class="fs11">${esc(t.title)}</td><td>${esc(t.agent_id||'—')}</td><td class="fs10 muted">${fmt(t.updated_at,16)}</td></tr>`).join('')}
          </tbody></table>` : `<div class="empty">No tasks yet.</div>`}
        </div>
      </div>
    </div>`;
}

async function recoverOrphans() {
  try { const j = await api('/engine/recover', {method:'POST'}); toast(`Reconciled ${j.data.recovered} orphan(s).`); loadOverview(); }
  catch (e) { toast(e.message, 'err'); }
}

/* ====================================================================
 * AGENT REGISTRY
 * =================================================================== */
async function renderRegistry() {
  $('#main').innerHTML = `
    <h1 class="page-title">AGENT REGISTRY</h1>
    <div class="page-sub">Identity model: LENNOX (human, final authority) · ALISTAIR (investigator) · NEXUS (execution). Adapters bound to roles below.</div>
    <div id="reg-body"><div class="empty">Loading…</div></div>`;
  await loadRegistry();
}

async function loadRegistry() {
  let agents, adapters;
  try { agents = (await api('/agents')).data; adapters = (await api('/adapters')).data; }
  catch (e) { $('#reg-body').innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const rows = agents.agents || [];
  const adap = adapters.adapters || [];
  $('#reg-body').innerHTML = `
  <div class="grid g2">
    <div class="panel">
      <div class="panel-head"><span>Registered Agents</span><span class="muted">${rows.length}</span></div>
      <div class="panel-body" style="padding:0">
        ${rows.length ? `<table><thead><tr><th>ID</th><th>Role</th><th>Capabilities</th><th>Approval</th></tr></thead><tbody>
          ${rows.map(a=>`<tr>
            <td><span class="badge hl">${esc(a.agent_id)}</span></td>
            <td>${esc(a.role||'')}</td>
            <td class="fs11">${(a.capabilities||[]).map(c=>`<span class="badge">${esc(c)}</span>`).join(' ')}</td>
            <td>${a.requires_approval?'<span class="badge warn">GATED</span>':'<span class="badge">open</span>'}</td>
          </tr>`).join('')}</tbody></table>` : `<div class="empty">No agents registered.</div>`}
      </div>
    </div>
    <div class="panel">
      <div class="panel-head"><span>Available Adapters</span><span class="muted">${adap.length}</span></div>
      <div class="panel-body">
        ${adap.length ? adap.map(a=>`
          <div class="panel p12 mt" style="background:#11161d">
            <div class="row spread">
              <div><span class="badge hl">${esc(a.name)}</span>${(a.capabilities||[]).map(c=>`<span class="badge">${esc(c)}</span>`).join('')}</div>
              <span class="fs10 muted">default approval: ${a.default_requires_approval?'<span class="badge warn">required</span>':'<span class="badge">none</span>'}</span>
            </div>
            <div class="fs11 mt muted">${esc(a.description||'')}</div>
            <div class="fs10 mt"><span class="muted">models: </span>${esc((a.supported_models||[]).join(', '))}</div>
          </div>`).join('') : `<div class="empty">None.</div>`}
      </div>
    </div>
  </div>
  <div class="panel mt">
    <div class="panel-head"><span>Register Adapter-Bound Agent</span></div>
    <div class="panel-body">
      <form id="agent-form" class="grid g3">
        <div><label>Agent ID</label><input name="agent_id" required placeholder="e.g. patrol"></div>
        <div><label>Role</label><input name="role" placeholder="role tag"></div>
        <div><label>Name</label><input name="name" placeholder="display name"></div>
        <div><label>Capabilities (csv)</label><input name="capabilities" placeholder="observe,report"></div>
        <div><label>Approval required</label><select name="requires_approval"><option value="false">No</option><option value="true">Yes</option></select></div>
        <div class="row" style="align-items:flex-end"><button class="btn primary" type="submit">Register</button></div>
      </form>
    </div>
  </div>`;
  $('#agent-form') && $('#agent-form').addEventListener('submit', async ev => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    const body = { agent_id: fd.get('agent_id'), role: fd.get('role') || fd.get('agent_id'), name: fd.get('name') || fd.get('agent_id') };
    body.capabilities = fd.get('capabilities') ? fd.get('capabilities').split(',').map(s=>s.trim()).filter(Boolean) : [];
    body.requires_approval = fd.get('requires_approval') === 'true';
    try { await api('/agents', {method:'POST', body: JSON.stringify(body)}); toast('Agent registered'); loadRegistry(); }
    catch (e) { toast(e.message, 'err'); }
  });
}
/* ====================================================================
 * MISSION CONTROL
 * =================================================================== */
async function renderMission() {
  $('#main').innerHTML = `
    <h1 class="page-title">MISSION CONTROL</h1>
    <div class="page-sub">Draft & dispatch operations with LENNOX/ALISTAIR/NEXUS identity and approval gates.</div>
    <div id="mission-body"><div class="empty">Loading form…</div></div>`;
  await loadMissionForm();
}

async function loadMissionForm() {
  let agents, adapters;
  try { agents = (await api('/agents')).data.agents || []; adapters = (await api('/adapters')).data.adapters || []; }
  catch (e) { $('#mission-body').innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const roles = [
    {id:'alistair', name:'ALISTAIR — investigator, hands off to NEXUS'},
    {id:'nexus', name:'NEXUS — execution specialist'},
  ];
  $('#mission-body').innerHTML = `
  <div class="grid g2">
    <div class="panel">
      <div class="panel-head"><span>New Mission / Task</span></div>
      <div class="panel-body">
        <form id="mission-form" class="grid g2">
          <div class="p12" style="grid-column:1/-1;background:#11161d;border:1px solid #1c232c;border-radius:4px">
            <div class="grid g3">
              <div><label>Agent (executor)</label><select name="agent_id">${roles.map(r=>`<option value="${r.id}">${r.name}</option>`).join('')}</select></div>
              <div><label>Adapter</label><select name="adapter"><option value="">— none —</option>${adapters.map(a=>`<option value="${a.name}">${a.name}</option>`).join('')}</select></div>
              <div><label>Approval gate</label><select name="approval_mode"><option value="none">none</option><option value="required">required (LENNOX)</option></select></div>
            </div>
          </div>
          <div style="grid-column:1/-1"><label>Title *</label><input name="title" required maxlength="300" placeholder="Operation title"></div>
          <div style="grid-column:1/-1"><label>Objective</label><textarea name="objective" rows="2" maxlength="2000" placeholder="What must be accomplished"></textarea></div>
          <div><label>Scope (authorized)</label><textarea name="scope" rows="2" maxlength="2000" placeholder="Boundaries of authorized action"></textarea></div>
          <div><label>Forbidden</label><textarea name="forbidden" rows="2" maxlength="2000" placeholder="Explicit prohibitions"></textarea></div>
          <div style="grid-column:1/-1"><label>Acceptance criteria</label><textarea name="acceptance" rows="2" maxlength="2000" placeholder="How do we know it worked"></textarea></div>
          <div style="grid-column:1/-1"><label>Payload (JSON for adapter)</label><textarea name="payload" rows="3" class="mono" placeholder='{"message":"hi"}'></textarea></div>
          <div><label>Priority</label><input name="priority" type="number" min="0" max="1000" value="500"></div>
          <div><label>Idempotency key</label><input name="idempotency_key" placeholder="unique-key"></div>
          <div style="grid-column:1/-1" class="row"><button class="btn primary" type="submit">Dispatch Mission</button><span id="mission-status" class="muted fs11"></span></div>
        </form>
      </div>
    </div>
    <div class="panel">
      <div class="panel-head"><span>Identity / Authority Map</span></div>
      <div class="panel-body">
        <div class="panel p12"><b style="color:#a5d6ff">LENNOX</b> <span class="badge">DECIDE</span><div class="fs11 muted mt">Human commander · final authority · approves gated executions.</div></div>
        <div class="panel p12 mt"><b style="color:#ffd8a5">ALISTAIR</b> <span class="badge">OBSERVE</span><div class="fs11 muted mt">Investigator · read-only inspection · hands off to NEXUS.</div></div>
        <div class="panel p12 mt"><b style="color:#a5ffa5">NEXUS</b> <span class="badge">EXECUTE</span><div class="fs11 muted mt">Execution specialist · performs authorized bounded work.</div></div>
      </div>
    </div>
  </div>`;
  $('#mission-form').addEventListener('submit', async ev => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    const body = {};
    for (const [k,v] of fd.entries()) if (v !== '') body[k] = v;
    if (body.priority) body.priority = parseInt(body.priority);
    if (body.payload) { try { body.payload = JSON.parse(body.payload); } catch (_) { toast('Payload must be valid JSON', 'err'); return; } }
    $('#mission-status').textContent = 'Dispatching…';
    try {
      const r = await api('/tasks', {method:'POST', body: JSON.stringify(body)});
      const t = r.data.task;
      toast('Dispatched: ' + t.status, 'ok');
      $('#mission-status').textContent = 'Task ' + t.id.slice(0,8) + ' → ' + t.status;
    } catch (e) { toast(e.message, 'err'); $('#mission-status').textContent = ''; }
  });
}

/* ====================================================================
 * TASK QUEUE
 * =================================================================== */
let Q_FILTER = '';
async function renderQueue() {
  $('#main').innerHTML = `
    <h1 class="page-title">TASK QUEUE</h1>
    <div class="page-sub">Durable task state with atomic transitions. Idempotent submission; retries within budget.</div>
    <div class="filters" id="q-filters">
      ${['','queued','waiting_approval','running','done','failed','cancelled'].map(f=>
        `<button class="btn ${Q_FILTER===f?'active':''}" data-f="${f}">${f||'ALL'}</button>`).join('')}
    </div>
    <div id="q-body"><div class="empty">Loading…</div></div>`;
  $$('#q-filters .btn').forEach(b => b.addEventListener('click', () => { Q_FILTER = b.dataset.f; renderQueue(); }));
  await loadQueue();
}

async function loadQueue() {
  let j;
  try { j = await api('/tasks?limit=300' + (Q_FILTER?('&status='+Q_FILTER):'')); }
  catch (e) { $('#q-body').innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const tasks = j.data.tasks || [];
  $('#q-body').innerHTML = tasks.length ? `
    <div class="panel"><div class="panel-body" style="padding:0">
    <table><thead><tr><th>Status</th><th>Priority</th><th>Title</th><th>Agent</th><th>Adapter</th><th>Att</th><th>Approval</th><th>Updated</th><th></th></tr></thead>
    <tbody>${tasks.map(t=>`
      <tr>
        <td>${statusBadge(t.status)}</td>
        <td class="fs11" style="color:${t.priority>=700?'#f85149':t.priority<=300?'#8b949e':'#c9d1d9'}">${t.priority}</td>
        <td><a href="#/timeline" onclick="focusTask('${t.id}')">${esc(t.title)}</a><div class="fs10 muted">${t.id.slice(0,12)}…</div></td>
        <td>${esc(t.agent_id||'—')}</td>
        <td class="fs11">${esc(t.adapter||'—')}</td>
        <td class="fs11">${t.attempts}/${t.max_attempts}</td>
        <td class="fs11">${t.approval_mode==='required' ? (t.approval_status==='approved'?'<span class="badge">approved</span>':'<span class="badge warn">GATED</span>') : '<span class="badge">—</span>'}</td>
        <td class="fs10 muted">${fmt(t.updated_at,16)}</td>
        <td class="right">
          ${t.status==='waiting_approval'?`<button class="btn btn-sm" onclick="approveTask('${t.id}')">approve</button> <button class="btn btn-sm danger" onclick="rejectTask('${t.id}')">reject</button>`:''}
          ${t.status==='queued'?`<button class="btn btn-sm danger" onclick="cancelTask('${t.id}')">cancel</button>`:''}
          ${(t.status==='failed'||t.status==='cancelled')?`<button class="btn btn-sm" onclick="retryTask('${t.id}')">retry</button>`:''}
        </td>
      </tr>`).join('')}</tbody></table>
    </div></div>` : `<div class="empty">No tasks in this view.</div>`;
}

async function approveTask(id){ try { await api(`/tasks/${id}/approve`,{method:'POST',body:JSON.stringify({approver:'lennox'})}); toast('Approved; released to engine','ok'); loadQueue(); } catch(e){ toast(e.message,'err'); } }
async function rejectTask(id){ try { await api(`/tasks/${id}/reject`,{method:'POST',body:JSON.stringify({rejector:'lennox'})}); toast('Rejected','ok'); loadQueue(); } catch(e){ toast(e.message,'err'); } }
async function cancelTask(id){ try { await api(`/tasks/${id}/cancel`,{method:'POST',body:JSON.stringify({actor:'lennox'})}); toast('Cancelled','ok'); loadQueue(); } catch(e){ toast(e.message,'err'); } }
async function retryTask(id){ try { await api(`/tasks/${id}/retry`,{method:'POST',body:JSON.stringify({actor:'lennox'})}); toast('Requeued','ok'); loadQueue(); } catch(e){ toast(e.message,'err'); } }
function focusTask(id){ window._focustarget = id; }

/* ====================================================================
 * EXECUTION TIMELINE
 * =================================================================== */
let TL_TICK = null;
async function renderTimeline() {
  $('#main').innerHTML = `
    <h1 class="page-title">EXECUTION TIMELINE</h1>
    <div class="page-sub">Lifecycle detail: claim → run → heartbeat → terminal. Exactly-once persistence per run token.</div>
    <div id="tl-body"><div class="empty">Loading…</div></div>`;
  clearInterval(TL_TICK); TL_TICK = setInterval(loadTimeline, 5000);
  loadTimeline();
}
async function loadTimeline() {
  let j;
  try { j = await api('/tasks?limit=60'); }
  catch (e) { $('#tl-body').innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const tasks = (j.data.tasks || []).slice().sort((a,b)=> (b.updated_at||'').localeCompare(a.updated_at||''));
  const target = window._focustarget;
  $('#tl-body').innerHTML = tasks.length ? tasks.map(t=>`
    <div class="panel mb" style="${target&&t.id===target?'border-color:#58a6ff':''}">
      <div class="panel-head"><span class="row"><span class="badge hl">${t.id.slice(0,8)}</span> ${esc(t.title)}</span>
        <span>${statusBadge(t.status)}</span></div>
      <div class="panel-body">
        ${t.status==='running'?`<div class="progress mb"><i style="width:45%"></i></div>`:''}
        <div class="grid g2">
          <div class="kv fs11">
            <dt>Agent / Adapter</dt><dd>${esc(t.agent_id||'—')} · ${esc(t.adapter||'—')}</dd>
            <dt>Attempts</dt><dd>${t.attempts}/${t.max_attempts}</dd>
            <dt>Created</dt><dd>${fmt(t.created_at,19)}</dd>
            <dt>Updated</dt><dd>${fmt(t.updated_at,19)}</dd>
            ${t.run_token?`<dt>Run token</dt><dd class="fs10">${esc(t.run_token)}</dd>`:''}
            ${t.heartbeat_at?`<dt>Heartbeat</dt><dd>${fmt(t.heartbeat_at,19)}</dd>`:''}
            ${t.next_attempt_at?`<dt>Next attempt</dt><dd>${fmt(t.next_attempt_at,19)}</dd>`:''}
          </div>
          <div>
            <div class="fs10 muted mb">SCOPE</div><pre class="code">${esc(t.scope || '(none)')}</pre>
            ${t.forbidden?`<div class="fs10 muted mt">FORBIDDEN</div><pre class="code">${esc(t.forbidden)}</pre>`:''}
            ${t.objective?`<div class="fs10 muted mt">OBJECTIVE</div><pre class="code">${esc(t.objective)}</pre>`:''}
          </div>
        </div>
        ${t.payload?`<div class="fs10 muted mt">PAYLOAD</div><pre class="code">${esc(JSON.stringify(t.payload,null,1))}</pre>`:''}
        ${t.result?`<div class="fs10 muted mt">RESULT</div><pre class="code">${esc(JSON.stringify(t.result,null,1))}</pre>`:''}
        ${t.error?`<div class="fs10 muted mt" style="color:#f85149">ERROR</div><pre class="code" style="color:#f85149">${esc(t.error)}</pre>`:''}
        ${t.idempotency_key?`<div class="fs10 muted mt">idempotency_key: <tt>${esc(t.idempotency_key)}</tt></div>`:''}
      </div>
    </div>`).join('') : `<div class="empty">No tasks.</div>`;
  window._focustarget = null;
}
/* ====================================================================
 * MODEL ROUTING
 * =================================================================== */
let RT_TICK = null;
async function renderRouting() {
  $('#main').innerHTML = `
    <h1 class="page-title">MODEL ROUTING</h1>
    <div class="page-sub">Adapter → model binding. Local-first; external runtimes are documented adapters, not wired in this vertical slice.</div>
    <div id="rt-body"><div class="empty">Loading…</div></div>`;
  clearInterval(RT_TICK); RT_TICK = setInterval(loadRouting, 10000);
  loadRouting();
}
async function loadRouting() {
  let adapters;
  try { adapters = (await api('/adapters')).data.adapters || []; }
  catch (e) { $('#rt-body').innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  $('#rt-body').innerHTML = `
    <div class="panel"><div class="panel-body" style="padding:0">
      <table><thead><tr><th>Adapter</th><th>Capabilities</th><th>Supported Models</th><th>Default Approval</th></tr></thead>
      <tbody>${adapters.map(a=>`<tr>
        <td><span class="badge hl">${esc(a.name)}</span></td>
        <td class="fs11">${(a.capabilities||[]).map(c=>`<span class="badge">${esc(c)}</span>`).join(' ')}</td>
        <td class="fs11">${(a.supported_models||[]).map(m=>`<span class="badge">${esc(m)}</span>`).join(' ')}</td>
        <td>${a.default_requires_approval?'<span class="badge warn">required</span>':'<span class="badge">none</span>'}</td>
      </tr>`).join('')}</tbody></table>
    </div></div>
    <div class="panel mt">
      <div class="panel-head"><span>Routing policy</span></div>
      <div class="panel-body">
        <pre class="code">LOCAL-FIRST (this vertical slice)
  echo      → local          (deterministic)
  inspector → local          (read-only)
  artifact  → local          (writes, approval-gated)

EXTERNAL (documented adapters, Phase 6, NOT wired)
  hermes      :4097 · armor gateway
  nexus-opencode :4098
  9Router     tuf-f15-david…ts.net:20128/v1</pre>
      </div>
    </div>`;
}

/* ====================================================================
 * USAGE ANALYTICS
 * =================================================================== */
async function renderUsage() {
  $('#main').innerHTML = `
    <h1 class="page-title">USAGE ANALYTICS</h1>
    <div class="page-sub">Recorded usage per task run (tokens / invocations). No invented data — only what the engine actually recorded.</div>
    <div id="usage-body"><div class="empty">Loading…</div></div>`;
  await loadUsage();
}
async function loadUsage() {
  let j;
  try { j = await api('/usage'); }
  catch (e) { $('#usage-body').innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const s = j.data.summary || {};
  const byModel = j.data.by_model || [];
  const kinds = s.kinds || {};
  const total = s.total || 0;
  $('#usage-body').innerHTML = `
    <div class="grid g4 mb">
      <div class="panel p12"><div class="stat"><span class="n">${total}</span><span class="l">Total Runs</span></div></div>
      ${Object.entries(kinds).map(([k,v])=>`<div class="panel p12"><div class="stat"><span class="n">${v}</span><span class="l">${esc(k)}</span></div></div>`).join('')}
    </div>
    <div class="panel">
      <div class="panel-head"><span>Usage by model</span></div>
      <div class="panel-body" style="padding:0">
        ${byModel.length?`<table><thead><tr><th>Model</th><th>Kind</th><th>Total</th></tr></thead><tbody>
          ${byModel.map(r=>`<tr><td><span class="badge hl">${esc(r.model)}</span></td><td class="fs11">${esc(r.kind||'')}</td><td>${r.total}</td></tr>`).join('')}
        </tbody></table>`:'<div class="empty">No usage recorded yet.</div>'}
      </div>
    </div>`;
}

/* ====================================================================
 * SYSTEM HEALTH
 * =================================================================== */
let HT_TICK = null;
async function renderHealth() {
  $('#main').innerHTML = `
    <h1 class="page-title">SYSTEM HEALTH</h1>
    <div class="page-sub">Live store health, engine state, and agent availability. Real checks only.</div>
    <div id="health-body"><div class="empty">Loading…</div></div>`;
  clearInterval(HT_TICK); HT_TICK = setInterval(loadHealth, 5000);
  loadHealth();
}
async function loadHealth() {
  let j, eng, agents, logs;
  try { j = await api('/health'); eng = (await api('/engine')).data; agents = (await api('/agents')).data.agents || []; logs = (await api('/logs?limit=20')).data.logs || []; }
  catch (e) { $('#health-body').innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const h = j.data;
  $('#health-body').innerHTML = `
    <div class="grid g4 mb">
      <div class="panel p12"><div class="stat"><span class="n" style="color:${h.ok?'#3fb950':'#f85149'}">${h.ok?'OK':'DOWN'}</span><span class="l">Store write-read</span></div></div>
      <div class="panel p12"><div class="stat"><span class="n" style="color:${eng.state==='running'?'#3fb950':'#d29922'}">${esc(eng.state||'?')}</span><span class="l">Engine</span></div></div>
      <div class="panel p12"><div class="stat"><span class="n">${agents.length}</span><span class="l">Agents</span></div></div>
      <div class="panel p12"><div class="stat"><span class="n">${logs}</span><span class="l">Engine Logs</span></div></div>
    </div>
    <div class="grid g2">
      <div class="panel"><div class="panel-head"><span>Store detail</span></div><div class="panel-body kv">
        <dt>version</dt><dd>${esc(h.version||'')}</dd><dt>path</dt><dd class="fs10">${esc(h.path||'')}</dd>
        <dt>time</dt><dd>${esc(h.time||'')}</dd><dt>latency</dt><dd>${h.latency_ms!=null?h.latency_ms+'ms':'—'}</dd>
      </div></div>
      <div class="panel"><div class="panel-head"><span>Engine</span></div><div class="panel-body kv">
        <dt>workers</dt><dd>${eng.workers} (busy ${eng.busy})</dd>
        <dt>workspace</dt><dd class="fs10">${esc(eng.workspace||'—')}</dd>
        <dt>store_ok</dt><dd>${eng.store_ok?'<span class="k">true</span>':'<span class="k">false</span>'}</dd>
        <dt>active runs</dt><dd>${(eng.running||[]).map(r=>`<span class="badge">${esc(String(r).slice(0,8))}</span>`).join(' ')||'—'}</dd>
      </div></div>
    </div>
    <div class="panel mt">
      <div class="panel-head"><span>Engine Log Stream</span></div>
      <div class="panel-body" style="padding:0">
        ${logs.length?`<table class="tight"><thead><tr><th>When</th><th>Scope</th><th>Message</th></tr></thead><tbody>
          ${logs.map(l=>`<tr><td class="fs10 muted" style="white-space:nowrap">${fmt(l.created_at,19)}</td><td><span class="badge">${esc(l.scope||'')}</span></td><td class="fs11">${esc(l.message||'')}</td></tr>`).join('')}
        </tbody></table>`:'<div class="empty">No logs.</div>'}
      </div>
    </div>`;
}

/* ====================================================================
 * AUDIT LOGS
 * =================================================================== */
let AUD_TICK = null;
async function renderAudit() {
  $('#main').innerHTML = `
    <h1 class="page-title">AUDIT LOGS</h1>
    <div class="page-sub">Immutable audit trail of every control-plane action.</div>
    <div id="audit-body"><div class="empty">Loading…</div></div>`;
  clearInterval(AUD_TICK); AUD_TICK = setInterval(loadAudit, 10000);
  loadAudit();
}
async function loadAudit() {
  let j;
  try { j = await api('/audit?limit=300'); }
  catch (e) { $('#audit-body').innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  const rows = j.data.audit || [];
  $('#audit-body').innerHTML = rows.length ? `
    <div class="panel"><div class="panel-body" style="padding:0">
    <table><thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Object</th><th>Detail</th></tr></thead>
    <tbody>${rows.map(r=>`<tr>
      <td class="fs10 muted" style="white-space:nowrap">${fmt(r.created_at,19)}</td>
      <td><span class="badge hl">${esc(r.actor)}</span> <span class="fs10 muted">(${esc(r.actor_role||'')})</span></td>
      <td style="color:#58a6ff">${esc(r.action)}</td>
      <td class="fs11">${esc(r.object_type||'')} ${r.object_id?`· <tt>${esc(r.object_id.slice(0,8))}</tt>`:''}</td>
      <td class="fs11 muted">${esc((r.detail||'').slice(0,120))}</td>
    </tr>`).join('')}</tbody></table></div></div>`
  : `<div class="empty">No audit records.</div>`;
}

/* ====================================================================
 * SETTINGS
 * =================================================================== */
let ST_TICK = null;
async function renderSettings() {
  $('#main').innerHTML = `
    <h1 class="page-title">SETTINGS</h1>
    <div class="page-sub">Local configuration. Engine operator controls with safety confirmation.</div>
    <div id="set-body"><div class="empty">Loading…</div></div>`;
  ST_TICK && clearInterval(ST_TICK);
  let eng, h;
  try { eng = (await api('/engine')).data; h = (await api('/health')).data; } catch (e) { $('#set-body').innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  $('#set-body').innerHTML = `
    <div class="panel"><div class="panel-head"><span>Engine</span></div><div class="panel-body kv">
      <dt>State</dt><dd>${eng.state}</dd>
      <dt>Workers</dt><dd>${eng.workers} (busy: ${eng.busy})</dd>
      <dt>Workspace</dt><dd class="fs10">${esc(eng.workspace||'')}</dd>
      <dt>Store</dt><dd class="fs10">${esc(h.path||'')}</dd>
    </div></div>
    <div class="grid g3 mt">
      <div class="panel p12"><div class="row"><span class="live ${eng.state==='running'?'on':'off'}"></span> Engine running</div>
        <button class="btn mt" onclick="engStop()">Stop engine</button></div>
      <div class="panel p12">Reconcile stale runs into retry/failure.
        <button class="btn mt" onclick="recoverOrphans()">Recover orphans</button></div>
      <div class="panel p12">Roles/identity are fixed by design (LENNOX·ALISTAIR·NEXUS).
        <button class="btn mt" onclick="go('registry')">View registry</button></div>
    </div>
    <div class="panel mt"><div class="panel-head"><span>About</span></div><div class="panel-body fs11 muted">
      OPERATION NEXUS Multi-Agent Command Center — local vertical slice. Durable state lives in SQLite (WAL).
      All task output from the builtin adapters is SIMULATED provenance. Deployments and privileged operations
      are not performed by this application.</div></div>`;
}
async function engStop(){ try { await api('/engine/stop',{method:'POST'}); toast('Engine stopped'); renderSettings(); } catch(e){ toast(e.message,'err'); } }

/* ====================================================================
 * BOOT
 * =================================================================== */
window.addEventListener('hashchange', () => {
  const p = (location.hash || '#/overview').replace('#/','').split('?')[0];
  if (PAGES.some(x=>x[0]===p)) go(p);
});
window.addEventListener('load', () => {
  renderNav();
  go((location.hash||'#/overview').replace('#/',''));
});
setInterval(() => {
  const isRunning = CURRENT==='queue' || CURRENT==='overview';
  if (isRunning) { /* heavy polling pages re-render on their own ticks */ }
}, 5000);
