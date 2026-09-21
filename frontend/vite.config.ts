import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后端地址可由环境变量覆盖：scripts/dev.ps1 用 -BackendPort 时会自动注入 BACKEND_URL
const backendUrl = process.env.BACKEND_URL ?? 'http://127.0.0.1:8000'
// ask ai 阶段五后端（FastAPI）：/api/knowledge/* 知识库文档管理走这里
const askAiUrl = process.env.ASK_AI_URL ?? 'http://127.0.0.1:8080'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) }
  },
  server: {
    port: Number(process.env.VITE_PORT ?? 5173),
    strictPort: true,
    proxy: {
      // 开发环境直连后端：避免 CORS，且 SSE 需要 proxy_buffering off（Nginx 见 deploy/nginx.conf）
      // 更具体的路径写在前面（Vite 按声明顺序匹配）：知识库文档管理 → ask ai 阶段五后端
      '/api/knowledge': { target: askAiUrl, changeOrigin: true },
      // 健康检查在主后端根部（/health 无 /api 前缀）：顶栏状态栏依赖它，漏了会误报「后端不可达」
      '/health': { target: backendUrl, changeOrigin: true },
      '/api': { target: backendUrl, changeOrigin: true }
    }
  }
})

