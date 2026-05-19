/**
 * Description:
 *   Bootstrap the bundled React + Dockview FAITH browser shell.
 *
 * Requirements:
 *   - Reuse the existing browser panel runtimes while replacing the workspace engine.
 *   - Persist and restore Dockview layouts together with minimized-panel state.
 *   - Provide a maintained Radix UI menubar and context-menu layer for shell actions.
 */

import React from "react";
import { createPortal } from "react-dom";
import { createRoot } from "react-dom/client";
import * as ContextMenu from "@radix-ui/react-context-menu";
import { DockviewReact } from "dockview";
import * as Menubar from "@radix-ui/react-menubar";
import workspaceLayoutSnap from "./workspace-layout-snap.js";

import "./faith-ui.css";

const { normalizeLayoutForPersistence } = workspaceLayoutSnap;

const LAYOUT_STORAGE_KEY = "faith_dockview_layout_v2";
const LEGACY_LAYOUT_STORAGE_KEYS = Object.freeze([
  "faith_dockview_layout_v1",
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
  AUDIT_TRAIL: "audit-trail-panel",
  EVENT_TIMELINE: "event-timeline-panel",
  SESSION_HISTORY: "session-history-panel",
  TOKEN_USAGE: "token-usage-panel",
  APPROVAL_HISTORY: "approval-history-panel",
  EFFECTIVE_CONTEXT: "effective-context-panel",
  PA_SYSTEM_PROMPT: "pa-system-prompt-panel",
  USER_SETTINGS: "user-settings-panel",
  MODEL_SETTINGS: "model-settings-panel",
});

