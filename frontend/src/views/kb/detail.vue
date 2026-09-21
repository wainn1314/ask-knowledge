<template>
  <div class="col">
    <div class="row" style="justify-content: space-between">
      <div>
        <h2 style="margin: 0">{{ kb?.name || '加载中…' }}</h2>
        <p class="muted">{{ kb?.description || '（无描述）' }}</p>
      </div>
      <div class="row">
        <router-link :to="`/kb/${kbId}/chat`"><button class="primary">开始问答</button></router-link>
        <router-link :to="`/kb/${kbId}/quiz`"><button>生成题目</button></router-link>
      </div>
    </div>

    <div v-if="error" class="alert">{{ error }}</div>

    <div class="card col">
      <h3 style="margin: 0">上传资料</h3>
      <Uploader :kb-id="kbId" @uploaded="onUploaded" />
    </div>

    <div class="card">
      <div class="row" style="justify-content: space-between">
        <h3 style="margin: 0">文档（{{ total }}）</h3>
        <div class="row">
          <select v-model="statusFilter" style="width: 140px" @change="loadDocuments">
            <option value="">全部状态</option>
            <option value="pending">排队中</option>
            <option value="parsing">解析中</option>
            <option value="auditing">审核中</option>
            <option value="indexing">入库中</option>
            <option value="ready">可问答</option>
            <option value="failed">失败</option>
            <option value="blocked">已拦截</option>
          </select>
          <button @click="loadDocuments">刷新</button>
        </div>
      </div>

      <div v-if="!documents.length" style="margin-top: 12px">
        <EmptyState text="还没有文档，先上传 Markdown / PDF / Word 资料" />
      </div>

      <table v-else style="margin-top: 12px">
        <thead>
          <tr>
            <th>文件名</th>
            <th>大小</th>
            <th>状态</th>
            <th>审核</th>
            <th>分块</th>
            <th>解析</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="doc in documents" :key="doc.id">
            <td>
              {{ doc.filename }}
              <div v-if="doc.error_message" class="muted">{{ doc.error_message }}</div>
            </td>
            <td class="muted">{{ formatSize(doc.file_size) }}</td>
            <td><StatusTag :status="doc.status" :progress="doc.stage_progress" /></td>
            <td><AuditBadge :status="doc.audit_status" /></td>
            <td class="muted">{{ doc.chunk_count ?? '-' }}</td>
            <td class="muted">{{ parseModeLabel(doc.parse_mode) }}</td>
            <td>
              <div class="row wrap">
                <button @click="openPreview(doc)">预览</button>
                <button @click="openChunks(doc)">分块</button>
                <button @click="retry(doc)">重试</button>
                <button @click="remove(doc)">删除</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="muted" v-if="polling">处理中的文档每 2 秒自动刷新一次…</p>
    </div>

    <div class="card col">
      <h3 style="margin: 0">检索测试</h3>
      <div class="row">
        <input v-model="testQuery" placeholder="输入一个问题，看看能召回哪些片段" @keyup.enter="runTest" />
        <button class="primary" @click="runTest">测试</button>
      </div>
      <div v-for="(record, index) in testRecords" :key="index" class="record">
        <div class="row" style="justify-content: space-between">
          <span class="tag info">#{{ index + 1 }} 相似度 {{ record.score.toFixed(4) }}</span>
          <span class="muted">{{ record.document_name }} · 第 {{ record.position }} 段</span>
        </div>
        <pre class="markdown">{{ record.content }}</pre>
      </div>
    </div>

    <div class="card col">
      <h3 style="margin: 0">成员与权限</h3>
      <div class="row">
        <input v-model="memberUserId" placeholder="用户 ID（UUID）" />
        <select v-model="memberRole" style="width: 140px">
          <option value="viewer">viewer（仅问答）</option>
          <option value="editor">editor（可上传）</option>
          <option value="owner">owner（可管理）</option>
        </select>
        <button :disabled="!memberUserId" @click="addMember">添加成员</button>
      </div>
      <table>
        <thead>
          <tr><th>用户</th><th>角色</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="member in members" :key="member.id">
            <td>
              {{ member.user?.nickname || member.user?.email }}
              <div class="muted">{{ member.user_id }}</div>
            </td>
            <td><span class="tag info">{{ member.role }}</span></td>
            <td>
              <button :disabled="member.user_id === kb?.owner_id" @click="removeMember(member.user_id)">
                移除
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="panel" class="modal" @click.self="panel = ''">
      <div class="modal-body card col">
        <div class="row" style="justify-content: space-between">
          <h3 style="margin: 0">{{ panelTitle }}</h3>
          <button @click="panel = ''">关闭</button>
        </div>
        <p v-if="panel === 'chunks'" class="muted">共 {{ panelTotal }} 个分块（真相源为 Dify）</p>
        <p v-else class="muted">
          共 {{ panelTotal }} 字符 · 解析模式 {{ panelMode || '-' }} ·
          {{ previewTruncated ? '已截断显示' : '完整显示' }}
        </p>
        <pre class="markdown">{{ panelContent }}</pre>
      </div>
    </div>
  </div>
