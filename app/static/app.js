const $ = (selector) => document.querySelector(selector);
const splitTags = (value) => value.split(',').map((item) => item.trim()).filter(Boolean);
const selected = (name) => [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((input) => input.value);
let draftId = null, previewGeneration = 0, myProfile = null, refreshTimer = null;

async function api(path, options = {}) {
  const started = performance.now();
  const response = await fetch(path, {...options, headers:{'X-NETwork-Request':'1', ...(options.headers || {})}});
  field.telemetry(options.method || 'GET', path, performance.now() - started, response.status);
  if (response.status === 204) return null;
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The request could not be completed.');
  return data;
}
const post = (path, body) => api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});

function switchTab(name) {
  document.querySelectorAll('.tab,.panel').forEach((el) => el.classList.remove('active'));
  document.querySelector(`.tab[data-panel="${name}"]`)?.classList.add('active');
  $(`#${name}`).classList.add('active');
  field.setVisible(name === 'constellation');
  ops.setVisible(name === 'ops');
}
document.querySelectorAll('.tab').forEach((tab) => tab.addEventListener('click', () => switchTab(tab.dataset.panel)));

async function refreshMe() {
  const data = await api('/api/me');
  myProfile = data.profile;
  field.setYou(data.state && data.state !== 'revoked' ? myProfile : null);
  if (data.pipeline) renderPipeline(data.pipeline);
  $('#suggest-people').classList.toggle('hidden', data.state !== 'indexed');
  if (data.state) {
    $('#my-card').classList.remove('hidden');
    $('#profile-form').classList.add('hidden');
    $('#my-card-status').textContent = data.state === 'revoked' ? 'Your card and contacts have been withdrawn.' : `${myProfile?.display_name || 'Your card'} · ${data.state}${myProfile ? ` · expires ${new Date(myProfile.discoverable_until).toLocaleTimeString([], {hour:'numeric', minute:'2-digit'})}` : ''}`;
    $('#revoke-card').disabled = data.state === 'revoked';
    if (['queued', 'processing', 'retry'].includes(data.state)) {
      clearTimeout(refreshTimer); refreshTimer = setTimeout(() => refreshMe().catch(showStatusError), 1500);
    }
    if (data.state === 'failed') $('#my-card-status').textContent += ' · Publishing failed, so your card can\'t be found. Withdraw it or contact the organizer.';
  }
}
const STAGE_ICON = {done:'✓', active:'◌', pending:'·', skipped:'–', withdrawn:'×', failed:'!'};
let lastStages = {};
function renderPipeline(pipeline) {
  const list = $('#pipeline'); let fresh = 0;
  list.innerHTML = '';
  pipeline.stages.forEach((stage) => {
    const li = document.createElement('li'), changed = lastStages[stage.key] !== stage.state;
    li.className = `stage ${stage.state}${changed && stage.state === 'done' ? ' just-done' : ''}`;
    if (changed && stage.state === 'done') li.style.setProperty('--delay', `${fresh++ * 180}ms`);
    li.innerHTML = `<span class="stage-dot" aria-hidden="true">${STAGE_ICON[stage.state] || '·'}</span><div><strong>${escapeHtml(stage.label)}</strong><small>${escapeHtml(stage.detail)}</small></div><em class="mono">${escapeHtml(stage.state)}</em>`;
    list.appendChild(li);
    lastStages[stage.key] = stage.state;
  });
  const job = pipeline.job;
  $('#pipeline-meta').textContent = `job ${job.state} · attempt ${job.attempts}${job.error_code ? ` · ${job.error_code}` : ''} · ${pipeline.engine === 'memory_demo' ? 'memory demo, no embeddings computed' : `embeddings: ${pipeline.model_id}`}`;
}
$('#suggest-people').addEventListener('click', () => {
  const wants = [...(myProfile?.looking_for || []), ...(myProfile?.can_help_with || []).slice(0, 2)].join(', ');
  $('#question').value = wants ? `People who can help with ${wants}` : 'People I should meet at this event';
  switchTab('ask'); $('#search-form').requestSubmit();
});

function showStatusError(error) { $('#runtime-status').textContent = error.message; }
$('#seed-demo').addEventListener('click', async () => {
  try { const data = await post('/api/demo/seed', {}); $('#runtime-status').textContent = `demo · ${data.seeded} synthetic cards loaded · in-memory lexical search`; }
  catch (error) { showStatusError(error); }
});

$('#revoke-card').addEventListener('click', async () => {
  try { await api('/api/me', {method:'DELETE'}); clearTimeout(refreshTimer); await refreshMe(); }
  catch (error) { showStatusError(error); }
});

$('#profile-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget, fd = new FormData(form), result = $('#profile-result');
  if (fd.get('resume')?.size && !draftId) { result.textContent = 'Preview your resume and review the suggestions before you approve, or remove the file and fill in the card yourself.'; return; }
  result.textContent = 'Publishing your card…';
  try {
    const data = await post('/api/profiles', {display_name:fd.get('display_name'), headline:fd.get('headline'),
      public_summary:fd.get('public_summary'), approved_experience:fd.get('approved_experience'),
      can_help_with:splitTags(fd.get('can_help_with')), looking_for:splitTags(fd.get('looking_for')), roles:['peer'],
      formats:selected('formats'), availability:fd.get('availability'), email:fd.get('email') || null,
      linkedin_url:fd.get('linkedin_url') || null, consent_to_discover:fd.get('consent_to_discover') === 'on',
      consent_to_resume_parsing:fd.get('consent_to_resume_parsing') === 'on', draft_id:draftId});
    result.textContent = data.state === 'indexed' ? 'Other attendees can now find your card. Keep this browser open to accept introductions.' : 'Your card is queued. Other attendees can find it once indexing finishes.';
    await refreshMe();
  } catch (error) { result.textContent = error.message; }
});

const resumeInput = document.querySelector('input[name="resume"]');
resumeInput.addEventListener('change', () => { draftId = null; previewGeneration++; $('#resume-preview').textContent = 'Preview this file, then review your card above.'; });
document.querySelector('select[name="source_type"]').addEventListener('change', () => { draftId = null; previewGeneration++; });
document.querySelector('input[name="consent_to_resume_parsing"]').addEventListener('change', (event) => {
  if (!event.target.checked) { draftId = null; previewGeneration++; }
});
$('#preview-resume').addEventListener('click', async () => {
  const file = resumeInput.files[0], result = $('#resume-preview'), form = $('#profile-form');
  if (!file) { result.textContent = 'Choose a resume, or fill in your card yourself.'; return; }
  if (!form.elements.consent_to_resume_parsing.checked) { result.textContent = 'Tick the resume-parsing consent box before previewing.'; return; }
  if (file.size > 6000000) { result.textContent = 'Resume must be 6 MB or smaller.'; return; }
  const generation = ++previewGeneration;
  const fd = new FormData(); fd.append('resume', file); fd.append('consent_to_resume_parsing', 'true'); fd.append('source_type', form.elements.source_type.value);
  result.textContent = 'Reading your resume…';
  try {
    const data = await api('/api/resumes/preview', {method:'POST', body:fd});
    if (generation !== previewGeneration) return;
    draftId = data.draft_id;
    const hints = data.contact_hints || {}, suggestions = data.suggestions || {};
    const setIfEmpty = (name, value) => { const input = form.elements[name]; if (input && !input.value && value) input.value = value; };
    setIfEmpty('display_name', hints.suggested_name); setIfEmpty('email', hints.email); setIfEmpty('linkedin_url', hints.linkedin_url);
    setIfEmpty('can_help_with', (suggestions.skills || []).join(', '));
    setIfEmpty('public_summary', (suggestions.project_highlights || []).join('\n'));
    setIfEmpty('approved_experience', (suggestions.experience_highlights || []).join('\n'));
    result.textContent = `Found ${suggestions.skills?.length || 0} skills. Review your card above before you approve it. This preview expires in 20 minutes.`;
    form.elements.approved_experience.focus();
  } catch (error) { if (generation === previewGeneration) result.textContent = error.message; }
});

