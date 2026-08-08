const { contextBridge } = require('electron')

// The renderer needs nothing privileged — it speaks HTTP/WebSocket to the local
// backend like any browser would. Exposing only inert metadata keeps the
// isolation boundary meaningful.
contextBridge.exposeInMainWorld('meridian', {
  platform: process.platform,
  isDesktop: true,
  version: process.env.npm_package_version ?? '1.0.0',
})
