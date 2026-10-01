import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import ChatView from '../views/user/ChatView.vue'

const TOKEN_KEY = 'auth_token'
const USER_KEY = 'auth_user'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    // 用户端：智能对话（需登录后才能对话）
    { path: '/', name: 'chat', component: ChatView, meta: { requiresAuth: true } },
    // 用户端：我的订单（只读，售后入口）——客服侧对订单只有查询权限，改单走工单
    {
      path: '/my/orders',
      name: 'my-orders',
      component: () => import('@/views/user/OrderView.vue'),
      meta: { requiresAuth: true },
    },
    // 登录 / 注册
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue') },
    // 管理端：布局 + 子页面（需登录，且仅管理员）
    {
      path: '/admin',
      component: () => import('@/views/admin/AdminLayout.vue'),
      meta: { requiresAuth: true, requiresAdmin: true },
      children: [
        { path: 'workbench', name: 'workbench', component: () => import('@/views/admin/WorkbenchView.vue') },
        { path: 'tickets', name: 'tickets', component: () => import('@/views/admin/TicketView.vue') },
        { path: 'orders', name: 'orders', component: () => import('@/views/admin/OrderView.vue') },
        { path: 'kb', name: 'kb', component: () => import('@/views/admin/KbDocsView.vue') },
        { path: 'users', name: 'users', component: () => import('@/views/admin/UserView.vue') },
        { path: 'monitor', name: 'monitor', component: () => import('@/views/admin/MonitorView.vue') },
        { path: '', redirect: '/admin/tickets' },
      ],
    },
  ],
})

function currentUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null')
  } catch (e) {
    return null
  }
}

// 路由守卫：后台需登录且仅管理员；已登录再访问登录页则按角色送回对应首页
router.beforeEach((to) => {
  const user = currentUser()
  const logged = !!localStorage.getItem(TOKEN_KEY) && !!user
  if (to.meta.requiresAuth && !logged) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.meta.requiresAdmin && user?.role !== 'admin') {
    ElMessage.warning('管理后台仅管理员可访问')
    return { path: '/' }
  }
  if (to.path === '/login' && logged) {
    return { path: user?.role === 'admin' ? '/admin/tickets' : '/' }
  }
})

export default router