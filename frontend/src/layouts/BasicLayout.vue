<template>
  <div class="layout">
    <header class="topbar">
      <div class="row">
        <strong>Ask Knowledge</strong>
        <span class="muted">AI 学习伙伴 · 知识库问答与自测</span>
      </div>
      <nav class="row">
        <router-link to="/kb">知识库</router-link>
        <router-link to="/knowledge">知识库管理</router-link>
        <router-link to="/wrong-book">错题本</router-link>
        <router-link to="/history">历史会话</router-link>
      </nav>
      <div class="row">
        <span class="muted" :title="healthText">{{ healthText }}</span>
        <span class="muted">{{ displayName() }}</span>
        <button @click="logout">退出</button>
      </div>
    </header>

    <main class="content">
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { health } from '@/api/http'
import { clearAuth, displayName } from '@/stores/auth'

const router = useRouter()
const healthText = ref('后端连接中…')

onMounted(async () => {
  try {
    const info = await health()
    const mode = info.dify_mode as string
    healthText.value = `后端 ${info.status} · Dify=${mode} · 解析=${info.parser_mode}`
  } catch {
    healthText.value = '后端不可达（请先启动 uvicorn app.main:app）'
  }
})

function logout() {
  clearAuth()
  void router.push({ name: 'login' })
}
</script>

<style scoped>
.layout {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 10px 20px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 5;
}
.topbar nav a {
  margin-right: 12px;
  color: var(--muted);
}
.topbar nav a.router-link-active {
  color: var(--primary);
  font-weight: 600;
}
.content {
  flex: 1;
  padding: 18px 20px 40px;
  max-width: 1180px;
  width: 100%;
  margin: 0 auto;
}
</style>
