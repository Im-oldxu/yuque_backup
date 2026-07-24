import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api'
import { ApiError, setAuthRequiredHandler } from '@/api/client'
import type { Admin } from '@/api/types'

export const useSessionStore = defineStore('session', () => {
  const administrator = ref<Admin | null>(null)
  const systemInitialized = ref(false)
  const bootstrapped = ref(false)
  const loading = ref(false)
  const bootstrapError = ref<string | null>(null)
  const isAuthenticated = computed(() => administrator.value !== null)

  async function bootstrap(force = false) {
    if (bootstrapped.value && !force) return
    loading.value = true
    let completed = false
    bootstrapError.value = null
    try {
      const state = await api.getInitialization()
      systemInitialized.value = state.initialized
      if (state.initialized) {
        try {
          administrator.value = await api.getMe()
        } catch (error) {
          if (error instanceof ApiError && error.status === 401) administrator.value = null
          else throw error
        }
      } else administrator.value = null
      completed = true
    } catch (error) {
      bootstrapError.value = error instanceof Error ? error.message : '初始化状态检查失败。'
      throw error
    } finally {
      bootstrapped.value = completed
      loading.value = false
    }
  }

  async function initialize(username: string, password: string) {
    administrator.value = await api.initialize(username, password)
    systemInitialized.value = true
    bootstrapped.value = true
    bootstrapError.value = null
  }

  async function login(username: string, password: string) {
    administrator.value = await api.login(username, password)
    systemInitialized.value = true
    bootstrapped.value = true
    bootstrapError.value = null
  }

  async function logout() {
    try { await api.logout() } finally { administrator.value = null }
  }

  function clear() {
    administrator.value = null
    bootstrapped.value = true
  }

  setAuthRequiredHandler(clear)

  return { administrator, systemInitialized, bootstrapped, loading, bootstrapError, isAuthenticated, bootstrap, initialize, login, logout, clear }
})
