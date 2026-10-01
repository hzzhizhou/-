<template>
  <div>
    <div class="toolbar">
      <el-radio-group v-model="status" @change="load">
        <el-radio-button label="">全部</el-radio-button>
        <el-radio-button label="pending">待付款</el-radio-button>
        <el-radio-button label="paid">已付款</el-radio-button>
        <el-radio-button label="shipped">已发货</el-radio-button>
        <el-radio-button label="delivered">已签收</el-radio-button>
        <el-radio-button label="refunding">退款中</el-radio-button>
        <el-radio-button label="refunded">已退款</el-radio-button>
        <el-radio-button label="cancelled">已取消</el-radio-button>
      </el-radio-group>
      <span class="tip">订单为业务数据（客服只读），此处供后台核对与展示</span>
    </div>

    <el-table :data="items" border stripe v-loading="loading">
      <el-table-column prop="order_id" label="订单号" width="190" />
      <el-table-column prop="customer" label="客户" width="110" />
      <el-table-column prop="product" label="商品" min-width="180" />
      <el-table-column label="金额" width="100">
        <template #default="{ row }">¥{{ Number(row.amount).toFixed(2) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)">{{ statusCN(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="logistics" label="物流" min-width="160" show-overflow-tooltip />
      <el-table-column label="归属账号" width="150" show-overflow-tooltip>
        <template #default="{ row }">
          <!-- 客服侧按该字段做归属校验：非本人订单不予查询/办理 -->
          <span v-if="row.user_id">{{ userLabel(row.user_id) }}</span>
          <el-tag v-else size="small" type="info">未绑定</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="下单时间" width="170" />
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-dropdown @command="(v) => changeStatus(row, v)">
            <el-button size="small">更新状态</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="paid">已付款</el-dropdown-item>
                <el-dropdown-item command="shipped">已发货</el-dropdown-item>
                <el-dropdown-item command="delivered">已签收</el-dropdown-item>
                <el-dropdown-item command="refunding">退款中</el-dropdown-item>
                <el-dropdown-item command="refunded">已退款</el-dropdown-item>
                <el-dropdown-item command="cancelled">已取消</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
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
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import http from '@/api'

const status = ref('')
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)
// user_id → 可读账号名：订单表只存归属账号标识，靠账号列表翻译成人看得懂的名字
const userNames = ref({})

const STATUS_CN = {
  pending: '待付款', paid: '已付款', shipped: '已发货',
  delivered: '已签收', refunding: '退款中', refunded: '已退款', cancelled: '已取消',
}

function statusCN(s) { return STATUS_CN[s] || s }
function statusType(s) {
  return { pending: 'info', paid: 'primary', shipped: 'warning',
           delivered: 'success', refunding: 'danger', refunded: 'info',
           cancelled: 'info' }[s] || 'info'
}

async function load() {
  loading.value = true
  try {
    const { data } = await http.get('/orders', { params: {
      status: status.value, page: page.value, page_size: pageSize.value,
    } })
    items.value = data.items || []
    total.value = data.total || 0
  } finally {
    loading.value = false
  }
}

function onPageChange(p) { page.value = p; load() }
function onSizeChange(s) { pageSize.value = s; page.value = 1; load() }

async function changeStatus(row, v) {
  try {
    await http.patch(`/orders/${row.order_id}`, { status: v })
    ElMessage.success(`订单 ${row.order_id} 状态已更新为 ${statusCN(v)}`)
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '更新失败')
  }
}

async function loadUsers() {
  try {
    const { data } = await http.get('/users', { params: { page: 1, page_size: 100 } })
    userNames.value = Object.fromEntries(
      (data.items || []).map((u) => [u.user_id, `${u.nickname || u.username}（${u.username}）`])
    )
  } catch {
    // 账号列表加载失败不影响订单列表本身展示（归属列退化为显示账号标识）
  }
}

function userLabel(id) { return userNames.value[id] || id }

onMounted(() => { load(); loadUsers() })
</script>

<style scoped>
.toolbar { margin-bottom: 12px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.tip { color: #909399; font-size: 13px; }
.pager { margin-top: 12px; display: flex; justify-content: flex-end; }
</style>