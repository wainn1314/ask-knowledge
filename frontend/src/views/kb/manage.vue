<template>
  <div class="col">
    <div class="row" style="justify-content: space-between">
      <div>
        <h2 style="margin: 0">知识库文档管理</h2>
        <p class="muted" style="margin: 4px 0 0">
          上传 / 查询 / 删除都只作用于「所选知识库自己的 Dify 数据集」，各知识库互不影响。
        </p>
      </div>
      <div class="row">
        <select v-if="!singleKbMode" v-model="selectedId" style="min-width: 240px" @change="onSelect">
          <option value="">— 选择知识库 —</option>
          <option v-for="item in kbs" :key="item.id" :value="item.id">
            {{ item.name }}（{{ item.doc_count }} 篇）
          </option>
        </select>
        <span v-else-if="kb" class="tag ok">唯一知识库：{{ kb.name }}</span>
        <button :disabled="loadingKbs" @click="loadKbs">{{ loadingKbs ? '刷新中…' : '刷新列表' }}</button>
      </div>
    </div>

    <div v-if="error" class="alert">{{ error }}</div>
    <div v-if="notice" class="alert ok">{{ notice }}</div>
    <div v-if="singleKbMode" class="alert ok">
      当前为<b>单知识库模式</b>：不能新建知识库，所有上传的资料都会写入
      「{{ kb?.name || '唯一知识库' }}」，不会再在 Dify 里生成新的知识库。
    </div>

    <div v-if="!loadingKbs && !kbs.length && !singleKbMode" class="card">
      <EmptyState text="还没有知识库，请先在「知识库」页面新建一个" />
    </div>

    <div v-if="kbs.length && !kb && !singleKbMode" class="card">
      <EmptyState text="请选择要管理的知识库：每个知识库对应一个独立的 Dify 数据集" />
    </div>

    <div v-if="kb" class="col">
      <div class="card col">
        <div class="row" style="justify-content: space-between">
          <div>
            <h3 style="margin: 0">{{ kb.name }}</h3>
            <p class="muted" style="margin: 4px 0 0">{{ kb.description || '（无描述）' }}</p>
          </div>
          <div class="row">
            <router-link :to="`/kb/${kb.id}`"><button>库详情</button></router-link>
            <router-link :to="`/kb/${kb.id}/chat`"><button class="primary">开始问答</button></router-link>
            <router-link :to="`/kb/${kb.id}/quiz`"><button>生成题目</button></router-link>
          </div>
        </div>
        <div class="row wrap">
          <span class="tag info">我的角色：{{ kb.role || '-' }}</span>
          <span class="tag">{{ kb.visibility === 'tenant' ? '租户可见' : '私有' }}</span>
          <span class="tag" :class="kb.dify_dataset_id ? 'ok' : 'warn'">
            Dify 数据集：{{ kb.dify_dataset_id || '未创建' }}
          </span>
          <span class="tag">分块 {{ kb.chunk_size }}/{{ kb.chunk_overlap }}</span>
          <span class="tag">topK {{ kb.top_k }} · 阈值 {{ kb.score_threshold }} · {{ kb.retrieval_mode }}</span>
        </div>
      </div>

      <div class="card col">
        <h3 style="margin: 0">上传资料到「{{ kb.name }}」</h3>
        <Uploader :kb-id="kb.id" @uploaded="onUploaded" />
        <p class="muted" style="margin: 0">
          上传后走「解析 → 内容审核 → 切片入库」流水线，最终写入{{
            singleKbMode ? '唯一知识库（Dify 数据集 ' + (kb.dify_dataset_id || '-') + '）' : '本库自己的 Dify 数据集'
          }}。
        </p>
      </div>

      <div class="card">
        <div class="row" style="justify-content: space-between">
          <h3 style="margin: 0">文档（{{ total }}）</h3>
          <div class="row">
            <select v-model="statusFilter" style="width: 150px" @change="reloadDocuments">
              <option value="">全部状态</option>
              <option value="pending">排队中</option>
              <option value="parsing">解析中</option>
              <option value="auditing">审核中</option>
              <option value="indexing">入库中</option>
              <option value="ready">可问答</option>
              <option value="failed">失败</option>
              <option value="blocked">已拦截</option>
            </select>
            <input
              v-model="keyword"
              placeholder="按文件名搜索"
              style="width: 170px"
              @keyup.enter="reloadDocuments"
            />
            <button @click="reloadDocuments">刷新</button>
          </div>
        </div>

        <div v-if="!documents.length" style="margin-top: 12px">
          <EmptyState text="该知识库还没有文档，先上传 Markdown / PDF / Word 资料" />
        </div>

        <table v-else style="margin-top: 12px">
          <thead>
            <tr>
              <th>文件名</th>
              <th>大小</th>
              <th>状态</th>
              <th>审核</th>
              <th>分块</th>
              <th>上传时间</th>
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
              <td class="muted">{{ formatTime(doc.created_at) }}</td>
              <td>
                <div class="row wrap">
                  <button @click="openChunks(doc)">分块</button>
                  <button :disabled="retryingId === doc.id" @click="retry(doc)">
                    {{ retryingId === doc.id ? '重试中…' : '重试' }}
                  </button>
                  <button :disabled="removingId === doc.id" @click="removeDocument(doc)">
                    {{ removingId === doc.id ? '删除中…' : '删除' }}
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>

        <div class="row" style="justify-content: space-between; margin-top: 10px">
          <span class="muted" v-if="polling">处理中的文档每 2 秒自动刷新一次…</span>
          <span class="muted" v-else>共 {{ total }} 个文档</span>
          <div class="row">
            <span class="muted">第 {{ page }} / {{ totalPages }} 页</span>
            <button :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
            <button :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
          </div>
        </div>
      </div>

      <div v-if="chunkPanel" class="card col">
        <div class="row" style="justify-content: space-between">
          <h3 style="margin: 0">分块预览 · {{ chunkPanel.filename }}（{{ chunkTotal }} 段）</h3>
          <button @click="closeChunks">关闭</button>
        </div>
        <pre class="markdown">{{ chunkContent || '（暂无分块）' }}</pre>
      </div>

      <div class="card col">
        <h3 style="margin: 0">检索测试（只检索本知识库）</h3>
        <div class="row">
          <input v-model="testQuery" placeholder="输入一个问题，看能召回本库哪些片段" @keyup.enter="runTest" />
          <button class="primary" :disabled="testing" @click="runTest">
            {{ testing ? '检索中…' : '测试' }}
          </button>
        </div>
        <div v-for="(record, index) in testRecords" :key="index" class="record">
          <div class="row" style="justify-content: space-between">
            <span class="tag info">#{{ index + 1 }} 相似度 {{ record.score.toFixed(4) }}</span>
            <span class="muted">
              {{ record.document_name || '未知文档' }} · 第 {{ record.position ?? '-' }} 段
            </span>
          </div>
          <p style="margin: 8px 0 0; white-space: pre-wrap">{{ record.content }}</p>
        </div>
        <p v-if="tested && !testRecords.length" class="muted">
          本库没有召回到任何片段：确认文档状态为「可问答」，或调低本库的相似度阈值。
        </p>
      </div>

      <div class="card col">
        <div class="row" style="justify-content: space-between">
          <h3 style="margin: 0">直连 Dify 数据集（排障用）</h3>
          <button :disabled="!kb.dify_dataset_id || difyLoading" @click="loadDifyDocuments">
            {{ difyLoading ? '读取中…' : '读取 Dify 侧文档' }}
          </button>
        </div>
        <p class="muted" style="margin: 0">
          直接向 Dify 查询本库数据集（{{ kb.dify_dataset_id || '未创建，无法直连' }}）的文档，用来核对
          「本地流水线状态」与「Dify 侧实际入库结果」是否一致；日常增删请用上面的入口。
        </p>
        <div v-if="difyError" class="alert">{{ difyError }}</div>
        <table v-if="difyDocuments.length" style="margin-top: 8px">
          <thead>
            <tr>
              <th>Dify 文档名</th>
              <th>状态</th>
              <th>字数</th>
              <th>入库时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="doc in difyDocuments" :key="doc.id">
              <td>{{ doc.name }}</td>
              <td><span class="tag">{{ doc.status }}</span></td>
              <td class="muted">{{ doc.word_count }}</td>
              <td class="muted">{{ formatTime(doc.created_at) }}</td>
              <td><button :disabled="difyRemovingId === doc.id" @click="removeDifyDocument(doc)">
                {{ difyRemovingId === doc.id ? '删除中…' : '从 Dify 删除' }}
              </button></td>
            </tr>
          </tbody>
        </table>
        <p v-else-if="difyLoaded" class="muted">Dify 侧暂无文档。</p>
      </div>

    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { documentApi } from '@/api/document'
