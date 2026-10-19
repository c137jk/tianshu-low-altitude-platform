-- =====================================================================
-- M-55 场站预约配套：权限点 + 授权
--
-- 表 station_reservation 在 v1 DDL 里已经建好（含 uk_reservation_no、idx_station_time
-- 与四个外键），本页只补权限点——没有权限点的接口在菜单里进不去，perm_required 也会 403。
--
-- 拆两个点的原因：申请/取消/回执是商家自己的事（resource:reservation），
-- 机位放行是场站运营方的事（resource:reservation:audit），
-- 合成一个点会让商家审核别人、或让运维取消商家的预约。
--
-- 幂等：permission / role_permission 全部 INSERT IGNORE。
-- 注意：本文件不能出现 % 字符（apply_migration 走 exec_driver_sql，pyformat 参数化）。
-- 用法：FLASK_SIM_DISABLED=1 python scripts/apply_migration.py \
--       sql/migrations/20260929_p14_station_reservation.sql
-- =====================================================================

-- 1) 权限点 ----------------------------------------------------------
-- permission 列序：id,parent_id,perm_code,perm_name,perm_type,path,icon,sort
-- 线上已用到 id=44（monitor:device），本批取 45/46；父节点 5=低空资源，sort 接在 37 之后。
INSERT IGNORE INTO permission (id, parent_id, perm_code, perm_name, perm_type, path, icon, sort)
 VALUES (45, 5,'resource:reservation','场站预约',3,NULL,NULL,5),
        (46, 5,'resource:reservation:audit','场站预约审核',3,NULL,NULL,6);

-- 2) 授权 ------------------------------------------------------------
-- 1 平台管理员在 DDL 里是"SELECT id FROM permission"一次性铺满的，新增点必须补授权，
-- 否则 admin 也看不到这个菜单（D-96）。
-- 申请：平台管理员/运维/商家管理员/商家运维，与 resource:vertiport(23) 同一批人；
-- 审核：只给平台管理员与运维——机位余量是场站运营方的裁量权。
INSERT IGNORE INTO role_permission (role_id, permission_id) VALUES
 (1, 45),(3, 45),(4, 45),(8, 45),
 (1, 46),(3, 46);
