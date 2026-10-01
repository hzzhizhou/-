<template>
  <div class="workbench">
    <!-- 左侧：人工会话列表 -->
    <aside class="sessions">
      <div class="sessions-head">
        <span class="head-title">人工会话</span>
        <el-button size="small" text type="primary" @click="loadSessions()">刷新</el-button>
      </div>
      <div class="sessions-body" v-loading="loadingList">
        <el-empty v-if="!sessions.length" description="暂无人工会话" :image-size="70" />
        <div
          v-for="s in sessions"
          :key="s.ticket_id"
          class="session-item"
          :class="{ active: s.ticket_id === activeId, closed: s.status === 'closed' }"
          @click="openSession(s)"
        >
          <div class="row-1">
            <span class="who">{{ s.nickname || s.username || s.user_id }}</span>
            <el-tag v-if="s.status === 'closed'" size="small" type="info">已结束</el-tag>
          </div>
          <div class="row-2">{{ s.last_content }}</div>
          <div class="row-3">
            <span>{{ s.ticket_id }}</span>
            <span>{{ s.last_at }}</span>
          </div>
        </div>
      </div>
    </aside>

    <!-- 右侧：对话窗 -->
    <section class="chat">
      <template v-if="active">
        <header class="chat-head">
          <div class="head-left">
            <span class="title">{{ active.nickname || active.username || active.user_id }}</span>
            <span class="sub">工单号 {{ active.ticket_id }}</span>
          </div>
          <el-button
            v-if="active.status !== 'closed'"
            size="small"
            type="danger"
            plain
            @click="closeSession"
          >
            结束会话
          </el-button>
          <el-tag v-else type="info">会话已结束</el-tag>
        </header>

        <!-- 转人工前的机器人会话：客服接手时的上下文，不用再问「您之前问的是？」 -->
        <div v-if="context.length" class="pre-context">
          <div class="pre-head" @click="showContext = !showContext">
            <span class="pre-title">转人工前的机器人会话</span>
            <span class="pre-count">最近 {{ context.length }} 条</span>
            <span class="pre-toggle">{{ showContext ? '收起' : '展开' }}</span>
          </div>
          <div v-show="showContext" class="pre-body">
            <div v-for="(m, i) in context" :key="i" class="pre-row" :class="m.role">
              <span class="pre-who">{{ m.role === 'user' ? '客户' : '机器人' }}</span>
              <span class="pre-text">{{ m.content }}</span>
            </div>
          </div>
        </div>

        <main class="chat-body">
          <div class="msg-list">
            <div v-for="(m, i) in messages" :key="i" class="msg-row" :class="m.role">
              <div v-if="m.role === 'system'" class="sys-line">{{ m.text }}</div>
              <template v-else>
                <div class="avatar">{{ m.role === 'user' ? '客' : '我' }}</div>
                <div class="bubble-wrap">
                  <div class="bubble">{{ m.text }}</div>
                  <div class="meta">{{ m.created_at }}</div>
                </div>
              </template>
            </div>
          </div>
        </main>

        <footer class="chat-foot">
          <el-input
            v-model="input"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 5 }"
            resize="none"
            :disabled="active.status === 'closed' || busy"
            :placeholder="active.status === 'closed' ? '会话已结束' : '输入回复内容，回车发送'"
            @keydown.enter.exact.prevent="send"
          />
          <el-button
            type="primary"
            :loading="busy"
            :disabled="active.status === 'closed'"
            @click="send"
          >
            发送
          </el-button>
        </footer>
      </template>
      <el-empty v-else description="请从左侧选择一场人工会话" />
    </section>
  </div>
</template>

