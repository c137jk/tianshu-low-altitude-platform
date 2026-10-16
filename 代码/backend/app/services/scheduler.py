"""APScheduler 定时任务（需求 4.2.2-3 超时未接单提醒 / 4.2.3 资质到期预警 / DDL 轨迹月分区维护 / 6.3.2 券下架）。

差距分析里这几条一直挂在"缺"上，缺的不是表，是**没有任何东西会自己跑**：
`qualification.status` 的 2（即将到期）/3（已过期）在 DDL 里定义了、资质台账也按它排序出标签，
却从来没人写进去；`order_take_timeout_min`、`track_online_months`、`merchant_qual_warn_days`
三个参数躺在 sys_config 里零引用；`flight_track` 的月分区只建到 2027-02 就交给 pmax 兜底
（`sql/tianshu-ddl-v1.sql:489-496`），第三个自然月之后的帧全落在 pmax 里，
"整月 DROP 归档"这个设计从建库那天起就没成立过。本模块是这些字段唯一的写入方。

四条不肯含糊的口径：
  · **任务函数是普通函数，APScheduler 只是它的一个调用方**——测试直接调 `scan_qualifications()`，
    不用等调度器醒；线上也能手工跑一轮复现。调度层只管"什么时候跑、跑几轮"。
  · **每一轮都幂等**：提醒按业务事件去重（`_notified`），归档动作全是带条件的 UPDATE 与 DDL。
    线程重启、周期改短、人为补跑，都不该变成对用户的反复轰炸或多删一个分区。
  · **写库方向单一**：资质状态只沿 有效→即将到期→已过期 往前走，审核结论（4 驳回）
    绝不被时间推进覆盖；分区 DROP 默认关（`track_partition_drop_enabled=0`）——
    预建分区没有副作用，删真实轨迹数据必须有人显式把开关打开。
  · **开关与仿真器共用**：`FLASK_SIM_DISABLED` 现在读作"不要起任何后台线程"。
    测试与探针脚本本来就带着它，复用同一个开关，比新增一个"忘了设就在背后改库"的隐患靠谱。
"""
import logging
import os
import re
import threading
from datetime import date, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text

from app.common import scope
from app.extensions import db
from app.services import notify, order_service

log = logging.getLogger("tianshu.scheduler")

# 飞行器没有"本人"，到期了要平台资质审核岗来拦，所以预警发给商家、过期追加平台
QUAL_PERM = "resource:qualification"
COUPON_PERM = "marketing:coupon"
QUAL_WARN_CFG = {1: "merchant_qual_warn_days", 2: "staff_qual_warn_days",
                 3: "aircraft_qual_warn_days"}
QUAL_WARN_DEFAULT = {1: 7, 2: 15, 3: 15}

TRACK_TABLE = "flight_track"
PART_CATCH_ALL = "pmax"
_PART_NAME = re.compile(r"^p(\d{6})$")
_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()


# ------------------------------------------------------------------
# 日历：分区名与保留期都从这里算，别散在 SQL 字符串里
# ------------------------------------------------------------------
def _first_of_month(d: date) -> date:
    return d.replace(day=1)


