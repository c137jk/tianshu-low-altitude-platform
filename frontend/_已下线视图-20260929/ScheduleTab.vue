<template>
  <div>
    <div style="margin-bottom:12px;text-align:right">
      <el-button type="primary" size="small" @click="show=true">新增排班</el-button>
    </div>
    <el-table :data="list" stripe v-loading="loading">
      <el-table-column prop="shift_date" label="日期" width="120" />
      <el-table-column prop="user_name" label="人员" width="120" />
      <el-table-column label="班次" width="100">
        <template #default="{ row }">{{ shiftName[row.shift_type] }}</template>
      </el-table-column>
      <el-table-column prop="start_time" label="开始" width="170" />
      <el-table-column prop="end_time" label="结束" width="170" />
      <el-table-column prop="responsible_area" label="责任区域" />
      <el-table-column label="操作" width="80">
        <template #default="{ row }">
          <el-button link type="danger" size="small" @click="del(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-dialog v-model="show" title="新增排班" width="460px">
      <el-form :model="form" label-width="84px">
        <el-form-item label="人员">
          <el-select v-model="form.userId" filterable style="width:100%">
            <el-option v-for="p in pilots" :key="p.userId" :label="`${p.realName || p.username}（${p.roleNames || '人员'}）`"
              :value="p.userId" />
          </el-select>
        </el-form-item>
        <el-form-item label="日期">
          <el-date-picker v-model="form.date" type="date" value-format="YYYY-MM-DD" style="width:100%" />
        </el-form-item>
        <el-form-item label="班次">
          <el-radio-group v-model="form.shiftType">
            <el-radio :label="1">早班</el-radio>
            <el-radio :label="2">中班</el-radio>
            <el-radio :label="3">晚班</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="责任区域">
          <el-input v-model="form.area" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="show=false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/utils/request'
import { listPersonnelApi, type PersonnelVO } from '@/api/resource'
const list = ref<any[]>([])
const loading = ref(true)
const pilots = ref<PersonnelVO[]>([])
const shiftName: Record<number, string> = { 1: '早班', 2: '中班', 3: '晚班' }
// 班次 → 起止时刻。晚班跨零点：结束时间落在次日，否则存成"结束早于开始"，
// 人员台账里"今日是否在岗"按 shift_date=今天 查，跨零点这条照样算在岗。
const SHIFT_HOURS: Record<number, [string, string, boolean]> = {
  1: ['07:00', '15:00', false], 2: ['15:00', '23:00', false], 3: ['23:00', '07:00', true],
}
const show = ref(false)
const form = reactive<any>({ userId: null, date: '', shiftType: 1, area: '' })
async function load() {
  loading.value = true
  try { list.value = await request('/aux/schedules') } finally { loading.value = false }
}
async function submit() {
  if (!form.date) return ElMessage.warning('请选择排班日期')
  const h = SHIFT_HOURS[form.shiftType]
  if (!h) return ElMessage.warning('请选择班次')
  let endDay = form.date
  if (h[2]) {
    const d = new Date(`${form.date}T00:00:00`)
    d.setDate(d.getDate() + 1)
    endDay = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  }
  await request('/aux/schedules', {
    method: 'POST',
    data: { ...form, start: `${form.date} ${h[0]}:00`, end: `${endDay} ${h[1]}:00` } })
  ElMessage.success('已保存'); show.value = false; load()
}
async function del(row: any) {
  await ElMessageBox.confirm('确认删除该排班？', '提示', { type: 'warning' })
  await request(`/aux/schedules/${row.id}`, { method: 'DELETE' })
  load()
}
onMounted(async () => {
  // 人员下拉走资源域花名册：平台运维有 resource:schedule 但没有 system:user，
  // 原先调 /system/users 在运维账号下直接 403，新增排班点开就是空的。
  pilots.value = (await listPersonnelApi()).list
  load()
})
</script>
