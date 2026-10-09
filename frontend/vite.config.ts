import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

export default defineConfig({
  plugins: [react()],
  // Relative base so the built bundle also loads from file:// inside Electron.
  base: './',
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    port: 5173,
    // Fall forward to the next free port rather than refusing to start when
    // another project is already on 5173.
    strictPort: false,
    proxy: {
      // xfwd: tell the backend who the browser really is. Without a token it
      // serves this machine only, and `vite --host` must not launder a LAN
      // client into a local one.
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true, xfwd: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true, xfwd: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks: {
          charts: ['lightweight-charts'],
          canvas: ['konva', 'react-konva'],
          motion: ['framer-motion'],
        },
      },
    },
  },
})
