"""B2 订单：多条件检索（含手机号）/ 管理端导出 / 服务空域准入 / 批量导入 / 强制终止。

这一批用例全部对着差距分析 §3.1 的"看着有、其实没通电"来写，尤其两条：
  - 前端输入框写着"订单号 / 手机号"，后端从来不查手机号 → 输手机号恒 0 结果。
    根因不是少写条件，而是 sender_phone 是 AES-GCM 随机 nonce 密文，LIKE 结构上不可能命中，
    只能靠摘要列。所以"手机号能搜到"必须被用例钉住，否则哪天有人把摘要列写漏又会退回 0 结果。
  - 批量导入不许有自己的校验分支：禁运品 / 服务空域 / 禁飞区必须和普通下单同一道闸，
    否则就出现"页面下不进去的单，改成 CSV 就能进来"。
"""
import csv
import io
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.common.errors import ApiError
from app.extensions import db
from app.models import Merchant, Order, PaymentTransaction, User, Vertiport
from app.services import geo, order_import, order_service, pricing_service

CSV_HEADERS = order_import.template_csv().lstrip("﻿").splitlines()[0].split(",")
ROW_KEYS = ("senderName", "senderPhone", "senderAddress", "senderLng", "senderLat",
            "receiverName", "receiverPhone", "receiverAddress", "receiverLng", "receiverLat",
            "goodsType", "goodsName", "weightKg", "deliveryType", "expectAt", "remark")


def _u(user_ids, username):
    return db.session.get(User, user_ids[username])


def _pt(vp_id: int, east: float, north: float) -> tuple[float, float]:
    vp = db.session.get(Vertiport, vp_id)
    return geo.offset_meters(float(vp.lng), float(vp.lat), east, north)


def _payload(**over) -> dict:
    """两个演示场站附近的取送货点：在服务空域内、且航线不穿龙湾净空禁飞区。"""
    s, r = _pt(1, 60, 40), _pt(2, 120, -80)
    base = {
        "senderName": "张三", "senderPhone": "13900000011", "senderAddress": "瓯江口取货点",
        "senderLng": Decimal(str(round(s[0], 6))), "senderLat": Decimal(str(round(s[1], 6))),
        "receiverName": "李四", "receiverPhone": "13900000022", "receiverAddress": "龙湾收货点",
        "receiverLng": Decimal(str(round(r[0], 6))), "receiverLat": Decimal(str(round(r[1], 6))),
        "goodsType": "文件", "goodsName": "合同", "weightKg": Decimal("1.0"), "deliveryType": 1,
    }
    return {**base, **over}


def _mk(customer, **over) -> Order:
    return order_service.create_order(customer, _payload(**over))


def _csv(resp):
    """下载响应 → (表头列表, 数据行列表)。utf-8-sig 顺带验证了 BOM 存在。"""
    table = list(csv.reader(io.StringIO(resp.data.decode("utf-8-sig"))))
    table = [r for r in table if any(c.strip() for c in r)]
    return table[0], table[1:]


def _row(**over) -> list[str]:
    s, r = _pt(1, 60, 40), _pt(2, 120, -80)
    vals = ["张三", "13900000011", "瓯江口取货点", f"{s[0]:.6f}", f"{s[1]:.6f}",
            "李四", "13900000022", "龙湾收货点", f"{r[0]:.6f}", f"{r[1]:.6f}",
            "文件", "合同", "1.0", "1", "", ""]
    for k, v in over.items():
        vals[ROW_KEYS.index(k)] = v
    return vals


