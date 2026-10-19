-- =====================================================================
-- 迁移：P1 订单检索 / 导出 / 服务空域（2026-09-29，批次 B2）
--
-- 三件事，都由差距分析 §3.1 的"看着有、其实没通电"引出来：
--
-- 1) orders 补 4 个摘要列。
--    sender_phone / receiver_phone 是 AES-256-GCM 密文，每次加密 nonce 随机，
--    同一手机号两行密文完全不同 → LIKE 永远搜不到。前端输入框写着"订单号 / 手机号"，
--    后端只查 order_no / goods_type，所以输手机号恒 0 结果（差距分析列为 Bug）。
--    可检索只能靠确定性摘要：整号摘要管"完整手机号"，后 4 位摘要管"尾号"，
--    两者都是 HMAC-SHA256，不落明文，不破需求 10.4 的 PII 加密口径。
--    ★ 存量行的摘要必须由 scripts/backfill_order_phone.py 解密后回填（SQL 里算不了 HMAC），
--      本迁移只负责加列，回填见该脚本；未回填的历史行搜不到，属预期。
--
-- 2) 新增 2 个权限点：order:export（导出含收件人电话的明细）、order:force_cancel
--    （平台/商家强制终止订单）。导出是"批量把 PII 搬出系统"，和看一页列表不是一个量级，
--    所以单独设权限点，不蹭 order:list。
--
-- 3) merchants.service_area_json 从"区域名数组"升级为"区域名 + 覆盖多边形"。
--    旧值 JSON_ARRAY('瓯江口新区','龙湾区') 只有名字，订单表里没有区域字段，
--    拿名字没法判断一个经纬度在不在服务范围内（要逆地理编码，演示环境没有）。
--    补一个覆盖多边形后，下单/批量导入才真能拦"超出服务范围"的寄收点。
--    只改 ARRAY 形态的旧值 + 上一版那个画窄了的框，其余人工编辑过的不覆盖，重跑幂等。
--
-- 幂等：列用 information_schema 守卫，权限用 INSERT IGNORE，JSON 用 JSON_TYPE 条件。
-- 注意：本文件不能出现 % 字符（apply_migration 走 exec_driver_sql，pyformat 参数化）。
-- 用法：FLASK_SIM_DISABLED=1 python scripts/apply_migration.py \
--       sql/migrations/20260929_p12_order_search.sql
-- =====================================================================

-- 1) 手机号摘要列 ------------------------------------------------------
SET @c := (SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'orders' AND COLUMN_NAME = 'sender_phone_digest');
SET @s := IF(@c = 0,
  'ALTER TABLE orders
     ADD COLUMN sender_phone_digest  CHAR(64) NULL COMMENT ''寄件手机号HMAC摘要（整号检索）'' AFTER sender_phone,
     ADD COLUMN sender_phone_tail    CHAR(64) NULL COMMENT ''寄件手机号后4位摘要（尾号检索）'' AFTER sender_phone_digest,
     ADD COLUMN receiver_phone_digest CHAR(64) NULL COMMENT ''收件手机号HMAC摘要（整号检索）'' AFTER receiver_phone,
     ADD COLUMN receiver_phone_tail  CHAR(64) NULL COMMENT ''收件手机号后4位摘要（尾号检索）'' AFTER receiver_phone_digest,
     ADD KEY idx_order_sender_phone (sender_phone_digest),
     ADD KEY idx_order_receiver_phone (receiver_phone_digest)',
  'SELECT 1');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 2) 权限点 ------------------------------------------------------------
-- permission 列序：id,parent_id,perm_code,perm_name,perm_type,path,icon,sort
-- 线上已用到 id=41（demo:run），新点从 42 起。
INSERT IGNORE INTO permission (id, parent_id, perm_code, perm_name, perm_type, path, icon, sort) VALUES
 (42, 2,'order:export','订单导出',3,NULL,NULL,5),
 (43, 2,'order:force_cancel','订单强制终止',3,NULL,NULL,6);

-- 1 平台管理员：两个都要（导出与终止是平台侧运营手段）
INSERT IGNORE INTO role_permission (role_id, permission_id) VALUES (1, 42),(1, 43);
-- 4 商家管理员：本商家范围内导出 + 终止（服务层再按 merchant_id 收口）
INSERT IGNORE INTO role_permission (role_id, permission_id) VALUES (4, 42),(4, 43);

-- 3) 商家服务空域：区域名 + 覆盖多边形（GCJ-02，首点=末点闭合） ----------
-- 矩形要同时罩住两类数据，缺一类都会把下单链路堵死：
--   DDL 7.4 的两座演示场站：瓯江口综合示范站(120.9295,27.9555)、龙湾城市起降场(120.815,27.945)；
--   演示库里实际在跑的场站与历史订单端点：温州南站(120.669,28.012)、温州商学院(120.700,27.913)
--   以及 62 个聚在 (120.70,28.00) 附近的取送货点。
-- 第一版只按 DDL 的两座场站画了 120.76~120.99 的框，把演示库西侧那批场站整个漏掉了
-- （库是边演示边长出来的，DDL 只是初始种子），照那个框下单会全被"超出服务空域"拒掉。
-- 龙湾机场净空禁飞区落在服务范围内部是有意为之——
-- "在服务区里"和"航线合规"是两道独立的闸门，不能合并。
UPDATE merchants
   SET service_area_json = CAST('{"districts":["瓯江口新区","龙湾区","鹿城区","瓯海区"],'
       '"geometry":{"type":"Polygon","coordinates":[[[120.600,27.880],[121.000,27.880],'
       '[121.000,28.050],[120.600,28.050],[120.600,27.880]]]}}' AS JSON),
       updated_at = NOW()
 WHERE id = 1 AND JSON_TYPE(service_area_json) = 'ARRAY';

-- 把上一版按 DDL 场站画窄了的框纠正过来（只认那一组首点坐标，人工配过的不动）。
-- 本语句必须跟在上面那条之后：新库重放时第一条已经写好正确值，这里自然 0 行。
UPDATE merchants
   SET service_area_json = CAST('{"districts":["瓯江口新区","龙湾区","鹿城区","瓯海区"],'
       '"geometry":{"type":"Polygon","coordinates":[[[120.600,27.880],[121.000,27.880],'
       '[121.000,28.050],[120.600,28.050],[120.600,27.880]]]}}' AS JSON),
       updated_at = NOW()
 WHERE id = 1
   AND JSON_EXTRACT(service_area_json, '$.geometry.coordinates[0][0][0]') = 120.760
   AND JSON_EXTRACT(service_area_json, '$.geometry.coordinates[0][0][1]') = 27.890;
