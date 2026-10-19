<template>
  <el-card shadow="never">
    <template #header>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <b>常用地址</b>
        <el-button type="primary" size="small" @click="open()">新增地址</el-button>
      </div>
    </template>
    <el-table :data="list" stripe v-loading="loading">
      <el-table-column prop="contact_name" label="联系人" width="120" />
      <el-table-column prop="contact_phone" label="手机号" width="140" />
      <el-table-column prop="address" label="地址" />
      <el-table-column prop="label" label="标签" width="100" />
      <el-table-column label="默认" width="90">
        <template #default="{ row }">
          <el-tag v-if="row.is_default" type="success" size="small">默认</el-tag>
          <el-button v-else link size="small" @click="setDefault(row)">设为默认</el-button>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="90">
        <template #default="{ row }">
          <el-button link type="danger" size="small" @click="del(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="show" title="新增地址" width="460px">
      <el-form :model="form" label-width="72px">
        <el-form-item label="联系人"><el-input v-model="form.contactName" /></el-form-item>
        <el-form-item label="手机号"><el-input v-model="form.contactPhone" /></el-form-item>
        <el-form-item label="地址" required>
          <el-input v-model="form.address" type="textarea" :rows="2"
            placeholder="可在下单页地图搜索后复制，或直接填写详细地址" />
        </el-form-item>
        <el-form-item label="标签">
          <el-select v-model="form.label" style="width:100%">
            <el-option label="家" value="家" />
            <el-option label="公司" value="公司" />
            <el-option label="其他" value="其他" />
          </el-select>
        </el-form-item>
        <el-form-item label="设为默认"><el-switch v-model="form.isDefault" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="show=false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>
<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import request from '@/utils/request'
const list = ref<any[]>([])
const loading = ref(true)
const show = ref(false)
const form = reactive<any>({ contactName: '', contactPhone: '', address: '', label: '其他', isDefault: false })

async function load() {
  loading.value = true
  try { list.value = await request('/aux/addresses') } finally { loading.value = false }
}
function open() {
  Object.assign(form, { contactName: '', contactPhone: '', address: '', label: '其他', isDefault: false })
  show.value = true
}
async function save() {
  if (!form.address) return ElMessage.warning('请填写地址')
  await request('/aux/addresses', { method: 'POST', data: { ...form } })
  ElMessage.success('已保存'); show.value = false; load()
}
async function setDefault(row: any) {
  await request(`/aux/addresses/${row.id}/default`, { method: 'PUT' })
  load()
}
async function del(row: any) {
  await ElMessageBox.confirm('确认删除该地址？', '提示', { type: 'warning' })
  await request(`/aux/addresses/${row.id}`, { method: 'DELETE' })
  load()
}
onMounted(load)
</script>
