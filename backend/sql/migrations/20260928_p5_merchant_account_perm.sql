-- ---------------------------------------------------------------------
-- P5：商家管理员的账号维护权限（2026-09-28）
-- 背景：差距分析复查发现，system 蓝图（M-81 账号维护）按设计写明了数据范围口径
--       "商家员工（user_type=3）只看/只管本商家账号"，接口层也用
--       scope.merchant_scope + _assert_manageable 做了双重收窄；但种子里
--       merchant_admin（role_id=4）没有 system:user 权限点，导致商家管理员
--       连自己公司的子账号都建不了 —— 前端系统中心页整个 tab 都不出现，
--       M-81 的"商家自助开户/停号"只能靠平台管理员代劳。
-- 处理：只补 27（system:user，账号维护）+ 8（system 一级菜单，否则侧边栏进不去系统中心）。
--       不补 28（system:role，角色与权限配置）：角色授权集合属于平台侧资产，
--       商家能改角色就等于能给自己加权限。因此商家可以开号/停用/重置密码，
--       改岗位仍需平台管理员（set_user_roles 挂的是 system:role）。
-- 幂等：INSERT IGNORE（role_permission 有唯一索引），不改动任何已有数据。
-- 用法：python scripts/apply_migration.py sql/migrations/20260928_p5_merchant_account_perm.sql
-- ---------------------------------------------------------------------

INSERT IGNORE INTO role_permission (role_id, permission_id)
SELECT 4, p.id FROM permission p
WHERE p.id IN (8, 27) AND p.perm_code IN ('system', 'system:user') AND p.deleted = 0;
