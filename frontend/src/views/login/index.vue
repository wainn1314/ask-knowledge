<template>
  <div class="login-wrap">
    <div class="card login-card">
      <h2>Ask Knowledge</h2>
      <p class="muted">AI 学习伙伴：把资料变成可问答、可自测的知识库</p>

      <div class="col">
        <label class="muted">邮箱</label>
        <input v-model="email" type="email" placeholder="owner@example.com" @keyup.enter="submit" />
        <label class="muted">密码</label>
        <input v-model="password" type="password" placeholder="secret123" @keyup.enter="submit" />
        <div class="row">
          <button class="primary" :disabled="loading" @click="submit">
            {{ loading ? '请稍候…' : mode === 'login' ? '登录' : '注册并登录' }}
          </button>
          <button :disabled="loading" @click="mode = mode === 'login' ? 'register' : 'login'">
            {{ mode === 'login' ? '没有账号？注册' : '已有账号？登录' }}
          </button>
        </div>
        <div v-if="error" class="alert">{{ error }}</div>
        <p class="muted">
          提示：执行 <code>python backend/scripts/seed.py</code> 会创建 owner@example.com / secret123
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { http } from '@/api/http'
import { setAuth } from '@/stores/auth'
import type { TokenResult } from '@/types/api'

const router = useRouter()
const route = useRoute()
const mode = ref<'login' | 'register'>('login')
const email = ref('owner@example.com')
const password = ref('secret123')
const loading = ref(false)
const error = ref('')

async function submit() {
  if (!email.value || !password.value) {
    error.value = '请填写邮箱与密码'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const path = mode.value === 'login' ? '/auth/login' : '/auth/register'
    const result = await http.post<TokenResult>(path, {
      email: email.value,
      password: password.value,
      ...(mode.value === 'register' ? { nickname: email.value.split('@')[0] } : {})
    })
    setAuth(result)
    const redirect = (route.query.redirect as string) || '/kb'
    await router.replace(redirect)
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(180deg, #eef2ff 0%, var(--bg) 60%);
}
.login-card {
  width: 360px;
}
</style>
