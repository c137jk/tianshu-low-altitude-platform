"""轨迹与遥测上报落库（设计03 D6 的 `POST /tracks/batch`、设计07 的"攒帧批量写 flight_track"）。

设计07 把 `/tracks/batch` 定位成**仿真器与未来真机 GCS 共用的同一个上报入口**
（"真机走同一个入口，业务代码零改动"，`flight_track.source` 就是为这一步留的列：
`sql/tianshu-ddl-v1.sql:484`）。在此之前全仓只有 `flight_simulator` 里一段 ORM 直写，
`source` 硬编码 1，真机数据没有落点——"统一入口"只是文档里的一句话。
本模块是那个入口的实现体：校验在 HTTP 边界做一次，落库与合规判定在这里只做一次，
仿真器和新端点调的是同一个函数。

四条口径：

* **`source` 由调用方在服务端定死，请求体里的一律忽略**。否则上报方可以自己填 1 把真机数据
  冒充成仿真数据（或反过来），`source` 这个用于"这条轨迹该不该信"的列就失去意义了。
* **违规判定不在这里发明第二套**。每一帧照旧交给 `compliance_monitor.annotate()`，
  打标必须在 INSERT 之前（复合主键 + 毫秒时间会让插后改字段抛 StaleDataError），
  这条铁律由 `compliance_monitor.py:281-290` 单独守着，这里不绕过它。
  设备自报的 `violation` 字段同样忽略——地面站说自己没偏航不算证据。
* **上报只回头写"热快照"与链路状态**（`plan.current_*` / `plan.last_report_at` /
  `aircraft.online` / 位置电量），**绝不改 `plan.status`、`aircraft.status`**：
  状态机的唯一入口是 D3（`dispatch_service`），遥测不是审批也不是降落指令。
  这条与仿真器第 3 条口径同源。
* **一帧脏数据不许拖垮整批**。上报是持续动作，一次网络抖动送来 200 帧、其中 3 帧字段越界，
  正确结果收下 197 帧并回报哪三帧为什么被丢；整批 400 会让人开始怀疑整个链路。
  越界的判定见下面 `_RANGE`，坐标那一档还顺手兜住"lng/lat 写反"这个最常见的接入错误。
"""
import logging
from datetime import datetime

from sqlalchemy import text

from app.extensions import db
from app.models import FlightTrack
from app.services import compliance_monitor
from app.services.geo import bearing_deg

log = logging.getLogger("tianshu.track")

SOURCE_NAME = {1: "仿真器", 2: "真机GCS"}

MAX_FRAMES = 2000              # 单次上报帧数上限：按 1s/帧约 33 分钟，再多就该分次发
FUTURE_TOLERANCE_SEC = 600     # 只容忍 10 分钟的机载时钟偏差，未来的帧不能当心跳
HISTORY_LIMIT_DAYS = 7         # 断链补报最多回补 7 天，再老的数据已经没有回放价值

# 列宽与物理意义都在这里卡：越界写进 DECIMAL/TINYINT 会被 MySQL 截断或报 1264 整批回滚。
_RANGE = {
    "lng": (73.0, 136.0),       # 中国境内经度；写反的坐标几乎必然掉出这个区间
    "lat": (3.0, 54.0),
    "altitude": (-100.0, 3000.0),
    "speed": (0.0, 300.0),      # m/s
}
TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
               "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f")
ALT_KEYS = ("altitude", "alt")     # 报文里两三种写法都有，只认这两个别名


def _altitude(item: dict):
    for key in ALT_KEYS:
        if item.get(key) is not None:
            return _num(item.get(key))
    return None


# ------------------------------------------------------------------
# 解析与校验：只服务于外部边界（HTTP），内部调用方直接给 dict
# ------------------------------------------------------------------
def _num(raw):
    """能转 float 就转，不能就返回 None（字符串数字是遥测报文的常态）。"""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _time(raw, now: datetime) -> datetime | None:
    """帧时间：接受常见三种写法，解析不出来返回 None 由调用方判错。"""
    if raw is None:
        return now
    if isinstance(raw, datetime):
        return raw.replace(microsecond=0)
    s = str(raw).strip().replace("Z", "")
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(s, fmt).replace(microsecond=0)
        except ValueError:
            continue
    try:                                        # 也收秒级时间戳（GCS 常用）
        ts = float(s)
    except ValueError:
        return None
    if ts > 1e12:                               # 毫秒时间戳
        ts /= 1000.0
    return datetime.fromtimestamp(ts).replace(microsecond=0)


def normalize_frames(raw, *, now: datetime) -> tuple[list[dict], list[dict]]:
    """请求体里的帧 → 可信帧列表 + 被丢弃帧的原因列表。

    顺序保持上报顺序：`persist_frames` 靠相邻帧推航向角，打乱了航向就是错的。
    """
    frames: list[dict] = []
    errors: list[dict] = []
    if not isinstance(raw, list):
        return frames, [{"index": 0, "reason": "frames 必须是数组"}]
    for i, item in enumerate(raw):
        if len(frames) >= MAX_FRAMES:
            errors.append({"index": i, "reason": f"单批最多 {MAX_FRAMES} 帧，请分次上报"})
            continue
        if not isinstance(item, dict):
            errors.append({"index": i, "reason": "帧必须是对象"})
            continue
        bad = None
        vals = {}
        for key, (lo, hi) in _RANGE.items():
            v = _altitude(item) if key == "altitude" else _num(item.get(key))
            if v is None:
                bad = f"{key} 缺失或不是数字"
                break
            if not lo <= v <= hi:
                bad = f"{key}={v} 超出 {lo}~{hi}"
                break
            vals[key] = round(v, 6 if key in ("lng", "lat") else 2)
        if bad:
            errors.append({"index": i, "reason": bad})
            continue
        t = _time(item.get("t", item.get("time", item.get("trackTime"))), now)
        if t is None:
            errors.append({"index": i, "reason": "时间格式无法解析"})
            continue
        if (t - now).total_seconds() > FUTURE_TOLERANCE_SEC:
            errors.append({"index": i, "reason": "帧时间晚于服务器时间，请校准机载时钟"})
            continue
        if (now - t).total_seconds() > HISTORY_LIMIT_DAYS * 86400:
            errors.append({"index": i, "reason": f"帧时间超过 {HISTORY_LIMIT_DAYS} 天，不在补报窗口内"})
            continue
        battery = _num(item.get("battery"))
        direction = _num(item.get("direction"))
        frames.append({
            "t": t, **vals,
            "battery": None if battery is None else max(0, min(100, int(round(battery)))),
            "direction": None if direction is None else int(direction) % 360,
        })
    return frames, errors


