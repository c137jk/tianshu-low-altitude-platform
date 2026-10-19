-- =====================================================================
-- 迁移：排班种子补齐（2026-09-29）
-- 背景：staff_schedule 只有 4 条 shift_type=1（早班）的记录，且都落在当天。
--       「排班管理」页面上看上去就像系统只支持早班 —— 其实 shift_type 的 DDL
--       注释写明 1早班 2中班 3晚班 4全天，是种子数据没铺开。
-- 做法：给花名册里的机长/运维岗按 (人员 id + 日偏移) 轮转排未来 7 天班，
--       四种班次都会出现；起止时刻与责任区域按班次固定，晚班 23:00-次日 07:00。
--       人员取自 role_code（pilot / merchant_maintainer / ops_maintainer），不写死用户 id，
--       新库复现时同样成立。
--       只给有商家归属（merchant_id 非空）的人排：staff_schedule.merchant_id 是 NOT NULL，
--       平台侧人员（如平台运维）落库时会被填成 1，种子给他排班等于把他的值班表塞进 1 号商家
--       的排班列表里。写接口目前也是这个行为，本迁移不制造新的越界数据。
-- 幂等：uk_user_date_shift(user_id, shift_date, shift_type) + INSERT IGNORE，
--       同一天重复执行不会翻倍；已存在的早班保持原样。隔天再跑会补上新的一周。
-- 注意：脚本内不能出现 % 字符（执行器按参数化 SQL 处理），取模一律用 MOD()。
-- 用法：FLASK_SIM_DISABLED=1 python scripts/apply_migration.py sql/migrations/20260929_p10_schedule_shifts.sql
-- =====================================================================

INSERT IGNORE INTO staff_schedule
  (user_id, merchant_id, shift_date, shift_type, start_time, end_time,
   responsible_area, status, created_at, updated_at, deleted)
SELECT a.uid,
       a.mid,
       a.dt,
       a.shift_type,
       CONCAT(a.dt, ' ', c.start_hh),
       -- 晚班跨零点：结束时间落在值班日的次日，否则存成"结束早于开始"
       CONCAT(IF(c.cross_day = 1, a.dt + INTERVAL 1 DAY, a.dt), ' ', c.end_hh),
       a.area,
       1, NOW(), NOW(), 0
  FROM (
        SELECT u.uid,
               COALESCE(u.mid, 1) AS mid,
               CAST(CURDATE() + INTERVAL d.n DAY AS CHAR) AS dt,
               1 + MOD(u.uid + d.n, 4) AS shift_type,
               CASE 1 + MOD(u.uid + d.n, 4)
                 WHEN 1 THEN '温州全域'
                 WHEN 2 THEN '瓯江口示范区'
                 WHEN 3 THEN '机场净空区'
                 ELSE '瑞安低空示范区'
               END AS area
          FROM (
                SELECT DISTINCT us.id AS uid, us.merchant_id AS mid
                  FROM users us
                  JOIN user_role ur ON ur.user_id = us.id
                  JOIN role r       ON r.id = ur.role_id
                 WHERE us.deleted = 0
                   AND us.merchant_id IS NOT NULL
                   AND r.role_code IN ('pilot', 'merchant_maintainer', 'ops_maintainer')
               ) u
         CROSS JOIN (
                SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3
                  UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6
               ) d
       ) a
  JOIN (
        SELECT 1 AS shift_type, '07:00:00' AS start_hh, '15:00:00' AS end_hh, 0 AS cross_day
          UNION ALL SELECT 2, '15:00:00', '23:00:00', 0
          UNION ALL SELECT 3, '23:00:00', '07:00:00', 1
          UNION ALL SELECT 4, '08:00:00', '20:00:00', 0
       ) c
    ON c.shift_type = a.shift_type;
