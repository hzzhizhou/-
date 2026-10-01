<template>
  <div class="chat-page">
    <div class="chat-main">
      <!-- 顶部栏 -->
      <header class="chat-header">
        <div class="brand">
          <span class="brand-logo">智</span>
          <span class="brand-title">智选商城 · 智能客服</span>
        </div>
        <div class="header-actions">
          <el-button size="small" text type="primary" @click="goOrders">我的订单</el-button>
          <el-tag
            v-if="handoff"
            :type="agentJoined ? 'success' : 'warning'"
            size="small"
            round
            class="handoff-tag"
          >
            {{ agentJoined ? '人工客服中' : '等待客服接入' }} · {{ handoff.ticket_id }}
          </el-tag>
          <el-button v-else size="small" text type="warning" @click="requestHandoff">
            转人工
          </el-button>
          <span class="me-name">{{ userStore.displayName }}</span>
          <el-button size="small" text @click="doLogout">退出</el-button>
          <el-tooltip v-if="userStore.isAdmin" content="进入管理后台" placement="bottom">
            <el-button size="small" text type="primary" @click="goAdmin">管理端</el-button>
          </el-tooltip>
        </div>
      </header>

      <!-- 消息区 -->
      <main class="chat-body">
        <!-- 空状态引导：界面元素，不属于对话消息（不写入历史） -->
        <div v-if="showGuide && !messages.length" class="chat-empty">
          <div class="empty-icon">🤖</div>
          <p class="empty-title">您好，欢迎光临智选商城！我是您的专属智能客服小智</p>
          <p class="empty-sub">我可以为您办理以下事项，请直接描述您遇到的问题：</p>
          <div class="empty-feats">
            <span>📦 订单与物流查询</span>
            <span>↩️ 退货 / 退款办理</span>
            <span>📣 投诉受理与转人工</span>
            <span>📄 售后政策咨询</span>
          </div>
          <div class="quick-questions">
            <el-button round size="small" @click="quickAsk('退货流程是什么？')">退货流程</el-button>
            <el-button round size="small" @click="quickAsk('如何查询订单？')">查询订单</el-button>
            <el-button round size="small" @click="quickAsk('物流到哪啦？')">物流进度</el-button>
          </div>
        </div>

        <div v-else class="msg-list">
          <div v-for="(m, i) in messages" :key="i" class="msg-row" :class="m.role">
            <!-- 系统提示与本地分隔条（居中的界面元素，不参与左右气泡布局） -->
            <div v-if="m.role === 'system' || m.role === 'divider'" class="sys-line">
              {{ m.text }}
            </div>
            <template v-else>
              <div class="avatar">
                <span>{{ avatarText(m) }}</span>
              </div>
              <div class="bubble-wrap">
                <div class="bubble">
                  <pre class="content">{{ m.text || m.content }}</pre>
                </div>
              </div>
            </template>
          </div>
        </div>
      </main>

      <!-- 输入区 -->
      <footer class="chat-footer">
        <div class="input-bar">
          <el-input
            v-model="input"
            class="chat-input"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 5 }"
            resize="none"
            :placeholder="handoff ? '给人工客服留言…' : '请输入您的问题，例如：我要退货'"
            :disabled="busy"
            @keydown.enter.exact.prevent="send"
          />
          <el-button class="send-btn" type="primary" round :loading="busy" @click="send">
            发送
          </el-button>
        </div>
      </footer>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import http, { streamPost, openHandoffSocket } from '@/api'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const input = ref('')
const busy = ref(false)
const messages = ref([])
const showGuide = ref(true)     // 空状态引导卡片（界面元素，不属于对话消息）
// 服务端是会话的唯一事实来源：进页面直接续接上次的对话，不用本地记忆
const sessionId = ref('')

// ---------- 人工会话（转人工实时对话）----------
const handoff = ref(null)        // 非空表示正处于人工客服模式（值为工单记录）
const handoffIds = new Set()     // 已上屏的人工消息 msg_id，用于去重（无需响应式）

// 客服是否已真正接入：以「有没有收到过客服发来的消息」为准。
// 转人工只代表请求已提交，此刻还没有客服受理，所以不能直接显示「人工客服中」。
const agentJoined = computed(() => messages.value.some((m) => m.role === 'agent'))
let handoffSocket = null
let disposed = false             // 组件卸载后不再改响应式状态、不再解析推送

function goAdmin() { router.push('/admin/tickets') }
function goOrders() { router.push('/my/orders') }

async function doLogout() {
  await userStore.logout()
  router.push('/login')
}

