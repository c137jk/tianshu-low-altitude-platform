-- =====================================================================
-- 迁移：B6-2 定时任务配套（2026-09-29）
--
-- 1) 四个阈值参数。每个都对应一个"没有阈值就只能写死在代码里"的任务：
--    reserve_remind_lead_min      场站预约开始前多少分钟提醒申请人（原来 start_at 只躺在列表里，
--                                 审核通过那一刻发一条站内信之后，就再没人记得这事儿了）
--    aircraft_qual_warn_days      飞行器资质预警窗口。DDL 7.9 只给了商家/人员两档，
--                                 subject_type=3 一直没有阈值，扫描时只能借用人那一档的天数——
--                                 飞行器适航证和执照的提前量本来就不是一回事
--    track_partition_drop_enabled 超期轨迹分区是否真删：0 只做"预建下月分区"这件无副作用的事
--    coupon_offline_after_days    券过期后再隔多少天才下架（留退货与客服处理的余量）
--
--    drop 开关故意用 number 而不是 boolean：/aux/configs 的保存口只校验 number 是数字，
--    boolean 进去什么都能存，读出来 int("true") 失败只能回退默认——
--    一个决定要不要删真实轨迹数据的开关，不能靠"解析失败恰好落在安全的那一侧"。
--
-- 2) message_notification 补 (biz_type, biz_id) 索引。
--    三个提醒类任务的去重都是一句"这条业务事件通知过没有"，而原表只有 idx_user_read(user_id, is_read)，
--    每轮都得扫全表；这张表是只增不删的流水，跑半年就是几十万行。
--
-- 没有新权限点：/aux/scheduler/jobs 与 /aux/configs 是同一张参数页，
-- 能改阈值的人就该看得到任务下次什么时候跑、有没有被开关停掉。
--
-- 幂等：sys_config 走 INSERT IGNORE（config_key 唯一键），索引走 information_schema 守卫。
-- 注意：本文件不能出现 % 字符（apply_migration 走 exec_driver_sql，pyformat 参数化）。
-- 用法：FLASK_SIM_DISABLED=1 python scripts/apply_migration.py \
--       sql/migrations/20260929_p17_scheduler_config.sql
-- =====================================================================

INSERT IGNORE INTO sys_config (config_group, config_key, config_value, value_type, remark) VALUES
 ('station','reserve_remind_lead_min','30','number','场站预约开始前提醒提前量（分钟）'),
 ('qualification','aircraft_qual_warn_days','15','number','飞行器资质到期预警天数'),
 ('track','track_partition_drop_enabled','0','number','超期轨迹分区是否删除：0 只预建不删，1 按在线保留月数 DROP'),
 ('coupon','coupon_offline_after_days','30','number','优惠券到期后多少天自动下架');

SET @i := (SELECT COUNT(*) FROM information_schema.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'message_notification' AND INDEX_NAME = 'idx_biz');
SET @s := IF(@i = 0,
  'ALTER TABLE message_notification ADD KEY idx_biz (biz_type, biz_id)',
  'SELECT 1');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;
