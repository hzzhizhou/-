<template>
  <el-container class="admin-layout">
    <el-aside width="220px" class="aside">
      <div class="brand">
        <span class="brand-logo">智</span>
        <span class="brand-text">智选后台</span>
      </div>
      <el-menu
        :default-active="$route.path"
        router
        background-color="#1f2740"
        text-color="#b6c0d8"
        active-text-color="#409eff"
        class="side-menu"
      >
        <el-menu-item index="/admin/workbench">
          <span class="menu-dot">💬</span>客服工作台
        </el-menu-item>
        <el-menu-item index="/admin/tickets">
          <span class="menu-dot">📋</span>工单管理
        </el-menu-item>
        <el-menu-item index="/admin/kb">
          <span class="menu-dot">📚</span>知识库管理
        </el-menu-item>
        <el-menu-item index="/admin/orders">
          <span class="menu-dot">📦</span>订单管理
        </el-menu-item>
        <el-menu-item index="/admin/monitor">
          <span class="menu-dot">📊</span>系统监控
        </el-menu-item>
        <el-menu-item index="/admin/users">
          <span class="menu-dot">👤</span>用户管理
        </el-menu-item>
      </el-menu>
      <div class="to-user">
        <div class="me" v-if="userStore.isLogin">
          <span class="me-name">{{ userStore.displayName }}</span>
          <el-tag size="small" :type="userStore.user?.role === 'admin' ? 'warning' : 'info'">
            {{ userStore.user?.role === 'admin' ? '管理员' : '用户' }}
          </el-tag>
        </div>
        <el-button v-if="userStore.isLogin" size="small" text class="link-btn" @click="doLogout">
          退出登录
        </el-button>
        <el-button size="small" text class="link-btn" @click="$router.push('/')">← 回到用户端</el-button>
      </div>
    </el-aside>

    <el-container>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()

// 刷新页面后用本地令牌换回最新用户信息（令牌失效会自动清理并跳登录页）
onMounted(() => userStore.fetchMe())

async function doLogout() {
  await userStore.logout()
  ElMessage.success('已退出登录')
  router.push('/login')
}
</script>

<style scoped>
.admin-layout { height: 100vh; }
.aside { display: flex; flex-direction: column; background: #1f2740; position: relative; }
.brand {
  display: flex; align-items: center; gap: 10px;
  padding: 18px 20px; color: #fff;
}
.brand-logo {
  width: 32px; height: 32px; border-radius: 8px;
  background: linear-gradient(135deg, #409eff, #6a5cff);
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 17px;
}
.brand-text { font-size: 16px; font-weight: 600; }
.side-menu { border-right: none; flex: 1; }
.side-menu :deep(.el-menu-item.is-active) {
  background: rgba(64, 158, 255, 0.12) !important;
  border-right: 3px solid #409eff;
}
.menu-dot { margin-right: 8px; }
.to-user { padding-bottom: 20px; text-align: center; display: flex; flex-direction: column; align-items: center; gap: 6px; }
.to-user .el-button { color: #b6c0d8; }
.me { display: flex; align-items: center; gap: 6px; color: #fff; font-size: 13px; }
.me-name { font-weight: 600; }
.link-btn { margin: 0 !important; }
.main { background: #f2f4f9; padding: 24px; }
</style>