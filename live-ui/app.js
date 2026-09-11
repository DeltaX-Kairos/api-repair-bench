(function (global) {
  'use strict';
  const TERMINAL = new Set(['completed', 'stopped', 'uncertain', 'failed']);
  const ERRORS = {
    disabled: 'New runs are disabled. You can still explore the saved replay.',
    live_disabled: 'New runs are disabled. You can still explore the saved replay.',
    busy: 'Another run is already active. Wait for it to finish before starting another.',
    quota: 'The run allowance has been reached. Saved evidence remains available.',
    quota_exhausted: 'The run allowance has been reached. Saved evidence remains available.',
    invalid_input: 'The function or checks were not accepted. Review the input requirements.',
    not_found: 'This run is unavailable in the current browser session.',
    origin_not_allowed: 'This site is not enabled for live runs.'
  };
  const UNAVAILABLE = {
    credential_unavailable: 'The live provider connection is not configured. Saved results remain available.',
    operator_disabled: 'New runs are currently switched off. Explore the saved results instead.',
    busy: 'A repair is already running. New submissions are paused until it finishes.',
    allowance_exhausted: 'The live demonstration allowance has been used. Saved evidence remains available.',
    session_limit: 'This browser session has used its live run allowance. You can inspect and download its evidence.',
    visitor_limit: 'This browser session has used its live run allowance. You can inspect and download its evidence.'
  };
  function availabilityMessage(status) {
    if (['session_limit', 'visitor_limit'].includes(status.reason) && Number.isInteger(status.limits?.jobs_per_visitor))
      return `This browser session has used its ${status.limits.jobs_per_visitor} live runs. You can inspect and download its evidence.`;
    return UNAVAILABLE[status.reason] || 'New runs are currently unavailable. Explore saved results using the replay link above.';
  }
  function bytes(text) { return new TextEncoder().encode(text).length; }
  function customPayload(source, rawChecks) {
    if (!source.trim() || bytes(source) > 8192) throw new Error('Enter one Python function of no more than 8 KB.');
    let checks;
    try { checks = JSON.parse(rawChecks); } catch (_) { throw new Error('The checks must be valid JSON. Check quotes and trailing commas.'); }
    if (!checks || Array.isArray(checks) || typeof checks !== 'object' || typeof checks.description !== 'string' || !checks.description.trim()) throw new Error('Include a description of the intended behavior.');
    if (!Array.isArray(checks.checks) || checks.checks.length < 1 || checks.checks.length > 8) throw new Error('Provide between one and eight checks.');
    const ids = new Set();
    for (const check of checks.checks) {
      if (!check || typeof check !== 'object' || typeof check.id !== 'string' || !check.id.trim() || ids.has(check.id)) throw new Error('Give every check a unique, nonempty id.');
      ids.add(check.id);
      if (!Array.isArray(check.args)) throw new Error('Every check needs an args array, even when empty.');
      if (check.kwargs !== undefined && (!check.kwargs || Array.isArray(check.kwargs) || typeof check.kwargs !== 'object')) throw new Error('Optional kwargs must be a JSON object.');
      const expected = Object.prototype.hasOwnProperty.call(check, 'expected');
      const error = Object.prototype.hasOwnProperty.call(check, 'error');
      if (expected === error || (error && (typeof check.error !== 'string' || !check.error.trim()))) throw new Error('Each check needs either expected or an exception name in error, but not both.');
    }
    const payload = {source, checks};
    if (bytes(JSON.stringify(payload)) > 24576) throw new Error('The function and checks together must fit within 24 KB.');
    return payload;
  }
  class LiveClient {
    constructor(fetcher) {
      this.fetch = fetcher; this.submitting = false; this.jobId = null; this.ambiguous = false;
      this.advancing = false; this.advanceReservations = new Set(); this.advanceError = null;
    }
    async request(path, options = {}) {
      let response;
      try { response = await this.fetch(path, {credentials: 'same-origin', cache: 'no-store', ...options}); }
      catch (_) { const error = new Error('Connection lost. The request may have reached the server.'); error.ambiguous = true; throw error; }
      let data;
      try { data = await response.json(); }
      catch (_) { const error = new Error('The server returned an unreadable response.'); error.ambiguous = true; throw error; }
      if (!response.ok) {
        const error = new Error(ERRORS[data.error] || (response.status === 429 ? 'Run capacity is currently unavailable. Please return to the saved results.' : response.status === 403 ? 'Live access is unavailable for this session.' : response.status === 400 ? 'The function or checks were not accepted. Review the input requirements.' : 'The server could not complete this request.'));
        error.ambiguous = response.status >= 500;
        throw error;
      }
      return data;
    }
    status() { return this.request('/api/live/status'); }
    async start(payload) {
      if (this.submitting || this.jobId || this.ambiguous) throw new Error('This run is already submitted or its outcome is uncertain.');
      this.submitting = true;
      try {
        const job = await this.request('/api/live/runs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
        if (!job || typeof job.job_id !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(job.job_id)) { const e = new Error('No usable run identifier was returned. Do not submit again.'); e.ambiguous = true; throw e; }
        this.jobId = job.job_id;
        return job;
      } catch (error) { if (error.ambiguous) this.ambiguous = true; throw error; }
      finally { this.submitting = false; }
    }
    poll() {
      if (!this.jobId) return Promise.reject(new Error('No run identifier is available.'));
      return this.request('/api/live/runs/' + encodeURIComponent(this.jobId));
    }
    advanceKey(job) { return job.job_id + ':' + job.phase_token; }
    canAdvance(job) {
      return !this.advancing && job && job.job_id === this.jobId && !TERMINAL.has(job.status)
        && job.needs_advance === true && typeof job.phase_token === 'string'
        && job.phase_token.length > 0 && job.phase_token.length <= 1024
        && !this.advanceReservations.has(this.advanceKey(job));
    }
    async advance(job) {
      if (!this.canAdvance(job)) throw new Error('This phase is already submitted or is not ready to advance.');
      const key = this.advanceKey(job);
      // Reserve the phase before dispatch. Neither polling nor a lost response
      // may resend this token. Server-side durable tokens enforce the same rule.
      this.advanceReservations.add(key); this.advancing = true; this.advanceError = null;
      try {
        if (this.onAdvanceReserved) this.onAdvanceReserved();
        return await this.request('/api/live/runs/' + encodeURIComponent(this.jobId) + '/advance', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({phase_token: job.phase_token})
        });
      } catch (error) { this.advanceError = {key, message: error.message}; throw error; }
      finally { this.advancing = false; }
    }
    async prepareAnother(previous) {
      if (this.submitting || this.advancing || this.ambiguous || !previous || !['completed', 'stopped', 'failed'].includes(previous.status) || previous.job_id !== this.jobId)
        throw new Error('The current run must reach a known stopping point before starting another.');
      const status = await this.status();
      if (status.enabled !== true || status.replay_only === true || status.jobs_remaining === 0)
        throw new Error(availabilityMessage(status));
      this.jobId = null;
      return status;
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {LiveClient, customPayload, TERMINAL};
  if (!global.document) return;
  const $ = id => document.getElementById(id);
  const client = new LiveClient(global.fetch.bind(global));
  try {
    const sent = JSON.parse(sessionStorage.getItem('repair-bench-live-phases') || '[]');
    if (Array.isArray(sent)) client.advanceReservations = new Set(sent.filter(x => typeof x === 'string').slice(-64));
  } catch (_) {}
  client.onAdvanceReserved = () => {
    try { sessionStorage.setItem('repair-bench-live-phases', JSON.stringify([...client.advanceReservations].slice(-64))); } catch (_) {}
  };
  let available = false, latest = null, polls = 0, timer = null, polling = false;
  function message(id, text) { $(id).textContent = text; $(id).hidden = !text; }
  function el(tag, text, cls) { const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text); if (cls) node.className = cls; return node; }
  function lock() { $('start').disabled = !available || client.submitting || !!client.jobId || client.ambiguous; }
  function storage(value) { try { if (value) sessionStorage.setItem('repair-bench-live-job', value); } catch (_) {} }
  function title(stage) { return String(stage || 'Update').replace(/[_-]+/g, ' ').replace(/^./, c => c.toUpperCase()); }
  function render(job) {
    latest = job;
    $('empty-state').hidden = true;
    $('run-state').textContent = title(job.status);
    const summaries = {queued: 'Queued. The server will start this run when ready.', running: 'Working through the bounded repair workflow.', completed: 'Run complete. Inspect each proposal and its observed checks below.', stopped: 'The workflow stopped. Review the trace for its stopping reason.', uncertain: 'An execution outcome is uncertain. It has not been counted as a pass and will not be retried automatically.', failed: 'The workflow could not finish. The trace records where it stopped.'};
    $('run-summary').textContent = summaries[job.status] || 'Waiting for the next update.';
    $('trace').replaceChildren();
    const trace = Array.isArray(job.trace) ? job.trace : [];
    for (const item of trace.slice(-40)) {
      const li = el('li'); li.append(el('strong', title(item.stage)));
      if (item.at) {
        const date = new Date(typeof item.at === 'number' ? item.at * (item.at < 1e12 ? 1000 : 1) : item.at);
        if (!Number.isNaN(date.getTime())) li.append(el('time', date.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'})));
      }
      li.append(el('p', item.message || '')); $('trace').append(li);
    }
    const stageText = trace.map(item => item.stage).join(' ').toLowerCase();
    $('step-propose').classList.toggle('active', trace.length > 0);
    $('step-test').classList.toggle('active', /sandbox|verif|test/.test(stageText));
    $('step-correct').classList.toggle('active', /correct/.test(stageText));
    $('step-stop').classList.toggle('active', TERMINAL.has(job.status));
    $('attempts').replaceChildren();
    for (const receipt of (Array.isArray(job.receipts) ? job.receipts : [])) {
      const card = el('article', undefined, 'attempt');
      const top = el('div', undefined, 'attempt-top');
      top.append(el('h3', receipt.stage === 'correction' ? 'Correction proposal' : 'Initial proposal'));
      const checks = Array.isArray(receipt.checks) ? receipt.checks : [];
      const tested = receipt.sandbox && receipt.sandbox.completed === true;
      top.append(el('span', tested && checks.length ? `${checks.filter(c => c.passed === true).length} / ${checks.length} checks passed` : title(receipt.status || 'Awaiting checks'), 'score'));
      card.append(top);
      if (receipt.proposal && receipt.proposal.explanation) card.append(el('p', receipt.proposal.explanation));
      if (checks.length) {
        const list = el('ul', undefined, 'checks');
        for (const check of checks) list.append(el('li', `${check.passed === true ? '✓' : check.passed === false ? '×' : '·'} ${title(check.id)}`, check.passed === true ? 'passed' : 'failed'));
        card.append(list);
      }
      if (receipt.proposal && receipt.proposal.source) {
        const details = el('details'); details.open = true; details.append(el('summary', 'Inspect generated function'));
        const pre = el('pre'); pre.tabIndex = 0; pre.append(el('code', receipt.proposal.source)); details.append(pre); card.append(details);
      }
      if (receipt.run_id) card.append(el('p', 'Evidence record: ' + receipt.run_id));
      $('attempts').append(card);
    }
    $('download').hidden = !TERMINAL.has(job.status);
    $('new-run').hidden = !['completed', 'stopped', 'failed'].includes(job.status);
    lock();
  }
  async function poll() {
    if (polling) return;
    polling = true; clearTimeout(timer); $('resume').hidden = true;
    try {
      const job = await client.poll(); render(job); polls += 1;
      const reserved = job.needs_advance === true && typeof job.phase_token === 'string'
        && client.advanceReservations.has(client.advanceKey(job));
      if (reserved && !client.advancing) message('connection', 'This phase was already requested. Reading its saved state only; it will not be submitted again.');
      else message('connection', '');
      if (!TERMINAL.has(job.status)) {
        if (client.canAdvance(job)) {
          // Deliberately do not await: read-only polls keep the trace current
          // while the single phase request performs its provider work.
          client.advance(job).then(() => {
            if (!polling) { clearTimeout(timer); timer = setTimeout(poll, 0); }
          }).catch(error => {
            message('connection', error.message + ' Reading saved state only; this phase will not be resent.');
          });
        }
        if (polls < 120) timer = setTimeout(poll, 3000);
        else { message('connection', 'This run is taking longer than expected. Check its saved state again; no new run will be started.'); $('resume').hidden = false; }
      }
    } catch (error) { message('connection', error.message + ' Checking again reads this same run; it does not resubmit it.'); $('resume').hidden = false; }
    finally { polling = false; }
  }
  $('resume').addEventListener('click', () => { polls = 0; poll(); });
  $('new-run').addEventListener('click', async () => {
    // Read service availability before presenting a fresh, explicit submission.
    $('new-run').disabled = true;
    try {
      await client.prepareAnother(latest);
      available = true; polls = 0;
      // Keep the completed record visible, downloadable, and restorable until
      // the next explicit submission returns a new job identifier.
      $('new-run').hidden = true;
      $('download').textContent = 'Download previous evidence ↓';
      $('run-summary').textContent = 'Ready for another repair. The previous run remains below until you start the next one.';
      message('connection', ''); message('form-error', ''); lock(); $('start').focus();
    } catch (error) { message('connection', error.message); }
    finally { $('new-run').disabled = false; }
  });
  for (const radio of document.querySelectorAll('input[name="mode"]')) radio.addEventListener('change', () => {
    const custom = document.querySelector('input[name="mode"]:checked').value === 'custom';
    $('custom-panel').hidden = !custom; $('case-panel').hidden = custom; message('form-error', '');
  });
  $('repair-form').addEventListener('submit', async event => {
    event.preventDefault(); if ($('start').disabled) return;
    message('form-error', ''); let payload;
    try { payload = document.querySelector('input[name="mode"]:checked').value === 'custom' ? customPayload($('source').value, $('checks').value) : {case: 'schema'}; }
    catch (error) { message('form-error', error.message); return; }
    $('start').disabled = true;
    try { const job = await client.start(payload); storage(client.jobId); $('download').textContent = 'Download evidence ↓'; render(job); poll(); }
    catch (error) { message('form-error', error.message + (error.ambiguous ? ' Do not submit again: the server may already be working on it.' : '')); }
    finally { lock(); }
  });
  $('download').addEventListener('click', () => {
    if (!latest || !TERMINAL.has(latest.status)) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify({exported_at: new Date().toISOString(), ...latest}, null, 2)], {type: 'application/json'}));
    const link = el('a'); link.href = url; link.download = 'repair-evidence-' + latest.job_id + '.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  (async () => {
    try {
      const status = await client.status(); available = status.enabled === true && status.replay_only !== true;
      $('availability').textContent = available ? 'Live runs available' : 'Saved replay only';
      $('availability-detail').textContent = available ? 'Start one bounded workflow. You will see progress and recorded results here.' : availabilityMessage(status);
    } catch (_) { $('availability').textContent = 'Live service unavailable'; $('availability-detail').textContent = 'The live service could not be reached. No model call has been made by this page.'; }
    try { const saved = sessionStorage.getItem('repair-bench-live-job'); if (saved && /^[a-zA-Z0-9_-]{1,128}$/.test(saved)) { client.jobId = saved; poll(); } } catch (_) {}
    lock();
  })();
})(typeof window === 'undefined' ? globalThis : window);
