-- =====================================================================
-- p18 · 地面设备心跳时钟（B6-3 收口后补的一处显示正确性缺陷）
--
-- 起因：设备在线页的场站行一直拿 vertiport.updated_at 当"最近心跳"显示，
--   但状态判定 _station_state 只看 status（1 开放就是在线），两个字段说的是
--   两件事。在只有"台账推导"那一版里这没问题——地面设备确实没有遥测链路。
--   B6-3 开了 POST /devices/heartbeat 且接受 terminalType=2 之后，场站可以真的
--   上报心跳了，于是同一列 updated_at 变成两种语义混在一起：
--     · 从没上报过的场站：updated_at 是资料更新时间，7 天前也该显示"在线"（按运营状态）
--     · 报过之后静默的场站：updated_at 是最后一次心跳，超时就该显示"离线"
--   一列分不出这两种，结果就是"报了心跳的场站被标成 · 资料更新"（说假话）
--   和"静默一周的场站永远在线"（粉饰）。所以把心跳时钟单列存，NULL 就老实表示"从未上报"。
--
-- 数据安全：只加一个可空列，不回填、不改任何已有行；存量场站该列全 NULL，
--   行为与加列前完全一致（按运营状态判在线），不会让演示库里的设备一夜之间集体掉线。
-- 幂等：information_schema 守卫 + PREPARE/EXECUTE，重复执行结果一致。
-- 注意：本文件不能出现 % 字符（apply_migration 走 exec_driver_sql，pyformat 参数化）。
-- 用法：FLASK_SIM_DISABLED=1 python scripts/apply_migration.py \
--       sql/migrations/20260930_p18_station_heartbeat.sql
-- =====================================================================

SET @c := (SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'vertiport'
    AND COLUMN_NAME = 'last_heartbeat_at');
SET @s := IF(@c = 0,
  'ALTER TABLE vertiport
     ADD COLUMN last_heartbeat_at DATETIME NULL COMMENT ''最近一次设备心跳，NULL=从未上报（在线状态回落到运营状态）'' AFTER status',
  'SELECT 1');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 自检（手工执行，别写进本脚本）：
--   SELECT COUNT(*) FROM information_schema.COLUMNS
--    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'vertiport'
--      AND COLUMN_NAME = 'last_heartbeat_at';              -- 期望 1
--   SELECT station_no, status, last_heartbeat_at FROM vertiport WHERE deleted = 0;
--                                                          -- 期望 last_heartbeat_at 全 NULL
