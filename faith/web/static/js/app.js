async function refreshStatus() {
  const target = document.getElementById("status-output");
  if (!target) {
    return;
  }

  try {
    const response = await fetch("/api/status");
    const payload = await response.json();
    target.textContent = JSON.stringify(payload, null, 2);
  } catch (error) {
    target.textContent = `Failed to load status: ${error}`;
  }
}

refreshStatus();
setInterval(refreshStatus, 5000);
