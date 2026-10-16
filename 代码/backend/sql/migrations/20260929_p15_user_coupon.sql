-- =====================================================================
-- 6.3.2 优惠券抵扣配套：领取关系表 user_coupon
--
-- coupon 表 v1 DDL 里已经有了（后台增删改也早通），缺的是"谁领了哪张、抵扣在哪个订单"，
-- 所以下单永远不减钱（差距分析 §7.1）。
--
-- 只存四件事：归属(user_id)、券(coupon_id)、状态、用在哪一单。
-- 不存券面规则（门槛/金额/折扣）的快照：
--   规则改了，已领的券跟着改，这是运营的一次决定，不该在每张券上留一份副本各自漂移；
--   而"这张券给这一单减了多少钱"必须冻结，那个数字落在 orders.discount_amount 上，
--   不在这里——券是规则，订单是结果。
-- 不存"过期"状态：coupon.end_at 一到，未核销的券自然失效，读的时候按 JOIN 判就行，
--   存一份 status=3 的过期态只会造出"表里过期、券被续期后没人管"的两张皮
--   （定时归档留在 B6 的 APScheduler 里做）。
--
-- 唯一键 uk_user_coupon = 每人每张券限领一张；抢光靠 got_count 的条件自增，
--   UPDATE ... WHERE got_count < total_count 走 InnoDB 行锁，不用先查再写。
--
-- 幂等：建表 IF NOT EXISTS。
-- 注意：本文件不能出现 % 字符（apply_migration 走 exec_driver_sql，pyformat 参数化）。
-- 用法：FLASK_SIM_DISABLED=1 python scripts/apply_migration.py \
--       sql/migrations/20260929_p15_user_coupon.sql
-- =====================================================================

CREATE TABLE IF NOT EXISTS user_coupon (
  id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id    BIGINT UNSIGNED NOT NULL COMMENT '领取人 users.id（客户本人，不按商家收窄）',
  coupon_id  BIGINT UNSIGNED NOT NULL,
  order_id   BIGINT UNSIGNED NULL COMMENT '核销所在订单；退券时清回 NULL',
  status     TINYINT NOT NULL DEFAULT 1 COMMENT '1未使用 2已核销',
  got_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '领取时间',
  used_at    DATETIME NULL COMMENT '核销时间（下单成功那一刻）',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted    TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_user_coupon (user_id, coupon_id),
  KEY idx_user_status (user_id, status),
  KEY idx_order (order_id),
  CONSTRAINT fk_uc_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_uc_coupon FOREIGN KEY (coupon_id) REFERENCES coupon (id),
  CONSTRAINT fk_uc_order FOREIGN KEY (order_id) REFERENCES orders (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='优惠券领取与核销（6.3.2 抵扣）';
