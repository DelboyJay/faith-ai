/**
 * Description:
 *   Bootstrap the bundled React + Dockview FAITH browser shell.
 *
 * Requirements:
 *   - Reuse the existing browser panel runtimes while replacing the workspace engine.
 *   - Persist and restore Dockview layouts with a versioned localStorage key.
 *   - Preserve the current default Project Agent, System Status, Input, User Settings, and Approvals view.
 */

import React from "react";
import { createPortal } from "react-dom";
import { createRoot } from "react-dom/client";
import { DockviewReact } from "dockview";

import "./faith-ui.css";

const LAYOUT_STORAGE_KEY = "faith_dockview_layout_v1";
const LEGACY_LAYOUT_STORAGE_KEYS = Object.freeze([
  "faith_layout_v1",
  "faith_layout_v2",
  "faith_layout_v3",
  "faith_layout_v4",
]);

const COMPONENT_TYPES = Object.freeze({
  AGENT: "agent-panel",
  TOOL: "tool-panel",
  INPUT: "input-panel",
  APPROVAL: "approval-panel",
  STATUS: "status-panel",
  DOCKER_RUNTIME: "docker-runtime-panel",
  PA_SYSTEM_PROMPT: "pa-system-prompt-panel",
  USER_SETTINGS: "user-settings-panel",
});

const mountedPanelHandles = new Map();

/**
 * Description:
 *   Register one mounted panel runtime handle under its stable panel ID.
 *
 * Requirements:
 *   - Let workspace-level actions inspect panel dirty state when needed.
 *
 * @param {string} panelId Stable panel ID.
 * @param {object} handle Runtime handle returned by the mounted panel.
 * @returns {void}
 */
function registerMountedPanelHandle(panelId, handle) {
  if (!panelId || !handle) {
    return;
  }
  mountedPanelHandles.set(panelId, handle);
}

/**
 * Description:
 *   Remove one mounted panel runtime handle from the registry.
 *
 * Requirements:
 *   - Avoid removing a newer handle when an older effect instance cleans up late.
 *
 * @param {string} panelId Stable panel ID.
 * @param {object} handle Runtime handle being cleaned up.
 * @returns {void}
 */
function unregisterMountedPanelHandle(panelId, handle) {
  if (!panelId) {
    return;
  }
  if (mountedPanelHandles.get(panelId) === handle) {
    mountedPanelHandles.delete(panelId);
  }
}

/**
 * Description:
 *   Confirm whether one panel may discard its local state for an action.
 *
 * Requirements:
 *   - Reuse panel-provided confirmation text when available.
 *   - Fall back to a generic confirmation when a panel exposes only dirty-state information.
 *
 * @param {string} panelId Stable panel ID.
 * @param {string} actionLabel User-visible action name.
 * @returns {boolean} True when the action may proceed.
 */
function confirmPanelAction(panelId, actionLabel) {
  const handle = mountedPanelHandles.get(panelId);
  if (!handle) {
    return true;
  }
  if (typeof handle.confirmDiscardChanges === "function") {
    return handle.confirmDiscardChanges(actionLabel);
  }
  if (typeof handle.hasUnsavedChanges === "function" && handle.hasUnsavedChanges()) {
    return window.confirm(`Discard unsaved panel changes and ${actionLabel}?`);
  }
  return true;
}

/**
 * Description:
 *   Confirm whether the workspace layout may be reset.
 *
 * Requirements:
 *   - Stop at the first panel that rejects discarding local changes.
 *
 * @returns {boolean} True when the reset may proceed.
 */
function confirmWorkspaceReset() {
  for (const [panelId] of mountedPanelHandles.entries()) {
    if (!confirmPanelAction(panelId, "reset the layout")) {
      return false;
    }
  }
  return true;
}

/**
 * Description:
 *   Return the shared workspace descriptor from the browser global when present.
 *
 * Requirements:
 *   - Prefer the existing shared workspace asset so the default layout stays canonical.
 *   - Fall back to a local descriptor if that asset is unavailable unexpectedly.
 *
 * @returns {object} Default workspace descriptor.
 */
