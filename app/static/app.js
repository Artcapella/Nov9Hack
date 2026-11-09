const $ = (selector) => document.querySelector(selector);
const splitTags = (value) => value.split(',').map((item) => item.trim()).filter(Boolean);
const selected = (name) => [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((input) => input.value);

document.querySelectorAll('.tab').forEach((tab) => tab.addEventListener('click', () => {
  document.querySelectorAll('.tab,.panel').forEach((el) => el.classList.remove('active'));
  tab.classList.add('active'); $(`#${tab.dataset.panel}`).classList.add('active');
}));

$('#profile-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget, fd = new FormData(form), result = $('#profile-result');
  result.textContent = 'Creating your opt-in conference card…';
  const hasResume = fd.get('resume')?.size > 0;
  try {
    let response;
    if (hasResume) {
      fd.set('can_help_with', JSON.stringify(splitTags(fd.get('can_help_with'))));
      fd.set('looking_for', JSON.stringify(splitTags(fd.get('looking_for'))));
      fd.set('roles', JSON.stringify(['peer'])); fd.set('formats', JSON.stringify(selected('formats')));
      response = await fetch('/api/profiles/from-resume', {method:'POST', body:fd});
    } else {
      const body = { display_name:fd.get('display_name'), headline:fd.get('headline'), public_summary:fd.get('public_summary'), can_help_with:splitTags(fd.get('can_help_with')), looking_for:splitTags(fd.get('looking_for')), roles:['peer'], formats:selected('formats'), availability:fd.get('availability'), email:fd.get('email') || null, linkedin_url:fd.get('linkedin_url') || null, consent_to_discover:fd.get('consent_to_discover') === 'on', consent_to_resume_parsing:false };
      response = await fetch('/api/profiles', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    }
    const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'Could not create profile');
    result.textContent = `You’re live in NETwork until ${new Date(data.discoverable_until).toLocaleTimeString([], {hour:'numeric', minute:'2-digit'})}.`;
    form.reset();
  } catch (error) { result.textContent = error.message; }
});

document.querySelector('input[name="resume"]')?.addEventListener('change', async (event) => {
  const file = event.target.files[0], result = $('#resume-preview'), form = $('#profile-form');
  if (!file) return;
  if (!form.querySelector('input[name="consent_to_resume_parsing"]').checked) {
    result.textContent = 'Check resume-parsing consent to generate an editable profile preview.'; return;
  }
  const fd = new FormData(); fd.append('resume', file); fd.append('consent_to_resume_parsing', 'true');
  result.textContent = 'Extracting editable skills and project hints…';
  try {
    const response = await fetch('/api/resumes/preview', {method:'POST', body:fd}); const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Could not parse this file');
    const hints = data.contact_hints || {}, suggestions = data.suggestions || {};
    const setIfEmpty = (name, value) => { const input=form.elements[name]; if (input && !input.value && value) input.value=value; };
    setIfEmpty('display_name', hints.suggested_name); setIfEmpty('email', hints.email); setIfEmpty('linkedin_url', hints.linkedin_url);
    setIfEmpty('can_help_with', (suggestions.skills || []).join(', '));
    if (!form.elements.public_summary.value && (suggestions.project_highlights || []).length) form.elements.public_summary.value = suggestions.project_highlights.slice(0,2).join(' ');
    result.textContent = `Parsed ${suggestions.skills?.length || 0} skill hints. Review every field before joining.`;
  } catch (error) { result.textContent = error.message; }
});

$('#search-form')?.addEventListener('submit', async (event) => {
  event.preventDefault(); const question = $('#question').value, results = $('#search-results'), rag = $('#rag-answer');
  results.innerHTML = '<p class="subtle">Searching lexical and semantic signals…</p>'; rag.classList.add('hidden');
  try {
    const response = await fetch('/api/search', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question, sponsor_intent:$('#sponsor-intent').checked, size:5})});
    const hits = await response.json(); if (!response.ok) throw new Error(hits.detail || 'Search failed');
    results.innerHTML = ''; if (!hits.length) results.innerHTML = '<p class="subtle">No opt-in profiles matched yet. Invite someone to join through the QR form.</p>';
    hits.forEach((hit) => results.appendChild(renderHit(hit)));
    const ragResponse = await fetch('/api/ask', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question})});
    const answer = await ragResponse.json(); renderAnswer(answer, rag);
  } catch (error) { results.innerHTML = `<p class="subtle">${error.message}</p>`; }
});

