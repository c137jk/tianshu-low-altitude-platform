<template>
  <div>
    <h2 class="title">个人中心</h2>
    <el-row :gutter="16">
      <el-col :span="14">
        <div class="ts-card">
          <h3>基本资料</h3>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="账号">{{ info?.username }}</el-descriptions-item>
            <el-descriptions-item label="姓名">{{ info?.realName }}</el-descriptions-item>
            <el-descriptions-item label="客户类型">{{ info?.userType === 2 ? '企业客户' : '个人客户' }}</el-descriptions-item>
            <el-descriptions-item label="所属商家">{{ info?.merchantName || '—' }}</el-descriptions-item>
            <el-descriptions-item label="实名认证">
              <el-tag :type="realnameTag.type" size="small">{{ realnameTag.name }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="角色">
              <el-tag v-for="r in info?.roles" :key="r" size="small" style="margin-right:6px">{{ r }}</el-tag>
            </el-descriptions-item>
          </el-descriptions>
          <el-button type="primary" plain style="margin-top:16px" @click="dialogVisible = true">修改密码</el-button>
          <el-button v-if="(info?.realnameStatus ?? 0) === 0" type="success" plain style="margin:16px 0 0 8px"
            @click="rnShow = true">申请实名认证</el-button>
        </div>
      </el-col>
      <el-col :span="10">
        <div class="ts-card tips">
          <h3>说明</h3>
          <p>· 实名认证信息来自平台审核，演示账号已认证。</p>
          <p>· 地址簿、消息、评价投诉已在顶部导航开放。</p>
          <p>· 修改密码后需用新密码重新登录。</p>
        </div>
      </el-col>
    </el-row>

    <el-dialog v-model="rnShow" title="实名认证" width="440px">
      <el-form :model="rn" label-width="92px">
        <el-form-item label="客户类型">
          <el-radio-group v-model="rn.customerType">
            <el-radio :label="1">个人客户</el-radio>
            <el-radio :label="2">企业客户</el-radio>
          </el-radio-group>
        </el-form-item>
        <template v-if="rn.customerType === 2">
          <el-form-item label="企业名称"><el-input v-model="rn.enterpriseName" /></el-form-item>
          <el-form-item label="信用代码"><el-input v-model="rn.creditCode" placeholder="18位统一社会信用代码" /></el-form-item>
        </template>
        <el-form-item v-else label="说明"><span style="color:#909399;font-size:12.5px">提交后平台将在1个工作日内完成审核。</span></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rnShow=false">取消</el-button>
        <el-button type="primary" @click="rnSubmit">提交申请</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="dialogVisible" title="修改密码" width="400px">
      <el-form :model="pwd" label-width="90px">
        <el-form-item label="旧密码">
          <el-input v-model="pwd.oldPassword" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="pwd.newPassword" type="password" show-password placeholder="6~32 位" />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input v-model="pwd.confirm" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { changePasswordApi } from '@/api/auth'
import { resetDynamicRoutes } from '@/router'

const userStore = useUserStore()
const router = useRouter()
const info = computed(() => userStore.userInfo)
const realnameTag = computed(() => {
  const m: Record<number, { name: string; type: 'success' | 'warning' | 'info' | 'danger' }> = {
    0: { name: '未认证', type: 'info' }, 1: { name: '审核中', type: 'warning' },
    2: { name: '已认证', type: 'success' }, 3: { name: '已驳回', type: 'danger' },
  }
  return m[info.value?.realnameStatus ?? 0]
})

import request from '@/utils/request'
const rnShow = ref(false)
const rn = reactive<any>({ customerType: 1, enterpriseName: '', creditCode: '' })
async function rnSubmit() {
  await request('/aux/realname', { method: 'POST', data: { ...rn } })
  ElMessage.success('认证申请已提交')
  rnShow.value = false
  // 实名认证状态变了，重新拉一次用户信息（restore 会一并刷新菜单与权限）
  await userStore.restore().catch(() => {})
}
const dialogVisible = ref(false)
const saving = ref(false)
const pwd = reactive({ oldPassword: '', newPassword: '', confirm: '' })

async function onSave() {
  if (!pwd.oldPassword || !pwd.newPassword) { ElMessage.warning('请填写完整'); return }
  if (pwd.newPassword.length < 6 || pwd.newPassword.length > 32) { ElMessage.warning('新密码需 6~32 位'); return }
  if (pwd.newPassword !== pwd.confirm) { ElMessage.warning('两次输入的新密码不一致'); return }
  saving.value = true
  try {
    await changePasswordApi(pwd.oldPassword, pwd.newPassword)
    ElMessage.success('密码修改成功，请重新登录')
    dialogVisible.value = false
    await userStore.logout()
    resetDynamicRoutes()
    router.replace('/login')
  } finally {
    saving.value = false
  }
}
</script>

<style lang="scss" scoped>
.title { margin: 0 0 16px; color: var(--ts-primary-dark); }
h3 { margin: 0 0 14px; color: var(--ts-primary-dark); font-size: 15px; }
.tips p { font-size: 12.5px; color: var(--ts-text-secondary); line-height: 2.1; margin: 0; }
</style>
