-- =====================================================================
-- 迁移：D8 统计报表中心的权限点（2026-09-29 / B6）
--
-- 背景：/aux/report 这个"全平台快照"式的监管报告早就挂了 report:view(id=40)，
--       但 M-12 统计报表中心要做的是"区间 + 维度 + 导出"，读的是同一批表；
--       把 report:view 一权两用会让"能看大屏"和"能把经营数据导成 CSV"分不开，
--       所以导出单独设一个 report:export。
--
-- 授权口径（设计06 M-12：可见角色 管理员/财务；/stats/merchant 是商家大屏）：
--   report:view    1 平台管理员 · 2 空管 · 3 运维（已有 40 的授权）+ 本次补 4 商家管理员、6 财务
--   report:export  1/2/3/4/6 —— 商家管理员能导本商家的，导不出别人的（范围在 SQL 里收）
--
-- 注意：本文件不能出现 % 字符（apply_migration 走 exec_driver_sql，pyformat 参数化）。
-- 幂等：INSERT IGNORE，可重复执行。
-- 用法：FLASK_SIM_DISABLED=1 python scripts/apply_migration.py \
--       sql/migrations/20260929_p16_report_export_perm.sql
-- =====================================================================

INSERT IGNORE INTO permission (id, parent_id, perm_code, perm_name, perm_type, path, icon, sort) VALUES
 (47, 1,'report:export','报表导出',3,NULL,NULL,3);

-- 已有 40 的行在旧库里 sort=2、挂在 dashboard(1) 下；新库复现时保持同一口径
UPDATE permission SET parent_id = 1, sort = 2 WHERE id = 40;

INSERT IGNORE INTO role_permission (role_id, permission_id) VALUES
 (4, 40),(6, 40),
 (1, 47),(2, 47),(3, 47),(4, 47),(6, 47);