import { kbApi } from '@/api/kb'
import { knowledgeApi } from '@/api/knowledge'
import AuditBadge from '@/components/AuditBadge.vue'
import EmptyState from '@/components/EmptyState.vue'
import StatusTag from '@/components/StatusTag.vue'
import Uploader from '@/components/Uploader.vue'
import { formatSize, formatTime } from '@/utils/format'
import type { DocumentItem, Kb, KnowledgeDocument, RetrievalRecord, UploadResult } from '@/types/api'

/** `#/knowledge?kb=<id>`：只管理该知识库自己的文档（写入它自己的 Dify 数据集）。 */
const route = useRoute()
const router = useRouter()

const PAGE_SIZE = 10

const kbs = ref<Kb[]>([])
const selectedId = ref('')
const kb = ref<Kb | null>(null)
const loadingKbs = ref(false)
/** 单知识库模式：没有「选择知识库」这回事，所有上传都进唯一知识库（后端 /kbs/default 下发） */
const singleKbMode = ref(false)

const documents = ref<DocumentItem[]>([])
const total = ref(0)
const page = ref(1)
const statusFilter = ref('')
const keyword = ref('')
const removingId = ref('')
const retryingId = ref('')

const chunkPanel = ref<DocumentItem | null>(null)
const chunkContent = ref('')
const chunkTotal = ref(0)

