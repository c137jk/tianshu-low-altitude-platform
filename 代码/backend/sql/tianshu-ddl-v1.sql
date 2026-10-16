-- =====================================================================
-- 天枢·低空智能运营管控平台  数据库建表脚本 v1.0
-- 目标库：MySQL 8.0.x   引擎：InnoDB   字符集：utf8mb4
-- 配套文档：设计02 状态机 / 设计03 模块契约 / 设计04 字段明细 / 设计05 E-R图
-- 执行方式：Navicat 打开本文件 → 运行；或命令行 mysql -uroot -p < 天枢-ddl-v1.sql
-- 幂等：可重复执行（先 DROP 再 CREATE；演示数据也会重建）
-- =====================================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ---------------------------------------------------------------------
-- 0. 建库
-- ---------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS tianshu_utms
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_general_ci;
USE tianshu_utms;

-- ---------------------------------------------------------------------
-- 0.1 清理旧表（顺序无所谓，已关闭外键检查；分区表一并删除）
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS coupon, help_article, backup_record, external_api_log, system_log,
  operation_log, sys_config, message_notification, work_order, emergency_event,
  evaluation_complaint, address_book, station_reservation, staff_schedule, maintenance_record,
  payment_transaction, settlement, violation_record, weather_snapshot, geofence, airspace,
  flight_track, approval_record, flight_plan, orders, aircraft, vertiport, qualification,
  customer_profile, role_permission, user_role, permission, role, users, merchants;

-- *********************************************************************
-- 一、用户与组织（RBAC）
-- *********************************************************************

-- 1. 角色表 ----------------------------------------------------------
CREATE TABLE role (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  role_code   VARCHAR(50)  NOT NULL COMMENT '角色编码（英文，程序判断用）',
  role_name   VARCHAR(50)  NOT NULL COMMENT '角色名称（界面显示）',
  role_scope  TINYINT      NOT NULL DEFAULT 1 COMMENT '数据范围 1平台级 2商家级 3客户级',
  built_in    TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否内置不可删 0否1是',
  sort        INT          NOT NULL DEFAULT 0 COMMENT '排序',
  remark      VARCHAR(255) NULL COMMENT '备注',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  deleted     TINYINT      NOT NULL DEFAULT 0 COMMENT '逻辑删除 0否1是',
  PRIMARY KEY (id),
  UNIQUE KEY uk_role_code (role_code)
) ENGINE=InnoDB COMMENT='角色表';

-- 2. 权限点表（自引用成树：菜单/按钮/接口） ---------------------------
CREATE TABLE permission (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  parent_id   BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '父权限id，0=根节点',
  perm_code   VARCHAR(80)  NOT NULL COMMENT '权限编码，如 order:create / airspace:approve',
  perm_name   VARCHAR(80)  NOT NULL COMMENT '权限名称',
  perm_type   TINYINT      NOT NULL DEFAULT 1 COMMENT '1菜单 2按钮 3接口',
  path        VARCHAR(120) NULL COMMENT '前端路由/接口路径',
  icon        VARCHAR(60)  NULL COMMENT '菜单图标',
  sort        INT          NOT NULL DEFAULT 0 COMMENT '排序',
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted     TINYINT      NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_perm_code (perm_code),
  KEY idx_parent (parent_id)
) ENGINE=InnoDB COMMENT='权限点表（菜单-按钮-接口 一棵树）';

-- 3. 商家企业表（users.merchant_id 依赖它，先建） ---------------------
CREATE TABLE merchants (
  id                      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  merchant_no             VARCHAR(32)  NOT NULL COMMENT '商家编号',
  merchant_name           VARCHAR(100) NOT NULL COMMENT '企业名称',
  unified_credit_code     VARCHAR(18)  NULL COMMENT '统一社会信用代码',
  contact_user_id         BIGINT UNSIGNED NULL COMMENT '商家管理员 users.id',
  legal_person            VARCHAR(50)  NULL COMMENT '法人代表',
  contact_phone_cipher    VARCHAR(100) NULL COMMENT '联系电话AES密文',
  service_area_json       JSON         NULL COMMENT '服务覆盖区域',
  pricing_json            JSON         NULL COMMENT '收费标准（重量/距离/空域类型）',
  audit_status            TINYINT      NOT NULL DEFAULT 0 COMMENT '0待提交1审核中2通过3驳回4停用注销',
  audit_opinion           VARCHAR(500) NULL COMMENT '审核意见/驳回原因',
  audited_by              BIGINT UNSIGNED NULL COMMENT '审核人',
  audited_at              DATETIME     NULL COMMENT '审核时间',
  expire_at               DATETIME     NULL COMMENT '资质到期时间（7天预警）',
  status                  TINYINT      NOT NULL DEFAULT 1 COMMENT '1正常2暂停3注销',
  created_at              DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at              DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted                 TINYINT      NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_merchant_no (merchant_no),
  UNIQUE KEY uk_credit_code (unified_credit_code),
  KEY idx_audit_status (audit_status),
  KEY idx_expire (expire_at)
) ENGINE=InnoDB COMMENT='商家企业表';

-- 4. 用户主表（全角色统一登录） ---------------------------------------
CREATE TABLE users (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
  username        VARCHAR(50)  NOT NULL COMMENT '登录账号',
  password_hash   VARCHAR(100) NOT NULL COMMENT 'BCrypt密码哈希（不可逆）',
  user_type       TINYINT      NOT NULL COMMENT '1个人客户2企业客户3商家员工4平台管理员5空管6运维',
  real_name       VARCHAR(50)  NULL COMMENT '真实姓名',
  phone_cipher    VARCHAR(100) NOT NULL COMMENT '手机号AES密文（出参脱敏）',
  phone_digest    CHAR(64)     NOT NULL COMMENT '手机号HMAC-SHA256摘要（查找/唯一约束）',
  email_cipher    VARCHAR(100) NULL COMMENT '邮箱AES密文',
  id_card_cipher  VARCHAR(100) NULL COMMENT '身份证AES密文',
  realname_status TINYINT      NOT NULL DEFAULT 0 COMMENT '0未认证1审核中2已认证3驳回',
  gender          TINYINT      NOT NULL DEFAULT 0 COMMENT '0未知1男2女',
  avatar_url      VARCHAR(255) NULL COMMENT '头像URL',
  merchant_id     BIGINT UNSIGNED NULL COMMENT '所属商家（员工必填，其余为空）',
  status          TINYINT      NOT NULL DEFAULT 1 COMMENT '1正常2冻结3注销',
  frozen_reason   VARCHAR(255) NULL COMMENT '冻结原因',
  last_login_at   DATETIME     NULL COMMENT '最后登录时间',
  last_login_ip   VARCHAR(45)  NULL COMMENT '最后登录IP（兼容IPv6）',
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted         TINYINT      NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_username (username),
  UNIQUE KEY uk_phone_digest (phone_digest),
  KEY idx_merchant (merchant_id),
  KEY idx_type_status (user_type, status),
  CONSTRAINT fk_users_merchant FOREIGN KEY (merchant_id) REFERENCES merchants (id)
) ENGINE=InnoDB COMMENT='用户主表（全角色统一）';

