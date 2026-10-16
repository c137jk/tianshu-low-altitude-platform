-- =====================================================================
-- 迁移：P0 订单时效字段补齐（2026-09-28）
-- 背景：orders 表没有送达/完成时刻，但三处代码都在往 order.delivered_at /
--       order.finished_at 赋值（D3 降落、仿真器到点、Mock 数据生成器）。
--       SQLAlchemy 允许给未映射属性赋值，于是这些写入一直静默丢失——
--       读的时候反而 AttributeError。补列后"下单→送达用时"才真的能统计。
-- 幂等：information_schema 守卫 + PREPARE/EXECUTE，重复执行结果一致。
-- 数据安全：只加两个可空列，不改任何已有行；存量订单的送达时刻保持 NULL。
--           如需回填（可选，会改动历史行的这两列），手动执行文件末尾的派生 UPDATE。
-- =====================================================================

SET @c := (SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'orders' AND COLUMN_NAME = 'delivered_at');
SET @s := IF(@c = 0,
  'ALTER TABLE orders ADD COLUMN delivered_at DATETIME NULL COMMENT ''实际送达时刻（状态转 6 已送达）'' AFTER paid_at,
     ADD COLUMN finished_at DATETIME NULL COMMENT ''客户确认完成时刻（状态转 7 已完成）'' AFTER delivered_at',
  'SELECT 1');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 可选回填（默认不执行）：从已完成飞行计划的实际降落时刻派生送达时刻
-- UPDATE orders o
--   JOIN flight_plan p ON p.order_id = o.id AND p.status = 4 AND p.deleted = 0
--   SET o.delivered_at = p.actual_landing_at
--   WHERE o.deleted = 0 AND o.status IN (6, 7) AND o.delivered_at IS NULL;