function getDefaultWorkspaceDescriptor() {
  if (
    window.faithWorkspaceConfig &&
    typeof window.faithWorkspaceConfig.buildDefaultWorkspaceDescriptor === "function"
  ) {
    return window.faithWorkspaceConfig.buildDefaultWorkspaceDescriptor();
  }
  return {
    version: "v1",
    upperGroup: {
      panels: [
        {
          id: "project-agent",
          componentType: COMPONENT_TYPES.AGENT,
          title: "Project Agent",
          componentState: {
            agentId: "project-agent",
            displayName: "Project Agent",
            model: "ollama/llama3:8b",
          },
        },
        {
          id: "system-status",
          componentType: COMPONENT_TYPES.STATUS,
          title: "System Status",
          componentState: {},
        },
      ],
    },
    lowerGroup: {
      panels: [
          {
            id: "input",
            componentType: COMPONENT_TYPES.INPUT,
            title: "Input",
            stackedPanels: [
              {
                id: "user-settings",
                componentType: COMPONENT_TYPES.USER_SETTINGS,
                title: "User Settings",
                componentState: {},
              },
            ],
            componentState: {},
          },
        {
          id: "approvals",
          componentType: COMPONENT_TYPES.APPROVAL,
          title: "Approvals",
          componentState: {},
        },
      ],
    },
  };
}

/**
 * Description:
 *   Build the stable panel identifier used by Dockview.
 *
 * Requirements:
 *   - Keep singleton panel identifiers constant across reloads.
 *   - Use explicit prefixes for dynamic agent and tool panels.
 *
 * @param {string} componentType Panel component type.
 * @param {object} componentState Panel parameter object.
 * @param {string} fallbackId Descriptor fallback identifier.
 * @returns {string} Stable panel identifier.
 */
function buildPanelId(componentType, componentState, fallbackId) {
  if (componentType === COMPONENT_TYPES.AGENT) {
    return componentState && componentState.agentId
      ? `agent:${componentState.agentId}`
      : `agent:${fallbackId}`;
  }
  if (componentType === COMPONENT_TYPES.TOOL) {
    return componentState && componentState.toolId
      ? `tool:${componentState.toolId}`
      : `tool:${fallbackId}`;
  }
  return fallbackId;
}

/**
 * Description:
 *   Add one panel to Dockview unless it already exists.
 *
 * Requirements:
 *   - Focus the existing panel instead of duplicating singleton or identity-based panels.
 *   - Preserve the component parameters so the legacy panel runtimes receive the same state.
 *
 * @param {object} api Dockview API instance.
 * @param {object} panelDescriptor Panel descriptor from the workspace config.
 * @param {object | undefined} position Optional Dockview positioning directive.
 * @param {boolean} inactive Whether the new panel should stay inactive after insertion.
 * @returns {object} Existing or newly created Dockview panel object.
 */
function ensurePanel(api, panelDescriptor, position, inactive = false) {
  const panelId = buildPanelId(
    panelDescriptor.componentType,
    panelDescriptor.componentState || {},
    panelDescriptor.id,
  );
  const existingPanel = api.getPanel(panelId);
  if (existingPanel) {
    existingPanel.api.setActive();
    return existingPanel;
  }
  return api.addPanel({
    id: panelId,
    component: panelDescriptor.componentType,
    title: panelDescriptor.title,
    params: panelDescriptor.componentState || {},
    position: position,
    inactive: inactive,
  });
}

/**
 * Description:
 *   Apply the canonical first-load workspace arrangement to Dockview.
 *
 * Requirements:
 *   - Keep Project Agent and System Status in one tab group.
 *   - Keep Input and User Settings in one lower-left tab group.
 *   - Keep Approvals in the lower-right split beneath the upper group.
 *
 * @param {object} api Dockview API instance.
 */
