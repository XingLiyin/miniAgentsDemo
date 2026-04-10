import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '0.0.0.0',   // 👈 关键
    proxy: {
      '/api/v1/sessions': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        // SSE needs responses to not be buffered
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            // Ensure SSE responses flow through without buffering
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              proxyRes.headers['cache-control'] = 'no-cache'
            }
          })
        },
      },
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
