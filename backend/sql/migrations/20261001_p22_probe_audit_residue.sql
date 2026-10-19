-- p22：收回 2026-09-29 权限探针写在演示库资质上的审核痕迹
-- 探针当时连的是演示库（tianshu_utms），对 qualification id=1 调过一次审核，
-- 于是台账里那句审核意见成了"探针复核"——这是给客户看的业务内容，不该留测试黑话。
-- 该行的 status/expire_at 都是造数脚本本来铺好的三态样例（1 有效 / 2 即将到期 / 3 已过期），
-- 只有这三列被探针写过，所以只把这三列清回 NULL，与 id=2、id=3 的写法一致。
-- 幂等：按探针那句精确值匹配，第二次跑就是 0 行；重建种子库时这三列本就是 NULL，也是 0 行。
UPDATE qualification
   SET audited_by = NULL, audited_at = NULL, audit_opinion = NULL
 WHERE id = 1 AND audit_opinion = '探针复核';