const SHELL_PANEL_OPTIONS = Object.freeze([
  {
    label: "Project Agent",
    id: "project-agent",
    title: "Project Agent",
    componentType: COMPONENT_TYPES.AGENT,
    componentState: {
      agentId: "project-agent",
      displayName: "Project Agent",
      model: "ollama/llama3:8b",
    },
  },
  {
    label: "System Status",
    id: "system-status",
    title: "System Status",
    componentType: COMPONENT_TYPES.STATUS,
    componentState: {},
  },
  {
    label: "Input",
    id: "input",
    title: "Input",
    componentType: COMPONENT_TYPES.INPUT,
    componentState: {},
  },
  {
    label: "Approvals",
    id: "approvals",
    title: "Approvals",
    componentType: COMPONENT_TYPES.APPROVAL,
    componentState: {},
  },
  {
    label: "Docker Runtime",
    id: "docker-runtime",
    title: "Docker Runtime",
    componentType: COMPONENT_TYPES.DOCKER_RUNTIME,
    componentState: {},
  },
  {
    label: "Audit Trail",
    id: "audit-trail",
    title: "Audit Trail",
    componentType: COMPONENT_TYPES.AUDIT_TRAIL,
    componentState: {},
  },
  {
    label: "Event Timeline",
    id: "event-timeline",
    title: "Event Timeline",
    componentType: COMPONENT_TYPES.EVENT_TIMELINE,
    componentState: {},
  },
  {
    label: "Session History",
    id: "session-history",
    title: "Session History",
    componentType: COMPONENT_TYPES.SESSION_HISTORY,
    componentState: {},
  },
  {
    label: "Effective Context",
    id: "effective-context",
    title: "Effective Context",
    componentType: COMPONENT_TYPES.EFFECTIVE_CONTEXT,
    componentState: {},
  },
  {
    label: "Token Usage",
    id: "token-usage",
    title: "Token Usage",
    componentType: COMPONENT_TYPES.TOKEN_USAGE,
    componentState: {},
  },
  {
    label: "Approval History",
    id: "approval-history",
    title: "Approval History",
    componentType: COMPONENT_TYPES.APPROVAL_HISTORY,
    componentState: {},
  },
  {
    label: "PA System Prompt",
    id: "pa-system-prompt",
    title: "PA System Prompt",
    componentType: COMPONENT_TYPES.PA_SYSTEM_PROMPT,
    componentState: {},
  },
  {
    label: "User Settings",
    id: "user-settings",
    title: "User Settings",
    componentType: COMPONENT_TYPES.USER_SETTINGS,
    componentState: {},
  },
  {
    label: "Model Settings",
    id: "model-settings",
    title: "Model Settings",
    componentType: COMPONENT_TYPES.MODEL_SETTINGS,
    componentState: {},
  },
  {
    label: "Agent Panel",
    dynamic: "agent",
    componentType: COMPONENT_TYPES.AGENT,
  },
  {
    label: "Tool Panel",
    dynamic: "tool",
    componentType: COMPONENT_TYPES.TOOL,
  },
]);

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
    upperLeftGroup: {
      panels: [
        {
          id: "session-history",
          componentType: COMPONENT_TYPES.SESSION_HISTORY,
          title: "Session History",
          componentState: {},
        },
      ],
    },
    upperRightGroup: {
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
 *   Normalize one panel descriptor into the shape expected by the workspace helpers.
 *
 * Requirements:
 *   - Preserve existing panel identity rules for singleton and runtime panels.
 *
 * @param {object} panelDescriptor Partial or complete panel descriptor.
 * @returns {object} Normalized descriptor with stable ID, title, component type, and state.
 */
function normalizePanelDescriptor(panelDescriptor) {
  const componentState = panelDescriptor.componentState || panelDescriptor.componentState === null
    ? panelDescriptor.componentState || {}
    : panelDescriptor.params || {};
  const componentType =
    panelDescriptor.componentType || panelDescriptor.component || COMPONENT_TYPES.TOOL;
  const fallbackId =
    panelDescriptor.id ||
    componentState.agentId ||
    componentState.toolId ||
    componentType;

  return {
    id: buildPanelId(componentType, componentState, fallbackId),
    title: panelDescriptor.title || componentState.displayName || componentState.toolId || fallbackId,
    componentType: componentType,
    componentState: componentState,
  };
}

/**
 * Description:
 *   Build a serializable panel descriptor from one Dockview panel instance.
 *
 * Requirements:
 *   - Preserve the params and title needed to recreate the panel later.
 *
 * @param {object} panel Dockview panel instance.
 * @returns {object} Normalized panel descriptor.
 */
function buildPanelDescriptorFromDockviewPanel(panel) {
  return normalizePanelDescriptor({
    id: panel.id,
    title: panel.title,
    componentType: panel.component,
    componentState: panel.params || {},
  });
}

/**
 * Description:
 *   Return the best-effort default restore placement for a known FAITH panel.
 *
 * Requirements:
 *   - Rebuild the agreed default arrangement when a more precise restore hint is unavailable.
 *
 * @param {object} panelDescriptor Normalized panel descriptor.
 * @returns {object} Restore placement hint.
 */
function getDefaultRestorePlacement(panelDescriptor) {
  switch (panelDescriptor.id) {
    case "session-history":
      return { referencePanelId: "agent:project-agent", direction: "left" };
    case "system-status":
      return { referencePanelId: "agent:project-agent", direction: "within" };
    case "input":
      return { referencePanelId: "agent:project-agent", direction: "below" };
    case "user-settings":
      return { referencePanelId: "input", direction: "within" };
    case "approvals":
      return { referencePanelId: "input", direction: "right" };
    default:
      return { referencePanelId: "agent:project-agent", direction: "within" };
  }
}

/**
 * Description:
 *   Build the best available restore placement for one minimized panel.
 *
 * Requirements:
 *   - Prefer restoring a panel back into its previous tab group when sibling tabs still exist.
 *   - Fall back to the canonical FAITH layout slots when no live sibling reference remains.
 *
 * @param {object} panelDescriptor Normalized panel descriptor.
 * @param {object} panelApi Dockview panel API instance.
 * @returns {object} Restore placement hint.
 */
function buildRestorePlacement(panelDescriptor, panelApi) {
  const siblingPanels =
    panelApi && panelApi.group && Array.isArray(panelApi.group.panels)
      ? panelApi.group.panels.filter(function keepSibling(panel) {
          return panel.id !== panelDescriptor.id;
        })
      : [];

  if (siblingPanels.length > 0) {
    return {
      referencePanelId: siblingPanels[0].id,
      direction: "within",
    };
  }

  return getDefaultRestorePlacement(panelDescriptor);
}

/**
 * Description:
 *   Resolve one restore placement into a Dockview addPanel position.
 *
 * Requirements:
 *   - Fall back cleanly when the recorded reference panel no longer exists.
 *
 * @param {object} api Dockview API instance.
 * @param {object} restorePlacement Saved restore placement descriptor.
 * @returns {object|undefined} Dockview position object when it can be resolved.
 */
function resolveRestorePosition(api, restorePlacement) {
  if (!restorePlacement || !restorePlacement.referencePanelId) {
    return undefined;
  }
  const referencePanel = api.getPanel(restorePlacement.referencePanelId);
  if (!referencePanel) {
    return undefined;
  }
  return {
    referencePanel: restorePlacement.referencePanelId,
    direction: restorePlacement.direction || "within",
  };
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
 * @param {object|undefined} position Optional Dockview positioning directive.
 * @param {boolean} inactive Whether the new panel should stay inactive after insertion.
 * @returns {object} Existing or newly created Dockview panel object.
 */
function ensurePanel(api, panelDescriptor, position, inactive = false) {
  const normalizedDescriptor = normalizePanelDescriptor(panelDescriptor);
  const existingPanel = api.getPanel(normalizedDescriptor.id);
  if (existingPanel) {
    existingPanel.api.setActive();
    return existingPanel;
  }
  return api.addPanel({
    id: normalizedDescriptor.id,
    component: normalizedDescriptor.componentType,
    title: normalizedDescriptor.title,
    params: normalizedDescriptor.componentState,
    position: position,
    inactive: inactive,
  });
}

/**
 * Description:
 *   Apply the canonical first-load workspace arrangement to Dockview.
 *
 * Requirements:
 *   - Keep Session History beside the Project Agent workspace in the upper region.
 *   - Keep Project Agent and System Status in one upper-right tab group.
 *   - Keep Input and User Settings in one lower-left tab group.
 *   - Keep Approvals in the lower-right split beneath the upper group.
 *
 * @param {object} api Dockview API instance.
 */
function applyDefaultWorkspace(api) {
  const workspaceDescriptor = getDefaultWorkspaceDescriptor();
  const upperLeftPanels =
    workspaceDescriptor.upperLeftGroup && Array.isArray(workspaceDescriptor.upperLeftGroup.panels)
      ? workspaceDescriptor.upperLeftGroup.panels
      : [];
  const upperRightPanels =
    workspaceDescriptor.upperRightGroup && Array.isArray(workspaceDescriptor.upperRightGroup.panels)
      ? workspaceDescriptor.upperRightGroup.panels
      : [];
  const lowerPanels =
    workspaceDescriptor.lowerGroup && Array.isArray(workspaceDescriptor.lowerGroup.panels)
      ? workspaceDescriptor.lowerGroup.panels
      : [];

  if (upperLeftPanels.length === 0 && upperRightPanels.length === 0) {
    return;
  }

  const primaryUpperRightPanel = ensurePanel(
    api,
    upperRightPanels.length > 0 ? upperRightPanels[0] : upperLeftPanels[0],
  );

  upperRightPanels.slice(1).forEach(function addUpperTab(panelDescriptor) {
    ensurePanel(
      api,
      panelDescriptor,
      {
        referencePanel: primaryUpperRightPanel.id,
        direction: "within",
      },
      true,
    );
  });

  const primaryUpperLeftPanel =
    upperLeftPanels.length > 0
      ? ensurePanel(api, upperLeftPanels[0], {
          referencePanel: primaryUpperRightPanel.id,
          direction: "left",
        })
      : primaryUpperRightPanel;

  upperLeftPanels.slice(1).forEach(function addUpperLeftTab(panelDescriptor) {
    ensurePanel(
      api,
      panelDescriptor,
      {
        referencePanel: primaryUpperLeftPanel.id,
        direction: "within",
      },
      true,
    );
  });

  if (lowerPanels.length === 0) {
    return;
  }

  const lowerPrimaryPanel = ensurePanel(api, lowerPanels[0], {
    referencePanel: primaryUpperRightPanel.id,
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
      referencePanel:
        index === 0
          ? lowerPrimaryPanel.id
          : buildPanelId(
              lowerPanels[index].componentType,
              lowerPanels[index].componentState || {},
              lowerPanels[index].id,
            ),
      direction: "right",
    });
  });
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
 *   Persist the current Dockview layout and minimized tray state in localStorage.
 *
 * Requirements:
 *   - Fail quietly when browser storage is unavailable so the workspace still runs.
 *
 * @param {object} api Dockview API instance.
 * @param {object[]} minimizedPanels Serialized minimized panel descriptors.
 * @returns {void}
 */
function saveWorkspaceState(api, minimizedPanels) {
  try {
    // Dockview does not expose a true Datadog-style grid engine, so FAITH normalizes
    // persisted split sizes to tidy increments instead of fighting live drag behavior.
    const normalizedLayout = normalizeLayoutForPersistence(api.toJSON());
    window.localStorage.setItem(
      LAYOUT_STORAGE_KEY,
      JSON.stringify({
        layout: normalizedLayout,
        minimizedPanels: minimizedPanels,
      }),
    );
  } catch (error) {
    console.warn("[FAITH] Failed to save Dockview workspace state.", error);
  }
}

/**
 * Description:
 *   Restore the saved Dockview layout and minimized tray state when available.
 *
 * Requirements:
 *   - Preserve backward compatibility with the earlier layout-only storage format.
 *   - Clear malformed saved workspace state before falling back.
 *
 * @param {object} api Dockview API instance.
 * @returns {object} Restored workspace state metadata.
 */
function restoreSavedWorkspaceState(api) {
  clearLegacyLayouts();
  try {
    const rawWorkspaceState = window.localStorage.getItem(LAYOUT_STORAGE_KEY);
    if (!rawWorkspaceState) {
      return {
        restored: false,
        minimizedPanels: [],
      };
    }

    const parsedWorkspaceState = JSON.parse(rawWorkspaceState);
    if (parsedWorkspaceState && parsedWorkspaceState.layout) {
      api.fromJSON(normalizeLayoutForPersistence(parsedWorkspaceState.layout));
      return {
        restored: true,
        minimizedPanels: Array.isArray(parsedWorkspaceState.minimizedPanels)
          ? parsedWorkspaceState.minimizedPanels
          : [],
      };
    }

    api.fromJSON(normalizeLayoutForPersistence(parsedWorkspaceState));
    return {
      restored: true,
      minimizedPanels: [],
    };
  } catch (error) {
    window.localStorage.removeItem(LAYOUT_STORAGE_KEY);
    console.warn("[FAITH] Failed to restore Dockview workspace state.", error);
    return {
      restored: false,
      minimizedPanels: [],
    };
  }
}

/**
 * Description:
 *   Mount one legacy imperative panel runtime inside a React component.
 *
 * Requirements:
 *   - Reuse the proven panel logic while the workspace shell moves to React + Dockview.
 *   - Support cleanup functions returned either directly or through a `destroy` method.
 *
 * @param {string} namespace Global namespace that exposes `mountPanel`.
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

  return <div className="faith-react-panel-host" ref={hostRef} />;
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
 *   Render a Radix context menu around one panel surface.
 *
 * Requirements:
 *   - Reuse the same minimize and close commands exposed through the shell menubar.
 *
 * @param {object} options Panel context-menu options.
 * @returns {React.ReactElement} Context-menu wrapped panel content.
 */
function renderPanelContextMenu(options) {
  const closeDisabled = options.closeDisabled || false;

  return (
    <ContextMenu.Root>
      <ContextMenu.Trigger asChild>{options.children}</ContextMenu.Trigger>
      <ContextMenu.Portal>
        <ContextMenu.Content className="faith-shell-menu faith-shell-menu--context" sideOffset={6}>
          <ContextMenu.Item
            className="faith-shell-menu__item"
            onSelect={function onMinimizeSelect(event) {
              event.preventDefault();
              options.onMinimize();
            }}
          >
            Minimize
          </ContextMenu.Item>
          <ContextMenu.Item
            className="faith-shell-menu__item"
            disabled={closeDisabled}
            onSelect={function onCloseSelect(event) {
              event.preventDefault();
              if (!closeDisabled) {
                options.onClose();
              }
            }}
          >
            Close
          </ContextMenu.Item>
        </ContextMenu.Content>
      </ContextMenu.Portal>
    </ContextMenu.Root>
  );
}

/**
 * Description:
 *   Wrap one panel surface with the shared context-menu actions.
 *
 * Requirements:
 *   - Keep panel command routing consistent across every panel type.
 *
 * @param {object} props Panel shell properties.
 * @returns {React.ReactElement} Wrapped panel content.
 */
function PanelActionFrame(props) {
  return renderPanelContextMenu({
    closeDisabled: props.closeDisabled,
    onClose: props.onClose,
    onMinimize: props.onMinimize,
    children: <div className="faith-panel-frame">{props.children}</div>,
  });
}

/**
 * Description:
 *   Render the Dockview header actions for minimizing and closing panels.
 *
 * Requirements:
 *   - Keep minimize available for supported panels.
 *   - Suppress close for protected singleton panels.
 *
 * @param {object} props Dockview header action props.
 * @returns {React.ReactElement|null} Header action buttons or `null`.
 */
function DockviewHeaderActions(props) {
  if (!props || !props.activePanel) {
    return null;
  }
  const panelId = props.activePanel.id;
  const closeDisabled = panelId === "agent:project-agent";

  return (
    <div className="faith-panel-actions">
      <button
        type="button"
        className="faith-toolbar__button"
        aria-label="Minimize panel"
        onClick={function onMinimizeClick() {
          props.onMinimize(
            buildPanelDescriptorFromDockviewPanel(props.activePanel),
            props.activePanel.api,
          );
        }}
      >
        _
      </button>
      {!closeDisabled ? (
        <button
          type="button"
          className="faith-toolbar__button"
          aria-label="Close panel"
          onClick={function onCloseClick() {
            props.onClose(panelId, props.activePanel.api);
          }}
        >
          ×
        </button>
      ) : null}
    </div>
  );
}

/**
 * Description:
 *   Render the empty-state watermark inside Dockview when no panels remain.
 *
 * Requirements:
 *   - Give the user a clear hint that the panels menubar can restore panels.
 *
 * @returns {React.ReactElement} Empty-state watermark element.
 */
function WorkspaceWatermark() {
  return <div className="faith-dockview-watermark">Use Panels in the menu bar to reopen a panel.</div>;
}

/**
 * Description:
 *   Render the shell menubar into the static server-rendered toolbar host.
 *
 * Requirements:
 *   - Provide maintained Radix UI menus for workspace and panel actions.
 *   - Reuse the same panel-opening and reset handlers as the wider shell.
 *
 * @param {object} props Menubar properties.
 * @returns {React.ReactElement|null} Portal with the toolbar menubar or `null`.
 */
function ToolbarControls(props) {
  const host = document.getElementById("faith-toolbar-controls");

  function renderMenubar() {
    return (
      <Menubar.Root className="faith-toolbar__menubar">
        <Menubar.Menu>
          <Menubar.Trigger className="faith-toolbar__button">Workspace</Menubar.Trigger>
          <Menubar.Portal>
            <Menubar.Content className="faith-shell-menu" sideOffset={6} align="end">
              <Menubar.Item
                className="faith-shell-menu__item"
                disabled={!props.isReady}
                onSelect={function onResetSelect(event) {
                  event.preventDefault();
                  props.handleResetLayout();
                }}
              >
                Reset Layout
              </Menubar.Item>
            </Menubar.Content>
          </Menubar.Portal>
        </Menubar.Menu>
        <Menubar.Menu>
          <Menubar.Trigger className="faith-toolbar__button">Panels</Menubar.Trigger>
          <Menubar.Portal>
            <Menubar.Content className="faith-shell-menu" sideOffset={6} align="end">
              {SHELL_PANEL_OPTIONS.map(function renderPanelOption(option) {
                return (
                  <Menubar.Item
                    key={option.label}
                    className="faith-shell-menu__item"
                    disabled={!props.isReady}
                    onSelect={function onPanelSelect(event) {
                      event.preventDefault();
                      props.handleAddPanelOption(option);
                    }}
                  >
                    {option.label}
                  </Menubar.Item>
                );
              })}
            </Menubar.Content>
          </Menubar.Portal>
        </Menubar.Menu>
      </Menubar.Root>
    );
  }

  if (!host) {
    return null;
  }

  return createPortal(renderMenubar(), host);
}

/**
 * Description:
 *   Render the tray of minimized panels below the main workspace.
 *
 * Requirements:
 *   - Keep minimized panels discoverable and restorable without reopening the full add-panel menu.
 *
 * @param {object} props Tray properties.
 * @returns {React.ReactElement|null} Restore tray or `null`.
 */
function MinimizedPanelTray(props) {
  if (!props.minimizedPanels.length) {
    return null;
  }

  return (
    <div className="faith-toolbar__tray" aria-label="Minimized panels">
      <span className="faith-toolbar__tray-label">Minimized</span>
      <div className="faith-toolbar__tray-items">
        {props.minimizedPanels.map(function renderTrayItem(panelDescriptor) {
          return (
            <button
              key={panelDescriptor.id}
              type="button"
              className="faith-toolbar__tray-button"
              onClick={function onRestoreClick() {
                props.handleRestoreMinimizedPanel(panelDescriptor.id);
              }}
            >
              {panelDescriptor.title}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * Description:
 *   Render the FAITH React + Dockview application shell.
 *
 * Requirements:
 *   - Initialise Dockview once and restore the saved layout when present.
 *   - Persist future layout changes together with minimized tray state.
 *   - Keep the legacy panel runtimes mounted through thin bridge components.
 *
 * @returns {React.ReactElement} Root FAITH workspace application.
 */
function FaithWorkspaceApp() {
  const [dockviewApi, setDockviewApi] = React.useState(null);
  const [minimizedPanels, setMinimizedPanels] = React.useState([]);
  const hasInitialisedLayoutRef = React.useRef(false);
  const minimizedPanelsRef = React.useRef([]);

  React.useEffect(
    function syncMinimizedPanelsRef() {
      minimizedPanelsRef.current = minimizedPanels;
    },
    [minimizedPanels],
  );

  /**
   * Description:
   *   Persist the current workspace state when the Dockview API is ready.
   *
   * Requirements:
   *   - Avoid writing state before the initial restore or default layout application finishes.
   *
   * @returns {void}
   */
  function persistWorkspaceState() {
    if (!dockviewApi || !hasInitialisedLayoutRef.current) {
      return;
    }
    saveWorkspaceState(dockviewApi, minimizedPanelsRef.current);
  }

  /**
   * Description:
   *   Close one panel through the shared shell command path.
   *
   * Requirements:
   *   - Confirm unsaved changes before closing.
   *   - Keep the protected Project Agent singleton from being closed accidentally.
   *
   * @param {object} panel Dockview panel instance.
   * @returns {void}
   */
  function handleClosePanel(panelId, panelApi) {
    if (!panelId || !panelApi || panelId === "agent:project-agent") {
      return;
    }
    if (!confirmPanelAction(panelId, "close the panel")) {
      return;
    }
    panelApi.close();
  }

  /**
   * Description:
   *   Minimize one panel into the bottom restore tray.
   *
   * Requirements:
   *   - Remove the panel from the active Dockview layout.
   *   - Preserve enough metadata to restore it later.
   *
   * @param {object} panel Dockview panel instance.
   * @returns {void}
   */
  function handleMinimizePanel(panelDescriptor, panelApi) {
    if (!dockviewApi || !panelDescriptor || !panelApi) {
      return;
    }
    if (!confirmPanelAction(panelDescriptor.id, "minimize the panel")) {
      return;
    }
    const restorePlacement = buildRestorePlacement(panelDescriptor, panelApi);

    setMinimizedPanels(function updateMinimizedPanels(currentPanels) {
      if (currentPanels.some((entry) => entry.id === panelDescriptor.id)) {
        return currentPanels;
      }
      return currentPanels.concat([
        {
          ...panelDescriptor,
          restorePlacement: restorePlacement,
        },
      ]);
    });

    panelApi.close();
  }

  /**
   * Description:
   *   Restore one minimized panel back into the Dockview workspace.
   *
   * Requirements:
   *   - Return the panel to its prior restore hint when the reference panel still exists.
   *   - Fall back cleanly when the original placement can no longer be recreated.
   *
   * @param {string} panelId Stable minimized-panel identifier.
   * @returns {void}
   */
  function handleRestoreMinimizedPanel(panelId) {
    if (!dockviewApi) {
      return;
    }
    const minimizedPanel = minimizedPanelsRef.current.find(function findMinimizedPanel(entry) {
      return entry.id === panelId;
    });
    if (!minimizedPanel) {
      return;
    }

    const restorePosition = resolveRestorePosition(dockviewApi, minimizedPanel.restorePlacement);
    ensurePanel(dockviewApi, minimizedPanel, restorePosition);

    setMinimizedPanels(function removeRestoredPanel(currentPanels) {
      return currentPanels.filter(function keepRemainingPanel(entry) {
        return entry.id !== panelId;
      });
    });
  }

  /**
   * Description:
   *   Add or reveal one requested panel from the shell menubar.
   *
   * Requirements:
   *   - Prompt for runtime identities on dynamic agent and tool panels.
   *   - Reuse existing Dockview panels instead of creating duplicates.
   *
   * @param {object} option Toolbar panel option descriptor.
   * @returns {void}
   */
  function handleAddPanelOption(option) {
    if (!dockviewApi) {
      return;
    }

    if (option.dynamic === "agent") {
      const agentId = window.prompt("Enter agent ID", "new-agent");
      if (!agentId) {
        return;
      }
      ensurePanel(dockviewApi, {
        id: agentId,
        title: agentId,
        componentType: COMPONENT_TYPES.AGENT,
        componentState: {
          agentId: agentId,
          displayName: agentId,
          model: "unknown",
        },
      });
      return;
    }

    if (option.dynamic === "tool") {
      const toolId = window.prompt("Enter tool ID", "tool-id");
      if (!toolId) {
        return;
      }
      ensurePanel(dockviewApi, {
        id: toolId,
        title: toolId,
        componentType: COMPONENT_TYPES.TOOL,
        componentState: {
          toolId: toolId,
          displayName: toolId,
        },
      });
      return;
    }

    ensurePanel(dockviewApi, option);
  }

  /**
   * Description:
   *   Reset the browser workspace to the canonical default layout.
   *
   * Requirements:
   *   - Clear any saved Dockview and minimize state first.
   *   - Rebuild the layout immediately without forcing a browser reload.
   *
   * @returns {void}
   */
  function handleResetLayout() {
    if (!dockviewApi) {
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
    setMinimizedPanels([]);
    dockviewApi.clear();
    applyDefaultWorkspace(dockviewApi);
    saveWorkspaceState(dockviewApi, []);
  }

  React.useEffect(
    function restoreWorkspaceOnReady() {
      if (!dockviewApi || hasInitialisedLayoutRef.current) {
        return undefined;
      }

      hasInitialisedLayoutRef.current = true;
      const restoredWorkspaceState = restoreSavedWorkspaceState(dockviewApi);
      if (!restoredWorkspaceState.restored) {
        applyDefaultWorkspace(dockviewApi);
      }
      setMinimizedPanels(restoredWorkspaceState.minimizedPanels);
      saveWorkspaceState(
        dockviewApi,
        restoredWorkspaceState.minimizedPanels,
      );
      return undefined;
    },
    [dockviewApi],
  );

  React.useEffect(
    function subscribeLayoutPersistence() {
      if (!dockviewApi) {
        return undefined;
      }

      const disposable = dockviewApi.onDidLayoutChange(function onLayoutChange() {
        persistWorkspaceState();
      });

      return function disposeLayoutPersistence() {
        if (disposable && typeof disposable.dispose === "function") {
          disposable.dispose();
        }
      };
    },
    [dockviewApi],
  );

  React.useEffect(
    function persistMinimizedPanels() {
      persistWorkspaceState();
    },
    [dockviewApi, minimizedPanels],
  );

  return (
    <div className="faith-workspace-shell">
      <ToolbarControls
        handleAddPanelOption={handleAddPanelOption}
        handleResetLayout={handleResetLayout}
        isReady={Boolean(dockviewApi)}
      />
      <DockviewReact
        className="faith-dockview-shell dockview-theme-dark"
        components={{
          [COMPONENT_TYPES.AGENT]: function AgentPanelComponent(props) {
            const panelId = buildPanelId(COMPONENT_TYPES.AGENT, props.params || {}, "agent");
            return (
              <PanelActionFrame
                closeDisabled={panelId === "agent:project-agent"}
                onClose={function onClose() {
                  handleClosePanel(panelId, props.api);
                }}
                onMinimize={function onMinimize() {
                  handleMinimizePanel(
                    normalizePanelDescriptor({
                      id: panelId,
                      title: props.api.title,
                      componentType: COMPONENT_TYPES.AGENT,
                      componentState: props.params || {},
                    }),
                    props.api,
                  );
                }}
              >
                <LegacyPanelBridge
                  namespace="faithAgentPanel"
                  panelId={panelId}
                  params={props.params}
                />
              </PanelActionFrame>
            );
          },
          [COMPONENT_TYPES.INPUT]: function InputPanelComponent(props) {
            return (
              <PanelActionFrame
                onClose={function onClose() {
                  handleClosePanel("input", props.api);
                }}
                onMinimize={function onMinimize() {
                  handleMinimizePanel(
                    normalizePanelDescriptor({
                      id: "input",
                      title: props.api.title,
                      componentType: COMPONENT_TYPES.INPUT,
                      componentState: {},
                    }),
                    props.api,
                  );
                }}
              >
                <LegacyPanelBridge namespace="faithInputPanel" panelId="input" params={{}} />
              </PanelActionFrame>
            );
          },
          [COMPONENT_TYPES.APPROVAL]: function ApprovalPanelComponent(props) {
            return (
              <PanelActionFrame
                onClose={function onClose() {
                  handleClosePanel("approvals", props.api);
                }}
                onMinimize={function onMinimize() {
                  handleMinimizePanel(
                    normalizePanelDescriptor({
                      id: "approvals",
                      title: props.api.title,
                      componentType: COMPONENT_TYPES.APPROVAL,
                      componentState: {},
                    }),
                    props.api,
                  );
                }}
              >
                <LegacyPanelBridge namespace="faithApprovalPanel" panelId="approvals" params={{}} />
              </PanelActionFrame>
            );
          },
          [COMPONENT_TYPES.STATUS]: function StatusPanelComponent(props) {
            return (
              <PanelActionFrame
                onClose={function onClose() {
                  handleClosePanel("system-status", props.api);
                }}
                onMinimize={function onMinimize() {
                  handleMinimizePanel(
                    normalizePanelDescriptor({
                      id: "system-status",
                      title: props.api.title,
                      componentType: COMPONENT_TYPES.STATUS,
                      componentState: {},
                    }),
                    props.api,
                  );
                }}
              >
                <LegacyPanelBridge
                  namespace="faithDockerRuntimePanel"
                  panelId="system-status"
                  params={{}}
                />
              </PanelActionFrame>
            );
          },
          [COMPONENT_TYPES.DOCKER_RUNTIME]: function DockerRuntimePanelComponent(props) {
            return (
              <PanelActionFrame
                onClose={function onClose() {
                  handleClosePanel("docker-runtime", props.api);
                }}
                onMinimize={function onMinimize() {
                  handleMinimizePanel(
                    normalizePanelDescriptor({
                      id: "docker-runtime",
                      title: props.api.title,
                      componentType: COMPONENT_TYPES.DOCKER_RUNTIME,
                      componentState: {},
                    }),
                    props.api,
                  );
                }}
              >
                <LegacyPanelBridge
                  namespace="faithDockerRuntimePanel"
                  panelId="docker-runtime"
                  params={{}}
                />
              </PanelActionFrame>
            );
          },
          [COMPONENT_TYPES.AUDIT_TRAIL]: function AuditTrailPanelComponent(props) {
            return (
              <PanelActionFrame
                onClose={function onClose() {
                  handleClosePanel("audit-trail", props.api);
                }}
                onMinimize={function onMinimize() {
                  handleMinimizePanel(
                    normalizePanelDescriptor({
                      id: "audit-trail",
                      title: props.api.title,
                      componentType: COMPONENT_TYPES.AUDIT_TRAIL,
                      componentState: {},
                    }),
                    props.api,
                  );
                }}
              >
                <LegacyPanelBridge
                  namespace="faithAuditTrailPanel"
                  panelId="audit-trail"
                  params={{}}
                />
              </PanelActionFrame>
            );
          },
          [COMPONENT_TYPES.EVENT_TIMELINE]: function EventTimelinePanelComponent(props) {
            return (
              <PanelActionFrame
                onClose={function onClose() {
                  handleClosePanel("event-timeline", props.api);
                }}
                onMinimize={function onMinimize() {
                  handleMinimizePanel(
                    normalizePanelDescriptor({
                      id: "event-timeline",
                      title: props.api.title,
                      componentType: COMPONENT_TYPES.EVENT_TIMELINE,
                      componentState: {},
                    }),
                    props.api,
                  );
                }}
              >
                <LegacyPanelBridge
                  namespace="faithEventTimelinePanel"
                  panelId="event-timeline"
                  params={{}}
                />
              </PanelActionFrame>
            );
          },
          [COMPONENT_TYPES.SESSION_HISTORY]: function SessionHistoryPanelComponent(props) {
            return (
              <PanelActionFrame
                onClose={function onClose() {
                  handleClosePanel("session-history", props.api);
                }}
                onMinimize={function onMinimize() {
                  handleMinimizePanel(
                    normalizePanelDescriptor({
                      id: "session-history",
                      title: props.api.title,
                      componentType: COMPONENT_TYPES.SESSION_HISTORY,
                      componentState: {},
                    }),
                    props.api,
                  );
                }}
              >
              <LegacyPanelBridge
                namespace="faithSessionHistoryPanel"
                panelId="session-history"
                params={{}}
              />
            </PanelActionFrame>
          );
        },
          [COMPONENT_TYPES.EFFECTIVE_CONTEXT]: function EffectiveContextPanelComponent(props) {
            return (
              <PanelActionFrame
                onClose={function onClose() {
                  handleClosePanel("effective-context", props.api);
                }}
                onMinimize={function onMinimize() {
                  handleMinimizePanel(
                    normalizePanelDescriptor({
                      id: "effective-context",
                      title: props.api.title,
                      componentType: COMPONENT_TYPES.EFFECTIVE_CONTEXT,
                      componentState: {},
                    }),
                    props.api,
                  );
                }}
              >
                <LegacyPanelBridge
                  namespace="faithEffectiveContextPanel"
                  panelId="effective-context"
                  params={{}}
                />
              </PanelActionFrame>
            );
          },
          [COMPONENT_TYPES.TOKEN_USAGE]: function TokenUsagePanelComponent(props) {
            return (
              <PanelActionFrame
                onClose={function onClose() {
                  handleClosePanel("token-usage", props.api);
                }}
                onMinimize={function onMinimize() {
                  handleMinimizePanel(
                    normalizePanelDescriptor({
                      id: "token-usage",
                      title: props.api.title,
                      componentType: COMPONENT_TYPES.TOKEN_USAGE,
                      componentState: {},
                    }),
                    props.api,
                  );
                }}
              >
                <LegacyPanelBridge
                  namespace="faithTokenUsagePanel"
                  panelId="token-usage"
                  params={{}}
                />
              </PanelActionFrame>
            );
          },
          [COMPONENT_TYPES.APPROVAL_HISTORY]: function ApprovalHistoryPanelComponent(props) {
            return (
              <PanelActionFrame
                onClose={function onClose() {
                  handleClosePanel("approval-history", props.api);
                }}
                onMinimize={function onMinimize() {
                  handleMinimizePanel(
                    normalizePanelDescriptor({
                      id: "approval-history",
                      title: props.api.title,
                      componentType: COMPONENT_TYPES.APPROVAL_HISTORY,
                      componentState: {},
                    }),
                    props.api,
                  );
                }}
              >
                <LegacyPanelBridge
                  namespace="faithApprovalHistoryPanel"
                  panelId="approval-history"
                  params={{}}
                />
              </PanelActionFrame>
            );
          },
          [COMPONENT_TYPES.PA_SYSTEM_PROMPT]: function PaSystemPromptPanelComponent(props) {
            return (
              <PanelActionFrame
                onClose={function onClose() {
                  handleClosePanel("pa-system-prompt", props.api);
                }}
                onMinimize={function onMinimize() {
                  handleMinimizePanel(
                    normalizePanelDescriptor({
                      id: "pa-system-prompt",
                      title: props.api.title,
                      componentType: COMPONENT_TYPES.PA_SYSTEM_PROMPT,
                      componentState: {},
                    }),
                    props.api,
                  );
                }}
              >
                <LegacyPanelBridge
                  namespace="faithPaSystemPromptPanel"
                  panelId="pa-system-prompt"
                  params={{}}
                />
              </PanelActionFrame>
            );
          },
          [COMPONENT_TYPES.USER_SETTINGS]: function UserSettingsPanelComponent(props) {
            return (
              <PanelActionFrame
                onClose={function onClose() {
                  handleClosePanel("user-settings", props.api);
                }}
                onMinimize={function onMinimize() {
                  handleMinimizePanel(
                    normalizePanelDescriptor({
                      id: "user-settings",
                      title: props.api.title,
                      componentType: COMPONENT_TYPES.USER_SETTINGS,
                      componentState: {},
                    }),
                    props.api,
                  );
                }}
              >
                <LegacyPanelBridge
                  namespace="faithUserSettingsPanel"
                  panelId="user-settings"
                  params={{}}
                />
              </PanelActionFrame>
            );
          },
          [COMPONENT_TYPES.MODEL_SETTINGS]: function ModelSettingsPanelComponent(props) {
            return (
              <PanelActionFrame
                onClose={function onClose() {
                  handleClosePanel("model-settings", props.api);
                }}
                onMinimize={function onMinimize() {
                  handleMinimizePanel(
                    normalizePanelDescriptor({
                      id: "model-settings",
                      title: props.api.title,
                      componentType: COMPONENT_TYPES.MODEL_SETTINGS,
                      componentState: {},
                    }),
                    props.api,
                  );
                }}
              >
                <LegacyPanelBridge
                  namespace="faithModelSettingsPanel"
                  panelId="model-settings"
                  params={{}}
                />
              </PanelActionFrame>
            );
          },
          [COMPONENT_TYPES.TOOL]: function ToolPanelComponent(props) {
            return (
              <PanelActionFrame
                onClose={function onClose() {
                  handleClosePanel(
                    buildPanelId(COMPONENT_TYPES.TOOL, props.params || {}, props.params.toolId || "tool"),
                    props.api,
                  );
                }}
                onMinimize={function onMinimize() {
                  handleMinimizePanel(
                    normalizePanelDescriptor({
                      id: buildPanelId(
                        COMPONENT_TYPES.TOOL,
                        props.params || {},
                        props.params.toolId || "tool",
                      ),
                      title: props.api.title,
                      componentType: COMPONENT_TYPES.TOOL,
                      componentState: props.params || {},
                    }),
                    props.api,
                  );
                }}
              >
                <ToolPlaceholderPanel {...props} />
              </PanelActionFrame>
            );
          },
        }}
        getTabContextMenuItems={function getTabContextMenuItems() {
          return [];
        }}
        rightHeaderActionsComponent={function renderHeaderActions(props) {
          return (
            <DockviewHeaderActions
              {...props}
              onClose={handleClosePanel}
              onMinimize={handleMinimizePanel}
            />
          );
        }}
        watermarkComponent={WorkspaceWatermark}
        onReady={function onDockviewReady(event) {
          setDockviewApi(event.api);
        }}
      />
      <MinimizedPanelTray
        handleRestoreMinimizedPanel={handleRestoreMinimizedPanel}
        minimizedPanels={minimizedPanels}
      />
    </div>
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
