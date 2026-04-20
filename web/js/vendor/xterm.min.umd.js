(function initialiseFaithXtermFallback(globalScope) {
  if (globalScope.Terminal) {
    return;
  }

  function Terminal() {}

  Terminal.prototype.open = function open() {};
  Terminal.prototype.write = function write() {};
  Terminal.prototype.dispose = function dispose() {};

  globalScope.Terminal = Terminal;
})(window);