let lastCompare = null, viewMode = 'hybrid';
const MODE_NOTE = {
  elasticsearch: 'Keyword = BM25 on profile_text · Semantic = Jina semantic_text · Hybrid = both fused with RRF · same policy filters on every branch',
  memory_demo: 'Memory demo: keyword = term overlap · semantic = transparent concept map (not Jina) · hybrid = RRF of both',
};
$('#search-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const question = $('#question').value, results = $('#search-results'), rag = $('#rag-answer');
  results.textContent = 'Running keyword, semantic and hybrid retrieval…'; rag.classList.add('hidden'); $('#compare-bar').classList.add('hidden');
  try {
    const sponsor_intent = $('#sponsor-intent').checked;
    lastCompare = await post('/api/search/compare', {question, sponsor_intent, size:5});
    viewMode = 'hybrid';
    field.showQuery(question, lastCompare.modes.hybrid);
    renderMode();
    renderAnswer(await post('/api/ask', {question, sponsor_intent}), rag);
  } catch (error) { results.textContent = error.message; }
});
document.querySelectorAll('.seg [data-mode]').forEach((button) => button.addEventListener('click', () => { viewMode = button.dataset.mode; renderMode(); }));
$('.seg').addEventListener('keydown', (event) => {
  const modes = ['keyword', 'semantic', 'hybrid'], step = {ArrowRight:1, ArrowDown:1, ArrowLeft:-1, ArrowUp:-1}[event.key];
  if (!step) return; event.preventDefault();
  viewMode = modes[(modes.indexOf(viewMode) + step + 3) % 3]; renderMode(); $(`.seg [data-mode="${viewMode}"]`).focus();
});
function renderMode() {
  if (!lastCompare) return;
  const results = $('#search-results'), hits = lastCompare.modes[viewMode], missed = new Set(lastCompare.missed_by_keyword);
  $('#compare-bar').classList.remove('hidden');
  document.querySelectorAll('.seg [data-mode]').forEach((button) => {
    const on = button.dataset.mode === viewMode; button.setAttribute('aria-checked', on); button.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll('.seg [data-count]').forEach((el) => { el.textContent = lastCompare.modes[el.dataset.count].length; });
  const semanticDown = lastCompare.modes.semantic.length === 0 && lastCompare.modes.hybrid.some((h) => h.retrieval_mode === 'lexical_fallback');
  $('#compare-summary').textContent = `${missed.size ? `Hybrid surfaced ${missed.size} ${missed.size === 1 ? 'person' : 'people'} keyword search missed. ` : 'Keyword search already found every hybrid match. '}${semanticDown ? 'Semantic inference unavailable; hybrid fell back to lexical. ' : ''}${MODE_NOTE[lastCompare.engine] || ''}`;
  results.textContent = hits.length ? '' : `No ${viewMode} matches. ${viewMode === 'keyword' ? 'Try Semantic or Hybrid for conceptually related people.' : 'Invite more attendees to opt in.'}`;
  hits.forEach((hit) => results.appendChild(renderHit(hit, viewMode !== 'keyword' && missed.has(hit.id))));
}

function chips(items) { return items.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join(''); }
function renderWhy(why) {
  const rows = [
    ['Exact terms (BM25)', why.lexical_terms, 'No exact word overlap: found by meaning only'],
    ['Shared concepts', why.concepts, 'No mapped concept overlap'],
    ['They offer what you need', why.they_offer_you_need, null],
    ['You offer what they need', why.you_offer_they_need, null],
    ['Shared areas with your card', why.shared_areas, null],
  ];
  return `${rows.filter(([, items, empty]) => items?.length || empty).map(([label, items, empty]) => `<div class="why-row"><span>${label}</span><div>${items?.length ? chips(items) : `<em>${empty}</em>`}</div></div>`).join('')}
    <div class="why-row"><span>Fields searched</span><div>${chips(why.fields_searched || [])}<em>Resume text, contacts and restricted vectors are never searched.</em></div></div>
    <div class="why-row"><span>Policy gates passed</span><div class="gates">${(why.policy_gates || []).map((gate) => `<code>${escapeHtml(gate)}</code>`).join('')}</div></div>`;
}

function renderHit(hit, conceptualOnly = false) {
  const node = $('#result-template').content.cloneNode(true);
  node.querySelector('.role').textContent = `${hit.roles.join(' · ')} · ${hit.retrieval_mode.replaceAll('_', ' ')}`;
  node.querySelector('.name').textContent = hit.display_name;
  node.querySelector('.headline').textContent = hit.headline || hit.availability;
  node.querySelector('.summary').textContent = hit.public_summary;
  const why = hit.explanation || {}, foundBy = why.found_by || [];
  node.querySelector('.found-by').innerHTML = ['keyword', 'semantic'].map((name) => `<span class="badge ${foundBy.includes(name) ? 'on' : ''}">${foundBy.includes(name) ? '●' : '○'} ${name}</span>`).join('') + (conceptualOnly ? '<span class="badge new">missed by keyword</span>' : '');
  node.querySelector('.why-body').innerHTML = renderWhy(why);
  const hue = field.colorFor(hit.id); if (hue) node.querySelector('.hue-bar').style.setProperty('--hue', hue);
  hit.match_reasons.forEach((reason) => { const li = document.createElement('li'); li.textContent = reason; node.querySelector('.reasons').appendChild(li); });
  node.querySelector('.connect').addEventListener('click', () => createConnection(hit).catch(showStatusError));
  node.querySelector('.locate').addEventListener('click', () => { switchTab('constellation'); field.focusId(hit.id); });
  if (conceptualOnly) node.querySelector('.match-card').classList.add('conceptual');
  if (hit.display_name.startsWith('DEMO · ')) {
    node.querySelector('.connect').disabled = true;
    node.querySelector('.connect').textContent = 'Synthetic card · introductions need real attendees';
  }
  return node;
}

async function createConnection(hit) {
  const message = window.prompt(`Short note to ${hit.display_name}:`, 'Want to connect about this project?');
  if (!message) return;
  const item = await post('/api/connection-requests', {requester_name:myProfile?.display_name || 'Attendee', recipient_profile_id:hit.id, message, preferred_channel:'meetup_qr'});
  field.link(hit.id, false);
  const node = document.createElement('article'); node.className = 'card';
  node.innerHTML = `<p class="eyebrow">Introduction requested · expires in 20 min</p><p class="subtle">The recipient must scan this code in the browser they used to join NETwork.</p><img alt="Recipient introduction QR" width="240" src="/api/connection-requests/${encodeURIComponent(item.token)}/qr.png"><button class="secondary">Check whether they accepted</button><p class="result" aria-live="polite"></p>`;
  node.querySelector('button').addEventListener('click', async () => {
    const result = node.querySelector('.result');
    try {
      const data = await api(`/api/connection-requests/${encodeURIComponent(item.token)}/contacts`);
      if (data.status === 'accepted') field.link(hit.id, true);
      result.textContent = data.status === 'accepted' ? `Accepted. Email: ${data.contacts?.email || 'Not shared'} · LinkedIn: ${data.contacts?.linkedin_url || 'Not shared'}` : 'They haven\'t accepted yet.';
    } catch (error) { result.textContent = error.message; }
  });
  $('#search-results').prepend(node);
}

function renderAnswer(answer, target) {
  target.classList.remove('hidden');
  const trace = answer.trace || [], people = [...new Set((answer.sources || []).map((s) => s.title.replace(/: conference profile$/, '').replace(/^DEMO · /, '')))];
  const status = answer.agent_status === 'ok' ? 'Elastic Agent Builder' : answer.agent_status === 'not_configured' ? 'Retrieval-only (agent not configured)' : answer.agent_status === 'unavailable' ? 'Retrieval-only (agent unavailable, failed closed)' : 'No eligible evidence';
  target.innerHTML = `<p class="eyebrow">${escapeHtml(answer.mode.replaceAll('_', ' '))} · ${escapeHtml(answer.retrieval_mode.replaceAll('_', ' '))}</p><h3>Concierge</h3><p>${escapeHtml(answer.answer || '')}</p>
    ${people.length ? `<p class="eyebrow">People discussing or building this</p><div class="people-row">${chips(people)}</div>` : ''}
    ${(answer.sources || []).map((source, index) => `<div class="source"><strong>[${index + 1}] ${escapeHtml(source.title)}</strong><br>${escapeHtml(source.excerpt || '')}</div>`).join('')}
    <details class="trace"><summary>Tool trace · ${trace.length} ${trace.length === 1 ? 'step' : 'steps'} · ${escapeHtml(status)}</summary>
      <ol>${trace.map((step) => `<li><div class="trace-head"><code>${escapeHtml(step.tool_id)}</code><span class="mono">${escapeHtml(step.type)}${step.results == null ? '' : ` · ${step.results} rows`}</span></div><pre>${escapeHtml(step.query)}</pre></li>`).join('')}</ol>
      <p class="subtle small">Queries shown are the server's verified tool definitions. Model-supplied parameters and raw rows are never returned to the browser.</p>
    </details>`;
}

async function loadConnection() {
  const token = encodeURIComponent(location.pathname.split('/').pop());
  $('#app-view').classList.add('hidden'); $('#connect-view').classList.remove('hidden');
  const card = $('#connection-card');
  try {
    const item = await api(`/api/connection-requests/${token}`);
    card.innerHTML = `<h2>${escapeHtml(item.requester_name)} wants to meet.</h2><p class="subtle">${escapeHtml(item.message)}</p><p class="subtle small">If you accept, this person will see the contact details you chose to share.</p><p><button class="primary" id="accept">Accept introduction</button></p><p id="accept-status" class="result" aria-live="polite"></p>`;
    $('#accept').addEventListener('click', async () => {
      try { await post(`/api/connection-requests/${token}/accept`, {}); $('#accept-status').textContent = 'Accepted. They can now see the contact details you shared.'; $('#accept').disabled = true; }
      catch (error) { $('#accept-status').textContent = error.message; }
    });
  } catch (error) { card.textContent = `${error.message}. Open this link in the browser you used to join this event. Scanning someone else's QR code doesn't sign you in.`; }
}
function escapeHtml(value) { const d = document.createElement('div'); d.textContent = value ?? ''; return d.innerHTML; }

/* ======================================================================
   Semantic field
   A 2-D projection of expertise. Each cluster has an anchor on a ring, and
   its colour is the angle of that anchor: hue is a function of position,
   not a category lookup. Nodes are placed by weighted keyword affinity,
   so a node between web and systems gets a blended yellow-cyan tint, and
   nodes near the generalist centre stay grey.
   Search remains the source of truth for matching; this is navigation.
   ====================================================================== */
const TAU = Math.PI * 2;
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const kwRe = (k) => new RegExp(`(?:^|[^a-z0-9])${escRe(k)}(?:$|[^a-z0-9])`, 'i');
const hashStr = (s) => { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; };
const seeded = (seed) => () => { seed = (seed + 0x6D2B79F5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
const lerp = (a, b, t) => a + (b - a) * t;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const easeInOut = (t) => (t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const RING = 0.62;

const CLUSTERS = [
  {label:'web', hue:50, keywords:['react','typescript','css','accessibility','frontend','javascript','web','next.js','ui','html','node']},
  {label:'product · design', hue:112, keywords:['design','ux','figma','product','research','prototyping','growth']},
  {label:'systems', hue:186, keywords:['kubernetes','observability','terraform','sre','infra','devops','cloud','latency','performance','opentelemetry','platform','docker','networking','k8s']},
  {label:'data', hue:236, keywords:['sql','spark','analytics','elasticsearch','data','pipelines','etl','search','warehouse','dbt']},
  {label:'ai · ml', hue:288, keywords:['llm','rag','embeddings','pytorch','ml','machine learning','ai','mlops','nlp','vector','python']},
  {label:'security', hue:350, keywords:['privacy','iam','threat','compliance','security','auth','crypto']},
];
CLUSTERS.forEach((c) => { const a = c.hue * Math.PI / 180; c.ax = Math.cos(a) * RING; c.ay = Math.sin(a) * RING; c.cx = c.ax; c.cy = c.ay; c.res = c.keywords.map(kwRe); });

// Illustrative synthetic attendees. Real search hits are added as live nodes.
const SEED_NODES = [
  ['Mira Chen','python, pandas, analytics, sql'], ['Ari Morris','react, typescript, css, accessibility'],
  ['Sofia Park','kubernetes, terraform, sre'], ['Nia Brooks','ml, embeddings, pytorch'],
  ['Leo Grant','frontend, web performance, javascript'], ['Harper Quinn','observability, opentelemetry, latency'],
  ['Noor Diaz','data pipelines, spark, etl'], ['Jun Ito','design, figma, ux, prototyping'],
  ['Kiran Shah','security, iam, cloud'], ['Ember Cole','llm, rag, search, elasticsearch'],
  ['Tomás Vela','react, node, html'], ['Priya Nair','mlops, kubernetes, python'],
  ['Owen Hart','product, growth, analytics'], ['Lena Fischer','privacy, compliance, data'],
  ['Sam Okafor','devops, docker, cloud'], ['Yuki Sato','nlp, llm, vector search'],
  ['Rae Lindqvist','ux research, accessibility, html'], ['Dev Malhotra','sql, dbt, warehouse'],
  ['Isla Moreau','next.js, typescript, design'], ['Cyrus Bell','threat modeling, auth, crypto'],
  ['Hana Kim','platform, networking, sre'], ['Felix Adeyemi','ai, product, prototyping'],
  ['Zara Holt','elasticsearch, observability, data'], ['Marco Ruiz','javascript, ui, figma'],
  ['Atlas Labs','kubernetes, observability, cloud','sponsor'], ['Northstar','ai, llm, rag','sponsor'],
  ['Lumen Data','analytics, warehouse, spark','sponsor'],
];

function hueAt(x, y, t = 0) { return ((Math.atan2(y, x) * 180 / Math.PI + 360) % 360) + 5 * Math.sin(t / 7000 + Math.hypot(x, y) * 5); }
function satAt(x, y) { return clamp(Math.hypot(x, y) / 0.5, 0, 1); }
function tint(x, y, l, a, t = 0, s = 1) { return `hsla(${hueAt(x, y, t).toFixed(1)},${Math.min(100, satAt(x, y) * 62 * s).toFixed(0)}%,${l}%,${a.toFixed(3)})`; }
function embed(text) { return CLUSTERS.map((c) => c.res.reduce((n, re) => n + (re.test(text) ? 1 : 0), 0)); }
function cosine(a, b) { let d = 0, na = 0, nb = 0; a.forEach((v, i) => { d += v * b[i]; na += v * v; nb += b[i] * b[i]; }); return na && nb ? d / Math.sqrt(na * nb) : 0; }
function jaccard(a, b) { const A = new Set(a), B = new Set(b); let i = 0; A.forEach((v) => { if (B.has(v)) i++; }); return A.size + B.size ? i / (A.size + B.size - i) : 0; }

function createField() {
  const canvas = $('#constellation-canvas'), stage = $('#constellation-stage'), ctx = canvas.getContext('2d');
  const caption = $('#constellation-caption'), legend = $('#cluster-legend'), zoomOut = $('#zoom-readout'), teleOut = $('#telemetry-readout');
  const MONO = '"JetBrains Mono", "SF Mono", ui-monospace, Menlo, Consolas, monospace', SANS = 'Inter, ui-sans-serif, system-ui, sans-serif';
  let W = 1, H = 1, S = 1, dpr = 1;
  const cam = {x:0, y:0, z:1};
  let flight = null, nodes = [], edges = [], edgeMap = new Map(), adj = new Map();
  const links = new Map();
  const byId = new Map();
  let sel = null, hov = null, packets = [], flashes = [], spawnLog = [], lastReq = '';
  let visible = false, raf = null, last = performance.now(), selAcc = 0, pending = null, firstShow = true, hudTimer = 0;
  const clusterHeat = CLUSTERS.map(() => 0);

  legend.innerHTML = CLUSTERS.map((c, i) => `<span data-i="${i}"><b style="color:hsl(${c.hue} 60% 66%)"></b>${c.label}</span>`).join('');

  function makeNode({id, name, kind, tags = [], text = ''}) {
    const full = `${tags.join(' , ')} ${text}`.toLowerCase(), emb = embed(full), total = emb.reduce((a, b) => a + b, 0), r = seeded(hashStr(id));
    let x = 0, y = 0;
    if (total) CLUSTERS.forEach((c, i) => { x += c.ax * emb[i] / total; y += c.ay * emb[i] / total; });
    x += (r() - .5) * .18; y += (r() - .5) * .18;
    return {id, name, kind, tags:tags.map((t) => t.toLowerCase()), text:full, emb, x, y, cluster: total ? emb.indexOf(Math.max(...emb)) : -1, heat:0, born:performance.now()};
  }
  function addNode(n) { const old = byId.get(n.id); if (old) nodes = nodes.filter((m) => m !== old); nodes.push(n); byId.set(n.id, n); return n; }
  function removeNode(id) { const n = byId.get(id); if (!n) return; nodes = nodes.filter((m) => m !== n); byId.delete(id); if (sel === n) clearSelection(); if (hov === n) hov = null; }

  function relax(iter, only = null) {
    for (let it = 0; it < iter; it++) for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j]; if (only && !only.has(a) && !only.has(b)) continue;
      let dx = b.x - a.x, dy = b.y - a.y; const d = Math.hypot(dx, dy) || 1e-4, min = a.kind === 'concept' || b.kind === 'concept' ? .07 : .09;
      if (d >= min) continue;
      dx /= d; dy /= d; const am = !only || only.has(a), bm = !only || only.has(b), f = (min - d) / (am && bm ? 2 : 1);
      if (am) { a.x -= dx * f; a.y -= dy * f; } if (bm) { b.x += dx * f; b.y += dy * f; }
    }
  }
  function centroids() {
    CLUSTERS.forEach((c, i) => { const m = nodes.filter((n) => n.cluster === i && n.kind !== 'query'); c.cx = m.length ? m.reduce((s, n) => s + n.x, 0) / m.length : c.ax; c.cy = m.length ? m.reduce((s, n) => s + n.y, 0) / m.length : c.ay; });
  }
  function buildEdges() {
    edges = []; edgeMap.clear(); adj = new Map(nodes.map((n) => [n, new Set()]));
    const add = (a, b, type) => { if (!a || !b || a === b) return; const k = a.id < b.id ? `${a.id}|${b.id}` : `${b.id}|${a.id}`; if (edgeMap.has(k)) { if (type === 'link') edgeMap.get(k).type = 'link'; return; } const e = {a, b, type, heat:0}; edges.push(e); edgeMap.set(k, e); adj.get(a).add(b); adj.get(b).add(a); };
    const concepts = nodes.filter((n) => n.kind === 'concept'), agents = nodes.filter((n) => n.kind !== 'concept' && n.kind !== 'query');
    agents.forEach((n) => concepts.filter((c) => c.re.test(n.text)).slice(0, 3).forEach((c) => add(n, c, 'concept')));
    agents.forEach((n) => agents.filter((m) => m !== n)
      .map((m) => ({m, s: cosine(n.emb, m.emb) + jaccard(n.tags, m.tags) - Math.hypot(n.x - m.x, n.y - m.y) * .8}))
      .sort((p, q) => q.s - p.s).slice(0, 2).forEach(({m}) => add(n, m, 'peer')));
    const q = byId.get('query'); if (q) q.hits.forEach((id) => { const h = byId.get(id); if (h) add(q, h, 'query'); });
    links.forEach((accepted, id) => add(byId.get('you'), byId.get(id), 'link'));
  }
  function rebuild(newNodes) {
    if (newNodes) relax(40, new Set(newNodes)); buildEdges(); centroids();
    $('#node-options').innerHTML = nodes.filter((n) => n.kind !== 'query').map((n) => `<option value="${escapeHtml(n.name)}"></option>`).join('');
  }

  CLUSTERS.forEach((c, ci) => c.keywords.slice(0, 4).forEach((kw, k) => {
    const n = makeNode({id:`c:${kw}`, name:kw, kind:'concept', text:kw}), a = c.hue * Math.PI / 180 + (k - 1.5) * .42, r = RING + (k % 2 ? .17 : -.13);
    n.x = Math.cos(a) * r; n.y = Math.sin(a) * r; n.cluster = ci; n.re = kwRe(kw); addNode(n);
  }));
  SEED_NODES.forEach(([name, tags, kind]) => addNode(makeNode({id:`s:${name}`, name, kind:kind || 'person', tags:splitTags(tags)})));
  relax(90); rebuild();

  // Camera
  const toScreen = (x, y) => ({x:(x - cam.x) * S * cam.z + W / 2, y:(y - cam.y) * S * cam.z + H / 2});
  const toWorld = (sx, sy) => ({x:(sx - W / 2) / (S * cam.z) + cam.x, y:(sy - H / 2) / (S * cam.z) + cam.y});
  function flyTo(x, y, z, dur = 1100) {
    z = clamp(z, .5, 8);
    if (reduceMotion || !visible) { cam.x = x; cam.y = y; cam.z = z; flight = null; return; }
    const d = Math.hypot(x - cam.x, y - cam.y) * Math.max(cam.z, z);
    flight = {from:{...cam}, to:{x, y, z}, t0:performance.now(), dur: dur + Math.min(500, d * 220), dip: clamp((d - .25) * .3, 0, .55)};
  }
  function stepCamera(now) {
    if (!flight) return;
    const p = clamp((now - flight.t0) / flight.dur, 0, 1), e = easeInOut(p);
    cam.x = lerp(flight.from.x, flight.to.x, e); cam.y = lerp(flight.from.y, flight.to.y, e);
    cam.z = Math.exp(lerp(Math.log(flight.from.z), Math.log(flight.to.z), e)) * (1 - flight.dip * Math.sin(Math.PI * p));
    if (p >= 1) flight = null;
  }
  function zoomAt(sx, sy, z) { const w = toWorld(sx, sy); cam.z = clamp(z, .5, 8); cam.x = w.x - (sx - W / 2) / (S * cam.z); cam.y = w.y - (sy - H / 2) / (S * cam.z); }
  function fit(list, dur) {
    if (!list.length) return;
    const xs = list.map((n) => n.x), ys = list.map((n) => n.y), hw = (Math.max(...xs) - Math.min(...xs)) / 2 + .12, hh = (Math.max(...ys) - Math.min(...ys)) / 2 + .12;
    flyTo((Math.max(...xs) + Math.min(...xs)) / 2, (Math.max(...ys) + Math.min(...ys)) / 2, clamp(Math.min(W / 2 / (S * hw), H / 2 / (S * hh)) * .85, .8, 3.4), dur);
  }

  // Traffic
  function spawn(a, b, {strong = false, delay = 0, dur} = {}) {
    if (!a || !b || a === b) return;
    if (!visible) { b.heat = Math.min(1.6, b.heat + .3); if (b.cluster >= 0) clusterHeat[b.cluster] += .12; return; }
    const k = a.id < b.id ? `${a.id}|${b.id}` : `${b.id}|${a.id}`, now = performance.now();
    packets.push({a, b, e:edgeMap.get(k), strong, t0:now + delay, dur: dur || 420 + Math.hypot(a.x - b.x, a.y - b.y) * 760});
    spawnLog.push(now + delay);
  }
  function burst(origin, count = 4, strong = false) {
    const nb = [...(adj.get(origin) || [])].sort(() => Math.random() - .5).slice(0, count);
    nb.forEach((m, i) => spawn(origin, m, {strong, delay:i * 70}));
    flashes.push({n:origin, t0:performance.now(), strong});
  }
  function arrive(pk, now) {
    flashes.push({n:pk.b, t0:now, strong:pk.strong});
    pk.b.heat = Math.min(1.6, pk.b.heat + (pk.strong ? .8 : .5));
    if (pk.b.cluster >= 0) clusterHeat[pk.b.cluster] = Math.min(3, clusterHeat[pk.b.cluster] + (pk.strong ? .3 : .12));
  }

  // Selection
  const KIND = {person:'PEER', sponsor:'SPONSOR', concept:'CONCEPT', live:'MATCH', you:'YOU', query:'QUERY'};
  function focus(n, zoom) {
    sel = n; selAcc = 0;
    flyTo(n.x, n.y, zoom || (n.kind === 'concept' ? 2.4 : 3.1));
    const nb = [...adj.get(n)], people = nb.filter((m) => m.kind !== 'concept' && m.kind !== 'query').map((m) => m.name), concepts = nb.filter((m) => m.kind === 'concept').map((m) => m.name);
    const area = n.cluster >= 0 ? CLUSTERS[n.cluster].label : 'generalist';
    const detail = n.kind === 'query' ? `${n.hits.length} matched cards` : concepts.join(' · ') || n.tags.slice(0, 4).join(' · ');
    caption.innerHTML = `<span>${KIND[n.kind]}</span><p><strong>${escapeHtml(n.name)}</strong> · ${escapeHtml(area)}<br><em>${escapeHtml(detail)}</em>${people.length ? `<br>${people.length} linked · ${escapeHtml(people.slice(0, 3).join(', '))}${people.length > 3 ? '…' : ''}` : ''}</p>`;
    legend.querySelectorAll('span').forEach((s) => s.classList.toggle('on', +s.dataset.i === n.cluster));
    setTimeout(() => burst(n, 8, true), reduceMotion ? 0 : 420);
  }
  function clearSelection() {
    sel = null; caption.innerHTML = '<span>—</span><p>Click a node to zoom in and see its connections.</p>';
    legend.querySelectorAll('span').forEach((s) => s.classList.remove('on'));
  }
  function reset() { clearSelection(); flyTo(0, 0, 1); }
  function pick(sx, sy) {
    let best = null, bd = Infinity;
    nodes.forEach((n) => { const p = toScreen(n.x, n.y), d = Math.hypot(p.x - sx, p.y - sy); if (d < bd) { bd = d; best = n; } });
    return bd < 16 ? best : null;
  }

  // Rendering
  function resize() {
    const b = stage.getBoundingClientRect(); if (!b.width) return;
    dpr = Math.min(window.devicePixelRatio || 1, 2); W = b.width; H = b.height; S = Math.min(W, H) * .46;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr); canvas.style.width = `${W}px`; canvas.style.height = `${H}px`;
  }
  function line(p, q) { ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y); ctx.stroke(); }
  function draw(now, dt) {
    const t = now;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = '#070707'; ctx.fillRect(0, 0, W, H);
    const focusSet = sel ? new Set([sel, ...adj.get(sel)]) : null;

    // Cluster light fields: centred on live centroids, intensity from recent traffic
    CLUSTERS.forEach((c, i) => {
      const p = toScreen(c.cx, c.cy), r = (.46 + .03 * Math.sin(t / 2600 + i * 1.7)) * S * cam.z;
      const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
      g.addColorStop(0, `hsla(${hueAt(c.cx, c.cy, t).toFixed(1)},70%,52%,${(.06 + Math.min(.1, clusterHeat[i] * .05)).toFixed(3)})`); g.addColorStop(1, 'hsla(0,0%,0%,0)');
      ctx.fillStyle = g; ctx.fillRect(p.x - r, p.y - r, r * 2, r * 2);
    });

    // World grid (moves with the camera, so zoom reads as depth)
    const step = cam.z > 3.5 ? .05 : cam.z > 1.6 ? .1 : .2, tl = toWorld(0, 0), br = toWorld(W, H);
    ctx.fillStyle = 'rgba(255,255,255,.07)';
    for (let gx = Math.ceil(tl.x / step) * step; gx <= br.x; gx += step) for (let gy = Math.ceil(tl.y / step) * step; gy <= br.y; gy += step) { const p = toScreen(gx, gy); ctx.fillRect(Math.round(p.x), Math.round(p.y), 1, 1); }
    const o = toScreen(0, 0); ctx.strokeStyle = 'rgba(255,255,255,.08)'; ctx.lineWidth = 1;
    line({x:o.x - 6, y:o.y}, {x:o.x + 6, y:o.y}); line({x:o.x, y:o.y - 6}, {x:o.x, y:o.y + 6});

    // Edges
    edges.forEach((e) => {
      e.heat *= Math.exp(-dt / 520);
      let a = e.type === 'concept' ? .05 : e.type === 'query' ? .3 : e.type === 'link' ? .6 : .1;
      if (focusSet) a = e.a === sel || e.b === sel ? .42 : e.type === 'link' ? .3 : .02;
      a += e.heat * .5;
      ctx.strokeStyle = tint((e.a.x + e.b.x) / 2, (e.a.y + e.b.y) / 2, 80, Math.min(.9, a), t, .7);
      ctx.lineWidth = (e.type === 'link' ? 1.4 : .6) + e.heat * .9; ctx.setLineDash(e.type === 'query' ? [2, 4] : e.type === 'link' && !links.get(e.a.id === 'you' ? e.b.id : e.a.id) ? [6, 4] : []);
      line(toScreen(e.a.x, e.a.y), toScreen(e.b.x, e.b.y));
    });
    ctx.setLineDash([]);

    // Packets & flashes, additive
    ctx.globalCompositeOperation = 'lighter'; ctx.lineCap = 'round';
    packets = packets.filter((pk) => {
      const p = (now - pk.t0) / pk.dur; if (p < 0) return true;
      if (p >= 1) { arrive(pk, now); return false; }
      if (pk.e) pk.e.heat = Math.max(pk.e.heat, pk.strong ? .9 : .55);
      const ep = easeInOut(p), tp = Math.max(0, ep - (pk.strong ? .3 : .2));
      const hx = lerp(pk.a.x, pk.b.x, ep), hy = lerp(pk.a.y, pk.b.y, ep), tx = lerp(pk.a.x, pk.b.x, tp), ty = lerp(pk.a.y, pk.b.y, tp);
      const hd = toScreen(hx, hy), tl2 = toScreen(tx, ty), g = ctx.createLinearGradient(tl2.x, tl2.y, hd.x, hd.y);
      g.addColorStop(0, tint(tx, ty, 70, 0, t, 1.4)); g.addColorStop(1, tint(hx, hy, 82, pk.strong ? .95 : .7, t, 1.5));
      ctx.strokeStyle = g; ctx.lineWidth = pk.strong ? 2.2 : 1.4; line(tl2, hd);
      const gr = pk.strong ? 16 : 10, rg = ctx.createRadialGradient(hd.x, hd.y, 0, hd.x, hd.y, gr);
      rg.addColorStop(0, tint(hx, hy, 85, pk.strong ? .8 : .55, t, 1.5)); rg.addColorStop(1, tint(hx, hy, 50, 0, t));
      ctx.fillStyle = rg; ctx.beginPath(); ctx.arc(hd.x, hd.y, gr, 0, TAU); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,.95)'; ctx.beginPath(); ctx.arc(hd.x, hd.y, pk.strong ? 1.7 : 1.2, 0, TAU); ctx.fill();
      return true;
    });
    flashes = flashes.filter((f) => {
      const p = (now - f.t0) / (f.strong ? 800 : 620); if (p >= 1) return false;
      const s = toScreen(f.n.x, f.n.y), r = 4 + easeInOut(p) * (f.strong ? 38 : 22);
      ctx.strokeStyle = tint(f.n.x, f.n.y, 86, (1 - p) * .75, t, 1.4); ctx.lineWidth = (1 - p) * 1.4 + .2;
      ctx.beginPath(); ctx.arc(s.x, s.y, r, 0, TAU); ctx.stroke();
      if (p < .3) { const cg = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, 18); cg.addColorStop(0, `rgba(255,255,255,${(.5 * (1 - p / .3)).toFixed(3)})`); cg.addColorStop(1, 'rgba(255,255,255,0)'); ctx.fillStyle = cg; ctx.beginPath(); ctx.arc(s.x, s.y, 18, 0, TAU); ctx.fill(); }
      return true;
    });
    ctx.globalCompositeOperation = 'source-over';

    // Nodes
    const zr = clamp(.75 + cam.z * .22, .8, 1.8);
    nodes.forEach((n) => {
      n.heat *= Math.exp(-dt / 1100);
      const s = toScreen(n.x, n.y); if (s.x < -40 || s.y < -40 || s.x > W + 40 || s.y > H + 40) return;
      const dim = focusSet && !focusSet.has(n), appear = clamp((now - n.born) / 700, 0, 1);
      const r = ({person:3, sponsor:3.4, concept:2.2, live:3.4, you:3.6, query:4.5})[n.kind] * zr * (.3 + .7 * easeInOut(appear)) * (1 + n.heat * .3);
      const A = dim ? .16 : 1;
      if (!dim && n.kind !== 'concept') {
        const hr = r * 4 + n.heat * 16 + (n === sel ? 16 : 0), g = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, hr);
        g.addColorStop(0, tint(n.x, n.y, 70, Math.min(.6, .14 + n.heat * .22 + (n === sel ? .2 : 0)), t, 1.2)); g.addColorStop(1, tint(n.x, n.y, 40, 0, t));
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(s.x, s.y, hr, 0, TAU); ctx.fill();
      }
      ctx.fillStyle = tint(n.x, n.y, 84, A, t, .9); ctx.strokeStyle = tint(n.x, n.y, 84, A, t, .9); ctx.lineWidth = 1;
      if (n.kind === 'sponsor') { ctx.save(); ctx.translate(s.x, s.y); ctx.rotate(Math.PI / 4); ctx.fillRect(-r * .8, -r * .8, r * 1.6, r * 1.6); ctx.restore(); }
      else if (n.kind === 'concept') { ctx.strokeStyle = tint(n.x, n.y, 62, A * .8, t, .8); ctx.strokeRect(s.x - r, s.y - r, r * 2, r * 2); }
      else if (n.kind === 'live') { ctx.beginPath(); ctx.arc(s.x, s.y, r + 1.5, 0, TAU); ctx.stroke(); ctx.beginPath(); ctx.arc(s.x, s.y, r * .45, 0, TAU); ctx.fill(); }
      else if (n.kind === 'query') {
        ctx.strokeStyle = `rgba(255,255,255,${A})`; ctx.beginPath(); ctx.arc(s.x, s.y, r, 0, TAU); ctx.stroke();
        line({x:s.x - r * 2.2, y:s.y}, {x:s.x - r * 1.3, y:s.y}); line({x:s.x + r * 1.3, y:s.y}, {x:s.x + r * 2.2, y:s.y}); line({x:s.x, y:s.y - r * 2.2}, {x:s.x, y:s.y - r * 1.3}); line({x:s.x, y:s.y + r * 1.3}, {x:s.x, y:s.y + r * 2.2});
      } else { ctx.beginPath(); ctx.arc(s.x, s.y, r, 0, TAU); ctx.fill(); }
      if (n.kind === 'you') { ctx.strokeStyle = `rgba(255,255,255,${(.5 * A).toFixed(2)})`; ctx.beginPath(); ctx.arc(s.x, s.y, r + 4 + (reduceMotion ? 0 : Math.sin(t / 420) * 1.5), 0, TAU); ctx.stroke(); }
      if (n === sel || n === hov) { ctx.strokeStyle = n === sel ? 'rgba(255,255,255,.85)' : 'rgba(255,255,255,.4)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(s.x, s.y, r + 6, 0, TAU); ctx.stroke(); }
      n._r = r;
    });

    // Labels
    ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
    nodes.forEach((n) => {
      if (focusSet && !focusSet.has(n)) return;
      const show = n === sel || n === hov || focusSet || ['you', 'query', 'live'].includes(n.kind) || (n.kind === 'concept' ? cam.z > 1.35 : cam.z > 1.9);
      if (!show) return;
      const s = toScreen(n.x, n.y); if (s.x < -10 || s.y < -10 || s.x > W + 10 || s.y > H + 10) return;
      if (n.kind === 'concept') { ctx.font = `400 10px ${MONO}`; ctx.fillStyle = tint(n.x, n.y, 70, .75, t, .6); }
      else { ctx.font = `${n === sel ? 600 : 500} ${n === sel ? 12.5 : 11}px ${SANS}`; ctx.fillStyle = n === sel ? '#fff' : 'rgba(225,225,225,.82)'; }
      ctx.fillText(n.name, s.x + (n._r || 3) + 7, s.y);
    });

    // Cluster labels fade out as you zoom in
    const la = clamp((2.1 - cam.z) / 1.1, 0, 1) * (focusSet ? .25 : .6);
    if (la > .01) {
      ctx.font = `500 10px ${MONO}`; ctx.textAlign = 'center';
      CLUSTERS.forEach((c) => { const d = Math.hypot(c.cx, c.cy) || 1, p = toScreen(c.cx / d * (d + .3), c.cy / d * (d + .3)); ctx.fillStyle = tint(c.cx, c.cy, 72, la, t, 1); ctx.fillText(c.label.toUpperCase().split('').join(' '), p.x, p.y); });
    }
  }
  function updateHud(now) {
    zoomOut.textContent = `${cam.z.toFixed(2)}×`;
    if (now - hudTimer < 250) return; hudTimer = now;
    spawnLog = spawnLog.filter((ts) => ts > now - 1000 && ts <= now);
    teleOut.textContent = `${String(spawnLog.length).padStart(2, '0')} pkt/s${lastReq ? `\n${lastReq}` : ''}`;
    teleOut.style.whiteSpace = 'pre';
  }
  function frame(now) {
    const dt = Math.min(64, now - last); last = now;
    stepCamera(now);
    if (!reduceMotion && Math.random() < dt * .0026 && edges.length) { const e = edges[Math.random() * edges.length | 0]; if (e.type !== 'query') Math.random() < .5 ? spawn(e.a, e.b) : spawn(e.b, e.a); }
    if (sel && !reduceMotion && (selAcc += dt) > 360) { selAcc = 0; const nb = [...adj.get(sel)]; if (nb.length) { const m = nb[Math.random() * nb.length | 0]; Math.random() < .6 ? spawn(sel, m, {strong:true}) : spawn(m, sel, {strong:true}); } }
    draw(now, dt); updateHud(now);
    raf = visible ? requestAnimationFrame(frame) : null;
  }

  // Interaction
  const pointers = new Map(); let drag = null, pinch = null;
  canvas.addEventListener('pointerdown', (e) => {
    canvas.setPointerCapture(e.pointerId); pointers.set(e.pointerId, {x:e.offsetX, y:e.offsetY}); flight = null;
    if (pointers.size === 1) drag = {x:e.offsetX, y:e.offsetY, moved:0};
    else if (pointers.size === 2) { const [a, b] = [...pointers.values()]; pinch = {d:Math.hypot(a.x - b.x, a.y - b.y) || 1, z:cam.z}; drag = null; }
  });
  canvas.addEventListener('pointermove', (e) => {
    const pt = {x:e.offsetX, y:e.offsetY}; if (pointers.has(e.pointerId)) pointers.set(e.pointerId, pt);
    if (pinch && pointers.size === 2) { const [a, b] = [...pointers.values()]; zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, pinch.z * Math.hypot(a.x - b.x, a.y - b.y) / pinch.d); }
    else if (drag) { const dx = pt.x - drag.x, dy = pt.y - drag.y; drag.moved += Math.abs(dx) + Math.abs(dy); cam.x -= dx / (S * cam.z); cam.y -= dy / (S * cam.z); drag.x = pt.x; drag.y = pt.y; if (drag.moved > 4) canvas.classList.add('dragging'); }
    else { hov = pick(pt.x, pt.y); canvas.classList.toggle('hovering', !!hov); }
  });
  const release = (e) => {
    pointers.delete(e.pointerId);
    if (drag && drag.moved < 5 && e.type === 'pointerup') { const n = pick(e.offsetX, e.offsetY); n ? focus(n) : sel && clearSelection(); }
    drag = null; if (pointers.size < 2) pinch = null; canvas.classList.remove('dragging');
  };
  canvas.addEventListener('pointerup', release); canvas.addEventListener('pointercancel', release);
  canvas.addEventListener('pointerleave', () => { hov = null; canvas.classList.remove('hovering'); });
  canvas.addEventListener('wheel', (e) => { e.preventDefault(); flight = null; zoomAt(e.offsetX, e.offsetY, cam.z * Math.exp(-e.deltaY * .0015)); }, {passive:false});
  new ResizeObserver(() => { if (visible) resize(); }).observe(stage);

  // Ambient page field: a tiny canvas stretched behind the UI, coloured by the same centroids
  const fc = $('#field'), fx = fc.getContext('2d'); fc.width = 64; fc.height = 40; let lastAmb = 0;
  function ambient(now) {
    requestAnimationFrame(ambient);
    const dt = now - lastAmb; if (dt < 90) return; lastAmb = now;
    clusterHeat.forEach((h, i) => { clusterHeat[i] = h * Math.exp(-dt / 2600); });
    fx.clearRect(0, 0, 64, 40);
    const rot = reduceMotion ? 0 : now * .000018 + .6, focusHue = sel && sel.cluster >= 0 ? sel.cluster : -1;
    CLUSTERS.forEach((c, i) => {
      const x = c.cx * Math.cos(rot) - c.cy * Math.sin(rot), y = c.cx * Math.sin(rot) + c.cy * Math.cos(rot);
      const px = 32 + x * 40, py = 20 + y * 26, r = 20 + Math.min(10, clusterHeat[i] * 4) + (i === focusHue ? 6 : 0);
      const a = .07 + Math.min(.2, clusterHeat[i] * .07) + (i === focusHue ? .08 : 0);
      const g = fx.createRadialGradient(px, py, 0, px, py, r);
      g.addColorStop(0, `hsla(${hueAt(c.cx, c.cy, now).toFixed(1)},60%,48%,${a.toFixed(3)})`); g.addColorStop(1, 'hsla(0,0%,0%,0)');
      fx.fillStyle = g; fx.fillRect(0, 0, 64, 40);
    });
  }
  requestAnimationFrame(ambient);

  function findNode(query) {
    const q = query.trim().toLowerCase(); if (!q) return null;
    const list = nodes.filter((n) => n.kind !== 'query');
    return list.find((n) => n.name.toLowerCase() === q) || list.find((n) => n.name.toLowerCase().startsWith(q)) || list.find((n) => n.name.toLowerCase().includes(q) || n.tags.includes(q));
  }

  return {
    setVisible(v) {
      visible = v;
      if (!v) return;
      resize();
      if (firstShow && !pending) { firstShow = false; cam.z = .55; flyTo(0, 0, 1, 1300); }
      if (pending) { const p = pending; pending = null; firstShow = false; fit(p.list, 900); setTimeout(p.replay, reduceMotion ? 0 : 700); }
      if (!raf) { last = performance.now(); raf = requestAnimationFrame(frame); }
    },
    telemetry(method, path, ms, status) {
      lastReq = `${method} ${path.replace(/\/connection-requests\/[^/]+/, '/connection-requests/:token').split('?')[0]} · ${Math.round(ms)}ms${status >= 400 ? ` · ${status}` : ''}`;
      const agents = nodes.filter((n) => n.kind !== 'concept' && n.kind !== 'query');
      const origin = byId.get('you') || sel || agents[Math.random() * agents.length | 0];
      if (origin) burst(origin, method === 'GET' ? 2 : 5, method !== 'GET');
    },
    showQuery(question, hits) {
      removeNode('query');
      const live = hits.map((h) => addNode(makeNode({id:`p:${h.id}`, name:h.display_name.replace(/^DEMO · /, ''), kind:'live', tags:h.can_help_with || [], text:`${h.headline} ${h.public_summary}`})));
      const q = addNode(makeNode({id:'query', name:question.length > 34 ? `${question.slice(0, 33)}…` : question, kind:'query', text:question}));
      q.hits = live.map((n) => n.id);
      rebuild([q, ...live]);
      const replay = () => { flashes.push({n:q, t0:performance.now(), strong:true}); live.forEach((n, i) => spawn(q, n, {strong:true, delay:i * 160, dur:900})); };
      if (visible) { clearSelection(); fit([q, ...live]); setTimeout(replay, reduceMotion ? 0 : 500); }
      else pending = {list:[q, ...live], replay};
    },
    colorFor(id) { const n = byId.get(`p:${id}`); return n ? `hsl(${hueAt(n.x, n.y).toFixed(0)} ${(satAt(n.x, n.y) * 55).toFixed(0)}% 62%)` : null; },
    focusId(id) { const n = byId.get(`p:${id}`); if (!n) return; pending = null; firstShow = false; focus(n); },
    find(query) { const n = findNode(query); if (n) focus(n); return !!n; },
    setYou(profile) {
      if (!profile) { removeNode('you'); rebuild(); return; }
      const joined = !byId.has('you');
      const n = addNode(makeNode({id:'you', name:`${profile.display_name} (you)`, kind:'you', tags:profile.can_help_with || [], text:`${profile.headline || ''} ${profile.public_summary || ''}`}));
      rebuild([n]);
      if (joined) burst(n, 6, true);  // a join pings the neighbourhood
    },
    link(id, accepted) {
      // Introduction requested: dashed link. Accepted: solid, with traffic both ways.
      const you = byId.get('you'), peer = byId.get(`p:${id}`);
      if (!you || !peer || links.get(`p:${id}`) === true) return;
      links.set(`p:${id}`, accepted); rebuild();
      const pulses = accepted ? 6 : 3;
      for (let i = 0; i < pulses; i++) { spawn(you, peer, {strong:true, delay:i * 220, dur:1000}); if (accepted) spawn(peer, you, {strong:true, delay:i * 220 + 110, dur:1000}); }
      flashes.push({n:peer, t0:performance.now(), strong:true});
      if (visible) fit([you, peer], 900);
    },
    reset,
    isVisible: () => visible,
  };
}
const field = createField();

