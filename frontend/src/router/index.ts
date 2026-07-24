import { createRouter, createWebHashHistory } from 'vue-router'
import { api } from '@/api'
import { useSessionStore } from '@/stores/session'
import { resolveKnowledgeBaseEntry } from './knowledge-base-entry'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/auth', name: 'auth', component: () => import('@/views/AuthView.vue'), meta: { public: true, title: '管理员入口' } },
    {
      path: '/',
      component: () => import('@/layouts/AppShell.vue'),
      children: [
        { path: '', redirect: '/dashboard' },
        { path: 'dashboard', name: 'dashboard', component: () => import('@/views/DashboardView.vue'), meta: { title: '仪表盘' } },
        {
          path: 'repositories',
          name: 'repositories',
          component: () => import('@/views/RepositoriesView.vue'),
          beforeEnter: async () => {
            try {
              const documentId = await resolveKnowledgeBaseEntry(api)
              return documentId
                ? { name: 'document', params: { documentId }, replace: true }
                : { name: 'repository-management', replace: true }
            } catch {
              return { name: 'repository-management', replace: true }
            }
          },
          meta: { title: '知识库' },
        },
        { path: 'repositories/manage', name: 'repository-management', component: () => import('@/views/RepositoriesView.vue'), meta: { title: '知识库管理', activePath: '/repositories' } },
        { path: 'documents/:documentId', name: 'document', component: () => import('@/views/DocumentView.vue'), props: true, meta: { title: '文档与历史', activePath: '/repositories' } },
        { path: 'jobs', name: 'jobs', component: () => import('@/views/JobsView.vue'), meta: { title: '备份任务' } },
        { path: 'credentials', name: 'credentials', component: () => import('@/views/CredentialsView.vue'), meta: { title: '语雀凭据' } },
        { path: 'tombstones', name: 'tombstones', component: () => import('@/views/TombstonesView.vue'), meta: { title: '删除记录' } },
        { path: 'settings', name: 'settings', component: () => import('@/views/SettingsView.vue'), meta: { title: '设置' } },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
  ],
})

router.beforeEach(async (to) => {
  const session = useSessionStore()
  document.title = `${String(to.meta.title ?? '语雀备份')} | Yuque Backup`
  try {
    await session.bootstrap()
  } catch {
    if (to.name !== 'auth') {
      return { name: 'auth', query: { bootstrap_error: '1', redirect: to.fullPath } }
    }
    return true
  }
  if (!session.systemInitialized && to.name !== 'auth') return { name: 'auth' }
  if (session.systemInitialized && !session.isAuthenticated && to.name !== 'auth') return { name: 'auth', query: { redirect: to.fullPath } }
  if (to.name === 'auth' && session.isAuthenticated) return { name: 'dashboard' }
})

export default router
