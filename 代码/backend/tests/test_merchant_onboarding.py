"""商家入驻审核（M-82，需求 4.2.1 运营方准入）。

这里钉住的是"审核通过"到底改变了什么，而不只是表单能不能提交：
  · 承接闸门——`pricing_service.get_demo_merchant()` 只认 audit_status=2 且 status=1，
    未通过的商家拿不到订单；通过后也不该把已经在承接的种子商家挤掉（按 id 升序）。
  · 身份转换——通过时把申请人绑到该商家并转成商家身份（user_type=3 + merchant_admin）。
    少这一步，`scope.merchant_scope()` 对这个账号返回 None，他一登录就是全量数据视野。
  · 驳回必须给原因，且原因要通知到申请方；驳回后可以重新提交，但**复用同一行**，
    否则 `uk_credit_code` 会让第二次提交撞 1062 变成 500。
  · 审核权只在平台侧（merchant:audit），商家/客户账号连列表都打不开。
"""
import itertools

import pytest
from sqlalchemy import text

from app.extensions import db
from app.services import pricing_service

_SEQ = itertools.count(1)


def _code() -> str:
    """统一社会信用代码 18 位：用例之间不能重复，否则撞唯一键变成假红。"""
    return f"91PYTESTM82{next(_SEQ):07d}"


@pytest.fixture()
def applicant(fresh_user):
    """临时客户账号当申请人：种子 customer 不能被转成商家，会污染后面所有订单用例。"""
    return fresh_user("m82_applicant", role_code="customer", user_type=1, merchant_id=None)


@pytest.fixture()
def keep_merchants_clean(app_ctx):
    """merchants 不在 conftest 的清场名单里（它是"环境"不是流水），本用例现造的自己带走。

    不带走就会把 unified_credit_code 留在测试库：下一次跑到"信用代码已被占用"那条判据时
    它已经存在，表现为一堆莫名 409 的假红。
    """
    before = {r[0] for r in db.session.execute(text("SELECT id FROM merchants")).all()}
    yield
    new_ids = [r[0] for r in db.session.execute(text("SELECT id FROM merchants")).all()
               if r[0] not in before]
    if new_ids:
        in_list = ",".join(str(int(i)) for i in new_ids)
        # 审核通过会把申请人绑到这家新商家，users.merchant_id 上有外键：先解绑再删企业
        db.session.execute(text(f"UPDATE users SET merchant_id=NULL WHERE merchant_id IN ({in_list})"))
        db.session.execute(text(f"DELETE FROM merchants WHERE id IN ({in_list})"))
        db.session.commit()


def _notify_rows(biz_type="merchant_audit"):
    db.session.expire_all()
    return db.session.execute(text(
        "SELECT user_id,title,content FROM message_notification WHERE biz_type=:b"
        " ORDER BY id"), {"b": biz_type}).mappings().all()


def _apply(api, applicant, name="pytest 低空配送有限公司", code=None, **kw):
    body = {"merchantName": name, "unifiedCreditCode": code or _code(),
            "legalPerson": "张测试", "contactPhone": "13800008888",
            "serviceDistricts": ["瓯江口新区"], "expireAt": "2027-12-31"}
    body.update(kw)
    return api.as_id(applicant.id).post("/api/system/merchants/apply", json=body)


def _row(mid) -> dict:
    db.session.expire_all()
    return db.session.execute(text(
        "SELECT audit_status, audit_opinion, audited_by, contact_user_id, status,"
        " merchant_name, merchant_no, pricing_json, service_area_json, expire_at"
        " FROM merchants WHERE id=:i"), {"i": mid}).mappings().first()


