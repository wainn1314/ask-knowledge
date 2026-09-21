<template>
  <div class="col">
    <div class="row" style="justify-content: space-between">
      <h2 style="margin: 0">历史会话</h2>
      <div class="row">
        <input v-model="keyword" placeholder="按标题搜索" style="width: 180px" @keyup.enter="load" />
        <button @click="load">刷新</button>
      </div>
    </div>

    <div v-if="error" class="alert">{{ error }}</div>

    <div class="card">
      <div v-if="!items.length"><EmptyState text="还没有会话记录" /></div>
      <table v-else>
        <thead>
          <tr><th>标题</th><th>知识库</th><th>条数</th><th>最后活跃</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="conv in items" :key="conv.id">
            <td>{{ conv.title || '未命名会话' }}</td>
            <td class="muted">{{ conv.kb_name }}</td>
            <td>{{ conv.message_count }}</td>
            <td class="muted">{{ formatTime(conv.last_message_at) }}</td>
            <td>
              <div class="row">
                <router-link :to="`/kb/${conv.kb_id}/chat`"><button>继续追问</button></router-link>
                <button @click="rename(conv.id, conv.title || '')">重命名</button>
                <button @click="remove(conv.id)">删除</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { chatApi } from '@/api/chat'
import EmptyState from '@/components/EmptyState.vue'
import { formatTime } from '@/utils/format'
import type { Conversation } from '@/types/api'

const items = ref<Conversation[]>([])
const keyword = ref('')
const error = ref('')

async function load() {
  error.value = ''
  try {
    const page = await chatApi.conversations({ keyword: keyword.value || undefined, page_size: 50 })
    items.value = page.items
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function rename(id: string, current: string) {
  const title = window.prompt('新的会话标题', current)
  if (!title) return
  try {
    await chatApi.rename(id, title)
    await load()
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function remove(id: string) {
  if (!window.confirm('确认删除该会话（同时删除 Dify 侧会话）？')) return
  try {
    await chatApi.remove(id)
    await load()
  } catch (err) {
    error.value = (err as Error).message
  }
}

onMounted(load)
</script>
