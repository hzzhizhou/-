import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import http from '@/api'

// 登录态持久化 key（与 api/index.js 的拦截器共用，命名保持一致）
const TOKEN_KEY = 'auth_token'
const USER_KEY = 'auth_user'

export const useUserStore = defineStore('user', () => {
  const token = ref(localStorage.getItem(TOKEN_KEY) || '')
  const user = ref(JSON.parse(localStorage.getItem(USER_KEY) || 'null'))
  const isLogin = computed(() => !!token.value)
  const isAdmin = computed(() => user.value?.role === 'admin')
  const displayName = computed(() => user.value?.nickname || user.value?.username || '')

  function persist() {
    if (token.value) localStorage.setItem(TOKEN_KEY, token.value)
    else localStorage.removeItem(TOKEN_KEY)
    if (user.value) localStorage.setItem(USER_KEY, JSON.stringify(user.value))
    else localStorage.removeItem(USER_KEY)
  }

  function setAuth(data) {
    token.value = data.token
    user.value = data.user
    persist()
  }

  async function login(username, password) {
    const { data } = await http.post('/auth/login', { username, password })
    setAuth(data)
  }

  async function register(username, password, nickname) {
    const { data } = await http.post('/auth/register', { username, password, nickname })
    setAuth(data)
  }

  // 刷新页面后用本地 token 换回最新用户信息（token 失效则自动登出）
  async function fetchMe() {
    if (!token.value) return
    try {
      const { data } = await http.get('/auth/me')
      user.value = data.user
      persist()
    } catch (e) {
      logout()
    }
  }

  async function logout() {
    try {
      if (token.value) await http.post('/auth/logout')
    } catch (e) {
      /* 令牌可能已失效，忽略 */
    }
    token.value = ''
    user.value = null
    persist()
  }

  return { token, user, isLogin, isAdmin, displayName, login, register, fetchMe, logout, setAuth }
})