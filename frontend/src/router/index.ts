/** 路由与登录守卫（docs/02 §3）。 */

import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'
import { isLoggedIn } from '@/stores/auth'

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/views/login/index.vue') },
  {
    path: '/',
    component: () => import('@/layouts/BasicLayout.vue'),
    children: [
      { path: '', redirect: '/kb' },
      { path: 'kb', name: 'kb-list', component: () => import('@/views/kb/list.vue') },
      { path: 'kb/:kbId', name: 'kb-detail', component: () => import('@/views/kb/detail.vue') },
      { path: 'kb/:kbId/chat', name: 'chat', component: () => import('@/views/chat/index.vue') },
      { path: 'kb/:kbId/quiz', name: 'quiz-generate', component: () => import('@/views/quiz/generate.vue') },
      // 知识库文档管理（主后端 /api/v1/kbs/{kb_id}/documents 流水线，?kb=<id> 指定知识库）
      { path: 'knowledge', name: 'knowledge', component: () => import('@/views/kb/manage.vue') },
      { path: 'quiz/:quizId/take', name: 'quiz-take', component: () => import('@/views/quiz/take.vue') },
      { path: 'quiz/:quizId/result', name: 'quiz-result', component: () => import('@/views/quiz/result.vue') },
      { path: 'wrong-book', name: 'wrong-book', component: () => import('@/views/wrong-book/index.vue') },
      { path: 'history', name: 'history', component: () => import('@/views/history/index.vue') }
    ]
  },
  { path: '/:pathMatch(.*)*', redirect: '/kb' }
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes
})

router.beforeEach((to) => {
  if (to.name === 'login') return true
  if (!isLoggedIn.value) return { name: 'login', query: { redirect: to.fullPath } }
  return true
})
