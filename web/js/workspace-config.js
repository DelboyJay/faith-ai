/**
 * Description:
 *   Define the shared FAITH workspace descriptor consumed by the current browser
 *   runtime and the future Dockview shell.
 *
 * Requirements:
 *   - Keep the default workspace arrangement in one canonical place.
 *   - Preserve stable panel identities and panel metadata needed by the
 *     current runtime.
 *   - Expose a browser-global API that can be loaded before `layout.js`.
 */

(function initialiseFaithWorkspaceConfig(globalScope) {
  /**
   * Description:
   *   Return the canonical default workspace descriptor for first browser load.
   *
   * Requirements:
   *   - Keep the Project Agent and System Status in the upper tab group.
   *   - Keep Input and User Settings in one lower-left tab group.
   *   - Keep Approvals to the right of that lower-left tab group.
   *   - Preserve the Project Agent model metadata used by the current panel.
   *
   * @returns {object} Shared default workspace descriptor.
   */
  function buildDefaultWorkspaceDescriptor() {
    return {
      version: "v1",
      upperGroup: {
        panels: [
          {
            id: "project-agent",
            componentType: "agent-panel",
            title: "Project Agent",
            componentState: {
              agentId: "project-agent",
              displayName: "Project Agent",
              model: "ollama/llama3:8b",
            },
          },
          {
            id: "system-status",
            componentType: "status-panel",
            title: "System Status",
            componentState: {},
          },
        ],
      },
      lowerGroup: {
        panels: [
          {
            id: "input",
            componentType: "input-panel",
            title: "Input",
            size: 34,
            stackedPanels: [
              {
                id: "user-settings",
                componentType: "user-settings-panel",
                title: "User Settings",
                componentState: {},
              },
            ],
            componentState: {},
          },
          {
            id: "approvals",
            componentType: "approval-panel",
            title: "Approvals",
            size: 66,
            componentState: {},
          },
        ],
      },
    };
  }

  globalScope.faithWorkspaceConfig = {
    buildDefaultWorkspaceDescriptor: buildDefaultWorkspaceDescriptor,
  };
})(window);
