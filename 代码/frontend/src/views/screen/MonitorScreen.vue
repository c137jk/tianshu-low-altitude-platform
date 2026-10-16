<template>
  <div class="screen">
    <header class="hd">
      <div class="hd-left">
        <span class="logo">天</span>
        <h1>天枢 · 低空智能运营管控平台</h1>
        <span class="sub">WENZHOU OUJIANGKOU · UTM LIVE</span>
      </div>
      <div class="hd-right">
        <!-- 脱敏口径可见化（差距分析 P0-2）：投屏无登录=脱敏，管控台点进来=全量 -->
        <span class="badge" :class="masked ? 'anon' : 'full'">
          {{ masked ? '对外投屏 · 已脱敏' : '内部全量视图' }}
        </span>
        <!-- 大屏会被管控台「实时监控」页签用 iframe 内嵌，页内任何整页跳转都会把 iframe
             导航成"管控台套管控台"并卡住路由，所以这里不放返回管控台的入口 -->
        <a v-if="masked && hasToken" class="exit" href="#refresh">登录态失效</a>
        <a v-else-if="masked" class="exit" href="/login?redirect=/screen">登录</a>
        <span class="clock">{{ clock }}</span>
      </div>
    </header>

    <div class="body">
      <!-- 左列：今日统计 + 机队 -->
      <aside class="col left">
        <div class="panel">
          <h3>今日运行</h3>
          <div class="stat-grid">
            <div class="stat"><b class="num gold">{{ data.today.takeoffs }}</b><label>今日起飞</label></div>
            <div class="stat"><b class="num cyan">{{ data.today.landings }}</b><label>今日降落</label></div>
            <div class="stat"><b class="num green">{{ data.flying.length }}</b><label>在飞架次</label></div>
          </div>
        </div>
        <div class="panel flex">
          <h3>机队状态</h3>
          <div class="fleet">
            <div v-for="f in data.fleet" :key="f.id" class="fleet-row">
              <span class="dot" :class="f.status === 2 ? 'fly' : f.online ? 'idle' : 'off'"></span>
              <span class="fname">{{ f.no }}</span>
              <span class="fbat" :class="{low: f.battery < 20}">{{ f.battery.toFixed(0) }}%</span>
            </div>
          </div>
        </div>
      </aside>

      <!-- 中：地图 -->
      <main class="map-wrap">
        <div ref="mapEl" class="map"></div>
        <div style="position:absolute;left:20px;top:70px;background:rgba(6,13,31,.85);padding:10px 14px;border-radius:6px;color:#cdd6e4;font-size:12px;line-height:2.1;z-index:10">
          <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#c8a45c;margin-right:6px"></span>起降场<br/>
          <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#37d67a;margin-right:6px"></span>在飞飞机<br/>
          <span style="display:inline-block;width:8px;height:8px;background:#37d67a;transform:rotate(45deg);margin-right:6px"></span>寄件点<br/>
          <span style="display:inline-block;width:8px;height:8px;background:#ffb020;border-radius:2px;margin-right:6px"></span>收件点<br/>
          <span style="display:inline-block;width:18px;height:3px;background:#c8a45c;vertical-align:middle;margin-right:6px"></span>飞行航线
        </div>
      </main>

      <!-- 右列：在飞任务 -->
      <aside class="col right">
        <div class="panel flex">
          <h3>在飞任务</h3>
          <div class="miss">
            <div v-if="!data.flying.length" class="empty">当前无在飞架次</div>
            <div v-for="m in data.flying" :key="m.planNo" class="miss-card">
              <div class="mc-top">
                <span class="num">{{ m.planNo }}</span>
                <span class="tag">执行中</span>
              </div>
              <div class="mc-cargo">{{ m.cargo }} → {{ m.receiver }}</div>
              <div style="font-size:12px;color:#7fd3ff">当前阶段：{{ m.phase }}（{{ m.progress }}%）</div>
              <div class="mc-meta">
                <span>{{ m.aircraftNo }} · {{ m.pilot }}</span>
              </div>
              <div class="mc-bar">
                <i :style="{ width: Math.min(100, m.battery) + '%', background: m.battery < 20 ? '#ff5b5b' : '#37d67a' }"></i>
              </div>
              <div class="mc-extra">高度 {{ m.altitude.toFixed(0) }}m · 电量 {{ m.battery.toFixed(0) }}%</div>
            </div>
          </div>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { loadAmap } from '@/composables/useAmap'
