import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  },
  server: {
    port: 5174,
    proxy: {
      '^/v1/workflow': {
        target: 'http://localhost:3800',
        changeOrigin: true
      },
      '^/v1/(calendars|holiday-sets|associations|engine)': {
        target: 'http://localhost:3700',
        changeOrigin: true
      },
      '^/v1': {
        target: 'http://localhost:3300',
        changeOrigin: true
      },
      '^/config': {
        target: 'http://localhost:3600',
        changeOrigin: true
      },
      '^/dashboard': {
        target: 'http://localhost:3500',
        changeOrigin: true
      },
      '^/sessions': {
        target: 'http://localhost:3500',
        changeOrigin: true
      },
      '^/supervisor': {
        target: 'http://localhost:3500',
        changeOrigin: true
      },
      '^/reports': {
        target: 'http://localhost:3500',
        changeOrigin: true
      },
      '^/webchat': {
        target: 'http://localhost:8010',
        changeOrigin: true
      }
    }
  }
})
