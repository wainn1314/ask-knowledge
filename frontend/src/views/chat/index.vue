<template>
  <div class="chat-layout">
    <aside class="card side">
      <div class="row" style="justify-content: space-between">
        <strong>会话</strong>
        <button @click="newChat">新会话</button>
      </div>
      <div v-if="!conversations.length" class="muted" style="margin-top: 8px">暂无历史会话</div>
      <ul class="conv-list">
        <li
          v-for="conv in conversations"
          :key="conv.id"
          :class="{ active: conv.id === conversationId }"
          @click="openConversation(conv.id)"
        >
          <div class="title">{{ conv.title || '未命名会话' }}</div>
          <div class="muted">{{ conv.message_count }} 条 · {{ formatTime(conv.last_message_at) }}</div>
        </li>
      </ul>
      <p class="muted" v-if="currentMeta">
        Dify 会话：{{ currentMeta.dify_available === false ? '已失联（仅本地归档）' : '可继续追问' }}
      </p>
    </aside>

    <section class="card main">
      <div class="row" style="justify-content: space-between">
        <div>
          <strong>{{ kbName || '知识库问答' }}</strong>
          <span class="muted"> · 引用来源带 [n] 编号，点击右侧引用查看原文</span>
        </div>
        <router-link to="/kb"><button>返回知识库</button></router-link>
      </div>

      <div ref="listRef" class="messages">
        <div v-if="!messages.length" class="muted" style="padding: 12px">
          提问试试：例如「中序遍历的顺序是什么？」
        </div>
        <div v-for="message in messages" :key="message.id" class="bubble" :class="message.role">
          <div class="bubble-head">
            <span>{{ message.role === 'user' ? '我' : 'AI 学习伙伴' }}</span>
            <span v-if="message.role === 'assistant' && message.latency_ms !== undefined && message.latency_ms !== null" class="muted">
              {{ message.latency_ms }}ms
            </span>
            <AuditBadge v-if="message.role === 'assistant'" :status="message.audit_status" />
          </div>
          <pre class="markdown">{{ message.content || (streaming && message.id === streamingId ? '思考中…' : '') }}</pre>
          <div v-if="message.sources?.length" class="sources">
            <button
              v-for="source in message.sources"
              :key="source.index"
              class="tag info"
              @click="showSource(source)"
            >
              [{{ source.index }}] {{ source.document_name || '片段' }}
            </button>
          </div>
        </div>
      </div>

      <div v-if="notice" class="alert" :class="{ ok: noticeOk }">{{ notice }}</div>

      <div class="col">
        <textarea
          v-model="query"
          rows="2"
          placeholder="输入问题（Enter 发送，Shift+Enter 换行）"
          @keydown.enter.exact.prevent="send"
        ></textarea>
        <div class="row">
          <button class="primary" :disabled="streaming || !query.trim()" @click="send">
            {{ streaming ? '生成中…' : '发送' }}
          </button>
          <button v-if="streaming" @click="stop">停止</button>
        </div>
      </div>
    </section>
  </div>

  <div v-if="activeSource" class="modal" @click.self="activeSource = null">
    <div class="modal-body card col">
      <div class="row" style="justify-content: space-between">
        <strong>引用 [{{ activeSource.index }}] {{ activeSource.document_name }}</strong>
        <button @click="activeSource = null">关闭</button>
      </div>
      <p class="muted">相似度 {{ activeSource.score }} · 段落位置 {{ activeSource.position ?? '-' }}</p>
      <pre class="markdown">{{ activeSource.content }}</pre>
    </div>
  </div>
</template>


<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { chatApi } from '@/api/chat'
import { kbApi } from '@/api/kb'
import AuditBadge from '@/components/AuditBadge.vue'
import { formatTime } from '@/utils/format'
import type { SseController } from '@/utils/sse'
import type { ChatMessage, Conversation, SourceItem } from '@/types/api'

const route = useRoute()
const kbId = String(route.params.kbId)

const kbName = ref('')
const conversations = ref<Conversation[]>([])
const currentMeta = ref<Conversation | null>(null)
const conversationId = ref<string | null>(null)
const messages = ref<ChatMessage[]>([])
const query = ref('')
const streaming = ref(false)
const streamingId = ref('')
const notice = ref('')
const noticeOk = ref(false)
const activeSource = ref<SourceItem | null>(null)
const listRef = ref<HTMLElement | null>(null)

let controller: SseController | null = null

