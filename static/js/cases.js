/**
 * cases.js — Case management: list, detail timeline, notes, status
 */

async function loadCases() {
  const status   = document.getElementById('fStatus').value;
  const priority = document.getElementById('fPriority').value;
  const params   = new URLSearchParams();
  if (status)   params.set('status', status);
  if (priority) params.set('priority', priority);
  try {
    const res  = await fetch('/api/cases?' + params);
    const rows = await res.json();
    renderCaseList(rows);
  } catch(e) { showToast('Failed to load cases', 'error'); }
}

function renderCaseList(rows) {
  const tbody = document.getElementById('casesBody');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-row">No cases found</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(r => {
    const statusBadge = {
      'Open':'<span class="badge badge-review">Open</span>',
      'Under Investigation':'<span class="badge badge-review">Investigating</span>',
      'Escalated':'<span class="badge badge-block">Escalated</span>',
      'Resolved':'<span class="badge badge-approve">Resolved</span>',
    }[r.status] || r.status;
    const priColor = {Critical:'#DC2626',High:'#D97706',Medium:'#1E40AF',Low:'#059669'}[r.priority]||'#64748B';
    return `<tr>
      <td><code>${r.case_number}</code></td>
      <td><code>${r.transaction_id}</code></td>
      <td>${formatCurrency(r.amount||0)}</td>
      <td>${statusBadge}</td>
      <td><span style="color:${priColor};font-weight:600">${r.priority}</span></td>
      <td>${r.assigned_to||'<span class="muted">Unassigned</span>'}</td>
      <td>${new Date(r.opened_at).toLocaleDateString()}</td>
      <td><button class="btn btn-secondary btn-sm" onclick="openCaseDetail(${r.id})">Open</button></td>
    </tr>`;
  }).join('');
}

async function openCaseDetail(caseId) {
  try {
    const res  = await fetch(`/api/cases/${caseId}`);
    const data = await res.json();
    renderCaseModal(data);
    document.getElementById('caseModal').classList.remove('hidden');
  } catch(e) { showToast('Failed to load case', 'error'); }
}

function renderCaseModal(c) {
  document.getElementById('caseModalTitle').textContent = `${c.case_number} — ${c.status}`;
  const notes = c.notes || [];
  document.getElementById('caseModalBody').innerHTML = `
    <div class="form-grid-2" style="margin-bottom:1.5rem">
      <div><span class="muted">Transaction</span><br><code>${c.transaction_id}</code></div>
      <div><span class="muted">Amount</span><br>${formatCurrency(c.amount||0)}</div>
      <div><span class="muted">Decision</span><br>${decisionBadge(c.tx_decision||c.decision)}</div>
      <div><span class="muted">Risk Score</span><br>${Math.round((c.fraud_probability||0)*100)}/100</div>
      <div><span class="muted">Priority</span><br>${c.priority}</div>
      <div><span class="muted">Assigned To</span><br>
        <input type="text" id="assignTo" value="${c.assigned_to||''}" placeholder="Username"
          style="padding:4px 8px;border:1px solid #E2E8F0;border-radius:4px;font-size:.8rem"/>
      </div>
    </div>

    <div class="section-label">Timeline</div>
    <div class="timeline" style="margin-bottom:1.5rem">
      <div class="timeline-item">
        <span class="timeline-time">${new Date(c.opened_at).toLocaleTimeString()}</span>
        <span class="timeline-actor">System</span>
        <span class="timeline-note">Case opened automatically</span>
      </div>
      ${notes.map(n=>`<div class="timeline-item">
        <span class="timeline-time">${new Date(n.created_at).toLocaleTimeString()}</span>
        <span class="timeline-actor">${n.author}</span>
        <span class="timeline-note">${n.note}</span>
      </div>`).join('')}
    </div>

    <div class="form-group" style="margin-bottom:1rem">
      <label>Add Note</label>
      <textarea id="newNote" rows="2" placeholder="Add investigation note…"
        style="width:100%;padding:8px;border:1px solid #E2E8F0;border-radius:6px;font-family:inherit;box-sizing:border-box"></textarea>
      <button class="btn btn-secondary btn-sm" style="margin-top:6px" onclick="addNote(${c.id})">Add Note</button>
    </div>

    <div class="form-group" style="margin-bottom:1rem">
      <label>Update Status</label>
      <select id="newStatus" style="padding:8px;border:1px solid #E2E8F0;border-radius:6px;font-family:inherit">
        <option value="">— Select —</option>
        <option value="Open">Open</option>
        <option value="Under Investigation">Under Investigation</option>
        <option value="Escalated">Escalated</option>
        <option value="Resolved">Resolved</option>
      </select>
    </div>

    <div class="form-group" style="margin-bottom:1.5rem">
      <label>Resolution</label>
      <select id="newResolution" style="padding:8px;border:1px solid #E2E8F0;border-radius:6px;font-family:inherit">
        <option value="">— Select —</option>
        <option value="Confirmed Fraud">Confirmed Fraud</option>
        <option value="False Positive">False Positive</option>
        <option value="Inconclusive">Inconclusive</option>
      </select>
    </div>

    <div style="display:flex;gap:8px">
      <button class="btn btn-secondary" onclick="closeModal('caseModal')">Close</button>
      <button class="btn btn-primary" onclick="saveCase(${c.id})">Save Changes</button>
    </div>
  `;
}

async function addNote(caseId) {
  const note = document.getElementById('newNote').value.trim();
  if (!note) return;
  try {
    await fetch(`/api/cases/${caseId}`, {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({note}),
    });
    showToast('Note added', 'success');
    openCaseDetail(caseId);
  } catch(e) { showToast('Failed to add note', 'error'); }
}

async function saveCase(caseId) {
  const updates = {};
  const status     = document.getElementById('newStatus').value;
  const resolution = document.getElementById('newResolution').value;
  const assignTo   = document.getElementById('assignTo').value.trim();
  if (status)     updates.status     = status;
  if (resolution) updates.resolution = resolution;
  if (assignTo)   updates.assigned_to = assignTo;
  if (!Object.keys(updates).length) { showToast('No changes to save', 'warning'); return; }
  try {
    await fetch(`/api/cases/${caseId}`, {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(updates),
    });
    showToast('Case updated', 'success');
    closeModal('caseModal');
    loadCases();
  } catch(e) { showToast('Update failed', 'error'); }
}

function closeModal(id) { document.getElementById(id).classList.add('hidden'); }

loadCases();