function applyDefaultWorkspace(api) {
  const workspaceDescriptor = getDefaultWorkspaceDescriptor();
  const upperPanels = workspaceDescriptor.upperGroup && Array.isArray(workspaceDescriptor.upperGroup.panels)
    ? workspaceDescriptor.upperGroup.panels
    : [];
  const lowerPanels = workspaceDescriptor.lowerGroup && Array.isArray(workspaceDescriptor.lowerGroup.panels)
    ? workspaceDescriptor.lowerGroup.panels
    : [];
  if (upperPanels.length === 0) {
    return;
  }

  const primaryUpperPanel = ensurePanel(api, upperPanels[0]);
  upperPanels.slice(1).forEach(function addUpperTab(panelDescriptor) {
    ensurePanel(
      api,
      panelDescriptor,
      {
        referencePanel: primaryUpperPanel.id,
        direction: "within",
      },
      true,
    );
  });

  if (lowerPanels.length > 0) {
    const lowerPrimaryPanel = ensurePanel(api, lowerPanels[0], {
      referencePanel: primaryUpperPanel.id,
      direction: "below",
    });
    const stackedLowerPanels = Array.isArray(lowerPanels[0].stackedPanels)
      ? lowerPanels[0].stackedPanels
      : [];
    stackedLowerPanels.forEach(function addLowerTab(panelDescriptor) {
      ensurePanel(
        api,
        panelDescriptor,
        {
          referencePanel: lowerPrimaryPanel.id,
          direction: "within",
        },
        true,
      );
    });
    lowerPanels.slice(1).forEach(function addLowerSplit(panelDescriptor, index) {
      ensurePanel(api, panelDescriptor, {
        referencePanel: index === 0 ? lowerPrimaryPanel.id : buildPanelId(
          lowerPanels[index].componentType,
          lowerPanels[index].componentState || {},
          lowerPanels[index].id,
        ),
        direction: "right",
      });
    });
  }
}

/**
 * Description:
 *   Remove stale layout keys from previous workspace runtimes.
 *
 * Requirements:
 *   - Keep the browser from restoring GoldenLayout-era geometry into Dockview.
 *
 * @returns {void}
 */
function clearLegacyLayouts() {
  LEGACY_LAYOUT_STORAGE_KEYS.forEach(function removeLegacyLayout(key) {
    window.localStorage.removeItem(key);
  });
}

/**
 * Description:
 *   Persist the current Dockview layout in localStorage.
 *
 * Requirements:
 *   - Fail quietly when browser storage is unavailable so the workspace still runs.
 *
 * @param {object} api Dockview API instance.
 * @returns {void}
 */
function saveLayout(api) {
  try {
    window.localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(api.toJSON()));
  } catch (error) {
    console.warn("[FAITH] Failed to save Dockview layout state.", error);
  }
}

/**
 * Description:
 *   Restore a previously saved Dockview layout when one exists.
 *
 * Requirements:
 *   - Return false when no valid saved layout is available.
 *   - Clear malformed saved layout state before falling back.
 *
 * @param {object} api Dockview API instance.
 * @returns {boolean} True when a saved layout was restored.
 */
function restoreSavedLayout(api) {
  clearLegacyLayouts();
  try {
    const rawLayout = window.localStorage.getItem(LAYOUT_STORAGE_KEY);
    if (!rawLayout) {
      return false;
    }
    api.fromJSON(JSON.parse(rawLayout));
    return true;
  } catch (error) {
    window.localStorage.removeItem(LAYOUT_STORAGE_KEY);
    console.warn("[FAITH] Failed to restore Dockview layout state.", error);
    return false;
  }
}

/**
 * Description:
 *   Mount one legacy imperative panel runtime inside a React component.
 *
 * Requirements:
 *   - Reuse the proven panel logic while the workspace shell moves to React + Dockview.
 *   - Support cleanup functions returned either directly or through a ``destroy`` method.
 *
 * @param {string} namespace Global namespace that exposes ``mountPanel``.
 * @param {string} panelId Stable panel ID used for lifecycle coordination.
 * @param {object} params Panel parameter object passed through Dockview.
 * @returns {React.ReactElement} Mounted panel host element.
 */
function LegacyPanelBridge({ namespace, panelId, params }) {
  const hostRef = React.useRef(null);

  React.useEffect(
    function mountLegacyPanel() {
      const runtime = window[namespace];
      if (!runtime || typeof runtime.mountPanel !== "function" || !hostRef.current) {
        return undefined;
      }
      const cleanup = runtime.mountPanel(hostRef.current, params || {});
      registerMountedPanelHandle(panelId, cleanup);
      if (typeof cleanup === "function") {
        return function cleanupLegacyPanelFunction() {
          unregisterMountedPanelHandle(panelId, cleanup);
          cleanup();
        };
      }
      if (cleanup && typeof cleanup.destroy === "function") {
        return function cleanupLegacyPanel() {
          unregisterMountedPanelHandle(panelId, cleanup);
          cleanup.destroy();
        };
      }
      unregisterMountedPanelHandle(panelId, cleanup);
      return undefined;
    },
    [namespace, panelId, JSON.stringify(params || {})],
  );

  return React.createElement("div", { className: "faith-react-panel-host", ref: hostRef });
}

