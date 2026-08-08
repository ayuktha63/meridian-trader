import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import { applyTheme, readStoredTheme } from './lib/theme'

// Set the theme before the first paint. Doing this in an effect would flash the
// dark palette for a frame on every load for a light-mode user.
applyTheme(readStoredTheme())

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
