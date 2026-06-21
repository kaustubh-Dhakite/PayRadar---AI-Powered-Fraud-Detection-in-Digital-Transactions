/**
 * accounts.js — Account list, profile modal, freeze/unfreeze
 */

let freezeTargetAcct = null;

async function loadAccounts() {
  const status = document.getElementById('fStatus').value;
  const search = document.getElementById('fSearch').value.trim();
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  if (search) params.set('search', search);
  try {
    const res  = await fetch('/api/accounts?' + params);
    const rows = await res.json();
    renderAccountList(rows);
  } catch(e) { showToast('Failed to load accounts', 'error'); }
}

function renderAccountList(rows) {
  const tbody = document.getElementById('acctBody');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-row">No accounts found</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(r => {
    const statusBadge = {
      'Active':      '<span class="badge badge-active">Active</span>',
      'Frozen':      '<span class="badge badge-frozen">Frozen</span>',
      'Under Review':'<span class="badge badge-review-status">Under Review</span>',
    }[r.status] || r.status;
    const risk = Math.round((r.avg_risk_score||0)*100);
    const riskColor = risk>=70?'#DC2626':risk>=40?'#D97706':'#059669';
    return `<tr>
      <td><code>${r.account_id}</code></td>
      <td>${statusBadge}</td>
      <td>${(r.total_transactions||0).toLocaleString()}</td>
      <td style="color:${r.total_fraud_flags>0?'#DC2626':'inherit'}">${r.total_fraud_flags||0}</td>
      <td style="color:${riskColor};font-weight:600">${risk}%</td>
      <td>${r.first_seen?new Date(r.first_seen).toLocaleDateString():'—'}</td>
      <td>${r.last_seen?new Date(r.last_seen).toLocaleDateString():'—'}</td>
      <td style="display:flex;gap:4px">
        <button class="btn btn-secondary btn-sm" onclick="openProfile('${r.account_id}')">Profile</button>
        ${r.status!=='Frozen'
          ? `<button class="btn btn-danger btn-sm" onclick="openFreezeModal('${r.account_id}')">Freeze</button>`
          : (typeof USER_ROLE!=='undefined'&&USER_ROLE==='admin'
              ? `<button class="btn btn-secondary btn-sm" onclick="unfreeze('${r.account_id}')">Unfreeze</button>`
              : '<span class="muted" style="font-size:.75rem">Frozen</span>')}
      </td>
    </tr>`;
  }).join('');
}

async function openProfile(accountId) {
  try {
    const res  = await fetch(`/api/accounts/${encodeURIComponent(accountId)}`);
    const data = await res.json();
    renderProfileModal(data);
    document.getElementById('acctModal').classList.remove('hidden');
  } catch(e) { showToast('Failed to load account', 'error'); }
}

function renderProfileModal(a) {
  document.getElementById('acctModalTitle').textContent = `Account: ${a.account_id}`;
  const txns = a.recent_transactions || [];
  const risk = Math.round((a.avg_risk_score||0)*100);
  document.getElementById('acctModalBody').innerHTML = `
    <div class="form-grid-2" style="margin-bottom:1.5rem">
      <div><span class="muted">Status</span><br>
        ${{Active:'<span class="badge badge-active">Active</span>',
           Frozen:'<span class="badge badge-frozen">Frozen</span>',
           'Under Review':'<span class="badge badge-review-status">Under Review</span>'}[a.status]||a.status}
      </div>
      <div><span class="muted">Avg Risk Score</span><br>
        <strong style="color:${risk>=70?'#DC2626':risk>=40?'#D97706':'#059669'}">${risk}%</strong>
      </div>
      <div><span class="muted">Total Transactions</span><br>${(a.total_transactions||0).toLocaleString()}</div>
      <div><span class="muted">Fraud Flags</span><br>
        <strong style="color:${a.total_fraud_flags>0?'#DC2626':'inherit'}">${a.total_fraud_flags||0}</strong>
      </div>
      ${a.frozen_by?`<div><span class="muted">Frozen By</span><br>${a.frozen_by}</div>`:''}
      ${a.freeze_reason?`<div><span class="muted">Freeze Reason</span><br>${a.freeze_reason}</div>`:''}
    </div>

    <div class="section-label">Recent Transactions</div>
    <div class="table-wrap">
      <table class="data-table">
        <thead><tr><th>TXN ID</th><th>Type</th><th>Amount</th><th>Risk</th><th>Decision</th><th>Time</th></tr></thead>
        <tbody>
          ${txns.length ? txns.map(t=>`<tr class="${t.decision==='BLOCK'?'row-block':t.decision==='REVIEW'?'row-review':''}">
            <td><code>${t.transaction_id}</code></td>
            <td>${t.type}</td>
            <td>${formatCurrency(t.amount)}</td>
            <td>${Math.round((t.fraud_probability||0)*100)}%</td>
            <td>${decisionBadge(t.decision)}</td>
            <td>${new Date(t.timestamp).toLocaleDateString()}</td>
          </tr>`).join('') : '<tr><td colspan="6" class="empty-row">No transactions</td></tr>'}
        </tbody>
      </table>
    </div>
  `;
}

function openFreezeModal(accountId) {
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
    loadAccounts();
  } catch(e) { showToast('Freeze failed', 'error'); }
}

async function unfreeze(accountId) {
  if (!confirm(`Unfreeze account ${accountId}?`)) return;
  try {
    await fetch(`/api/accounts/${encodeURIComponent(accountId)}/unfreeze`, {method:'POST'});
    showToast(`Account ${accountId} unfrozen`, 'success');
    loadAccounts();
  } catch(e) { showToast('Unfreeze failed', 'error'); }
}

function closeModal(id) { document.getElementById(id).classList.add('hidden'); }

loadAccounts();
