-- =====================================================================
-- M-63 设备在线（监控中心）配套：心跳阈值 + 人工"标记失联"落库 + 权限点
--
-- 为什么只建一张"标记"表、不建 device 台账表：
--   平台里的设备就是飞行器（机载终端）和场站（地面充换电设备），台账已经分别在
--   aircraft / vertiport 里，再造一张 device 表只会多出一份没人维护的副本。
--   "在线/离线"是能从数据推出来的（心跳新不新、有没有在飞计划），不该存；
--   唯一存不下的是运维口头确认的那句"这台图传丢了、先按失联处理"，所以只存它。
--
-- 幂等：建表 IF NOT EXISTS，参数与权限 INSERT IGNORE（sys_config.config_key 有唯一键）。
-- 注意：本文件不能出现 % 字符（apply_migration 走 exec_driver_sql，pyformat 参数化）。
-- 用法：FLASK_SIM_DISABLED=1 python scripts/apply_migration.py \
--       sql/migrations/20260929_p13_device_online.sql
-- =====================================================================

-- 1) 心跳判定阈值 ----------------------------------------------------
INSERT INTO sys_config (config_group, config_key, config_value, value_type, remark) VALUES
 ('monitor','device_heartbeat_timeout_sec','60','number','设备心跳超时秒数，超过即判离线/失联')
ON DUPLICATE KEY UPDATE config_group = VALUES(config_group);

-- 2) 人工失联标记 ----------------------------------------------------
CREATE TABLE IF NOT EXISTS device_lost_mark (
  id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  device_type TINYINT NOT NULL COMMENT '1机载终端(aircraft) 2地面设备(vertiport)',
  device_id  BIGINT UNSIGNED NOT NULL COMMENT '对应 aircraft.id / vertiport.id',
  reason     VARCHAR(200) NULL COMMENT '失联原因，如：图传丢失、只有遥测没有位置',
  marked_by  BIGINT UNSIGNED NULL COMMENT '操作人 users.id',
  marked_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_lost_device (device_type, device_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='设备人工失联标记（M-63 标记失联/解除标记）';

-- 3) 权限点 monitor:device（设备在线页 + 标记失联动作） ----------------
-- permission 列序：id,parent_id,perm_code,perm_name,perm_type,path,icon,sort
-- 线上已用到 id=43（order:force_cancel），本点取 44。
INSERT IGNORE INTO permission (id, parent_id, perm_code, perm_name, perm_type, path, icon, sort)
 VALUES (44, 6,'monitor:device','设备在线',3,NULL,NULL,6);

-- 1 平台管理员在 DDL 里是"SELECT id FROM permission"一次性铺满的，新增点必须补授权，
-- 否则 admin 也看不到这个菜单。
INSERT IGNORE INTO role_permission (role_id, permission_id) VALUES (1, 44),(2, 44),(3, 44),(8, 44);
