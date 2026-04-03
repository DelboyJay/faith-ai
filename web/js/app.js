/**
 * Description:
 *   Poll the Web UI status endpoint and render the payload into the status placeholder.
 *
 * Requirements:
 *   - Update the placeholder output when the panel exists.
 *   - Fail visibly in the browser without throwing uncaught errors.
 *
 * @returns {Promise<void>} Promise that resolves after one refresh attempt.
 */
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

/**
 * Description:
 *   Return whether the browser has both the FAITH layout API and GoldenLayout available.
 *
 * Requirements:
 *   - Treat the CDN UMD global as the primary GoldenLayout source.
 *
 * @returns {boolean} True when the layout runtime is ready.
 */
function isLayoutRuntimeReady() {
  return Boolean(window.faithLayout && (window.goldenLayout || window.GoldenLayout));
}

/**
 * Description:
 *   Initialise the FAITH panel workspace once the layout runtime is ready.
 *
 * Requirements:
 *   - Mount GoldenLayout into the dedicated layout container.
 *   - Start the status polling loop after the placeholder status panel exists.
 *   - Retry briefly when the vendored GoldenLayout fallback is still loading.
 *
 * @param {number} attempt: Current retry counter.
 */
function bootstrapFaithWorkspace(attempt = 0) {
  const container = document.getElementById("faith-layout");
  const toolbar = document.getElementById("faith-toolbar");
  if (!container || !toolbar) {
    return;
  }

  if (!isLayoutRuntimeReady()) {
    if (attempt >= 40) {
      container.textContent = "GoldenLayout failed to load.";
      return;
    }
    window.setTimeout(function retryLayoutBootstrap() {
      bootstrapFaithWorkspace(attempt + 1);
    }, 150);
    return;
  }

  if (!window.faithLayoutInstance) {
    window.faithLayoutInstance = window.faithLayout.initLayout(container, toolbar);
  }

  refreshStatus();
  window.setInterval(refreshStatus, 5000);
}

document.addEventListener("DOMContentLoaded", function onFaithDomReady() {
  bootstrapFaithWorkspace();
});

