<template>
  <div>
    <el-button type="primary" text @click="load">刷新</el-button>

    <el-card v-if="health" class="card">
      <template #header>系统健康</template>
      <el-descriptions :column="3" border>
        <el-descriptions-item label="状态">
          <el-tag :type="health.status === 'healthy' ? 'success' : 'danger'">
            {{ health.status }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item
          v-for="(v, k) in health.components"
          :key="k"
          :label="k"
        >
          <span :class="String(v).startsWith('ok') ? 'ok' : 'fail'">{{ v }}</span>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card class="card">
      <template #header>Prometheus 指标（关键项）</template>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="http 请求总数">
          <b>{{ metrics.requests }}</b>
        </el-descriptions-item>
        <el-descriptions-item label="请求总耗时(s)">
          <b>{{ metrics.durationSum.toFixed(2) }}</b>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import http from '@/api'

const health = ref(null)
const metrics = ref({ requests: 0, durationSum: 0 })

async function load() {
  try {
    const h = await http.get('/health')
    health.value = h.data
  } catch (e) {
    health.value = { status: 'unavailable: ' + (e?.response?.status || e.message), components: {} }
  }
  try {
    const m = await http.get('/metrics', { responseType: 'text' })
    let requests = 0
    let durationSum = 0
    for (const line of m.data.split('\n')) {
      const rm = line.match(/^http_requests_total\{(.*?)\}\s+(\d+)$/)
      if (rm) requests += Number(rm[2])
      const sm = line.match(/^http_request_duration_seconds_sum\{(.*?)\}\s+([\d.eE+-]+)$/)
      if (sm) durationSum += Number(sm[2])
    }
    metrics.value = { requests, durationSum }
  } catch (e) {
    /* metrics 读取失败忽略 */
  }
}

load()
</script>

<style scoped>
.card { margin-bottom: 16px; }
.ok { color: #67c23a; }
.fail { color: #f56c6c; }
</style>