function setNotice(text: string, ok = false) {
  notice.value = text
  noticeOk.value = ok
}

async function scrollToEnd() {
  await nextTick()
  if (listRef.value) listRef.value.scrollTop = listRef.value.scrollHeight
}

async function loadKb() {
  try {
    const kb = await kbApi.detail(kbId)
    kbName.value = kb.name
  } catch (err) {
    setNotice((err as Error).message)
  }
}

async function loadConversations() {
  try {
    const page = await chatApi.conversations({ kb_id: kbId, page_size: 50 })
    conversations.value = page.items
  } catch (err) {
    setNotice((err as Error).message)
  }
}

async function openConversation(id: string) {
  conversationId.value = id
  messages.value = []
  try {
    const [detail, page] = await Promise.all([
      chatApi.conversation(id),
      chatApi.messages(id, { page_size: 100 })
    ])
    currentMeta.value = detail
    messages.value = [...page.items].reverse()
    await scrollToEnd()
  } catch (err) {
    setNotice((err as Error).message)
  }
}

function newChat() {
  conversationId.value = null
  currentMeta.value = null
  messages.value = []
  notice.value = ''
}

function stop() {
  controller?.abort()
  controller = null
  streaming.value = false
}

async function send() {
  const text = query.value.trim()
  if (!text || streaming.value) return
  notice.value = ''
  query.value = ''
  messages.value.push({
    id: `local-user-${Date.now()}`,
    conversation_id: conversationId.value ?? '',
    role: 'user',
    content: text,
    audit_status: 'pass'
  })
  await scrollToEnd()

  const placeholder: ChatMessage = {
    id: `local-assistant-${Date.now()}`,
    conversation_id: conversationId.value ?? '',
    role: 'assistant',
    content: '',
    audit_status: 'pass',
    sources: []
  }
  messages.value.push(placeholder)
  streaming.value = true
  streamingId.value = placeholder.id

  const payload = { query: text, conversation_id: conversationId.value }
  const handlers = {
    onEvent(event: string, data: Record<string, unknown>) {
      if (event === 'meta') {
        const convId = String(data.conversation_id || '')
        if (convId) {
          conversationId.value = convId
          placeholder.conversation_id = convId
        }
        if (data.message_id) placeholder.id = String(data.message_id)
      } else if (event === 'sources') {
        placeholder.sources = (data.items as SourceItem[]) ?? []
      } else if (event === 'message') {
        placeholder.content += String(data.delta ?? '')
        void scrollToEnd()
      } else if (event === 'usage') {
        placeholder.latency_ms = Number(data.latency_ms ?? 0)
      } else if (event === 'moderation') {
        setNotice(String(data.message ?? '内容触发审核'))
        placeholder.audit_status = 'block'
      } else if (event === 'error') {
        setNotice(`生成失败（${data.code}）：${data.message}`)
      } else if (event === 'done') {
        if (data.finish_reason === 'moderation_blocked') placeholder.audit_status = 'block'
      }
    },
    onError(error: Error) {
      setNotice(error.message)
    },
    onClose() {
      streaming.value = false
      streamingId.value = ''
      controller = null
      void loadConversations()
    }
  }

  controller =
    conversationId.value && currentMeta.value
      ? chatApi.continueStream(conversationId.value, payload, handlers)
      : chatApi.stream(kbId, payload, handlers)
}

function showSource(source: SourceItem) {
  activeSource.value = source
}

onMounted(async () => {
  await loadKb()
  await loadConversations()
})
</script>

<style scoped>
.chat-layout {
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: 14px;
  align-items: start;
}
.side {
  position: sticky;
  top: 70px;
}
.conv-list {
  list-style: none;
  padding: 0;
  margin: 8px 0 0;
  max-height: 60vh;
  overflow: auto;
}
.conv-list li {
  padding: 8px;
  border-radius: 8px;
  cursor: pointer;
}
.conv-list li:hover {
  background: #f1f5f9;
}
.conv-list li.active {
  background: var(--primary-weak);
}
.conv-list .title {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.main {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.messages {
  height: 52vh;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-right: 4px;
}
.bubble {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px;
  background: #fff;
}
.bubble.user {
  background: var(--primary-weak);
  border-color: #c7d2fe;
}
.bubble-head {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--muted);
}
.sources {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.sources .tag {
  cursor: pointer;
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
  max-width: 720px;
  width: 100%;
  max-height: 80vh;
  overflow: auto;
}
</style>
