"""资质「上传 / 新建」这口子开了以后，复审必须还在原地。

需求 4.2.3 要"资质文件上传与审核"，而这张表过去只有一个写入口（复审），
所以真正的风险不是"没有上传口"，而是**插入即有效**：DDL 里 status 默认 1，
一旦开新建而不定入口态，审核岗就形同虚设——页面上多一个按钮，台账里少一道闸。
这一组用例钉的是四条口径：

  · 新建一律落 0 待审核，包括 resource:qualification 自己登记的那些；
  · 两条入口（管理侧代登记 / 本人自助上传）都不越出台账权限半步：
    自助口主体写死成本人，客户账号仍然进不来，机长也读不到别人的台账与原件；
  · 证照原件存在 static 外面，台账侧只有跟着那份放行名单 + 同一份商家归属才拿得到字节；
    本人另有一条自助读口（主体条件写在 WHERE 里），越界读到的是 404 不是 403；
  · 主体必须真实存在、不能跨商家、同主体同类别不许堆两件在办的证。

qualification 不在 conftest 的清场名单里（它是台账），所以本文件的夹具自己造、自己收。
"""
import io
import os
import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy import bindparam, text

from app.extensions import db

URL_QUAL = "/api/resource/qualifications"
URL_MINE = "/api/resource/qualifications/mine"
FILE_TPL = "/api/resource/qualifications/{}/file"
FILE_MINE_TPL = "/api/resource/qualifications/mine/{}/file"

PDF_BYTES = b"%PDF-1.4\n%tianshu qualification test attachment\n"
KEY_RE = re.compile(r"^qualification/\d{6}/[0-9a-f]{32}\.pdf$")


def _q(stmt, **p):
    return db.session.execute(text(stmt), p)


def _row(qid: int) -> dict:
    db.session.expire_all()
    return dict(_q("SELECT * FROM qualification WHERE id=:i", i=qid).mappings().first())


def _file(name="驾驶证.pdf", data=PDF_BYTES):
    """multipart 的一个文件部件（Werkzeug 测试客户端认 (BytesIO, 文件名) 这种写法）。"""
    return {"file": (io.BytesIO(data), name)}