/** 进入即续接最近一次对话（像进店铺直接接着聊）；没有历史就等第一条消息创建会话 */
async function resumeLastConversation() {
  try {
    const { data } = await http.get('/history/sessions')
    const last = (data.sessions || [])[0]
    if (!last) return
    sessionId.value = last.session_id
    const { data: hist } = await http.get('/history/messages', {
      params: { session_id: last.session_id },
    })
    messages.value = (hist.messages || []).map((m) => ({
      role: m.role,
      content: m.content,
      text: m.content,
      done: true,
      fb: undefined,
    }))
    showGuide.value = messages.value.length === 0
    scrollBottom()
  } catch (e) {
    console.error(e)
  }
}

function quickAsk(q) { input.value = q; send() }

async function send() {
  const q = input.value.trim()
  if (!q || busy.value) return
  input.value = ''
  if (handoff.value) return sendToAgent(q)

  // 首次对话：本地生成会话 ID，发出这条消息后服务端才会真正建会话
  if (!sessionId.value) sessionId.value = crypto.randomUUID()
  messages.value.push({ role: 'user', content: q, text: q, done: true })
  const item = reactive({ role: 'assistant', content: '', text: '', done: false })
  messages.value.push(item)
  showGuide.value = false
  // 发出后立刻把自己这条滚进视野：首字要等检索+生成（实测约 2.8 秒），
  // 原先只在收到首个分片时才滚，这 2.8 秒里新消息停在视野外
  scrollBottom()
  busy.value = true
  try {
    await streamPost('/agent/stream', { question: q, session_id: sessionId.value }, (chunk) => {
      item.text += chunk
      item.content = item.text
      scrollBottom()
    })
  } catch (e) {
    // 流式连接被中断（网络抖动、开发期热更新重载等）：服务端即使客户端断开也会把整段
    // 答复生成完并落库，这里把它取回来补上，避免用户只看到半句话
    if (!(await recoverAnswer(q, item))) {
      item.text = `⚠️ 出错了：${e.message}`
    }
  } finally {
    item.done = true
    busy.value = false
    scrollBottom()
    // AI 可能刚判定需要转人工并建了工单，这里感知一下，自动切到人工模式
    await checkHandoff()
  }
}

/**
 * 流式中断后的补救：服务端即使客户端断开也会把整段答复生成完并落库，
 * 这里把已落库的答复取回来补上，用户就不会停在半句话上。
 * 只认「历史最后两条 = 本次提问 + 助手答复」，避免把上一条的答案错当成这一条的。
 * 落库发生在流结束之后，而检索+生成首字就要好几秒，所以轮询窗口要给足（6 次 × 2 秒）。
 */
async function recoverAnswer(question, item) {
  for (let attempt = 0; attempt < 6; attempt++) {
    if (disposed) return false
    if (attempt) await new Promise((resolve) => setTimeout(resolve, 2000))
    try {
      const { data } = await http.get('/history/messages', {
        params: { session_id: sessionId.value },
      })
      const list = data.messages || []
      const last = list[list.length - 1]
      const prev = list[list.length - 2]
      if (last && last.role === 'assistant' && last.content
          && prev && prev.role === 'user'
          && (prev.content || '').trim() === question) {
        item.text = last.content
        item.content = last.content
        scrollBottom()
        return true
      }
    } catch (e) {
      console.error(e)
    }
  }
  return false
}

// ---------- 人工会话 ----------

/** 消息角色 → 气泡头像文字 */
function avatarText(m) {
  if (m.role === 'user') return '我'
  return m.role === 'agent' ? '客服' : 'AI'
}

/** 服务端消息 → 界面消息。sender 为 user/agent/system */
function fromServer(m) {
  const role = m.sender === 'agent' ? 'agent' : m.sender === 'system' ? 'system' : 'user'
  return { role, content: m.content, text: m.content, done: true }
}

/** 上屏一条人工消息，按 msg_id 去重（发送方自己也会收到广播） */
function appendHandoff(m) {
  if (handoffIds.has(m.msg_id)) return
  handoffIds.add(m.msg_id)
  messages.value.push(fromServer(m))
  showGuide.value = false
  scrollBottom()
}

/** 查询是否已有人工会话；有则进入人工模式 */
async function checkHandoff() {
  if (disposed || handoff.value) return
  try {
    const { data } = await http.get('/handoff/active')
    if (data.ticket) await enterHandoff(data.ticket)
  } catch (e) {
    console.error(e)
  }
}