const testQuery = ref('')
const testRecords = ref<RetrievalRecord[]>([])
const tested = ref(false)
const testing = ref(false)

const difyDocuments = ref<KnowledgeDocument[]>([])
const difyLoading = ref(false)
const difyLoaded = ref(false)
const difyError = ref('')
const difyRemovingId = ref('')

const error = ref('')
const notice = ref('')

const polling = ref(false)
let timer: number | undefined

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

async function loadKbs() {
  loadingKbs.value = true
  try {
    // 单知识库模式：列表里只有「唯一知识库」，不存在选择/新建的余地
    const defaultInfo = await kbApi.defaultKb()
    singleKbMode.value = defaultInfo.single_kb_mode
    if (defaultInfo.single_kb_mode) {
      kbs.value = defaultInfo.kb ? [defaultInfo.kb] : []
      return
    }
    kbs.value = (await kbApi.list({ page_size: 100 })).items
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    loadingKbs.value = false
  }
}

/** 切换所选知识库：拉详情，并重置文档列表 / 检索测试 / Dify 排障面板 */
async function applyKb(id: string, syncUrl = false) {
  if (!id) {
    selectedId.value = ''
    kb.value = null
    return
  }
  selectedId.value = id
  error.value = ''
  notice.value = ''
  resetDocuments()
  testQuery.value = ''
  testRecords.value = []
  tested.value = false
  resetDifyPanel()
  if (syncUrl && route.query.kb !== id) void router.replace({ query: { kb: id } })
  try {
    kb.value = await kbApi.detail(id)
  } catch (err) {
    kb.value = null
    error.value = (err as Error).message
    return
  }
  await loadDocuments()
}

function onSelect() {
  void applyKb(selectedId.value, true)
}
function resetDocuments() {
  documents.value = []
  total.value = 0
  page.value = 1
  statusFilter.value = ''
  keyword.value = ''
  stopPolling()
}

async function loadDocuments() {
  if (!kb.value) return
  try {
    const result = await documentApi.list(kb.value.id, {
      status: statusFilter.value || undefined,
      keyword: keyword.value || undefined,
      page: page.value,
      page_size: PAGE_SIZE
    })
    documents.value = result.items
    total.value = result.total
  } catch (err) {
    error.value = (err as Error).message
  }
  const busy = documents.value.some((doc) =>
    ['pending', 'parsing', 'auditing', 'indexing'].includes(doc.status)
  )
  if (busy) startPolling()
  else stopPolling()
}

function reloadDocuments() {
  page.value = 1
  void loadDocuments()
}

function goPage(target: number) {
  page.value = Math.min(Math.max(target, 1), totalPages.value)
  void loadDocuments()
}

function startPolling() {
  if (timer !== undefined) return
  polling.value = true
  // 5s 一次：文档状态查询会打到 Dify 的「知识库」接口，而 Dify 对它有每分钟上限
  // （超限返回 403 —— 出题/问答随后的检索就会失败）。后端另有缓存兜底。
  timer = window.setInterval(() => void loadDocuments(), 5000)
}

function stopPolling() {
  polling.value = false
  if (timer !== undefined) {
    window.clearInterval(timer)
    timer = undefined
  }
}

