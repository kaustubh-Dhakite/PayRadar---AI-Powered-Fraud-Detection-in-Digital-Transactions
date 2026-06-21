/**
 * dashboard.js — Live dashboard: KPIs, Plotly charts, live stream
 */

const LAYOUT = {
  paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
  font:{color:'#374151',size:11}, margin:{t:10,b:40,l:40,r:10},
  showlegend:true,
};

let streamTimer   = null;
let streamRunning = false;
let lastBlockCount = 0;

// ── Fetch & render ────────────────────────────────────────────────────────
async function refresh() {
  try {
    const [sRes, tRes] = await Promise.all([
      fetch('/stats'), fetch('/transactions/recent?limit=20')
    ]);
    const stats = await sRes.json();
    const txns  = await tRes.json();
    updateKPIs(stats);
    renderDonut(stats);
    renderLine(stats);
    renderBar(stats);
    renderTable(txns.predictions || []);
    // Alert on new blocks
    if (lastBlockCount > 0 && stats.blocked > lastBlockCount) {
      showToast(`🚨 ${stats.blocked - lastBlockCount} new BLOCK transaction(s) detected!`, 'error');
    }
    lastBlockCount = stats.blocked || 0;
  } catch(e) { console.error('Refresh error:', e); }
}

function updateKPIs(s) {
  const total = s.total || 1;
  setText('kpiSafe',    (s.safe||0).toLocaleString());
  setText('kpiSafePct', `${((s.safe||0)/total*100).toFixed(1)}% of total`);
  setText('kpiReview',  (s.review||0).toLocaleString());
  setText('kpiBlocked', (s.blocked||0).toLocaleString());
  setText('kpiAvg',     `${((s.avg_risk||0)*100).toFixed(1)}%`);
}

function renderDonut(s) {
  Plotly.react('chartDonut', [{
    type:'pie', hole:0.55,
    values:[s.safe||0, s.review||0, s.blocked||0],
    labels:['Approve','Review','Block'],
    marker:{colors:['#059669','#D97706','#DC2626']},
    textinfo:'percent',
    hovertemplate:'%{label}: %{value}<extra></extra>',
  }], {...LAYOUT, legend:{orientation:'h',y:-0.1}}, {displayModeBar:false});
}

function renderLine(s) {
  const hourly = s.hourly || [];
  Plotly.react('chartLine', [
    {x:hourly.map(h=>h.hour), y:hourly.map(h=>h.count),
     type:'scatter', mode:'lines+markers', name:'Total',
     line:{color:'#1E40AF',width:2}, fill:'tozeroy',
     fillcolor:'rgba(30,64,175,.06)',
     hovertemplate:'%{x}: %{y} txns<extra></extra>'},
    {x:hourly.map(h=>h.hour), y:hourly.map(h=>h.fraud_count||0),
     type:'scatter', mode:'lines+markers', name:'Fraud',
     line:{color:'#DC2626',width:2},
     hovertemplate:'%{x}: %{y} fraud<extra></extra>'},
  ], {...LAYOUT, margin:{t:10,b:40,l:40,r:10},
      xaxis:{title:'Hour',gridcolor:'#F1F5F9'},
      yaxis:{title:'Count',gridcolor:'#F1F5F9'}},
  {displayModeBar:false});
}

function renderBar(s) {
  const recent = s.recent || [];
  const scores = recent.map(r => Math.round((r.fraud_probability||0)*100));
  const buckets = Array(10).fill(0);
  scores.forEach(s => { const b = Math.min(Math.floor(s/10), 9); buckets[b]++; });
  const labels = ['0-10','10-20','20-30','30-40','40-50','50-60','60-70','70-80','80-90','90-100'];
  const colors = buckets.map((_,i) => i>=7?'#DC2626':i>=4?'#D97706':'#059669');
  Plotly.react('chartBar', [{
    x:labels, y:buckets, type:'bar',
    marker:{color:colors},
    hovertemplate:'%{x}%: %{y} txns<extra></extra>',
  }], {...LAYOUT, margin:{t:10,b:50,l:40,r:10},
      xaxis:{title:'Risk Score',gridcolor:'#F1F5F9'},
      yaxis:{title:'Count',gridcolor:'#F1F5F9'}},
  {displayModeBar:false});
}

function renderTable(rows) {
  const tbody = document.getElementById('txnBody');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-row">No transactions yet. Start the live stream or submit a prediction.</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(r => {
    const cls = r.decision==='BLOCK'?'row-block':r.decision==='REVIEW'?'row-review':'';
    const ml  = Math.round((r.ml_probability||0)*100);
    const fin = Math.round((r.fraud_probability||0)*100);
    const ts  = new Date(r.timestamp).toLocaleTimeString();
    return `<tr class="${cls}">
      <td><code>${r.transaction_id||'—'}</code></td>
      <td>${r.type||'—'}</td>
      <td>${formatCurrency(r.amount||0)}</td>
      <td><code>${r.orig_account||'—'}</code></td>
      <td>${ml}%</td>
      <td>${fin}/100</td>
      <td>${decisionBadge(r.decision)}</td>
      <td>${ts}</td>
    </tr>`;
  }).join('');
}

// ── Live stream ───────────────────────────────────────────────────────────
function toggleStream() {
  const btn = document.getElementById('btnStream');
  if (streamRunning) {
    clearInterval(streamTimer);
    streamRunning = false;
    btn.textContent = '▶ Start Live Stream';
    btn.className = 'btn btn-primary';
    document.getElementById('liveBadge').style.display = 'none';
  } else {
    const speed = parseInt(document.getElementById('streamSpeed').value);
    streamTimer = setInterval(runSimulate, speed);
    streamRunning = true;
    btn.textContent = '⏹ Stop Live Stream';
    btn.className = 'btn btn-danger';
    document.getElementById('liveBadge').style.display = '';
    runSimulate();
  }
}

async function runSimulate() {
  try {
    await fetch('/simulate', {method:'POST'});
    refresh();
  } catch(e) { console.error('Simulate error:', e); }
}

document.getElementById('streamSpeed')?.addEventListener('change', () => {
  if (streamRunning) { toggleStream(); toggleStream(); }
});

// ── Admin model metrics ───────────────────────────────────────────────────
async function loadModelMetrics() {
  const el = document.getElementById('modelMetrics');
  if (!el) return;
  try {
    const res  = await fetch('/api/model-stats');
    const data = await res.json();
    el.innerHTML = [
      {label:'Total Predictions', value:(data.total_predictions||0).toLocaleString(), icon:'📊'},
      {label:'Total Blocked',     value:(data.total_blocked||0).toLocaleString(),     icon:'🚫'},
      {label:'Override Rate',     value:((data.override_rate||0)*100).toFixed(1)+'%', icon:'↩️'},
      {label:'Avg Risk Score',    value:((data.avg_fraud_prob||0)*100).toFixed(1)+'%',icon:'📈'},
    ].map(k=>`<div class="kpi-card kpi-info">
      <div class="kpi-icon">${k.icon}</div>
      <div class="kpi-value">${k.value}</div>
      <div class="kpi-label">${k.label}</div>
    </div>`).join('');
  } catch(e) {}
}

// ── Helpers ───────────────────────────────────────────────────────────────
function setText(id, val) { const el=document.getElementById(id); if(el) el.textContent=val; }

// ── Init ──────────────────────────────────────────────────────────────────
refresh();
setInterval(refresh, 5000);
loadModelMetrics();
