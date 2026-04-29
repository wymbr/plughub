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
      '^/auth': {
        target: 'http://localhost:3200',
        changeOrigin: true
      },
      '^/api': {
        target: 'http://localhost:3100',
        changeOrigin: true
      },
      '^/agent-ws': {
        target: 'ws://localhost:3100',
        changeOrigin: true,
        ws: true
      },
      '^/v1/workflow': {
        target: 'http://localhost:3800',
        changeOrigin: true
      },
      '^/v1/(calendars|holiday-sets|associations|engine)': {
        target: 'http://localhost:3700',
        changeOrigin: true
      },
      '^/v1/pricing': {
        target: 'http://localhost:3900',
        changeOrigin: true
      },
      '^/v1/evaluation': {
        target: 'http://localhost:3400',
        changeOrigin: true
      },
      '^/v1/knowledge': {
        target: 'http://localhost:3400',
        changeOrigin: true
      },
      '^/v1': {
        target: 'http://localhost:3300',
        changeOrigin: true
      },
      '^/config': {
        target: 'http://localhost:3600',
        changeOrigin: true,
        bypass(req) {
          // SPA routes under /config must be served by React Router, not the config-api.
          // Browser navigation sends Accept: text/html; API fetch calls do not.
          if (req.headers.accept?.includes('text/html')) return '/index.html'
        }
      },
      '^/dashboard': {
        target: 'http://localhost:3500',
        changeOrigin: true,
        bypass(req) {
          if (req.headers.accept?.includes('text/html')) return '/index.html'
        }
      },
      '^/sessions': {
        target: 'http://localhost:3500',
        changeOrigin: true,
        bypass(req) {
          if (req.headers.accept?.includes('text/html')) return '/index.html'
        }
      },
      '^/supervisor': {
        target: 'http://localhost:3500',
        changeOrigin: true,
        bypass(req) {
          if (req.headers.accept?.includes('text/html')) return '/index.html'
        }
      },
      '^/reports': {
        target: 'http://localhost:3500',
        changeOrigin: true,
        bypass(req) {
          if (req.headers.accept?.includes('text/html')) return '/index.html'
        }
      },
      '^/webchat': {
        target: 'http://localhost:8010',
        changeOrigin: true
      }
    }
  }
})
