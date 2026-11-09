const $ = (selector) => document.querySelector(selector);
const splitTags = (value) => value.split(',').map((item) => item.trim()).filter(Boolean);
const selected = (name, root = document) => [...root.querySelectorAll(`input[name="${name}"]:checked`)].map((input) => input.value);
let draftId = null, previewGeneration = 0, myProfile = null, myState = null, refreshTimer = null;
// SynthDemo: the administratively seeded synthetic cohort. Viewers can hide it everywhere; introductions to it are disabled.
const SYNTH = 'SynthDemo';
let showSynth = false;
try { showSynth = localStorage.getItem('network.showSynth') === '1'; } catch { /* storage unavailable: real attendees only */ }
const isSynthetic = (hit) => hit?.dataset === SYNTH || (hit?.display_name || '').startsWith('DEMO · ');
const cleanName = (name) => (name || '').replace(/^DEMO · /, '');

async function api(path, options = {}) {
  const started = performance.now();
  let response = await fetch(path, {...options, headers:{'X-NETwork-Request':'1', ...(options.headers || {})}});
  if (response.status === 401 && path !== '/api/session') {
    // No session cookie yet (first request raced boot) or it expired: start one and retry once.
    await sessionReady();
    response = await fetch(path, {...options, headers:{'X-NETwork-Request':'1', ...(options.headers || {})}});
  }
  field.telemetry(options.method || 'GET', path, performance.now() - started, response.status);
  if (response.status === 204) return null;
  const data = await response.json();
  if (!response.ok) throw Object.assign(new Error(typeof data.detail === 'string' ? data.detail : 'The request could not be completed.'), {status: response.status});
  return data;
}
let sessionPromise = null;
function sessionReady() {
  sessionPromise ??= fetch('/api/session', {method:'POST', headers:{'X-NETwork-Request':'1'}}).then((r) => r.json()).finally(() => { setTimeout(() => { sessionPromise = null; }, 1000); });
  return sessionPromise;
}
const post = (path, body) => api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});

function switchTab(name) {
  document.querySelectorAll('.tab,.panel').forEach((el) => el.classList.remove('active'));
  document.querySelector(`.tab[data-panel="${name}"]`)?.classList.add('active');
  $(`#${name}`).classList.add('active');
  $('.shell').dataset.panel = name;
  field.setVisible(name === 'constellation');
  g3.setVisible(name === 'network3d');
  ego.setVisible(name === 'mynet');
  graphFeed.setActive(['constellation', 'network3d', 'mynet'].includes(name));
  liveFeed.setActive(['constellation', 'elastic'].includes(name));
  stack.setVisible(name === 'elastic');
  ops.setVisible(name === 'ops');
  if (name === 'people' && !people.loaded) runPeople();
}
document.querySelectorAll('.tab').forEach((tab) => tab.addEventListener('click', () => switchTab(tab.dataset.panel)));

async function refreshMe() {
  const data = await api('/api/me');
  if (data.state !== myState) people.loaded = false;  // card became searchable (or was withdrawn): re-rank on next visit
  myState = data.state;
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
  const missing = (message, el) => { result.textContent = message; el?.focus(); };
  if ((fd.get('display_name') || '').trim().length < 2) return missing('Display name is required (at least 2 characters).', form.elements.display_name);
  if (!selected('formats', form).length) return missing('Pick at least one connection style.', form.querySelector('input[name="formats"]'));
  if (fd.get('consent_to_discover') !== 'on') return missing('Tick the discovery consent box to join. It is the only way other attendees can find your card.', form.elements.consent_to_discover);
  if (form.elements.email.value && !form.elements.email.checkValidity()) { form.querySelector('#contact-details').open = true; return missing('That email address does not look valid.', form.elements.email); }
  if (fd.get('resume')?.size && !draftId) { result.textContent = 'Autofill from your resume first (step 1), or go back and remove the file.'; return; }
  result.textContent = 'Publishing your card…';
  try {
    const data = await post('/api/profiles', {display_name:fd.get('display_name'), headline:fd.get('headline'),
      public_summary:fd.get('public_summary'), approved_experience:fd.get('approved_experience'),
      can_help_with:splitTags(fd.get('can_help_with')), looking_for:splitTags(fd.get('looking_for')), interests:splitTags(fd.get('interests')), roles:['peer'],
      formats:selected('formats', form), availability:fd.get('availability'), email:fd.get('email') || null,
      linkedin_url:fd.get('linkedin_url') || null, consent_to_discover:fd.get('consent_to_discover') === 'on',
      consent_to_resume_parsing:fd.get('consent_to_resume_parsing') === 'on', draft_id:draftId});
    result.textContent = data.state === 'indexed' ? 'Other attendees can now find your card. Keep this browser open to accept introductions.' : 'Your card is queued. Other attendees can find it once indexing finishes.';
    await refreshMe();
  } catch (error) { result.textContent = error.message; }
});

