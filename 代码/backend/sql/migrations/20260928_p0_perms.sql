-- =====================================================================
-- 迁移：P0 权限门禁补齐（2026-09-28）
-- 背景：差距分析发现 93 个端点里只有 12 个挂了权限点。本迁移为新增的服务端
--       门禁补权限点与角色授权，并给 work_order 补商家维度以支持数据收口。
-- 注意：线上库 permission 已有 35 行（34 marketing / 35 service 两个一级菜单
--       是后补的，未回写 DDL 主文件），因此新权限点从 id=36 起。
--       permission 表列序为 id,parent_id,perm_code,perm_name,perm_type,path,icon,sort。
-- 幂等：全部 INSERT IGNORE / information_schema 守卫，可重复执行。
-- 用法：python scripts/apply_migration.py sql/migrations/20260928_p0_perms.sql
-- =====================================================================

-- 0) 把线上已有、但 DDL 主文件缺的两个一级菜单补齐（新库复现用） --------
INSERT IGNORE INTO permission (id, parent_id, perm_code, perm_name, perm_type, path, icon, sort) VALUES
 (34, 0,'marketing','营销中心',1,'/marketing',NULL,9),
 (35, 0,'service',  '客服中心',1,'/service',  NULL,10);

-- 1) 新增 6 个权限点 ----------------------------------------------------
INSERT IGNORE INTO permission (id, parent_id, perm_code, perm_name, perm_type, path, icon, sort) VALUES
 (36,35,'service:workorder','客服工单处理',3,NULL,NULL,1),
 (37, 5,'resource:schedule', '排班管理',   3,NULL,NULL,4),
 (38,34,'marketing:coupon',  '优惠券管理', 3,NULL,NULL,1),
 (39, 8,'system:article',    '公告与帮助内容',3,NULL,NULL,9),
 (40, 1,'report:view',       '监管统计报告',3,NULL,NULL,2),
 (41, 8,'demo:run',          '一键演示流程',3,NULL,NULL,10);

-- 2) 角色授权（最小够用） ----------------------------------------------
-- 1 平台管理员：全部新权限点
INSERT IGNORE INTO role_permission (role_id, permission_id)
SELECT 1, id FROM permission WHERE id BETWEEN 34 AND 41;

-- 2 空管监管员：监管统计报告
INSERT IGNORE INTO role_permission (role_id, permission_id) VALUES (2, 40);

-- 3 平台运维：工单、排班、报告、演示
INSERT IGNORE INTO role_permission (role_id, permission_id) VALUES (3, 36),(3, 37),(3, 40),(3, 41);

-- 4 商家管理员：工单、排班（优惠券属平台侧营销资产，券表无 merchant_id，不给商家）
INSERT IGNORE INTO role_permission (role_id, permission_id) VALUES (4, 36),(4, 37);

-- 5 调度员：工单、排班
INSERT IGNORE INTO role_permission (role_id, permission_id) VALUES (5, 36),(5, 37);

-- 8 设备运维员：工单、排班
INSERT IGNORE INTO role_permission (role_id, permission_id) VALUES (8, 36),(8, 37);

-- 2b) 平台运维补齐日常运维权限（参数配置 system:config、优惠券 marketing:coupon） --
INSERT IGNORE INTO role_permission (role_id, permission_id) VALUES (3, 29),(3, 38);
-- 收敛早期版本误授：商家管理员不持有平台营销权限
DELETE FROM role_permission WHERE role_id = 4 AND permission_id = 38;

-- 3) work_order 补商家维度（客服工单数据范围收口用） --------------------
SET @c := (SELECT COUNT(*) FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'work_order' AND COLUMN_NAME = 'merchant_id');
SET @s := IF(@c = 0,
  'ALTER TABLE work_order ADD COLUMN merchant_id BIGINT UNSIGNED NULL COMMENT ''归属商家，平台侧工单为 NULL'' AFTER handler_id, ADD KEY idx_wo_merchant (merchant_id)',
  'SELECT 1');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;