$('#recenter-constellation').addEventListener('click', () => field.reset());
const nodeSearch = $('#node-search');
const goToNode = () => { if (field.find(nodeSearch.value)) nodeSearch.select(); };
nodeSearch.addEventListener('change', goToNode);
nodeSearch.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); goToNode(); } });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && field.isVisible()) field.reset(); });

/* ======================================================================
   Trust center: what is held, where, under which gates, for how long.
   ====================================================================== */
const trustDrawer = $('#trust-drawer');
let trustReturnFocus = null;
const fmtTime = (iso) => iso ? new Date(iso).toLocaleString([], {weekday:'short', hour:'numeric', minute:'2-digit'}) : '—';
async function openTrust() {
  trustReturnFocus = document.activeElement;
  trustDrawer.hidden = false; requestAnimationFrame(() => trustDrawer.classList.add('open'));
  trustDrawer.querySelector('[data-close].icon-button').focus();
  const body = $('#trust-body'); body.textContent = 'Loading…';
  try {
    const t = await api('/api/trust'), card = t.card;
    const consent = !card ? ['none', 'No card yet', 'Nothing about you is indexed.']
      : card.consent_to_discover ? ['on', 'Discoverable', `Until ${fmtTime(card.discoverable_until)} · approval v${card.approval_version} · ${card.state}`]
      : ['off', 'Withdrawn', 'Your card, evidence and contacts were removed; tombstones block late writes.'];
    body.innerHTML = `
      <section class="consent-state ${consent[0]}"><span class="pulse" aria-hidden="true"></span><div><strong>${consent[1]}</strong><p>${escapeHtml(consent[2])}</p></div></section>
      <h3 class="eyebrow">Where your data lives</h3>
      <ul class="stores">${t.stores.map((st) => `<li class="${st.present ? 'present' : ''}">
        <div class="store-head"><strong>${escapeHtml(st.label)}</strong><span class="access ${st.access}">${escapeHtml(st.access)}</span><span class="held mono">${st.present ? 'held' : 'not held'}</span></div>
        ${st.index ? `<code>${escapeHtml(st.index)}</code>` : ''}<p>${escapeHtml(st.detail)}</p></li>`).join('')}</ul>
      <h3 class="eyebrow">Policy gates on every search, answer and agent tool</h3>
      <div class="gates">${t.policy_gates.map((g) => `<code>${escapeHtml(g)}</code>`).join('')}</div>
      <p class="subtle small">The same filters run in the BM25 branch, the semantic branch, the lexical fallback and the ES|QL agent tools. Sponsors only appear when you ask for them.</p>
      <h3 class="eyebrow">Retention</h3>
      <dl class="retention">
        <div><dt>${t.retention.resume_preview_minutes} min</dt><dd>Resume preview draft</dd></div>
        <div><dt>${t.retention.card_hours} h</dt><dd>Card discoverable</dd></div>
        <div><dt>${t.retention.introduction_minutes} min</dt><dd>Introduction QR</dd></div>
        <div><dt>${t.retention.session_hours} h</dt><dd>Browser session</dd></div>
      </dl>
      ${card && card.consent_to_discover ? '<button class="ghost" type="button" id="trust-withdraw">Withdraw card and contacts now</button>' : ''}`;
    $('#trust-withdraw')?.addEventListener('click', async () => { $('#revoke-card').click(); setTimeout(openTrust, 600); });
  } catch (error) { body.textContent = error.message; }
}
function closeTrust() {
  trustDrawer.classList.remove('open');
  setTimeout(() => { trustDrawer.hidden = true; }, reduceMotion ? 0 : 220);
  trustReturnFocus?.focus?.();
}
$('#open-trust').addEventListener('click', () => openTrust());
trustDrawer.querySelectorAll('[data-close]').forEach((el) => el.addEventListener('click', closeTrust));
trustDrawer.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { e.stopPropagation(); closeTrust(); }
  if (e.key === 'Tab') {
    const f = [...trustDrawer.querySelectorAll('button, [href], input, [tabindex]:not([tabindex="-1"])')].filter((el) => !el.disabled && el.offsetParent);
    if (!f.length) return;
    if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
  }
});