/**
 * Description:
 *   Render a placeholder panel for tool panels that do not yet have a dedicated UI.
 *
 * Requirements:
 *   - Keep the panel visible and labelled so add-panel actions remain predictable.
 *
 * @param {object} props Dockview panel props containing the tool parameters.
 * @returns {React.ReactElement} Placeholder tool panel.
 */
function ToolPlaceholderPanel(props) {
  const params = props && props.params ? props.params : {};
  return (
    <section className="faith-panel">
      <p className="faith-panel__placeholder">
        {`${params.displayName || params.toolId || "Tool"} panel placeholder`}
      </p>
    </section>
  );
}

/**
 * Description:
 *   Render the Dockview header action for closing panels.
 *
 * Requirements:
 *   - Keep the close affordance visible in the panel title bar.
 *   - Suppress the button for protected singleton panels.
 *
 * @param {object} props Dockview header action props.
 * @returns {React.ReactElement|null} Header action button or ``null``.
 */
function DockviewCloseAction(props) {
  const panelId = props && props.panel ? props.panel.id : "";
  const isProtectedPanel = panelId === "project-agent";
  if (isProtectedPanel || !props || !props.panel) {
    return null;
  }
  return (
    <button
      type="button"
      className="faith-toolbar__button"
      aria-label="Close panel"
      onClick={function closePanel() {
        if (!confirmPanelAction(panelId, "close the panel")) {
          return;
        }
        props.panel.api.close();
      }}
    >
      ×
    </button>
  );
}

/**
 * Description:
 *   Render the empty-state watermark inside Dockview when no panels remain.
 *
 * Requirements:
 *   - Give the user a clear hint that the add-panel control can restore panels.
 *
 * @returns {React.ReactElement} Empty-state watermark element.
 */
function WorkspaceWatermark() {
  return <div className="faith-dockview-watermark">Use the + button to reopen a panel.</div>;
}

/**
 * Description:
 *   Render the toolbar controls into the static server-rendered toolbar shell.
 *
 * Requirements:
 *   - Keep add-panel and reset-layout actions outside the Dockview content area.
 *   - Defer dynamic panel prompts until the Dockview API is ready.
 *
 * @param {object} props Toolbar properties.
 * @returns {React.ReactElement|null} Portal with toolbar controls or ``null``.
 */