def _batch_body(rows: list[list[str]]) -> bytes:
    """按模板表头拼一份 CSV（表头直接取模板，等于顺手保证"模板能被解析"）。"""
    lines = [",".join(CSV_HEADERS)] + [",".join(r) for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8-sig")


def _post_batch(api, who: str, body: bytes, **form):
    data = {"file": (io.BytesIO(body), "批量导入.csv"), **form}
    return api.as_(who).post("/api/orders/batch", data=data,
                             content_type="multipart/form-data").get_json()


@pytest.fixture()
def merchant1(app_ctx):
    """演示商家（下单唯一承接方），服务空域是迁移 20260929_p12 灌的多边形。"""
    return db.session.query(Merchant).filter_by(id=1).first()


@pytest.fixture()
def legacy_service_area(merchant1):
    """临时把服务空域退回"只有区域名、没有几何"的旧形态，用完必须还原。

    _truncate 不清 merchants 表，不还原的话这个"不设防"的形态会漏给后面的用例。
    """
    keep = merchant1.service_area_json
    merchant1.service_area_json = ["瓯江口新区", "龙湾区"]
    db.session.commit()
    yield merchant1
    merchant1.service_area_json = keep
    db.session.commit()


# ---------------------------------------------------------------- 手机号检索（差距分析列为 Bug）

def test_phone_search_hits_both_parties(user_ids):
    """整号搜索必须命中寄件人或收件人——以前输手机号永远是 0 行。"""
    _mk(_u(user_ids, "customer"))
    admin = _u(user_ids, "admin")
    for phone, expect in (("13900000011", 1), ("13900000022", 1), ("13900000099", 0)):
        assert order_service.list_orders(admin, keyword=phone)["total"] == expect, phone


def test_phone_tail_search(user_ids):
    """后 4 位也能搜（尾号摘要），客户记不住的正是完整号码。"""
    _mk(_u(user_ids, "customer"), receiverPhone="13800000077")
    rows = order_service.list_orders(_u(user_ids, "admin"), keyword="0077")
    assert rows["total"] == 1 and rows["list"][0]["receiverPhone"] == "13800000077"


def test_phone_digest_is_deterministic_but_cipher_is_not(user_ids):
    """同号码两次下单 → 摘要相同、密文不同（随机 nonce 正是密文不能 LIKE 的原因）。"""
    a = _mk(_u(user_ids, "customer"))
    b = _mk(_u(user_ids, "customer"))
    assert a.sender_phone_digest == b.sender_phone_digest
    assert a.sender_phone != b.sender_phone
    assert db.session.query(Order).filter_by(sender_phone_digest=a.sender_phone_digest).count() == 2


def test_keyword_covers_name_and_address(user_ids):
    _mk(_u(user_ids, "customer"), receiverName="王五")
    admin = _u(user_ids, "admin")
    assert order_service.list_orders(admin, keyword="王五")["total"] == 1
    assert order_service.list_orders(admin, keyword="龙湾")["total"] == 1
    assert order_service.list_orders(admin, keyword="查无此人")["total"] == 0


# ---------------------------------------------------------------- 多条件筛选

def test_time_range_same_day_inclusive(user_ids):
    """只给日期时右边界必须补到次日零点，否则"今天~今天"查出一张空表。"""
    order = _mk(_u(user_ids, "customer"))
    yesterday = datetime.now() - timedelta(hours=30)
    db.session.execute(db.text("UPDATE orders SET created_at=:t WHERE id=:i"),
                       {"t": yesterday, "i": order.id})
    db.session.commit()
    day = yesterday.strftime("%Y-%m-%d")
    admin = _u(user_ids, "admin")

    assert [r["id"] for r in order_service.list_orders(admin, start=day, end=day)["list"]] == [order.id]
    assert order_service.list_orders(admin, start=datetime.now().strftime("%Y-%m-%d"))["total"] == 0
    # 带时分秒的右边界按字面值比较，不自动补一天
    assert order_service.list_orders(admin, start=day, end=f"{day} 00:00:00")["total"] == 0


def test_filters_combine(user_ids):
    cust = _u(user_ids, "customer")
    _mk(cust, deliveryType=2, orderType=2)
    _mk(cust, goodsType="生鲜")
    admin = _u(user_ids, "admin")
    assert order_service.list_orders(admin, delivery_type=2)["total"] == 1
    assert order_service.list_orders(admin, order_type=2)["total"] == 1
    assert order_service.list_orders(admin, goods_type="生鲜")["total"] == 1
    assert order_service.list_orders(admin, paid=0)["total"] == 2
    assert order_service.list_orders(admin, delivery_type=2, goods_type="生鲜")["total"] == 0


def test_merchant_scope_applies_to_filters(user_ids, second_merchant):
    """筛选不能绕过数据范围：单子改到别的商家名下，本商家员工按任何条件都不该看见它。"""
    order = _mk(_u(user_ids, "customer"))
    staff = _u(user_ids, "merchant")
    assert order_service.list_orders(staff, keyword=order.order_no)["total"] == 1
    order.merchant_id = 2
    db.session.commit()
    assert order_service.list_orders(staff, keyword=order.order_no)["total"] == 0
    assert order_service.list_orders(staff, merchant_id=2)["total"] == 0
    assert order_service.list_orders(_u(user_ids, "admin"), merchant_id=2)["total"] == 1


def test_bad_range_value_is_400_not_500(api):
    r = api.as_("admin").get("/api/orders", query_string={"start": "昨天"}).get_json()
    assert r["code"] == 40000 and "格式" in r["message"]


# ---------------------------------------------------------------- 管理端导出

def test_export_csv_respects_filters_and_scope(api, user_ids, fresh_user, second_merchant):
    order = _mk(_u(user_ids, "customer"))
    other = fresh_user("u_m2_staff", "merchant_admin", user_type=3, merchant_id=2)

    head, rows = _csv(api.as_("admin").get("/api/orders/export"))
    assert head[0] == "订单号" and "收件人电话" in head
    assert len(rows) == 1 and order.order_no in rows[0][0]
    assert "13900000022" in rows[0]          # 导出给的是完整号码，不是 139****0022

    # 带条件导出：筛选和导出口径必须一致（同一个 _filters）
    assert len(_csv(api.as_("admin").get("/api/orders/export",
                                         query_string={"keyword": "13900000011"}))[1]) == 1
    assert len(_csv(api.as_("admin").get("/api/orders/export",
                                         query_string={"deliveryType": "2"}))[1]) == 0
    # 第二家商家的员工：一行都不许带走
    assert len(_csv(api.as_id(other.id).get("/api/orders/export"))[1]) == 0


def test_export_requires_permission(api, user_ids):
    _mk(_u(user_ids, "customer"))
    for who in ("customer", "pilot"):
        r = api.as_(who).get("/api/orders/export")
        assert r.status_code == 403 and "order:export" in r.get_json()["message"]
    assert api.as_("merchant").get("/api/orders/export").status_code == 200


def test_order_read_needs_order_list_for_staff(api, user_ids, fresh_user):
    """员工侧读订单要 order:list；客户仍只认登录态。

    钉住的根因：/orders、/orders/<id>、/orders/filter-options 三条口只挂 @jwt_required，
    又因为在 SELF 白名单里而不进权限矩阵，而 _scope_query 对平台岗位和机长是全量放开的。
    结果是空管/机长只要直接敲 GET /api/orders，就能一次拿走整张客户台账
    （含收发货人手机号与地址）——管控台侧边栏没给他们这一页，只是"没显示"，不是"拿不到"。
    门户那几页必须继续能用：customer 角色一个权限点都没有，所以不能整条挂 @perm_required。
    """
    order = _mk(_u(user_ids, "customer"))
    atc = fresh_user("oq_atc", "air_traffic", user_type=5, real_name="订单读门·空管")

    for who in ("customer", "merchant", "admin"):
        assert api.as_(who).get("/api/orders").get_json()["code"] == 0, who

    for caller, name in ((api.as_("pilot"), "机长"), (api.as_id(atc.id), "空管")):
        for url in (f"/api/orders?page=1&size=5", f"/api/orders/{order.id}",
                    "/api/orders/filter-options"):
            r = caller.get(url)
            body = r.get_json() or {}
            assert r.status_code == 403 and "order:list" in body.get("message", ""), \
                f"{name} 读 {url} 未被拦：{r.status_code} {body.get('message')}"
    # 机长读自己那单也一样拦：拦的是"没有列表权限的人批量读订单"，不是某一条数据
    assert api.as_id(atc.id).get(f"/api/orders/{order.id}").status_code == 403


def test_export_truncated_flag(user_ids):
    cust = _u(user_ids, "customer")
    for _ in range(3):
        _mk(cust)
    admin = _u(user_ids, "admin")
    assert order_service.export_orders(admin, limit=2)["truncated"] is True
    assert order_service.export_orders(admin, limit=10)["truncated"] is False


# ---------------------------------------------------------------- 服务空域准入

def test_out_of_service_area_rejected(user_ids):
    """寄/收点出了商家服务范围 → 试算和下单都必须拒（需求 4.2.2-1）。"""
    base = _pt(1, 0, 0)
    far_lng = base[0] + 1.0                       # 经度 +1° ≈ 100km，明显出界
    r1 = _pt(2, 120, -80)
    with pytest.raises(ApiError) as e:
        pricing_service.estimate(sender_lng=far_lng, sender_lat=base[1],
                                 receiver_lng=r1[0], receiver_lat=r1[1],
                                 weight_kg=Decimal("1.0"))
    assert "服务空域" in e.value.message and "寄件点" in e.value.message

    s = _pt(1, 60, 40)
    with pytest.raises(ApiError) as e:
        _mk(_u(user_ids, "customer"), receiverLng=Decimal(str(round(far_lng, 6))),
             receiverLat=Decimal(str(round(s[1], 6))))
    assert "收件点" in e.value.message


def test_service_area_names_only_does_not_block(legacy_service_area, user_ids):
    """旧形态（只有区域名、没有几何）一律不拦：商家配漏了不能把下单链路堵死。"""
    assert pricing_service.service_area(legacy_service_area)["rings"] == []
    order = _mk(_u(user_ids, "customer"), receiverLng=Decimal("121.900000"),
                receiverLat=Decimal("28.900000"))
    assert float(order.receiver_lng) == pytest.approx(121.9)


def test_inside_service_area_still_blocked_by_no_fly(user_ids):
    """"在服务区里"和"航线合规"是两道独立闸门：服务矩形包住龙湾净空区，穿过去照样拒。"""
    s = _pt(1, 0, 0)
    with pytest.raises(ApiError) as e:
        pricing_service.estimate(sender_lng=s[0], sender_lat=s[1],
                                 receiver_lng=120.855, receiver_lat=27.915,   # 净空区中心
                                 weight_kg=Decimal("1.0"))
    assert "禁飞" in e.value.message


# ---------------------------------------------------------------- M-23 批量导入

def test_template_headers_are_parseable():
    """模板表头和解析器必须对得上——改了列名忘了改解析器，客户第一天用就翻车。"""
    records = order_import.parse_rows(order_import.template_csv() + ",".join(_row()) + "\n")
    assert len(records) == 1 and records[0][1]["senderName"] == "张三"
    assert records[0][0] == 2          # 数据行是文件第 2 行


def test_batch_import_creates_orders(api, user_ids):
    r = _post_batch(api, "customer", _batch_body([_row(), _row(receiverName="王五",
                                                                receiverPhone="13900000033")]))
    assert r["code"] == 0 and (r["data"]["succeeded"], r["data"]["failed"]) == (2, 0)
    nos = [x["orderNo"] for x in r["data"]["rows"]]
    assert db.session.query(Order).filter(Order.order_no.in_(nos)).count() == 2
    made = db.session.query(Order).filter_by(order_no=nos[1]).first()
    assert made.receiver_name == "王五" and made.customer_id == user_ids["customer"]
    assert made.status == 0            # 批量下的单进"待支付"，钱还是要真付


def test_batch_import_reports_per_row_reasons(api, user_ids):
    """一行错不能带走整批：坏行逐条给原因，好行照常成单（客户最怕只有一句"导入失败"）。"""
    body = _batch_body([
        _row(),
        _row(receiverPhone="12340000000"),                     # 手机号段非法
        _row(goodsType="危险品", goodsName="汽油"),             # 禁运品
        _row(weightKg="abc"),                                  # 不是数字
        _row(senderLng="121.900000", senderLat="28.900000"),   # 出服务空域
        _row(receiverName=""),                                 # 少必填
    ])
    rows = _post_batch(api, "customer", body)["data"]["rows"]
    assert [x["ok"] for x in rows] == [True, False, False, False, False, False]
    assert "有效手机号" in rows[1]["reason"]
    assert "禁运" in rows[2]["reason"]
    assert "不是数字" in rows[3]["reason"]
    assert "服务空域" in rows[4]["reason"]
    assert "缺少必填列" in rows[5]["reason"]
    assert [x["line"] for x in rows] == [2, 3, 4, 5, 6, 7]     # 对着 CSV 能找得到是哪一行


def test_batch_import_recovers_after_failed_row(api, user_ids):
    """失败行之后必须还能正常下单：rollback 只带走坏行那一截，不能把 session 弄脏。"""
    body = _batch_body([_row(goodsName="酒精"), _row(goodsName="文件")])
    r = _post_batch(api, "customer", body)
    assert (r["data"]["succeeded"], r["data"]["failed"]) == (1, 1)
    assert db.session.query(Order).count() == 1


def test_batch_dry_run_writes_nothing(api, user_ids):
    body = _batch_body([_row(), _row(receiverName="王五")])
    r = _post_batch(api, "customer", body, dryRun="1")
    assert r["data"]["validateOnly"] is True and r["data"]["succeeded"] == 2
    assert all("orderNo" not in x for x in r["data"]["rows"])
    assert r["data"]["rows"][0]["totalAmount"] > 0
    assert db.session.query(Order).count() == 0


def test_batch_import_rejects_bad_file_and_identity(api, user_ids, second_merchant):
    bad = "姓名,电话\n张三,13900000011\n".encode("utf-8-sig")
    r = _post_batch(api, "customer", bad)
    assert r["code"] == 40000 and "表头无法识别" in r["message"]

    r2 = _post_batch(api, "customer", order_import.template_csv().encode("utf-8"))
    assert "只有表头" in r2["message"]

    assert api.as_("pilot").post("/api/orders/batch").status_code == 403   # 机长没有 order:create
    # 运营岗即便有 order:create（商家管理员有），也不是客户账号 → 服务层必须挡
    r3 = _post_batch(api, "merchant", _batch_body([_row()]))
    assert r3["code"] == 40300 and "只有客户账号" in r3["message"]


def test_batch_import_row_limit(api, user_ids):
    body = _batch_body([_row() for _ in range(order_import.MAX_ROWS + 1)])
    r = _post_batch(api, "customer", body)
    assert r["code"] == 40000 and "最多导入" in r["message"]


def test_template_download_matches_parser(api):
    r = api.as_("customer").get("/api/orders/batch/template")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    text = r.data.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("寄件人姓名*（不超过50字）")
    assert len(text.strip().splitlines()) == 1     # 不带示例数据行，防止原样传上来真下单


# ---------------------------------------------------------------- 强制终止（2.2.2-3-1）

def test_force_terminate_after_dispatch_refunds(api, make_flight, user_ids):
    """进了调度池就没有自助出口了，平台/商家必须有一条终止路径，且退款要留下流水。"""
    order, plan = make_flight("submitted")           # 3 待审批 + 方案待审批
    r = api.as_("admin").post(f"/api/orders/{order.id}/force-cancel",
                              json={"reason": "客户临时撤销"})
    assert r.status_code == 200
    db.session.refresh(order)
    assert order.status == 8 and "强制终止：客户临时撤销" in order.cancel_reason
    assert db.session.get(Order, order.id).status == 8
    from app.models import FlightPlan
    assert db.session.get(FlightPlan, plan.id).status == 6     # 在途方案一起作废
    refund = (db.session.query(PaymentTransaction)
              .filter_by(order_id=order.id, direction=2).first())
    assert refund is not None and float(refund.amount) == float(order.pay_amount)
    n = db.session.execute(db.text(
        "SELECT COUNT(*) FROM message_notification WHERE biz_type='order' AND biz_id=:i "
        "AND user_id=:u"), {"i": order.id, "u": order.customer_id}).scalar()
    assert n == 1                                     # 退了钱得让客户知道


def test_force_terminate_gates(make_flight, user_ids, api, fresh_user, second_merchant):
    order, _ = make_flight("paid")                   # 1 待接单
    with pytest.raises(ApiError) as e:
        order_service.force_terminate_order(order, _u(user_ids, "customer"), "偷终止")
    assert "只有运营方或平台" in e.value.message

    with pytest.raises(ApiError) as e:
        order_service.force_terminate_order(order, _u(user_ids, "admin"), "   ")
    assert "必须填写原因" in e.value.message

    flying, _ = make_flight("flying")
    with pytest.raises(ApiError) as e:
        order_service.force_terminate_order(flying, _u(user_ids, "admin"), "天上叫停")
    assert "飞行中" in e.value.message and "不能强制终止" in e.value.message
    assert flying.status == 5

    stranger = fresh_user("u_m2_owner", "merchant_admin", user_type=3, merchant_id=2)
    with pytest.raises(ApiError) as e:
        order_service.force_terminate_order(order, db.session.get(User, stranger.id), "越界")
    assert "其他商家" in e.value.message
    assert api.as_("pilot").post(f"/api/orders/{order.id}/force-cancel",
                                 json={"reason": "x"}).status_code == 403


def test_force_terminate_unpaid_needs_no_refund(user_ids):
    order = _mk(_u(user_ids, "customer"))            # 0 待支付
    order_service.force_terminate_order(order, _u(user_ids, "merchant"), "重复下单")
    assert order.status == 8
    assert db.session.query(PaymentTransaction).filter_by(order_id=order.id).count() == 0


def test_all_cancel_paths_share_one_refund_shape(make_flight, user_ids):
    """三条作废路径（自助取消 / 商家拒单 / 强制终止）的退款流水口径必须一致。"""
    paid, _ = make_flight("paid")
    order_service.cancel_order(paid, _u(user_ids, "customer"), "不想要了")
    rejected, _ = make_flight("paid")
    order_service.staff_reject_order(rejected, _u(user_ids, "merchant"), "超出范围")
    for o in (paid, rejected):
        t = db.session.query(PaymentTransaction).filter_by(order_id=o.id, direction=2).first()
        assert t is not None and t.status == 4 and float(t.amount) == float(o.pay_amount)
