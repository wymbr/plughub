import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Forward API calls to analytics-api in dev
      '/dashboard': { target: 'http://localhost:3500', changeOrigin: true },
      '/reports':   { target: 'http://localhost:3500', changeOrigin: true },
      '/admin':     { target: 'http://localhost:3500', changeOrigin: true },
      '/sessions':  { target: 'http://localhost:3500', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
