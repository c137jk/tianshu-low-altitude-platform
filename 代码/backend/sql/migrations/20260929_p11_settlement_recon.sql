-- =====================================================================
-- 迁移：结算金额口径统一 + 对账/差异链路演示面打通（2026-09-29）
--
-- 背景一（口径冲突，必须先修）：settlement.pay_amount 在同一张表里有两套写法。
--   种子脚本 seed/generate_mock_data.py 记的是"商家扣完平台抽成后的实收"
--   （= 订单实付 - platform_fee，例如订单实付 105.24、抽成 10.52、账单 94.72）；
--   而订单确认收货时 order_service.confirm_receipt 记的是订单实付全额。
--   reconcile 改成"拿支付流水净额核账单"之后，12 笔种子账单会被全部判成
--   "金额不符"——对账功能只是在自己的种子数据上制造假差异。
--   统一后的口径：账单金额 = 向客户收取的钱 = 订单实付 = 应与支付流水净额相等；
--   商家实收是派生值（账单金额 - 平台抽成），由接口用 merchantIncome 字段另给，
--   不再占用 pay_amount。这也和监管报告的营收口径（SUM(orders.pay_amount)）对齐。
--
-- 背景二（演示面是死的）：库里 14 张账单全是 status=2 已结算，而 reconcile 只抓
--   待结算(1)/对账中(3)/有差异(4)。财务账号一进结算页就是"没有待对账的账单"，
--   对账、差异、开票这条链在演示里根本走不起来。把最近 6 张尚未开票的已结算账单
--   退回待结算，还原"订单完成 → 账单待结算 → 财务对账 → 平账已结算 → 申请发票 →
--   开具"的正常在制品状态。已开票（invoice_status=1）的账单不动，避免出现
--   "账单待结算却已经申请过发票"这种自相矛盾的数据。
--
-- 背景三（差异得有样本）：给退回的账单里金额最大的一张补一笔售后部分退款流水。
--   业务上是"订单已完成、客服做了部分退款、但账单没冲减"，核账时流水净额小于账单，
--   判成"有退款"差异——这正是对账要挑出来的东西。只新增 payment_transaction 行，
--   不改订单、不改账单金额。
--
-- 幂等：三条语句都带条件——金额已相等不再命中；退回集合按"状态已是目标值"过滤后
--   重新取集合，第二次执行退的是另外 6 张（可接受，见下面说明）；退款流水用固定
--   txn_no 走 uk_txn_no + INSERT IGNORE 去重。
--   注：第二条每跑一次会再退 6 张，这是"给演示准备在制品"的刻意行为，不是数据错误；
--   重复执行只会让待结算账单变多，不会产生矛盾数据。要收敛就手工把账单再对账一次。
-- 注意：脚本内不能出现百分号字符（执行器按参数化 SQL 处理），取模一律用 MOD()。
-- 用法：FLASK_SIM_DISABLED=1 python scripts/apply_migration.py sql/migrations/20260929_p11_settlement_recon.sql
-- =====================================================================

-- 1) 账单金额口径统一为订单实付（商家实收 = pay_amount - platform_fee，接口派生）
UPDATE settlement s
  JOIN orders o ON o.id = s.order_id
   SET s.pay_amount = o.pay_amount,
       s.updated_at = NOW()
 WHERE s.deleted = 0
   AND ABS(s.pay_amount - o.pay_amount) > 0.005;

-- 2) 最近 6 张未开票的已结算账单退回待结算（给财务留出可对账的在制品）
UPDATE settlement
   SET status = 1,
       reconciled_at = NULL,
       updated_at = NOW()
 WHERE id IN (
       SELECT id FROM (
              SELECT id FROM settlement
               WHERE deleted = 0 AND status = 2 AND invoice_status = 0
               ORDER BY id DESC
               LIMIT 6
       ) keep
 );

-- 3) 差异样本：待结算账单里金额最大的一张，补一笔售后部分退款流水（一成）
INSERT IGNORE INTO payment_transaction
  (txn_no, order_id, user_id, amount, direction, pay_method, channel_txn_no,
   status, paid_at, raw_resp, created_at, updated_at, deleted)
SELECT 'RFDEMO20260929000001', s.order_id, s.customer_id,
       ROUND(s.pay_amount / 10, 2), 2, COALESCE(o.pay_method, 1),
       'MOCKR202609290000000001', 4, NOW(),
       JSON_OBJECT('channel', 'mock', 'refund', 'SUCCESS',
                   'note', '售后部分退款，账单金额未冲减（对账差异样本）'),
       NOW(), NOW(), 0
  FROM settlement s
  JOIN orders o ON o.id = s.order_id
 WHERE s.deleted = 0 AND s.status = 1
 ORDER BY s.pay_amount DESC, s.id ASC
 LIMIT 1;