-- 5. 用户-角色（N:N 桥梁） -------------------------------------------
CREATE TABLE user_role (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id     BIGINT UNSIGNED NOT NULL,
  role_id     BIGINT UNSIGNED NOT NULL,
  granted_by  BIGINT UNSIGNED NULL COMMENT '授权人',
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_user_role (user_id, role_id),
  KEY idx_role (role_id),
  CONSTRAINT fk_ur_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_ur_role FOREIGN KEY (role_id) REFERENCES role (id)
) ENGINE=InnoDB COMMENT='用户-角色关联';

-- 6. 角色-权限（N:N 桥梁） -------------------------------------------
CREATE TABLE role_permission (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  role_id       BIGINT UNSIGNED NOT NULL,
  permission_id BIGINT UNSIGNED NOT NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_role_perm (role_id, permission_id),
  KEY idx_perm (permission_id),
  CONSTRAINT fk_rp_role FOREIGN KEY (role_id) REFERENCES role (id),
  CONSTRAINT fk_rp_perm FOREIGN KEY (permission_id) REFERENCES permission (id)
) ENGINE=InnoDB COMMENT='角色-权限关联';

-- 7. 客户扩展信息（1:1 users） ----------------------------------------
CREATE TABLE customer_profile (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id         BIGINT UNSIGNED NOT NULL,
  customer_type   TINYINT NOT NULL DEFAULT 1 COMMENT '1个人2企业',
  enterprise_name VARCHAR(100) NULL COMMENT '企业名称（企业客户）',
  credit_code     VARCHAR(18)  NULL COMMENT '企业信用代码',
  verified_at     DATETIME NULL COMMENT '认证时间',
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted         TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_user (user_id),
  CONSTRAINT fk_cp_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB COMMENT='客户扩展信息';

-- 8. 资质文件（多态：商家/人员/飞行器） -------------------------------
CREATE TABLE qualification (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  subject_type TINYINT NOT NULL COMMENT '归属主体 1商家2人员3飞行器',
  subject_id   BIGINT UNSIGNED NOT NULL COMMENT '主体id（多态，不建物理外键）',
  qual_type    VARCHAR(30) NOT NULL COMMENT '资质类型：license营业执照/permit运营许可/airworthiness适航证/driver驾驶证/training培训证',
  qual_name    VARCHAR(100) NOT NULL COMMENT '资质名称',
  file_url     VARCHAR(255) NULL COMMENT '文件URL',
  issue_at     DATETIME NULL COMMENT '签发日期',
  expire_at    DATETIME NULL COMMENT '到期日期（预警扫描）',
  status       TINYINT NOT NULL DEFAULT 0 COMMENT '0待审核1有效2即将到期3已过期4驳回',
  audited_by   BIGINT UNSIGNED NULL,
  audited_at   DATETIME NULL,
  audit_opinion VARCHAR(500) NULL,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted      TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY idx_subject (subject_type, subject_id),
  KEY idx_expire (expire_at)
) ENGINE=InnoDB COMMENT='资质文件表（多态）';

-- *********************************************************************
-- 二、低空资源
-- *********************************************************************

-- 9. 起降/换电场站 ----------------------------------------------------
CREATE TABLE vertiport (
  id                     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  station_no             VARCHAR(32) NOT NULL COMMENT '场站编号',
  station_name           VARCHAR(80) NOT NULL COMMENT '场站名称',
  station_type           TINYINT NOT NULL COMMENT '1起降场2换电站3综合站',
  lng                    DECIMAL(10,6) NOT NULL COMMENT '经度GCJ-02',
  lat                    DECIMAL(9,6)  NOT NULL COMMENT '纬度GCJ-02',
  address                VARCHAR(255) NULL COMMENT '文本地址',
  merchant_id            BIGINT UNSIGNED NULL COMMENT '归属商家，空=公共场站',
  capacity               INT NOT NULL DEFAULT 0 COMMENT '同时停靠容量',
  charger_total          INT NOT NULL DEFAULT 0 COMMENT '充电桩总数',
  charger_available      INT NOT NULL DEFAULT 0 COMMENT '可用充电桩（快照）',
  occupied_count         INT NOT NULL DEFAULT 0 COMMENT '当前占用数',
  service_aircraft_types VARCHAR(200) NULL COMMENT '可服务机型',
  open_time              TIME NULL COMMENT '开放时间',
  close_time             TIME NULL COMMENT '关闭时间',
  status                 TINYINT NOT NULL DEFAULT 1 COMMENT '1开放2维护中3关闭',
  last_heartbeat_at      DATETIME NULL COMMENT '最近一次设备心跳，NULL=从未上报（在线状态回落到运营状态）',
  created_at             DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at             DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted                TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_station_no (station_no),
  KEY idx_merchant (merchant_id),
  KEY idx_status (status),
  CONSTRAINT fk_vp_merchant FOREIGN KEY (merchant_id) REFERENCES merchants (id)
) ENGINE=InnoDB COMMENT='起降/换电场站';

-- 10. 飞行器台账 ------------------------------------------------------
CREATE TABLE aircraft (
  id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  aircraft_no           VARCHAR(32) NOT NULL COMMENT '飞行器编号',
  merchant_id           BIGINT UNSIGNED NOT NULL COMMENT '所属商家',
  brand                 VARCHAR(60) NULL COMMENT '品牌',
  model                 VARCHAR(60) NULL COMMENT '型号',
  serial_no             VARCHAR(64) NULL COMMENT '出厂序列号',
  endurance_min         INT NULL COMMENT '续航（分钟）',
  max_load_kg           DECIMAL(8,2) NULL COMMENT '最大载重kg',
  max_speed             DECIMAL(5,1) NULL COMMENT '最大速度m/s',
  max_altitude          INT NULL COMMENT '最大飞行高度m',
  purchase_at           DATE NULL COMMENT '购置日期',
  current_vertiport_id  BIGINT UNSIGNED NULL COMMENT '当前所在场站',
  status                TINYINT NOT NULL DEFAULT 1 COMMENT '1闲置2执行中3充换电4维保中5故障离线',
  health_score          TINYINT NOT NULL DEFAULT 100 COMMENT '健康度0-100（≥80绿/60-79黄/<60红）',
  total_flight_min      INT NOT NULL DEFAULT 0 COMMENT '累计飞行分钟',
  task_count            INT NOT NULL DEFAULT 0 COMMENT '累计任务数',
  fault_count           INT NOT NULL DEFAULT 0 COMMENT '累计故障数',
  last_maintain_at      DATETIME NULL COMMENT '上次维保',
  next_maintain_at      DATETIME NULL COMMENT '下次维保（提醒）',
  current_lng           DECIMAL(10,6) NULL COMMENT '最新经度快照',
  current_lat           DECIMAL(9,6)  NULL COMMENT '最新纬度快照',
  battery               TINYINT NULL COMMENT '当前电量%',
  online                TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否在线（Redis心跳为准）',
  created_at            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted               TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_aircraft_no (aircraft_no),
  KEY idx_merchant_status (merchant_id, status),
  KEY idx_vertiport (current_vertiport_id),
  KEY idx_next_maintain (next_maintain_at),
  CONSTRAINT fk_ac_merchant FOREIGN KEY (merchant_id) REFERENCES merchants (id),
  CONSTRAINT fk_ac_vertiport FOREIGN KEY (current_vertiport_id) REFERENCES vertiport (id)
) ENGINE=InnoDB COMMENT='飞行器台账';

-- 11. 维保/故障记录（P1） ---------------------------------------------
CREATE TABLE maintenance_record (
  id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  aircraft_id      BIGINT UNSIGNED NOT NULL,
  record_type      TINYINT NOT NULL COMMENT '1定期维保2故障维修3巡检',
  fault_type       VARCHAR(60) NULL COMMENT '故障类型',
  description      VARCHAR(500) NULL COMMENT '问题描述',
  photo_urls       JSON NULL COMMENT '照片URL数组',
  handler_id       BIGINT UNSIGNED NULL COMMENT '处理人 users.id',
  started_at       DATETIME NULL,
  finished_at      DATETIME NULL,
  result           VARCHAR(255) NULL COMMENT '处理结果',
  status           TINYINT NOT NULL DEFAULT 1 COMMENT '1待处理2处理中3已完成',
  flight_minutes_at INT NULL COMMENT '发生时累计飞行分钟',
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted          TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY idx_aircraft (aircraft_id, id),
  KEY idx_status (status),
  CONSTRAINT fk_mr_aircraft FOREIGN KEY (aircraft_id) REFERENCES aircraft (id),
  CONSTRAINT fk_mr_handler FOREIGN KEY (handler_id) REFERENCES users (id)
) ENGINE=InnoDB COMMENT='飞行器维保与故障记录';

-- 12. 人员排班（P1） --------------------------------------------------
CREATE TABLE staff_schedule (
  id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id            BIGINT UNSIGNED NOT NULL COMMENT '机长/运维',
  merchant_id        BIGINT UNSIGNED NOT NULL,
  shift_date         DATE NOT NULL COMMENT '值班日期',
  shift_type         TINYINT NOT NULL COMMENT '1早班2中班3晚班4全天',
  start_time         DATETIME NOT NULL,
  end_time           DATETIME NOT NULL,
  responsible_area   VARCHAR(100) NULL COMMENT '负责区域',
  bound_aircraft_id  BIGINT UNSIGNED NULL COMMENT '绑定飞行器',
  status             TINYINT NOT NULL DEFAULT 1 COMMENT '1计划2已出勤3请假4缺勤',
  created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted            TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_user_date_shift (user_id, shift_date, shift_type),
  KEY idx_merchant_date (merchant_id, shift_date),
  CONSTRAINT fk_ss_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_ss_merchant FOREIGN KEY (merchant_id) REFERENCES merchants (id),
  CONSTRAINT fk_ss_aircraft FOREIGN KEY (bound_aircraft_id) REFERENCES aircraft (id)
) ENGINE=InnoDB COMMENT='人员排班';

-- 13. 场站预约（P1） --------------------------------------------------
CREATE TABLE station_reservation (
  id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  reservation_no VARCHAR(32) NOT NULL,
  station_id     BIGINT UNSIGNED NOT NULL,
  merchant_id    BIGINT UNSIGNED NOT NULL,
  user_id        BIGINT UNSIGNED NOT NULL,
  aircraft_id    BIGINT UNSIGNED NULL,
  purpose        TINYINT NOT NULL COMMENT '1起降2换电3充电',
  start_at       DATETIME NOT NULL,
  end_at         DATETIME NOT NULL,
  status         TINYINT NOT NULL DEFAULT 1 COMMENT '1待审2通过3驳回4完成5取消',
  audit_opinion  VARCHAR(255) NULL,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted        TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_reservation_no (reservation_no),
  KEY idx_station_time (station_id, start_at, end_at),
  CONSTRAINT fk_sr_station FOREIGN KEY (station_id) REFERENCES vertiport (id),
  CONSTRAINT fk_sr_merchant FOREIGN KEY (merchant_id) REFERENCES merchants (id),
  CONSTRAINT fk_sr_user FOREIGN KEY (user_id) REFERENCES users (id),
  CONSTRAINT fk_sr_aircraft FOREIGN KEY (aircraft_id) REFERENCES aircraft (id)
) ENGINE=InnoDB COMMENT='场站预约';

-- *********************************************************************
-- 三、业务流转：订单 / 飞行计划 / 审批 / 轨迹
-- *********************************************************************

-- 14. 订单表 ----------------------------------------------------------
CREATE TABLE orders (
  id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  order_no         VARCHAR(32) NOT NULL COMMENT '业务单号 TS+日期+序列',
  customer_id      BIGINT UNSIGNED NOT NULL COMMENT '下单客户',
  merchant_id      BIGINT UNSIGNED NULL COMMENT '承接商家（接单前可空）',
  order_type       TINYINT NOT NULL DEFAULT 1 COMMENT '1普通配送2应急配送3预留',
  status           TINYINT NOT NULL DEFAULT 0 COMMENT '0待支付1待接单2待调度3待审批4待起飞5飞行中6已送达7已完成8已取消',
  -- 寄件（下单快照）
  sender_name      VARCHAR(50) NOT NULL,
  sender_phone     VARCHAR(100) NOT NULL COMMENT 'AES密文',
  sender_address   VARCHAR(255) NOT NULL,
  sender_lng       DECIMAL(10,6) NOT NULL,
  sender_lat       DECIMAL(9,6)  NOT NULL,
  -- 收件（下单快照）
  receiver_name    VARCHAR(50) NOT NULL,
  receiver_phone   VARCHAR(100) NOT NULL COMMENT 'AES密文',
  receiver_address VARCHAR(255) NOT NULL,
  receiver_lng     DECIMAL(10,6) NOT NULL,
  receiver_lat     DECIMAL(9,6)  NOT NULL,
  -- 物品时效
  goods_type       VARCHAR(40) NOT NULL COMMENT '物品类型（禁运校验）',
  goods_name       VARCHAR(100) NULL,
  weight_kg        DECIMAL(8,2) NOT NULL COMMENT '重量kg',
  volume_m3        DECIMAL(8,3) NULL COMMENT '体积m³',
  quantity         INT NOT NULL DEFAULT 1,
  goods_cert_url   VARCHAR(255) NULL COMMENT '物品合规证明',
  delivery_type    TINYINT NOT NULL DEFAULT 1 COMMENT '1普通2加急',
  expect_at        DATETIME NULL COMMENT '预约时间，空=即时单',
  -- 费用快照（冻结）
  base_fee         DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  airspace_fee     DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  urgent_fee       DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  discount_amount  DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  total_amount     DECIMAL(10,2) NOT NULL COMMENT '应收合计',
  pay_amount       DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '实付',
  pay_method       TINYINT NULL COMMENT '1微信2支付宝3对公(Mock)',
  paid             TINYINT(1) NOT NULL DEFAULT 0,
  paid_at          DATETIME NULL,
  cancel_reason    VARCHAR(255) NULL,
  remark           VARCHAR(255) NULL,
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted          TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_order_no (order_no),
  KEY idx_customer (customer_id, status, created_at),
  KEY idx_merchant (merchant_id, status, created_at),
  KEY idx_status_created (status, created_at),
  KEY idx_expect_at (expect_at),
  CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES users (id),
  CONSTRAINT fk_orders_merchant FOREIGN KEY (merchant_id) REFERENCES merchants (id),
  CONSTRAINT chk_amount CHECK (total_amount >= 0 AND weight_kg > 0)
) ENGINE=InnoDB COMMENT='订单表';

-- 15. 飞行计划（含调度方案，与订单1:1） --------------------------------
CREATE TABLE flight_plan (
  id                     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  plan_no                VARCHAR(32) NOT NULL,
  order_id               BIGINT UNSIGNED NOT NULL COMMENT '关联订单（唯一→1:1）',
  merchant_id            BIGINT UNSIGNED NOT NULL COMMENT '冗余归属商家',
  status                 TINYINT NOT NULL DEFAULT 0 COMMENT '0待申报1待审批2已批准待执行3执行中4已完成5已驳回6已取消',
  -- 调度方案
  aircraft_id            BIGINT UNSIGNED NULL,
  pilot_id               BIGINT UNSIGNED NULL COMMENT '机长 users.id',
  takeoff_vertiport_id   BIGINT UNSIGNED NULL,
  landing_vertiport_id   BIGINT UNSIGNED NULL,
  route_json             JSON NULL COMMENT '有序航点[{lng,lat}]GCJ-02',
  route_distance_m       INT NULL,
  est_duration_sec       INT NULL,
  dispatch_by            BIGINT UNSIGNED NULL,
  dispatched_at          DATETIME NULL,
  -- 飞行要素
  task_name              VARCHAR(100) NULL,
  purpose                VARCHAR(255) NULL,
  plan_start_at          DATETIME NULL,
  plan_end_at            DATETIME NULL,
  altitude_m             INT NULL COMMENT '申报高度',
  cruise_speed           DECIMAL(5,1) NULL COMMENT '巡航速度m/s',
  weather_json           JSON NULL COMMENT '申报时气象快照',
  -- 审批当前结果
  submitted_at           DATETIME NULL,
  approve_result         TINYINT NULL COMMENT '1通过2驳回',
  approved_by            BIGINT UNSIGNED NULL,
  approved_at            DATETIME NULL,
  approve_opinion        VARCHAR(500) NULL,
  reject_reason          VARCHAR(500) NULL,
  -- 执行实况（热快照）
  assigned_at            DATETIME NULL COMMENT '下发机长时间',
  actual_takeoff_at      DATETIME NULL,
  actual_landing_at      DATETIME NULL,
  current_lng            DECIMAL(10,6) NULL,
  current_lat            DECIMAL(9,6)  NULL,
  current_altitude       DECIMAL(7,2) NULL,
  current_speed          DECIMAL(5,2) NULL,
  current_battery        TINYINT NULL,
  last_report_at         DATETIME NULL COMMENT '最后遥测时间',
  remark                 VARCHAR(255) NULL,
  created_at             DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at             DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted                TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_plan_no (plan_no),
  UNIQUE KEY uk_order (order_id),
  KEY idx_merchant_status (merchant_id, status),
  KEY idx_pilot_status (pilot_id, status),
  KEY idx_aircraft (aircraft_id),
  KEY idx_status_start (status, plan_start_at),
  KEY idx_last_report (last_report_at),
  CONSTRAINT fk_fp_order FOREIGN KEY (order_id) REFERENCES orders (id),
  CONSTRAINT fk_fp_merchant FOREIGN KEY (merchant_id) REFERENCES merchants (id),
  CONSTRAINT fk_fp_aircraft FOREIGN KEY (aircraft_id) REFERENCES aircraft (id),
  CONSTRAINT fk_fp_pilot FOREIGN KEY (pilot_id) REFERENCES users (id),
  CONSTRAINT fk_fp_takeoff_vp FOREIGN KEY (takeoff_vertiport_id) REFERENCES vertiport (id),
  CONSTRAINT fk_fp_landing_vp FOREIGN KEY (landing_vertiport_id) REFERENCES vertiport (id)
) ENGINE=InnoDB COMMENT='飞行计划（调度+申报+审批+执行 一行走完一生）';

-- 16. 审批流水（多轮留痕） --------------------------------------------
CREATE TABLE approval_record (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  plan_id     BIGINT UNSIGNED NOT NULL,
  approver_id BIGINT UNSIGNED NOT NULL,
  result      TINYINT NOT NULL COMMENT '1通过2驳回',
  opinion     VARCHAR(500) NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_plan (plan_id, id),
  CONSTRAINT fk_ar_plan FOREIGN KEY (plan_id) REFERENCES flight_plan (id),
  CONSTRAINT fk_ar_approver FOREIGN KEY (approver_id) REFERENCES users (id)
) ENGINE=InnoDB COMMENT='审批流水（每轮一条）';

-- 17. 飞行轨迹（海量窄表 · 按月RANGE分区 · 不建物理外键） --------------
-- 说明：MySQL 分区表不支持外键；高并发写入也应避免外键检查。
--      关联一致性由应用层保证，冗余 plan_id/aircraft_id/order_id 免连表。
--      每月定时任务预建下月分区；近2个月在线，更早整分区 DROP 归档。
CREATE TABLE flight_track (
  id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '帧自增id',
  plan_id        BIGINT UNSIGNED NOT NULL,
  aircraft_id    BIGINT UNSIGNED NOT NULL,
  order_id       BIGINT UNSIGNED NOT NULL,
  lng            DECIMAL(10,6) NOT NULL,
  lat            DECIMAL(9,6)  NOT NULL,
  altitude       DECIMAL(7,2) NOT NULL DEFAULT 0 COMMENT '高度m',
  speed          DECIMAL(5,2) NOT NULL DEFAULT 0 COMMENT '地速m/s',
  direction      SMALLINT NOT NULL DEFAULT 0 COMMENT '航向0-359',
  battery        TINYINT UNSIGNED NOT NULL DEFAULT 100,
  track_time     DATETIME(3) NOT NULL COMMENT '毫秒帧时间（分区键）',
  is_violation   TINYINT(1) NOT NULL DEFAULT 0 COMMENT '该点是否违规',
  violation_type VARCHAR(40) NULL COMMENT '违规类型打标',
  source         TINYINT NOT NULL DEFAULT 1 COMMENT '1仿真器2真机GCS',
  PRIMARY KEY (id, track_time),
  KEY idx_plan_time (plan_id, track_time),
  KEY idx_aircraft_time (aircraft_id, track_time)
) ENGINE=InnoDB COMMENT='飞行轨迹（按月分区）'
PARTITION BY RANGE (TO_DAYS(track_time)) (
  PARTITION p202609 VALUES LESS THAN (TO_DAYS('2026-10-01')),
  PARTITION p202610 VALUES LESS THAN (TO_DAYS('2026-11-01')),
  PARTITION p202611 VALUES LESS THAN (TO_DAYS('2026-12-01')),
  PARTITION p202612 VALUES LESS THAN (TO_DAYS('2027-01-01')),
  PARTITION p202701 VALUES LESS THAN (TO_DAYS('2027-02-01')),
  PARTITION p202702 VALUES LESS THAN (TO_DAYS('2027-03-01')),
  PARTITION pmax    VALUES LESS THAN MAXVALUE
);
-- 【每月预建分区模板】（示例：2027年3月到来前执行，由 APScheduler 定时任务生成）
-- ALTER TABLE flight_track REORGANIZE PARTITION pmax INTO (
--   PARTITION p202703 VALUES LESS THAN (TO_DAYS('2027-04-01')),
--   PARTITION pmax VALUES LESS THAN MAXVALUE);
-- 【归档（秒级删整月）】ALTER TABLE flight_track DROP PARTITION p202609;

-- *********************************************************************
-- 四、空域与监控
-- *********************************************************************

-- 18. 空域区 ----------------------------------------------------------
CREATE TABLE airspace (
  id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  airspace_no      VARCHAR(32) NOT NULL,
  airspace_name    VARCHAR(80) NOT NULL,
  airspace_type    TINYINT NOT NULL COMMENT '1禁飞(红)2限飞(黄)3可飞(绿)4临时管制',
  source           TINYINT NOT NULL DEFAULT 1 COMMENT '1空管同步(Mock)2平台自绘',
  geometry_json    JSON NOT NULL COMMENT 'GeoJSON多边形/圆 GCJ-02',
  altitude_min     INT NULL COMMENT '高度下限m',
  altitude_max     INT NULL COMMENT '高度上限m',
  control_start_at DATETIME NULL COMMENT '管制开始（临时管制）',
  control_end_at   DATETIME NULL COMMENT '管制结束',
  control_reason   VARCHAR(255) NULL,
  region_name      VARCHAR(80) NULL COMMENT '所属区域',
  status           TINYINT NOT NULL DEFAULT 1 COMMENT '1生效2失效',
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted          TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_airspace_no (airspace_no),
  KEY idx_type_status (airspace_type, status),
  KEY idx_control_time (control_start_at, control_end_at)
) ENGINE=InnoDB COMMENT='空域区（参考数据，应用层点-多边形校验）';

-- 19. 电子围栏 --------------------------------------------------------
CREATE TABLE geofence (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  fence_name      VARCHAR(80) NOT NULL,
  merchant_id     BIGINT UNSIGNED NULL COMMENT '设置方，空=平台',
  geometry_json   JSON NOT NULL,
  fence_type      TINYINT NOT NULL COMMENT '1限入2限出3接近告警',
  altitude_limit  INT NULL,
  warn_distance_m INT NULL COMMENT '触发告警距离m',
  status          TINYINT NOT NULL DEFAULT 1 COMMENT '1启用2停用',
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted         TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY idx_merchant (merchant_id),
  CONSTRAINT fk_gf_merchant FOREIGN KEY (merchant_id) REFERENCES merchants (id)
) ENGINE=InnoDB COMMENT='电子围栏（运营自定义）';

-- 20. 气象快照（Mock，P1） --------------------------------------------
CREATE TABLE weather_snapshot (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  region_name   VARCHAR(80) NULL,
  lng           DECIMAL(10,6) NULL,
  lat           DECIMAL(9,6)  NULL,
  wind_speed    DECIMAL(4,1) NULL COMMENT '风速m/s',
  wind_direction SMALLINT NULL COMMENT '风向°',
  visibility_m  INT NULL COMMENT '能见度m',
  temperature   DECIMAL(4,1) NULL,
  weather_desc  VARCHAR(40) NULL,
  warning_level TINYINT NOT NULL DEFAULT 0 COMMENT '0无1蓝2黄3橙4红',
  fetched_at    DATETIME NOT NULL COMMENT '拉取/生成时间',
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_region_time (region_name, fetched_at)
) ENGINE=InnoDB COMMENT='气象快照（Mock 10分钟刷新）';

-- 21. 违规记录与处罚 --------------------------------------------------
CREATE TABLE violation_record (
  id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  violation_no   VARCHAR(32) NOT NULL,
  merchant_id    BIGINT UNSIGNED NOT NULL,
  order_id       BIGINT UNSIGNED NULL,
  plan_id        BIGINT UNSIGNED NULL,
  aircraft_id    BIGINT UNSIGNED NULL,
  pilot_id       BIGINT UNSIGNED NULL,
  violation_type TINYINT NOT NULL COMMENT '1闯禁飞区2偏航3超时4超高5无证6其他',
  lng            DECIMAL(10,6) NULL,
  lat            DECIMAL(9,6)  NULL,
  track_id       BIGINT UNSIGNED NULL COMMENT '对应轨迹帧（分区表不建FK）',
  evidence_urls  JSON NULL COMMENT '证据截图URL数组',
  violation_time DATETIME NOT NULL,
  description    VARCHAR(500) NULL,
  penalty_type   TINYINT NULL COMMENT '0未处罚1警告2罚款3暂停服务4永久封禁',
  penalty_amount DECIMAL(10,2) NULL,
  penalty_days   INT NULL COMMENT '暂停天数',
  penalty_reason VARCHAR(500) NULL,
  penalty_by     BIGINT UNSIGNED NULL,
  penalty_at     DATETIME NULL,
  notice_url     VARCHAR(255) NULL COMMENT '处理通知书',
  status         TINYINT NOT NULL DEFAULT 1 COMMENT '1未处理2处理中3已归档',
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted        TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_violation_no (violation_no),
  KEY idx_merchant_status (merchant_id, status),
  KEY idx_type (violation_type),
  KEY idx_time (violation_time),
  CONSTRAINT fk_vr_merchant FOREIGN KEY (merchant_id) REFERENCES merchants (id),
  CONSTRAINT fk_vr_pilot FOREIGN KEY (pilot_id) REFERENCES users (id)
) ENGINE=InnoDB COMMENT='违规记录与处罚';

-- *********************************************************************
-- 五、交易闭环
-- *********************************************************************

-- 22. 结算单（与订单1:1） ---------------------------------------------
CREATE TABLE settlement (
  id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  settlement_no  VARCHAR(32) NOT NULL,
  order_id       BIGINT UNSIGNED NOT NULL,
  merchant_id    BIGINT UNSIGNED NOT NULL,
  customer_id    BIGINT UNSIGNED NOT NULL,
  base_fee       DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  airspace_fee   DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  urgent_fee     DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  platform_fee   DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '平台服务费/抽佣',
  pay_amount     DECIMAL(10,2) NOT NULL DEFAULT 0.00,
  status         TINYINT NOT NULL DEFAULT 1 COMMENT '1待结算2已结算3对账中4有差异',
  reconciled_at  DATETIME NULL,
  invoice_status TINYINT NOT NULL DEFAULT 0 COMMENT '0未申请1已申请2已开具',
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted        TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_settlement_no (settlement_no),
  UNIQUE KEY uk_order (order_id),
  KEY idx_merchant_status (merchant_id, status),
  CONSTRAINT fk_st_order FOREIGN KEY (order_id) REFERENCES orders (id),
  CONSTRAINT fk_st_merchant FOREIGN KEY (merchant_id) REFERENCES merchants (id),
  CONSTRAINT fk_st_customer FOREIGN KEY (customer_id) REFERENCES users (id)
) ENGINE=InnoDB COMMENT='结算单';

-- 23. 支付流水（Mock） ------------------------------------------------
CREATE TABLE payment_transaction (
  id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  txn_no         VARCHAR(32) NOT NULL COMMENT '平台流水号',
  order_id       BIGINT UNSIGNED NOT NULL,
  user_id        BIGINT UNSIGNED NOT NULL,
  amount         DECIMAL(10,2) NOT NULL,
  direction      TINYINT NOT NULL COMMENT '1支付2退款',
  pay_method     TINYINT NOT NULL COMMENT '1微信2支付宝3对公',
  channel_txn_no VARCHAR(64) NULL COMMENT '三方流水(Mock生成)',
  status         TINYINT NOT NULL DEFAULT 1 COMMENT '1待支付2成功3失败4已退款',
  paid_at        DATETIME NULL,
  raw_resp       JSON NULL COMMENT '渠道回调留痕(Mock)',
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted        TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_txn_no (txn_no),
  KEY idx_order (order_id),
  KEY idx_status_time (status, created_at),
  CONSTRAINT fk_pt_order FOREIGN KEY (order_id) REFERENCES orders (id),
  CONSTRAINT fk_pt_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB COMMENT='支付流水';

-- *********************************************************************
-- 六、P1/P2 预留表（建结构，功能后补）
-- *********************************************************************

-- 24. 常用地址簿 ------------------------------------------------------
CREATE TABLE address_book (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id      BIGINT UNSIGNED NOT NULL,
  contact_name VARCHAR(50) NOT NULL,
  contact_phone VARCHAR(100) NOT NULL COMMENT 'AES密文',
  address      VARCHAR(255) NOT NULL,
  lng          DECIMAL(10,6) NOT NULL,
  lat          DECIMAL(9,6)  NOT NULL,
  label        VARCHAR(20) NULL COMMENT '家/公司/其他',
  is_default   TINYINT(1) NOT NULL DEFAULT 0,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted      TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY idx_user (user_id),
  CONSTRAINT fk_ab_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB COMMENT='常用地址簿(P1)';

-- 25. 评价与投诉 ------------------------------------------------------
CREATE TABLE evaluation_complaint (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  order_id    BIGINT UNSIGNED NOT NULL,
  user_id     BIGINT UNSIGNED NOT NULL,
  merchant_id BIGINT UNSIGNED NULL,
  type        TINYINT NOT NULL COMMENT '1评价2投诉',
  score       TINYINT NULL COMMENT '评分1-5',
  content     VARCHAR(1000) NULL,
  reply       VARCHAR(1000) NULL,
  reply_by    BIGINT UNSIGNED NULL,
  reply_at    DATETIME NULL,
  status      TINYINT NOT NULL DEFAULT 1 COMMENT '1待处理2已回复3已关闭',
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted     TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY idx_order (order_id),
  KEY idx_merchant_status (merchant_id, status),
  CONSTRAINT fk_ec_order FOREIGN KEY (order_id) REFERENCES orders (id),
  CONSTRAINT fk_ec_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB COMMENT='评价与投诉(P1)';

-- 26. 应急事件 --------------------------------------------------------
CREATE TABLE emergency_event (
  id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  event_no       VARCHAR(32) NOT NULL,
  order_id       BIGINT UNSIGNED NULL,
  plan_id        BIGINT UNSIGNED NULL,
  aircraft_id    BIGINT UNSIGNED NULL,
  event_type     TINYINT NOT NULL COMMENT '1失联2闯禁飞3低电量4恶劣天气5其他',
  level          TINYINT NOT NULL DEFAULT 1 COMMENT '1一般2较大3重大',
  lng            DECIMAL(10,6) NULL,
  lat            DECIMAL(9,6)  NULL,
  description    VARCHAR(500) NULL,
  handle_measure VARCHAR(500) NULL,
  handler_id     BIGINT UNSIGNED NULL,
  started_at     DATETIME NULL,
  closed_at      DATETIME NULL,
  status         TINYINT NOT NULL DEFAULT 1 COMMENT '1发生中2处置中3已解除',
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted        TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_event_no (event_no),
  KEY idx_status (status)
) ENGINE=InnoDB COMMENT='应急事件(P1)';

-- 27. 客服工单 --------------------------------------------------------
CREATE TABLE work_order (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  ticket_no   VARCHAR(32) NOT NULL,
  source      TINYINT NOT NULL COMMENT '1客服2投诉3应急4系统',
  biz_type    VARCHAR(30) NULL COMMENT '关联业务类型',
  biz_id      BIGINT UNSIGNED NULL,
  title       VARCHAR(120) NOT NULL,
  content     VARCHAR(1000) NULL,
  handler_id  BIGINT UNSIGNED NULL,
  priority    TINYINT NOT NULL DEFAULT 2 COMMENT '1低2中3高4紧急',
  status      TINYINT NOT NULL DEFAULT 1 COMMENT '1待处理2处理中3待反馈4已关闭',
  closed_at   DATETIME NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted     TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ticket_no (ticket_no),
  KEY idx_handler_status (handler_id, status)
) ENGINE=InnoDB COMMENT='客服工单(P1)';

-- 28. 消息通知 --------------------------------------------------------
CREATE TABLE message_notification (
  id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id    BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '接收人，0=全员广播',
  title      VARCHAR(120) NOT NULL,
  content    VARCHAR(1000) NULL,
  biz_type   VARCHAR(30) NULL COMMENT '关联业务',
  biz_id     BIGINT UNSIGNED NULL,
  channel    TINYINT NOT NULL DEFAULT 1 COMMENT '1站内2短信(Mock)',
  is_read    TINYINT(1) NOT NULL DEFAULT 0,
  read_at    DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_user_read (user_id, is_read)
) ENGINE=InnoDB COMMENT='消息通知(P1)';

-- 29. 系统参数 --------------------------------------------------------
CREATE TABLE sys_config (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  config_group VARCHAR(50) NOT NULL DEFAULT 'common',
  config_key   VARCHAR(80) NOT NULL,
  config_value VARCHAR(500) NULL,
  value_type   VARCHAR(20) NOT NULL DEFAULT 'string' COMMENT 'string/number/boolean/json',
  remark       VARCHAR(255) NULL,
  is_system    TINYINT(1) NOT NULL DEFAULT 1 COMMENT '系统内置不可删',
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted      TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_config_key (config_key)
) ENGINE=InnoDB COMMENT='系统参数';

-- 30. 操作日志（切面写入，留存≥6个月，无逻辑删除/更新） ----------------
CREATE TABLE operation_log (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id       BIGINT UNSIGNED NULL,
  username_snap VARCHAR(50) NULL COMMENT '操作时账号快照',
  module        VARCHAR(50) NULL,
  action        VARCHAR(80) NULL,
  method        VARCHAR(10) NULL COMMENT 'HTTP方法',
  request_url   VARCHAR(255) NULL,
  request_param TEXT NULL,
  ip            VARCHAR(45) NULL,
  user_agent    VARCHAR(255) NULL,
  status        SMALLINT NOT NULL DEFAULT 200 COMMENT 'HTTP状态码：2xx成功，4xx/5xx失败（见迁移p6）',
  cost_ms       INT NULL COMMENT '耗时毫秒',
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_user_time (user_id, created_at),
  KEY idx_module_time (module, created_at)
) ENGINE=InnoDB COMMENT='操作日志(P1)';

-- 31. 系统日志（P2） --------------------------------------------------
CREATE TABLE system_log (
  id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  level      VARCHAR(10) NOT NULL COMMENT 'DEBUG/INFO/WARN/ERROR',
  logger     VARCHAR(120) NULL,
  message    TEXT NULL,
  stack_trace TEXT NULL,
  host       VARCHAR(60) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_level_time (level, created_at)
) ENGINE=InnoDB COMMENT='系统日志(P2)';

-- 32. 外部接口日志（Mock 适配器留痕，P2） ------------------------------
CREATE TABLE external_api_log (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  api_name     VARCHAR(30) NOT NULL COMMENT 'UTM空管/weather气象/pay支付/GCS',
  request_url  VARCHAR(255) NULL,
  request_body TEXT NULL,
  response_body TEXT NULL,
  http_status  INT NULL,
  cost_ms      INT NULL,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_api_time (api_name, created_at)
) ENGINE=InnoDB COMMENT='外部接口日志(P2)';

-- 33. 备份记录（P2） --------------------------------------------------
CREATE TABLE backup_record (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  backup_type TINYINT NOT NULL COMMENT '1全量2增量',
  file_path   VARCHAR(255) NOT NULL,
  file_size   BIGINT NULL COMMENT '字节',
  started_at  DATETIME NULL,
  finished_at DATETIME NULL,
  status      TINYINT NOT NULL DEFAULT 1 COMMENT '1进行中2成功3失败',
  operator_id BIGINT UNSIGNED NULL,
  remark      VARCHAR(255) NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id)
) ENGINE=InnoDB COMMENT='数据库备份记录(P2)';

-- 34. 帮助文章（P2） --------------------------------------------------
CREATE TABLE help_article (
  id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  category   VARCHAR(50) NULL,
  title      VARCHAR(150) NOT NULL,
  content    MEDIUMTEXT NULL,
  sort       INT NOT NULL DEFAULT 0,
  view_count INT NOT NULL DEFAULT 0,
  status     TINYINT NOT NULL DEFAULT 1 COMMENT '1发布2下架',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted    TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY idx_category (category)
) ENGINE=InnoDB COMMENT='帮助文章(P2)';

-- 35. 优惠券（P2） ----------------------------------------------------
CREATE TABLE coupon (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  coupon_name  VARCHAR(80) NOT NULL,
  coupon_type  TINYINT NOT NULL COMMENT '1满减2折扣',
  threshold    DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '使用门槛',
  amount       DECIMAL(10,2) NULL COMMENT '满减金额',
  discount     DECIMAL(3,2) NULL COMMENT '折扣如0.85',
  total_count  INT NOT NULL DEFAULT 0,
  got_count    INT NOT NULL DEFAULT 0,
  start_at     DATETIME NULL,
  end_at       DATETIME NULL,
  status       TINYINT NOT NULL DEFAULT 1 COMMENT '1启用2停用',
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted      TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY idx_status_time (status, start_at, end_at)
) ENGINE=InnoDB COMMENT='优惠券(P2)';

SET FOREIGN_KEY_CHECKS = 1;

-- #####################################################################
-- 七、初始种子数据（演示/登录即用）
-- #####################################################################

-- 7.1 九个预置角色 ----------------------------------------------------
INSERT INTO role (id, role_code, role_name, role_scope, built_in, sort, remark) VALUES
 (1,'platform_admin','平台管理员',1,1,1,'全平台最高权限'),
 (2,'air_traffic','空管监管员',1,1,2,'飞行计划审批/空域/违规'),
 (3,'ops_maintainer','平台运维',1,1,3,'资源/日志/应急保障'),
 (4,'merchant_admin','商家管理员',2,1,10,'本商家全部功能'),
 (5,'dispatcher','调度员',2,1,11,'调度方案与申报'),
 (6,'finance','财务',2,1,12,'结算对账'),
 (7,'pilot','机长',2,1,13,'接收并执行飞行任务'),
 (8,'merchant_maintainer','设备运维员',2,1,14,'飞行器维保'),
 (9,'customer','客户',3,1,20,'下单与查询');

-- 7.2 权限树（8个一级菜单 + 动作/接口权限） ----------------------------
INSERT INTO permission (id, parent_id, perm_code, perm_name, perm_type, path, sort) VALUES
 (1,0,'dashboard','运行驾驶舱',1,'/dashboard',1),
 (2,0,'order','订单管理',1,'/order',2),
 (3,0,'dispatch','调度飞行',1,'/dispatch',3),
 (4,0,'airspace','空域合规',1,'/airspace',4),
 (5,0,'resource','低空资源',1,'/resource',5),
 (6,0,'monitor','监控中心',1,'/monitor',6),
 (7,0,'settlement','结算管理',1,'/settlement',7),
 (8,0,'system','系统管理',1,'/system',8),
 (9,2,'order:list','订单列表/查询',3,NULL,1),
 (10,2,'order:create','客户下单',3,NULL,2),
 (11,2,'order:cancel','取消订单',3,NULL,3),
 (12,2,'order:refund','退款审核',3,NULL,4),
 (13,3,'dispatch:plan','生成调度方案',3,NULL,1),
 (14,3,'dispatch:assign','指派人机/场站',3,NULL,2),
 (15,3,'dispatch:submit','提交空管申报',3,NULL,3),
 (16,3,'approval:handle','飞行计划审批',3,NULL,4),
 (17,6,'monitor:realtime','实时监控',3,NULL,1),
 (18,6,'monitor:track','轨迹回放',3,NULL,2),
 (19,6,'monitor:violation','违规处置',3,NULL,3),
 (20,4,'airspace:draw','空域绘制',3,NULL,1),
 (21,4,'airspace:audit','空域审核',3,NULL,2),
 (22,5,'resource:aircraft','飞行器管理',3,NULL,1),
 (23,5,'resource:vertiport','场站管理',3,NULL,2),
 (24,5,'resource:qualification','资质审核',3,NULL,3),
 (25,7,'settlement:list','账单查询',3,NULL,1),
 (26,7,'settlement:reconcile','对账处理',3,NULL,2),
 (27,8,'system:user','用户管理',3,NULL,1),
 (28,8,'system:role','角色权限配置',3,NULL,2),
 (29,8,'system:config','参数配置',3,NULL,3),
 (30,8,'system:log','日志审计',3,NULL,4),
 (31,8,'merchant:audit','商家入驻审核',3,NULL,5),
 (32,6,'monitor:emergency','应急处置',3,NULL,5),
 (33,3,'dispatch:execute','起飞/降落确认',3,NULL,5);

-- 7.3 平台管理员 = 全部权限（一条 SELECT 自动铺满） --------------------
INSERT INTO role_permission (role_id, permission_id)
SELECT 1, id FROM permission;

-- 其余角色授权（按最小够用原则）
INSERT INTO role_permission (role_id, permission_id) VALUES
 (2,3),(2,4),(2,6),(2,16),(2,17),(2,18),(2,19),(2,20),(2,21),(2,32),                 -- 空管
 (3,1),(3,5),(3,6),(3,8),(3,22),(3,23),(3,30),(3,32),                                 -- 平台运维
 (4,1),(4,2),(4,3),(4,5),(4,6),(4,7),(4,8),(4,9),(4,10),(4,11),(4,13),(4,14),(4,15),
   (4,17),(4,18),(4,22),(4,23),(4,24),(4,25),(4,27),                                    -- 商家管理员
 (5,1),(5,2),(5,3),(5,6),(5,9),(5,13),(5,14),(5,15),(5,17),(5,18),                   -- 调度员
 (6,1),(6,7),(6,12),(6,25),(6,26),                                                   -- 财务
 (7,1),(7,3),(7,6),(7,17),(7,18),(7,33),                                           -- 机长
 (8,1),(8,5),(8,6),(8,22),(8,23),                                                    -- 设备运维
 (9,10),(9,11);                                                                      -- 客户

-- 7.4 演示商家 --------------------------------------------------------
INSERT INTO merchants (id, merchant_no, merchant_name, unified_credit_code, legal_person,
                       service_area_json, pricing_json, audit_status, status, expire_at)
VALUES (1,'M2026090001','温州天枢示范运营有限公司','91330300MA2TS0001X','张示范',
        JSON_ARRAY('瓯江口新区','龙湾区'),
        JSON_OBJECT('basePerKm',3.5,'perKg',1.2,'urgentRate',1.5),
        2,1,'2027-09-14 00:00:00');

-- 7.5 演示账号 ---------------------------------------------------------
-- 统一初始密码：Admin@123 （BCrypt cost=10，已用 bcrypt.checkpw 校验通过）
-- phone_cipher 为占位明文，后端加密模块上线后由初始化任务统一改写为 AES 密文；
-- phone_digest 为 HMAC-SHA256(演示密钥 tianshu-dev-seed, 手机号)。
INSERT INTO users (id, username, password_hash, user_type, real_name, phone_cipher, phone_digest,
                   realname_status, gender, merchant_id, status) VALUES
 (1,'admin','$2b$10$rtZxNxtzebJj5526GjLX.eALepDfYSRbOd9Zfd9Cj0dBr0AoVmyRa',4,'平台管理员',
   'SEED:13800000001','cad7d968fc1148a51942309d03df27c805db793826577b928a13973d454d8f1a',2,1,NULL,1),
 (2,'merchant','$2b$10$rtZxNxtzebJj5526GjLX.eALepDfYSRbOd9Zfd9Cj0dBr0AoVmyRa',3,'王调度',
   'SEED:13800000002','898fd76502d585ea5f73077fc6c8b05cd0eb367e4b73c4feedca9e5ac3328551',2,1,1,1),
 (3,'customer','$2b$10$rtZxNxtzebJj5526GjLX.eALepDfYSRbOd9Zfd9Cj0dBr0AoVmyRa',1,'李客户',
   'SEED:13800000003','92d9f5ebd2eca1f9c594510748b3c6e6235f9ac097647870231994bc0c0b86e6',2,1,NULL,1),
 (4,'pilot','$2b$10$rtZxNxtzebJj5526GjLX.eALepDfYSRbOd9Zfd9Cj0dBr0AoVmyRa',3,'陈机长',
   'SEED:13800000004','18098ef1ef8ef17bd92d50c05deb4ef89d81f0f08130557b70625ddfe4929c2f',2,1,1,1),
 (5,'pilot02','$2b$10$rtZxNxtzebJj5526GjLX.eALepDfYSRbOd9Zfd9Cj0dBr0AoVmyRa',3,'林机长',
   'SEED:13800000005','a71ce00fcadb96da78c00eaf990a79083d0bfc4c4d8fa5222e75c23e56bc5489',2,2,1,1);

UPDATE merchants SET contact_user_id = 2 WHERE id = 1;

INSERT INTO user_role (user_id, role_id, granted_by) VALUES
 (1,1,NULL),(2,4,1),(2,5,1),(3,9,1),(4,7,1),(5,7,1);
INSERT INTO customer_profile (user_id, customer_type, verified_at) VALUES (3,1,NOW());

-- 7.6 温州演示场站（GCJ-02，坐标已经高德地理编码核对；VP1 位于灵昆岛瓯江口核心区） ----
INSERT INTO vertiport (id, station_no, station_name, station_type, lng, lat, address,
                       merchant_id, capacity, charger_total, charger_available, occupied_count, status) VALUES
 (1,'VP-WZ-001','瓯江口综合示范站',3,120.929500,27.955500,'温州市洞头区瓯江口产业集聚区灵昆岛',1,8,6,6,0,1),
 (2,'VP-WZ-002','龙湾城市起降场',1,120.815000,27.945000,'温州市龙湾区温州湾城市配送点',1,4,0,0,0,1);

-- 7.7 演示飞行器 ------------------------------------------------------
INSERT INTO aircraft (id, aircraft_no, merchant_id, brand, model, serial_no, endurance_min,
                      max_load_kg, max_speed, max_altitude, purchase_at, current_vertiport_id,
                      status, health_score, battery, online) VALUES
 (1,'TS-UAV-001',1,'大疆','FlyCart 30','SN0001',36,30.00,18.0,120,'2026-03-01',1,1,95,100,1),
 (2,'TS-UAV-002',1,'极飞','X100','SN0002',32,40.00,16.0,120,'2026-04-01',2,1,88,96,1);

-- 7.8 温州空域 Mock（示例多边形，GCJ-02；首点=末点闭合） ----------------
-- AS-WZ-004 的 source=1（空管下发）而前三条是 2（平台自绘）：禁飞区的口径以空管为准，
-- 平台不自己画禁区。它不写高度上下限，因为整片鹿城建成区一律不开放。
INSERT INTO airspace (airspace_no, airspace_name, airspace_type, source, geometry_json,
                      altitude_min, altitude_max, region_name, status, control_reason) VALUES
 ('AS-WZ-001','龙湾机场净空禁飞区',1,2,
  '{"type":"Polygon","coordinates":[[[120.830,27.900],[120.880,27.900],[120.880,27.930],[120.830,27.930],[120.830,27.900]]]}',
  0,0,'龙湾区',1,NULL),
 ('AS-WZ-002','瓯海中心限飞区',2,2,
  '{"type":"Polygon","coordinates":[[[120.600,27.980],[120.660,27.980],[120.660,28.030],[120.600,28.030],[120.600,27.980]]]}',
  0,60,'瓯海区',1,NULL),
 ('AS-WZ-003','瓯江口低空可飞示范区',3,2,
  '{"type":"Polygon","coordinates":[[[120.885,27.915],[120.975,27.915],[120.975,27.975],[120.885,27.975],[120.885,27.915]]]}',
  20,120,'瓯江口',1,NULL),
 ('AS-WZ-004','鹿城区核心区净空禁飞区',1,1,
  '{"type": "Polygon", "coordinates": [[[120.55, 28.025], [120.75, 28.025], [120.75, 28.09], [120.55, 28.09], [120.55, 28.025]]]}',
  NULL,NULL,'鹿城区',1,'城市建成区人口密集，不开放低空物流飞行');

-- 7.9 系统参数（业务阈值，后端从这里读，不写死在代码里） ----------------
INSERT INTO sys_config (config_group, config_key, config_value, value_type, remark) VALUES
 ('order','order_pay_timeout_min','30','number','待支付订单超时分钟数'),
 ('order','order_take_timeout_min','15','number','待接单超时分钟数'),
 ('track','track_frame_interval_sec','1','number','仿真器推帧间隔秒'),
 ('track','track_online_months','2','number','轨迹在线保留月数，超期归档'),
 ('qualification','merchant_qual_warn_days','7','number','商家资质提前预警天数'),
 ('qualification','staff_qual_warn_days','15','number','人员资质提前预警天数'),
 ('security','login_fail_lock_times','5','number','连续登录失败锁定次数'),
 ('flight','battery_return_threshold','20','number','低电量返航阈值%');

-- =====================================================================
-- 建库完成。验收 SQL：
--   USE tianshu_utms;
--   SHOW TABLES;                       -- 应为 35 张表
--   SELECT role_code,role_name FROM role;                       -- 9 角色
--   SELECT COUNT(*) FROM permission;                            -- 33 权限点
--   SELECT username,user_type,status FROM users;                -- 5 演示账号
--   SELECT airspace_name,airspace_type FROM airspace;           -- 3 温州空域
--   SELECT PARTITION_NAME, TABLE_ROWS FROM information_schema.PARTITIONS
--     WHERE TABLE_NAME='flight_track';                          -- 7 个分区
-- =====================================================================