# ------------------------------------------------------------------ 申请
def test_apply_enters_review_and_alerts_platform(api, applicant, user_ids, keep_merchants_clean):
    body = _apply(api, applicant).get_json()
    assert body["code"] == 0, body
    m = body["data"]
    assert m["auditStatus"] == 1 and m["auditStatusName"] == "审核中"
    assert m["merchantNo"].startswith("MH")
    # 非平台身份看不到明文联系电话（需求 10.4 PII）
    assert "*" in m["contactPhone"]
    mine = api.as_id(applicant.id).get("/api/system/merchants/mine").get_json()["data"]
    assert mine["id"] == m["id"] and mine["unifiedCreditCode"] == m["unifiedCreditCode"]

    rows = _notify_rows()
    assert rows and rows[0]["user_id"] == user_ids["admin"], "审核权持有者要收到待审提示"
    assert m["unifiedCreditCode"] in rows[0]["content"]
    # 平台侧审核人才拿到明文，用来回电核验
    as_admin = api.as_("admin").get("/api/system/merchants?auditStatus=1").get_json()["data"]
    assert as_admin["total"] == 1 and "*" not in as_admin["list"][0]["contactPhone"]


def test_apply_validation_and_scope(api, applicant, keep_merchants_clean):
    bad = api.as_id(applicant.id).post("/api/system/merchants/apply", json={
        "merchantName": "缺材料公司", "legalPerson": "李四", "contactPhone": "1380000",
        "unifiedCreditCode": "9133"}).get_json()
    assert bad["code"] == 40000 and "18 位" in bad["message"], bad
    short = _apply(api, applicant, contactPhone="1380000888").get_json()
    assert short["code"] == 40000 and "11 位手机号" in short["message"], short
    nolegal = _apply(api, applicant, legalPerson="  ").get_json()
    assert nolegal["code"] == 40000 and "法人" in nolegal["message"], nolegal

    # 运营方账号不走入驻自助，企业信息在后台维护
    as_staff = api.as_("merchant").post("/api/system/merchants/apply", json={
        "merchantName": "商家自己申请", "unifiedCreditCode": _code(),
        "legalPerson": "王五", "contactPhone": "13800001234"}).get_json()
    assert as_staff["code"] == 40300, as_staff


def test_pending_application_is_unique(api, applicant, keep_merchants_clean):
    assert _apply(api, applicant).get_json()["code"] == 0
    again = _apply(api, applicant, name="换个名字再提一次").get_json()
    assert again["code"] == 40900 and "正在审核中" in again["message"], again


def test_credit_code_cannot_be_reused(api, applicant, keep_merchants_clean):
    """已通过的种子商家信用代码不能被人拿去冒名申请。"""
    taken = db.session.execute(text("SELECT unified_credit_code FROM merchants WHERE id=1")).scalar()
    body = _apply(api, applicant, code=taken).get_json()
    assert body["code"] == 40900 and "已被占用" in body["message"], body


# ------------------------------------------------------------------ 审核
def test_approve_opens_gate_and_converts_identity(api, applicant, keep_merchants_clean):
    mid = _apply(api, applicant).get_json()["data"]["id"]
    r = api.as_("admin").post(f"/api/system/merchants/{mid}/audit",
                              json={"result": 1, "opinion": "资质齐全"})
    assert r.get_json()["code"] == 0, r.get_json()
    row = _row(mid)
    assert row["audit_status"] == 2 and row["audited_by"] == 1

    # 申请人变成该商家的商家管理员：merchant_scope 从此返回本商家 id
    db.session.expire_all()
    from app.models import User
    u = db.session.get(User, applicant.id)
    assert u.merchant_id == mid and u.user_type == 3
    from app.common import scope
    assert scope.merchant_scope(u) == mid
    roles = [x[0] for x in db.session.execute(text(
        "SELECT r.role_code FROM user_role ur JOIN role r ON r.id=ur.role_id"
        " WHERE ur.user_id=:u"), {"u": u.id}).all()]
    assert "merchant_admin" in roles
    told = [x for x in _notify_rows() if x["user_id"] == u.id]
    assert told and "商家管理员" in told[-1]["content"]

    # 承接池里种子商家仍排第一，新通过的商家不会把演示下单的承接方抢走
    assert pricing_service.get_demo_merchant().id == 1
    assert api.as_("admin").get("/api/system/merchants?auditStatus=1").get_json()["data"]["total"] == 0


