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
      // Forward workflow API calls
      '/v1/workflow': {
        target: process.env.VITE_WORKFLOW_API_BASE_URL ?? 'http://localhost:3800',
        changeOrigin: true,
      },
      // Forward config API calls
      '/config': {
        target: process.env.VITE_CONFIG_API_BASE_URL ?? 'http://localhost:3600',
        changeOrigin: true,
      },
      // Forward supervisor intervention calls
      '/supervisor': {
        target: process.env.VITE_ANALYTICS_BASE_URL ?? 'http://localhost:3500',
        changeOrigin: true,
      },
      // Forward pricing API calls
      '/v1/pricing': {
        target: process.env.VITE_PRICING_API_BASE_URL ?? 'http://localhost:3900',
        changeOrigin: true,
      },
      // Forward agent-registry API calls (pools, agent-types, skills, instances)
      '/v1/pools': {
        target: process.env.VITE_REGISTRY_API_BASE_URL ?? 'http://localhost:3300',
        changeOrigin: true,
      },
      '/v1/agent-types': {
        target: process.env.VITE_REGISTRY_API_BASE_URL ?? 'http://localhost:3300',
        changeOrigin: true,
      },
      '/v1/skills': {
        target: process.env.VITE_REGISTRY_API_BASE_URL ?? 'http://localhost:3300',
        changeOrigin: true,
      },
      '/v1/instances': {
        target: process.env.VITE_REGISTRY_API_BASE_URL ?? 'http://localhost:3300',
        changeOrigin: true,
      },
      '/v1/channels': {
        target: process.env.VITE_REGISTRY_API_BASE_URL ?? 'http://localhost:3300',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
