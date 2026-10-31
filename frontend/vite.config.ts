import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

const backend = process.env.BACKEND_URL ?? 'http://127.0.0.1:8010'
const proxy = { '/api': { target: backend, changeOrigin: false } }

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
  build: { target: 'es2022', sourcemap: false },
  test: { include: ['tests/unit/**/*.test.ts'], environment: 'node' },
})
