/**
 * settings.js — Rule editor, threshold config, weight validation
 */

async function loadRules() {
  try {
    const res  = await fetch('/api/rules');
    const rows = await res.json();
    renderRulesTable(rows);
  } catch(e) { showToast('Failed to load rules', 'error'); }
}

function renderRulesTable(rules) {
  const tbody = document.getElementById('rulesBody');
  tbody.innerHTML = rules.map(r => `
    <tr data-rule-id="${r.rule_id}">
      <td><code>${r.rule_id}</code></td>
      <td><strong>${r.rule_name}</strong></td>
      <td class="muted">${r.description||'—'}</td>
      <td>
        <input type="number" class="rule-weight" value="${r.weight}" step="0.05" min="0" max="1"
          style="width:70px;padding:4px 6px;border:1px solid #E2E8F0;border-radius:4px;font-size:.85rem"
          ${r.rule_id==='R7'?'readonly':''}/>
      </td>
      <td>
        ${r.threshold_value!=null
          ? `<input type="number" class="rule-threshold" value="${r.threshold_value}" step="1" min="0"
               style="width:90px;padding:4px 6px;border:1px solid #E2E8F0;border-radius:4px;font-size:.85rem"/>`
          : '<span class="muted">—</span>'}
      </td>
      <td>
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="checkbox" class="rule-active" ${r.is_active?'checked':''} ${r.rule_id==='R7'?'disabled':''}/>
          <span style="font-size:.8rem">${r.is_active?'Active':'Inactive'}</span>
        </label>
      </td>
    </tr>
  `).join('');
}

async function saveRules() {
  const rows = document.querySelectorAll('#rulesBody tr[data-rule-id]');
  const updates = Array.from(rows).map(row => {
    const ruleId    = row.dataset.ruleId;
    const weight    = parseFloat(row.querySelector('.rule-weight')?.value || 0);
    const thEl      = row.querySelector('.rule-threshold');
    const threshold = thEl ? parseFloat(thEl.value) : null;
    const isActive  = row.querySelector('.rule-active')?.checked ?? true;
    return {rule_id: ruleId, weight, threshold_value: threshold, is_active: isActive};
  });
  try {
    const res = await fetch('/api/rules', {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(updates),
    });
    if (!res.ok) throw new Error('Save failed');
    showToast('Rules saved successfully', 'success');
  } catch(e) { showToast('Failed to save rules: '+e.message, 'error'); }
}

async function loadThresholds() {
  try {
    const res  = await fetch('/api/settings/thresholds');
    const data = await res.json();
    document.getElementById('tApprove').value  = data.approve_threshold  || 0.40;
    document.getElementById('tBlock').value    = data.block_threshold    || 0.70;
    document.getElementById('tCritical').value = data.critical_threshold || 0.85;
    document.getElementById('wML').value       = data.ml_weight          || 0.60;
    document.getElementById('wRules').value    = data.rules_weight       || 0.40;
  } catch(e) {}
}

async function saveThresholds() {
  const ml    = parseFloat(document.getElementById('wML').value);
  const rules = parseFloat(document.getElementById('wRules').value);
  if (Math.abs(ml + rules - 1.0) > 0.001) {
    showToast('ML Weight + Rules Weight must equal 1.0', 'warning');
    return;
  }
  const payload = {
    approve_threshold:  parseFloat(document.getElementById('tApprove').value),
    block_threshold:    parseFloat(document.getElementById('tBlock').value),
    critical_threshold: parseFloat(document.getElementById('tCritical').value),
    ml_weight:          ml,
    rules_weight:       rules,
  };
  try {
    const res = await fetch('/api/settings/thresholds', {
      method:'PUT', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error('Save failed');
    showToast('Thresholds saved', 'success');
  } catch(e) { showToast('Failed to save thresholds: '+e.message, 'error'); }
}

function syncWeights(changed) {
  const ml    = parseFloat(document.getElementById('wML').value) || 0;
  const rules = parseFloat(document.getElementById('wRules').value) || 0;
  const err   = document.getElementById('weightError');
  if (Math.abs(ml + rules - 1.0) > 0.001) {
    err.classList.remove('hidden');
  } else {
    err.classList.add('hidden');
  }
}

loadRules();
loadThresholds();
