-- ---------------------------------------------------------------------
-- P6：操作日志状态列（2026-09-28）
-- 背景：app/__init__.py 的 after_request 把 HTTP 状态码写进 operation_log.status，
--       而建表语句里这一列是 TINYINT（有符号，最大 127），于是每一条 200/401/403
--       都触发 MySQL 1264 "Out of range value" —— 日志写入异常被 best-effort 吞掉，
--       结果是 M-84 日志审计页永远查不到任何一条操作记录（只有告警日志里有痕迹）。
--       前端 SystemCenter.vue 的列名就是"状态码"，说明设计意图本来就是 HTTP 状态码。
-- 处理：把列宽改为 SMALLINT 并纠正注释口径；不改写入代码，不删任何历史数据。
-- 幂等：MODIFY COLUMN 可重复执行，结果一致。
-- 用法：python scripts/apply_migration.py sql/migrations/20260928_p6_operation_log_status.sql
-- ---------------------------------------------------------------------

ALTER TABLE operation_log
  MODIFY COLUMN status SMALLINT NOT NULL DEFAULT 200 COMMENT 'HTTP 状态码：2xx 成功，4xx/5xx 失败';
