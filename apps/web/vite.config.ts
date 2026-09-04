import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import { loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import { PRODUCTION_API_BASE } from './src/apiConfig.js'

function loadDeviceHttps(mode: string) {
  if (mode !== 'device') return undefined

  const env = loadEnv(mode, process.cwd(), 'ROCKY_DEV_')
  const certPath = env.ROCKY_DEV_HTTPS_CERT
  const keyPath = env.ROCKY_DEV_HTTPS_KEY
  if (!certPath || !keyPath) {
    throw new Error(
      'Device mode requires ROCKY_DEV_HTTPS_CERT and ROCKY_DEV_HTTPS_KEY.',
    )
  }

  return {
    cert: readFileSync(resolve(certPath)),
    key: readFileSync(resolve(keyPath)),
  }
}

function validateNativeApiBase(mode: string) {
  if (mode !== 'native' && mode !== 'native-release') return

  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const apiBase = env.VITE_API_BASE_URL?.trim()
  const release = mode === 'native-release' || env.VITE_ANDROID_RELEASE === 'true'
  if (!apiBase) {
    if (release) return
    throw new Error('Native builds require VITE_API_BASE_URL with an absolute Rocky backend URL.')
  }

  let url: URL
  try {
    url = new URL(apiBase)
  } catch {
    throw new Error('Native VITE_API_BASE_URL must be an absolute Rocky backend URL.')
  }

  if (release) {
    if (url.protocol !== 'https:') {
      throw new Error('Native release VITE_API_BASE_URL must use HTTPS.')
    }
    if (url.origin !== PRODUCTION_API_BASE || url.pathname.replace(/\/+$/, '') !== '') {
      throw new Error(`Native release VITE_API_BASE_URL must be ${PRODUCTION_API_BASE}.`)
    }
  } else if (url.protocol !== 'https:' && url.protocol !== 'http:') {
    throw new Error('Native VITE_API_BASE_URL must use http or https.')
  }
  if (url.origin === 'https://localhost' || url.origin === 'http://localhost') {
    throw new Error("Native VITE_API_BASE_URL must not use Capacitor's local localhost WebView origin.")
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  validateNativeApiBase(mode)
  const deviceMode = mode === 'device'
  const https = loadDeviceHttps(mode)
  const proxy = {
    '/api': {
      target: 'http://localhost:8000',
      changeOrigin: true,
      rewrite: (path: string) => path.replace(/^\/api/, ''),
    },
  }

  return {
    plugins: [
      react(),
      VitePWA({
        registerType: 'prompt',
        manifest: {
          name: 'Rocky',
          short_name: 'Rocky',
          description: 'Your personal intelligence OS.',
          theme_color: '#0e0f11',
          background_color: '#0e0f11',
          display: 'standalone',
          start_url: '/',
          scope: '/',
          icons: [
            { src: '/rocky-icon-192.png', sizes: '192x192', type: 'image/png' },
            { src: '/rocky-icon-512.png', sizes: '512x512', type: 'image/png' },
            { src: '/rocky-icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
          ],
        },
        workbox: {
          globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
          navigateFallback: '/index.html',
          navigateFallbackDenylist: [/^\/api(?:\/|$)/],
          cleanupOutdatedCaches: true,
        },
      }),
    ],
    server: {
      host: deviceMode ? '0.0.0.0' : undefined,
      strictPort: deviceMode,
      https,
      proxy,
    },
    preview: {
      host: deviceMode ? '0.0.0.0' : undefined,
      strictPort: deviceMode,
      https,
      proxy,
    },
  }
})
