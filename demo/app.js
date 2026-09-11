/* This desk renders reviewed demonstration data. It never executes patch source. */
(() => {
  'use strict';
  const data = window.REPAIR_BENCH_DEMO;
  let current = data.cases[0];
  const $ = id => document.getElementById(id);
  const text = (id, value) => { $(id).textContent = value; };
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  function activateTab(name, focus = false) {
    tabs.forEach(tab => {
      const active = tab.dataset.tab === name;
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      $('panel-' + tab.dataset.tab).hidden = !active;
      if (active && focus) tab.focus();
    });
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activateTab(tab.dataset.tab));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length - 1;
      if (next !== undefined) { event.preventDefault(); activateTab(tabs[next].dataset.tab, true); }
    });
  });
  function selectCase(id) {
    current = data.cases.find(item => item.id === id);
    if (!current) return;
    document.querySelectorAll('.case-button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.id === id)));
    text('case-category', current.category);
    text('case-title', current.title);
    text('case-summary', current.summary);
    text('failure-detail', current.baseline.detail);
    text('repair-explanation', current.explanation);
    text('source-code', current.source);
    text('patch-code', current.patch);
    text('impact', current.impact);
    const checks = $('checks'); checks.replaceChildren();
    current.checks.forEach(check => {
      const row = document.createElement('div'); row.className = 'check-row';
      const symbol = document.createElement('span'); symbol.className = 'check-symbol';
      const status = check.passed === true ? 'Passed' : check.passed === false ? 'Failed' : 'Not verified';
      symbol.textContent = check.passed === true ? '✓' : check.passed === false ? '×' : '—';
      symbol.setAttribute('aria-label', status);
      const body = document.createElement('div');
      const title = document.createElement('strong'); title.textContent = check.label;
      const detail = document.createElement('p'); detail.textContent = check.detail;
      body.append(title, detail); row.append(symbol, body); checks.append(row);
    });
    $('approve').checked = false; $('export').disabled = true;
    activateTab('change');
    text('announcement', current.title + ' selected. Review approval has been reset.');
  }
  data.cases.forEach((item, index) => {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'case-button'; button.dataset.id = item.id;
    const number = document.createElement('span'); number.className = 'case-number'; number.textContent = 'CASE 0' + (index + 1);
    const title = document.createElement('span'); title.className = 'case-name'; title.textContent = item.short;
    const detail = document.createElement('span'); detail.className = 'case-desc'; detail.textContent = item.teaser;
    button.append(number, title, detail); button.addEventListener('click', () => selectCase(item.id)); $('case-list').append(button);
  });
  $('approve').addEventListener('change', () => { $('export').disabled = !$('approve').checked; });
  $('export').addEventListener('click', () => {
    if (!$('approve').checked) return;
    const artifact = {
      artifact: 'repair-bench-reference-review', version: 1,
      exported_at: new Date().toISOString(), scope: data.scope,
      source_sha256: data.source_sha256,
      review: {acknowledged_in_this_browser: true, deployment_authorized: false, live_model_repair: false},
      case: current,
      limitations: ['Reviewed reference demonstration, not an autonomous repair.', 'Local fixture success does not prove production safety.', 'No patch was executed or deployed by this interface.']
    };
    const blob = new Blob([JSON.stringify(artifact, null, 2) + '\n'], {type:'application/json'});
    const url = URL.createObjectURL(blob); const link = document.createElement('a');
    link.href = url; link.download = 'repair-bench-' + current.id + '-review.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    text('announcement', 'Review package exported. No code was deployed.');
  });
  async function loadRuns() {
    const container = $('provider-runs'); container.replaceChildren();
    text('runs-status', 'Reading individual saved attempts…');
    try {
      const response = await fetch('runs.json', {cache:'no-store', credentials:'same-origin'});
      if (!response.ok) throw new Error('Run endpoint unavailable');
      const payload = await response.json();
      if (!Array.isArray(payload.runs)) throw new Error('Unsupported run format');
      text('runs-status', payload.runs.length ? 'Generated source is untrusted. This view cannot approve, execute or deploy it.' : 'No saved attempts. Nothing has been verified by this view.');
      payload.runs.forEach(run => {
        const card = document.createElement('article'); card.className = 'run-card';
        const header = document.createElement('div'); header.className = 'run-header';
        const identity = document.createElement('div');
        const title = document.createElement('h4'); title.textContent = typeof run.case === 'string' ? run.case : 'Unknown scenario';
        const id = document.createElement('p'); id.className = 'run-id'; id.textContent = typeof run.run_id === 'string' ? run.run_id : 'Unknown run ID';
        identity.append(title, id);
        const badge = document.createElement('span'); badge.className = 'run-badge';
        let label = 'Incomplete';
        if (run.status === 'decision_evaluated') label = 'Decision evaluated · no runtime claim';
        else if (run.status === 'uncertain') label = 'Uncertain outcome';
        else if (run.status === 'rejected' || run.status === 'proposal_rejected') label = 'Proposal rejected';
        else if (run.remote_completed === true && run.observations_match === true) label = 'Observations match · not attested';
        else if (run.remote_completed === true && run.observations_match === false) label = 'Observed checks differ';
        else if (run.model_completed === true && typeof run.source === 'string') label = 'Candidate awaiting verification';
        else if (run.model_completed === true) label = 'Response without valid patch';
        badge.textContent = label; header.append(identity, badge); card.append(header);
        const detail = document.createElement('p'); detail.className = 'run-detail';
        const status = typeof run.status === 'string' ? run.status : 'unknown';
        const finish = typeof run.finish_reason === 'string' ? run.finish_reason : 'unknown';
        detail.textContent = 'Recorded status: ' + status + ' · Finish reason: ' + finish + ' · Remote execution: ' + (run.remote_completed === true ? 'completed' : run.remote_completed === false ? 'not completed' : 'unknown');
        card.append(detail);
        if (typeof run.explanation === 'string' && run.explanation) {
          const explanation = document.createElement('p'); explanation.className = 'run-explanation'; explanation.textContent = run.explanation; card.append(explanation);
        }
        if (Array.isArray(run.review_findings) && run.review_findings.some(item => typeof item === 'string' && item)) {
          const review = document.createElement('section'); review.className = 'source-review';
          const heading = document.createElement('h5'); heading.textContent = 'Source review: changes required';
          const note = document.createElement('p'); note.textContent = 'Static source observations — not runtime-test results.';
          const findings = document.createElement('ul');
          run.review_findings.filter(item => typeof item === 'string' && item).forEach(finding => {
            const item = document.createElement('li'); item.textContent = finding; findings.append(item);
          });
          review.append(heading, note, findings); card.append(review);
        }
        if (typeof run.source === 'string' && run.source) {
          const disclosure = document.createElement('details');
          const summary = document.createElement('summary'); summary.textContent = 'Inspect generated source · untrusted';
          const pre = document.createElement('pre'); pre.tabIndex = 0;
          const code = document.createElement('code'); code.textContent = run.source;
          pre.append(code); disclosure.append(summary, pre); card.append(disclosure);
        } else {
          const missing = document.createElement('p'); missing.className = 'run-no-source'; missing.textContent = 'No structurally valid patch source is available for this attempt.'; card.append(missing);
        }
        container.append(card);
      });
    } catch (error) {
      text('runs-status', 'Individual saved attempts are unavailable. This does not establish failure or success.');
    }
  }
  $('load-evidence').addEventListener('click', async () => {
    const button = $('load-evidence'); button.disabled = true;
    text('provider-status', 'Reading saved receipts…');
    try {
      const response = await fetch('evidence.json', {cache:'no-store', credentials:'same-origin'});
      if (!response.ok) throw new Error('Evidence endpoint unavailable');
      const evidence = await response.json();
      if (evidence.schema_version !== 1 || !evidence.stages) throw new Error('Unsupported evidence format');
      const metrics = $('provider-metrics'); metrics.replaceChildren();
      const completed = evidence.stages.baseline.remote_completed + evidence.stages.correction.remote_completed;
      const values = [
        ['Completed model calls', evidence.completed_model_calls],
        ['Completed sandbox attempts', completed],
        ['Uncertain calls', evidence.uncertain_calls ?? null],
        ['Paired comparisons', evidence.paired_comparisons.length]
      ];
      values.forEach(([label, value]) => {
        const metric = document.createElement('div'); metric.className = 'provider-metric';
        const number = document.createElement('strong'); number.textContent = value === null ? 'Unknown' : String(value);
        const name = document.createElement('span'); name.textContent = label;
        metric.append(number, name); metrics.append(metric);
      });
      metrics.hidden = false;
      text('provider-status', completed === 0 ? 'No remotely tested repairs in these receipts. Model responses alone do not establish a working repair.' : 'Saved execution results loaded. Review their scope before drawing conclusions.');
      text('provider-limit', evidence.trust_limit || 'Saved receipts are not independent execution attestation.');
      $('provider-limit').hidden = false;
    } catch (error) {
      $('provider-metrics').hidden = true; $('provider-limit').hidden = true;
      text('provider-status', 'Saved evidence is unavailable in this view. Run the local application server to read receipts; missing evidence is not a failed test.');
    } finally { await loadRuns(); button.disabled = false; }
  });
  selectCase(current.id);
})();