const resumeInput = document.querySelector('input[name="resume"]');
function showCardStep(fromResume) {
  $('#resume-step').classList.add('hidden'); $('#card-step').classList.remove('hidden');
  $('#autofill-note').classList.toggle('hidden', !fromResume);
  const name = $('#profile-form').elements.display_name;
  (fromResume ? $('#card-step') : name).scrollIntoView({block:'start', behavior: reduceMotion ? 'auto' : 'smooth'});
  if (!fromResume) name.focus();
}
$('#skip-resume').addEventListener('click', () => showCardStep(false));
$('#back-to-resume').addEventListener('click', () => { $('#card-step').classList.add('hidden'); $('#resume-step').classList.remove('hidden'); });
resumeInput.addEventListener('change', () => {
  draftId = null; previewGeneration++;
  const file = resumeInput.files[0];
  $('#resume-drop').classList.toggle('has-file', !!file);
  $('#resume-drop .dropzone-text strong').textContent = file ? file.name : 'Choose or drop a resume';
  $('#resume-preview').textContent = file ? (resumeInput.form.elements.consent_to_resume_parsing.checked ? '' : 'Tick the parsing consent box, then autofill.') : '';
  if (file && resumeInput.form.elements.consent_to_resume_parsing.checked) $('#preview-resume').click();
});
['dragenter', 'dragover'].forEach((type) => $('#resume-drop').addEventListener(type, () => $('#resume-drop').classList.add('dragging')));
['dragleave', 'drop'].forEach((type) => $('#resume-drop').addEventListener(type, () => $('#resume-drop').classList.remove('dragging')));
document.querySelector('select[name="source_type"]').addEventListener('change', () => { draftId = null; previewGeneration++; });
document.querySelector('input[name="consent_to_resume_parsing"]').addEventListener('change', (event) => {
  if (!event.target.checked) { draftId = null; previewGeneration++; }
});
$('#preview-resume').addEventListener('click', async () => {
  const file = resumeInput.files[0], result = $('#resume-preview'), form = $('#profile-form');
  if (!file) { result.textContent = 'Choose a resume first, or skip and fill in your card yourself.'; return; }
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
    setIfEmpty('display_name', (hints.suggested_name || '').replace(/\s+/g, ' ').trim()); setIfEmpty('email', hints.email); setIfEmpty('linkedin_url', hints.linkedin_url);
    setIfEmpty('headline', suggestions.headline);
    setIfEmpty('can_help_with', (suggestions.skills || []).slice(0, 20).join(', '));
    setIfEmpty('public_summary', (suggestions.project_highlights || []).join('\n').slice(0, 1600));
    setIfEmpty('approved_experience', (suggestions.experience_highlights || []).join('\n').slice(0, 2400));
    if (hints.email || hints.linkedin_url) $('#contact-details').open = true;
    const filled = ['display_name', 'headline', 'can_help_with', 'public_summary', 'approved_experience', 'email', 'linkedin_url'].filter((n) => form.elements[n].value).length;
    result.textContent = '';
    $('#autofill-note').textContent = `Autofilled ${filled} fields and ${suggestions.skills?.length || 0} skills from ${file.name}. Edit anything, add what you're looking for, then approve. The preview expires in 20 minutes.`;
    showCardStep(true);
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
    lastCompare = await post('/api/search/compare', {question, sponsor_intent, size:5, include_synthetic:showSynth});
    viewMode = 'hybrid';
    field.showQuery(question, lastCompare.modes.hybrid);
    renderMode();
    renderAnswer(await post('/api/ask', {question, sponsor_intent, include_synthetic:showSynth}), rag);
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
function renderWhy(why, lexicalLabel = 'Exact terms (BM25)') {
  const rows = [
    [lexicalLabel, why.lexical_terms, 'No exact word overlap: found by meaning only'],
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
  node.querySelector('.name').textContent = cleanName(hit.display_name);
  if (isSynthetic(hit)) {
    node.querySelector('.name').insertAdjacentHTML('beforeend', '<span class="demo-flag mono">SynthDemo</span>');
    node.querySelector('.connect').disabled = true;
    node.querySelector('.connect').textContent = 'Synthetic card · introductions need real attendees';
  }
  node.querySelector('.name').classList.add('openable');
  node.querySelector('.name').addEventListener('click', () => openSheet(hit));
  return node;
}

async function createConnection(hit, container = $('#search-results')) {
  const message = window.prompt(`Short note to ${hit.display_name}:`, 'Want to connect about this project?');
  if (!message) return;
  const item = await post('/api/connection-requests', {requester_name:myProfile?.display_name || 'Attendee', recipient_profile_id:hit.id, message, preferred_channel:'meetup_qr'});
  field.link(hit.id, false); graphFeed.poke();
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
  container.prepend(node);
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
// Discipline tint: angle of a card's discipline-anchor position = hue, distance = saturation. Shared by every view.
function hueAt(x, y, t = 0) { return ((Math.atan2(y, x) * 180 / Math.PI + 360) % 360) + 5 * Math.sin(t / 7000 + Math.hypot(x, y) * 5); }
function satAt(x, y) { return clamp(Math.hypot(x, y) / 0.5, 0, 1); }
function tint(x, y, l, a, t = 0, s = 1) { return `hsla(${hueAt(x, y, t).toFixed(1)},${Math.min(100, satAt(x, y) * 62 * s).toFixed(0)}%,${l}%,${a.toFixed(3)})`; }
const KIND_WEIGHT = {view:1, event:2, message:3, meet:5};
const KIND_LABEL = {view:'viewed profile', event:'same session', message:'messaged', meet:'met in person'};

/* ======================================================================
   2D network. Every discoverable card from /api/graph, placed by the first two
   PCA components of its stored Jina vector (the same projection as 3D, seen
   from the front). Lines exist only where people interacted; their weight is
   the interaction strength. Shaded regions are the server's k-means clusters,
   named after their members' most over-represented features. Packets and
   flashes are real: interactions, joins and live API requests.
   ====================================================================== */
function createField() {
  const canvas = $('#constellation-canvas'), stage = $('#constellation-stage'), ctx = canvas.getContext('2d');
  const caption = $('#constellation-caption'), legend = $('#cluster-legend'), zoomOut = $('#zoom-readout'), teleOut = $('#telemetry-readout');
  const MONO = '"JetBrains Mono", "SF Mono", ui-monospace, Menlo, Consolas, monospace', SANS = 'Inter, ui-sans-serif, system-ui, sans-serif';
  const PLACE = .9;
  let W = 1, H = 1, S = 1, dpr = 1;
  const cam = {x:0, y:0, z:1};
  let flight = null, nodes = [], edges = [], edgeMap = new Map(), adj = new Map();
  const links = new Map(), byId = new Map();
  let sel = null, hov = null, packets = [], flashes = [], spawnLog = [], lastReq = '', liveStats = null, lastSeq = null;
  let visible = false, raf = null, last = performance.now(), selAcc = 0, pending = null, firstShow = true, hudTimer = 0;
  let groups = [], serverEdges = [], youId = null, legendSig = '', fingerprint = null;

  function makeNode({id, name, kind, tags = [], pos = null, hue = null, cluster = -1}) {
    const r = seeded(hashStr(id));
    const x = pos ? pos.x * PLACE : (r() - .5) * .3, y = pos ? pos.y * PLACE : (r() - .5) * .3, h = hue || {x:0, y:0};
    return {id, name, kind, tags:tags.map((t) => t.toLowerCase()), x, y, tx:x, ty:y, hx:h.x, hy:h.y, cluster, heat:0, born:performance.now()};
  }
  function addNode(n) { const old = byId.get(n.id); if (old) nodes = nodes.filter((m) => m !== old); nodes.push(n); byId.set(n.id, n); return n; }
  function removeNode(id) { const n = byId.get(id); if (!n) return; nodes = nodes.filter((m) => m !== n); byId.delete(id); if (sel === n) clearSelection(); if (hov === n) hov = null; }
  const nodeFor = (id) => (id === youId ? byId.get('you') : null) || byId.get(`p:${id}`) || byId.get(`g:${id}`);

  // Spread overlapping targets a little so every card stays clickable.
  function relax(iter, only = null) {
    for (let it = 0; it < iter; it++) for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j]; if (only && !only.has(a) && !only.has(b)) continue;
      let dx = b.tx - a.tx, dy = b.ty - a.ty; const d = Math.hypot(dx, dy) || 1e-4, min = .04;
      if (d >= min) continue;
      dx /= d; dy /= d; const am = !only || only.has(a), bm = !only || only.has(b), f = (min - d) / (am && bm ? 2 : 1);
      if (am) { a.tx -= dx * f; a.ty -= dy * f; } if (bm) { b.tx += dx * f; b.ty += dy * f; }
    }
  }
  function centroids() {
    groups.forEach((g) => {
      const m = nodes.filter((n) => n.cluster === g.id && n.kind !== 'query');
      const avg = (k, d) => (m.length ? m.reduce((s, n) => s + n[k], 0) / m.length : d);
      g.cx = avg('tx', (g.centroid2?.x || 0) * PLACE); g.cy = avg('ty', (g.centroid2?.y || 0) * PLACE); g.hx = avg('hx', 0); g.hy = avg('hy', 0);
      g.spread = m.length ? Math.sqrt(m.reduce((s, n) => s + (n.tx - g.cx) ** 2 + (n.ty - g.cy) ** 2, 0) / m.length) : .1;
      g.heat ??= 0;
    });
  }
  function buildEdges() {
    edges = []; edgeMap.clear(); adj = new Map(nodes.map((n) => [n, new Set()]));
    const add = (a, b, type, extra = {}) => {
      if (!a || !b || a === b) return;
      const k = a.id < b.id ? `${a.id}|${b.id}` : `${b.id}|${a.id}`, old = edgeMap.get(k);
      if (old) { if (type === 'link') old.type = 'link'; return; }
      const e = {a, b, type, heat:0, ...extra}; edges.push(e); edgeMap.set(k, e); adj.get(a).add(b); adj.get(b).add(a);
    };
    serverEdges.forEach((e) => add(nodeFor(e.a), nodeFor(e.b), 'act', {strength:e.strength, kind:e.kind, simulated:!!e.simulated}));
    const q = byId.get('query'); if (q) q.hits.forEach((id) => add(q, byId.get(id), 'query'));
    links.forEach((accepted, id) => add(byId.get('you'), byId.get(id), 'link'));
  }
  function renderLegend() {
    const sig = groups.map((g) => `${g.id}:${g.label}:${g.size}`).join('|'); if (sig === legendSig) return; legendSig = sig;
    legend.innerHTML = groups.length ? `<p class="legend-title mono">clusters · k-means on stored vectors</p>${groups.map((g) => `<button type="button" data-i="${g.id}"><b style="color:${tint(g.hx, g.hy, 66, 1, 0, 1.3)}"></b>${escapeHtml(g.label)}<em>${g.size}</em></button>`).join('')}` : '';
    legend.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
      const g = groups.find((x) => x.id === +b.dataset.i); if (!g) return;
      clearSelection(); fit(nodes.filter((n) => n.cluster === g.id && n.kind !== 'query'), 900);
      legend.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
      caption.innerHTML = `<span>CLUSTER</span><p><strong>${escapeHtml(g.label)}</strong> · ${g.size} cards<br><em>${escapeHtml((g.features || []).map((f) => `${f.term} ${Math.round(f.share * 100)}%`).join(' · '))}</em></p>`;
    }));
  }
  function rebuild(fresh) {
    if (fresh) relax(fresh === true ? 30 : 40, fresh === true ? null : new Set(fresh));
    buildEdges(); centroids(); renderLegend();
    $('#node-options').innerHTML = nodes.filter((n) => n.kind !== 'query').map((n) => `<option value="${escapeHtml(n.name)}"></option>`).join('');
  }

  // Camera
  const toScreen = (x, y) => ({x:(x - cam.x) * S * cam.z + W / 2, y:(y - cam.y) * S * cam.z + H / 2});
  const toWorld = (sx, sy) => ({x:(sx - W / 2) / (S * cam.z) + cam.x, y:(sy - H / 2) / (S * cam.z) + cam.y});
  function flyTo(x, y, z, dur = 1100) {
    z = clamp(z, .5, 10);
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
  function zoomAt(sx, sy, z) { const w = toWorld(sx, sy); cam.z = clamp(z, .5, 10); cam.x = w.x - (sx - W / 2) / (S * cam.z); cam.y = w.y - (sy - H / 2) / (S * cam.z); }
  function fit(list, dur) {
    if (!list.length) return;
    const xs = list.map((n) => n.tx), ys = list.map((n) => n.ty), hw = (Math.max(...xs) - Math.min(...xs)) / 2 + .12, hh = (Math.max(...ys) - Math.min(...ys)) / 2 + .12;
    flyTo((Math.max(...xs) + Math.min(...xs)) / 2, (Math.max(...ys) + Math.min(...ys)) / 2, clamp(Math.min(W / 2 / (S * hw), H / 2 / (S * hh)) * .85, .8, 4), dur);
  }

  // Traffic (only ever triggered by real events)
  function spawn(a, b, {strong = false, delay = 0, dur} = {}) {
    if (!a || !b || a === b) return;
    if (!visible) { b.heat = Math.min(1.6, b.heat + .3); return; }
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
    const g = groups.find((x) => x.id === pk.b.cluster); if (g) g.heat = Math.min(3, g.heat + (pk.strong ? .3 : .12));
  }

  // Selection
  const KIND = {person:'PEER', sponsor:'SPONSOR', live:'MATCH', you:'YOU', query:'QUERY'};
  function focus(n, zoom) {
    sel = n; selAcc = 0;
    flyTo(n.tx, n.ty, zoom || 3.1);
    const nb = [...adj.get(n)].filter((m) => m.kind !== 'query'), area = groups.find((g) => g.id === n.cluster)?.label || 'unclustered';
    const ties = nb.map((m) => { const e = edgeMap.get(n.id < m.id ? `${n.id}|${m.id}` : `${m.id}|${n.id}`); return {m, w:e?.strength || 0, kind:e?.kind}; }).sort((a, b) => b.w - a.w);
    const detail = n.kind === 'query' ? `${n.hits.length} matched cards` : n.tags.slice(0, 4).join(' · ');
    caption.innerHTML = `<span>${KIND[n.kind]}</span><p><strong>${escapeHtml(n.name)}</strong> · ${escapeHtml(area)}<br><em>${escapeHtml(detail)}</em>${ties.length ? `<br>${ties.length} link${ties.length > 1 ? 's' : ''} · ${escapeHtml(ties.slice(0, 3).map((x) => `${x.m.name}${x.kind ? ` (${KIND_LABEL[x.kind]})` : ''}`).join(', '))}${ties.length > 3 ? '…' : ''}` : '<br>no interactions yet'}</p>`;
    legend.querySelectorAll('button').forEach((s) => s.classList.toggle('on', +s.dataset.i === n.cluster));
    setTimeout(() => burst(n, 8, true), reduceMotion ? 0 : 420);
    if (n.profile) openSheet(n.profile);
  }
  function clearSelection() {
    sel = null; caption.innerHTML = '<span>—</span><p>Tap a person to see their links. Tap a cluster name to zoom to it.</p>';
    legend.querySelectorAll('button').forEach((s) => s.classList.remove('on'));
  }
  function reset() { clearSelection(); flyTo(0, 0, 1); }
  function pick(sx, sy, slop = 16) {
    let best = null, bd = Infinity;
    nodes.forEach((n) => { const p = toScreen(n.x, n.y), d = Math.hypot(p.x - sx, p.y - sy); if (d < bd) { bd = d; best = n; } });
    return bd < slop ? best : null;
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
    const ease = 1 - Math.exp(-dt / 420);
    nodes.forEach((n) => { n.x += (n.tx - n.x) * ease; n.y += (n.ty - n.y) * ease; });
    const focusSet = sel ? new Set([sel, ...adj.get(sel)]) : null;

    // Cluster regions: centred on the members, sized by their spread, brighter with recent traffic
    groups.forEach((g, i) => {
      g.heat = (g.heat || 0) * Math.exp(-dt / 2600);
      const p = toScreen(g.cx, g.cy), r = (g.spread * 1.9 + .08 + .01 * Math.sin(t / 2600 + i * 1.7)) * S * cam.z;
      const gr = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
      gr.addColorStop(0, tint(g.hx, g.hy, 52, .07 + Math.min(.1, g.heat * .05), t, 1.3)); gr.addColorStop(1, 'hsla(0,0%,0%,0)');
      ctx.fillStyle = gr; ctx.fillRect(p.x - r, p.y - r, r * 2, r * 2);
    });

    // World grid (moves with the camera, so zoom reads as depth)
    const step = cam.z > 3.5 ? .05 : cam.z > 1.6 ? .1 : .2, tl = toWorld(0, 0), br = toWorld(W, H);
    ctx.fillStyle = 'rgba(255,255,255,.07)';
    for (let gx = Math.ceil(tl.x / step) * step; gx <= br.x; gx += step) for (let gy = Math.ceil(tl.y / step) * step; gy <= br.y; gy += step) { const p = toScreen(gx, gy); ctx.fillRect(Math.round(p.x), Math.round(p.y), 1, 1); }

    // Interaction links: width and brightness follow strength; profile views are dotted; SynthDemo links are faint
    edges.forEach((e) => {
      e.heat *= Math.exp(-dt / 520);
      const st = e.strength || 1;
      let a = e.type === 'query' ? .3 : e.type === 'link' ? .6 : (e.simulated ? .05 : .16) + st * (e.simulated ? .03 : .08);
      if (focusSet) a = e.a === sel || e.b === sel ? .45 + st * .07 : e.type === 'link' ? .3 : .025;
      a += e.heat * .6;
      ctx.strokeStyle = tint((e.a.hx + e.b.hx) / 2, (e.a.hy + e.b.hy) / 2, 80, Math.min(.95, a), t, .8);
      ctx.lineWidth = (e.type === 'link' ? 1.4 : e.type === 'act' ? .35 + st * .3 : .6) + e.heat * 1.2;
      ctx.setLineDash(e.type === 'query' ? [2, 4] : e.type === 'link' && !links.get(e.a.id === 'you' ? e.b.id : e.a.id) ? [6, 4] : e.kind === 'view' ? [1.5, 3] : []);
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
      const cx = lerp(pk.a.hx, pk.b.hx, ep), cy = lerp(pk.a.hy, pk.b.hy, ep);
      const hd = toScreen(hx, hy), tl2 = toScreen(tx, ty), g = ctx.createLinearGradient(tl2.x, tl2.y, hd.x, hd.y);
      g.addColorStop(0, tint(cx, cy, 70, 0, t, 1.4)); g.addColorStop(1, tint(cx, cy, 82, pk.strong ? .95 : .7, t, 1.5));
      ctx.strokeStyle = g; ctx.lineWidth = pk.strong ? 2.2 : 1.4; line(tl2, hd);
      const gr = pk.strong ? 16 : 10, rg = ctx.createRadialGradient(hd.x, hd.y, 0, hd.x, hd.y, gr);
      rg.addColorStop(0, tint(cx, cy, 85, pk.strong ? .8 : .55, t, 1.5)); rg.addColorStop(1, tint(cx, cy, 50, 0, t));
      ctx.fillStyle = rg; ctx.beginPath(); ctx.arc(hd.x, hd.y, gr, 0, TAU); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,.95)'; ctx.beginPath(); ctx.arc(hd.x, hd.y, pk.strong ? 1.7 : 1.2, 0, TAU); ctx.fill();
      return true;
    });
    flashes = flashes.filter((f) => {
      const p = (now - f.t0) / (f.strong ? 800 : 620); if (p >= 1) return false; if (p < 0) return true;
      const s = toScreen(f.n.x, f.n.y), r = 4 + easeInOut(p) * (f.strong ? 38 : 22);
      ctx.strokeStyle = tint(f.n.hx, f.n.hy, 86, (1 - p) * .75, t, 1.4); ctx.lineWidth = (1 - p) * 1.4 + .2;
      ctx.beginPath(); ctx.arc(s.x, s.y, r, 0, TAU); ctx.stroke();
      if (p < .3) { const cg = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, 18); cg.addColorStop(0, `rgba(255,255,255,${(.5 * (1 - p / .3)).toFixed(3)})`); cg.addColorStop(1, 'rgba(255,255,255,0)'); ctx.fillStyle = cg; ctx.beginPath(); ctx.arc(s.x, s.y, 18, 0, TAU); ctx.fill(); }
      return true;
    });
    ctx.globalCompositeOperation = 'source-over';

    // Nodes: people without any interaction yet are drawn quieter
    const zr = clamp(.75 + cam.z * .22, .8, 1.8);
    nodes.forEach((n) => {
      n.heat *= Math.exp(-dt / 1100);
      const s = toScreen(n.x, n.y); if (s.x < -40 || s.y < -40 || s.x > W + 40 || s.y > H + 40) return;
      const dim = focusSet && !focusSet.has(n), appear = clamp((now - n.born) / 700, 0, 1), lonely = !adj.get(n)?.size && n.kind === 'person';
      const r = ({person:2.8, sponsor:3.4, live:3.4, you:3.8, query:4.5})[n.kind] * zr * (.3 + .7 * easeInOut(appear)) * (1 + n.heat * .3);
      const A = (dim ? .16 : 1) * (lonely && !focusSet ? .6 : 1);
      if (!dim && !lonely) {
        const hr = r * 4 + n.heat * 16 + (n === sel ? 16 : 0), g = ctx.createRadialGradient(s.x, s.y, 0, s.x, s.y, hr);
        g.addColorStop(0, tint(n.hx, n.hy, 70, Math.min(.6, .14 + n.heat * .22 + (n === sel ? .2 : 0)), t, 1.2)); g.addColorStop(1, tint(n.hx, n.hy, 40, 0, t));
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(s.x, s.y, hr, 0, TAU); ctx.fill();
      }
      ctx.fillStyle = tint(n.hx, n.hy, 84, A, t, .9); ctx.strokeStyle = tint(n.hx, n.hy, 84, A, t, .9); ctx.lineWidth = 1;
      if (n.kind === 'sponsor') { ctx.save(); ctx.translate(s.x, s.y); ctx.rotate(Math.PI / 4); ctx.fillRect(-r * .8, -r * .8, r * 1.6, r * 1.6); ctx.restore(); }
      else if (n.kind === 'live') { ctx.beginPath(); ctx.arc(s.x, s.y, r + 1.5, 0, TAU); ctx.stroke(); ctx.beginPath(); ctx.arc(s.x, s.y, r * .45, 0, TAU); ctx.fill(); }
      else if (n.kind === 'query') {
        ctx.strokeStyle = `rgba(255,255,255,${A})`; ctx.beginPath(); ctx.arc(s.x, s.y, r, 0, TAU); ctx.stroke();
        line({x:s.x - r * 2.2, y:s.y}, {x:s.x - r * 1.3, y:s.y}); line({x:s.x + r * 1.3, y:s.y}, {x:s.x + r * 2.2, y:s.y}); line({x:s.x, y:s.y - r * 2.2}, {x:s.x, y:s.y - r * 1.3}); line({x:s.x, y:s.y + r * 1.3}, {x:s.x, y:s.y + r * 2.2});
      } else { ctx.beginPath(); ctx.arc(s.x, s.y, r, 0, TAU); ctx.fill(); }
      if (n.kind === 'you') { ctx.strokeStyle = `rgba(255,255,255,${(.6 * A).toFixed(2)})`; ctx.beginPath(); ctx.arc(s.x, s.y, r + 4 + (reduceMotion ? 0 : Math.sin(t / 420) * 1.5), 0, TAU); ctx.stroke(); }
      if (n === sel || n === hov) { ctx.strokeStyle = n === sel ? 'rgba(255,255,255,.85)' : 'rgba(255,255,255,.4)'; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(s.x, s.y, r + 6, 0, TAU); ctx.stroke(); }
      n._r = r;
    });

    // Labels
    ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
    nodes.forEach((n) => {
      if (focusSet && !focusSet.has(n)) return;
      const show = n === sel || n === hov || focusSet || ['you', 'query', 'live'].includes(n.kind) || cam.z > 2.2 || n.heat > .6;
      if (!show) return;
      const s = toScreen(n.x, n.y); if (s.x < -10 || s.y < -10 || s.x > W + 10 || s.y > H + 10) return;
      ctx.font = `${n === sel ? 600 : 500} ${n === sel ? 12.5 : 11}px ${SANS}`; ctx.fillStyle = n === sel ? '#fff' : 'rgba(225,225,225,.82)';
      ctx.fillText(n.name, s.x + (n._r || 3) + 7, s.y);
    });

    // Cluster names, fading out as you zoom in
    const la = clamp((2.4 - cam.z) / 1.2, 0, 1) * (focusSet ? .25 : .75);
    if (la > .01) {
      ctx.font = `500 10px ${MONO}`; ctx.textAlign = 'center';
      groups.forEach((g) => { const p = toScreen(g.cx, g.cy - g.spread - .05); ctx.fillStyle = tint(g.hx, g.hy, 76, la, t, 1.2); ctx.fillText(g.label.toUpperCase(), p.x, p.y); });
    }
  }
  function updateHud(now) {
    zoomOut.textContent = `${cam.z.toFixed(2)}×`;
    if (now - hudTimer < 250) return; hudTimer = now;
    spawnLog = spawnLog.filter((ts) => ts > now - 1000 && ts <= now);
    const server = liveStats ? `server ${liveStats.rps.toFixed(1)} req/s · p95 ${liveStats.p95}ms` : 'server telemetry…';
    const es = liveStats?.elastic?.count_ms != null ? `\nelastic count ${liveStats.elastic.count_ms}ms · links ${liveStats.elastic.links_ms ?? '—'}ms` : '';
    teleOut.textContent = `${server}${es}\n${String(spawnLog.length).padStart(2, '0')} packets/s${lastReq ? `\n${lastReq}` : ''}`;
  }
  function frame(now) {
    const dt = Math.min(64, now - last); last = now;
    stepCamera(now);
    if (sel && !reduceMotion && (selAcc += dt) > 360) { selAcc = 0; const nb = [...adj.get(sel)]; if (nb.length) { const m = nb[Math.random() * nb.length | 0]; Math.random() < .6 ? spawn(sel, m, {strong:true}) : spawn(m, sel, {strong:true}); } }
    draw(now, dt); updateHud(now);
    raf = visible ? requestAnimationFrame(frame) : null;
  }

  // Interaction: mouse, touch drag and pinch
  const pointers = new Map(); let drag = null, pinch = null;
  canvas.addEventListener('pointerdown', (e) => {
    canvas.setPointerCapture(e.pointerId); pointers.set(e.pointerId, {x:e.offsetX, y:e.offsetY}); flight = null;
    if (pointers.size === 1) drag = {x:e.offsetX, y:e.offsetY, moved:0, touch:e.pointerType === 'touch'};
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
    if (drag && drag.moved < 8 && e.type === 'pointerup') { const n = pick(e.offsetX, e.offsetY, drag.touch ? 26 : 16); n ? focus(n) : sel && clearSelection(); }
    drag = null; if (pointers.size < 2) pinch = null; canvas.classList.remove('dragging');
  };
  canvas.addEventListener('pointerup', release); canvas.addEventListener('pointercancel', release);
  canvas.addEventListener('pointerleave', () => { hov = null; canvas.classList.remove('hovering'); });
  canvas.addEventListener('wheel', (e) => { e.preventDefault(); flight = null; zoomAt(e.offsetX, e.offsetY, cam.z * Math.exp(-e.deltaY * .0015)); }, {passive:false});
  new ResizeObserver(() => { if (visible) resize(); }).observe(stage);

  // Ambient page field: a tiny canvas stretched behind the UI, coloured by the live clusters
  const fc = $('#field'), fx = fc.getContext('2d'); fc.width = 64; fc.height = 40; let lastAmb = 0;
  function ambient(now) {
    requestAnimationFrame(ambient);
    const dt = now - lastAmb; if (dt < 120) return; lastAmb = now;
    fx.clearRect(0, 0, 64, 40);
    const rot = reduceMotion ? 0 : now * .000018 + .6, focusGroup = sel ? sel.cluster : -1;
    groups.forEach((g) => {
      if (!visible) g.heat = (g.heat || 0) * Math.exp(-dt / 2600);
      const x = g.cx * Math.cos(rot) - g.cy * Math.sin(rot), y = g.cx * Math.sin(rot) + g.cy * Math.cos(rot);
      const px = 32 + x * 30, py = 20 + y * 20, r = 14 + Math.min(10, g.heat * 4) + (g.id === focusGroup ? 6 : 0);
      const a = .06 + Math.min(.2, g.heat * .07) + (g.id === focusGroup ? .08 : 0);
      const gr = fx.createRadialGradient(px, py, 0, px, py, r);
      gr.addColorStop(0, tint(g.hx, g.hy, 48, a, now, 1.3)); gr.addColorStop(1, 'hsla(0,0%,0%,0)');
      fx.fillStyle = gr; fx.fillRect(0, 0, 64, 40);
    });
  }
  requestAnimationFrame(ambient);

  function findNode(query) {
    const q = query.trim().toLowerCase(); if (!q) return null;
    const list = nodes.filter((n) => n.kind !== 'query');
    return list.find((n) => n.name.toLowerCase() === q) || list.find((n) => n.name.toLowerCase().startsWith(q)) || list.find((n) => n.name.toLowerCase().includes(q) || n.tags.includes(q));
  }
  function pulseActivity(item) {
    const a = nodeFor(item.a), b = nodeFor(item.b); if (!a || !b) return;
    const strong = (KIND_WEIGHT[item.kind] || 1) >= 3;
    for (let i = 0; i < (strong ? 4 : 2); i++) spawn(a, b, {strong:true, delay:i * 180, dur:900});
    flashes.push({n:a, t0:performance.now(), strong:true}, {n:b, t0:performance.now() + 300, strong:true});
    a.heat = Math.min(1.6, a.heat + 1); b.heat = Math.min(1.6, b.heat + 1);
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
      lastReq = `you · ${method} ${path.replace(/\/connection-requests\/[^/]+/, '/connection-requests/:token').split('?')[0]} · ${Math.round(ms)}ms${status >= 400 ? ` · ${status}` : ''}`;
      const you = byId.get('you'); if (you && !path.startsWith('/api/telemetry')) burst(you, method === 'GET' ? 2 : 5, method !== 'GET');
    },
    live(stats) {
      // Requests other attendees just made (route templates only): each lights a random interaction link.
      liveStats = stats;
      if (lastSeq != null && visible) {
        const acts = edges.filter((e) => e.type === 'act');
        stats.recent.filter((r) => r.seq > lastSeq && !r.route.startsWith('/api/telemetry')).slice(-6).forEach((r, i) => {
          const e = acts[Math.random() * acts.length | 0]; if (e) spawn(...(Math.random() < .5 ? [e.a, e.b] : [e.b, e.a]), {delay:i * 120, strong:r.method !== 'GET'});
        });
      }
      lastSeq = stats.seq;
    },
    activity(items) { items.forEach(pulseActivity); },
    showQuery(question, hits) {
      removeNode('query');
      const live = hits.map((h) => {
        const g = byId.get(`g:${h.id}`), n = makeNode({id:`p:${h.id}`, name:cleanName(h.display_name), kind:'live', tags:h.can_help_with || [], pos:g ? {x:g.tx / PLACE, y:g.ty / PLACE} : null, hue:h.position || (g && {x:g.hx, y:g.hy}), cluster:g ? g.cluster : -1});
        if (g) { n.profile = {...g.profile, ...h}; removeNode(g.id); } else n.profile = h;
        return addNode(n);
      });
      const mean = (k) => (live.length ? live.reduce((s, n) => s + n[k], 0) / live.length : 0);
      const q = addNode(makeNode({id:'query', name:question.length > 34 ? `${question.slice(0, 33)}…` : question, kind:'query', pos:{x:mean('tx') / PLACE, y:mean('ty') / PLACE - .12}, hue:{x:mean('hx'), y:mean('hy')}}));
      q.hits = live.map((n) => n.id);
      rebuild([q, ...live]);
      const replay = () => { flashes.push({n:q, t0:performance.now(), strong:true}); live.forEach((n, i) => spawn(q, n, {strong:true, delay:i * 160, dur:900})); };
      if (visible) { clearSelection(); fit([q, ...live]); setTimeout(replay, reduceMotion ? 0 : 500); }
      else pending = {list:[q, ...live], replay};
    },
    colorFor(id) { const n = byId.get(`p:${id}`) || byId.get(`g:${id}`); return n ? `hsl(${hueAt(n.hx, n.hy).toFixed(0)} ${(satAt(n.hx, n.hy) * 55).toFixed(0)}% 62%)` : null; },
    focusId(id) { const n = nodeFor(id); if (!n) return; pending = null; firstShow = false; focus(n); },
    find(query) { const n = findNode(query); if (n) focus(n); return !!n; },
    setYou(profile) {
      if (!profile) { removeNode('you'); rebuild(); return; }
      if (byId.has('you')) { byId.get('you').name = `${profile.display_name} (you)`; return; }
      const n = addNode(makeNode({id:'you', name:`${profile.display_name} (you)`, kind:'you', tags:profile.can_help_with || []}));
      n.profile = profile;
      rebuild();
    },
    link(id, accepted) {
      // Introduction requested: dashed link. Accepted: solid, with traffic both ways.
      const you = byId.get('you'), peer = nodeFor(id);
      if (!you || !peer || links.get(peer.id) === true) return;
      links.set(peer.id, accepted); rebuild();
      const pulses = accepted ? 6 : 3;
      for (let i = 0; i < pulses; i++) { spawn(you, peer, {strong:true, delay:i * 220, dur:1000}); if (accepted) spawn(peer, you, {strong:true, delay:i * 220 + 110, dur:1000}); }
      flashes.push({n:peer, t0:performance.now(), strong:true});
      if (visible) fit([you, peer], 900);
    },
    setGraph(data) {
      youId = data.you; serverEdges = data.edges || [];
      groups = (data.clusters || []).map((c) => ({...(groups.find((g) => g.id === c.id) || {}), ...c}));
      const changed = data.stats.fingerprint !== fingerprint; fingerprint = data.stats.fingerprint;
      const want = new Set(data.nodes.filter((p) => p.id !== youId).map((p) => `g:${p.id}`));
      [...byId.keys()].filter((id) => id.startsWith('g:') && !want.has(id)).forEach(removeNode);
      const fresh = [], firstLoad = !nodes.some((n) => n.id.startsWith('g:'));
      data.nodes.forEach((p) => {
        const pos = p.position2 || p.position3, hue = p.position || {x:0, y:0};
        const target = byId.get(p.id === youId ? 'you' : `p:${p.id}`) || byId.get(`g:${p.id}`);
        if (target) {
          target.cluster = p.cluster; target.hx = hue.x; target.hy = hue.y; target.profile = target.kind === 'live' ? {...p, ...target.profile} : p;
          if (changed && target.kind !== 'live') { target.tx = pos.x * PLACE; target.ty = pos.y * PLACE; if (target.kind === 'you' && !target.placed) { target.x = target.tx; target.y = target.ty; target.placed = true; } }
          return;
        }
        if (p.id === youId) return;
        const kind = (p.roles || []).some((r) => r === 'sponsor' || r === 'recruiter') ? 'sponsor' : 'person';
        const n = makeNode({id:`g:${p.id}`, name:cleanName(p.display_name), kind, tags:p.can_help_with || [], pos, hue, cluster:p.cluster});
        n.profile = p; fresh.push(addNode(n));
      });
      rebuild(changed ? true : null);
      if (!firstLoad && fresh.length && fresh.length < 6) fresh.forEach((n) => burst(n, 4, true));
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
   People: the general similarity view.
   The server ranks by vector similarity (Jina in Elastic mode) and returns
   an (x, y) position in the same discipline ring the Field map uses for
   every person and tag. Colour is tint(x, y): angle = discipline, distance
   from centre = saturation, so a web tag is yellow and a generalist is grey.
   ====================================================================== */
const PEOPLE_MODES = {
  query: {placeholder: 'Search people by skill, project or idea', card: 'similar to your card'},
  problem: {placeholder: 'Describe the problem you are stuck on', card: 'who offer what your card is looking for'},
  hire: {placeholder: 'Role or skills you want to be hired for (optional)', card: 'hiring for what your card offers'},
  interests: {placeholder: 'Climbing, jazz, chess… (optional)', card: 'who share your interests outside work'},
  discover: {placeholder: 'Optional: a direction to explore', card: 'furthest from your cluster'},
  shuffle: {placeholder: 'Random discovery ignores search text', card: 'sampled at random'},
};
const people = {mode: 'query', seq: 0, loaded: false};
const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '?';
const peopleFilters = () => {
  const form = $('#people-filters');
  return {formats: selected('formats', form), roles: selected('roles', form),
    available_now: form.elements.available_now.checked, sponsor_intent: form.elements.sponsor_intent.checked};
};
function updateFilterCount() {
  const f = peopleFilters(), n = f.formats.length + f.roles.length + f.available_now + f.sponsor_intent;
  $('#people-filter-count').textContent = n ? ` · ${n}` : '';
}

async function runPeople() {
  const seq = ++people.seq, list = $('#people-results'), basis = $('#people-basis');
  people.loaded = true;
  list.setAttribute('aria-busy', 'true'); list.classList.add('loading');
  basis.textContent = people.mode === 'shuffle' ? 'Finding someone unexpected…' : 'Ranking people by vector similarity…';
  const question = $('#people-q').value.trim();
  try {
    const data = await post('/api/people/search', {mode:people.mode, question, size:12, include_synthetic:showSynth, ...peopleFilters()});
    if (seq !== people.seq) return;
    renderPeople(data, question);
  } catch (error) {
    if (seq !== people.seq) return;
    list.innerHTML = '';
    basis.textContent = error.message;
    if (error.status === 403) {
      const li = document.createElement('li'); li.className = 'people-empty';
      li.innerHTML = '<p>Join with a card to see who is similar to you.</p><button class="secondary compact-btn" type="button">Create your card</button>';
      li.querySelector('button').addEventListener('click', () => switchTab('join'));
      list.appendChild(li);
    }
    people.loaded = false;
  } finally {
    if (seq === people.seq) { list.removeAttribute('aria-busy'); list.classList.remove('loading'); }
  }
}

function renderPeople(data, question) {
  const list = $('#people-results'), n = data.hits.length;
  list.innerHTML = '';
  const what = data.basis === 'query' ? `matching “${question.length > 40 ? `${question.slice(0, 39)}…` : question}”` : PEOPLE_MODES[data.mode].card;
  const order = data.mode === 'discover' ? 'Ordered by distance from your cluster; % is similarity to you' : '% is vector similarity';
  const colour = data.colour_source === 'jina' ? 'colour from Jina anchor similarity' : 'colour from keyword affinity';
  $('#people-basis').textContent = data.note || `${n} ${n === 1 ? 'person' : 'people'} ${what} · ${order} · ${data.similarity} · ${colour}`;
  if (!n && !data.note) {
    const li = document.createElement('li'); li.className = 'people-empty';
    li.innerHTML = `<p>No one matches yet. ${data.mode === 'hire' ? 'No recruiters or sponsors match these filters.' : 'Try another view, clear filters, or invite more attendees to opt in.'}</p>`;
    list.appendChild(li);
  }
  data.hits.forEach((hit, i) => list.appendChild(renderPerson(hit, i)));
  if (n) field.showQuery(question || `${PEOPLE_MODES[data.mode].card}`, data.hits);
}

function renderPerson(hit, index) {
  const li = document.createElement('li'), {x, y} = hit.position, demo = isSynthetic(hit);
  const name = cleanName(hit.display_name), why = hit.explanation || {};
  li.className = 'person';
  li.style.setProperty('--i', index);
  li.style.setProperty('--wash', tint(x, y, 48, .13, 0, 1.3));
  li.style.setProperty('--ring', tint(x, y, 68, .9, 0, 1.3));
  li.style.setProperty('--pct', `${hit.percent}%`);
  li.innerHTML = `
    <div class="avatar" aria-hidden="true">${escapeHtml(initials(name))}</div>
    <div class="person-main">
      <header class="person-top">
        <h3 class="openable" title="Open card">${escapeHtml(name)}${demo ? '<span class="demo-flag mono">SynthDemo</span>' : ''}</h3>
        ${hit.percent == null ? '<span class="mono">Discovery</span>' : `<span class="pct" aria-label="${hit.percent}% similar"><b>${hit.percent}</b><small>%</small></span>`}
      </header>
      ${hit.percent == null ? '' : '<div class="meter" aria-hidden="true"><i></i></div>'}
      <p class="person-headline">${escapeHtml(hit.headline || hit.availability)}${hit.discipline ? ` <span class="discipline mono">${escapeHtml(hit.discipline)}</span>` : ''}</p>
      <div class="tags"></div>
      ${hit.reasons.length ? `<p class="person-reason mono">${escapeHtml(hit.reasons[0])}</p>` : ''}
      <div class="quick">
        <button type="button" class="qa intro">${demo ? 'Synthetic card' : 'Request intro'}</button>
        <button type="button" class="qa why-toggle" aria-expanded="false">Why</button>
        <button type="button" class="qa locate">Map</button>
      </div>
      <div class="person-why" hidden>
        ${hit.reasons.length > 1 ? `<ul class="reasons">${hit.reasons.slice(1).map((r) => `<li>${escapeHtml(r)}</li>`).join('')}</ul>` : ''}
        <div class="why-body">${renderWhy(why, 'Shared words')}</div>
      </div>
    </div>`;
  const tags = li.querySelector('.tags');
  hit.tags.forEach((tag) => {
    const span = document.createElement('span'), p = tag.position;
    span.className = 'tag'; span.textContent = tag.label;
    span.title = tag.discipline ? `${tag.discipline} · placed by vector similarity` : 'near the centre: no single discipline';
    span.style.setProperty('--tag-fg', tint(p.x, p.y, 74, 1, 0, 1.3));
    span.style.setProperty('--tag-bg', tint(p.x, p.y, 45, .14, 0, 1.3));
    span.style.setProperty('--tag-line', tint(p.x, p.y, 60, .42, 0, 1.3));
    tags.appendChild(span);
  });
  const intro = li.querySelector('.intro');
  if (demo) intro.disabled = true;
  else intro.addEventListener('click', () => createConnection(hit, $('#people-intros')).catch(showStatusError));
  const toggle = li.querySelector('.why-toggle'), panel = li.querySelector('.person-why');
  toggle.addEventListener('click', () => { panel.hidden = !panel.hidden; toggle.setAttribute('aria-expanded', String(!panel.hidden)); });
  li.querySelector('.locate').addEventListener('click', () => { switchTab('constellation'); field.focusId(hit.id); });
  li.querySelector('h3').addEventListener('click', () => openSheet(hit));
  li.querySelector('.avatar').addEventListener('click', () => openSheet(hit));
  return li;
}

function setPeopleMode(mode, focus = false) {
  people.mode = mode;
  document.querySelectorAll('.mode-chips [data-mode]').forEach((b) => {
    const on = b.dataset.mode === mode; b.setAttribute('aria-checked', String(on)); b.tabIndex = on ? 0 : -1;
    if (on && focus) b.focus();
    if (on) b.scrollIntoView({block:'nearest', inline:'nearest', behavior: reduceMotion ? 'auto' : 'smooth'});
  });
  $('#people-q').placeholder = PEOPLE_MODES[mode].placeholder;
  runPeople();
}
document.querySelectorAll('.mode-chips [data-mode]').forEach((b) => b.addEventListener('click', () => setPeopleMode(b.dataset.mode)));
$('.mode-chips').addEventListener('keydown', (event) => {
  const step = {ArrowRight:1, ArrowDown:1, ArrowLeft:-1, ArrowUp:-1}[event.key];
  if (!step || !event.target.dataset.mode) return; event.preventDefault();
  const modes = Object.keys(PEOPLE_MODES);
  setPeopleMode(modes[(modes.indexOf(people.mode) + step + modes.length) % modes.length], true);
});
$('#people-form').addEventListener('submit', (event) => { event.preventDefault(); runPeople(); });
$('#people-filters-toggle').addEventListener('click', (event) => {
  const sheet = $('#people-filters'); sheet.hidden = !sheet.hidden; event.currentTarget.setAttribute('aria-expanded', String(!sheet.hidden));
});
$('#people-filters').addEventListener('change', updateFilterCount);
$('#people-filters').addEventListener('submit', (event) => {
  event.preventDefault(); event.currentTarget.hidden = true; $('#people-filters-toggle').setAttribute('aria-expanded', 'false'); runPeople();
});
$('#people-filters').addEventListener('reset', () => setTimeout(() => { updateFilterCount(); runPeople(); }));

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

/* ======================================================================
   Person sheet: the card of whoever you click, docked at the base of the screen.
   ====================================================================== */
const sheet = $('#person-sheet');
let sheetReturnFocus = null;
function openSheet(p) {
  if (!p) return;
  const synthetic = isSynthetic(p), mine = myProfile && p.id === myProfile.id, name = cleanName(p.display_name);
  const pos = p.position || {x:0, y:0};
  sheetReturnFocus = sheet.hidden ? document.activeElement : sheetReturnFocus;
  const tagRow = (label, items) => items?.length ? `<div class="sheet-row"><span class="eyebrow">${label}</span><div class="tags">${items.map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join('')}</div></div>` : '';
  const facts = [(p.roles || []).join(' · '), (p.formats || []).map((f) => ({async:'async intro', one_to_one:'1:1', small_group:'small group'})[f] || f).join(' · '), p.availability].filter(Boolean).join('  ·  ');
  $('#sheet-body').innerHTML = `
    <div class="sheet-head">
      <div class="avatar" aria-hidden="true" style="--wash:${tint(pos.x, pos.y, 48, .16, 0, 1.3)};--ring:${tint(pos.x, pos.y, 68, .9, 0, 1.3)}">${escapeHtml(initials(name))}</div>
      <div class="sheet-title">
        <h2 id="sheet-name">${escapeHtml(name)}${mine ? ' <span class="demo-flag mono">you</span>' : ''}${synthetic ? ' <span class="demo-flag mono">SynthDemo</span>' : ''}</h2>
        <p class="person-headline">${escapeHtml(p.headline || '')}${p.discipline ? ` <span class="discipline mono">${escapeHtml(p.discipline)}</span>` : ''}</p>
      </div>
      ${p.percent != null ? `<span class="pct"><b>${p.percent}</b><small>%</small></span>` : ''}
    </div>
    ${p.public_summary ? `<p class="sheet-summary">${escapeHtml(p.public_summary)}</p>` : ''}
    ${tagRow('Can help with', p.can_help_with)}${tagRow('Looking for', p.looking_for)}${tagRow('Outside work', p.interests)}
    <p class="sheet-facts mono">${escapeHtml(facts)}</p>
    <div class="quick">
      <button type="button" class="qa intro" ${synthetic || mine ? 'disabled' : ''}>${mine ? 'This is your card' : synthetic ? 'Synthetic card · no intros' : 'Request intro'}</button>
      <button type="button" class="qa similar">Find similar people</button>
      <button type="button" class="qa in3d">Show in 3D</button>
      ${!synthetic && !mine ? '<button type="button" class="qa overlap">Find a shared break</button>' : ''}
    </div>
    <p class="overlap-status small" aria-live="polite"></p>
    ${mine ? '' : `<div class="interact" role="group" aria-label="Log how you interacted">
      <span class="eyebrow">Log an interaction <span class="hint">builds your link · weakest to strongest</span></span>
      <div class="interact-row">
        <button type="button" class="ix" data-kind="event">Same session</button>
        <button type="button" class="ix" data-kind="message">Messaged</button>
        <button type="button" class="ix" data-kind="meet">Met in person</button>
      </div>
      <p class="ix-status mono" aria-live="polite"></p>
    </div>`}
    <div class="sheet-intros"></div>`;
  if (!mine && myState === 'indexed') {
    logInteraction(p.id, 'view', null);
    $('#sheet-body').querySelectorAll('.ix').forEach((b) => b.addEventListener('click', () => logInteraction(p.id, b.dataset.kind, b)));
  } else {
    $('#sheet-body').querySelectorAll('.ix').forEach((b) => { b.disabled = true; });
    const st = $('#sheet-body .ix-status'); if (st) st.textContent = 'Join first to build links.';
  }
  $('#sheet-body .intro').addEventListener('click', () => createConnection(p, $('#sheet-body .sheet-intros')).catch(showStatusError));
  $('#sheet-body .similar').addEventListener('click', () => {
    closeSheet(); $('#people-q').value = p.headline || (p.can_help_with || []).slice(0, 3).join(', ');
    switchTab('people'); if (people.mode !== 'query') setPeopleMode('query'); else runPeople();
  });
  $('#sheet-body .in3d').addEventListener('click', () => { switchTab('network3d'); g3.focusId(p.id); });
  $('#sheet-body .overlap')?.addEventListener('click', async (event) => {
    const button = event.currentTarget, status = $('#sheet-body .overlap-status');
    button.disabled = true; status.textContent = 'Checking shared availability…';
    try {
      const data = await api(`/api/people/${encodeURIComponent(p.id)}/overlap`);
      status.textContent = data.status === 'suggested'
        ? `${new Date(data.starts_at).toLocaleString()} – ${new Date(data.ends_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}. ${data.same_area ? `Both at ${data.your_area}.` : `You: ${data.your_area}. Them: ${data.their_area}.`} ${data.message}`
        : data.message;
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
  sheet.hidden = false; requestAnimationFrame(() => sheet.classList.add('open'));
}
const viewedThisSession = new Set();
const breakForm = $('#break-form'), breakStatus = $('#break-status');
$('#break-details').addEventListener('toggle', async (event) => {
  if (!event.target.open) return;
  try {
    const data = await api('/api/me/break');
    breakForm.reset();
    if (data.break) {
      for (const name of ['starts_at', 'ends_at']) {
        const date = new Date(data.break[name]);
        breakForm.elements[name].value = new Date(date - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      }
      breakForm.elements.area.value = data.break.area;
    }
    breakStatus.textContent = data.break ? 'Your break is shared. You can update or remove it.' : 'No active break shared.';
  } catch (error) { breakStatus.textContent = error.message; }
});
breakForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = breakForm.querySelector('[type="submit"]'); button.disabled = true;
  try {
    await post('/api/me/break', {starts_at:new Date(breakForm.elements.starts_at.value).toISOString(),
      ends_at:new Date(breakForm.elements.ends_at.value).toISOString(), area:breakForm.elements.area.value});
    breakStatus.textContent = 'Break saved. Open someone’s card to find a shared break.';
  } catch (error) { breakStatus.textContent = error.message; }
  finally { button.disabled = false; }
});
$('#break-clear').addEventListener('click', async () => {
  try { await api('/api/me/break', {method:'DELETE'}); breakForm.reset(); breakStatus.textContent = 'Break removed.'; }
  catch (error) { breakStatus.textContent = error.message; }
});
async function logInteraction(id, kind, button) {
  if (kind === 'view') { if (viewedThisSession.has(id)) return; viewedThisSession.add(id); }
  const status = $('#sheet-body .ix-status');
  try {
    const result = await post('/api/interactions', {target_id: id, kind});
    if (button) { button.classList.add('done'); if (status) status.textContent = `Logged · ${result.label}${result.count > 1 ? ` ×${result.count}` : ''}`; }
    graphFeed.poke();
  } catch (error) { if (button && status) status.textContent = error.message; }
}
function closeSheet() {
  if (sheet.hidden) return;
  sheet.classList.remove('open');
  setTimeout(() => { if (!sheet.classList.contains('open')) sheet.hidden = true; }, reduceMotion ? 0 : 220);
  sheetReturnFocus?.focus?.(); sheetReturnFocus = null;
}
sheet.querySelector('.sheet-close').addEventListener('click', closeSheet);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !sheet.hidden) closeSheet(); });

/* ======================================================================
   Graph feed: polls /api/graph (every discoverable card in Elastic, placed by
   its stored embedding) while a map is open, and reports who joined or left.
   ====================================================================== */
function createGraphFeed() {
  let active = false, timer = null, inflight = false, last = null, known = null, seenActivity = null;
  const listeners = [];
  async function load() {
    if (inflight) return; inflight = true;
    try {
      const sponsors = $('#g3-sponsors').checked;
      const data = await api(`/api/graph?include_synthetic=${showSynth}&include_sponsors=${sponsors}`);
      const ids = new Set(data.nodes.map((n) => n.id));
      const joined = known ? data.nodes.filter((n) => !known.has(n.id)) : [];
      const left = known ? [...known].filter((id) => !ids.has(id)) : [];
      // Interaction activity not seen before (the first load only primes the set, so old events don't replay).
      const activity = seenActivity ? (data.activity || []).filter((a) => !seenActivity.has(a.key)) : [];
      seenActivity = new Set((data.activity || []).map((a) => a.key));
      known = ids; last = data;
      listeners.forEach((fn) => fn(data, {joined, left, activity}));
    } catch (error) {
      listeners.forEach((fn) => fn(null, {error}));
    } finally { inflight = false; }
  }
  return {
    on(fn) { listeners.push(fn); if (last) fn(last, {joined:[], left:[]}); },
    setActive(v) { active = v; clearInterval(timer); timer = null; if (v) { load(); timer = setInterval(load, 3000); } },
    reload() { known = null; if (active) load(); },
    poke() { if (active) setTimeout(load, 250); },
    last: () => last,
  };
}
const graphFeed = createGraphFeed();
graphFeed.on((data, info) => { if (data) { field.setGraph(data); field.activity(info.activity || []); } });

/* ======================================================================
   3D network. Positions are the server's PCA projection of the stored
   1024-d Jina vectors; colour is the same discipline tint as everywhere else.
   Plain canvas with a perspective camera, no WebGL dependency.
   ====================================================================== */
function createGraph3D() {
  const canvas = $('#g3-canvas'), stage = $('#g3-stage'), ctx = canvas.getContext('2d');
  const statsEl = $('#g3-stats'), feedEl = $('#g3-feed'), axesEl = $('#g3-axes'), emptyEl = $('#g3-empty');
  const SANS = 'Inter, ui-sans-serif, system-ui, sans-serif';
  let W = 1, H = 1, dpr = 1, visible = false, raf = null, lastT = performance.now();
  const cam = {yaw:.6, pitch:-.32, dist:2.9, tyaw:null, tpitch:null, tdist:null};
  let clusters = [], nodes = new Map(), edges = [], youId = null, hov = null, sel = null, pulses = [], idleSince = 0, stats = null, pendingFocus = null, lastFeed = 0;
  const feed = [];

  function resize() {
    const b = stage.getBoundingClientRect(); if (!b.width) return;
    dpr = Math.min(window.devicePixelRatio || 1, 2); W = b.width; H = b.height;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr); canvas.style.width = `${W}px`; canvas.style.height = `${H}px`;
  }
  function project(x, y, z) {
    const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw), cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
    const x1 = x * cy - z * sy, z1 = x * sy + z * cy;
    const y2 = y * cp - z1 * sp, z2 = y * sp + z1 * cp;
    const depth = cam.dist + z2, f = Math.min(W, H) * 1.25 / Math.max(.2, depth);
    return {x: W / 2 + x1 * f, y: H / 2 + y2 * f, depth, f};
  }
  function update(data, {joined = [], left = [], activity = []} = {}) {
    stats = data.stats; youId = data.you; clusters = (data.clusters || []).filter((c) => c.centroid3);
    const now = performance.now(), seen = new Set();
    data.nodes.forEach((p) => {
      seen.add(p.id);
      const t = p.position3, n = nodes.get(p.id);
      if (n) { Object.assign(n, {tx:t.x, ty:t.y, tz:t.z, profile:p, dying:null}); }
      else nodes.set(p.id, {id:p.id, profile:p, x:t.x * .2, y:t.y * .2, z:t.z * .2, tx:t.x, ty:t.y, tz:t.z, born:now, dying:null, joinedLive:joined.some((j) => j.id === p.id)});
    });
    nodes.forEach((n) => { if (!seen.has(n.id) && !n.dying) n.dying = now; });
    edges = data.edges.map((e) => ({a:e.a, b:e.b, s:e.strength || 1, kind:e.kind, simulated:!!e.simulated}));
    joined.forEach((p) => { log(`+ ${cleanName(p.display_name)} joined`); edgesOf(p.id).forEach((e, i) => pulse(e, p.id, i * 120)); });
    activity.forEach((a) => {
      const e = edges.find((x) => (x.a === a.a && x.b === a.b) || (x.a === a.b && x.b === a.a)); if (!e) return;
      for (let i = 0; i < 3; i++) pulse(e, a.a, i * 200);
      const na = nodes.get(a.a), nb = nodes.get(a.b); [na, nb].forEach((n) => { if (n) n.flash = now; });
      log(`${cleanName(na?.profile.display_name || '?')} ↔ ${cleanName(nb?.profile.display_name || '?')} · ${KIND_LABEL[a.kind] || a.kind}`);
    });
    left.forEach((id) => log(`− ${cleanName(nodes.get(id)?.profile.display_name || 'a card')} left`));
    const ev = stats.explained_variance || [], synth = stats.synthetic;
    const realLinks = data.interactions?.real ?? edges.length;
    statsEl.innerHTML = `<span>${stats.count} cards · ${realLinks} real links${edges.length > realLinks ? ` · ${edges.length - realLinks} simulated` : ''} · ${clusters.length} clusters</span><span>${escapeHtml(stats.index)}</span><span>${stats.engine === 'jina_stored' ? `${escapeHtml(stats.model_id)} · ${stats.dimensions}-d stored vectors` : 'memory demo · concept vectors, not Jina'}</span><span>${synth ? `${synth} SynthDemo · ${stats.count - synth} real` : `${stats.count} real · SynthDemo hidden`}</span>`;
    axesEl.textContent = ev.length ? `PCA 3D · ${(ev.reduce((a, b) => a + b, 0) * 100).toFixed(1)}% variance (${ev.map((v) => (v * 100).toFixed(1)).join(' / ')})` : '';
    emptyEl.classList.toggle('hidden', data.nodes.length > 0);
    emptyEl.textContent = 'No discoverable cards yet. Join, or show the SynthDemo cohort.';
    if (sel && !nodes.has(sel)) sel = null;
    if (pendingFocus && nodes.has(pendingFocus)) { focusNode(nodes.get(pendingFocus), false); pendingFocus = null; }
  }
  function edgesOf(id) { return edges.filter((e) => e.a === id || e.b === id); }
  function pulse(e, from, delay = 0) { pulses.push({e, from, t0:performance.now() + delay, dur:900 + Math.random() * 500}); }
  function log(text) { feed.unshift({text, t:performance.now()}); feed.length = Math.min(feed.length, 5); renderFeed(); }
  function renderFeed() {
    const now = performance.now();
    feedEl.innerHTML = `<span class="g3-live"><i></i>live · polling Elastic</span>${feed.filter((f) => now - f.t < 30000).map((f) => `<span>${escapeHtml(f.text)}</span>`).join('')}`;
  }
  function draw(now, dt) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#070707'; ctx.fillRect(0, 0, W, H);
    // ease toward targets
    const k = 1 - Math.exp(-dt / 380);
    nodes.forEach((n) => { n.x += (n.tx - n.x) * k; n.y += (n.ty - n.y) * k; n.z += (n.tz - n.z) * k; });
    if (cam.tyaw != null) { cam.yaw += (cam.tyaw - cam.yaw) * k; cam.pitch += (cam.tpitch - cam.pitch) * k; cam.dist += (cam.tdist - cam.dist) * k; if (Math.abs(cam.tyaw - cam.yaw) < .002) cam.tyaw = null; }
    else if (!reduceMotion && now - idleSince > 2500 && !sel) cam.yaw += dt * .00008;

    // floor grid for depth cues
    ctx.strokeStyle = 'rgba(255,255,255,.045)'; ctx.lineWidth = 1;
    for (let i = -4; i <= 4; i++) {
      const a = project(i * .4, 1.35, -1.6), b = project(i * .4, 1.35, 1.6), c = project(-1.6, 1.35, i * .4), d = project(1.6, 1.35, i * .4);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.moveTo(c.x, c.y); ctx.lineTo(d.x, d.y); ctx.stroke();
    }
    const P = new Map();
    nodes.forEach((n) => {
      const life = n.dying ? 1 - clamp((now - n.dying) / 900, 0, 1) : clamp((now - n.born) / 700, 0, 1);
      if (n.dying && life <= 0) { nodes.delete(n.id); return; }
      P.set(n.id, {...project(n.x, n.y, n.z), n, life});
    });
    const focusSet = sel ? new Set([sel, ...edgesOf(sel).map((e) => (e.a === sel ? e.b : e.a))]) : null;
    // edges
    edges.forEach((e) => {
      const a = P.get(e.a), b = P.get(e.b); if (!a || !b) return;
      const on = focusSet && (e.a === sel || e.b === sel), fog = clamp(1.4 - ((a.depth + b.depth) / 2 - cam.dist) * .45, .15, 1);
      const pa = a.n.profile.position || {x:0, y:0}, pb = b.n.profile.position || {x:0, y:0};
      ctx.strokeStyle = tint((pa.x + pb.x) / 2, (pa.y + pb.y) / 2, 75, (focusSet ? (on ? .5 + e.s * .08 : .04) : (e.simulated ? .06 : .18) + e.s * (e.simulated ? .03 : .09)) * fog * Math.min(a.life, b.life), now, .8);
      ctx.lineWidth = (on ? .6 : .3) + e.s * .3;
      ctx.setLineDash(e.kind === 'view' ? [1.5, 3] : []);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    });
    ctx.setLineDash([]);
    // cluster names at their 3D centroids
    if (!sel) {
      ctx.font = '500 10px "JetBrains Mono", ui-monospace, monospace'; ctx.textAlign = 'center';
      clusters.forEach((c) => { const q = project(c.centroid3.x, c.centroid3.y - .28, c.centroid3.z), fog = clamp(1.4 - (q.depth - cam.dist) * .5, .2, 1); ctx.fillStyle = `rgba(230,230,230,${(.5 * fog).toFixed(2)})`; ctx.fillText(c.label.toUpperCase(), q.x, q.y); });
      ctx.textAlign = 'left';
    }
    ctx.globalCompositeOperation = 'lighter';
    pulses = pulses.filter((pk) => {
      const t = (now - pk.t0) / pk.dur; if (t < 0) return true; if (t >= 1) return false;
      const from = P.get(pk.from), to = P.get(pk.from === pk.e.a ? pk.e.b : pk.e.a); if (!from || !to) return false;
      const x = lerp(from.x, to.x, easeInOut(t)), y = lerp(from.y, to.y, easeInOut(t)), pos = from.n.profile.position || {x:0, y:0};
      const g = ctx.createRadialGradient(x, y, 0, x, y, 9); g.addColorStop(0, tint(pos.x, pos.y, 85, .8, now, 1.5)); g.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, 9, 0, TAU); ctx.fill();
      return true;
    });
    ctx.globalCompositeOperation = 'source-over';
    // nodes, back to front
    const order = [...P.values()].sort((a, b) => b.depth - a.depth);
    order.forEach((p) => {
      const n = p.n, pos = n.profile.position || {x:0, y:0}, you = n.id === youId, synth = isSynthetic(n.profile);
      const dim = focusSet && !focusSet.has(n.id), fog = clamp(1.5 - (p.depth - cam.dist) * .5, .25, 1);
      const r = Math.max(1.6, (you ? 5.5 : synth ? 3 : 4.2) * p.f / 260) * (.4 + .6 * easeInOut(p.life));
      const A = (dim ? .15 : 1) * fog * p.life;
      if (!dim) {
        const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r * 4.5);
        g.addColorStop(0, tint(pos.x, pos.y, 70, .22 * A, now, 1.3)); g.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p.x, p.y, r * 4.5, 0, TAU); ctx.fill();
      }
      ctx.fillStyle = tint(pos.x, pos.y, synth ? 72 : 84, A, now, synth ? .7 : 1.1);
      if ((n.profile.roles || []).some((x) => x === 'sponsor' || x === 'recruiter')) { ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(Math.PI / 4); ctx.fillRect(-r * .8, -r * .8, r * 1.6, r * 1.6); ctx.restore(); }
      else if (synth) { ctx.strokeStyle = ctx.fillStyle; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, TAU); ctx.stroke(); }
      else { ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, TAU); ctx.fill(); }
      if (you || n.joinedLive && now - n.born < 6000) {
        const ring = you ? r + 5 + (reduceMotion ? 0 : Math.sin(now / 420) * 1.5) : r + ((now - n.born) % 1200) / 1200 * 22;
        ctx.strokeStyle = `rgba(255,255,255,${you ? .6 * A : .7 * (1 - ((now - n.born) % 1200) / 1200)})`; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(p.x, p.y, ring, 0, TAU); ctx.stroke();
      }
      if (n.id === sel || n === hov) { ctx.strokeStyle = n.id === sel ? 'rgba(255,255,255,.9)' : 'rgba(255,255,255,.45)'; ctx.beginPath(); ctx.arc(p.x, p.y, r + 7, 0, TAU); ctx.stroke(); }
      if (n.flash && now - n.flash < 1400) { const q = (now - n.flash) / 1400; ctx.strokeStyle = `rgba(255,255,255,${(.9 * (1 - q)).toFixed(2)})`; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(p.x, p.y, r + 3 + q * 26, 0, TAU); ctx.stroke(); ctx.lineWidth = 1; }
      p.r = r;
    });
    // labels: you, hovered, selected, its neighbours, new arrivals, and the nearest few
    ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
    const near = new Set(order.slice(-Math.min(10, order.length)).map((p) => p.n.id));
    order.forEach((p) => {
      const n = p.n, show = n.id === youId || n === hov || n.id === sel || (focusSet ? focusSet.has(n.id) : near.has(n.id) || (n.joinedLive && performance.now() - n.born < 8000));
      if (!show || p.life < .5) return;
      ctx.font = `${n.id === sel || n.id === youId ? 600 : 500} ${n.id === sel ? 12.5 : 11}px ${SANS}`;
      ctx.fillStyle = n.id === sel || n.id === youId ? '#fff' : `rgba(225,225,225,${(clamp(1.6 - (p.depth - cam.dist) * .6, .35, .85)).toFixed(2)})`;
      ctx.fillText(cleanName(n.profile.display_name) + (n.id === youId ? ' (you)' : ''), p.x + p.r + 6, p.y);
    });
    canvas._order = order;
  }
  function frame(now) {
    const dt = Math.min(64, now - lastT); lastT = now;
    draw(now, dt);
    if (now - lastFeed > 1000) { lastFeed = now; renderFeed(); }
    raf = visible ? requestAnimationFrame(frame) : null;
  }
  function pick(sx, sy, slop = 12) {
    let best = null, bd = Infinity;
    (canvas._order || []).forEach((p) => { const d = Math.hypot(p.x - sx, p.y - sy) - (p.r || 3); if (d < bd && p.life > .5) { bd = d; best = p.n; } });
    return bd < slop ? best : null;
  }
  function focusNode(n, open = true) {
    sel = n.id;
    // turn the camera so the node faces the viewer
    const yaw = Math.atan2(-n.tx, -n.tz), pitch = clamp(-Math.atan2(n.ty, Math.hypot(n.tx, n.tz)), -1.2, 1.2);
    cam.tyaw = cam.yaw + ((yaw - cam.yaw + Math.PI) % TAU + TAU) % TAU - Math.PI; cam.tpitch = pitch * .8; cam.tdist = 2.2;
    edgesOf(n.id).forEach((e, i) => pulse(e, n.id, i * 90));
    if (open) openSheet(n.profile);
  }
  const pointers = new Map(); let drag = null;
  let pinch = null;
  canvas.addEventListener('pointerdown', (e) => {
    canvas.setPointerCapture(e.pointerId); pointers.set(e.pointerId, {x:e.offsetX, y:e.offsetY}); cam.tyaw = null; cam.tdist = null; idleSince = performance.now();
    if (pointers.size === 2) { const [a, b] = [...pointers.values()]; pinch = {d:Math.hypot(a.x - b.x, a.y - b.y) || 1, dist:cam.dist}; drag = null; }
    else drag = {x:e.offsetX, y:e.offsetY, moved:0, touch:e.pointerType === 'touch'};
  });
  canvas.addEventListener('pointermove', (e) => {
    if (pointers.has(e.pointerId)) pointers.set(e.pointerId, {x:e.offsetX, y:e.offsetY});
    if (pinch && pointers.size === 2) { const [a, b] = [...pointers.values()]; cam.dist = clamp(pinch.dist * pinch.d / (Math.hypot(a.x - b.x, a.y - b.y) || 1), 1.4, 9); idleSince = performance.now(); }
    else if (drag && pointers.has(e.pointerId)) {
      const dx = e.offsetX - drag.x, dy = e.offsetY - drag.y; drag.moved += Math.abs(dx) + Math.abs(dy);
      cam.yaw += dx * .006; cam.pitch = clamp(cam.pitch + dy * .006, -1.4, 1.4); drag.x = e.offsetX; drag.y = e.offsetY; idleSince = performance.now();
      if (drag.moved > 4) canvas.classList.add('dragging');
    } else { hov = pick(e.offsetX, e.offsetY); canvas.classList.toggle('hovering', !!hov); }
  });
  const release = (e) => {
    pointers.delete(e.pointerId);
    if (drag && drag.moved < 8 && e.type === 'pointerup') { const n = pick(e.offsetX, e.offsetY, drag.touch ? 24 : 12); if (n) focusNode(n); else { sel = null; closeSheet(); } }
    drag = null; if (pointers.size < 2) pinch = null; canvas.classList.remove('dragging');
  };
  canvas.addEventListener('pointerup', release); canvas.addEventListener('pointercancel', release);
  canvas.addEventListener('pointerleave', () => { hov = null; canvas.classList.remove('hovering'); });
  canvas.addEventListener('wheel', (e) => { e.preventDefault(); cam.tdist = null; cam.tyaw = null; cam.dist = clamp(cam.dist * Math.exp(e.deltaY * .0012), 1.4, 9); idleSince = performance.now(); }, {passive:false});
  canvas.addEventListener('keydown', (e) => {
    const step = {ArrowLeft:-.15, ArrowRight:.15}[e.key]; if (step) { e.preventDefault(); cam.yaw += step; idleSince = performance.now(); }
    if (e.key === 'Escape') { sel = null; }
  });
  new ResizeObserver(() => { if (visible) resize(); }).observe(stage);
  $('#g3-reset').addEventListener('click', () => { sel = null; cam.tyaw = .6; cam.tpitch = -.32; cam.tdist = 2.9; closeSheet(); });
  $('#g3-sponsors').addEventListener('change', () => graphFeed.reload());
  graphFeed.on((data, info) => {
    if (data) update(data, info);
    else if (info.error) {
      statsEl.textContent = info.error.status === 403 ? 'join to see the live network' : info.error.message;
      emptyEl.classList.toggle('hidden', info.error.status !== 403);
      emptyEl.innerHTML = info.error.status === 403 ? 'The live network shows opted-in attendees only. <button class="secondary compact-btn" type="button">Create your card</button>' : '';
      emptyEl.querySelector('button')?.addEventListener('click', () => switchTab('join'));
    }
  });
  return {
    setVisible(v) { visible = v; if (v) { resize(); renderFeed(); if (!raf) { lastT = performance.now(); raf = requestAnimationFrame(frame); } } },
    focusId(id) { const n = nodes.get(id); if (n) focusNode(n, false); else pendingFocus = id; },
  };
}
const g3 = createGraph3D();