def _body(**kw):
    """表单字段：默认给一张"还有 60 天到期"的驾驶证。"""
    d = {"qualType": "driver", "qualName": "UQU 无人机驾驶员执照",
         "issueAt": "2024-01-01",
         "expireAt": (datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d")}
    d.update(kw)
    return {k: v for k, v in d.items() if v is not None}


@pytest.fixture()
def made(app_ctx):
    """自己造的资质自己收回去（它不在清场名单里，留下就会被下一条用例读到）。"""
    ids: list[int] = []

    def keep(qid: int) -> int:
        ids.append(int(qid))
        return int(qid)

    yield keep
    if ids:
        db.session.execute(text("DELETE FROM qualification WHERE id IN :ids")
                           .bindparams(bindparam("ids", expanding=True)), {"ids": ids})
        db.session.commit()


# ------------------------------------------------------------------
# 1. 新建的入口态：只能是待审核
# ------------------------------------------------------------------
def test_management_create_lands_pending(api, made, user_ids):
    """管理侧代登记：附件落对地方、库里停在 0、台账里读出来是"待审核"。"""
    resp = api.as_("admin").post(URL_QUAL, data=dict(
        _body(subjectType=2, subjectId=user_ids["pilot"]), **_file()))
    assert resp.get_json().get("code") == 0, resp.get_json()
    qid = made(resp.get_json()["data"]["id"])

    row = _row(qid)
    assert row["status"] == 0, f"新建的资质不是待审核：{row['status']}"
    assert KEY_RE.match(row["file_url"] or ""), f"file_url 不是服务端生成的相对键：{row['file_url']}"

    from flask import current_app
    on_disk = os.path.join(current_app.config["UPLOAD_ROOT"], row["file_url"].replace("/", os.sep))
    assert os.path.isfile(on_disk), "库里记了键，磁盘上却没有件"

    listed = api.as_("admin").get(URL_QUAL).get_json()["data"]["list"]
    hit = next(x for x in listed if x["id"] == qid)
    assert hit["statusName"] == "待审核", hit
    assert hit["fileUrl"] == row["file_url"], "台账没把原件的键带出来，前端就没有下载入口"


def test_reviewer_cannot_self_validate_on_create(api, made, user_ids):
    """审核岗自己登记也算"待审核"：不存在谁建谁有效的路径。"""
    qid = made(api.as_("admin").post(URL_QUAL, data=dict(
        _body(subjectType=2, subjectId=user_ids["pilot"]), **_file())).get_json()["data"]["id"])
    assert _row(qid)["status"] == 0

    ok = api.as_("admin").post(f"/api/resource/qualifications/{qid}/audit", json={"result": 1})
    assert ok.get_json().get("code") == 0, ok.get_json()
    assert _row(qid)["status"] == 1, "复审通过之后才该变成有效"


@pytest.mark.parametrize("name,data,why", [
    ("钓鱼页.html", b"<script>alert(1)</script>", "同源可读的上传件等于给平台投 XSS"),
    ("伪装.svg", b"<svg onload=alert(1)>", "svg 里能带脚本"),
    ("可执行.exe", b"MZ\x90\x00", "证照不可能是可执行文件"),
    ("没有扩展名", b"whatever", "扩展名是唯一可判定的口径"),
])
def test_bad_file_type_is_rejected_without_writing(api, user_ids, name, data, why):
    """白名单外的附件：400 拒掉，而且库里一行不许有（先校验、后落盘）。"""
    before = int(_q("SELECT COUNT(*) FROM qualification").scalar())
    resp = api.as_("admin").post(URL_QUAL, data=dict(
        _body(subjectType=2, subjectId=user_ids["pilot"]), **_file(name=name, data=data)))
    body = resp.get_json()
    assert resp.status_code == 400 and body["code"] == 40000, body
    assert "PDF" in body["message"], body
    assert int(_q("SELECT COUNT(*) FROM qualification").scalar()) == before, why


def test_file_is_mandatory(api, user_ids):
    """没原件就别登记：复审要看原件，不看一句话。"""
    body = api.as_("admin").post(URL_QUAL, data=_body(subjectType=2,
                                                     subjectId=user_ids["pilot"])).get_json()
    assert body["code"] == 40000 and "原件" in body["message"], body


def test_expire_at_required_and_order_checked(api, user_ids):
    """到期日是预警扫描的依据：必填，且不能早于签发日。"""
    miss = api.as_("admin").post(URL_QUAL, data=dict(
        _body(subjectType=2, subjectId=user_ids["pilot"], expireAt=None), **_file())).get_json()
    assert miss["code"] == 40000 and "到期日期" in miss["message"], miss

    bad = api.as_("admin").post(URL_QUAL, data=dict(
        _body(subjectType=2, subjectId=user_ids["pilot"], issueAt="2026-08-01",
              expireAt="2025-01-01"), **_file())).get_json()
    assert bad["code"] == 40000 and "晚于签发" in bad["message"], bad

    ugly = api.as_("admin").post(URL_QUAL, data=dict(
        _body(subjectType=2, subjectId=user_ids["pilot"], expireAt="下个月"), **_file())).get_json()
    assert ugly["code"] == 40000 and "YYYY-MM-DD" in ugly["message"], ugly


def test_unknown_qual_type_or_subject_rejected(api, user_ids):
    """类别只认 DDL 注释里那五种，subjectType/subjectId 不能瞎填。"""
    t = api.as_("admin").post(URL_QUAL, data=dict(
        _body(qualType="随便写的类别", subjectType=2, subjectId=user_ids["pilot"]),
        **_file())).get_json()
    assert t["code"] == 40000 and "资质类别" in t["message"], t

    s = api.as_("admin").post(URL_QUAL, data=dict(
        _body(subjectType=7, subjectId=user_ids["pilot"]), **_file())).get_json()
    assert s["code"] == 40000 and "subjectType" in s["message"], s

    n = api.as_("admin").post(URL_QUAL, data=dict(
        _body(subjectType="abc", subjectId=user_ids["pilot"]), **_file())).get_json()
    assert n["code"] == 40000 and "数字" in n["message"], n


def test_qual_type_must_match_subject_type(api, made, user_ids):
    """类别与主体要对得上：执照是公司的、适航证是飞机的、驾驶证是人的。

    这条约束只在"新建口打开之后"才需要——之前这张表没有写入口，类别和主体各填各的；
    现在不拦，台账第一天就会出现"某机长名下一本营业执照"。
    """
    def payload(qtype: str, qname: str):
        return dict(_body(qualType=qtype, qualName=qname,
                          subjectType=2, subjectId=user_ids["pilot"]), **_file())

    bad = api.as_("admin").post(URL_QUAL, data=payload("license", "UQU 商家营业执照")).get_json()
    assert bad["code"] == 40000 and "商家" in bad["message"], bad
    assert _q("SELECT COUNT(*) FROM qualification WHERE qual_type='license'").scalar() == 0

    # 自助口同理：主体写死成本人（2），递个适航证上去一样拦得住
    wrong = api.as_("pilot").post(URL_MINE, data=dict(
        _body(qualType="airworthiness"), **_file())).get_json()
    assert wrong["code"] == 40000 and "飞行器" in wrong["message"], wrong

    ok = made(api.as_("pilot").post(URL_MINE,
                   data=payload("training", "UQU 驾驶员复训记录")).get_json()["data"]["id"])
    assert _row(ok)["qual_type"] == "training"


def test_duplicate_open_record_conflicts_then_reopens_after_reject(api, made, user_ids):
    """同主体同类别只留一件在办；驳回之后本人可以重新递。"""
    # 每次都要现造一份：BytesIO 被上一个请求读完后就关掉了，复用 payload 会 "closed file"
    def payload():
        return dict(_body(subjectType=2, subjectId=user_ids["pilot"]), **_file())

    first = made(api.as_("admin").post(URL_QUAL, data=payload()).get_json()["data"]["id"])

    again = api.as_("admin").post(URL_QUAL, data=payload()).get_json()
    assert again["code"] == 40900 and str(first) in again["message"], again

    api.as_("admin").post(f"/api/resource/qualifications/{first}/audit",
                          json={"result": 4, "opinion": "照片模糊"})
    third = api.as_("admin").post(URL_QUAL, data=payload()).get_json()
    assert third.get("code") == 0, f"驳回之后仍然递不进去：{third}"
    made(third["data"]["id"])


def test_pending_row_is_not_swept_by_expiry_scan(api, made, user_ids):
    """时间推进不许覆盖审核流程：待审核的证即使到期日期已过，也还停在 0。"""
    qid = made(api.as_("admin").post(URL_QUAL, data=dict(
        _body(subjectType=2, subjectId=user_ids["pilot"],
              expireAt=(datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")),
        **_file())).get_json()["data"]["id"])
    from app.services import scheduler
    scheduler.scan_qualifications()
    assert _row(qid)["status"] == 0, "扫描把待审核改成了已过期，复审就看不到这件了"


# ------------------------------------------------------------------
# 2. 主体与数据范围
# ------------------------------------------------------------------
def test_subject_must_exist_and_merchant_scope_holds(api, made, fresh_user, second_merchant):
    """多态外键要靠代码把关：主体不存在给 404，跨主体登记给 403。"""
    ghost = api.as_("admin").post(URL_QUAL, data=dict(
        _body(subjectType=2, subjectId=999999), **_file())).get_json()
    assert ghost["code"] == 40400 and "不存在" in ghost["message"], ghost

    other = fresh_user("u_qu_other", "pilot", user_type=3, merchant_id=second_merchant)
    cross = api.as_("merchant").post(URL_QUAL, data=dict(
        _body(subjectType=2, subjectId=other.id), **_file())).get_json()
    assert cross["code"] == 40300 and "本商家" in cross["message"], cross

    # 平台侧没有商家归属，它给谁登记都行（这是它的活），但主体必须是真的
    ok = api.as_("admin").post(URL_QUAL, data=dict(
        _body(subjectType=2, subjectId=other.id), **_file())).get_json()
    assert ok.get("code") == 0, ok
    made(ok["data"]["id"])


def test_flight_vehicle_subject_resolves_owner(api, made):
    """飞行器主体（适航证）：归属按 aircraft.merchant_id 判，商家管理员只管自家的。"""
    ac = int(_q("SELECT id FROM aircraft WHERE deleted=0 AND merchant_id=1 LIMIT 1").scalar())
    qid = made(api.as_("merchant").post(URL_QUAL, data=dict(
        _body(qualType="airworthiness", subjectType=3, subjectId=ac,
              qualName="UQU 适航证"), **_file())).get_json()["data"]["id"])
    assert _row(qid)["subject_type"] == 3
    listed = api.as_("merchant").get(URL_QUAL).get_json()["data"]["list"]
    assert any(x["id"] == qid for x in listed), "本商家飞行器的资质在本商家台账里读不到"


# ------------------------------------------------------------------
# 3. 本人自助上传：不需要任何 resource:*
# ------------------------------------------------------------------
def test_self_upload_fixes_subject_to_owner(api, made, user_ids):
    """机长自助递证：主体只能是他自己，前端塞别人的 id 也没用。"""
    resp = api.as_("pilot").post(URL_MINE, data=dict(_body(), **_file()))
    assert resp.get_json().get("code") == 0, resp.get_json()
    qid = made(resp.get_json()["data"]["id"])
    row = _row(qid)
    assert (row["subject_type"], row["subject_id"]) == (2, user_ids["pilot"]), row
    assert row["status"] == 0

    mine = api.as_("pilot").get("/api/resource/my-duty").get_json()["data"]["qualifications"]
    assert next(x for x in mine if x["id"] == qid)["statusName"] == "待审核"


def test_self_upload_notifies_reviewers(api, made, user_ids):
    """递上去要有人接着办：本商家审核岗 + 平台侧审核岗都收到，提交人自己不算。"""
    qid = made(api.as_("pilot").post(URL_MINE, data=dict(_body(), **_file()))
               .get_json()["data"]["id"])
    got = {int(r["u"]) for r in _q(
        "SELECT user_id AS u FROM message_notification WHERE biz_type='qualification'"
        " AND biz_id=:i", i=qid).mappings().all()}
    assert {user_ids["admin"], user_ids["merchant"]} <= got, got
    assert user_ids["pilot"] not in got, "给自己发一条待办纯属刷屏"


def test_customer_cannot_self_upload(anon, api):
    """客户（user_type 1/2）没有"人员资质"这个概念；未登录必须 401。"""
    body = api.as_("customer").post(URL_MINE, data=dict(_body(), **_file())).get_json()
    assert body["code"] == 40300 and "仅运营侧" in body["message"], body
    assert anon.post(URL_MINE, data=_body()).status_code == 401


def test_self_upload_does_not_grant_ledger_access(api, made):
    """自助口只给"交自己的证"：机长仍然读不到别人的台账，也不能借台账那条口拿原件。"""
    qid = made(api.as_("pilot").post(URL_MINE, data=dict(_body(), **_file()))
               .get_json()["data"]["id"])
    assert api.as_("pilot").get(URL_QUAL).get_json()["code"] == 40300
    assert api.as_("pilot").get(FILE_TPL.format(qid)).status_code == 403, \
        "机长没有任何 resource:* 却能走台账口读原件 = 台账权限被变相发出去了"


# ------------------------------------------------------------------
# 3.1 本人读原件：自助口只认"这一条是不是我的"
# ------------------------------------------------------------------
def test_owner_reads_own_file_without_any_ledger_perm(api, made):
    """交证的人要能看到自己交上去的那张：走 mine 口，不需要任何 resource:* 权限点。"""
    qid = made(api.as_("pilot").post(URL_MINE, data=dict(_body(), **_file()))
               .get_json()["data"]["id"])
    resp = api.as_("pilot").get(FILE_MINE_TPL.format(qid))
    assert resp.status_code == 200, resp.get_json()
    assert resp.data == PDF_BYTES
    assert resp.mimetype == "application/pdf"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_self_file_read_cannot_reach_another_subject(api, anon, made, user_ids):
    """别人的原件从自助口拿不到，而且报 404：403 等于告诉试探者"这条 id 真的存在"。

    有台账权限的人也不能借这条口读非本人主体（那是台账口的活，归那边判）。
    """
    qid = made(api.as_("admin").post(URL_QUAL, data=dict(
        _body(subjectType=2, subjectId=user_ids["pilot"]), **_file())).get_json()["data"]["id"])

    for who in ("pilot02", "admin"):
        resp = api.as_(who).get(FILE_MINE_TPL.format(qid))
        assert resp.status_code == 404 and resp.get_json()["code"] == 40400, (who, resp.get_json())
    assert api.as_("pilot").get(FILE_MINE_TPL.format(qid)).status_code == 200
    assert anon.get(FILE_MINE_TPL.format(qid)).status_code == 401


def test_self_file_read_404s_on_row_without_attachment(api, made):
    """主体是自己的、但这一条没有存档原件：还是那句可读的 404，不是 500。"""
    pid = int(_q("SELECT id FROM users WHERE username='pilot'").scalar())
    qid = int(_q("INSERT INTO qualification (subject_type, subject_id, qual_type, qual_name,"
                 " expire_at, status, created_at, updated_at, deleted)"
                 " VALUES (2, :s, 'driver', 'UQU 自助口历史行', NOW(), 1, NOW(), NOW(), 0)",
                 s=pid).lastrowid)
    made(qid)
    resp = api.as_("pilot").get(FILE_MINE_TPL.format(qid))
    assert resp.status_code == 404 and "没有存档的证照原件" in resp.get_json()["message"], resp.get_json()


# ------------------------------------------------------------------
# 4. 原件读取：与台账同一份名单、同一份范围
# ------------------------------------------------------------------
def test_schedule_only_role_can_read_the_file(api, made, fresh_user):
    """只有排班权限的运维：进得来这一页就读得到原件，否则就成了"列得出证却点不开"。"""
    ops = fresh_user("u_qu_sched", "ops_maintainer", user_type=6, merchant_id=None)
    pid = int(_q("SELECT id FROM users WHERE username='pilot'").scalar())
    qid = made(api.as_("admin").post(URL_QUAL, data=dict(
        _body(subjectType=2, subjectId=pid), **_file())).get_json()["data"]["id"])

    assert api.as_id(ops.id).get(URL_QUAL).get_json()["code"] == 0
    resp = api.as_id(ops.id).get(FILE_TPL.format(qid))
    assert resp.status_code == 200, resp.get_json()
    assert resp.data == PDF_BYTES
    assert resp.mimetype == "application/pdf"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_file_read_keeps_merchant_scope(api, made, fresh_user, second_merchant):
    """跨商家的原件拿不走：放行名单管"能不能读这一页"，归属管"能读哪一张"。"""
    other = fresh_user("u_qu_far", "pilot", user_type=3, merchant_id=second_merchant)
    qid = made(api.as_("admin").post(URL_QUAL, data=dict(
        _body(subjectType=2, subjectId=other.id), **_file())).get_json()["data"]["id"])

    resp = api.as_("merchant").get(FILE_TPL.format(qid))
    assert resp.status_code == 403 and resp.get_json()["code"] == 40300, resp.get_json()
    assert "本商家" in resp.get_json()["message"]
    assert api.as_("admin").get(FILE_TPL.format(qid)).status_code == 200


def test_legacy_row_without_file_gives_readable_404(api, made):
    """历史行 file_url 为空不是错误，但也不能把任意字符串当路径往外发。"""
    pid = int(_q("SELECT id FROM users WHERE username='pilot'").scalar())
    qid = int(_q("INSERT INTO qualification (subject_type, subject_id, qual_type, qual_name,"
                 " expire_at, status, created_at, updated_at, deleted)"
                 " VALUES (2, :s, 'training', 'UQU 无原件历史行', NOW(), 1, NOW(), NOW(), 0)",
                 s=pid).lastrowid)
    made(qid)
    resp = api.as_("admin").get(FILE_TPL.format(qid))
    assert resp.status_code == 404 and resp.get_json()["code"] == 40400, resp.get_json()

    _q("UPDATE qualification SET file_url='../../app/config.py' WHERE id=:i", i=qid)
    evil = api.as_("admin").get(FILE_TPL.format(qid))
    assert evil.status_code == 404, f"库里塞个相对路径就被带出去了：{evil.get_json()}"


def test_missing_record_is_404_not_500(api):
    resp = api.as_("admin").get(FILE_TPL.format(999999))
    assert resp.status_code == 404 and resp.get_json()["code"] == 40400, resp.get_json()


# ------------------------------------------------------------------
# 5. 请求体上限
# ------------------------------------------------------------------
def test_oversize_upload_gets_friendly_413(api, user_ids):
    """超限由 Werkzeug 在视图之前抛出：信封还是那个信封，但提示要能看懂。"""
    from flask import current_app
    over = int(current_app.config["MAX_CONTENT_LENGTH"]) + 1024 * 1024
    resp = api.as_("admin").post(URL_QUAL, data=dict(
        _body(subjectType=2, subjectId=user_ids["pilot"]),
        **_file(data=b"x" * over)))
    body = resp.get_json()
    assert resp.status_code == 413, f"{resp.status_code} {body}"
    assert body["code"] == 40000 and "10MB" in body["message"], body
