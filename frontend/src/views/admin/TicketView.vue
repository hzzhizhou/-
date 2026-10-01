<template>
  <div>
    <div class="toolbar">
      <el-radio-group v-model="status" @change="onStatusChange">
        <el-radio-button label="">全部</el-radio-button>
        <el-radio-button label="open">待处理</el-radio-button>
        <el-radio-button label="processing">处理中</el-radio-button>
        <el-radio-button label="resolved">已解决</el-radio-button>
        <el-radio-button label="closed">已关闭</el-radio-button>
      </el-radio-group>
    </div>

    <el-table :data="items" border stripe v-loading="loading">
      <el-table-column prop="ticket_id" label="工单号" width="190" />
      <el-table-column prop="order_id" label="订单号" width="150">
        <template #default="{ row }">{{ row.order_id || '' }}</template>
      </el-table-column>
      <el-table-column prop="category" label="分类" width="90">
        <template #default="{ row }">
          <el-tag :type="categoryType(row.category)">{{ categoryCN(row.category) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="priority" label="优先级" width="90" />
      <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
      <el-table-column prop="created_at" label="创建时间" width="170" />
      <el-table-column prop="stage" label="处理环节" width="160">
        <template #default="{ row }">
          <span v-if="row.stage">{{ stageCN(row.stage) }}</span>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)">{{ statusCN(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click.stop="openDetail(row)">详情</el-button>
          <el-dropdown @command="(v) => changeStatus(row, v)">
            <el-button size="small">更新状态</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="processing">处理中</el-dropdown-item>
                <el-dropdown-item command="resolved">已解决</el-dropdown-item>
                <el-dropdown-item command="closed">已关闭</el-dropdown-item>
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

    <!-- 工单详情抽屉 -->
    <el-drawer v-model="detailOpen" size="46%" :title="'工单详情 · ' + (detail?.ticket_id || '')">
      <template v-if="detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="分类">
            <el-tag :type="categoryType(detail.category)">{{ categoryCN(detail.category) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="优先级">
            <el-tag>{{ detail.priority }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusType(detail.status)">{{ statusCN(detail.status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="当前环节">
            <el-tag v-if="detail.stage" type="primary">{{ stageCN(detail.stage) }}</el-tag>
            <span v-else>无（非退货/退款类工单）</span>
          </el-descriptions-item>
          <el-descriptions-item label="归属账号">{{ detail.user_id }}</el-descriptions-item>
          <el-descriptions-item label="订单号">{{ detail.order_id || '' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间" :span="2">{{ detail.created_at }}</el-descriptions-item>
          <el-descriptions-item label="问题描述" :span="2">
            <pre class="desc">{{ detail.description }}</pre>
          </el-descriptions-item>
        </el-descriptions>

        <!-- 业务环节流转轨迹（用户侧查询进度时看到的同一条轨迹） -->
        <div class="note-box" v-if="detail.stage_history && detail.stage_history.length">
          <h4>环节流转轨迹</h4>
          <el-timeline>
            <el-timeline-item
              v-for="(h, i) in detail.stage_history"
              :key="i"
              :timestamp="h.at"
              :type="i === detail.stage_history.length - 1 ? 'primary' : 'success'"
            >
              {{ stageCN(h.stage) }}
            </el-timeline-item>
          </el-timeline>
        </div>

        <div class="note-box" v-if="detail.stage">
          <h4>推进业务环节</h4>
          <div class="stage-row">
            <el-select v-model="drStage" style="flex: 1">
              <el-option
                v-for="s in stageOptions"
                :key="s.key"
                :label="s.cn"
                :value="s.key"
              />
            </el-select>
            <el-button :disabled="!nextStageKey" @click="advanceStage">推进到下一环节</el-button>
          </div>
          <p class="stage-tip">
            推进环节后，工单状态与订单退款状态自动联动，用户查询“退货进度”即可看到最新环节。
          </p>
        </div>

        <div class="note-box">
          <h4>处理备注</h4>
          <el-input
            type="textarea"
            :rows="4"
            v-model="drNote"
            placeholder="记录处理人员、处理方式、答复结论等（供后续复盘）"
          />
        </div>

        <div class="note-box">
          <h4>更新状态</h4>
          <el-select v-model="drStatus">
            <el-option label="待处理" value="open" />
            <el-option label="处理中" value="processing" />
            <el-option label="已解决" value="resolved" />
            <el-option label="已关闭" value="closed" />
          </el-select>
        </div>

        <div class="drawer-footer">
          <el-button @click="detailOpen = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="saveDetail">保存</el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import http from '@/api'

const status = ref('')
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const loading = ref(false)

function statusType(s) {
  return { open: 'danger', processing: 'warning', resolved: 'success', closed: 'info' }[s] || 'info'
}
const STATUS_CN = { open: '待处理', processing: '处理中', resolved: '已解决', closed: '已关闭' }
function statusCN(s) { return STATUS_CN[s] || s }

const CATEGORY_CN = {
  complaint: '投诉', return: '退货', refund: '退款', consult: '咨询',
  technical: '技术', logistics: '物流', other: '其他',
}
function categoryCN(c) { return CATEGORY_CN[c] || c }
function categoryType(c) {
  return { complaint: 'danger', return: 'warning', refund: 'warning',
           logistics: 'primary', technical: 'info', consult: 'info', other: 'info' }[c] || 'info'
}

// 退货/退款业务环节（顺序流转，与后端 reply_templates.RETURN_STAGES 一致）
const RETURN_STAGES = [
  { key: 'submitted', cn: '提交申请（待商家审核）' },
  { key: 'approved', cn: '商家审核通过（待您寄回）' },
  { key: 'shipped', cn: '商品已寄回（待商家收货）' },
  { key: 'received', cn: '商家已收货（验收中）' },
  { key: 'refunding', cn: '退款已发起（回退中）' },
  { key: 'completed', cn: '退款已到账（已办结）' },
]
const STAGE_EXCEPTION_CN = { rejected: '审核未通过' }
const STAGE_CN = Object.fromEntries(
  RETURN_STAGES.map((s) => [s.key, s.cn]).concat(Object.entries(STAGE_EXCEPTION_CN)),
)
const stageOptions = RETURN_STAGES.concat(
  Object.entries(STAGE_EXCEPTION_CN).map(([key, cn]) => ({ key, cn })),
)
function stageCN(s) { return STAGE_CN[s] || s }

async function load() {
  loading.value = true
  try {
    const { data } = await http.get('/tickets', {
      params: { status: status.value, page: page.value, page_size: pageSize.value },
    })
    items.value = data.items || []
    total.value = data.total || 0
  } finally {
    loading.value = false
  }
}

// 切换状态筛选回到第 1 页
function onStatusChange() {
  page.value = 1
  load()
}
function onPageChange(p) {
  page.value = p
  load()
}
function onSizeChange(size) {
  pageSize.value = size
  page.value = 1
  load()
}

async function changeStatus(row, v) {
  await http.patch(`/tickets/${row.ticket_id}`, { status: v })
  ElMessage.success(`已更新为 ${v}`)
  load()
}

const detailOpen = ref(false)
const detail = ref(null)
const drNote = ref('')
const drStatus = ref('')
const drStage = ref('')
const saving = ref(false)

// 下一个环节（已办结/审核未通过时无后续环节，按钮置灰）
const nextStageKey = computed(() => {
  const i = RETURN_STAGES.findIndex((s) => s.key === detail.value?.stage)
  return i >= 0 && i < RETURN_STAGES.length - 1 ? RETURN_STAGES[i + 1].key : ''
})

async function openDetail(row) {
  detail.value = null
  detailOpen.value = true
  const { data } = await http.get(`/tickets/${row.ticket_id}`)
  detail.value = data
  drNote.value = data.note || ''
  drStatus.value = data.status
  drStage.value = data.stage || ''
}

async function saveDetail() {
  if (!detail.value) return
  // 只提交真正改动过的字段：仅推环节时不显式传状态，交由后端按环节自动联动
  const payload = {}
  if (drStatus.value !== detail.value.status) payload.status = drStatus.value
  if (drNote.value !== (detail.value.note || '')) payload.note = drNote.value
  if (drStage.value !== (detail.value.stage || '')) payload.stage = drStage.value
  if (!Object.keys(payload).length) {
    ElMessage.info('没有需要保存的修改')
    return
  }
  saving.value = true
  try {
    const { data } = await http.patch(`/tickets/${detail.value.ticket_id}`, payload)
    ElMessage.success('已保存')
    detail.value = data
    drNote.value = data.note || ''
    drStatus.value = data.status
    drStage.value = data.stage || ''
    load() // 刷新列表状态
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

// 一键推进到下一环节（人工客服最常用操作）
async function advanceStage() {
  if (!detail.value || !nextStageKey.value) return
  drStage.value = nextStageKey.value
  await saveDetail()
}

onMounted(load)
</script>

<style scoped>
.toolbar { margin-bottom: 12px; }
.pager { margin-top: 12px; display: flex; justify-content: flex-end; }
.desc { white-space: pre-wrap; word-break: break-word; margin: 0; font-family: inherit; color: #606266; }
.note-box { margin-top: 16px; }
.note-box h4 { margin: 0 0 8px; color: #303133; font-size: 14px; }
.stage-row { display: flex; gap: 8px; }
.stage-tip { margin: 8px 0 0; color: #909399; font-size: 12px; line-height: 1.6; }
.drawer-footer { margin-top: 20px; display: flex; justify-content: flex-end; gap: 8px; }
</style>