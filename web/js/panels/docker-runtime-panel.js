/**
 * Description:
 *   Render the FAITH Docker runtime panel inside the browser workspace.
 *
 * Requirements:
 *   - Consume the dedicated Docker runtime HTTP and WebSocket feeds.
 *   - Render compact container cards grouped by runtime category.
 *   - Fail visibly without falling back to raw JSON dumps.
 */

(function initialiseFaithDockerRuntimePanel(globalScope) {
  const DOCKER_RUNTIME_HTTP_PATH = "/api/docker-runtime";
  const DOCKER_RUNTIME_WS_PATH = "/ws/docker";
  const CATEGORY_ORDER = ["bootstrap", "agent", "tool", "runtime", "sandbox"];
  const CATEGORY_LABELS = Object.freeze({
    bootstrap: "Bootstrap Services",
    agent: "Agent Containers",
    tool: "Tool Containers",
    runtime: "Runtime Containers",
    sandbox: "Sandbox Containers",
  });

  /**
   * Description:
   *   Resolve the correct WebSocket URL for the Docker runtime feed.
   *
   * Requirements:
   *   - Preserve the current browser host and protocol.
   *   - Upgrade `http` to `ws` and `https` to `wss`.
   *
   * @returns {string} Absolute Docker runtime WebSocket URL.
   */
  function buildDockerRuntimeWebSocketUrl() {
    const base = new URL(globalScope.location.href);
    base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
    base.pathname = DOCKER_RUNTIME_WS_PATH;
    base.search = "";
    base.hash = "";
    return base.toString();
  }

  /**
   * Description:
   *   Return whether one container state should be treated as healthy.
   *
   * Requirements:
   *   - Treat running and healthy containers as positive.
   *   - Treat stopped, exited, and degraded containers as negative.
   *
   * @param {object} container Container summary payload.
   * @returns {boolean} True when the container should render as healthy.
   */
  function isHealthyContainer(container) {
    const state = String(container && container.state ? container.state : "").toLowerCase();
    const health = String(container && container.health ? container.health : "").toLowerCase();
    if (health) {
      return health === "healthy";
    }
    return state === "running";
  }

  /**
   * Description:
   *   Build the concise state label for one runtime container card.
   *
   * Requirements:
   *   - Include both state and health when health is available.
   *
   * @param {object} container Container summary payload.
   * @returns {string} Human-readable state label.
   */
  function buildStateLabel(container) {
    const state = container && container.state ? String(container.state) : "unknown";
    const health = container && container.health ? String(container.health) : "";
    return health ? `${state} / ${health}` : state;
  }

  /**
   * Description:
   *   Group containers by their runtime category in a deterministic order.
   *
   * Requirements:
   *   - Preserve the configured category ordering for stable UI rendering.
   *   - Group unknown categories after the known ones.
   *
   * @param {Array<object>} containers Runtime container list.
   * @returns {Array<object>} Ordered groups ready for rendering.
   */
  function groupContainers(containers) {
    const grouped = new Map();
    (containers || []).forEach(function addContainer(container) {
      const category = container && container.category ? container.category : "runtime";
      if (!grouped.has(category)) {
        grouped.set(category, []);
      }
      grouped.get(category).push(container);
    });

    const orderedKeys = Array.from(grouped.keys()).sort(function compareCategories(left, right) {
      const leftIndex = CATEGORY_ORDER.indexOf(left);
      const rightIndex = CATEGORY_ORDER.indexOf(right);
      const safeLeft = leftIndex === -1 ? CATEGORY_ORDER.length : leftIndex;
      const safeRight = rightIndex === -1 ? CATEGORY_ORDER.length : rightIndex;
      if (safeLeft !== safeRight) {
        return safeLeft - safeRight;
      }
      return left.localeCompare(right);
    });

    return orderedKeys.map(function buildGroup(category) {
      return {
        category: category,
        label: CATEGORY_LABELS[category] || category,
        containers: grouped.get(category) || [],
      };
    });
  }

  /**
   * Description:
   *   Create one runtime status card element.
   *
   * Requirements:
   *   - Show name, role, state, image, restart count, and optional URL/ownership fields.
   *   - Use a clear tick-or-cross style state badge.
   *
   * @param {object} container Container summary payload.
   * @returns {HTMLElement} Rendered status card element.
   */
  function buildRuntimeCard(container) {
    const card = document.createElement("article");
    card.className = "faith-runtime-card";
    card.classList.add(isHealthyContainer(container) ? "faith-runtime-card--healthy" : "faith-runtime-card--degraded");

    const heading = document.createElement("div");
    heading.className = "faith-runtime-card__heading";

    const title = document.createElement("h3");
    title.className = "faith-runtime-card__title";
    title.textContent = container.role || container.name || "Container";

    const badge = document.createElement("span");
    badge.className = "faith-runtime-card__badge";
    badge.textContent = isHealthyContainer(container) ? "✓" : "✕";

    heading.appendChild(title);
    heading.appendChild(badge);
    card.appendChild(heading);

    const meta = document.createElement("dl");
    meta.className = "faith-runtime-card__meta";

    [
      ["Name", container.name],
      ["State", buildStateLabel(container)],
      ["Image", container.image],
      ["Restarts", String(container.restart_count || 0)],
    ].forEach(function appendMetaRow(entry) {
      const term = document.createElement("dt");
      term.textContent = entry[0];
      const description = document.createElement("dd");
      description.textContent = entry[1] || "—";
      meta.appendChild(term);
      meta.appendChild(description);
    });

    if (container.url) {
      const term = document.createElement("dt");
      term.textContent = "URL";
      const description = document.createElement("dd");
      const link = document.createElement("a");
      link.className = "faith-runtime-card__link";
      link.href = container.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = container.url;
      description.appendChild(link);
      meta.appendChild(term);
      meta.appendChild(description);
    }

    Object.entries(container.ownership || {}).forEach(function appendOwnership(entry) {
      const term = document.createElement("dt");
      term.textContent = entry[0].replaceAll("_", " ");
      const description = document.createElement("dd");
      description.textContent = entry[1];
      meta.appendChild(term);
      meta.appendChild(description);
    });

    card.appendChild(meta);
    return card;
  }

  /**
   * Description:
   *   Render one runtime snapshot into the supplied mount element.
   *
   * Requirements:
   *   - Render grouped cards rather than raw JSON.
   *   - Show an explicit empty state when no containers are present.
   *
   * @param {HTMLElement} target Mount element for the runtime panel.
   * @param {object} payload Docker runtime snapshot payload.
   */
  function renderRuntimeSnapshot(target, payload) {
    target.replaceChildren();

    const wrapper = document.createElement("section");
    wrapper.className = "faith-panel faith-panel--docker-runtime";

    const summary = document.createElement("p");
    summary.className = "faith-runtime-summary";
    summary.textContent = payload && payload.docker_available
      ? "FAITH Docker runtime visibility"
      : "Docker runtime unavailable";
    wrapper.appendChild(summary);

    if (payload && Array.isArray(payload.images) && payload.images.length > 0) {
      const imageSection = document.createElement("section");
      imageSection.className = "faith-runtime-group";

      const imageHeading = document.createElement("h3");
      imageHeading.className = "faith-runtime-group__title";
      imageHeading.textContent = "Image Inventory";
      imageSection.appendChild(imageHeading);

      const imageList = document.createElement("ul");
      imageList.className = "faith-runtime-images";
      payload.images.forEach(function appendImage(imageName) {
        const item = document.createElement("li");
        item.className = "faith-runtime-images__item";
        item.textContent = imageName;
        imageList.appendChild(item);
      });
      imageSection.appendChild(imageList);
      wrapper.appendChild(imageSection);
    }

    const groups = groupContainers(payload && payload.containers ? payload.containers : []);
    if (groups.length === 0) {
      const empty = document.createElement("p");
      empty.className = "faith-runtime-empty";
      empty.textContent = "No FAITH containers are currently visible.";
      wrapper.appendChild(empty);
      target.appendChild(wrapper);
      return;
    }

    groups.forEach(function appendGroup(group) {
      const section = document.createElement("section");
      section.className = "faith-runtime-group";

      const heading = document.createElement("h3");
      heading.className = "faith-runtime-group__title";
      heading.textContent = group.label;
      section.appendChild(heading);

      const grid = document.createElement("div");
      grid.className = "faith-runtime-grid";
      group.containers.forEach(function appendCard(container) {
        grid.appendChild(buildRuntimeCard(container));
      });
      section.appendChild(grid);
      wrapper.appendChild(section);
    });

    target.appendChild(wrapper);
  }

  /**
   * Description:
   *   Fetch the Docker runtime snapshot over HTTP once.
   *
   * Requirements:
   *   - Raise when the runtime endpoint responds with an error.
   *
   * @returns {Promise<object>} Parsed Docker runtime snapshot.
   */
  async function fetchDockerRuntimeSnapshot() {
    const response = await fetch(DOCKER_RUNTIME_HTTP_PATH);
    if (!response.ok) {
      throw new Error(`Runtime request failed with status ${response.status}`);
    }
    return await response.json();
  }

  /**
   * Description:
   *   Mount the Docker runtime panel into one GoldenLayout container.
   *
   * Requirements:
   *   - Render one initial HTTP snapshot.
   *   - Subscribe to the dedicated WebSocket feed for live updates.
   *   - Fail visibly without dumping raw JSON.
   *
   * @param {HTMLElement} target Mount element for the panel.
   * @returns {Promise<void>} Promise that resolves after initial setup.
   */
  async function mountPanel(target) {
    try {
      renderRuntimeSnapshot(target, await fetchDockerRuntimeSnapshot());
    } catch (error) {
      renderRuntimeSnapshot(target, {
        docker_available: false,
        images: [],
        containers: [],
      });
      const note = document.createElement("p");
      note.className = "faith-runtime-empty";
      note.textContent = `Failed to load Docker runtime: ${error}`;
      target.querySelector(".faith-panel--docker-runtime").appendChild(note);
    }

    try {
      const socket = new WebSocket(buildDockerRuntimeWebSocketUrl());
      socket.addEventListener("message", function onDockerRuntimeMessage(event) {
        try {
          renderRuntimeSnapshot(target, JSON.parse(event.data));
        } catch (error) {
          console.warn("[FAITH] Failed to parse Docker runtime payload.", error);
        }
      });
    } catch (error) {
      console.warn("[FAITH] Failed to open Docker runtime WebSocket.", error);
    }
  }

  globalScope.faithDockerRuntimePanel = {
    DOCKER_RUNTIME_HTTP_PATH: DOCKER_RUNTIME_HTTP_PATH,
    DOCKER_RUNTIME_WS_PATH: DOCKER_RUNTIME_WS_PATH,
    mountPanel: mountPanel,
    renderRuntimeSnapshot: renderRuntimeSnapshot,
  };
})(window);
