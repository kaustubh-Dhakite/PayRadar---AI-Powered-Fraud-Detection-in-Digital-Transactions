/**
 * predict.js — Prediction form, result rendering, SHAP chart, override
 */

let currentTxnId = null;
let acctDebounce = null;

// ── Random transaction ────────────────────────────────────────────────────
async function loadRandom() {
  try {
    const res  = await fetch('/simulate', {method:'POST'});
    const data = await res.json();
    document.getElementById('fType').value    = data.type || 'TRANSFER';
    document.getElementById('fStep').value    = 1;
    document.getElementById('fAmount').value  = data.amount || '';
    document.getElementById('fOrigAcc').value = data.transaction_id ? `ACC-${Math.floor(Math.random()*9000+1000)}` : '';
    document.getElementById('fDestAcc').value = `ACC-${Math.floor(Math.random()*9000+1000)}`;
    document.getElementById('fOldOrg').value  = data.ml_probability ? '' : '';
    // Fill from simulate response fields if available
    showResult(data);
  } catch(e) { showToast('Could not generate transaction: '+e.message, 'error'); }
}

// ── Account status check (debounced) ─────────────────────────────────────
function onAcctInput(val) {
  clearTimeout(acctDebounce);
  if (!val || val.length < 3) {
    document.getElementById('acctBanner').className = 'acct-banner hidden';
    return;
  }
  acctDebounce = setTimeout(() => checkAccount(val), 500);
}

async function checkAccount(accountId) {
  try {
    const res  = await fetch(`/api/accounts/${encodeURIComponent(accountId)}`);
    const data = await res.json();
    const banner = document.getElementById('acctBanner');
    if (data.status === 'Frozen') {
      banner.className = 'acct-banner acct-frozen';
      banner.innerHTML = `🔴 <strong>ACCOUNT FROZEN</strong> — Frozen by ${data.frozen_by||'admin'}: "${data.freeze_reason||'No reason given'}"`;
    } else if (data.total_fraud_flags > 2) {
      banner.className = 'acct-banner acct-risk';
      banner.innerHTML = `🟡 <strong>HIGH RISK ACCOUNT</strong> — ${data.total_fraud_flags} fraud flags`;
    } else {
      banner.className = 'acct-banner acct-ok';
      banner.innerHTML = `🟢 <strong>ACCOUNT ACTIVE</strong> — ${data.total_transactions||0} transactions, no flags`;
    }
  } catch(e) {}
}

// ── Submit prediction ─────────────────────────────────────────────────────
async function submitPredict() {
  const btn = document.getElementById('btnCheck');
  btn.disabled = true; btn.textContent = '⏳ Analysing…';

  // Velocity check
  const origAcc = document.getElementById('fOrigAcc').value;
  if (origAcc) {
    try {
      const vRes  = await fetch(`/api/accounts/${encodeURIComponent(origAcc)}/velocity`);
      const vData = await vRes.json();
      const velBanner = document.getElementById('velBanner');
      if (vData.is_high_velocity) {
        velBanner.className = 'vel-banner vel-warn';
        velBanner.innerHTML = `⚡ <strong>Velocity Warning</strong> — ${vData.transactions_last_10min} transactions in last 10 minutes`;
      } else {
        velBanner.className = 'vel-banner hidden';
      }
    } catch(e) {}
  }

  const payload = {
    step:          parseInt(document.getElementById('fStep').value) || 1,
    type:          document.getElementById('fType').value,
    amount:        parseFloat(document.getElementById('fAmount').value) || 0,
    orig_account:  document.getElementById('fOrigAcc').value || 'ACC-UNKNOWN',
    dest_account:  document.getElementById('fDestAcc').value || 'ACC-UNKNOWN',
    oldbalanceOrg: parseFloat(document.getElementById('fOldOrg').value) || 0,
    newbalanceOrig:parseFloat(document.getElementById('fNewOrig')?.value) || 0,
    oldbalanceDest:parseFloat(document.getElementById('fOldDest').value) || 0,
    newbalanceDest:0,
  };

  try {
    const res = await fetch('/predict', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload),
    });
    if (!res.ok) { const e=await res.json(); throw new Error(e.detail||'API error'); }
    const data = await res.json();
    showResult(data);
  } catch(e) {
    showToast('Error: '+e.message, 'error');
  } finally {
    btn.disabled=false; btn.textContent='🔍 Check for Fraud';
  }
}

