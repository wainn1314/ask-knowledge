<template>
  <div class="col">
    <div
      class="dropzone"
      :class="{ active: dragging }"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onDrop"
      @click="input?.click()"
    >
      <p>点击或拖拽文件到此处上传（支持 <code>{{ accept }}</code>）</p>
      <p class="muted">单次最多 {{ maxBatch }} 个文件，单个不超过 {{ maxSizeMb }}MB</p>
      <input ref="input" type="file" multiple :accept="acceptAttr" hidden @change="onPick" />
    </div>

    <div v-if="selected.length" class="card">
      <div class="row wrap">
        <span class="muted">待上传 {{ selected.length }} 个：</span>
        <span v-for="file in selected" :key="file.name" class="tag">{{ file.name }}</span>
        <button class="primary" :disabled="uploading" @click="submit">
          {{ uploading ? '上传中…' : '开始上传' }}
        </button>
        <button :disabled="uploading" @click="selected = []">清空</button>
      </div>
    </div>

    <div v-if="result" class="col">
      <div v-if="result.accepted.length" class="alert ok">
        已受理 {{ result.accepted.length }} 个文件，正在后台解析/审核/入库（下方列表会自动刷新）
      </div>
      <div v-for="item in result.rejected" :key="item.filename" class="alert">
        {{ item.filename }}：{{ item.message }}（错误码 {{ item.code }}）
      </div>
    </div>
    <div v-if="error" class="alert">{{ error }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { documentApi } from '@/api/document'
import type { UploadResult } from '@/types/api'

const props = defineProps<{ kbId: string; accept?: string; maxBatch?: number; maxSizeMb?: number }>()
const emit = defineEmits<{ (e: 'uploaded', result: UploadResult): void }>()

const accept = computed(() => props.accept ?? 'pdf,doc,docx,ppt,pptx,md,txt,png,jpg,jpeg')
const maxBatch = computed(() => props.maxBatch ?? 10)
const maxSizeMb = computed(() => props.maxSizeMb ?? 15)
const acceptAttr = computed(() => accept.value.split(',').map((ext) => `.${ext.trim()}`).join(','))

const input = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
const uploading = ref(false)
const selected = ref<File[]>([])
const result = ref<UploadResult | null>(null)
const error = ref('')

function add(files: FileList | File[]) {
  const incoming = Array.from(files)
  if (incoming.length > maxBatch.value) {
    error.value = `单次最多上传 ${maxBatch.value} 个文件`
    return
  }
  error.value = ''
  selected.value = incoming
}

function onDrop(event: DragEvent) {
  dragging.value = false
  if (event.dataTransfer?.files?.length) add(event.dataTransfer.files)
}

function onPick(event: Event) {
  const target = event.target as HTMLInputElement
  if (target.files?.length) add(target.files)
  target.value = ''
}

async function submit() {
  if (!selected.value.length) return
  uploading.value = true
  result.value = null
  error.value = ''
  try {
    const uploaded = await documentApi.upload(props.kbId, selected.value)
    result.value = uploaded
    selected.value = []
    emit('uploaded', uploaded)
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    uploading.value = false
  }
}
</script>

<style scoped>
.dropzone {
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  padding: 22px;
  text-align: center;
  background: #fbfcfe;
  cursor: pointer;
}
.dropzone.active {
  border-color: var(--primary);
  background: var(--primary-weak);
}
.dropzone p {
  margin: 4px 0;
}
</style>
