import axios from 'axios'

const TOKEN_KEY = 'auth_token'
const USER_KEY = 'auth_user'

// 统一 axios 实例：走 Vite 代理（/api → http://127.0.0.1:8000）
const http = axios.create({
  baseURL: '/api',
  timeout: 300000,
})

// 请求拦截：自动带上登录令牌
http.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 响应拦截：令牌失效统一清理并跳登录页（登录/注册接口本身返回 401 属正常校验，不跳转）
http.interceptors.response.use(
  (resp) => resp,
  (err) => {
    const url = err.config?.url || ''
    const isAuthEntry = url.includes('/auth/login') || url.includes('/auth/register')
    if (err.response?.status === 401 && !isAuthEntry) {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
      if (window.location.pathname !== '/login') window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

export default http

// 流式 POST：读 response.body，逐段回调，返回 Promise<完整文本>
export async function streamPost(url, payload, onChunk) {
  const token = localStorage.getItem(TOKEN_KEY)
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  const resp = await fetch('/api' + url, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })
  if (!resp.ok) {
    const text = await resp.text()
    throw new Error(`请求失败 ${resp.status}: ${text.slice(0, 200)}`)
  }
  if (onChunk && resp.body) {
    const reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let full = ''
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      const chunk = decoder.decode(value, { stream: true })
      full += chunk
      onChunk(chunk)
    }
    return full
  }
  return await resp.text()
}

// 人工会话下行通道：服务端有新消息就推过来（发送仍走 HTTP POST）。
// 浏览器 WebSocket 构造器不支持自定义 header，令牌只能放在 query 上。
// 用 location 动态拼地址，避免局域网访问时写死的 localhost 连不上。
export function openHandoffSocket(ticketId) {
  const token = localStorage.getItem(TOKEN_KEY) || ''
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return new WebSocket(
    `${proto}://${location.host}/api/ws/handoff/${ticketId}?token=${encodeURIComponent(token)}`
  )
}