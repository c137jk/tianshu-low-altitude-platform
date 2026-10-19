<template>
  <div class="login-page" ref="pageRef">
    <!-- ============ 夜间低空运营场景（纯图形，无文字） ============ -->
    <svg class="scene" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMax slice"
         xmlns:xlink="http://www.w3.org/1999/xlink" aria-hidden="true">
      <defs>
        <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#081428" />
          <stop offset="55%" stop-color="#0f2342" />
          <stop offset="100%" stop-color="#183055" />
        </linearGradient>
        <radialGradient id="moonGlow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stop-color="#f4efe0" stop-opacity="0.55" />
          <stop offset="100%" stop-color="#f4efe0" stop-opacity="0" />
        </radialGradient>
        <linearGradient id="routeFade" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stop-color="#C8A45C" stop-opacity="0.2" />
          <stop offset="18%" stop-color="#C8A45C" stop-opacity="0.9" />
          <stop offset="82%" stop-color="#C8A45C" stop-opacity="0.9" />
          <stop offset="100%" stop-color="#C8A45C" stop-opacity="0.2" />
        </linearGradient>
        <pattern id="win" width="20" height="26" patternUnits="userSpaceOnUse">
          <rect x="5" y="7" width="5" height="6" fill="#d8b873" opacity="0.16" />
          <rect x="13" y="16" width="5" height="6" fill="#cdd8ee" opacity="0.10" />
        </pattern>
        <clipPath id="backClip">
          <rect x="0" y="770" width="70" height="130" />
          <rect x="78" y="740" width="56" height="160" />
          <rect x="142" y="792" width="80" height="108" />
          <rect x="330" y="760" width="64" height="140" />
          <rect x="402" y="720" width="48" height="180" />
          <rect x="560" y="778" width="90" height="122" />
          <rect x="662" y="738" width="58" height="162" />
          <rect x="830" y="766" width="74" height="134" />
          <rect x="912" y="712" width="54" height="188" />
          <rect x="1060" y="772" width="84" height="128" />
          <rect x="1152" y="742" width="60" height="158" />
          <rect x="1240" y="784" width="90" height="116" />
          <rect x="1340" y="752" width="100" height="148" />
        </clipPath>
      </defs>

      <!-- 天空 -->
      <rect width="1440" height="900" fill="url(#sky)" />

      <!-- 远景层：月亮、星星、云 -->
      <g class="layer layer-far">
        <circle cx="1180" cy="150" r="110" fill="url(#moonGlow)" />
        <circle cx="1180" cy="150" r="42" fill="#f1ecdc" opacity="0.92" />
        <circle cx="1166" cy="138" r="9" fill="#d9d2bd" opacity="0.5" />
        <circle cx="1196" cy="162" r="6" fill="#d9d2bd" opacity="0.4" />
        <g ref="starsRef"></g>
        <!-- 漂移云 -->
        <g opacity="0.5">
          <g>
            <animateTransform attributeName="transform" type="translate" from="-360 180" to="1560 180" dur="95s" repeatCount="indefinite" />
            <ellipse cx="0" cy="0" rx="90" ry="16" fill="#9db2d4" opacity="0.07" />
            <ellipse cx="60" cy="-10" rx="70" ry="14" fill="#9db2d4" opacity="0.06" />
          </g>
          <g>
            <animateTransform attributeName="transform" type="translate" from="-520 320" to="1500 320" dur="130s" begin="-40s" repeatCount="indefinite" />
            <ellipse cx="0" cy="0" rx="120" ry="18" fill="#9db2d4" opacity="0.05" />
            <ellipse cx="80" cy="-12" rx="80" ry="13" fill="#9db2d4" opacity="0.05" />
          </g>
        </g>
      </g>

      <!-- 中景层：起降场、航线、无人机、雷达 -->
      <g class="layer layer-mid">
        <!-- 去程金色航线（虚线流动） -->
        <path d="M 380 610 C 560 320, 900 280, 1060 530"
              fill="none" stroke="#C8A45C" stroke-opacity="0.18" stroke-width="7" stroke-linecap="round" />
        <path d="M 380 610 C 560 320, 900 280, 1060 530"
              fill="none" stroke="url(#routeFade)" stroke-width="2.4" stroke-linecap="round"
              stroke-dasharray="2 12" class="route-flow" />
        <!-- 回程浅色航线 -->
        <path d="M 1060 530 C 880 700, 540 740, 380 610"
              fill="none" stroke="#9fb4d8" stroke-opacity="0.22" stroke-width="1.8" stroke-linecap="round"
              stroke-dasharray="3 10" class="route-flow-rev" />

        <!-- 航点脉冲 -->
        <g>
          <circle cx="560" cy="430" r="4" fill="#E0C993" />
          <circle cx="560" cy="430" r="4" fill="none" stroke="#E0C993">
            <animate attributeName="r" values="4;16" dur="2.6s" begin="0s" repeatCount="indefinite" />
            <animate attributeName="opacity" values="0.7;0" dur="2.6s" begin="0s" repeatCount="indefinite" />
          </circle>
        </g>
        <g>
          <circle cx="720" cy="355" r="4" fill="#E0C993" />
          <circle cx="720" cy="355" r="4" fill="none" stroke="#E0C993">
            <animate attributeName="r" values="4;16" dur="2.6s" begin="0.9s" repeatCount="indefinite" />
            <animate attributeName="opacity" values="0.7;0" dur="2.6s" begin="0.9s" repeatCount="indefinite" />
          </circle>
        </g>
        <g>
          <circle cx="900" cy="390" r="4" fill="#E0C993" />
          <circle cx="900" cy="390" r="4" fill="none" stroke="#E0C993">
            <animate attributeName="r" values="4;16" dur="2.6s" begin="1.7s" repeatCount="indefinite" />
            <animate attributeName="opacity" values="0.7;0" dur="2.6s" begin="1.7s" repeatCount="indefinite" />
          </circle>
        </g>

        <!-- 起降场 A（含雷达扫描） -->
        <g>
          <!-- 雷达扫描扇 -->
          <g>
            <circle cx="380" cy="610" r="78" fill="none" stroke="#C8A45C" stroke-opacity="0.12" />
            <circle cx="380" cy="610" r="52" fill="none" stroke="#C8A45C" stroke-opacity="0.10" />
            <circle cx="380" cy="610" r="26" fill="none" stroke="#C8A45C" stroke-opacity="0.10" />
            <path d="M 380 610 L 380 532 A 78 78 0 0 1 435 555 Z" fill="#C8A45C" opacity="0.10">
              <animateTransform attributeName="transform" type="rotate" from="0 380 610" to="360 380 610"
                                dur="4.5s" repeatCount="indefinite" />
            </path>
            <line x1="380" y1="532" x2="380" y2="688" stroke="#C8A45C" stroke-opacity="0.08" />
            <line x1="302" y1="610" x2="458" y2="610" stroke="#C8A45C" stroke-opacity="0.08" />
          </g>
          <!-- 脉冲环 -->
          <circle cx="380" cy="610" r="12" fill="none" stroke="#C8A45C" stroke-width="1.4">
            <animate attributeName="r" values="10;40" dur="3s" repeatCount="indefinite" />
            <animate attributeName="opacity" values="0.75;0" dur="3s" repeatCount="indefinite" />
          </circle>
          <!-- 起降台 -->
          <ellipse cx="380" cy="610" rx="26" ry="9" fill="#0c1c38" stroke="#C8A45C" stroke-width="1.6" />
          <ellipse cx="380" cy="607" rx="26" ry="9" fill="#1b3258" stroke="#E0C993" stroke-width="1.2" />
          <circle cx="380" cy="607" r="3.4" fill="#E0C993" />
          <line x1="362" y1="607" x2="398" y2="607" stroke="#E0C993" stroke-width="1" />
          <line x1="380" y1="589" x2="380" y2="625" stroke="#E0C993" stroke-width="1" />
        </g>

        <!-- 起降场 B -->
        <g>
          <circle cx="1060" cy="530" r="12" fill="none" stroke="#C8A45C" stroke-width="1.4">
            <animate attributeName="r" values="10;40" dur="3s" begin="1.4s" repeatCount="indefinite" />
            <animate attributeName="opacity" values="0.75;0" dur="3s" begin="1.4s" repeatCount="indefinite" />
          </circle>
          <ellipse cx="1060" cy="530" rx="26" ry="9" fill="#0c1c38" stroke="#C8A45C" stroke-width="1.6" />
          <ellipse cx="1060" cy="527" rx="26" ry="9" fill="#1b3258" stroke="#E0C993" stroke-width="1.2" />
          <circle cx="1060" cy="527" r="3.4" fill="#E0C993" />
          <line x1="1042" y1="527" x2="1078" y2="527" stroke="#E0C993" stroke-width="1" />
          <line x1="1060" y1="509" x2="1060" y2="545" stroke="#E0C993" stroke-width="1" />
        </g>

        <!-- 无人机（沿闭合航线循环飞行） -->
        <g>
          <animateMotion dur="28s" repeatCount="indefinite" rotate="auto">
            <mpath href="#flight-loop" xlink:href="#flight-loop" />
          </animateMotion>
          <g class="drone">
            <!-- 桨叶（旋转） -->
            <g class="blades" style="--cx:-15px;--cy:-10px">
              <line x1="-24" y1="-10" x2="-6" y2="-10" stroke="#dfe7f5" stroke-width="1.4" opacity="0.75" />
              <line x1="-15" y1="-19" x2="-15" y2="-1" stroke="#dfe7f5" stroke-width="1.4" opacity="0.75" />
            </g>
            <g class="blades" style="--cx:15px;--cy:-10px">
              <line x1="6" y1="-10" x2="24" y2="-10" stroke="#dfe7f5" stroke-width="1.4" opacity="0.75" />
              <line x1="15" y1="-19" x2="15" y2="-1" stroke="#dfe7f5" stroke-width="1.4" opacity="0.75" />
            </g>
            <g class="blades" style="--cx:-15px;--cy:10px">
              <line x1="-24" y1="10" x2="-6" y2="10" stroke="#dfe7f5" stroke-width="1.4" opacity="0.75" />
              <line x1="-15" y1="1" x2="-15" y2="19" stroke="#dfe7f5" stroke-width="1.4" opacity="0.75" />
            </g>
            <g class="blades" style="--cx:15px;--cy:10px">
              <line x1="6" y1="10" x2="24" y2="10" stroke="#dfe7f5" stroke-width="1.4" opacity="0.75" />
              <line x1="15" y1="1" x2="15" y2="19" stroke="#dfe7f5" stroke-width="1.4" opacity="0.75" />
            </g>
            <!-- 机臂 -->
            <line x1="-15" y1="-10" x2="15" y2="10" stroke="#aebcd6" stroke-width="2" />
            <line x1="-15" y1="10" x2="15" y2="-10" stroke="#aebcd6" stroke-width="2" />
            <circle cx="-15" cy="-10" r="2.2" fill="#8fa0c0" />
            <circle cx="15" cy="-10" r="2.2" fill="#8fa0c0" />
            <circle cx="-15" cy="10" r="2.2" fill="#8fa0c0" />
            <circle cx="15" cy="10" r="2.2" fill="#8fa0c0" />
            <!-- 机身 -->
            <ellipse cx="0" cy="0" rx="11" ry="7.5" fill="#eef2f9" />
            <ellipse cx="0" cy="-1.5" rx="6" ry="3.4" fill="#cdd8ee" />
            <line x1="-7" y1="2.5" x2="7" y2="2.5" stroke="#C8A45C" stroke-width="1.6" />
            <!-- 下方货舱 -->
            <rect x="-5" y="6.5" width="10" height="7" rx="1.4" fill="#C8A45C" />
            <line x1="-5" y1="10" x2="5" y2="10" stroke="#8a6d33" stroke-width="0.8" />
            <!-- 起落架 -->
            <line x1="-10" y1="16" x2="10" y2="16" stroke="#aebcd6" stroke-width="1.6" />
            <line x1="-7" y1="13" x2="-9" y2="16" stroke="#aebcd6" stroke-width="1.4" />
            <line x1="7" y1="13" x2="9" y2="16" stroke="#aebcd6" stroke-width="1.4" />
            <!-- 航行灯 -->
            <circle cx="-11.5" cy="-2" r="1.7" fill="#ff5a52" class="nav-red" />
            <circle cx="11.5" cy="-2" r="1.7" fill="#4ade80" class="nav-green" />
          </g>
        </g>
        <!-- 闭合运动路径（不显示，仅给无人机循迹） -->
        <path id="flight-loop" d="M 380 610 C 560 320, 900 280, 1060 530 C 880 700, 540 740, 380 610 Z"
              fill="none" stroke="none" />
      </g>

      <!-- 近景层：城市天际线（两层，视差） -->
      <g class="layer layer-near">
        <!-- 后排楼群 -->
        <g fill="#0e2040">
          <rect x="0" y="770" width="70" height="130" />
          <rect x="78" y="740" width="56" height="160" />
          <rect x="142" y="792" width="80" height="108" />
          <rect x="330" y="760" width="64" height="140" />
          <rect x="402" y="720" width="48" height="180" />
          <rect x="560" y="778" width="90" height="122" />
          <rect x="662" y="738" width="58" height="162" />
          <rect x="830" y="766" width="74" height="134" />
          <rect x="912" y="712" width="54" height="188" />
          <rect x="1060" y="772" width="84" height="128" />
          <rect x="1152" y="742" width="60" height="158" />
          <rect x="1240" y="784" width="90" height="116" />
          <rect x="1340" y="752" width="100" height="148" />
        </g>
        <g fill="url(#win)" clip-path="url(#backClip)" opacity="0.9">
          <rect x="0" y="700" width="1440" height="200" />
        </g>
        <!-- 前排楼群（更深） -->
        <g fill="#09162c">
          <rect x="-10" y="812" width="110" height="88" />
          <rect x="110" y="786" width="74" height="114" />
          <rect x="196" y="828" width="96" height="72" />
          <rect x="300" y="798" width="58" height="102" />
          <rect x="460" y="818" width="100" height="82" />
          <rect x="570" y="780" width="66" height="120" />
          <rect x="700" y="830" width="110" height="70" />
          <rect x="820" y="796" width="72" height="104" />
          <rect x="980" y="822" width="96" height="78" />
          <rect x="1088" y="788" width="64" height="112" />
          <rect x="1200" y="816" width="104" height="84" />
          <rect x="1316" y="792" width="70" height="108" />
          <rect x="1394" y="826" width="60" height="74" />
        </g>
        <!-- 高塔航空障碍灯 -->
        <line x1="939" y1="712" x2="939" y2="686" stroke="#1b3258" stroke-width="2" />
        <circle cx="939" cy="684" r="2.4" fill="#ff5a52">
          <animate attributeName="opacity" values="1;0.15;1" dur="1.6s" repeatCount="indefinite" />
        </circle>
        <line x1="426" y1="720" x2="426" y2="698" stroke="#1b3258" stroke-width="2" />
        <circle cx="426" cy="696" r="2" fill="#ff5a52">
          <animate attributeName="opacity" values="1;0.15;1" dur="2.1s" begin="0.6s" repeatCount="indefinite" />
        </circle>
      </g>
    </svg>

    <!-- ============ 悬浮毛玻璃登录卡片 ============ -->
    <div class="login-card">
      <div class="card-brand">
        <span class="brand-mark">天</span>
        <span class="brand-en">TIANSHU UTM</span>
      </div>
      <h2>{{ mode === 'login' ? '欢迎登录' : '客户注册' }}</h2>

      <el-form v-if="mode==='login'" ref="formRef" :model="form" :rules="rules" size="large" @submit.prevent>
        <el-form-item prop="username">
          <el-input v-model="form.username" placeholder="账号" :prefix-icon="User" clearable />
        </el-form-item>
        <el-form-item prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码"
            :prefix-icon="Lock"
            show-password
            @keyup.enter="onLogin"
          />
        </el-form-item>
        <el-button type="primary" class="login-btn" :loading="loading" @click="onLogin">
          登 录
        </el-button>
      </el-form>

      <el-form v-else ref="regRef" :model="reg" :rules="regRules" size="large" @submit.prevent>
        <el-form-item prop="username">
          <el-input v-model="reg.username" placeholder="设置登录账号" :prefix-icon="User" clearable />
        </el-form-item>
        <el-form-item prop="phone">
          <el-input v-model="reg.phone" placeholder="手机号" clearable />
        </el-form-item>
        <el-form-item prop="password">
          <el-input v-model="reg.password" type="password" placeholder="设置密码（≥6位）" :prefix-icon="Lock" show-password />
        </el-form-item>
        <el-button type="primary" class="login-btn" :loading="loading" @click="onRegister">
          注 册
        </el-button>
      </el-form>

      <div class="switch-mode">
        <template v-if="mode==='login'">
          还没有账号？<el-button link class="quick-btn" @click="mode='register'">立即注册</el-button>
        </template>
        <template v-else>
          已有账号？<el-button link class="quick-btn" @click="mode='login'">返回登录</el-button>
        </template>
      </div>

      <div v-if="mode==='login'" class="quick">
        <span>演示账号一键填充：</span>
        <el-button link class="quick-btn" @click="fill('admin')">平台管理员</el-button>
        <el-button link class="quick-btn" @click="fill('merchant')">调度员</el-button>
        <el-button link class="quick-btn" @click="fill('customer')">客户</el-button>
        <el-button link class="quick-btn" @click="fill('pilot02')">机长</el-button>
        <el-button link class="quick-btn" @click="fill('kongguan')">空管</el-button>
        <el-button link class="quick-btn" @click="fill('caiwu')">财务</el-button>
        <el-button link class="quick-btn" @click="fill('yunwei')">运维</el-button>
        <span class="hint">（密码统一 Admin@123）</span>
      </div>
      <div class="pub-link">
        <a href="/announcements.html" target="_blank">低空活动公告与信息查询</a>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { User, Lock } from '@element-plus/icons-vue'