/* ======================================================================
   Live telemetry feed: /api/telemetry/live (route templates, latency, Elastic
   round-trip) polled while the 2D map or the Stack tab is open.
   ====================================================================== */
function createLiveFeed() {
  let active = false, timer = null, inflight = false, seq = 0;
  const listeners = [];
  async function load() {
    if (inflight) return; inflight = true;
    try {
      const data = await api(`/api/telemetry/live?since=${seq}`);
      seq = data.seq; listeners.forEach((fn) => fn(data));
    } catch { /* telemetry is best-effort */ } finally { inflight = false; }
  }
  return {
    on(fn) { listeners.push(fn); },
    setActive(v) { if (v === active) return; active = v; clearInterval(timer); timer = null; if (v) { load(); timer = setInterval(load, 2000); } },
  };
}
const liveFeed = createLiveFeed();
liveFeed.on((d) => field.live(d));

/* ======================================================================
   My network: you at the centre, everyone you interacted with on rings by
   strength (met in person innermost, viewed profile outermost). Lines
   flash when either of you logs a new interaction.
   ====================================================================== */
function createEgo() {
  const canvas = $('#ego-canvas'), stage = $('#ego-stage'), ctx = canvas.getContext('2d'), listEl = $('#ego-list'), statsEl = $('#ego-stats');
  const SANS = 'Inter, ui-sans-serif, system-ui, sans-serif', MONO = '"JetBrains Mono", ui-monospace, monospace';
  const TIERS = [['meet', .32, 'MET IN PERSON'], ['message', .54, 'MESSAGED'], ['event', .76, 'SAME SESSION'], ['view', .98, 'VIEWED']];
  let W = 1, H = 1, R = 1, dpr = 1, visible = false, raf = null, last = performance.now();
  let you = null, people = [], ghosts = [], cross = [], hov = null, flashes = [], packets = [], clusterName = new Map();
  const placed = new Map();
  function resize() {
    const b = stage.getBoundingClientRect(); if (!b.width) return;
    dpr = Math.min(window.devicePixelRatio || 1, 2); W = b.width; H = b.height; R = Math.min(W, H) * .44;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr); canvas.style.width = `${W}px`; canvas.style.height = `${H}px`;
  }
  function update(data, info = {}) {
    const byId = new Map(data.nodes.map((n) => [n.id, n]));
    you = byId.get(data.you) || null;
    clusterName = new Map((data.clusters || []).map((c) => [c.id, c.label]));
    const mine = (data.edges || []).filter((e) => e.a === data.you || e.b === data.you);
    const ids = new Set(mine.map((e) => (e.a === data.you ? e.b : e.a)));
    people = mine.map((e) => ({id:e.a === data.you ? e.b : e.a, link:e})).filter((p) => byId.has(p.id)).map((p) => ({...p, profile:byId.get(p.id)}));
    people.sort((a, b) => (a.profile.cluster - b.profile.cluster) || (b.link.strength - a.link.strength));
    const now = performance.now();
    people.forEach((p, i) => {
      const tier = TIERS.findIndex((t) => t[0] === p.link.kind), ang = (i / Math.max(1, people.length)) * TAU - Math.PI / 2;
      const target = {x:Math.cos(ang) * TIERS[tier][1], y:Math.sin(ang) * TIERS[tier][1]};
      const old = placed.get(p.id); p.x = old ? old.x : 0; p.y = old ? old.y : 0; p.tx = target.x; p.ty = target.y; p.born = old?.born ?? now;
      placed.set(p.id, p);
    });
    // links among the people you know (second degree), drawn faintly
    cross = (data.edges || []).filter((e) => e.a !== data.you && e.b !== data.you && ids.has(e.a) && ids.has(e.b));
    // nearest neighbours by embedding: suggestions while your network is small
    const sim = (data.similar || []).filter((e) => e.a === data.you || e.b === data.you).map((e) => ({id:e.a === data.you ? e.b : e.a, s:e.similarity}))
      .filter((g) => !ids.has(g.id) && byId.has(g.id)).slice(0, 5);
    ghosts = sim.map((g, i) => ({...g, profile:byId.get(g.id), x:Math.cos(Math.PI * (.15 + i * .175)) * 1.12, y:Math.sin(Math.PI * (.15 + i * .175)) * 1.12 * -1 + .02}));
    (info.activity || []).forEach((a) => {
      if (a.a !== data.you && a.b !== data.you) return;
      const other = placed.get(a.a === data.you ? a.b : a.a); if (!other) return;
      for (let i = 0; i < 3; i++) packets.push({p:other, out:a.a === data.you, t0:now + i * 180, strong:KIND_WEIGHT[a.kind] >= 3});
      flashes.push({p:other, t0:now}, {p:null, t0:now});
    });
    renderList();
  }
  function renderList() {
    const groups = TIERS.map(([kind, , label]) => ({kind, label, items:people.filter((p) => p.link.kind === kind)}));
    const spans = new Set(people.map((p) => p.profile.cluster));
    statsEl.innerHTML = you ? `<span>${people.length} link${people.length === 1 ? '' : 's'}</span><span>${people.filter((p) => KIND_WEIGHT[p.link.kind] >= 3).length} strong</span><span>${spans.size} cluster${spans.size === 1 ? '' : 's'} reached</span><span>you: ${escapeHtml(clusterName.get(you.cluster) || '—')}</span>` : '';
    if (!you) { listEl.innerHTML = '<p class="subtle small">Join to start building your network.</p>'; return; }
    if (!people.length) {
      listEl.innerHTML = `<p class="subtle small">No links yet. Links appear only when you interact: open someone's card (that counts as a view), then log a shared session, a message or an in-person meeting.</p>${ghosts.length ? `<p class="eyebrow">Closest to you by embedding</p><ul class="ego-items">${ghosts.map((g) => `<li><button type="button" data-id="${escapeHtml(g.id)}">${escapeHtml(cleanName(g.profile.display_name))}<small>${escapeHtml(g.profile.headline || '')}</small></button><em class="mono">${Math.round(g.s * 100)}%</em></li>`).join('')}</ul>` : ''}`;
    } else {
      listEl.innerHTML = groups.filter((g) => g.items.length).map((g) => `<p class="eyebrow">${g.label.toLowerCase()} · ${g.items.length}</p><ul class="ego-items">${g.items.map((p) => `<li><button type="button" data-id="${escapeHtml(p.id)}">${escapeHtml(cleanName(p.profile.display_name))}<small>${escapeHtml(clusterName.get(p.profile.cluster) || p.profile.headline || '')}</small></button><em class="mono">${Object.entries(p.link.kinds || {}).map(([k, v]) => `${KIND_LABEL[k]}${v > 1 ? ` ×${v}` : ''}`).join(' · ') || escapeHtml(p.link.label)}</em></li>`).join('')}</ul>`).join('');
    }
    listEl.querySelectorAll('button[data-id]').forEach((b) => b.addEventListener('click', () => {
      const p = placed.get(b.dataset.id) || ghosts.find((g) => g.id === b.dataset.id); if (p) openSheet(p.profile);
    }));
  }
  const S = (x, y) => ({x:W / 2 + x * R, y:H / 2 + y * R});
  function draw(now, dt) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.fillStyle = '#070707'; ctx.fillRect(0, 0, W, H);
    const k = 1 - Math.exp(-dt / 380);
    people.forEach((p) => { p.x += (p.tx - p.x) * k; p.y += (p.ty - p.y) * k; });
    // tier rings
    ctx.textAlign = 'center'; ctx.font = `500 9px ${MONO}`;
    TIERS.forEach(([kind, r, label]) => {
      ctx.strokeStyle = 'rgba(255,255,255,.06)'; ctx.setLineDash(kind === 'view' ? [2, 5] : []); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(W / 2, H / 2, r * R, 0, TAU); ctx.stroke();
      ctx.fillStyle = 'rgba(255,255,255,.28)'; ctx.fillText(label, W / 2, H / 2 - r * R - 5);
    });
    ctx.setLineDash([]);
    if (!you) return;
    const yp = you.position || {x:0, y:0}, c = S(0, 0);
    cross.forEach((e) => { const a = placed.get(e.a), b = placed.get(e.b); if (!a || !b) return; const pa = S(a.x, a.y), pb = S(b.x, b.y); ctx.strokeStyle = 'rgba(255,255,255,.08)'; ctx.lineWidth = .6 + e.strength * .15; ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke(); });
    ghosts.forEach((g) => { const q = S(g.x, g.y), pos = g.profile.position || {x:0, y:0}; ctx.strokeStyle = tint(pos.x, pos.y, 70, .25, now); ctx.setLineDash([2, 5]); ctx.beginPath(); ctx.moveTo(c.x, c.y); ctx.lineTo(q.x, q.y); ctx.stroke(); ctx.setLineDash([]); ctx.beginPath(); ctx.arc(q.x, q.y, 4, 0, TAU); ctx.stroke(); g.sx = q.x; g.sy = q.y; });
    people.forEach((p) => {
      const q = S(p.x, p.y), pos = p.profile.position || {x:0, y:0}, st = p.link.strength;
      ctx.strokeStyle = tint((pos.x + yp.x) / 2, (pos.y + yp.y) / 2, 78, .25 + st * .1, now, 1); ctx.lineWidth = .5 + st * .45;
      ctx.setLineDash(p.link.kind === 'view' ? [2, 4] : []); ctx.beginPath(); ctx.moveTo(c.x, c.y); ctx.lineTo(q.x, q.y); ctx.stroke(); ctx.setLineDash([]);
    });
    ctx.globalCompositeOperation = 'lighter';
    packets = packets.filter((pk) => {
      const t = (now - pk.t0) / 900; if (t < 0) return true; if (t >= 1) return false;
      const q = S(pk.p.x, pk.p.y), e = easeInOut(t), x = pk.out ? lerp(c.x, q.x, e) : lerp(q.x, c.x, e), y = pk.out ? lerp(c.y, q.y, e) : lerp(q.y, c.y, e);
      const pos = pk.p.profile.position || {x:0, y:0}, g = ctx.createRadialGradient(x, y, 0, x, y, pk.strong ? 14 : 9);
      g.addColorStop(0, tint(pos.x, pos.y, 86, .9, now, 1.5)); g.addColorStop(1, 'rgba(0,0,0,0)'); ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, 14, 0, TAU); ctx.fill();
      return true;
    });
    flashes = flashes.filter((f) => {
      const t = (now - f.t0) / 1100; if (t >= 1) return false; if (t < 0) return true;
      const q = f.p ? S(f.p.x, f.p.y) : c; ctx.strokeStyle = `rgba(255,255,255,${(.8 * (1 - t)).toFixed(2)})`; ctx.lineWidth = 1.4; ctx.beginPath(); ctx.arc(q.x, q.y, 6 + t * 30, 0, TAU); ctx.stroke();
      return true;
    });
    ctx.globalCompositeOperation = 'source-over';
    ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    people.forEach((p) => {
      const q = S(p.x, p.y), pos = p.profile.position || {x:0, y:0}, r = 3 + p.link.strength * .7, appear = clamp((now - p.born) / 600, 0, 1);
      const g = ctx.createRadialGradient(q.x, q.y, 0, q.x, q.y, r * 4); g.addColorStop(0, tint(pos.x, pos.y, 70, .25, now, 1.3)); g.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(q.x, q.y, r * 4, 0, TAU); ctx.fill();
      ctx.fillStyle = tint(pos.x, pos.y, 84, appear, now, 1.1); ctx.beginPath(); ctx.arc(q.x, q.y, r * (.4 + .6 * appear), 0, TAU); ctx.fill();
      if (p === hov) { ctx.strokeStyle = 'rgba(255,255,255,.6)'; ctx.beginPath(); ctx.arc(q.x, q.y, r + 5, 0, TAU); ctx.stroke(); }
      ctx.font = `500 11px ${SANS}`; ctx.fillStyle = 'rgba(230,230,230,.85)'; const right = q.x >= c.x;
      ctx.textAlign = right ? 'left' : 'right'; ctx.fillText(cleanName(p.profile.display_name), q.x + (right ? r + 6 : -r - 6), q.y);
      p.sx = q.x; p.sy = q.y; p.r = r;
    });
    ctx.textAlign = 'center';
    ctx.font = `500 10px ${MONO}`; ctx.fillStyle = 'rgba(255,255,255,.4)';
    ghosts.forEach((g) => ctx.fillText(cleanName(g.profile.display_name).split(' ')[0], g.sx, g.sy - 11));
    const g = ctx.createRadialGradient(c.x, c.y, 0, c.x, c.y, 30); g.addColorStop(0, tint(yp.x, yp.y, 75, .4, now, 1.3)); g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(c.x, c.y, 30, 0, TAU); ctx.fill();
    ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(c.x, c.y, 6, 0, TAU); ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,.6)'; ctx.beginPath(); ctx.arc(c.x, c.y, 11 + (reduceMotion ? 0 : Math.sin(now / 420) * 1.5), 0, TAU); ctx.stroke();
    ctx.font = `600 12px ${SANS}`; ctx.fillStyle = '#fff'; ctx.fillText('you', c.x, c.y + 24);
  }
  function frame(now) { const dt = Math.min(64, now - last); last = now; draw(now, dt); raf = visible ? requestAnimationFrame(frame) : null; }
  function pick(x, y, slop) {
    let best = null, bd = Infinity;
    [...people, ...ghosts].forEach((p) => { const d = Math.hypot((p.sx ?? -99) - x, (p.sy ?? -99) - y); if (d < bd) { bd = d; best = p; } });
    return bd < slop ? best : null;
  }
  canvas.addEventListener('pointermove', (e) => { hov = pick(e.offsetX, e.offsetY, 16); canvas.classList.toggle('hovering', !!hov); });
  canvas.addEventListener('pointerup', (e) => { const p = pick(e.offsetX, e.offsetY, e.pointerType === 'touch' ? 28 : 16); if (p) openSheet(p.profile); });
  new ResizeObserver(() => { if (visible) resize(); }).observe(stage);
  graphFeed.on((data, info) => {
    if (data) update(data, info);
    else if (info.error) { statsEl.textContent = info.error.status === 403 ? 'join to build your network' : info.error.message; you = null; people = []; renderList(); }
  });
  return { setVisible(v) { visible = v; if (v) { resize(); if (!raf) { last = performance.now(); raf = requestAnimationFrame(frame); } } } };
}
const ego = createEgo();