// ── Render result ─────────────────────────────────────────────────────────
function showResult(d) {
  const panel = document.getElementById('resultPanel');
  panel.style.display = 'block';
  panel.scrollIntoView({behavior:'smooth', block:'start'});
  currentTxnId = d.transaction_id;

  // Verdict banner
  const banner = document.getElementById('verdictBanner');
  const isCrit   = d.risk_level === 'Critical';
  const isFraud  = d.decision === 'BLOCK';
  const isReview = d.decision === 'REVIEW';
  banner.className = 'verdict-banner ' + (isCrit?'verdict-critical':isFraud?'verdict-fraud':isReview?'verdict-review':'verdict-safe');
  document.getElementById('verdictIcon').textContent  = isCrit?'🚨':isFraud?'🛑':isReview?'⚠️':'✅';
  document.getElementById('verdictTitle').textContent = d.prediction + (isCrit?' — CRITICAL':' — '+d.risk_level+' Risk');
  document.getElementById('verdictSub').textContent   = `Decision: ${d.decision}  ·  Final Score: ${d.probability}%  ·  TXN: ${d.transaction_id}`;

  // Score bars
  setBar('barFinal','valFinal', d.probability);
  setBar('barML',   'valML',    d.ml_probability);
  setBar('barRule', 'valRule',  d.rule_score);

  // Triggered rules
  const rulesSection = document.getElementById('rulesSection');
  const rulesTags    = document.getElementById('rulesTags');
  if (d.triggered_rules?.length) {
    rulesSection.style.display = 'block';
    rulesTags.innerHTML = d.triggered_rules.map(r=>`<span class="tag-rule">⚡ ${r}</span>`).join('');
  } else {
    rulesSection.style.display = 'none';
  }

  // Reasons
  document.getElementById('reasonsList').innerHTML = (d.reasons||[]).map(r=>`<li>${r}</li>`).join('');

  // SHAP chart
  const shap = d.shap_explanation || [];
  if (shap.length) {
    const colors = shap.map(s => s.value > 0 ? '#DC2626' : '#059669');
    Plotly.react('shapChart', [{
      x: shap.map(s=>s.value), y: shap.map(s=>s.feature),
      type:'bar', orientation:'h', marker:{color:colors},
      text: shap.map(s=>s.value.toFixed(4)), textposition:'outside',
      hovertemplate:'%{y}: %{x:.4f}<extra></extra>',
    }], {
      paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
      font:{color:'#374151',size:10}, margin:{t:5,b:30,l:140,r:60},
      xaxis:{title:'SHAP Value',gridcolor:'#F1F5F9',zeroline:true,zerolinecolor:'#CBD5E1'},
      yaxis:{autorange:'reversed'},
    }, {displayModeBar:false});
  }

  // TXN ID
  document.getElementById('txnIdVal').textContent = d.transaction_id;

  // Override button
  const overrideSection = document.getElementById('overrideSection');
  overrideSection.style.display = isFraud ? 'block' : 'none';

  // Alert banner
  const alertBanner = document.getElementById('alertBanner');
  if (isFraud) {
    alertBanner.textContent = `⚠️ Suspicious Transaction Detected — Risk Level: ${d.risk_level}`;
    alertBanner.classList.remove('hidden');
    setTimeout(() => alertBanner.classList.add('hidden'), 6000);
  } else {
    alertBanner.classList.add('hidden');
  }
}

function setBar(barId, valId, pct) {
  const bar = document.getElementById(barId);
  const val = document.getElementById(valId);
  if (!bar||!val) return;
  const color = pct>=70?'#DC2626':pct>=40?'#D97706':'#059669';
  bar.style.width = pct+'%';
  bar.style.background = color;
  val.style.color = color;
  val.textContent = pct+'%';
}

// ── Override ──────────────────────────────────────────────────────────────
async function submitOverride() {
  const reason = document.getElementById('overrideReason').value.trim();
  if (reason.length < 20) { showToast('Reason must be at least 20 characters', 'warning'); return; }
  if (!currentTxnId) return;
  try {
    const res = await fetch(`/api/transactions/${currentTxnId}/override`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({reason}),
    });
    if (!res.ok) throw new Error('Override failed');
    showToast('Decision overridden successfully', 'success');
    document.getElementById('overrideSection').style.display = 'none';
  } catch(e) { showToast('Override failed: '+e.message, 'error'); }
}