/** 上传成功后自动轮询进度，并刷新知识库概览里的文档数 */
function onUploaded(result: UploadResult) {
  if (!result.accepted.length) return
  notice.value = `已受理 ${result.accepted.length} 个文件，正在解析 / 审核 / 入库`
  startPolling()
  void loadDocuments()
  void refreshKb()
}

async function refreshKb() {
  if (!kb.value) return
  try {
    kb.value = await kbApi.detail(kb.value.id)
  } catch {
    // 概览刷新失败不影响文档操作
  }
}

async function retry(doc: DocumentItem) {
  retryingId.value = doc.id
  error.value = ''
  try {
    await documentApi.retry(doc.id)
    startPolling()
    await loadDocuments()
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    retryingId.value = ''
  }
}

async function removeDocument(doc: DocumentItem) {
  const tip =
    `确认从「${kb.value?.name ?? ''}」删除「${doc.filename}」？\n` +
    '会同时删除该文档在 Dify 数据集里的索引，删除后不可恢复。'
  if (!window.confirm(tip)) return
  removingId.value = doc.id
  error.value = ''
  try {
    await documentApi.remove(doc.id)
    notice.value = `已删除「${doc.filename}」`
    await loadDocuments()
    await refreshKb()
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    removingId.value = ''
  }
}
function closeChunks() {
  chunkPanel.value = null
  chunkContent.value = ''
  chunkTotal.value = 0
}

async function openChunks(doc: DocumentItem) {
  error.value = ''
  try {
    const data = await documentApi.chunks(doc.id, 1, 20)
    chunkPanel.value = doc
    chunkTotal.value = data.total
    chunkContent.value = data.items
      .map((item) => `【#${item.position} · ${item.chars} 字】\n${item.content}`)
      .join('\n\n────────\n\n')
  } catch (err) {
    error.value = (err as Error).message
  }
}

/** 只检索本知识库（后端用它自己的 dify_dataset_id 调 /retrieve） */
async function runTest() {
  if (!kb.value || !testQuery.value.trim()) return
  testing.value = true
  error.value = ''
  try {
    const data = await kbApi.retrievalTest(
      kb.value.id,
      testQuery.value,
      kb.value.top_k,
      kb.value.score_threshold
    )
    testRecords.value = data.records
    tested.value = true
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    testing.value = false
  }
}

function resetDifyPanel() {
  difyDocuments.value = []
  difyLoaded.value = false
  difyError.value = ''
}

/** 直连 Dify（经 ask ai 后端 :8080 代理）列出本库数据集里的文档，用于排障对账 */
async function loadDifyDocuments() {
  const datasetId = kb.value?.dify_dataset_id
  if (!datasetId) return
  difyLoading.value = true
  difyError.value = ''
  try {
    const data = await knowledgeApi.list({ page: 1, limit: 50, datasetId })
    difyDocuments.value = data.items
    difyLoaded.value = true
  } catch (err) {
    difyError.value = `读取 Dify 文档失败：${(err as Error).message}（该面板需要 ask ai 后端 :8080 在运行）`
  } finally {
    difyLoading.value = false
  }
}

async function removeDifyDocument(doc: KnowledgeDocument) {
  const datasetId = kb.value?.dify_dataset_id
  if (!datasetId) return
  if (!window.confirm(`确认从 Dify 数据集删除「${doc.name}」？本地流水线记录不会同步删除。`)) return
  difyRemovingId.value = doc.id
  difyError.value = ''
  try {
    await knowledgeApi.remove(doc.id, datasetId)
    await loadDifyDocuments()
  } catch (err) {
    difyError.value = (err as Error).message
  } finally {
    difyRemovingId.value = ''
  }
}
onMounted(async () => {
  await loadKbs()
  const queryId = typeof route.query.kb === 'string' ? route.query.kb : ''
  const target = queryId || kbs.value[0]?.id || ''
  if (target) await applyKb(target)
})

// 从「知识库 → 管理文档」带 ?kb=xxx 进来时同步选择（同路由复用组件，不会重新 onMounted）
watch(
  () => route.query.kb,
  (value) => {
    const id = typeof value === 'string' ? value : ''
    if (id === selectedId.value) return
    void applyKb(id)
  }
)

onBeforeUnmount(stopPolling)
</script>

<style scoped>
.record {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
  margin-top: 8px;
}
pre.markdown {
  max-height: 380px;
  overflow: auto;
}
</style>