function renderHit(hit) {
  const node = $('#result-template').content.cloneNode(true); const card = node.querySelector('.match-card');
  node.querySelector('.role').textContent = hit.roles.join(' · '); node.querySelector('.name').textContent = hit.display_name;
  node.querySelector('.headline').textContent = hit.headline || hit.availability; node.querySelector('.summary').textContent = hit.public_summary;
  hit.match_reasons.forEach((reason) => { const li=document.createElement('li'); li.textContent=reason; node.querySelector('.reasons').appendChild(li); });
  node.querySelector('.connect').addEventListener('click', () => createConnection(hit)); return node;
}

async function createConnection(hit) {
  const requester_name = window.prompt('Your name for this introduction request:'); if (!requester_name) return;
  const message = window.prompt(`Short note to ${hit.display_name}:`, 'I think we may be able to help each other with this. Want to connect?'); if (!message) return;
  const response = await fetch('/api/connection-requests', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({requester_name, recipient_profile_id:hit.id, message, preferred_channel:'meetup_qr'})});
  const item = await response.json(); if (!response.ok) return alert(item.detail || 'Could not create request');
  const popup = window.open('', '_blank'); popup.document.write(`<title>NETwork QR</title><style>body{font-family:system-ui;text-align:center;padding:25px}img{max-width:300px}</style><h2>Scan to accept introduction</h2><p>This expires in 20 minutes.</p><img src="/api/connection-requests/${item.token}/qr.png"/>`);
}

function renderAnswer(answer, target) {
  target.classList.remove('hidden'); target.innerHTML = `<p class="eyebrow">${answer.mode.replaceAll('_',' ')}</p><h3>NETwork Concierge</h3><p>${escapeHtml(answer.answer || '')}</p><div>${(answer.sources || []).map((source,index) => `<div class="source"><strong>[${index+1}] ${escapeHtml(source.title)}</strong><br>${escapeHtml(source.excerpt || '')}</div>`).join('')}</div>`;
}

async function loadConnection() {
  const token = location.pathname.split('/').pop(); $('#app-view').classList.add('hidden'); $('#connect-view').classList.remove('hidden');
  const card = $('#connection-card');
  try { const response = await fetch(`/api/connection-requests/${token}`); const item = await response.json(); if (!response.ok) throw new Error(item.detail || 'Unavailable');
    card.innerHTML = `<h2>${escapeHtml(item.requester_name)} wants to meet.</h2><p>${escapeHtml(item.message)}</p><p class="subtle">Accepting reveals only the contact methods the attendee chose to share.</p><button class="primary" id="accept">Accept connection</button>`;
    $('#accept').addEventListener('click', async () => { const accept = await fetch(`/api/connection-requests/${token}/accept`, {method:'POST'}); const data=await accept.json(); card.innerHTML = accept.ok ? `<h2>Connection accepted</h2><p>LinkedIn: ${link(data.contacts?.linkedin_url)}</p><p>Email: ${escapeHtml(data.contacts?.email || 'Not shared')}</p>` : `<p>${escapeHtml(data.detail || 'This request expired.')}</p>`; });
  } catch (error) { card.innerHTML = `<p>${escapeHtml(error.message)}</p>`; }
}
function link(url) { return url ? `<a href="${escapeAttr(url)}" target="_blank" rel="noopener">Open LinkedIn</a>` : 'Not shared'; }
function escapeHtml(value) { const d=document.createElement('div'); d.textContent=value; return d.innerHTML; } function escapeAttr(value) { return escapeHtml(value).replaceAll('"','&quot;'); }
if (location.pathname.startsWith('/connect/')) loadConnection();
