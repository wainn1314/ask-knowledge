<template>
  <div class="col">
    <div class="row" style="justify-content: space-between">
      <h2 style="margin: 0">我的知识库</h2>
      <div class="row">
        <input v-model="keyword" placeholder="搜索名称" style="width: 200px" @keyup.enter="load" />
        <button @click="load">搜索</button>
        <button v-if="!singleKbMode" class="primary" @click="creating = !creating">
          {{ creating ? '取消' : '新建知识库' }}
        </button>
      </div>
    </div>

    <div v-if="singleKbMode" class="alert ok">
      当前为<b>单知识库模式</b>：无需（也不能）新建知识库，直接进「管理文档」上传资料，
      文档会自动进入「{{ items[0]?.name || '唯一知识库' }}」，不会在 Dify 里新建知识库。
    </div>

    <div v-if="creating && !singleKbMode" class="card col">
      <div class="row">
        <input v-model="form.name" placeholder="知识库名称（必填）" />
        <select v-model="form.visibility" style="width: 160px">
          <option value="private">私有（仅成员）</option>
          <option value="tenant">租户可见（只读）</option>
        </select>
      </div>
      <input v-model="form.description" placeholder="描述（可选）" />
      <div class="row wrap">
        <label class="muted">分块长度 <input v-model.number="form.chunk_size" type="number" style="width: 90px" /></label>
        <label class="muted">重叠 <input v-model.number="form.chunk_overlap" type="number" style="width: 80px" /></label>
        <label class="muted">TopK <input v-model.number="form.top_k" type="number" style="width: 70px" /></label>
        <label class="muted">
          阈值 <input v-model.number="form.score_threshold" type="number" step="0.05" style="width: 80px" />
        </label>
        <button class="primary" :disabled="saving" @click="create">创建（会同步创建 Dify 知识库）</button>
      </div>
    </div>

    <div v-if="error" class="alert">{{ error }}</div>

    <div v-if="!items.length" class="card">
      <EmptyState :text="singleKbMode ? '唯一知识库加载中…' : '还没有知识库，先创建一个吧'" />
    </div>

    <div v-else class="card">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>我的角色</th>
            <th>可见性</th>
            <th>文档数</th>
            <th>Dify 数据集</th>
            <th>检索配置</th>
            <th>更新时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="kb in items" :key="kb.id">
            <td>
              <router-link :to="`/kb/${kb.id}`">{{ kb.name }}</router-link>
              <div class="muted">{{ kb.description }}</div>
            </td>
            <td><span class="tag info">{{ kb.role }}</span></td>
            <td>{{ kb.visibility === 'tenant' ? '租户可见' : '私有' }}</td>
            <td>{{ kb.doc_count }}</td>
            <td class="muted" :title="kb.dify_dataset_id || '尚未创建 Dify 数据集'">
              {{ kb.dify_dataset_id ? `${kb.dify_dataset_id.slice(0, 8)}…` : '未创建' }}
            </td>
            <td class="muted">
              chunk {{ kb.chunk_size }}/{{ kb.chunk_overlap }} · topK {{ kb.top_k }} ·
              阈值 {{ kb.score_threshold }}
            </td>
            <td class="muted">{{ formatTime(kb.updated_at) }}</td>
            <td>
              <div class="row wrap">
                <router-link :to="`/kb/${kb.id}/chat`"><button>问答</button></router-link>
                <router-link :to="`/kb/${kb.id}/quiz`"><button>出题</button></router-link>
                <router-link :to="`/knowledge?kb=${kb.id}`"><button>管理文档</button></router-link>
                <button
                  v-if="!singleKbMode"
                  :disabled="kb.role !== 'owner' || removingId === kb.id"
                  :title="kb.role === 'owner' ? '删除该知识库（含 Dify 数据集）' : '只有创建者可以删除'"
                  @click="remove(kb)"
                >
                  {{ removingId === kb.id ? '删除中…' : '删除' }}
                </button>
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
import { kbApi, type KbPayload } from '@/api/kb'
import EmptyState from '@/components/EmptyState.vue'
import { formatTime } from '@/utils/format'
import type { Kb } from '@/types/api'

const items = ref<Kb[]>([])
const keyword = ref('')
const creating = ref(false)
const saving = ref(false)
const removingId = ref('')
const error = ref('')
/** 单知识库模式：不能建库，全站共用「唯一知识库」（后端 /kbs/default 下发） */
const singleKbMode = ref(false)

const form = reactive<Required<Pick<KbPayload, 'name' | 'visibility'>> & KbPayload>({
  name: '',
  description: '',
  visibility: 'private',
  chunk_size: 512,
  chunk_overlap: 50,
  top_k: 5,
  score_threshold: 0.5
})

async function load() {
  error.value = ''
  try {
    // 先问后端模式：单知识库模式下只有一个库，不展示「新建知识库」入口
    const defaultInfo = await kbApi.defaultKb()
    singleKbMode.value = defaultInfo.single_kb_mode
    if (defaultInfo.single_kb_mode && defaultInfo.kb) {
      const kw = keyword.value.trim().toLowerCase()
      items.value = kw && !defaultInfo.kb.name.toLowerCase().includes(kw) ? [] : [defaultInfo.kb]
      return
    }
    const page = await kbApi.list({ keyword: keyword.value || undefined, page_size: 50 })
    items.value = page.items
  } catch (err) {
    error.value = (err as Error).message
  }
}

async function create() {
  if (!form.name.trim()) {
    error.value = '请填写知识库名称'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await kbApi.create({ ...form })
    creating.value = false
    form.name = ''
    form.description = ''
    await load()
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    saving.value = false
  }
}

onMounted(load)

/** 删除知识库：后端软删本地记录并同步删除对应的 Dify 数据集（Dify 侧失败不阻塞）。 */
async function remove(kb: Kb) {
  if (kb.role !== 'owner') {
    error.value = '只有知识库创建者可以删除'
    return
  }
  const tip =
    `确认删除知识库「${kb.name}」？\n` +
    `· 该库 ${kb.doc_count} 个文档的索引会一并移除\n` +
    `· Dify 侧数据集 ${kb.dify_dataset_id ? kb.dify_dataset_id.slice(0, 8) + '…' : '（未创建）'} 会被删除\n` +
    '删除后不可恢复。'
  if (!window.confirm(tip)) return
  removingId.value = kb.id
  error.value = ''
  try {
    await kbApi.remove(kb.id)
    await load()
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    removingId.value = ''
  }
}
</script>
