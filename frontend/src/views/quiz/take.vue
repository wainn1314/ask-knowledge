<template>
  <div class="col">
    <div class="row" style="justify-content: space-between">
      <h2 style="margin: 0">{{ quiz?.title || '答题' }}</h2>
      <span class="muted">共 {{ quiz?.questions.length ?? 0 }} 题 · 简答题由 LLM 判分（60 分及格）</span>
    </div>

    <div v-if="error" class="alert">{{ error }}</div>

    <div v-for="(question, index) in quiz?.questions ?? []" :key="question.id" class="card col">
      <div class="row" style="justify-content: space-between">
        <strong>
          {{ index + 1 }}. {{ question.stem }}
        </strong>
        <span class="tag info">{{ QUESTION_TYPES[question.type] || question.type }}</span>
      </div>

      <div v-if="question.type === 'single' || question.type === 'multi'" class="col">
        <label v-for="option in question.options ?? []" :key="option.key" class="row" style="width: auto">
          <input
            v-if="question.type === 'single'"
            type="radio"
            :name="question.id"
            :value="option.key"
            :checked="answers[question.id] === option.key"
            style="width: auto"
            @change="answers[question.id] = option.key"
          />
          <input
            v-else
            type="checkbox"
            :value="option.key"
            :checked="(answers[question.id] as string[])?.includes(option.key)"
            style="width: auto"
            @change="toggleMulti(question.id, option.key, ($event.target as HTMLInputElement).checked)"
          />
          <span><strong>{{ option.key }}.</strong> {{ option.text }}</span>
        </label>
      </div>

      <div v-else-if="question.type === 'judge'" class="row">
        <label class="row" style="width: auto">
          <input
            type="radio"
            :name="question.id"
            :checked="answers[question.id] === true"
            style="width: auto"
            @change="answers[question.id] = true"
          />
          正确
        </label>
        <label class="row" style="width: auto">
          <input
            type="radio"
            :name="question.id"
            :checked="answers[question.id] === false"
            style="width: auto"
            @change="answers[question.id] = false"
          />
          错误
        </label>
      </div>

      <textarea
        v-else
        rows="3"
        placeholder="请作答（建议写出关键要点）"
        :value="(answers[question.id] as string) || ''"
        @input="answers[question.id] = ($event.target as HTMLTextAreaElement).value"
      ></textarea>
    </div>

    <div class="row">
      <button class="primary" :disabled="submitting || !quiz?.questions.length" @click="submit">
        {{ submitting ? '判分中…' : '提交并判分' }}
      </button>
      <button @click="fillAll">一键填「不确定」（用于演示错题本）</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { quizApi } from '@/api/quiz'
import { QUESTION_TYPES } from '@/utils/format'
import type { AnswerValue, Quiz } from '@/types/api'

const route = useRoute()
const router = useRouter()
const quizId = String(route.params.quizId)

const quiz = ref<Quiz | null>(null)
const answers = reactive<Record<string, AnswerValue>>({})
const submitting = ref(false)
const error = ref('')

function toggleMulti(questionId: string, key: string, checked: boolean) {
  const current = new Set((answers[questionId] as string[]) || [])
  if (checked) current.add(key)
  else current.delete(key)
  answers[questionId] = [...current]
}

function fillAll() {
  quiz.value?.questions.forEach((question) => {
    answers[question.id] = question.type === 'judge' ? null : question.type === 'multi' ? [] : '不确定'
  })
}

async function submit() {
  if (!quiz.value) return
  submitting.value = true
  error.value = ''
  try {
    const payload = quiz.value.questions.map((question) => ({
      question_id: question.id,
      answer: answers[question.id] ?? null
    }))
    await quizApi.submit(quizId, payload)
    await router.push(`/quiz/${quizId}/result`)
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  try {
    quiz.value = await quizApi.detail(quizId, false)
  } catch (err) {
    error.value = (err as Error).message
  }
})
</script>
