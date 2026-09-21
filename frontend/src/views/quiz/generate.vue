<template>
  <div class="col">
    <h2 style="margin: 0">生成题目</h2>
    <p class="muted">
      题目由 Dify 出题工作流基于知识库检索结果生成：先检索（不足会提示），再由 LLM 出题并做结构化校验。
    </p>

    <div class="card col">
      <div class="row wrap">
        <label class="muted">题型</label>
        <label v-for="item in typeOptions" :key="item.value" class="row" style="width: auto">
          <input
            type="checkbox"
            :value="item.value"
            v-model="form.types"
            style="width: auto"
          />
          {{ item.label }}
        </label>
      </div>
      <div class="row wrap">
        <label class="muted">数量 <input v-model.number="form.count" type="number" min="1" max="30" style="width: 80px" /></label>
        <label class="muted">
          难度
          <select v-model="form.difficulty" style="width: 120px">
            <option value="easy">简单</option>
            <option value="medium">中等</option>
            <option value="hard">困难</option>
            <option value="mixed">混合</option>
          </select>
        </label>
        <label class="muted" style="flex: 1">
          主题（可空，默认覆盖知识库核心知识点）
          <input v-model="form.topic" placeholder="例如：二叉树遍历" />
        </label>
      </div>
      <div class="row">
        <button class="primary" :disabled="loading || !form.types.length" @click="generate">
          {{ loading ? '生成中（最长约 120s）…' : '开始生成' }}
        </button>
        <router-link :to="`/kb/${kbId}`"><button>返回知识库</button></router-link>
      </div>
      <div v-if="error" class="alert">{{ error }}</div>
    </div>

    <div class="card">
      <div class="row" style="justify-content: space-between">
        <strong>历史出题记录</strong>
        <button @click="loadHistory">刷新</button>
      </div>
      <div v-if="!history.length" class="muted" style="margin-top: 8px">还没有出题记录</div>
      <table v-else style="margin-top: 8px">
        <thead>
          <tr><th>标题</th><th>题数</th><th>状态</th><th>时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="quiz in history" :key="quiz.id">
            <td>{{ quiz.title }}</td>
            <td>{{ quiz.question_count }}</td>
            <td><span class="tag info">{{ quiz.status }}</span></td>
            <td class="muted">{{ formatTime(quiz.created_at) }}</td>
            <td>
              <div class="row">
                <router-link :to="`/quiz/${quiz.id}/take`"><button>答题</button></router-link>
                <router-link :to="`/quiz/${quiz.id}/result`"><button>成绩</button></router-link>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { quizApi } from '@/api/quiz'
import { formatTime } from '@/utils/format'
import type { QuizBrief } from '@/types/api'

const route = useRoute()
const router = useRouter()
const kbId = String(route.params.kbId)

const typeOptions = [
  { value: 'single', label: '单选' },
  { value: 'multi', label: '多选' },
  { value: 'judge', label: '判断' },
  { value: 'short', label: '简答' }
]

const form = reactive<{ types: string[]; count: number; difficulty: string; topic: string }>({
  types: ['single', 'judge'],
  count: 5,
  difficulty: 'medium',
  topic: ''
})

const loading = ref(false)
const error = ref('')
const history = ref<QuizBrief[]>([])

async function generate() {
  loading.value = true
  error.value = ''
  try {
    const quiz = await quizApi.create({
      kb_id: kbId,
      types: form.types,
      count: form.count,
      difficulty: form.difficulty,
      topic: form.topic || null
    })
    await router.push(`/quiz/${quiz.quiz_id}/take`)
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    loading.value = false
  }
}

async function loadHistory() {
  try {
    const page = await quizApi.list({ kb_id: kbId, page_size: 20 })
    history.value = page.items
  } catch (err) {
    error.value = (err as Error).message
  }
}

onMounted(loadHistory)
</script>