# ------------------------------------------------------------------
# 落库
# ------------------------------------------------------------------
def _known_times(plan_id: int, frames: list[dict]) -> set[datetime]:
    """这批帧覆盖的时间区间里，库里已经有哪些帧——重传不该把同一段航迹存两遍。"""
    lo = min(f["t"] for f in frames)
    hi = max(f["t"] for f in frames)
    rows = db.session.execute(
        text("SELECT track_time FROM flight_track WHERE plan_id=:p"
             " AND track_time BETWEEN :lo AND :hi"),
        {"p": plan_id, "lo": lo, "hi": hi}).all()
    return {r[0].replace(microsecond=0) for r in rows}


def persist_frames(plan, frames: list[dict], *, source: int,
                   dedupe: bool = False) -> dict:
    """把帧写进 flight_track 并逐帧做合规判定。不 commit，事务边界由调用方掌握。

    `dedupe` 只对外的上报入口开：仿真器的帧是连续推进出来的，不存在重传，
    每 tick 多查一次库纯属白花。
    """
    out = {"accepted": 0, "duplicated": 0, "violations": 0, "newTypes": []}
    if not frames:
        return out
    if source not in SOURCE_NAME:
        raise ValueError(f"未知轨迹来源：{source}")
    have = _known_times(plan.id, frames) if dedupe else set()
    prev: tuple[float, float] | None = None
    for f in frames:
        if f["t"] in have:
            out["duplicated"] += 1
            continue
        track = FlightTrack(
            plan_id=plan.id, aircraft_id=plan.aircraft_id, order_id=plan.order_id,
            lng=f["lng"], lat=f["lat"], altitude=f["altitude"], speed=f["speed"],
            direction=f.get("direction") if f.get("direction") is not None
            else bearing_deg(prev, f["lng"], f["lat"]),
            battery=f["battery"] if f.get("battery") is not None else 100,
            track_time=f["t"], source=source)
        hits = compliance_monitor.annotate(plan, track, now=f["t"])   # 打标必须在 INSERT 前
        db.session.add(track)
        db.session.flush()
        have.add(f["t"])
        out["accepted"] += 1
        if hits:
            recorded = compliance_monitor.commit_findings(plan, track, hits, now=f["t"])
            out["violations"] += len(recorded)
            out["newTypes"] += recorded
        prev = (f["lng"], f["lat"])
    return out


def heartbeat_frame(body: dict) -> dict:
    """从心跳请求体里挑出可信字段。坐标越界或没带坐标就当"这次没报位置"——
    心跳本身仍然是"听到了"，不该因为缺一副经纬度就整条丢掉。"""
    out: dict = {}
    lng, lat = _num(body.get("lng")), _num(body.get("lat"))
    if lng is not None and lat is not None \
            and _RANGE["lng"][0] <= lng <= _RANGE["lng"][1] \
            and _RANGE["lat"][0] <= lat <= _RANGE["lat"][1]:
        out["lng"], out["lat"] = round(lng, 6), round(lat, 6)
    alt, spd, bat = _altitude(body), _num(body.get("speed")), _num(body.get("battery"))
    if alt is not None and _RANGE["altitude"][0] <= alt <= _RANGE["altitude"][1]:
        out["altitude"] = round(alt, 2)
    if spd is not None and _RANGE["speed"][0] <= spd <= _RANGE["speed"][1]:
        out["speed"] = round(spd, 2)
    if bat is not None:
        out["battery"] = max(0, min(100, int(round(bat))))
    return out


def apply_telemetry(plan, frame: dict | None, *, aircraft=None, now: datetime | None = None) -> None:
    """用最新一帧回头写热快照与链路状态（这一步就是需求 4.5 要的"设备心跳"）。

    只写 `online` 与位置电量，不写 `status`：在飞/落地/回收是 D3 状态机的事，
    遥测说了不算。`last_report_at` 用服务器时间而不是帧时间——失联判定问的是
    "地面最后一次听到它是什么时候"，机载时钟晚了十分钟不该让设备一直显示在线。
    缺字段跳过即可，心跳只带电量不带位置是常态。
    """
    now = now or datetime.now()
    plan.last_report_at = now
    frame = frame or {}
    if frame.get("lng") is not None and frame.get("lat") is not None:
        plan.current_lng, plan.current_lat = frame["lng"], frame["lat"]
        if aircraft is not None:
            aircraft.current_lng, aircraft.current_lat = frame["lng"], frame["lat"]
    if frame.get("altitude") is not None:
        plan.current_altitude = frame["altitude"]
    if frame.get("speed") is not None:
        plan.current_speed = frame["speed"]
    if frame.get("battery") is not None:
        plan.current_battery = frame["battery"]
        if aircraft is not None:
            aircraft.battery = frame["battery"]
    if aircraft is not None:
        aircraft.online = 1
