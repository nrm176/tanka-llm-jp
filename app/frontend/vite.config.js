import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// /api を FastAPI にプロキシして CORS を回避。
// ローカル実行: BACKEND_URL 未設定 → http://localhost:8001
// Docker 内: docker-compose で BACKEND_URL=http://backend:8001 を渡す
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:8001'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5178,
    host: true, // Docker から外向けに公開するため
    proxy: {
      '/api': {
        target: BACKEND_URL,
        changeOrigin: true,
      },
    },
  },
})