/** 进入人工模式：补分隔条 + 拉历史消息 + 建立下行连接 */
async function enterHandoff(ticket) {
  if (disposed || handoff.value) return
  handoff.value = ticket
  messages.value.push({
    role: 'divider',
    text: `已提交人工服务请求 · 工单号 ${ticket.ticket_id}`,
  })
  try {
    const { data } = await http.get(`/tickets/${ticket.ticket_id}/messages`)
    ;(data.messages || []).forEach(appendHandoff)
  } catch (e) {
    console.error(e)
  }
  openSocket(ticket.ticket_id)
  scrollBottom()
}

function openSocket(ticketId) {
  closeSocket()
  try {
    handoffSocket = openHandoffSocket(ticketId)
  } catch (e) {
    console.error(e)
    return
  }
  handoffSocket.onmessage = (ev) => {
    if (disposed) return
    let payload
    try { payload = JSON.parse(ev.data) } catch (e) { return }
    if (payload.type === 'message' && payload.message) {
      appendHandoff(payload.message)
    } else if (payload.type === 'session_closed') {
      if (payload.message) appendHandoff(payload.message)
      // 客服结束了会话：退回智能客服模式，用户仍可继续向 AI 提问
      handoff.value = null
      ElMessage.info('人工客服已结束本次会话')
    }
  }
  handoffSocket.onclose = () => { handoffSocket = null }
  handoffSocket.onerror = () => { /* 断线时仍可用 REST 拉历史，不额外提示 */ }
}

function closeSocket() {
  if (!handoffSocket) return
  try { handoffSocket.close() } catch (e) { /* 忽略关闭异常 */ }
  handoffSocket = null
}

/** 用户主动点「转人工」 */
async function requestHandoff() {
  if (busy.value) return
  busy.value = true
  try {
    const { data } = await http.post('/handoff/request', { question: input.value.trim() })
    input.value = ''
    await enterHandoff(data)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '转人工失败，请稍后再试')
  } finally {
    busy.value = false
  }
}

/** 人工模式下发送：走 HTTP POST，服务端写库后经 WebSocket 推给双方 */
async function sendToAgent(q) {
  busy.value = true
  try {
    const { data } = await http.post(`/tickets/${handoff.value.ticket_id}/messages`, {
      content: q,
    })
    // 本地立即上屏；WS 回显同一条消息时按 msg_id 去重，不会重复
    appendHandoff(data)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '发送失败，请稍后再试')
    input.value = q   // 发送失败把内容还给用户，避免白打一遍
  } finally {
    busy.value = false
  }
}

function scrollBottom() {
  nextTick(() => {
    const body = document.querySelector('.msg-list')
    if (body) body.scrollTop = body.scrollHeight
  })
}

// 进入页面直接续接上次的对话（淘宝式：进店就能接着聊）
onMounted(async () => {
  await resumeLastConversation()
  await checkHandoff()  // 上次转的人工会话若还在进行中，进页面直接接上
  userStore.fetchMe()   // 用本地令牌刷新登录态
  askFromOrders()       // 从「我的订单」页点退货跳过来：自动把退货诉求发给客服
})

/**
 * 「我的订单」页的退货入口：跳转时把诉求放在 query.ask 里，这里读出来自动发送。
 * 发完立即清掉 query，避免用户刷新页面又重复提交一遍。
 */
function askFromOrders() {
  const ask = route.query.ask
  if (!ask) return
  router.replace({ path: '/' })
  input.value = String(ask)
  send()
}

onUnmounted(() => {
  disposed = true
  closeSocket()
})
</script>

<style scoped>
.chat-page {
  height: 100vh;
  display: flex;
  background: linear-gradient(180deg, #eef2fb 0%, #f7f8fc 100%);
}

/* 单栏布局：进来就是对话本身，没有会话记录列表 */
.chat-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }

