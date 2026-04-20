(function initialiseFaithVueFallback(globalScope) {
  if (globalScope.Vue) {
    return;
  }

  globalScope.Vue = {
    createApp: function createApp() {
      return {
        mount: function mount() {
          return null;
        },
      };
    },
  };
})(window);