/* ======================================================================
   Operations command center (organizer). Aggregates only.
   ====================================================================== */
function createOps() {
  let token = '', timer = null, active = false;
  const body = $('#ops-body'), status = $('#ops-status'), auth = $('#ops-auth');
  const ago = (iso) => { if (!iso) return '—'; const s = Math.max(0, (Date.now() - new Date(iso)) / 1000); return s < 60 ? `${Math.round(s)}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`; };
  const bars = (items) => {
    if (!items.length) return '<p class="subtle small">No data yet.</p>';
    const max = Math.max(...items.map((i) => i.count));
    return `<ul class="bars">${items.map((i) => `<li><span>${escapeHtml(i.label)}</span><b style="--w:${(i.count / max * 100).toFixed(1)}%"></b><em class="mono">${i.count}</em></li>`).join('')}</ul>`;
  };
  const QUEUE = ['queued', 'processing', 'retry', 'indexed', 'failed', 'revoked'];
  function render(d) {
    const q = d.queue, c = d.consent, x = d.connections, totalJobs = Object.values(q.by_state).reduce((a, b) => a + b, 0) || 1;
    const tiles = [
      ['Discoverable now', c.discoverable, `${c.by_state.revoked || 0} withdrawn · ${c.awaiting_sweep} awaiting expiry sweep`],
      ['Jobs processed', q.by_state.indexed || 0, `${q.attempts} attempts · ${q.retried} retried`],
      ['Queue depth', (q.by_state.queued || 0) + (q.by_state.processing || 0) + (q.by_state.retry || 0), q.oldest_pending_seconds ? `oldest ${q.oldest_pending_seconds}s` : 'nothing waiting'],
      ['Failed', q.by_state.failed || 0, Object.entries(q.errors).map(([k, v]) => `${k} ×${v}`).join(' · ') || 'no error codes'],
      ['Introductions', x.total, `${x.by_status.accepted || 0} accepted${x.acceptance_rate == null ? '' : ` · ${Math.round(x.acceptance_rate * 100)}%`}`],
      ['Freshest card', ago(d.freshness.latest_card), `last job ${ago(d.freshness.latest_job)}`],
    ];
    body.innerHTML = `
      <div class="tiles">${tiles.map(([label, value, sub]) => `<div class="tile"><span class="eyebrow">${label}</span><strong>${escapeHtml(String(value))}</strong><small>${escapeHtml(sub)}</small></div>`).join('')}</div>
      <h3 class="eyebrow">Ingestion queue</h3>
      <div class="queue-bar" role="img" aria-label="${QUEUE.map((k) => `${k} ${q.by_state[k] || 0}`).join(', ')}">${QUEUE.filter((k) => q.by_state[k]).map((k) => `<span class="q-${k}" style="flex:${q.by_state[k] / totalJobs}" title="${k}: ${q.by_state[k]}"></span>`).join('')}</div>
      <p class="legend mono">${QUEUE.map((k) => `<span class="q-${k}"><i></i>${k} ${q.by_state[k] || 0}</span>`).join('')}</p>
      <div class="grid-2 ops-grid">
        <section><h3 class="eyebrow">What attendees offer</h3>${bars(d.impact.offers)}</section>
        <section><h3 class="eyebrow">What attendees are looking for</h3>${bars(d.impact.seeks)}</section>
        <section><h3 class="eyebrow">Emerging clusters</h3>${bars(d.impact.clusters)}</section>
        <section><h3 class="eyebrow">Introductions by status</h3>${bars(Object.entries(x.by_status).map(([label, count]) => ({label, count})))}</section>
      </div>
      <h3 class="eyebrow">Indices · ${escapeHtml(d.engine === 'memory_demo' ? 'memory demo' : 'Elasticsearch')} · ${escapeHtml(d.inference_id)}</h3>
      <table class="indices"><thead><tr><th>Alias</th><th>Access</th><th>Docs</th></tr></thead>
        <tbody>${d.indices.map((i) => `<tr><td><code>${escapeHtml(i.name)}</code></td><td><span class="access ${i.access}">${i.access}</span></td><td class="mono">${i.docs}</td></tr>`).join('')}</tbody></table>`;
    $('#ops-meta').textContent = `${d.event_id} · live`;
  }
  async function load() {
    try {
      const response = await fetch('/api/ops/summary', {headers: token ? {'X-NETwork-Ops-Token': token} : {}});
      const data = await response.json();
      if (response.status === 401) { auth.classList.remove('hidden'); body.classList.add('hidden'); status.textContent = token ? 'Token rejected.' : 'Enter the organizer token to view operations.'; stop(); return; }
      if (!response.ok) throw new Error(data.detail || 'Operations data unavailable');
      auth.classList.add('hidden'); body.classList.remove('hidden');
      status.textContent = `Updated ${new Date(data.generated_at).toLocaleTimeString()} · refreshes every 5 s`;
      render(data);
    } catch (error) { status.textContent = error.message; }
  }
  function stop() { clearInterval(timer); timer = null; }
  auth.addEventListener('submit', (e) => { e.preventDefault(); token = $('#ops-token').value; $('#ops-token').value = ''; load(); if (active && !timer) timer = setInterval(load, 5000); });
  return {
    setVisible(v) { active = v; stop(); if (v) { load(); timer = setInterval(load, 5000); } },
  };
}
const ops = createOps();

async function boot() {
  try {
    const session = await post('/api/session', {});
    $('#runtime-status').textContent = session.mode === 'memory_demo' ? 'demo · in-memory lexical search · data resets on restart' : `event ${session.event_id} · elastic connected`;
    if (session.mode === 'memory_demo') $('#seed-demo').classList.remove('hidden');
    await refreshMe();
    if (location.pathname.startsWith('/connect/')) await loadConnection();
  } catch (error) { showStatusError(error); }
}
boot();
