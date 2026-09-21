<template>
  <div class="col">
    <div class="row" style="justify-content: space-between">
      <h2 style="margin: 0">成绩单</h2>
      <div class="row">
        <router-link to="/wrong-book"><button>去错题本</button></router-link>
        <router-link :to="`/quiz/${quizId}/take`"><button>重新作答</button></router-link>
      </div>
    </div>

    <div v-if="error" class="alert">{{ error }}</div>

    <div v-if="result" class="card row" style="justify-content: space-around">
      <div class="col" style="align-items: center">
        <strong style="font-size: 30px">{{ result.score }}</strong>
        <span class="muted">平均得分</span>
      </div>
      <div class="col" style="align-items: center">
        <strong style="font-size: 30px">{{ result.correct }}/{{ result.total }}</strong>
        <span class="muted">答对题数</span>
      </div>
      <div class="col" style="align-items: center">
        <strong style="font-size: 30px">{{ result.wrong_question_ids.length }}</strong>
        <span class="muted">已进入错题本</span>
      </div>
    </div>

    <div v-for="(item, index) in result?.items ?? []" :key="item.question_id" class="card col">
      <div class="row" style="justify-content: space-between">
        <strong>{{ index + 1 }}. {{ stemOf(item.question_id) }}</strong>
        <span class="tag" :class="item.is_correct ? 'ok' : 'danger'">
          {{ item.is_correct ? '正确' : '错误' }} · {{ item.score ?? 0 }} 分
        </span>
      </div>
      <div class="col">
        <div class="muted">你的作答：{{ answerText(item.user_answer) }}</div>
        <div class="muted">参考答案：{{ answerText(item.correct_answer) }}</div>
        <div v-if="item.feedback" class="muted">点评：{{ item.feedback }}</div>
        <div v-if="item.analysis" class="muted">解析：{{ item.analysis }}</div>
      </div>
      <div v-if="sourcesOf(item.question_id).length" class="row wrap">
        <span class="muted">依据：</span>
        <span v-for="source in sourcesOf(item.question_id)" :key="source.index" class="tag">
          [{{ source.index }}] {{ source.document_name }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { quizApi } from '@/api/quiz'
import { answerText } from '@/utils/format'
import type { Quiz, QuizResult, SourceItem } from '@/types/api'

const route = useRoute()
const quizId = String(route.params.quizId)

const result = ref<QuizResult | null>(null)
const quiz = ref<Quiz | null>(null)
const error = ref('')

function stemOf(questionId: string): string {
  return quiz.value?.questions.find((question) => question.id === questionId)?.stem ?? '题目'
}

function sourcesOf(questionId: string): SourceItem[] {
  return quiz.value?.questions.find((question) => question.id === questionId)?.sources ?? []
}

onMounted(async () => {
  try {
    const [resultData, quizData] = await Promise.all([
      quizApi.result(quizId),
      quizApi.detail(quizId, true)
    ])
    result.value = resultData
    quiz.value = quizData
  } catch (err) {
    error.value = (err as Error).message
  }
})
</script>