/* ---------- 顶部 ---------- */
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 28px;
  height: 60px;
  background: #fff;
  box-shadow: 0 1px 6px rgba(31, 45, 61, 0.08);
  flex-shrink: 0;
}
.brand { display: flex; align-items: center; gap: 10px; }
.brand-logo {
  width: 34px; height: 34px;
  border-radius: 9px;
  background: linear-gradient(135deg, #409eff, #6a5cff);
  color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: 18px; font-weight: 700;
  box-shadow: 0 3px 8px rgba(90, 92, 255, 0.35);
}
.brand-title { font-size: 17px; font-weight: 600; color: #1f2d3d; }
.header-actions { display: flex; align-items: center; gap: 4px; }
.handoff-tag { margin-right: 6px; }
.me-name { font-size: 13px; color: #5a6b8c; margin-right: 4px; }

/* ---------- 消息区 ---------- */
.chat-body {
  flex: 1;
  overflow: hidden;
  padding: 20px 0;
}
.msg-list {
  height: 100%;
  overflow-y: auto;
  padding: 8px 28px 24px;
  max-width: 980px;
  margin: 0 auto;
}

.chat-empty {
  max-width: 980px;
  margin: 0 auto;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 0 24px;
}
.empty-icon { font-size: 52px; margin-bottom: 14px; }
.empty-title { font-size: 19px; font-weight: 600; color: #1f2d3d; margin-bottom: 10px; }
.empty-sub { color: #909399; margin-bottom: 20px; }
.empty-feats { display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; margin-bottom: 26px; }
.empty-feats span {
  background: #f2f6fc;
  color: #5a6b8c;
  font-size: 13px;
  padding: 6px 12px;
  border-radius: 14px;
}
.quick-questions { display: flex; gap: 12px; }

.msg-row { display: flex; gap: 12px; margin-bottom: 20px; }
.msg-row.user { flex-direction: row-reverse; }
.avatar {
  width: 38px; height: 38px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  color: #fff; font-size: 15px; font-weight: 600;
}
.msg-row.assistant .avatar {
  background: linear-gradient(135deg, #409eff, #6a5cff);
  box-shadow: 0 3px 8px rgba(90, 92, 255, 0.3);
}
.msg-row.user .avatar {
  background: linear-gradient(135deg, #36cfc9, #67c23a);
  box-shadow: 0 3px 8px rgba(103, 194, 58, 0.3);
}
/* 人工客服：换成橙色调，与 AI 的蓝色区分开 */
.msg-row.agent .avatar {
  background: linear-gradient(135deg, #e6a23c, #f56c6c);
  box-shadow: 0 3px 8px rgba(230, 162, 60, 0.35);
}
/* 系统提示 / 转人工分隔条：居中细字，不参与左右气泡布局 */
.msg-row.system, .msg-row.divider { justify-content: center; }
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
.msg-row.divider .sys-line {
  color: #409eff;
  background: #ecf5ff;
  border: 1px solid #d9ecff;
}
.bubble-wrap { max-width: 72%; display: flex; flex-direction: column; }
.msg-row.user .bubble-wrap { align-items: flex-end; }
.bubble {
  padding: 12px 16px;
  border-radius: 14px;
  font-size: 15px;
  line-height: 1.7;
  color: #1f2d3d;
}
.msg-row.assistant .bubble {
  background: #fff;
  border: 1px solid #eef0f4;
  border-top-left-radius: 4px;
  box-shadow: 0 2px 6px rgba(31, 45, 61, 0.05);
}
.msg-row.user .bubble {
  background: linear-gradient(135deg, #409eff, #3a7afe);
  color: #fff;
  border-top-right-radius: 4px;
}
/* 人工客服气泡：白底橙边，和 AI 的纯白气泡一眼能分开 */
.msg-row.agent .bubble {
  background: #fffdf8;
  border: 1px solid #fbe6c8;
  border-top-left-radius: 4px;
  box-shadow: 0 2px 6px rgba(230, 162, 60, 0.12);
}
.content { white-space: pre-wrap; word-break: break-word; margin: 0; font-family: inherit; }
.msg-row.assistant .content { max-height: 80vh; overflow-y: auto; }


/* ---------- 输入区 ---------- */
.chat-footer {
  flex-shrink: 0;
  background: #fff;
  padding: 12px 28px 20px;
  box-shadow: 0 -1px 6px rgba(31, 45, 61, 0.05);
}
.input-bar {
  max-width: 980px;
  margin: 0 auto;
  display: flex;
  gap: 12px;
  align-items: flex-end; /* 输入框可随内容长高，发送按钮贴底对齐 */
}
/* 多行输入：圆角外观改挂在 textarea 本体上（el-textarea 没有 el-input__wrapper） */
.chat-input :deep(.el-textarea__inner) {
  border-radius: 22px;
  padding: 9px 18px;
  box-shadow: 0 0 0 1px #e4e7ed inset;
  transition: box-shadow 0.2s;
  font-family: inherit;
  font-size: 15px;
  line-height: 1.6;
}
.chat-input :deep(.el-textarea__inner:focus) {
  box-shadow: 0 0 0 1px #409eff inset;
}
.send-btn { height: 40px; padding: 0 26px; font-size: 15px; }
</style>