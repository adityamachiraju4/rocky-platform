import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import { loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

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

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
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