function ToolbarControls(props) {
  const host = document.getElementById("faith-toolbar-controls");
  const isReady = Boolean(props.api);
  const [menuOpen, setMenuOpen] = React.useState(false);
  const addMenuRef = React.useRef(null);
  const panelOptions = [
    { label: "Project Agent", componentType: COMPONENT_TYPES.AGENT, state: { agentId: "project-agent", displayName: "Project Agent", model: "ollama/llama3:8b" }, title: "Project Agent", id: "project-agent" },
    { label: "System Status", componentType: COMPONENT_TYPES.STATUS, state: {}, title: "System Status", id: "system-status" },
    { label: "Input", componentType: COMPONENT_TYPES.INPUT, state: {}, title: "Input", id: "input" },
    { label: "Approvals", componentType: COMPONENT_TYPES.APPROVAL, state: {}, title: "Approvals", id: "approvals" },
    { label: "Docker Runtime", componentType: COMPONENT_TYPES.DOCKER_RUNTIME, state: {}, title: "Docker Runtime", id: "docker-runtime" },
    { label: "PA System Prompt", componentType: COMPONENT_TYPES.PA_SYSTEM_PROMPT, state: {}, title: "PA System Prompt", id: "pa-system-prompt" },
    { label: "User Settings", componentType: COMPONENT_TYPES.USER_SETTINGS, state: {}, title: "User Settings", id: "user-settings" },
    { label: "Agent Panel", componentType: COMPONENT_TYPES.AGENT, dynamic: "agent" },
    { label: "Tool Panel", componentType: COMPONENT_TYPES.TOOL, dynamic: "tool" },
  ];

  /**
   * Description:
   *   Add or reveal one requested panel from the toolbar.
   *
   * Requirements:
   *   - Prompt for runtime identities on dynamic agent and tool panels.
   *   - Reuse existing Dockview panels instead of creating duplicates.
   *
   * @param {object} option Toolbar panel option descriptor.
   * @returns {void}
   */
  function handleAddPanel(option) {
    if (!props.api) {
      return;
    }

    if (option.dynamic === "agent") {
      const agentId = window.prompt("Enter agent ID", "new-agent");
      if (!agentId) {
        return;
      }
      ensurePanel(props.api, {
        id: agentId,
        componentType: COMPONENT_TYPES.AGENT,
        title: agentId,
        componentState: {
          agentId: agentId,
          displayName: agentId,
          model: "unknown",
        },
      });
      setMenuOpen(false);
      return;
    }

    if (option.dynamic === "tool") {
      const toolId = window.prompt("Enter tool ID", "tool-id");
      if (!toolId) {
        return;
      }
      ensurePanel(props.api, {
        id: toolId,
        componentType: COMPONENT_TYPES.TOOL,
        title: toolId,
        componentState: {
          toolId: toolId,
          displayName: toolId,
        },
      });
      setMenuOpen(false);
      return;
    }

    ensurePanel(props.api, {
      id: option.id,
      componentType: option.componentType,
      title: option.title,
      componentState: option.state,
    });
    setMenuOpen(false);
  }

  /**
   * Description:
   *   Reset the browser workspace to the canonical default layout.
   *
   * Requirements:
   *   - Clear any saved Dockview state first.
   *   - Rebuild the layout immediately without forcing a browser reload.
   *
   * @returns {void}
   */
  function handleResetLayout() {
    if (!props.api) {
      return;
    }
    if (!confirmWorkspaceReset()) {
      return;
    }
    if (!window.confirm("Reset to the default FAITH layout?")) {
      return;
    }
    clearLegacyLayouts();
    window.localStorage.removeItem(LAYOUT_STORAGE_KEY);
    props.api.clear();
    applyDefaultWorkspace(props.api);
    saveLayout(props.api);
  }

  /**
   * Description:
   *   Close the add-panel menu when focus or pointer interaction moves outside it.
   *
   * Requirements:
   *   - Ignore outside-dismiss handling when the menu is already closed.
   *   - Keep interactions inside the menu wrapper from closing it.
   *
   * @returns {void}
   */
  React.useEffect(
    function subscribeOutsideDismiss() {
      if (!menuOpen) {
        return undefined;
      }

      /**
       * Description:
       *   Close the add-panel menu when one event target is outside the menu wrapper.
       *
       * Requirements:
       *   - Ignore events without a concrete event target.
       *   - Preserve the menu while interacting with its trigger or options.
       *
       * @param {Event} event Pointer or focus event raised by the browser.
       * @returns {void}
       */
      function handleOutsideInteraction(event) {
        if (!addMenuRef.current || !(event.target instanceof Node)) {
          return;
        }
        if (!addMenuRef.current.contains(event.target)) {
          setMenuOpen(false);
        }
      }

      window.addEventListener("pointerdown", handleOutsideInteraction);
      window.addEventListener("focusin", handleOutsideInteraction);
      return function cleanupOutsideDismiss() {
        window.removeEventListener("pointerdown", handleOutsideInteraction);
        window.removeEventListener("focusin", handleOutsideInteraction);
      };
    },
    [menuOpen],
  );

  if (!host) {
    return null;
  }

  return createPortal(
    <div className="faith-toolbar__controls">
      <details className="faith-toolbar__add-wrapper" ref={addMenuRef} open={menuOpen}>
        <summary
          className="faith-toolbar__button faith-toolbar__button--add"
          aria-label="Add panel"
          aria-expanded={menuOpen}
          onClick={function onToggleAddMenu(event) {
            event.preventDefault();
            setMenuOpen(!menuOpen);
          }}
        >
          +
        </summary>
        <div className="faith-toolbar__menu">
          {panelOptions.map(function renderPanelOption(option) {
            return (
              <button
                key={option.label}
                type="button"
                className="faith-toolbar__menu-item"
                disabled={!isReady}
                onClick={function onAddPanelClick() {
                  handleAddPanel(option);
                }}
              >
                {option.label}
              </button>
            );
          })}
        </div>
      </details>
      <button
        type="button"
        className="faith-toolbar__button"
        disabled={!isReady}
        onClick={handleResetLayout}
      >
        Reset Layout
      </button>
    </div>,
    host,
  );
}

