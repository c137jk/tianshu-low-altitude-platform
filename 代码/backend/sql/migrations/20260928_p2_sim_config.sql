-- ---------------------------------------------------------------------
-- P2：飞行合规 / 仿真参数（2026-09-28）
-- 背景：违规判定阈值与仿真倍速原先散落在代码常量里，改一次要重启；
--       设计07 要求"帧间隔/倍速/阈值可在系统参数页调"，这里补齐配置键。
-- 幂等：INSERT IGNORE（config_key 有唯一索引），重复执行结果一致，不改任何业务数据。
-- ---------------------------------------------------------------------

INSERT IGNORE INTO sys_config (config_group, config_key, config_value, value_type, remark) VALUES
 ('flight','route_deviation_limit_m','100','number','偏航判定阈值（米：当前位置到计划航线的最近距离）'),
 ('flight','altitude_limit_max_m','120','number','全局限高（真高米），超限判「超高」违规'),
 ('flight','overschedule_tolerance_min','10','number','超出计划结束时间的容忍分钟，超过判「超时飞行」违规'),
 ('sim','sim_speed_factor','60','number','仿真倍速：1 为实时，60 表示 1 秒推进 60 秒飞行时间'),
 ('sim','sim_frame_points','600','number','单个任务轨迹落帧上限（防止倍速过高时帧数爆炸）');
