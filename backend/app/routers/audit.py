from __future__ import annotations

from fastapi import APIRouter

from app.db import db, rows_to_list

router = APIRouter(tags=["审计"])


@router.get("/审计")
def list_audit(
    类别: str | None = None,
    kind: str | None = None,
    关联: int | None = None,
    ref_id: int | None = None,
    动作: str | None = None,
    action: str | None = None,
):
    kind_ref = 类别 if 类别 is not None else kind
    ref = 关联 if 关联 is not None else ref_id
    action_ref = 动作 if 动作 is not None else action
    where = []
    params = []
    if kind_ref:
        where.append("kind=?")
        params.append(kind_ref)
    if ref is not None:
        where.append("ref_id=?")
        params.append(ref)
    if action_ref:
        where.append("action=?")
        params.append(action_ref)
    sql = """
        SELECT id AS 编号, kind AS 类别, ref_id AS 关联, action AS 动作,
               created_at AS 时间, note AS 备注
        FROM audit_events
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT 500"
    with db() as conn:
        return rows_to_list(conn.execute(sql, params).fetchall())
