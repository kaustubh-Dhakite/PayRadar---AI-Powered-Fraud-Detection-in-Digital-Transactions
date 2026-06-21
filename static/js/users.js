/**
 * users.js — User management: list, create, deactivate
 */

async function loadUsers() {
  try {
    const res  = await fetch('/api/users');
    const rows = await res.json();
    renderUsers(rows);
  } catch(e) { showToast('Failed to load users', 'error'); }
}

function renderUsers(rows) {
  const tbody = document.getElementById('usersBody');
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty-row">No users found</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td><code>${r.username}</code></td>
      <td>${r.full_name||'—'}</td>
      <td><span class="badge ${r.role==='admin'?'badge-block':'badge-approve'}">${r.role}</span></td>
      <td>${r.email||'—'}</td>
      <td>${r.is_active
        ? '<span class="badge badge-active">Active</span>'
        : '<span class="badge badge-frozen">Inactive</span>'}</td>
      <td>${r.last_login?new Date(r.last_login).toLocaleString():'Never'}</td>
      <td>${r.created_by||'—'}</td>
      <td>
        ${r.is_active && r.role!=='admin'
          ? `<button class="btn btn-danger btn-sm" onclick="deactivateUser(${r.id},'${r.username}')">Deactivate</button>`
          : '<span class="muted">—</span>'}
      </td>
    </tr>
  `).join('');
}

function openCreateModal() {
  document.getElementById('newUsername').value  = '';
  document.getElementById('newFullName').value  = '';
  document.getElementById('newEmail').value     = '';
  document.getElementById('newPwBox').classList.add('hidden');
  document.getElementById('createModal').classList.remove('hidden');
}

async function createUser() {
  const username  = document.getElementById('newUsername').value.trim();
  const full_name = document.getElementById('newFullName').value.trim();
  const email     = document.getElementById('newEmail').value.trim();
  if (!username || !full_name || !email) { showToast('All fields are required', 'warning'); return; }
  try {
    const res  = await fetch('/api/users', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({username, full_name, email, role:'analyst'}),
    });
    const data = await res.json();
    document.getElementById('newPwVal').textContent = data.temp_password;
    document.getElementById('newPwBox').classList.remove('hidden');
    showToast(`User ${username} created`, 'success');
    loadUsers();
  } catch(e) { showToast('Failed to create user: '+e.message, 'error'); }
}

async function deactivateUser(userId, username) {
  if (!confirm(`Deactivate user "${username}"? They will not be able to login.`)) return;
  try {
    await fetch(`/api/users/${userId}`, {method:'PUT'});
    showToast(`User ${username} deactivated`, 'success');
    loadUsers();
  } catch(e) { showToast('Failed to deactivate user', 'error'); }
}

function closeModal(id) { document.getElementById(id).classList.add('hidden'); }

loadUsers();
