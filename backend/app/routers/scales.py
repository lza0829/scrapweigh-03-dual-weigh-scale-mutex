from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from app.db import db, rows_to_list, row_to_dict
from app.services.common import audit, now_str, release_scale
from app.services.seat import begin_write, open_ticket_count

router = APIRouter(tags=["地磅"])

SCALE_SQL = """
SELECT sc.id AS 编号, sc.id AS id, sc.id AS scale_id,
       sc.code AS 地磅, sc.status AS 状态,
       sc.status AS 钉住灯, sc.status AS 钉住灯状态,
       sc.note AS 备注,
       sc.pinned_ticket_id AS 钉住磅条编号, sc.pinned_at AS 钉住时间,
       pt.serial AS 钉住磅条号,
       st.id AS 回收点编号, st.name AS 回收点, st.status AS 回收点状态
FROM scales sc
JOIN sites st ON st.id = sc.site_id
LEFT JOIN tickets pt ON pt.id = sc.pinned_ticket_id
"""


class ScaleIn(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    回收点编号: int = Field(
        validation_alias=AliasChoices("回收点编号", "site_id", "siteId")
    )
    地磅: str = Field(
        min_length=1,
        validation_alias=AliasChoices("地磅", "地磅号", "code", "scale_code"),
    )
    备注: str = Field(default="", validation_alias=AliasChoices("备注", "note"))


class StatusIn(BaseModel):
    状态: str


class UnpinIn(BaseModel):
    地磅编号: int | None = None
    scale_id: int | None = None
    磅条编号: int | None = None
    ticket_id: int | None = None
    磅条号: str | None = None
    serial: str | None = None
    串号: str | None = None
    chain_no: str | None = None
    原因: str = "过磅串号核对"


def _scale(conn, scale_id: int):
    return row_to_dict(conn.execute(SCALE_SQL + " WHERE sc.id=?", (scale_id,)).fetchone())


@router.get("/地磅")
def list_scales():
    with db() as conn:
        return rows_to_list(conn.execute(SCALE_SQL + " ORDER BY st.id, sc.id").fetchall())


@router.post("/地磅")
def create_scale(body: ScaleIn):
    with db() as conn:
        site = conn.execute("SELECT * FROM sites WHERE id=?", (body.回收点编号,)).fetchone()
        if not site:
            raise HTTPException(404, "回收点不存在")
        if site["status"] == "已注销":
            raise HTTPException(400, "已注销回收点不能新增地磅")
        try:
            conn.execute(
                "INSERT INTO scales(site_id, code, status, note) VALUES (?, ?, '空闲', ?)",
                (body.回收点编号, body.地磅, body.备注),
            )
        except Exception as e:
            raise HTTPException(400, f"创建失败: {e}")
        row = conn.execute(
            "SELECT id FROM scales WHERE site_id=? AND code=?",
            (body.回收点编号, body.地磅),
        ).fetchone()
        audit(conn, "地磅", row["id"], "新建", body.地磅)
        return _scale(conn, row["id"])


@router.post("/地磅/{scale_id}/状态")
def set_scale_status(scale_id: int, body: StatusIn):
    if body.状态 not in ("空闲", "故障"):
        raise HTTPException(400, "只能改为空闲或故障")
    with db() as conn:
        row = conn.execute("SELECT * FROM scales WHERE id=?", (scale_id,)).fetchone()
        if not row:
            raise HTTPException(404, "地磅不存在")
        if row["status"] == "占用":
            raise HTTPException(400, "占用中的地磅不能改状态")
        if body.状态 == "故障" and row["status"] != "空闲":
            raise HTTPException(400, "仅空闲可标故障")
        if body.状态 == "空闲" and row["status"] != "故障":
            raise HTTPException(400, "仅故障可恢复空闲")
        conn.execute("UPDATE scales SET status=? WHERE id=?", (body.状态, scale_id))
        audit(conn, "地磅", scale_id, "改状态", body.状态)
        return _scale(conn, scale_id)


def _unpin_scale(conn, scale_id: int, body: UnpinIn):
    scale = conn.execute("SELECT * FROM scales WHERE id=?", (scale_id,)).fetchone()
    if not scale:
        raise HTTPException(404, "地磅不存在")

    open_count = open_ticket_count(conn, scale_id)
    if open_count:
        raise HTTPException(409, "该秤仍有未结正式磅条，禁止解钉；请先结价或销单")

    ticket_ref = body.磅条编号 if body.磅条编号 is not None else body.ticket_id
    serial_ref = body.磅条号 or body.serial
    chain_ref = body.串号 or body.chain_no
    if ticket_ref is not None or serial_ref or chain_ref:
        target = None
        if ticket_ref is not None:
            target = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_ref,)).fetchone()
        elif serial_ref:
            target = conn.execute("SELECT * FROM tickets WHERE serial=?", (serial_ref,)).fetchone()
        elif chain_ref:
            target = conn.execute("SELECT * FROM tickets WHERE chain_no=? OR serial=?", (chain_ref, chain_ref)).fetchone()
        if target is None:
            raise HTTPException(409, "过磅串号不符：待解钉关联磅条/串号不存在")
        if scale["pinned_ticket_id"] and scale["pinned_ticket_id"] != target["id"]:
            raise HTTPException(409, "过磅串号不符：钉住磅条与提交磅条不同票，拒绝解钉")
        if chain_ref and chain_ref != target["chain_no"] and chain_ref != target["serial"]:
            raise HTTPException(409, "过磅串号不符，拒绝解钉")

    changed = scale["status"] != "空闲" or scale["pinned_ticket_id"] is not None
    release_scale(conn, scale_id, scale["pinned_ticket_id"], f"串号解钉：{body.原因}")
    audit(conn, "地磅", scale_id, "串号解钉", body.原因)
    result = _scale(conn, scale_id)
    result["是否解除钉住"] = changed
    return result


@router.post("/地磅/{scale_id}/解钉")
def unpin_by_path(scale_id: int, body: UnpinIn | None = None):
    body = body or UnpinIn()
    with db() as conn:
        begin_write(conn)
        return _unpin_scale(conn, scale_id, body)


@router.post("/解钉")
@router.post("/串号解钉")
@router.post("/过磅/串号解钉")
@router.post("/过磅/解钉")
def unpin_by_body(body: UnpinIn):
    scale_id = body.地磅编号 if body.地磅编号 is not None else body.scale_id
    with db() as conn:
        begin_write(conn)
        if scale_id is None:
            ticket_ref = body.磅条编号 if body.磅条编号 is not None else body.ticket_id
            serial_ref = body.磅条号 or body.serial
            chain_ref = body.串号 or body.chain_no
            row = None
            if ticket_ref is not None:
                row = conn.execute("SELECT scale_id FROM tickets WHERE id=?", (ticket_ref,)).fetchone()
            elif serial_ref:
                row = conn.execute(
                    "SELECT scale_id FROM tickets WHERE serial=? OR receipt_no=?",
                    (serial_ref, serial_ref),
                ).fetchone()
            elif chain_ref:
                row = conn.execute(
                    "SELECT scale_id FROM tickets WHERE chain_no=? OR serial=? OR receipt_no=?",
                    (chain_ref, chain_ref, chain_ref),
                ).fetchone()
            if row is None:
                raise HTTPException(400, "地磅编号或可定位磅条的磅条号/串号必填")
            scale_id = int(row["scale_id"])
        return _unpin_scale(conn, scale_id, body)
