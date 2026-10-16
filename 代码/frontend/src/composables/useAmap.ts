/**
 * 高德 JS API 加载与地图封装（GCJ-02）。
 * key / 安全密钥走 .env.development；加载失败（如未配域名白名单）时页面走手动经纬度降级。
 */
import { ref } from 'vue'

const WENZHOU_CENTER: [number, number] = [120.93, 27.95]
export interface LngLatPoint { lng: number; lat: number; address?: string }

let loadPromise: Promise<any> | null = null
export const amapAvailable = ref(false)
export const amapError = ref('')

export function loadAmap(): Promise<any> {
  const w = window as any
  if (w.AMap) return Promise.resolve(w.AMap)
  if (loadPromise) return loadPromise

  const key = import.meta.env.VITE_AMAP_JS_KEY as string
  const code = import.meta.env.VITE_AMAP_JS_CODE as string
  if (!key) {
    amapError.value = '未配置高德 Key'
    return Promise.reject(new Error(amapError.value))
  }
  w._AMapSecurityConfig = { securityJsCode: code }

  loadPromise = new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      amapError.value = '地图加载超时（请检查高德 Key 白名单是否包含 localhost）'
      reject(new Error(amapError.value))
    }, 8000)
    const script = document.createElement('script')
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${key}&plugin=AMap.Geocoder,AMap.Scale`
    script.async = true
    script.onload = () => {
      window.clearTimeout(timer)
      if (!w.AMap) {
        amapError.value = '高德 JS API 初始化失败'
        reject(new Error(amapError.value)); return
      }
      amapAvailable.value = true
      resolve(w.AMap)
    }
    script.onerror = () => {
      window.clearTimeout(timer)
      amapError.value = '高德地图脚本加载失败（Key 白名单未配置或无网络），可改用手动输入坐标'
      reject(new Error(amapError.value))
    }
    document.head.appendChild(script)
  })
  return loadPromise
}

/** 下单选点图：点击/拖拽设置寄件或收件点，自动逆地理回填地址，寄收点之间画直线。 */
export async function createPickerMap(container: HTMLElement, onChange: (kind: 'sender' | 'receiver', p: LngLatPoint) => void) {
  const AMap = await loadAmap()
  const map = new AMap.Map(container, { zoom: 12, center: WENZHOU_CENTER, viewMode: '2D' })
  map.addControl && map.addControl(new AMap.Scale())
  const geocoder = new AMap.Geocoder({ city: '温州', extensions: 'base' })

  const points: Record<'sender' | 'receiver', LngLatPoint | null> = { sender: null, receiver: null }
  const markers: Record<'sender' | 'receiver', any> = { sender: null, receiver: null }
  let active: 'sender' | 'receiver' = 'sender'
  let polyline: any = null

  function reverse(lng: number, lat: number, cb: (addr: string) => void) {
    geocoder.getAddress([lng, lat], (status: string, result: any) => {
      if (status === 'complete' && result?.regeocode) cb(result.regeocode.formattedAddress)
      else cb('')
    })
  }

  function redrawLine() {
    if (polyline) map.remove(polyline)
    if (points.sender && points.receiver) {
      polyline = new AMap.Polyline({
        path: [[points.sender.lng, points.sender.lat], [points.receiver.lng, points.receiver.lat]],
        strokeColor: '#23406E', strokeWeight: 3, strokeStyle: 'dashed', showDir: true,
      })
      map.add(polyline)
      map.setFitView([markers.sender, markers.receiver])
    }
  }

  function drawPoint(kind: 'sender' | 'receiver', lng: number, lat: number, address?: string) {
    points[kind] = { lng, lat, address }
    if (markers[kind]) map.remove(markers[kind])
    const color = kind === 'sender' ? '#23406E' : '#C8A45C'
    markers[kind] = new AMap.Marker({
      position: [lng, lat], draggable: true, cursor: 'move',
      content: `<div style="background:${color};color:#fff;border-radius:50%;width:26px;height:26px;display:flex;align-items:center;justify-content:center;font-size:12px;box-shadow:0 2px 6px rgba(0,0,0,.3)">${kind === 'sender' ? '寄' : '收'}</div>`,
    })
    markers[kind].on('dragend', (e: any) => {
      const p = e.lnglat || markers[kind].getPosition()
      const lng = Number(p.lng?.toFixed?.(6) ?? p.getLng?.().toFixed(6))
      const lat = Number(p.lat?.toFixed?.(6) ?? p.getLat?.().toFixed(6))
      reverse(lng, lat, (addr) => onChange(kind, { lng, lat, address: addr }))
    })
    map.add(markers[kind])
    // 选完点让地图飞过去：另一点已有则双方适配，否则聚焦当前点
    if (points[kind === 'sender' ? 'receiver' : 'sender']) {
      map.setFitView([markers.sender, markers.receiver])
    } else {
      map.setZoomAndCenter(15, [lng, lat])
    }
    redrawLine()
    onChange(kind, { lng, lat, address })
  }

  function setPoint(kind: 'sender' | 'receiver', lng: number, lat: number, address?: string) {
    // 未显式给地址（地图外程序化选点/手动经纬度）时先逆地理，再回填
    if (address === undefined) {
      reverse(lng, lat, (addr) => drawPoint(kind, lng, lat, addr))
    } else {
      drawPoint(kind, lng, lat, address)
    }
  }

  map.on('click', (e: any) => {
    const lng = Number(e.lnglat.lng.toFixed(6))
    const lat = Number(e.lnglat.lat.toFixed(6))
    reverse(lng, lat, (addr) => drawPoint(active, lng, lat, addr))
  })

  return {
    setActive(kind: 'sender' | 'receiver') { active = kind },
    setPoint,
    destroy() { map.destroy() },
  }
}

/** 订单详情轨迹图：寄/收两个标记 + 航线（W4 接 WS 后在此叠加飞机实时位置）。 */
export async function createRouteMap(container: HTMLElement, sender: LngLatPoint, receiver: LngLatPoint) {
  const AMap = await loadAmap()
  const map = new AMap.Map(container, { zoom: 12, center: WENZHOU_CENTER })
  const mk = (p: LngLatPoint, text: string, color: string) => new AMap.Marker({
    position: [p.lng, p.lat],
    content: `<div style="background:${color};color:#fff;border-radius:50%;width:28px;height:28px;display:flex;align-items:center;justify-content:center;font-size:12px;box-shadow:0 2px 6px rgba(0,0,0,.3)">${text}</div>`,
  })
  const m1 = mk(sender, '寄', '#23406E')
  const m2 = mk(receiver, '收', '#C8A45C')
  const line = new AMap.Polyline({
    path: [[sender.lng, sender.lat], [receiver.lng, receiver.lat]],
    strokeColor: '#23406E', strokeWeight: 3, strokeStyle: 'dashed', showDir: true,
  })
  map.add([m1, m2, line])
  map.setFitView([m1, m2])
  return { destroy() { map.destroy() } }
}

/** 空域配色（与 AIRSPACE_TYPE_MAP 同口径） */
const AIRSPACE_COLORS: Record<number, string> = {
  1: '#C0392B', 2: '#D39A2B', 3: '#2E7D52', 4: '#8E44AD',
}

export interface NamedPoint extends LngLatPoint { name?: string }

export interface DispatchMapOptions {
  airspaces?: {
    airspaceType: number
    airspaceName?: string
    geometry: { type: string; coordinates: number[][][] }
  }[]
  route?: { lng: number; lat: number }[]
  /** 调机返场航段（收件点→降落场），浅色虚线，与载货航段区分 */
  routeReturn?: { lng: number; lat: number }[]
  markers?: {
    takeoff?: NamedPoint
    landing?: NamedPoint
    sender?: NamedPoint
    receiver?: NamedPoint
    live?: NamedPoint
  }
  /** 只高亮某条航线涉及的空域，其余淡化（审批台用） */
  highlightOnly?: boolean
  /** 视野范围：'route' 只框定航线+标记（任务/详情用）；默认框定含空域的全部要素 */
  fit?: 'route' | 'all'
}

function labelMarker(AMap: any, p: NamedPoint, text: string, color: string, pulse = false) {
  const ring = pulse ? 'box-shadow:0 0 0 rgba(192,57,43,.6);animation:ts-pulse 1.6s infinite;' : ''
  return new AMap.Marker({
    position: [p.lng, p.lat],
    content: `<div style="position:relative;background:${color};color:#fff;border-radius:50%;width:30px;height:30px;display:flex;align-items:center;justify-content:center;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.35);border:2px solid #fff;${ring}">${text}</div>`,
    title: p.name || '',
  })
}

/**
 * 调度/审批/计划详情通用地图：空域多边形（禁飞红/限飞黄/可飞绿/管制紫）
 * + 金色航线 + 起降场/寄收/实时位置标记。
 * 返回 setLive 更新飞机位置、destroy 销毁。
 */
export async function createDispatchMap(container: HTMLElement, opts: DispatchMapOptions = {}) {
  const AMap = await loadAmap()
  const map = new AMap.Map(container, { zoom: 12, center: WENZHOU_CENTER, viewMode: '2D' })
  map.addControl && map.addControl(new AMap.Scale())
  const overlays: any[] = []
  const fitPts: [number, number][] = []
  const fitTo = () => {
    if (!fitPts.length) return
    let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity
    for (const [lng, lat] of fitPts) {
      if (!(lng >= -180 && lng <= 180 && lat >= -90 && lat <= 90)) continue
      minLng = Math.min(minLng, lng); maxLng = Math.max(maxLng, lng)
      minLat = Math.min(minLat, lat); maxLat = Math.max(maxLat, lat)
    }
    if (!isFinite(minLng)) return
    if (minLng === maxLng) { minLng -= 0.01; maxLng += 0.01; minLat -= 0.01; maxLat += 0.01 }
    map.setBounds(new AMap.Bounds([minLng, minLat], [maxLng, maxLat]), false, [50, 50, 50, 50])
  }

  // 1. 空域多边形
  for (const asp of opts.airspaces || []) {
    const color = AIRSPACE_COLORS[asp.airspaceType] || '#666'
    const rings = asp.geometry?.coordinates || []
    for (const ring of rings) {
      const path = ring.map(([lng, lat]) => [lng, lat] as [number, number])
      if (opts.fit !== 'route') fitPts.push(...path)
      const polygon = new AMap.Polygon({
        path,
        strokeColor: color, strokeWeight: 2, strokeOpacity: 0.9,
        fillColor: color, fillOpacity: 0.12, strokeStyle: asp.airspaceType === 3 ? 'dashed' : 'solid',
      })
      map.add(polygon)
      overlays.push(polygon)
      // 类型标签放在多边形首个顶点附近
      if (path[0]) {
        const txt = new AMap.Text({
          text: asp.airspaceName || '',
          position: path[0],
          style: {
            color, border: `1px solid ${color}`, 'border-radius': '3px',
            'background-color': 'rgba(255,255,255,.85)', 'font-size': '11px', padding: '1px 5px',
          },
        })
        map.add(txt)
        overlays.push(txt)
      }
    }
  }

  // 2. 航线（载货航段金色实线 + 方向箭头；调机返场航段浅金虚线）
  if (opts.route && opts.route.length > 1) {
    const path = opts.route.map((p) => [p.lng, p.lat] as [number, number])
    fitPts.push(...path)
    const line = new AMap.Polyline({
      path,
      strokeColor: '#C8A45C', strokeWeight: 4, strokeOpacity: 0.95, showDir: true,
    })
    map.add(line)
    overlays.push(line)
  }
  if (opts.routeReturn && opts.routeReturn.length > 1) {
    const path = opts.routeReturn.map((p) => [p.lng, p.lat] as [number, number])
    fitPts.push(...path)
    const back = new AMap.Polyline({
      path,
      strokeColor: '#C8A45C', strokeWeight: 3, strokeOpacity: 0.5,
      strokeStyle: 'dashed', showDir: false,
    })
    map.add(back)
    overlays.push(back)
  }

  // 3. 标记
  const mk = opts.markers || {}
  const addMk = (p: NamedPoint | undefined, text: string, color: string, pulse = false) => {
    if (!p) return
    fitPts.push([p.lng, p.lat])
    const m = labelMarker(AMap, p, text, color, pulse)
    map.add(m)
    overlays.push(m)
  }
  // 起飞场与降落场为同一坐标（短途门到门、送完返场）时合并成一个场站标记，
  // 避免两个标记重叠、被误读成“中途还要降落一次”
  const sameStation = mk.takeoff && mk.landing
    && Math.abs(mk.takeoff.lng - mk.landing.lng) < 1e-4
    && Math.abs(mk.takeoff.lat - mk.landing.lat) < 1e-4
  if (sameStation && mk.takeoff) {
    addMk({ ...mk.takeoff, name: `${mk.takeoff.name || '起降场站'}（起飞/返场）` }, '场', '#23406E')
  } else {
    addMk(mk.takeoff, '起', '#23406E')
    addMk(mk.landing, '降', '#23406E')
  }
  addMk(mk.sender, '寄', '#3D6B9E')
  addMk(mk.receiver, '收', '#C8A45C')
  let liveMk: any = null
  if (mk.live) {
    liveMk = labelMarker(AMap, mk.live, '机', '#C0392B', true)
    map.add(liveMk)
    overlays.push(liveMk)
  }

  // setFitView 对 Text/Polygon 在地图未 complete 时可能失效（缩到全国），
  // 改为手动算 bounds，并在 complete 与延时兜底各执行一次
  map.on('complete', fitTo)
  window.setTimeout(fitTo, 300)

  return {
    map,
    setLive(lng: number, lat: number) {
      if (liveMk) liveMk.setPosition([lng, lat])
    },
    destroy() { map.destroy() },
  }
}