<script setup>
import { ref, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { openHandoffSocket } from '@/api'

const sessions = ref([])
const loadingList = ref(false)
const activeId = ref('')
const active = ref(null)
const messages = ref([])
const input = ref('')
const busy = ref(false)
const context = ref([])        // 转人工前的机器人会话（客服接手时的上下文）
const showContext = ref(true)  // 默认展开：客服一眼看到客户刚才在问什么
const seenIds = new Set()   // 已上屏消息的 msg_id，用于去重（无需响应式）
const POLL_MS = 2000        // 会话列表轮询间隔：人工会话随时可能转进来，客服不该靠手动刷新
let socket = null
let disposed = false
let timer = null            // 轮询定时器
let polling = false         // 上一轮请求是否在途，避免请求堆积

function fromServer(m) {
  const role = m.sender === 'agent' ? 'agent' : m.sender === 'system' ? 'system' : 'user'
  return {
    role,
    text: m.content,
    created_at: m.created_at,
    sender_name: m.sender_name,
  }
}

/**
 * 拉取会话列表。
 * 轮询时传 silent：不转圈、不弹错误提示 —— 否则每 2 秒闪一次 loading，
 * 网络抖动还会刷屏报错；手动点「刷新」保留可见反馈。
 */
async function loadSessions({ silent = false } = {}) {
  if (polling) return
  polling = true
  if (!silent) loadingList.value = true
  try {
    const { data } = await http.get('/handoff/sessions')
    sessions.value = data.sessions || []
    // 整个数组被替换，右侧对话窗持有的还是旧对象引用；不重新绑定的话，
    // 别人结束会话后本端的「结束会话」按钮不会变成「会话已结束」
    if (activeId.value) {
      active.value = sessions.value.find((s) => s.ticket_id === activeId.value) || active.value
    }
  } catch (e) {
    if (!silent) ElMessage.error(e.response?.data?.detail || '加载会话列表失败')
  } finally {
    polling = false
    if (!silent) loadingList.value = false
  }
}

function startPolling() {
  stopPolling()
  timer = setInterval(() => {
    if (document.hidden) return   // 后台标签页不轮询，切回来下一轮自动补上
    loadSessions({ silent: true })
  }, POLL_MS)
}

function stopPolling() {
  if (timer) clearInterval(timer)
  timer = null
}

function append(m) {
  if (seenIds.has(m.msg_id)) return
  seenIds.add(m.msg_id)
  messages.value.push(fromServer(m))
  scrollBottom()
}

async function openSession(s) {
  closeSocket()
  activeId.value = s.ticket_id
  active.value = s
  messages.value = []
  context.value = []
  seenIds.clear()
  try {
    const { data } = await http.get(`/tickets/${s.ticket_id}/messages`)
    ;(data.messages || []).forEach(append)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '加载会话消息失败')
  }
  loadContext(s.ticket_id)
  openWs(s.ticket_id)
}

/** 拉取转人工前的机器人会话记录；失败只是少一块上下文，不影响客服继续对话 */
async function loadContext(ticketId) {
  try {
    const { data } = await http.get(`/tickets/${ticketId}/context`)
    context.value = data.messages || []
  } catch (e) {
    console.error(e)
  }
}

function openWs(ticketId) {
  try {
    socket = openHandoffSocket(ticketId)
  } catch (e) {
    console.error(e)
    return
  }
  socket.onmessage = (ev) => {
    if (disposed) return
    let payload
    try { payload = JSON.parse(ev.data) } catch (e) { return }
    if (payload.type === 'message' && payload.message) {
      append(payload.message)
      // 同步更新左侧列表的最后一条，避免列表与对话窗对不上
      const s = sessions.value.find((x) => x.ticket_id === ticketId)
      if (s) {
        s.last_content = payload.message.content
        s.last_at = payload.message.created_at
        s.message_count = (s.message_count || 0) + 1
      }
    } else if (payload.type === 'session_closed') {
      if (payload.message) append(payload.message)
      const s = sessions.value.find((x) => x.ticket_id === ticketId)
      if (s) s.status = 'closed'
      if (active.value) active.value.status = 'closed'
      ElMessage.info('该会话已结束')
    }
  }
  socket.onclose = () => { socket = null }
  socket.onerror = () => { /* 断线不影响已拉取的历史，发送走 HTTP 仍可用 */ }
}

function closeSocket() {
  if (!socket) return
  try { socket.close() } catch (e) { /* 忽略关闭异常 */ }
  socket = null
}

async function send() {
  const text = input.value.trim()
  if (!text || busy.value || !activeId.value) return
  busy.value = true
  try {
    const { data } = await http.post(`/tickets/${activeId.value}/messages`, { content: text })
    input.value = ''
    append(data)   // 本地立即上屏；WS 回显按 msg_id 去重
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '发送失败')
  } finally {
    busy.value = false
  }
}

async function closeSession() {
  try {
    await ElMessageBox.confirm(
      '结束后用户将回到智能客服，仍可再次发起转人工。确定结束吗？',
      '结束会话',
      { type: 'warning' },
    )
  } catch (e) {
    return   // 用户取消
  }
  try {
    await http.post(`/handoff/${activeId.value}/close`)
    ElMessage.success('会话已结束')
    await loadSessions()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '结束会话失败')
  }
}

