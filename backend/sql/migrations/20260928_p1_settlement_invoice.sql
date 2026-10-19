-- =====================================================================
-- 迁移：P0 结算发票字段补齐（2026-09-28）
-- 背景：设计06 M-74 要求记录发票抬头/税号/申请时间，但 settlement 表只有
--       invoice_status；旧代码 `b.invoice_title = ...` 写到了未映射的属性上，
--       实际从未落库（静默缺陷）。本迁移补三列并在 ORM 里对齐。
-- 幂等：information_schema 守卫 + PREPARE/EXECUTE。
-- 用法：python scripts/apply_migration.py sql/migrations/20260928_p1_settlement_invoice.sql
-- =====================================================================

SET @c := (SELECT COUNT(*) FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'settlement' AND COLUMN_NAME = 'invoice_title');
SET @s := IF(@c = 0,
  'ALTER TABLE settlement ADD COLUMN invoice_title VARCHAR(120) NULL COMMENT ''发票抬头'' AFTER invoice_status,
     ADD COLUMN invoice_tax_no VARCHAR(40) NULL COMMENT ''纳税人识别号'' AFTER invoice_title,
     ADD COLUMN invoice_applied_at DATETIME NULL COMMENT ''发票申请时间'' AFTER invoice_tax_no',
  'SELECT 1');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;
