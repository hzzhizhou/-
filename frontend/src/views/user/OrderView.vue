<template>
  <div class="orders-page">
    <!-- 顶部栏：与聊天页保持同一套视觉，方便来回切换 -->
    <header class="orders-header">
      <div class="brand">
        <span class="brand-logo">智</span>
        <span class="brand-title">我的订单</span>
      </div>
      <div class="header-actions">
        <span class="me-name">{{ userStore.displayName }}</span>
        <el-button size="small" text type="primary" @click="goChat">返回智能客服</el-button>
      </div>
    </header>

    <main class="orders-body">
      <div class="card">
        <div class="toolbar">
          <div class="toolbar-left">
            <el-radio-group v-model="status" @change="load">
              <el-radio-button label="">全部</el-radio-button>
              <el-radio-button label="pending">待付款</el-radio-button>
              <el-radio-button label="paid">已付款</el-radio-button>
              <el-radio-button label="shipped">已发货</el-radio-button>
              <el-radio-button label="delivered">已签收</el-radio-button>
              <el-radio-button label="refunding">退款中</el-radio-button>
            </el-radio-group>
          </div>
          <span class="tip">
            订单信息仅供查询；退货请点「申请退货」，由客服按工单流程办理
          </span>
        </div>

        <el-table :data="shown" border stripe v-loading="loading" empty-text="暂无订单">
          <el-table-column prop="order_id" label="订单号" width="180" />
          <el-table-column prop="product" label="商品" min-width="180" show-overflow-tooltip />
          <el-table-column label="金额" width="110">
            <template #default="{ row }">¥{{ Number(row.amount).toFixed(2) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)">{{ statusCN(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="物流" min-width="240">
            <template #default="{ row }">
              <div v-if="row.logistics || row.tracking" class="logi">
                <div v-if="row.logistics">{{ row.logistics }}</div>
                <div v-if="row.tracking" class="logi-track">{{ row.tracking }}</div>
              </div>
              <span v-else class="muted">暂无物流信息</span>
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="下单时间" width="170" />
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <!-- 只能发起退货，不能直接改单：付款/取消/退款状态由后台按流程更新 -->
              <el-tooltip :disabled="canReturn(row)" :content="returnTip(row)" placement="top">
                <span>
                  <el-button size="small" type="primary" plain
                             :disabled="!canReturn(row)" @click="applyReturn(row)">
                    申请退货
                  </el-button>
                </span>
              </el-tooltip>
            </template>
          </el-table-column>
        </el-table>

        <p class="foot-tip">
          共 {{ total }} 张订单，当前显示 {{ shown.length }} 张。
          找不到要退的订单？也可以在对话里直接说「我要退货，订单号 SO…」
        </p>
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import http from '@/api'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()

const items = ref([])
const total = ref(0)
const status = ref('')
const loading = ref(false)

const STATUS_CN = {
  pending: '待付款', paid: '已付款', shipped: '已发货',
  delivered: '已签收', refunding: '退款中', refunded: '已退款', cancelled: '已取消',
}

// 可申请退货的状态：已付款/已发货/已签收。待付款无货可退，
// 退款中/已退款/已取消重复申请只会白建工单
const RETURNABLE = ['paid', 'shipped', 'delivered']
const RETURN_TIP = {
  pending: '订单尚未付款，无需退货',
  refunding: '该订单已在退款处理中',
  refunded: '该订单已退款完成',
  cancelled: '该订单已取消',
}

function statusCN(s) { return STATUS_CN[s] || s }
function statusType(s) {
  return { pending: 'info', paid: 'primary', shipped: 'warning',
           delivered: 'success', refunding: 'danger', refunded: 'info',
           cancelled: 'info' }[s] || 'info'
}
function canReturn(row) { return RETURNABLE.includes(row.status) }
function returnTip(row) { return RETURN_TIP[row.status] || '当前状态不支持退货' }

// 状态筛选放在前端做：接口一次返回本人全部订单，数量很小，不必为筛选再跑一趟后台
const shown = computed(() =>
  status.value ? items.value.filter((o) => o.status === status.value) : items.value
)

function goChat() { router.push('/') }

/**
 * 发起退货：不在订单表上直接改状态，而是带着订单号跳回对话页，
 * 由 Agent 的退货流程（归属校验 → 建退货工单 → 人工按阶段推进）来办理
 */
function applyReturn(row) {
  router.push({ path: '/', query: { ask: `我要退货，订单号 ${row.order_id}` } })
}

async function load() {
  loading.value = true
  try {
    const { data } = await http.get('/my/orders')
    items.value = data.items || []
    total.value = data.total || 0
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '订单加载失败，请稍后再试')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  userStore.fetchMe()
})
</script>

<style scoped>
.orders-page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, #eef2fb 0%, #f7f8fc 100%);
}

.orders-header {
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
.header-actions { display: flex; align-items: center; gap: 8px; }
.me-name { font-size: 13px; color: #5a6b8c; }

.orders-body { flex: 1; padding: 20px 28px 32px; }
.card {
  max-width: 1180px;
  margin: 0 auto;
  background: #fff;
  border-radius: 12px;
  padding: 18px 20px 20px;
  box-shadow: 0 2px 10px rgba(31, 45, 61, 0.06);
}

.toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.toolbar-left { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.toolbar-left :deep(.el-radio-button__inner) { padding: 7px 14px; }
.tip { color: #909399; font-size: 13px; }

.logi { line-height: 1.5; }
.logi-track { color: #909399; font-size: 12px; }
.muted { color: #c0c4cc; font-size: 13px; }
.foot-tip { margin: 14px 0 0; color: #909399; font-size: 13px; }
</style>