import { ymdHms } from '@/utils/date'

const mapEl = ref<HTMLElement | null>(null)
const clock = ref('')
let map: any = null
let centered = false  // 首次拿到在飞架次后自动跟一次飞机，之后不打扰用户缩放
let timer: number | null = null
let clockTimer: number | null = null
let overlayGroup: any = null

const data = ref<any>({
  fleet: [], flying: [], vertiports: [], airspaces: [],
  today: { takeoffs: 0, landings: 0, orderStatus: {} },
})

// 大屏可以匿名投屏，也可以从管控台带登录态进来；这里不引入 request.ts，
// 避免 token 过期时拦截器把投屏页整页踢回登录页（大屏要求断流不崩）。
const token = localStorage.getItem('ts_token') || ''
const hasToken = ref(!!token)
const masked = ref(true)

function authFetch(url: string) {
  return fetch(url, token ? { headers: { Authorization: `Bearer ${token}` } } : undefined)
}

function tickClock() {
  // 大屏时钟用 'YYYY-MM-DD HH:mm:ss' 本地时间：和右侧接口返回的时间列同一种写法，
  // 投屏时不会看着像两套系统。
  clock.value = ymdHms(new Date())
}

async function load() {
  try {
    const r = await authFetch('/api/monitor/screen')
    if (r.status === 401) {          // 带过去的 token 过期：退回脱敏口径继续投屏
      hasToken.value = false
      localStorage.removeItem('ts_token')
    }
    const j = await r.json()
    if (j.code === 0) {
      data.value = j.data; renderOverlays()
      masked.value = !j.data.authed
      if (!centered && j.data.flying?.length && map) {
        const f = j.data.flying[0]
        if (f.lng != null) { map.setZoomAndCenter(13.5, [f.lng, f.lat]); centered = true }
      }
    }
  } catch { /* 大屏断网不刷崩 */ }
}

async function initMap() {
  const AMap = await loadAmap()
  map = new AMap.Map(mapEl.value!, {
    zoom: 12, center: [120.79, 27.97], viewMode: '2D',
    mapStyle: 'amap://styles/dark',
  })
  renderOverlays()
}

function renderOverlays() {
  if (!map) return
  // 全量重绘（演示规模小，简单直接）
  map.remove(map.getAllOverlays())
  const d = data.value

  // 空域多边形：1禁飞红 / 2限飞黄 / 3可飞绿
  const airColor: Record<number, string> = { 1: '#ff5b5b', 2: '#e8c14f', 3: '#37d67a' }
  for (const a of d.airspaces || []) {
    const g = a.geometry
    if (!g || g.type !== 'Polygon') continue
    const coords = g.coordinates[0].map((c: number[]) => [c[0], c[1]])
    const color = airColor[a.type] || '#37d67a'
    new (window as any).AMap.Polygon({
      path: coords, strokeColor: color,
      strokeWeight: 2.5, strokeOpacity: 1, fillOpacity: 0.16,
      fillColor: color, map,
    })
  }

  // 场站
  for (const v of d.vertiports || []) {
    new (window as any).AMap.Marker({
      position: [v.lng, v.lat], map, anchor: 'center',
      content: `<div style="width:14px;height:14px;border-radius:50%;background:#c8a45c;border:2px solid #060d1f;box-shadow:0 0 10px #c8a45c"></div>`,
    })
  }

  // 在飞航线 + 飞机标记
  for (const m of d.flying || []) {
    if (m.route && m.route.length > 1) {
      new (window as any).AMap.Polyline({
        path: m.route.map((p: any) => [p.lng, p.lat]), map,
strokeColor: '#c8a45c', strokeWeight: 3,
      })
    }
    if (m.route && m.route.length) {
      const s = m.route[0], r = m.route[Math.floor(m.route.length * 0.5)]
      new (window as any).AMap.Marker({ position: [s.lng, s.lat], map, content: '<div style="width:8px;height:8px;background:#37d67a;transform:rotate(45deg)"></div>' })
      new (window as any).AMap.Marker({ position: [r.lng, r.lat], map, content: '<div style="width:8px;height:8px;background:#ffb020;border-radius:2px"></div>' })
    }
    if (m.lng != null) {
      new (window as any).AMap.Marker({
        position: [m.lng, m.lat], map, anchor: 'center',
        content: `<div style="position:relative;width:22px;height:22px">
          <div style="position:absolute;inset:-8px;border-radius:50%;border:2px solid #37d67a;animation:pulse 1.6s infinite"></div>
          <div style="position:absolute;inset:4px;border-radius:50%;background:#37d67a;box-shadow:0 0 12px #37d67a"></div>
        </div>`,
      })
    }
  }
}

