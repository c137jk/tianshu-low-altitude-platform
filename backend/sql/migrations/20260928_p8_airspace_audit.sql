-- =====================================================================
-- 迁移：空域审核留痕 + 电子围栏启用（2026-09-28，需求 4.2.4 空域合规运营）
-- 背景：差距分析里 M-42（空域申报审核流）与 M-43（电子围栏）判"缺"的两个根因：
--       1) airspace 表只有 status（生效/失效），没有任何"谁审的、什么时候、什么意见"
--          的列，前端也就做不出空域台账页 —— 只能读，改完无人负责；
--       2) geofence 表在 DDL 里建好了但全仓 0 引用，围栏功能等于不存在。
-- 口径：audit_status 默认 1（通过）。现网 3 条空域是空管同步/初始种子来的，
--       视为已审；只有平台自绘新增才走 0 待审核，不改动任何已有业务数据。
--       生效判定（active_airspaces）只认 audit_status=1，所以待审核/驳回的空域
--       不会参与合规计算，也不会污染演示。
-- 幂等：information_schema 守卫 + PREPARE/EXECUTE；围栏种子用 WHERE NOT EXISTS。
-- 用法：python scripts/apply_migration.py sql/migrations/20260928_p8_airspace_audit.sql
-- =====================================================================

-- 1) airspace 审核留痕四列 ----------------------------------------------
SET @c := (SELECT COUNT(*) FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'airspace'
             AND COLUMN_NAME = 'audit_status');
SET @s := IF(@c = 0,
  'ALTER TABLE airspace
     ADD COLUMN audit_status TINYINT NOT NULL DEFAULT 1 COMMENT ''0待审核1通过2驳回'' AFTER status,
     ADD COLUMN audited_by BIGINT UNSIGNED NULL COMMENT ''审核人'' AFTER audit_status,
     ADD COLUMN audited_at DATETIME NULL COMMENT ''审核时间'' AFTER audited_by,
     ADD COLUMN audit_opinion VARCHAR(500) NULL COMMENT ''审核意见/驳回原因'' AFTER audited_at',
  'SELECT 1');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 2) geofence 表（新库复现用；现网 DDL 已建则跳过） ----------------------
CREATE TABLE IF NOT EXISTS geofence (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  fence_name      VARCHAR(80) NOT NULL,
  merchant_id     BIGINT UNSIGNED NULL COMMENT '设置方，空=平台（对全部商家生效）',
  geometry_json   JSON NOT NULL,
  fence_type      TINYINT NOT NULL COMMENT '1限入2限出3接近告警',
  altitude_limit  INT NULL,
  warn_distance_m INT NULL COMMENT '触发告警距离m',
  status          TINYINT NOT NULL DEFAULT 1 COMMENT '1启用2停用',
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted         TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY idx_merchant (merchant_id)
) ENGINE=InnoDB COMMENT='电子围栏（运营自定义）';

-- 3) 围栏演示种子：一条平台级限入（远离航线，不会误伤在飞计划）、
--    一条商家级接近告警（贴着瓯江口起飞方向，用来演示"围栏下发预警"）----
INSERT INTO geofence (fence_name, merchant_id, geometry_json, fence_type,
                      altitude_limit, warn_distance_m, status)
SELECT * FROM (SELECT
  '洞头危化品码头限入围栏' AS a, NULL AS b,
  '{"type":"Polygon","coordinates":[[[120.858,27.862],[120.876,27.862],[120.876,27.876],[120.858,27.876],[120.858,27.862]]]}' AS c,
  1 AS d, 60 AS e, 150 AS f, 1 AS g) t
WHERE NOT EXISTS (SELECT 1 FROM geofence WHERE fence_name = '洞头危化品码头限入围栏');

INSERT INTO geofence (fence_name, merchant_id, geometry_json, fence_type,
                      altitude_limit, warn_distance_m, status)
SELECT * FROM (SELECT
  '瓯江口灵昆岛人群密集区接近告警' AS a, 1 AS b,
  '{"type":"Polygon","coordinates":[[[120.916,27.942],[120.936,27.942],[120.936,27.958],[120.916,27.958],[120.916,27.942]]]}' AS c,
  3 AS d, NULL AS e, 250 AS f, 1 AS g) t
WHERE NOT EXISTS (SELECT 1 FROM geofence WHERE fence_name = '瓯江口灵昆岛人群密集区接近告警');

-- 自检（手工执行，别写进本脚本：迁移执行器会把百分号当格式化占位符）：
--   SELECT COUNT(*) FROM information_schema.COLUMNS
--    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'airspace'
--      AND COLUMN_NAME IN ('audit_status','audited_by','audited_at','audit_opinion');  -- 期望 4
--   SELECT fence_name, fence_type, merchant_id FROM geofence WHERE deleted = 0;        -- 期望 2
