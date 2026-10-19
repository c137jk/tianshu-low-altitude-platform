-- =====================================================================
-- 迁移：给「温州商学院示范区」补两架停放飞行器（2026-10-08）
--
-- 背景：演示库里有 5 张待调度订单（寄件点在茶山一带），recommend 算出来的最近起飞场
--   是 VP-WZ-004 温州商学院示范区，而这个场站一架飞机都没停。调度台上点「推荐」只能看到
--   一句"起飞场「温州商学院示范区」没有停放的飞行器"，操作员按提示改选起飞场又会撞上
--   _validate_resources 的"该飞机不在起飞场"——这批单子在人眼里就是死单。
--   2026-10-08 全量点验时发现，用户拍板"补飞行器资源"，不接受演示里留 5 条调度不动的单。
--
-- 做法：在这个场站停两架闲置、在线、满电的机子（载重 50kg/40kg，够现有单子的 1kg）。
--   两架而不是更多：在飞一架时另一架仍可接一单，够演示并发；机长按空闲自动选。
--   merchant_id 取场站归属，公共场站（merchant_id 为空）落到演示主商家 1，
--   和 seed/generate_mock_data.py 里的 MID 同一口径。
--   坐标用场站经纬度做快照，否则大屏/详情页的"最新位置"是空的。
--
-- 幂等：aircraft_no 上有 uk_aircraft_no，走 INSERT IGNORE；重复执行 0 行受影响。
--   按 station_no 定位场站而不是写死 id=4：id 是演示库里建场站时自增出来的，
--   换一套库就飘了。该场站是由「场站管理」按真实地理编码建的，只存在于演示库；
--   在只有 DDL 种子的库里这条语句选不到行，属于预期的空操作。
-- 用法：FLASK_SIM_DISABLED=1 python scripts/apply_migration.py sql/migrations/20261008_p23_park_aircraft_vp004.sql
-- =====================================================================

INSERT IGNORE INTO aircraft (aircraft_no, merchant_id, brand, model, serial_no, endurance_min,
                             max_load_kg, max_speed, max_altitude, purchase_at,
                             current_vertiport_id, status, health_score, battery, online,
                             current_lng, current_lat)
SELECT s.aircraft_no, COALESCE(v.merchant_id, 1), s.brand, s.model, s.serial_no, s.endurance_min,
       s.max_load_kg, s.max_speed, 120, s.purchase_at, v.id, 1, s.health_score, s.battery, 1,
       v.lng, v.lat
  FROM vertiport v
  JOIN (SELECT 'TS-UAV-004' AS aircraft_no, '峰飞' AS brand, 'V2000CG' AS model,
               'SN0004' AS serial_no, 45 AS endurance_min, 50.00 AS max_load_kg,
               20.0 AS max_speed, '2026-06-01' AS purchase_at,
               96 AS health_score, 100 AS battery
        UNION ALL
        SELECT 'TS-UAV-005', '极飞', 'X100', 'SN0005', 38, 40.00, 16.0, '2026-06-01', 93, 95) s
 WHERE v.station_no = 'VP-WZ-004' AND v.deleted = 0;
