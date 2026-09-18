(function () {
  const card = document.getElementById('card');
  const token = card.dataset.token;
  const statusEl = document.getElementById('status');
  const approveBtn = document.getElementById('approve-btn');
  const rejectBtn = document.getElementById('reject-btn');

  async function act(action) {
    approveBtn.disabled = true;
    rejectBtn.disabled = true;
    statusEl.classList.remove('error');
    statusEl.textContent = 'Working...';
    try {
      const res = await fetch(`/api/${action}/${encodeURIComponent(token)}`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) {
        statusEl.classList.add('error');
        statusEl.textContent = data.status ? `Already ${data.status}.` : 'Something went wrong.';
        return;
      }
      statusEl.textContent = action === 'approve' ? 'Approved — posting now.' : 'Rejected — post skipped.';
    } catch (e) {
      statusEl.classList.add('error');
      statusEl.textContent = 'Network error. Try again.';
      approveBtn.disabled = false;
      rejectBtn.disabled = false;
    }
  }

  approveBtn.addEventListener('click', () => act('approve'));
  rejectBtn.addEventListener('click', () => act('reject'));
})();
