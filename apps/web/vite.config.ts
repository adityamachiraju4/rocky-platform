import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
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
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