/* ======================================================================
   Stack: what runs NETwork, with the request path animated from real
   telemetry and live numbers from the API and Elastic.
   ====================================================================== */
function createStack() {
  const svg = $('#stack-diagram'), tiles = $('#stack-tiles'), spark = $('#stack-spark'), routes = $('#stack-routes'), pca = $('#stack-pca'), note = $('#stack-note');
  let visible = false, lastSeq = null;
  // Which hops a request takes, by route template
  const PATHS = {
    browser_api: 'M110 70 L300 70', api_es: 'M440 70 L600 70', api_worker: 'M370 100 L370 210', worker_es: 'M440 240 L600 110',
    api_dash0: 'M370 100 C 370 300, 220 300, 180 300', es_jina: 'M670 100 L670 180', es_agent: 'M740 70 C 800 70, 800 250, 740 250',
  };
  const ROUTE_HOPS = (route) => {
    if (route.includes('/api/ask')) return ['browser_api', 'api_es', 'es_agent', 'api_dash0'];
    if (route.includes('/api/profiles')) return ['browser_api', 'api_es', 'api_worker', 'worker_es', 'es_jina', 'api_dash0'];
    if (/search|people|graph|interactions|me|trust|connection/.test(route)) return ['browser_api', 'api_es', 'api_dash0'];
    return ['browser_api', 'api_dash0'];
  };
  function fly(hops, delay, error) {
    hops.forEach((hop, i) => setTimeout(() => {
      const path = svg.querySelector(`path[data-hop="${hop}"]`); if (!path || !visible) return;
      const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      dot.setAttribute('r', '4'); dot.setAttribute('class', error ? 'dot err' : 'dot');
      const len = path.getTotalLength(), t0 = performance.now(), dur = 650;
      svg.appendChild(dot); path.classList.add('hot');
      const step = (now) => { const p = Math.min(1, (now - t0) / dur), pt = path.getPointAtLength(len * easeInOut(p)); dot.setAttribute('cx', pt.x); dot.setAttribute('cy', pt.y); if (p < 1) requestAnimationFrame(step); else { dot.remove(); path.classList.remove('hot'); } };
      requestAnimationFrame(step);
    }, delay + i * 260));
  }
  function render(d) {
    const e = d.elastic || {};
    const tile = (label, value, sub) => `<div class="tile"><span class="eyebrow">${label}</span><strong>${escapeHtml(String(value))}</strong><small>${escapeHtml(sub)}</small></div>`;
    tiles.innerHTML = [
      tile('API throughput', `${d.rps.toFixed(1)}/s`, `${d.total} requests · last ${d.window_seconds / 60} min`),
      tile('Latency p50 / p95', `${d.p50} / ${d.p95} ms`, `${d.errors} error responses`),
      tile('Elastic round trip', e.count_ms != null ? `${e.count_ms} ms` : '—', d.engine === 'elasticsearch' ? `count on ${e.index}` : 'memory demo · no Elastic'),
      tile('Cards indexed', e.index_docs ?? e.cards ?? '—', `${e.cards ?? '—'} discoverable in the graph`),
      tile('Embedding', e.dimensions ? `${e.dimensions}-d` : '—', e.model_id || ''),
      tile('Clusters', e.clusters ?? '—', 'k-means on stored vectors · named from member tags'),
    ].join('');
    const max = Math.max(1, ...d.series), w = 300, h = 56;
    const pts = d.series.map((v, i) => `${(i / (d.series.length - 1) * w).toFixed(1)},${(h - 4 - v / max * (h - 10)).toFixed(1)}`).join(' ');
    spark.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Requests per second over the last two minutes, peak ${max.toFixed(1)}"><polyline points="0,${h} ${pts} ${w},${h}" class="area"/><polyline points="${pts}" class="line"/></svg><p class="mono small-note">req/s · last 2 min · peak ${max.toFixed(1)}</p>`;
    const top = Math.max(1, ...d.routes.map((r) => r.p95));
    routes.innerHTML = d.routes.length ? `<ul class="lat">${d.routes.map((r) => `<li><code>${escapeHtml(r.route)}</code><span class="lat-bar"><b class="p50" style="--w:${(r.p50 / top * 100).toFixed(1)}%"></b><b class="p95" style="--w:${(r.p95 / top * 100).toFixed(1)}%"></b></span><em class="mono">${r.count}× · ${r.p50}/${r.p95}ms${r.errors ? ` · ${r.errors} err` : ''}</em></li>`).join('')}</ul>` : '<p class="subtle small">No requests yet.</p>';
    const ev = e.explained_variance || [];
    pca.innerHTML = ev.length ? `<ul class="bars">${ev.map((v, i) => `<li><span>PC${i + 1}</span><b style="--w:${(v / Math.max(...ev) * 100).toFixed(1)}%"></b><em class="mono">${(v * 100).toFixed(1)}%</em></li>`).join('')}</ul><p class="subtle small">Share of variance in the ${e.dimensions}-d vectors that each axis of the 2D/3D maps keeps. The rest is why the maps are approximate and clustering runs on the full vectors.</p>` : '<p class="subtle small">Open the 2D or 3D map once to compute the projection.</p>';
    note.textContent = `${d.engine === 'elasticsearch' ? 'Elastic Cloud Serverless' : 'memory demo'} · ${d.dash0 ? 'spans and metrics exporting to Dash0' : 'Dash0 export not configured'} · updated ${new Date().toLocaleTimeString()}`;
    if (lastSeq != null && visible) d.recent.filter((r) => r.seq > lastSeq).slice(-8).forEach((r, i) => fly(ROUTE_HOPS(r.route), i * 140, r.status >= 400));
    lastSeq = d.seq;
  }
  liveFeed.on((d) => { if (visible) render(d); });
  return { setVisible(v) { visible = v; if (!v) lastSeq = null; } };
}
const stack = createStack();


