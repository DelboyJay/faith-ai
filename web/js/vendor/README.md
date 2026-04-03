# Vendored Web UI Runtime Assets

This directory is reserved for offline fallback copies of the FAITH Web UI CDN
libraries.

Expected files:

- `goldenlayout.umd.js`
- `goldenlayout-base.css`
- `goldenlayout-dark-theme.css`
- `vue.global.prod.js`
- `xterm.min.js`
- `xterm.min.css`

FAITH-037 loads GoldenLayout from CDN first and falls back to these files when
they are present.
