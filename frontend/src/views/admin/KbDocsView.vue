<template>
  <div>
    <div class="toolbar">
      <el-upload
        :show-file-list="false"
        :http-request="doUpload"
        accept=".txt,.pdf,.docx,.xlsx,.md"
      >
        <el-button type="primary">上传文档</el-button>
      </el-upload>
      <span class="tip">支持 .txt/.pdf/.docx/.xlsx/.md，上传后自动增量入库</span>
    </div>

    <el-table :data="docs" border stripe v-loading="loading" class="doc-table" @row-click="viewChunks">
      <el-table-column prop="file_name" label="文件名" min-width="200">
        <template #default="{ row }">
          <span class="file-name">{{ row.file_name }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="file_path" label="路径" min-width="260" show-overflow-tooltip />
      <el-table-column prop="chunk_count" label="分块数" width="90">
        <template #default="{ row }">
          <el-tag size="small" type="info" round>{{ row.chunk_count }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="last_indexed" label="入库时间" width="180" />
      <el-table-column label="操作" width="150">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click.stop="viewChunks(row)">查看</el-button>
          <el-button size="small" text type="danger" @click.stop="confirmDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="table-tip">点击任意一行查看该文档的分块内容</div>

    <el-drawer v-model="drawer" :title="'分块内容 · ' + (currentDoc?.file_name || '')" size="50%">
      <el-collapse v-model="openedChunks">
        <el-collapse-item v-for="c in chunks" :key="c.chunk_id" :name="c.chunk_id">
          <template #title>#{{ c.chunk_index }} · {{ c.chunk_id }}</template>
          <pre class="chunk-text">{{ c.content || '（正文读取失败/为空）' }}</pre>
        </el-collapse-item>
      </el-collapse>
      <el-empty v-if="!chunks.length" description="暂无分块" />
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '@/api'

const docs = ref([])
const chunks = ref([])
const loading = ref(false)
const drawer = ref(false)
const currentDoc = ref(null)
const openedChunks = ref([])

async function load() {
  loading.value = true
  try {
    const { data } = await http.get('/knowledge/documents')
    docs.value = data.documents || []
  } finally {
    loading.value = false
  }
}

async function doUpload({ file }) {
  const form = new FormData()
  form.append('file', file)
  try {
    const { data } = await http.post('/knowledge/upload', form)
    ElMessage.success(`已上传 ${data.filename}，后台入库中…`)
    await waitIngestDone(data.filename)
    ElMessage.success(`入库完成`)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '上传失败')
  }
}

// 后台入库是异步的（耗时通常 3~10s），轮询直到列表出现该文档，或超时
async function waitIngestDone(filename, maxTries = 15) {
  for (let i = 0; i < maxTries; i++) {
    await load()
    if (docs.value.some((d) => d.file_name === filename)) return
    await new Promise((r) => setTimeout(r, 1000))
  }
}

async function viewChunks(row) {
  currentDoc.value = row
  openedChunks.value = []
  drawer.value = true
  const { data } = await http.get(`/knowledge/documents/${row.id}/chunks`)
  chunks.value = data.chunks || []
}

async function confirmDelete(row) {
  try {
    await ElMessageBox.confirm(
      `确认删除「${row.file_name}」吗？\n将同时清除向量库分块、数据库记录及源文件，且不可恢复。`,
      `删除文档 · ${row.file_name}`,
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return // 用户取消
  }
  try {
    const { data } = await http.delete(`/knowledge/documents/${row.id}`)
    ElMessage.success(`已删除${data.file_removed ? '' : '（源文件未能删除）'}`)
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '删除失败')
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar { margin-bottom: 12px; display: flex; align-items: center; gap: 12px; }
.tip { color: #909399; font-size: 13px; }
.chunk-text { white-space: pre-wrap; word-break: break-word; }
.doc-table :deep(.el-table__row) { cursor: pointer; }
.file-name { font-weight: 500; }
.table-tip { margin-top: 8px; color: #909399; font-size: 12px; }
</style>