/**
 * Description:
 *   Render the FAITH React + Dockview application shell.
 *
 * Requirements:
 *   - Initialise Dockview once and restore the saved layout when present.
 *   - Persist future layout changes through the Dockview API.
 *   - Keep the legacy panel runtimes mounted through thin bridge components.
 *
 * @returns {React.ReactElement} Root FAITH workspace application.
 */
function FaithWorkspaceApp() {
  const [dockviewApi, setDockviewApi] = React.useState(null);
  const hasInitialisedLayoutRef = React.useRef(false);

  React.useEffect(
    function subscribeLayoutPersistence() {
      if (!dockviewApi) {
        return undefined;
      }
      if (!hasInitialisedLayoutRef.current) {
        hasInitialisedLayoutRef.current = true;
        if (!restoreSavedLayout(dockviewApi)) {
          applyDefaultWorkspace(dockviewApi);
        }
      }
      const disposable = dockviewApi.onDidLayoutChange(function onLayoutChange() {
        saveLayout(dockviewApi);
      });
      return function disposeLayoutPersistence() {
        if (disposable && typeof disposable.dispose === "function") {
          disposable.dispose();
        }
      };
    },
    [dockviewApi],
  );

  return (
    <React.Fragment>
      <ToolbarControls api={dockviewApi} />
      <DockviewReact
        className="faith-dockview-shell dockview-theme-dark"
        components={{
          [COMPONENT_TYPES.AGENT]: function AgentPanelComponent(props) {
            return (
              <LegacyPanelBridge
                namespace="faithAgentPanel"
                panelId={buildPanelId(COMPONENT_TYPES.AGENT, props.params || {}, "agent")}
                params={props.params}
              />
            );
          },
          [COMPONENT_TYPES.INPUT]: function InputPanelComponent() {
            return <LegacyPanelBridge namespace="faithInputPanel" panelId="input" params={{}} />;
          },
          [COMPONENT_TYPES.APPROVAL]: function ApprovalPanelComponent() {
            return (
              <LegacyPanelBridge
                namespace="faithApprovalPanel"
                panelId="approvals"
                params={{}}
              />
            );
          },
          [COMPONENT_TYPES.STATUS]: function StatusPanelComponent() {
            return (
              <LegacyPanelBridge
                namespace="faithDockerRuntimePanel"
                panelId="system-status"
                params={{}}
              />
            );
          },
          [COMPONENT_TYPES.DOCKER_RUNTIME]: function DockerRuntimePanelComponent() {
            return (
              <LegacyPanelBridge
                namespace="faithDockerRuntimePanel"
                panelId="docker-runtime"
                params={{}}
              />
            );
          },
          [COMPONENT_TYPES.PA_SYSTEM_PROMPT]: function PaSystemPromptPanelComponent() {
            return (
              <LegacyPanelBridge
                namespace="faithPaSystemPromptPanel"
                panelId="pa-system-prompt"
                params={{}}
              />
            );
          },
          [COMPONENT_TYPES.USER_SETTINGS]: function UserSettingsPanelComponent() {
            return (
              <LegacyPanelBridge
                namespace="faithUserSettingsPanel"
                panelId="user-settings"
                params={{}}
              />
            );
          },
          [COMPONENT_TYPES.TOOL]: ToolPlaceholderPanel,
        }}
        getTabContextMenuItems={function getTabContextMenuItems() {
          return ["close", "closeOthers", "closeAll"];
        }}
        rightHeaderActionsComponent={DockviewCloseAction}
        watermarkComponent={WorkspaceWatermark}
        onReady={function onDockviewReady(event) {
          setDockviewApi(event.api);
        }}
      />
    </React.Fragment>
  );
}

/**
 * Description:
 *   Mount the FAITH React application into the server-rendered page.
 *
 * Requirements:
 *   - Fail visibly when the mount element is missing.
 *
 * @returns {void}
 */
function bootstrapFaithReactWorkspace() {
  const mountNode = document.getElementById("faith-app");
  if (!mountNode) {
    throw new Error("FAITH React mount node was not found.");
  }
  const root = createRoot(mountNode);
  root.render(<FaithWorkspaceApp />);
}

bootstrapFaithReactWorkspace();