/* SynthDemo visibility, applied to every surface. */
function renderSynthToggle() {
  const b = $('#synth-toggle'); b.setAttribute('aria-pressed', String(showSynth)); b.textContent = `SynthDemo · ${showSynth ? 'shown' : 'hidden'}`;
  b.classList.toggle('off', !showSynth);
}
$('#synth-toggle').addEventListener('click', () => {
  showSynth = !showSynth; renderSynthToggle();
  try { localStorage.setItem('network.showSynth', showSynth ? '1' : '0'); } catch { /* per-viewer convenience only */ }
  people.loaded = false;
  if ($('.shell').dataset.panel === 'people') runPeople();
  if (lastCompare && $('#question').value.trim().length >= 3) $('#search-form').requestSubmit();
  graphFeed.reload();
});
renderSynthToggle();

async function boot() {
  try {
    const session = await post('/api/session', {});
    $('#runtime-status').textContent = session.mode === 'memory_demo' ? 'demo · in-memory lexical search · data resets on restart' : `event ${session.event_id} · elastic connected`;
    if (session.mode === 'memory_demo') $('#seed-demo').classList.remove('hidden');
    await refreshMe();
    if (location.pathname.startsWith('/connect/')) await loadConnection();
    else if (myState === 'indexed') switchTab('people');
  } catch (error) { showStatusError(error); }
}
boot();
