-- =====================================================================
-- 迁移：找回被软删的「空域合规」一级菜单权限点（2026-09-28，P0 回归时发现）
-- 现象：现网库 tianshu_utms 里 permission id=4 perm_code='airspace' 的 deleted=1
--       （updated_at=2026-09-23 13:13:29，是人工改的，DDL 种子里明明是 0）。
-- 影响：/api/auth/menus 少掉整个 D5 域，前端"空域地图/空域审核"页面从导航里消失；
--       子权限 airspace:draw(20) / airspace:audit(21) 与角色授权都还在，
--       只是父节点被摘掉，动态菜单树挂不上去 —— 属于"数据漂移"而不是代码缺陷，
--       但表现和权限没收口一模一样，极易误判。
-- 口径：只把这一行的 deleted 改回 0；不新增权限点、不改任何 role_permission、
--       不动演示业务数据。幂等：已经是 0 时 UPDATE 命中 0 行，可重复执行。
-- 用法：python scripts/apply_migration.py sql/migrations/20260928_p7_airspace_menu.sql
-- =====================================================================

UPDATE permission
   SET deleted = 0
 WHERE perm_code = 'airspace' AND parent_id = 0 AND deleted = 1;

-- 自检（手工在客户端执行，不放在本脚本里：迁移执行器会把 % 当格式化占位符）：
--   SELECT COUNT(*) FROM permission
--    WHERE deleted = 0 AND (perm_code = 'airspace' OR perm_code LIKE 'airspace:%');   -- 期望 3