function scrollBottom() {
  requestAnimationFrame(() => {
    const el = document.querySelector('.workbench .msg-list')
    if (el) el.scrollTop = el.scrollHeight
  })
}

onUnmounted(() => {
  disposed = true
  stopPolling()
  closeSocket()
})

loadSessions()
startPolling()
</script>

<style scoped>
.workbench {
  display: flex;
  gap: 16px;
  height: calc(100vh - 48px);
}

/* ---------- 左侧会话列表 ---------- */
.sessions {
  width: 320px;
  flex-shrink: 0;
  background: #fff;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 1px 6px rgba(31, 45, 61, 0.06);
}
.sessions-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid #eef0f4;
}
.head-title { font-size: 15px; font-weight: 600; color: #1f2d3d; }
.sessions-body { flex: 1; overflow-y: auto; }
.session-item {
  padding: 12px 14px;
  border-bottom: 1px solid #f5f6f9;
  cursor: pointer;
  transition: background 0.15s;
}
.session-item:hover { background: #f7f9fc; }
.session-item.active { background: #ecf5ff; border-left: 3px solid #409eff; }
.session-item.closed { opacity: 0.6; }
.row-1 { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.who { font-size: 14px; font-weight: 600; color: #1f2d3d; }
.row-2 {
  font-size: 13px;
  color: #5a6b8c;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-bottom: 4px;
}
.row-3 {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #a8b0c2;
}

/* ---------- 右侧对话窗 ---------- */
.chat {
  flex: 1;
  min-width: 0;
  background: #fff;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 1px 6px rgba(31, 45, 61, 0.06);
}
.chat-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #eef0f4;
}
.head-left { display: flex; align-items: baseline; gap: 10px; }
.title { font-size: 15px; font-weight: 600; color: #1f2d3d; }
.sub { font-size: 12px; color: #909399; }
/* ---------- 转人工前的机器人会话（客服接手上下文） ---------- */
.pre-context { border-bottom: 1px solid #eef0f4; background: #fbfcfe; }
.pre-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  cursor: pointer;
  user-select: none;
}
.pre-title { font-size: 13px; font-weight: 600; color: #5a6b8c; }
.pre-count { font-size: 12px; color: #a8b0c2; }
.pre-toggle { margin-left: auto; font-size: 12px; color: #409eff; }
.pre-body { max-height: 190px; overflow-y: auto; padding: 0 16px 10px; }
.pre-row { display: flex; gap: 8px; font-size: 12px; line-height: 1.7; margin-bottom: 4px; }
.pre-who { flex-shrink: 0; width: 44px; color: #909399; }
.pre-text { color: #5a6b8c; word-break: break-word; }
.pre-row.user .pre-text { color: #1f2d3d; }

.chat-body { flex: 1; overflow: hidden; }
.msg-list { height: 100%; overflow-y: auto; padding: 16px; }
.msg-row { display: flex; gap: 10px; margin-bottom: 16px; }
.msg-row.agent { flex-direction: row-reverse; }
.msg-row.system { justify-content: center; }
.sys-line {
  font-size: 13px;
  color: #909399;
  background: #f2f6fc;
  padding: 5px 14px;
  border-radius: 12px;
  max-width: 80%;
  text-align: center;
  line-height: 1.6;
}
.avatar {
  width: 34px; height: 34px;
  border-radius: 50%;
  flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 14px; font-weight: 600;
}
.msg-row.user .avatar { background: linear-gradient(135deg, #36cfc9, #67c23a); }
.msg-row.agent .avatar { background: linear-gradient(135deg, #409eff, #6a5cff); }
.bubble-wrap { max-width: 72%; }
.msg-row.agent .bubble-wrap { display: flex; flex-direction: column; align-items: flex-end; }
.bubble {
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  background: #f4f6fa;
  color: #1f2d3d;
}
.msg-row.agent .bubble { background: #409eff; color: #fff; }
.meta { font-size: 11px; color: #a8b0c2; margin-top: 4px; }
.chat-foot {
  display: flex;
  gap: 10px;
  align-items: flex-end; /* 回复框可随内容长高，发送按钮贴底对齐 */
  padding: 12px 16px;
  border-top: 1px solid #eef0f4;
}
</style>