import { ElMessage, type FormInstance } from 'element-plus'
import { useUserStore } from '@/stores/user'
import request from '@/utils/request'
import { buildDynamicRoutes } from '@/router'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ username: '', password: '' })
const rules = {
  username: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

function fill(role: string) {
  form.username = role
  form.password = 'Admin@123'
}

const mode = ref<'login' | 'register'>('login')
const regRef = ref<FormInstance>()
const reg = reactive({ role: 'customer', username: '', realName: '', phone: '', password: '' })
const regRules = {
  username: [{ required: true, message: '请设置账号', trigger: 'blur' }],
  phone: [{ required: true, pattern: /^1\d{10}$/, message: '请输入11位手机号', trigger: 'blur' }],
  password: [{ required: true, min: 6, message: '密码至少 6 位', trigger: 'blur' }],
}
async function onRegister() {
  await regRef.value?.validate()
  loading.value = true
  try {
    await request('/auth/register', { method: 'POST', data: { ...reg } })
    ElMessage.success('注册成功，请登录')
    form.username = reg.username
    mode.value = 'login'
  } finally {
    loading.value = false
  }
}

async function onLogin() {
  await formRef.value?.validate()
  loading.value = true
  try {
    const res = await userStore.login({ ...form })
    buildDynamicRoutes(res.menus)
    ElMessage.success('登录成功')
    const redirect = (route.query.redirect as string) || userStore.homePath
    router.replace(redirect)
  } finally {
    loading.value = false
  }
}

// ---- 场景交互：星星生成 + 鼠标视差 ----
const pageRef = ref<HTMLElement>()
const starsRef = ref<SVGGElement>()

function buildStars() {
  const g = starsRef.value
  if (!g) return
  const ns = 'http://www.w3.org/2000/svg'
  for (let i = 0; i < 80; i++) {
    const c = document.createElementNS(ns, 'circle')
    c.setAttribute('cx', String(Math.round(Math.random() * 1440)))
    c.setAttribute('cy', String(Math.round(Math.random() * 620)))
    c.setAttribute('r', (0.4 + Math.random() * 1.1).toFixed(2))
    c.setAttribute('fill', '#cdd8ee')
    c.style.opacity = String(0.25 + Math.random() * 0.6)
    c.classList.add('star')
    c.style.animationDelay = `${(Math.random() * 3).toFixed(2)}s`
    c.style.animationDuration = `${(2.2 + Math.random() * 2.6).toFixed(2)}s`
    g.appendChild(c)
  }
}

function onParallax(e: MouseEvent) {
  const el = pageRef.value
  if (!el) return
  const nx = (e.clientX / window.innerWidth) * 2 - 1
  const ny = (e.clientY / window.innerHeight) * 2 - 1
  el.style.setProperty('--mx', nx.toFixed(3))
  el.style.setProperty('--my', ny.toFixed(3))
}

onMounted(() => {
  buildStars()
  window.addEventListener('mousemove', onParallax)
})
onBeforeUnmount(() => window.removeEventListener('mousemove', onParallax))
</script>

<style lang="scss" scoped>
.login-page {
  --mx: 0;
  --my: 0;
  position: relative;
  min-height: 100vh;
  overflow: hidden;
  background: #081428;
}
.scene {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.layer {
  transition: transform 0.35s ease-out;
  will-change: transform;
}
.layer-far { transform: translate(calc(var(--mx) * -14px), calc(var(--my) * -8px)); }
.layer-mid { transform: translate(calc(var(--mx) * 8px), calc(var(--my) * 5px)); }
.layer-near { transform: translate(calc(var(--mx) * 22px), calc(var(--my) * 12px)); }

.star {
  animation-name: twinkle;
  animation-iteration-count: infinite;
  animation-timing-function: ease-in-out;
}
@keyframes twinkle {
  0%, 100% { opacity: 0.25; }
  50% { opacity: 0.9; }
}

.route-flow {
  animation: dashMove 1.1s linear infinite;
}
.route-flow-rev {
  animation: dashMoveRev 1.6s linear infinite;
}
@keyframes dashMove {
  to { stroke-dashoffset: -14; }
}
@keyframes dashMoveRev {
  to { stroke-dashoffset: 13; }
}

.blades {
  transform-box: fill-box;
  transform-origin: center;
  animation: rotor 0.18s linear infinite;
}
@keyframes rotor {
  to { transform: rotate(360deg); }
}
.nav-red { animation: blinkR 1.1s ease-in-out infinite; }
.nav-green { animation: blinkG 1.1s ease-in-out infinite; }
@keyframes blinkR { 0%, 100% { opacity: 1; } 50% { opacity: 0.15; } }
@keyframes blinkG { 0%, 100% { opacity: 0.15; } 50% { opacity: 1; } }

/* 悬浮毛玻璃登录卡片 */
.login-card {
  position: absolute;
  top: 50%;
  right: 9%;
  width: 400px;
  transform: translateY(-50%);
  padding: 38px 36px 30px;
  border-radius: 12px;
  background: linear-gradient(160deg, rgba(20, 38, 68, 0.72), rgba(12, 25, 46, 0.66));
  border: 1px solid rgba(200, 164, 92, 0.35);
  box-shadow: 0 24px 60px rgba(4, 12, 26, 0.55), inset 0 1px 0 rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
}
.card-brand {
  display: flex; align-items: center; gap: 10px;
  margin-bottom: 22px;
}
.brand-mark {
  width: 34px; height: 34px; border-radius: 4px;
  background: var(--ts-gold); color: #1c3358;
  display: flex; align-items: center; justify-content: center;
  font-size: 18px; font-weight: 700;
}
.brand-en { font-size: 11px; letter-spacing: 3px; color: #c6d0e2; }
.pub-link { margin-top: 14px; text-align: center; font-size: 12.5px; }
.pub-link a { color: #c8a45c; text-decoration: none; }
.pub-link a:hover { text-decoration: underline; }
.login-card h2 {
  margin: 0 0 26px;
  color: #f3eedd;
  font-size: 21px;
  font-weight: 600;
  letter-spacing: 2px;
}
.login-btn {
  width: 100%;
  background: var(--ts-gold);
  border-color: var(--ts-gold);
  color: #1c3358;
  font-weight: 700;
  letter-spacing: 4px;
}
.login-btn:hover {
  background: var(--ts-gold-light);
  border-color: var(--ts-gold-light);
  color: #1c3358;
}
.switch-mode {
  margin-top: 14px;
  font-size: 13px;
  color: #9fb0cc;
  text-align: center;
}
.quick {
  margin-top: 18px;
  font-size: 12px;
  color: #9fb0cc;
  display: flex; flex-wrap: wrap; align-items: center; gap: 2px;
}
.quick-btn {
  --el-button-text-color: var(--ts-gold-light);
  --el-button-hover-text-color: #f0ddb0;
}
.hint { width: 100%; color: #7486a3; margin-top: 4px; }

@media (max-width: 960px) {
  .login-card {
    left: 50%; right: auto;
    transform: translate(-50%, -50%);
    width: min(90vw, 400px);
  }
}
@media (prefers-reduced-motion: reduce) {
  .star, .route-flow, .route-flow-rev, .blades, .nav-red, .nav-green { animation: none; }
  .layer { transition: none; }
}
</style>