</template>


<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { kbApi } from '@/api/kb'
import { documentApi } from '@/api/document'
import AuditBadge from '@/components/AuditBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import StatusTag from '@/components/StatusTag.vue'
import Uploader from '@/components/Uploader.vue'
import { formatSize, parseModeLabel } from '@/utils/format'
import type { DocumentItem, Kb, KbMember, RetrievalRecord, UploadResult } from '@/types/api'

const route = useRoute()
const kbId = String(route.params.kbId)

const kb = ref<Kb | null>(null)
const documents = ref<DocumentItem[]>([])
const total = ref(0)
const members = ref<KbMember[]>([])
const statusFilter = ref('')
const error = ref('')
const polling = ref(false)

const testQuery = ref('')
const testRecords = ref<RetrievalRecord[]>([])

const memberUserId = ref('')
const memberRole = ref('viewer')

const panel = ref<'' | 'preview' | 'chunks'>('')
const panelContent = ref('')
const panelTotal = ref(0)
const panelMode = ref<string | null>(null)
const previewTruncated = ref(false)

const panelTitle = computed(() => (panel.value === 'chunks' ? '分块查看' : '解析结果预览'))

let timer: number | undefined

async function loadKb() {
  try {
    kb.value = await kbApi.detail(kbId)
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function loadDocuments() {
  try {
    const page = await documentApi.list(kbId, {
      status: statusFilter.value || undefined,
      page_size: 50
    })
    documents.value = page.items
    total.value = page.total
    const busy = page.items.some((doc) =>
      ['pending', 'parsing', 'auditing', 'indexing'].includes(doc.status)
    )
    polling.value = busy
    if (busy) startPolling()
    else stopPolling()
  } catch (err) {
    error.value = (err as Error).message
  }
}

function startPolling() {
  if (timer !== undefined) return
  // 5s 一次：文档状态查询会打到 Dify 的「知识库」接口，而 Dify 对它有每分钟上限
  // （超限返回 403 —— 用户点「出题」时就会看到「Dify 返回 403」）。后端另有缓存兜底。
  timer = window.setInterval(() => void loadDocuments(), 5000)
}

function stopPolling() {
  if (timer !== undefined) {
    window.clearInterval(timer)
    timer = undefined
  }
}

async function loadMembers() {
  try {
    members.value = (await kbApi.members(kbId)).items
  } catch {
    members.value = []
  }
}

function onUploaded(result: UploadResult) {
  if (result.accepted.length) {
    startPolling()
    void loadDocuments()
  }
}

async function retry(doc: DocumentItem) {
  try {
    await documentApi.retry(doc.id)
    startPolling()
    await loadDocuments()
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function remove(doc: DocumentItem) {
  if (!window.confirm(`确认删除「${doc.filename}」？`)) return
  try {
    await documentApi.remove(doc.id)
    await loadDocuments()
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function openPreview(doc: DocumentItem) {
  try {
    const data = await documentApi.preview(doc.id)
    panel.value = 'preview'
    panelContent.value = data.content || '（暂无内容）'
    panelTotal.value = data.total_chars
    panelMode.value = data.mode ?? null
    previewTruncated.value = data.truncated
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function openChunks(doc: DocumentItem) {
  try {
    const data = await documentApi.chunks(doc.id, 1, 20)
    panel.value = 'chunks'
    panelTotal.value = data.total
    panelContent.value = data.items
      .map((item) => `【#${item.position} · ${item.chars} 字】\n${item.content}`)
      .join('\n\n────────\n\n')
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function runTest() {
  if (!testQuery.value.trim()) return
  error.value = ''
  try {
    const data = await kbApi.retrievalTest(kbId, testQuery.value, kb.value?.top_k)
    testRecords.value = data.records
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function addMember() {
  error.value = ''
  try {
    await kbApi.addMember(kbId, memberUserId.value, memberRole.value)
    memberUserId.value = ''
    await loadMembers()
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function removeMember(userId: string) {
  if (!window.confirm('确认移除该成员？')) return
  try {
    await kbApi.removeMember(kbId, userId)
    await loadMembers()
  } catch (err) {
    error.value = (err as Error).message
  }
}

onMounted(async () => {
  await Promise.all([loadKb(), loadDocuments(), loadMembers()])
})

onBeforeUnmount(stopPolling)
</script>

<style scoped>
.record {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
  margin-top: 8px;
}
.modal {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  z-index: 20;
}
.modal-body {
  max-width: 760px;
  width: 100%;
  max-height: 80vh;
  overflow: auto;
}
</style>