onMounted(async () => {
  tickClock()
  clockTimer = window.setInterval(tickClock, 1000)
  await initMap()
  await load()
  timer = window.setInterval(load, 2500)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  if (clockTimer) clearInterval(clockTimer)
  map?.destroy?.()
})
</script>

<style scoped>
.screen { position: fixed; inset: 0; background: #060d1f; color: #dce6f5; display: flex; flex-direction: column; overflow: hidden; }
.hd { height: 56px; display: flex; align-items: center; justify-content: space-between; padding: 0 20px;
  background: linear-gradient(180deg, #0c1a36 0%, transparent 100%); border-bottom: 1px solid #1d3358; }
.hd-left { display: flex; align-items: center; gap: 10px; }
.logo { width: 30px; height: 30px; border-radius: 4px; background: #c8a45c; color: #060d1f; font-weight: 800;
  display: grid; place-items: center; }
.hd h1 { font-size: 18px; letter-spacing: 2px; margin: 0; }
.sub { font-size: 10px; color: #5b7aa8; letter-spacing: 1px; }
.hd-right { display: flex; align-items: center; gap: 14px; }
.clock { font-family: monospace; color: #c8a45c; font-size: 14px; }
.badge { font-size: 11px; padding: 3px 9px; border-radius: 10px; border: 1px solid currentColor; }
.badge.anon { color: #e8c14f; }
.badge.full { color: #37d67a; }
.exit { color: #9db8dd; font-size: 12px; text-decoration: none; border: 1px solid #1d3358;
  padding: 4px 10px; border-radius: 4px; }
.exit:hover { color: #c8a45c; border-color: #c8a45c; }
.body { flex: 1; display: grid; grid-template-columns: 280px 1fr 300px; gap: 10px; padding: 10px; min-height: 0; }
.col { display: flex; flex-direction: column; gap: 10px; min-height: 0; }
.panel { background: rgba(16, 32, 64, .6); border: 1px solid #1d3358; border-radius: 6px; padding: 12px; }
.panel.flex { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.panel h3 { margin: 0 0 10px; font-size: 13px; color: #7ea4d8; letter-spacing: 1px; font-weight: 600; }
.stat-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.stat { text-align: center; padding: 8px 0; background: rgba(6,13,31,.6); border-radius: 4px; }
.stat b { display: block; font-size: 24px; font-family: monospace; }
.stat label { font-size: 11px; color: #7ea4d8; }
.gold { color: #c8a45c; } .cyan { color: #4fd8e8; } .green { color: #37d67a; }
.fleet { overflow-y: auto; }
.fleet-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px dashed #1d3358; font-size: 12px; }
.dot { width: 8px; height: 8px; border-radius: 50%; }
.dot.fly { background: #37d67a; box-shadow: 0 0 6px #37d67a; }
.dot.idle { background: #4fd8e8; }
.dot.off { background: #555; }
.fname { flex: 1; font-family: monospace; }
.fbat.low { color: #ff5b5b; }
.map-wrap { position: relative; border: 1px solid #1d3358; border-radius: 6px; overflow: hidden; min-height: 0; }
.map { position: absolute; inset: 0; }
.miss { overflow-y: auto; flex: 1; }
.miss-card { background: rgba(6,13,31,.6); border-left: 3px solid #c8a45c; border-radius: 4px;
  padding: 8px 10px; margin-bottom: 8px; }
.mc-top { display: flex; justify-content: space-between; font-size: 12px; }
.tag { color: #37d67a; font-size: 11px; }
.mc-cargo { font-size: 12px; margin: 4px 0; color: #dce6f5; }
.mc-meta { font-size: 11px; color: #7ea4d8; }
.mc-bar { height: 4px; background: #13244a; border-radius: 2px; margin: 6px 0 4px; overflow: hidden; }
.mc-bar i { display: block; height: 100%; }
.mc-extra { font-size: 11px; color: #9db8dd; }
.empty { color: #5b7aa8; font-size: 13px; text-align: center; padding: 30px 0; }
.num { font-family: monospace; }
@keyframes pulse { 0% { transform: scale(.6); opacity: 1; } 100% { transform: scale(1.4); opacity: 0; } }
</style>
