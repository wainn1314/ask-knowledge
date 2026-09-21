<template>
  <div class="col">
    <div class="row" style="justify-content: space-between">
      <h2 style="margin: 0">错题本</h2>
      <div class="row">
        <select v-model="statusFilter" style="width: 140px" @change="load">
          <option value="">全部</option>
          <option value="unmastered">未掌握</option>
          <option value="reviewing">复习中</option>
          <option value="mastered">已掌握</option>
        </select>
        <button @click="load">刷新</button>
      </div>
    </div>

    <div v-if="error" class="alert">{{ error }}</div>
    <div v-if="!items.length" class="card"><EmptyState text="还没有错题，去「生成题目」练一练吧" /></div>

    <div v-for="item in items" :key="item.id" class="card col">
      <div class="row" style="justify-content: space-between">
        <strong>{{ item.stem }}</strong>
        <div class="row">
          <span class="tag info">{{ QUESTION_TYPES[item.type] || item.type }}</span>
          <span class="tag" :class="item.status === 'mastered' ? 'ok' : 'warn'">
            {{ WRONG_STATUS[item.status] || item.status }}
          </span>
          <span class="tag">错 {{ item.wrong_count }} 次</span>
        </div>
      </div>
      <div class="muted">{{ item.kb_name }} · 最近错于 {{ formatTime(item.last_wrong_at) }}</div>

      <div v-if="detailMap[item.id]" class="col">
        <div v-if="detailMap[item.id].options?.length" class="col">
          <div v-for="option in detailMap[item.id].options ?? []" :key="option.key" class="muted">
            {{ option.key }}. {{ option.text }}
          </div>
        </div>
        <div class="muted">正确答案：{{ answerText(detailMap[item.id].answer) }}</div>
        <div class="muted">上次作答：{{ answerText(detailMap[item.id].last_user_answer) }}</div>
        <div v-if="detailMap[item.id].last_feedback" class="muted">
          点评：{{ detailMap[item.id].last_feedback }}
        </div>
        <div v-if="detailMap[item.id].analysis" class="muted">
          解析：{{ detailMap[item.id].analysis }}
        </div>
      </div>

      <div class="row wrap">
        <button @click="toggle(item.id)">
          {{ detailMap[item.id] ? '收起答案' : '查看答案' }}
        </button>
        <button @click="setStatus(item.id, 'reviewing')">标记复习中</button>
        <button class="primary" @click="setStatus(item.id, 'mastered')">标记已掌握</button>
        <button @click="remove(item.id)">删除</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { wrongBookApi } from '@/api/wrongBook'
import EmptyState from '@/components/EmptyState.vue'
import { QUESTION_TYPES, WRONG_STATUS, answerText, formatTime } from '@/utils/format'
import type { WrongBookDetail, WrongBookItem } from '@/types/api'

const items = ref<WrongBookItem[]>([])
const detailMap = reactive<Record<string, WrongBookDetail>>({})
const statusFilter = ref('')
const error = ref('')

async function load() {
  error.value = ''
  try {
    const page = await wrongBookApi.list({ status: statusFilter.value || undefined, page_size: 50 })
    items.value = page.items
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function toggle(id: string) {
  if (detailMap[id]) {
    delete detailMap[id]
    return
  }
  try {
    detailMap[id] = await wrongBookApi.detail(id)
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function setStatus(id: string, status: string) {
  try {
    await wrongBookApi.update(id, { status })
    await load()
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function remove(id: string) {
  if (!window.confirm('确认从错题本移除？')) return
  try {
    await wrongBookApi.remove(id)
    await load()
  } catch (err) {
    error.value = (err as Error).message
  }
}

onMounted(load)
</script>
