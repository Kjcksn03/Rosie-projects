// ── VIP Clinic Tracker – Frontend JS ──

// Hamburger menu
document.addEventListener('DOMContentLoaded', () => {
  const hamburger = document.querySelector('.hamburger');
  const navLinks = document.querySelector('.navbar-links');
  if (hamburger && navLinks) {
    hamburger.addEventListener('click', () => {
      navLinks.classList.toggle('open');
    });
  }

  // Auto-dismiss flash messages
  const flashes = document.querySelectorAll('.flash');
  flashes.forEach(f => {
    setTimeout(() => { f.style.opacity = '0'; f.style.transition = 'opacity .5s'; setTimeout(() => f.remove(), 500); }, 4000);
  });

  // Dept collapsible sections
  document.querySelectorAll('.dept-header').forEach(header => {
    header.addEventListener('click', () => {
      const body = header.nextElementSibling;
      if (body) {
        const isHidden = body.style.display === 'none';
        body.style.display = isHidden ? '' : 'none';
        const icon = header.querySelector('.collapse-icon');
        if (icon) icon.textContent = isHidden ? '▼' : '▶';
      }
    });
  });

  // @mention highlighting in notes
  document.querySelectorAll('.note-content').forEach(el => {
    el.innerHTML = el.innerHTML.replace(/@(\w+)/g, '<span class="mention">@$1</span>');
  });

  // Poll notification count every 60s
  updateNotifCount();
  setInterval(updateNotifCount, 60000);

  // Confirm delete
  document.querySelectorAll('.confirm-delete').forEach(form => {
    form.addEventListener('submit', e => {
      if (!confirm('Are you sure you want to delete this? This cannot be undone.')) {
        e.preventDefault();
      }
    });
  });
});

function updateNotifCount() {
  fetch('/api/notifications/count')
    .then(r => r.json())
    .then(data => {
      const badge = document.querySelector('.notif-badge');
      if (badge) {
        if (data.count > 0) {
          badge.textContent = data.count;
          badge.style.display = '';
        } else {
          badge.style.display = 'none';
        }
      }
    }).catch(() => {});
}

// Quick status update (inline on task table)
function quickStatusUpdate(taskId, selectEl) {
  const status = selectEl.value;
  fetch(`/api/task/${taskId}/status`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status})
  }).then(r => r.json()).then(data => {
    if (data.ok) {
      const row = selectEl.closest('tr');
      if (row) {
        row.classList.remove('task-overdue', 'task-blocked');
        if (status === 'Blocked') row.classList.add('task-blocked');
      }
      // Update badge next to status
      const badge = selectEl.closest('td')?.querySelector('.badge');
      if (badge) {
        badge.className = 'badge ' + statusBadgeClass(status);
        badge.textContent = status;
      }
    }
  }).catch(() => {});
}

function statusBadgeClass(status) {
  const map = {
    'Not Started': 'badge-not-started',
    'In Progress': 'badge-in-progress',
    'Complete': 'badge-complete',
    'Blocked': 'badge-blocked'
  };
  return map[status] || 'badge-not-started';
}