def test_reject_requires_reason_and_keeps_applicant(api, applicant, keep_merchants_clean):
    mid = _apply(api, applicant, name="pytest 被驳回公司").get_json()["data"]["id"]
    bad = api.as_("admin").post(f"/api/system/merchants/{mid}/audit",
                                json={"result": 2}).get_json()
    assert bad["code"] == 40000 and "必须填写原因" in bad["message"], bad

    ok = api.as_("admin").post(f"/api/system/merchants/{mid}/audit",
                               json={"result": 2, "opinion": "营业执照过期"}).get_json()
    assert ok["code"] == 0, ok
    row = _row(mid)
    assert row["audit_status"] == 3 and row["audit_opinion"] == "营业执照过期"
    db.session.expire_all()
    assert applicant.merchant_id is None and applicant.user_type == 1
    told = [x for x in _notify_rows() if x["user_id"] == applicant.id]
    assert told and "营业执照过期" in told[-1]["content"]
    # 被驳回的商家绝不能进承接池
    assert _row(mid)["audit_status"] != 2


def test_rejected_can_resubmit_reusing_the_row(api, applicant, keep_merchants_clean):
    """驳回后重提必须复用原行：新建会撞 uk_credit_code，而 1062 会变成 500。"""
    code = _code()
    mid = _apply(api, applicant, name="pytest 一次没过", code=code).get_json()["data"]["id"]
    api.as_("admin").post(f"/api/system/merchants/{mid}/audit",
                          json={"result": 2, "opinion": "材料模糊"})
    second = _apply(api, applicant, name="pytest 补齐材料", code=code).get_json()
    assert second["code"] == 0, second
    assert second["data"]["id"] == mid, "重提应该是同一行回到审核中，不是多出一行"
    row = _row(mid)
    assert row["audit_status"] == 1 and row["audit_opinion"] is None
    assert row["merchant_name"] == "pytest 补齐材料"
    assert db.session.execute(text(
        "SELECT COUNT(*) FROM merchants WHERE unified_credit_code=:c"), {"c": code}).scalar() == 1


def test_audit_is_one_shot(api, applicant, keep_merchants_clean):
    mid = _apply(api, applicant, name="pytest 重复审核").get_json()["data"]["id"]
    assert api.as_("admin").post(f"/api/system/merchants/{mid}/audit",
                                json={"result": 1}).get_json()["code"] == 0
    again = api.as_("admin").post(f"/api/system/merchants/{mid}/audit",
                                 json={"result": 1}).get_json()
    assert again["code"] == 40900 and "不能重复审核" in again["message"], again
    assert api.as_("admin").post("/api/system/merchants/999999/audit",
                                json={"result": 1}).get_json()["code"] == 40400


# ------------------------------------------------------------------ 数据范围与自助
def test_list_and_audit_need_merchant_audit_perm(api, applicant, keep_merchants_clean):
    mid = _apply(api, applicant, name="pytest 权限门槛").get_json()["data"]["id"]
    assert api.as_("customer").get("/api/system/merchants").get_json()["code"] == 40300
    assert api.as_("merchant").get("/api/system/merchants").get_json()["code"] == 40300
    assert api.as_("merchant").post(f"/api/system/merchants/{mid}/audit",
                                   json={"result": 1}).get_json()["code"] == 40300
    # 商家自己也不能看别人的入驻队列（列表是全平台口径，只放给有审核权的人）
    assert api.as_("pilot").get("/api/system/merchants").get_json()["code"] == 40300
    rows = api.as_("admin").get("/api/system/merchants?auditStatus=1").get_json()["data"]
    assert rows["total"] == 1 and [r["id"] for r in rows["list"]] == [mid]
    assert api.as_("admin").get("/api/system/merchants?keyword=权限门槛").get_json()["data"]["total"] == 1
    assert api.as_("admin").get("/api/system/merchants?keyword=不存在的公司").get_json()["data"]["total"] == 0


def test_mine_without_application_is_null(api, keep_merchants_clean):
    assert api.as_("customer").get("/api/system/merchants/mine").get_json()["data"] is None
