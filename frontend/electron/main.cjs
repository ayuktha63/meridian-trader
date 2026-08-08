const { app, BrowserWindow, shell } = require('electron')
const path = require('node:path')

const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL

function createWindow() {
  const window = new BrowserWindow({
    width: 1680,
    height: 1000,
    minWidth: 1180,
    minHeight: 720,
    show: false,
    backgroundColor: '#05070C',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    trafficLightPosition: { x: 14, y: 16 },
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      // The renderer is a plain web app talking to localhost over HTTP; it has
      // no reason to touch Node, so keep the sandbox intact.
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })

  // Avoid a white flash before the dark UI paints.
  window.once('ready-to-show', () => window.show())

  window.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  if (DEV_SERVER_URL) {
    window.loadURL(DEV_SERVER_URL)
    window.webContents.openDevTools({ mode: 'detach' })
  } else {
    window.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  }
}

app.whenReady().then(() => {
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
