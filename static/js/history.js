/**
 * history.js — Transaction history: filter, table, export, modals
 */

let allRows = [];
let freezeTargetAcct = null;

async function loadHistory() {
  const decision  = document.getElementById('fDecision').value;
  const source    = document.getElementById('fSource').value;
  const dateFrom  = document.getElementById('fDateFrom').value;
  const dateTo    = document.getElementById('fDateTo').value;
  const search    = document.getElementById('fSearch').value.trim();
  const limit     = document.getElementById('fLimit').value;

  const params = new URLSearchParams({limit});
  if (decision) params.set('decision', decision);
  if (source)   params.set('source', source);
  if (dateFrom) params.set('date_from', dateFrom);
  if (dateTo)   params.set('date_to', dateTo);
  if (search)   params.set('search', search);

  try {
    const res  = await fetch('/transactions/recent?' + params);
    const data = await res.json();
    allRows = data.predictions || [];
    renderTable(allRows);
  } catch(e) { showToast('Failed to load history', 'error'); }
}

function renderTable(rows) {
  const tbody = document.getElementById('histBody');
  document.getElementById('rowCount').textContent = rows.length + ' records';
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="12" class="empty-row">No records found</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(r => {
    const cls  = r.decision==='BLOCK'?'row-block':r.decision==='REVIEW'?'row-review':'';
    const ml   = Math.round((r.ml_probability||0)*100);
    const rule = Math.round((r.rule_score||0)*100);
    const fin  = Math.round((r.fraud_probability||0)*100);
    const ts   = new Date(r.timestamp).toLocaleString();
    const ovr  = r.is_overridden ? '<span class="badge badge-review">Overridden</span>' : '—';
    return `<tr class="${cls}">
      <td>${ts}</td>
      <td><code>${r.transaction_id||'—'}</code></td>
      <td>${r.type||'—'}</td>
      <td>${formatCurrency(r.amount||0)}</td>
      <td><code>${r.orig_account||'—'}</code></td>
      <td><code>${r.dest_account||'—'}</code></td>
      <td>${ml}%</td><td>${rule}%</td><td>${fin}/100</td>
      <td>${decisionBadge(r.decision)}</td>
      <td>${ovr}</td>
      <td>
        <button class="btn btn-secondary btn-sm" onclick="viewDetail('${r.transaction_id}')">View</button>
        ${r.decision==='REVIEW'?`<button class="btn btn-secondary btn-sm" onclick="openCase('${r.transaction_id}')">Case</button>`:''}
        <button class="btn btn-danger btn-sm" onclick="openFreezeModal('${r.orig_account||''}')">Freeze</button>
      </td>
    </tr>`;
  }).join('');
}

function viewDetail(txnId) {
  const row = allRows.find(r => r.transaction_id === txnId);
  if (!row) return;
  const rules = Array.isArray(row.triggered_rules) ? row.triggered_rules.join(', ') : (row.triggered_rules||'None');
  document.getElementById('detailContent').innerHTML = `
    <div class="form-grid-2" style="gap:.75rem">
      <div><span class="muted">Transaction ID</span><br><code>${row.transaction_id}</code></div>
      <div><span class="muted">Timestamp</span><br>${new Date(row.timestamp).toLocaleString()}</div>
      <div><span class="muted">Type</span><br>${row.type}</div>
      <div><span class="muted">Amount</span><br>${formatCurrency(row.amount)}</div>
      <div><span class="muted">Sender</span><br><code>${row.orig_account||'—'}</code></div>
      <div><span class="muted">Receiver</span><br><code>${row.dest_account||'—'}</code></div>
      <div><span class="muted">ML Probability</span><br>${Math.round((row.ml_probability||0)*100)}%</div>
      <div><span class="muted">Rule Score</span><br>${Math.round((row.rule_score||0)*100)}%</div>
      <div><span class="muted">Final Score</span><br>${Math.round((row.fraud_probability||0)*100)}/100</div>
      <div><span class="muted">Decision</span><br>${decisionBadge(row.decision)}</div>
      <div><span class="muted">Source</span><br>${row.source}</div>
      <div><span class="muted">Overridden</span><br>${row.is_overridden?'Yes by '+row.override_by:'No'}</div>
    </div>
    <div style="margin-top:1rem"><span class="muted">Triggered Rules</span><br>${rules||'None'}</div>
    ${row.override_reason?`<div style="margin-top:.5rem"><span class="muted">Override Reason</span><br>${row.override_reason}</div>`:''}
  `;
  document.getElementById('detailModal').classList.remove('hidden');
}

async function openCase(txnId) {
  try {
    const res = await fetch('/api/cases', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({transaction_id: txnId, priority:'Medium'}),
    });
    const data = await res.json();
    showToast(`Case ${data.case_number} created`, 'success');
  } catch(e) { showToast('Failed to create case', 'error'); }
}

function openFreezeModal(accountId) {
  if (!accountId || accountId === 'ACC-UNKNOWN') { showToast('No account ID available', 'warning'); return; }
  freezeTargetAcct = accountId;
  document.getElementById('freezeAcctId').textContent = accountId;
  document.getElementById('freezeReason').value = '';
  document.getElementById('freezeModal').classList.remove('hidden');
}

async function confirmFreeze() {
  const reason = document.getElementById('freezeReason').value.trim();
  if (!reason) { showToast('Reason is required', 'warning'); return; }
  try {
    await fetch(`/api/accounts/${encodeURIComponent(freezeTargetAcct)}/freeze`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({reason}),
    });
    showToast(`Account ${freezeTargetAcct} frozen`, 'success');
    closeModal('freezeModal');
  } catch(e) { showToast('Freeze failed', 'error'); }
}

function closeModal(id) { document.getElementById(id).classList.add('hidden'); }

function exportCSV() {
  if (!allRows.length) { showToast('No data to export', 'warning'); return; }
  const headers = ['timestamp','transaction_id','type','amount','orig_account','dest_account',
                   'ml_probability','rule_score','fraud_probability','decision','is_overridden'];
  const csv = [headers.join(','), ...allRows.map(r => headers.map(h => JSON.stringify(r[h]??'')).join(','))].join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv],{type:'text/csv'}));
  a.download = 'payradar_history.csv';
  a.click();
}

loadHistory();
