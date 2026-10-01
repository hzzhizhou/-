<template>
  <div>
    <div class="toolbar">
      <el-input
        v-model="keyword"
        placeholder="按用户名 / 昵称搜索"
        clearable
        style="width: 240px"
        @keyup.enter="onSearch"
        @clear="onSearch"
      />
      <el-button type="primary" plain @click="onSearch">搜索</el-button>
      <span class="total">共 {{ total }} 个账号</span>
    </div>

    <el-table :data="items" border stripe v-loading="loading">
      <el-table-column prop="username" label="用户名" width="150" />
      <el-table-column prop="nickname" label="昵称" width="150" />
      <el-table-column prop="role" label="角色" width="100">
        <template #default="{ row }">
          <el-tag :type="row.role === 'admin' ? 'warning' : 'info'">
            {{ row.role === 'admin' ? '管理员' : '普通用户' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 'active' ? 'success' : 'danger'">
            {{ row.status === 'active' ? '正常' : '已禁用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="注册时间" width="180" />
      <el-table-column prop="last_login_at" label="最近登录" width="180">
        <template #default="{ row }">{{ row.last_login_at || '从未登录' }}</template>
      </el-table-column>
      <el-table-column label="操作" min-width="180">
        <template #default="{ row }">
          <el-button
            size="small"
            text
            :type="row.status === 'active' ? 'warning' : 'success'"
            :disabled="row.user_id === currentUserId"
            @click="toggleStatus(row)"
          >
            {{ row.status === 'active' ? '禁用' : '启用' }}
          </el-button>
          <el-button
            size="small"
            text
            type="danger"
            :disabled="row.user_id === currentUserId"
            @click="removeUser(row)"
          >
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination
        background
        layout="total, prev, pager, next, sizes"
        :total="total"
        :page-size="pageSize"
        :current-page="page"
        :page-sizes="[10, 20, 50]"
        @current-change="onPageChange"
        @size-change="onSizeChange"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '@/api'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const currentUserId = computed(() => userStore.user?.user_id || '')

const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const keyword = ref('')
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const { data } = await http.get('/users', {
      params: { keyword: keyword.value, page: page.value, page_size: pageSize.value },
    })
    items.value = data.items
    total.value = data.total
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '加载用户列表失败')
  } finally {
    loading.value = false
  }
}

function onSearch() {
  page.value = 1
  load()
}

function onPageChange(p) {
  page.value = p
  load()
}

function onSizeChange(s) {
  pageSize.value = s
  page.value = 1
  load()
}

async function toggleStatus(row) {
  const next = row.status === 'active' ? 'disabled' : 'active'
  try {
    await http.patch(`/users/${row.user_id}`, { status: next })
    ElMessage.success(next === 'disabled' ? '已禁用该账号' : '已启用该账号')
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '操作失败')
  }
}

async function removeUser(row) {
  try {
    await ElMessageBox.confirm(`确定删除账号「${row.username}」吗？`, '删除确认', {
      type: 'warning',
    })
  } catch (e) {
    return
  }
  try {
    await http.delete(`/users/${row.user_id}`)
    ElMessage.success('已删除')
    if (items.value.length === 1 && page.value > 1) page.value -= 1
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '删除失败')
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.total { color: #909399; font-size: 13px; }
.pager { margin-top: 16px; display: flex; justify-content: flex-end; }
</style>