def _add_months(month_start: date, n: int) -> date:
    """输入必须是不带日的"月初"（本模块所有月份都这么表示，省掉月末进位的问题）。"""
    total = month_start.year * 12 + (month_start.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def _part_name(month_start: date) -> str:
    return f"p{month_start:%Y%m}"


def _part_month(name: str) -> date | None:
    """分区名 → 该分区承载的月份；pmax 之类不合名的返回 None（永远不会被当成可删分区）。"""
    hit = _PART_NAME.match(name or "")
    if not hit:
        return None
    return date(int(hit.group(1)[:4]), int(hit.group(1)[4:]), 1)


# ------------------------------------------------------------------
# 去重：一个业务事件只提醒一次
# ------------------------------------------------------------------
def _notified(biz_type: str, biz_id: int) -> bool:
    """这条业务事件是否已经提醒过（任何收件人）。

    没用 `notify.exists()`：那个按 (user_id, biz_id) 查，口径是"这个人收过没有"；
    这里要的是"这件事提醒过了"——换了岗位、补了商家联系人，都不该把老事件的提醒再发一遍。
    """
    return db.session.execute(text(
        "SELECT 1 FROM message_notification WHERE biz_type=:bt AND biz_id=:bi LIMIT 1"),
        {"bt": biz_type, "bi": int(biz_id)}).first() is not None


# ------------------------------------------------------------------
# 任务 1：超时未接单提醒（需求 4.2.2-3）
# ------------------------------------------------------------------
def remind_order_take_timeout() -> int:
    """待接单挂过 order_take_timeout_min 就催本商家的接单岗，返回本轮发出的条数。

    只催不代接单：需求要的是"设置接单规则：符合服务范围自动承接"，
    那需要一张商家级规则表（服务范围怎么算、哪些品类豁免），现在后端没有这张表，
    替商家接单等于替他做经营决策（差距分析把"自动接单规则"记为未做，本任务只是前半段）。
    """
    minutes = int(scope.sys_cfg_num("order_take_timeout_min", 15, int))
    rows = db.session.execute(text(
        "SELECT o.id, o.order_no, o.merchant_id, o.goods_type, o.total_amount, o.created_at"
        " FROM orders o WHERE o.deleted = 0 AND o.status = 1 AND o.merchant_id IS NOT NULL"
        " AND o.created_at <= DATE_SUB(NOW(), INTERVAL :m MINUTE)"
        " ORDER BY o.created_at LIMIT 100"), {"m": minutes}).mappings().all()
    sent = 0
    for r in rows:
        if _notified("order_take_timeout", r["id"]):
            continue
        waited = int((datetime.now() - r["created_at"]).total_seconds() // 60)
        sent += notify.notify_many(
            scope.user_ids_with_perm(order_service.MERCHANT_ORDER_PERM, r["merchant_id"]),
            f"订单 {r['order_no']} 超时未接单",
            f"{r['goods_type']}，应收 {float(r['total_amount'] or 0):.2f} 元，"
            f"已挂起 {waited} 分钟（超时阈值 {minutes} 分钟）。请尽快接单或写明原因拒单。",
            biz_type="order_take_timeout", biz_id=r["id"])
    return sent


# ------------------------------------------------------------------
# 任务 2：场站预约开始提醒
# ------------------------------------------------------------------
def remind_station_reservations() -> int:
    """预约开始前提醒申请人。

    只管已通过（status=2）的：待审那条在申请/审核接口里各发过一封，重复提醒是噪声。
    `start_at > NOW()` 是必要的——已经开始甚至结束了还发"即将开始"，就是假提醒。
    """
    lead = int(scope.sys_cfg_num("reserve_remind_lead_min", 30, int))
    rows = db.session.execute(text(
        "SELECT r.id, r.user_id, r.reservation_no, r.start_at, v.station_name"
        " FROM station_reservation r LEFT JOIN vertiport v ON v.id = r.station_id"
        " WHERE r.deleted = 0 AND r.status = 2"
        " AND r.start_at > NOW() AND r.start_at <= DATE_ADD(NOW(), INTERVAL :m MINUTE)"
        " ORDER BY r.start_at LIMIT 100"), {"m": lead}).mappings().all()
    sent = 0
    for r in rows:
        if _notified("reserve_remind", r["id"]):
            continue
        sent += 1 if notify.notify(
            r["user_id"], f"场站预约即将开始：{r['station_name'] or '所选场站'}",
            f"预约单 {r['reservation_no']} 将于 {r['start_at']:%Y-%m-%d %H:%M} 开始，请提前到场办理。"
            f"不能按时使用请及时取消，把机位让给别的商家。",
            biz_type="reserve_remind", biz_id=r["id"]) else 0
    return sent


# ------------------------------------------------------------------
# 任务 3：资质到期扫描（需求 4.2.3）
# ------------------------------------------------------------------
def _qual_recipients(row, expired: bool) -> list[int]:
    """预警发给谁：主体自己；已过期再追加平台资质审核岗——快到期是本人的事，
    已经过期是平台要拦的事（资质过期的人员还能被排进飞行计划，那是一道该关上的闸门）。"""
    stype, sid = int(row["subject_type"]), row["subject_id"]
    mine: list[int] = []
    if stype == 2:
        mine = [sid] if sid else []
    elif stype == 1:
        contact = db.session.execute(text(
            "SELECT contact_user_id FROM merchants WHERE id=:i AND deleted=0"),
            {"i": sid}).scalar()
        mine = [contact] if contact else []
    elif stype == 3 and sid:
        merchant_id = db.session.execute(text(
            "SELECT merchant_id FROM aircraft WHERE id=:i"), {"i": sid}).scalar()
        mine = scope.user_ids_with_perm(QUAL_PERM, merchant_id)
    if expired:
        mine += scope.user_ids_with_perm(QUAL_PERM, platform_only=True)
    return [x for x in mine if x]


def scan_qualifications() -> dict:
    """把 qualification.status 从"有效"推进到"即将到期/已过期"，并各发一次预警。

    状态只从 1、2 出发（`status IN (1,2)`）：4 是审核结论、3 是终态，
    时间推进不覆盖审核结论，也不把已过期拉回即将到期。
    预警窗口按主体分三档（商家/人员/飞行器各一个 sys_config 键），
    窗口调小之后已经标成"即将到期"的行不往回调——回退要连通知记录一起清理，
    为一个人可能根本不会遇到的配置抖动去建第二套状态流转，不值。
    """
    windows = {t: int(scope.sys_cfg_num(k, QUAL_WARN_DEFAULT[t], int))
               for t, k in QUAL_WARN_CFG.items()}
    rows = db.session.execute(text(
        "SELECT id, subject_type, subject_id, qual_name, expire_at, status FROM qualification"
        " WHERE deleted = 0 AND status IN (1, 2) AND expire_at IS NOT NULL"
        " AND expire_at <= DATE_ADD(NOW(), INTERVAL :d DAY)"
        " ORDER BY expire_at LIMIT 200"), {"d": max(windows.values())}).mappings().all()
    today = date.today()
    out = {"expiring": 0, "expired": 0, "notified": 0}
    for r in rows:
        days_left = (r["expire_at"].date() - today).days
        expired = r["expire_at"] <= datetime.now()
        if not expired:
            window = windows.get(int(r["subject_type"]), windows[2])
            if days_left > window:
                continue                    # 落在最大窗口里，但超出本主体那一档：还是"有效"
        target = 3 if expired else 2
        biz_type = "qual_expired" if expired else "qual_expiring"
        if int(r["status"]) != target:
            db.session.execute(text(
                "UPDATE qualification SET status=:s, updated_at=NOW()"
                " WHERE id=:i AND deleted=0 AND status IN (1, 2)"),
                {"s": target, "i": r["id"]})
            out["expiring" if not expired else "expired"] += 1
        if _notified(biz_type, r["id"]):
            continue
        label = "已过期" if expired else f"将在 {days_left} 天后到期"
        out["notified"] += notify.notify_many(
            _qual_recipients(r, expired), f"资质{label}：{r['qual_name']}",
            f"该资质有效期至 {r['expire_at']:%Y-%m-%d}。"
            + ("请立即补充证照，过期人员/飞行器不应再被排入飞行任务。" if expired
               else "请安排换证或续期，到期后系统会标记为已过期。"),
            biz_type=biz_type, biz_id=r["id"])
    db.session.commit()
    return out


# ------------------------------------------------------------------
# 任务 4：轨迹月分区预建与归档（DDL 17 的分区策略）
# ------------------------------------------------------------------
def maintain_track_partitions(table: str = TRACK_TABLE) -> dict:
    """预建本月与下月的分区（缺哪个补哪个），并按在线保留月数回收更老的整月分区。

    删这一步默认关：DROP PARTITION 是**秒级删掉整月真实轨迹**，
    留多久是运营与合规的口径（需求 10.5 要求轨迹留存），不该由一段代码替他们决定，
    所以 `track_partition_drop_enabled` 现默认 0，本任务只报"该删哪些"不动手。
    分区名先过 `_PART_NAME` 再拼进 DDL：pmax 这类不合名的永远进不了删除列表。

    `table` 是给测试留的接缝：DROP 分支不能拿真 flight_track 试（删掉的就是种子分区，
    测试库之后再也长不出那个月），所以测试自己建一张同构的探针表。表名不是用户输入，
    但它最终拼进 DDL 语句，照样过一道标识符白名单。
    """
    if not _TABLE_RE.match(table):
        raise ValueError(f"非法表名：{table!r}")
    online = max(1, int(scope.sys_cfg_num("track_online_months", 2, int)))
    drop_on = int(scope.sys_cfg_num("track_partition_drop_enabled", 0, int)) == 1
    have = set(db.session.execute(text(
        "SELECT PARTITION_NAME FROM information_schema.PARTITIONS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME=:t AND PARTITION_NAME IS NOT NULL"),
        {"t": table}).scalars().all())
    first = _first_of_month(date.today())
    created: list[str] = []
    for month in (first, _add_months(first, 1)):
        name = _part_name(month)
        if name in have:
            continue
        db.session.execute(text(
            f"ALTER TABLE {table} REORGANIZE PARTITION {PART_CATCH_ALL} INTO ("
            f"PARTITION {name} VALUES LESS THAN (TO_DAYS('{_add_months(month, 1):%Y-%m-%d}')),"
            f"PARTITION {PART_CATCH_ALL} VALUES LESS THAN MAXVALUE)"))
        db.session.commit()
        created.append(name)
        have.add(name)
    keep_from = _add_months(first, 1 - online)     # 保留最近 online 个自然月
    due = sorted(n for n in have
                 if (m := _part_month(n)) is not None and m < keep_from)
    dropped: list[str] = []
    if drop_on:
        for name in due:
            db.session.execute(text(f"ALTER TABLE {table} DROP PARTITION {name}"))
            db.session.commit()
            dropped.append(name)
    return {"created": created, "dueDrop": due, "dropped": dropped,
            "dropEnabled": drop_on, "keepFrom": f"{keep_from:%Y-%m}"}


# ------------------------------------------------------------------
# 任务 5：到期优惠券下架（6.3.2 归档动作）
# ------------------------------------------------------------------
def archive_expired_coupons() -> dict:
    """过期已久的券置为停用（coupon.status 1→2），返回下架张数。

    只动母表，一张都不动 user_coupon：券包里的"已过期"是读侧按 end_at 现判的
    （`coupon_service.expired_of`，p15 迁移里写明了为什么不存状态），
    这里再写一份就造出两张皮；要改的是"还能不能再领"，那是 `claimable()` 读的 coupon.status。
    宽限期（coupon_offline_after_days）留着，是给退货处理和客服对账用的时间窗。
    """
    days = int(scope.sys_cfg_num("coupon_offline_after_days", 30, int))
    rows = db.session.execute(text(
        "SELECT id, coupon_name, end_at FROM coupon"
        " WHERE deleted = 0 AND status = 1 AND end_at IS NOT NULL"
        " AND end_at < DATE_SUB(NOW(), INTERVAL :d DAY) ORDER BY end_at LIMIT 100"),
        {"d": days}).mappings().all()
    if not rows:
        return {"offlined": 0}
    kept = db.session.execute(text(
        "UPDATE coupon SET status = 2, updated_at = NOW()"
        " WHERE deleted = 0 AND status = 1 AND end_at IS NOT NULL"
        " AND end_at < DATE_SUB(NOW(), INTERVAL :d DAY)"), {"d": days}).rowcount
    db.session.commit()
    ops = scope.user_ids_with_perm(COUPON_PERM, platform_only=True)
    for r in rows:
        if _notified("coupon_offline", r["id"]):
            continue
        notify.notify_many(
            ops, f"优惠券到期下架：{r['coupon_name']}",
            f"有效期止 {r['end_at']:%Y-%m-%d}，已过 {days} 天宽限期，系统已自动下架，不能再领取。"
            f"需要继续发放请在营销页把有效期改到新时段并重新启用。",
            biz_type="coupon_offline", biz_id=r["id"])
    return {"offlined": int(kept)}


# ------------------------------------------------------------------
# 调度层
# ------------------------------------------------------------------
def _job_defs() -> tuple[dict, ...]:
    """任务表。触发器在这里现造：IntervalTrigger 把起算点定在构造那一刻，
    import 时就造好会让首轮执行时间跟着模块导入时刻漂。"""
    return (
        {"id": "order_take_timeout", "name": "超时未接单提醒",
         "trigger": IntervalTrigger(minutes=2), "func": remind_order_take_timeout},
        {"id": "reserve_remind", "name": "场站预约开始提醒",
         "trigger": IntervalTrigger(minutes=5), "func": remind_station_reservations},
        {"id": "qual_scan", "name": "资质到期扫描与预警",
         "trigger": CronTrigger(hour=1, minute=10), "func": scan_qualifications},
        {"id": "track_partition", "name": "轨迹月分区预建与归档",
         "trigger": CronTrigger(hour=2, minute=30), "func": maintain_track_partitions},
        {"id": "coupon_offline", "name": "到期优惠券下架",
         "trigger": CronTrigger(hour=3, minute=0), "func": archive_expired_coupons},
    )


def _runner(app, func):
    """调度线程里没有 Flask 上下文，读 sys_config 和拿 session 都得自己 push 一层。

    异常一律吃掉并记日志：任务写坏了不能把调度线程带走，更不能影响请求线程。
    """
    def run():
        with app.app_context():
            try:
                result = func()
            except Exception:
                log.exception("scheduled job %s failed", func.__name__)
            else:
                log.info("scheduled job %s -> %s", func.__name__, result)
    run.__name__ = func.__name__
    return run


def start(app):
    """应用工厂里调用：起后台调度器（守卫链与 flight_simulator.start 一致）。"""
    global _scheduler
    with _lock:
        if _scheduler is not None:
            return
        if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
            return                              # reloader 的监视进程也 import 一遍，别起两份
        if os.environ.get("FLASK_SIM_DISABLED"):
            log.info("scheduler disabled by FLASK_SIM_DISABLED")
            return
        sch = BackgroundScheduler(
            daemon=True,
            # coalesce：一轮跑迟了就只补一次；misfire_grace_time=None：夜里没开机，
            # 早上起来就把当天的扫描补掉（资质状态与分区都是"越早对齐越好"的判定）。
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": None})
        for job in _job_defs():
            sch.add_job(_runner(app, job["func"]), job["trigger"],
                        id=job["id"], name=job["name"])
        sch.start()
        _scheduler = sch
        log.info("scheduler started: %d jobs", len(sch.get_jobs()))


def snapshot() -> dict:
    """调度状态（系统参数页用）。

    没起来也要把"本该有哪些任务"列出来：空表只会让人分不清是功能没做，还是开关关着。
    """
    live = {j.id: j for j in (_scheduler.get_jobs() if _scheduler else [])}
    return {
        "running": _scheduler is not None,
        "disabled": bool(os.environ.get("FLASK_SIM_DISABLED")),
        "jobs": [{"id": d["id"], "name": d["name"], "trigger": str(d["trigger"]),
                  "nextRunAt": live[d["id"]].next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                  if d["id"] in live and live[d["id"]].next_run_time else None}
                 for d in _job_defs()],
    }
