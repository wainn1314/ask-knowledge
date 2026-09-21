<template>
  <span class="tag" :class="tone">{{ label }}</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ status: string; progress?: number }>()

const MAP: Record<string, { label: string; tone: string }> = {
  pending: { label: '排队中', tone: '' },
  parsing: { label: '解析中', tone: 'info' },
  auditing: { label: '审核中', tone: 'info' },
  indexing: { label: '入库中', tone: 'info' },
  ready: { label: '可问答', tone: 'ok' },
  failed: { label: '失败', tone: 'danger' },
  blocked: { label: '已拦截', tone: 'danger' }
}

const entry = computed(() => MAP[props.status] ?? { label: props.status || '未知', tone: '' })
const label = computed(() =>
  props.progress !== undefined && props.progress > 0 && props.progress < 100
    ? `${entry.value.label} ${props.progress}%`
    : entry.value.label
)
const tone = computed(() => entry.value.tone)
</script>
