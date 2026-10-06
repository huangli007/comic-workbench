<template>
  <div class="page">
    <el-page-header @back="() => {}" :content="'漫画工作台'" >
      <template #extra>
        <span class="muted">本地优先 · 漫画生产流水线</span>
      </template>
    </el-page-header>

    <el-row :gutter="12" style="margin-top:16px">
      <el-col :span="5"><el-card shadow="never"><el-statistic title="项目" :value="stats.project_count" /></el-card></el-col>
      <el-col :span="5"><el-card shadow="never"><el-statistic title="分镜格" :value="stats.panel_count" /></el-card></el-col>
      <el-col :span="5"><el-card shadow="never"><el-statistic title="人物与资产" :value="stats.character_count + stats.asset_count" /></el-card></el-col>
      <el-col :span="5"><el-card shadow="never"><el-statistic title="已导出文件" :value="stats.export_count" /></el-card></el-col>
      <el-col :span="4"><el-card shadow="never">
        <el-statistic title="队列任务" :value="stats.jobs_pending + stats.jobs_running" />
        <div class="muted" style="font-size:12px">排队 {{ stats.jobs_pending }} · 执行 {{ stats.jobs_running }}</div>
      </el-card></el-col>
    </el-row>

    <el-card style="margin-top:16px">
      <template #header>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span>项目列表</span>
          <div>
            <el-tag v-for="p in providers" :key="p.name" class="status-tag"
                    :type="p.available ? 'success' : 'info'" effect="plain">
              引擎：{{ label(PROVIDER, p.name) }}{{ p.available ? '（可用）' : '（不可用）' }}
            </el-tag>
            <el-button type="primary" @click="createDialog = true">新建项目</el-button>
          </div>
        </div>
      </template>
      <el-table :data="projects" style="width:100%" @row-click="openProject" row-class-name="row-click">
        <el-table-column prop="name" label="项目名称" min-width="160" />
        <el-table-column label="画风" width="170">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="styleColor(row.style) ? 'success' : 'info'">
              {{ styleLabel(row.style) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="panel_count" label="分镜格数" width="100" />
        <el-table-column label="资产库" width="110">
          <template #default="{ row }">{{ row.asset_count ?? 0 }} 项</template>
        </el-table-column>
        <el-table-column label="已导出格数" width="110">
          <template #default="{ row }">{{ row.exported_count ?? 0 }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="200">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="170">
          <template #default="{ row }">
            <el-button size="small" @click.stop="openProject(row)">打开</el-button>
            <el-button size="small" type="danger" plain @click.stop="removeProject(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!projects.length" description="还没有项目，点右上角「新建项目」开始" />
    </el-card>

    <el-dialog v-model="createDialog" title="新建项目" width="440px">
      <el-form label-width="80px">
        <el-form-item label="项目名称">
          <el-input v-model="newName" placeholder="例如：雨夜 / 我的漫画" />
        </el-form-item>
        <el-form-item label="画风">
          <el-select v-model="newStyle" style="width:100%" filterable>
            <el-option-group v-for="g in styleGroups" :key="g.name" :label="g.name">
              <el-option v-for="s in g.items" :key="s.key" :label="s.label" :value="s.key">
                <span>{{ s.label }}</span>
                <span class="muted" style="float:right;margin-left:8px">{{ s.color ? '彩色' : '黑白' }}</span>
              </el-option>
            </el-option-group>
          </el-select>
          <div class="muted" style="margin-top:4px;line-height:1.4">{{ currentStyleDesc }}</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">取消</el-button>
        <el-button type="primary" @click="doCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { STYLE, COLOR_STYLES, PROVIDER, label } from '../labels'

const router = useRouter()
const projects = ref([])
const providers = ref([])
const stats = ref({ project_count: 0, panel_count: 0, character_count: 0, asset_count: 0,
                    export_count: 0, jobs_pending: 0, jobs_running: 0 })
const styleLib = ref([])
const styleGroups = ref([])
const styleDescMap = ref({})
const createDialog = ref(false)
const newName = ref('')
const newStyle = ref('manhua_color')   // 默认彩色

async function load() {
  projects.value = await api.listProjects()
  providers.value = await api.providers()
  stats.value = await api.stats().catch(() => stats.value)
  const lib = await api.styles()
  styleLib.value = lib.styles || []
  styleGroups.value = (lib.groups || []).map((name) => ({
    name, items: styleLib.value.filter((s) => s.group === name),
  }))
  styleDescMap.value = Object.fromEntries(styleLib.value.map((s) => [s.key, s.desc]))
  if (lib.default) newStyle.value = lib.default
}

function styleLabel(key) {
  return styleLib.value.find((s) => s.key === key)?.label || label(STYLE, key)
}
function styleColor(key) {
  const f = styleLib.value.find((s) => s.key === key)
  return f ? f.color : COLOR_STYLES.includes(key)
}
const currentStyleDesc = computed(() => styleDescMap.value[newStyle.value] || '')

function fmtTime(t) {
  if (!t) return ''
  return new Date(t).toLocaleString('zh-CN')
}

function openProject(row) {
  router.push({ name: 'project', params: { id: row.id } })
}

async function doCreate() {
  if (!newName.value.trim()) return ElMessage.warning('请输入项目名称')
  const p = await api.createProject(newName.value.trim(), newStyle.value)
  createDialog.value = false
  newName.value = ''
  await load()
  openProject(p)
}

async function removeProject(row) {
  try {
    await ElMessageBox.confirm(`确定删除项目「${row.name}」及其全部分镜数据？`, '确认', { type: 'warning' })
  } catch (e) { return }
  await api.deleteProject(row.id)
  ElMessage.success('已删除')
  await load()
}

onMounted(load)
</script>

<style scoped>
:deep(.row-click) { cursor: pointer; }
</style>
