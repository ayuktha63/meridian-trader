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
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/ws': { target: 'ws://localhost:8000', ws: true },
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
