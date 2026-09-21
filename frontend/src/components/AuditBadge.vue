<template>
  <span class="tag" :class="tone" :title="title">{{ text }}</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ status: string }>()

const MAP: Record<string, { text: string; tone: string; title: string }> = {
  pass: { text: '审核通过', tone: 'ok', title: '内容审核通过' },
  review: { text: '待复核', tone: 'warn', title: '命中风险词，已记录待人工复核' },
  block: { text: '已拦截', tone: 'danger', title: '内容审核未通过' },
  error: { text: '审核异常', tone: 'danger', title: '审核服务异常，请检查配置' },
  pending: { text: '待审核', tone: '', title: '等待审核' }
}

const entry = computed(() => MAP[props.status] ?? { text: props.status || '未知', tone: '', title: '' })
const text = computed(() => entry.value.text)
const tone = computed(() => entry.value.tone)
const title = computed(() => entry.value.title)
</script>
