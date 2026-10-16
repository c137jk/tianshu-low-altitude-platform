# 天枢前端（Vue3 + TS + Vite + Element Plus）

对应设计06 信息架构、设计08 W1 任务、设计09 藏青金主题（D-28）。

## 启动（需先跑起后端 backend，端口 5000）

```powershell
npm install        # 首次
npm run dev        # 开发，默认 http://localhost:5173
```

其他命令：

```powershell
npm run type-check # TS 类型检查
npm run build      # 生产构建（输出 dist/）
```

Vite 已把 `/api` 代理到 `http://127.0.0.1:5000`，前端不写死后端地址、无跨域问题。

## 演示账号（密码统一 Admin@123）

| 账号 | 角色 | 登录后去向 |
|---|---|---|
| admin | 平台管理员 | 管控台（8 个菜单全量） |
| merchant | 商家管理员/调度员 | 管控台（6 个菜单） |
| customer | 个人客户（李客户，有演示订单） | 客户门户 |
| cust01~cust05 | 个人客户（造数生成，密码同为 Admin@123） | 客户门户 |

## 目录结构

```
src/
├─ api/            # 按后端领域 D1~D9 分文件（当前 auth.ts）
├─ layouts/        # AdminLayout 管控台 / PortalLayout 客户门户
├─ router/         # 固定路由 + 按后端权限树 addRoute 动态路由 + 登录守卫
├─ stores/         # Pinia：user（token/用户/菜单/权限）
├─ styles/         # variables.scss 主题令牌（换肤只改这里）+ global.scss
├─ utils/request.ts# Axios 封装：自动带 token、统一信封、401 处理
├─ views/
│  ├─ login/       # 登录页（藏青金）
│  ├─ admin/       # 管控台页面（Dashboard 真页面，其余先占位）
│  ├─ portal/      # 客户门户（W2 起逐页建设）
│  └─ errors/      # 403 / 404
├─ composables/    # useAmap / useWebSocket（后续批次）
└─ main.ts
```

## 主题（D-28）

- 藏青 `#23406E` 主色、金 `#C8A45C` 点缀、米白 `#FBF9F4` 底；
- Element Plus 主题通过 `src/styles/variables.scss` 的 CSS 变量覆盖；
- 监控大屏（W4）独立深蓝黑配色，走 `/screen` 全屏路由。

## 当前进度

**W2（客户闭环，已完成并实测）**：客户门户 6 个真页面——
`portal/HomeView`（首页+在途订单）、`OrderCreate`（高德选点/逆地理/实时试算/Mock 收银台）、
`OrderList`（Tab：全部/待支付/进行中/已完成/已取消，取消与确认收货）、
`OrderDetail`（进度步骤条、航线图、费用明细、支付/取消/确认）、
`BillsView`（支付退款流水 + 结算单两表）、`ProfileView`（资料 + 改密强制重登）。
对应 API 在 `src/api/order.ts`，字典/状态映射在 `src/constants/dict.ts`，高德封装在 `src/composables/useAmap.ts`。

- 高德 Key 配置在 `.env.development`（VITE_AMAP_JS_KEY / VITE_AMAP_JS_CODE）；
  Key 白名单未配 localhost 时，下单页自动降级为手动经纬度输入，不阻断流程。
- 开发环境下单页挂有调试钩子 `window.__tsPicker.setPoint(kind, lng, lat)` 模拟地图选点（生产构建不挂载）。

**W1（登录骨架）**：登录发 JWT、按角色分流、后端权限树驱动左侧菜单、路由守卫与 403、Axios 统一拦截。
其余管控台页面为"建设中"占位，按设计08 批次顺序替换为真页面。
