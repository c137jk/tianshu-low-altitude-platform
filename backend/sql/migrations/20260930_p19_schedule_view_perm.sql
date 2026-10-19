-- =====================================================================
-- 迁移：排班「只读」权限点（2026-09-30 / 用户决定：空管、财务要看别人的排班，但只能看）
--
-- 背景：resource:schedule(37) 一份权限同时管着"看班表"和"改班表"——GET /aux/schedules
--       认它，POST /aux/schedules 与 DELETE /aux/schedules/<id> 也只认它。要让空管、财务
--       进来就只能整份给，一给就顺带把写权给了。所以把"看"单独拆一个 code，写门一寸不动。
--
-- 授权口径：
--   2 air_traffic 空管监管员：平台侧身份（user_type 5），看全区班表
--   6 finance     财务：种子账号是商家侧员工（user_type 3 / merchant 1），
--                        能看的那一份仍由 scope.merchant_scope 收在本商家，不给跨商家
--   3 运维 / 4 商家管理员 / 5 调度员 / 8 设备运维员：已持有 37（含写），不必再给 48
--   1 平台管理员：走 User.has_perm 通配，无需显式授权行
--
-- 为什么连 id=5（'resource' 一级菜单，perm_type=1）也要授：`auth_service.build_menu_tree:93`
-- 只把"角色直接持有且 perm_type=1"的行当模块节点，父节点不在授权集合里时，叶子 48 组不出菜单。
-- 模块行本身不带任何接口能力，前端 `subPages` 仍按叶子 code 过滤，所以他们进去只看得到排班查看。
--
-- 注意：本文件不能出现 % 字符（apply_migration 走 exec_driver_sql，pyformat 参数化）。
-- 幂等：全部 INSERT IGNORE，可重复执行。
-- 用法：FLASK_SIM_DISABLED=1 python scripts/apply_migration.py \
--       sql/migrations/20260930_p19_schedule_view_perm.sql
-- =====================================================================

INSERT IGNORE INTO permission (id, parent_id, perm_code, perm_name, perm_type, path, icon, sort) VALUES
 (48, 5,'resource:schedule:view','排班查看（只读）',3,NULL,NULL,7);

-- 一级菜单节点（模块容器，不带接口能力）+ 只读叶子
INSERT IGNORE INTO role_permission (role_id, permission_id) VALUES
 (2, 5),(2, 48),
 (6, 5),(6, 48);
