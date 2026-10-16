"""站内消息通知写入（差距分析：message_notification 过去只有读、没有任何写入）。

约定：所有"应该让用户/运营知道"的业务节点都调 notify()——实名审核结果、工单流转、
违规产生、处罚下达、账单结算等。写入失败不能拖垮主流程，所以用 SAVEPOINT 隔离。

channel：1 站内信 2 短信(Mock)；user_id=0 表示全员广播（DDL 注释）。
"""
from sqlalchemy import text

from app.extensions import db

_INSERT = text("""
    INSERT INTO message_notification (user_id, title, content, biz_type, biz_id,
                                      channel, is_read, created_at)
    VALUES (:u, :t, :c, :bt, :bi, :ch, 0, NOW())""")


def notify(user_id, title, content=None, biz_type=None, biz_id=None, channel=1):
    """给单个用户发一条站内信；user_id 为空/0 视为广播。

    用 SAVEPOINT 隔离：通知写失败只回滚这一段，不能把调用方主事务一起带走。
    """
    if user_id is None:
        user_id = 0
    try:
        with db.session.begin_nested():
            db.session.execute(_INSERT, {"u": int(user_id), "t": str(title)[:120],
                                         "c": (content or "")[:1000] or None,
                                         "bt": biz_type, "bi": biz_id, "ch": channel})
        return True
    except Exception:
        return False


def notify_many(user_ids, title, content=None, biz_type=None, biz_id=None, channel=1):
    """批量通知（同一事件发给多个人，如违规同时通知商家管理员与机长）。"""
    sent = 0
    for uid in {int(x) for x in user_ids if x}:
        sent += 1 if notify(uid, title, content, biz_type, biz_id, channel) else 0
    return sent


def exists(biz_type, user_id, biz_id=None) -> bool:
    """去重判断：同一业务事件已通知过就不再重复轰炸（如每帧都产生的同类违规）。"""
    sql = "SELECT id FROM message_notification WHERE biz_type=:bt AND user_id=:u"
    params = {"bt": biz_type, "u": int(user_id or 0)}
    if biz_id is not None:
        sql += " AND biz_id=:bi"
        params["bi"] = int(biz_id)
    return db.session.execute(text(sql + " LIMIT 1"), params).first() is not None
