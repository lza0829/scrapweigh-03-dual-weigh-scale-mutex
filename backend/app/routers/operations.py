from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.db import db, rows_to_list
from app.services.common import TICKET_SELECT, assert_shift_writable, audit, get_setting, require_ticket, ticket_view
from app.services.seat import (
    SupplementIn,
    WEIGH_EVENT_SELECT,
    assert_pin_consistent,
    begin_write,
)

router = APIRouter(tags=["补称与毛皮流水"])


def _resolve_ticket_id(conn, ticket_ref: str | None, body: SupplementIn) -> int:
    if ticket_ref and str(ticket_ref).strip().isdigit():
        return int(ticket_ref)
    if body.磅条编号 is not None:
        return int(body.磅条编号)
    serial_ref = body.磅条号
    if serial_ref:
        row = conn.execute(
            "SELECT id FROM tickets WHERE serial=? OR receipt_no=?",
            (str(serial_ref), str(serial_ref)),
        ).fetchone()
        if row:
            return int(row["id"])
        raise HTTPException(404, "磅条不存在")
    if ticket_ref:
        row = conn.execute("SELECT id FROM tickets WHERE serial=? OR receipt_no=?", (str(ticket_ref), str(ticket_ref))).fetchone()
        if row:
            return int(row["id"])
        raise HTTPException(404, "磅条不存在")
    raise HTTPException(400, "磅条编号必填")


def _kind_is_gross(kind: str | None) -> bool:
    text = (kind or "").strip().lower()
    return text in ("毛", "毛重", "gross", "gross_kg")


def _kind_is_tare(kind: str | None) -> bool:
    text = (kind or "").strip().lower()
    return text in ("皮", "皮重", "tare", "tare_kg")


@router.get("/补称")
def supplement_entries():
    with db() as conn:
        return rows_to_list(conn.execute(
            TICKET_SELECT + " WHERE t.status IN ('待定级','待结价') ORDER BY t.id DESC LIMIT 200"
        ).fetchall())


@router.post("/补称")
@router.post("/补称/{ticket_ref}")
@router.post("/补称入口")
@router.post("/补称入口/{ticket_ref}")
@router.post("/磅条/{ticket_ref}/补称")
@router.post("/过磅/补称/{ticket_ref}")
@router.post("/扣杂")
@router.post("/扣杂/{ticket_ref}")
@router.post("/supplement")
def supplement(ticket_ref: str | None = None, body: SupplementIn | None = None):
    body = body or SupplementIn()
    with db() as conn:
        begin_write(conn)
        ticket_id = _resolve_ticket_id(conn, ticket_ref, body)
        ticket = require_ticket(conn, ticket_id)
        assert_shift_writable(conn, ticket["shift_id"])
        if ticket["status"] not in ("待定级", "待结价"):
            raise HTTPException(409, f"补称挡板关闭：磅条已{ticket['status']}")

        # The supplement barrier reads the same seat/pin projection as admission.
        assert_pin_consistent(conn, ticket["scale_id"], ticket_id)

        gross = float(ticket["gross_kg"])
        tare = float(ticket["tare_kg"])
        deduct = float(ticket["deduct_kg"])

        changed: list[str] = []
        events: list[dict[str, Any]] = []

        if body.扣杂 is not None:
            deduct = float(body.扣杂)
            if deduct < 0:
                raise HTTPException(400, "扣杂不能为负")
            changed.append("扣杂")

        if body.毛重 is not None:
            gross = float(body.毛重)
            changed.append("毛重")
            event_id = _insert_supplement_event(
                conn, ticket, gross, "毛重", "毛重", body.串号
            )
            events.append(event_id)
        if body.皮重 is not None:
            tare = float(body.皮重)
            changed.append("皮重")
            event_id = _insert_supplement_event(
                conn, ticket, tare, "皮重", "皮重", body.串号
            )
            events.append(event_id)

        if body.重量 is not None:
            weight = float(body.重量)
            if weight <= 0:
                raise HTTPException(400, "补称重量须大于零")
            if _kind_is_gross(body.类型):
                gross = weight
                changed.append("毛重")
                kind = "毛重"
            elif _kind_is_tare(body.类型):
                tare = weight
                changed.append("皮重")
                kind = "皮重"
            else:
                raise HTTPException(400, "补称须用类型指明毛重或皮重")
            event_id = _insert_supplement_event(
                conn, ticket, weight, kind, kind, body.串号
            )
            events.append(event_id)

        if not changed:
            raise HTTPException(400, "未提供补称重量或扣杂")
        if gross <= 0:
            raise HTTPException(400, "毛重须大于零")
        if tare < 0:
            raise HTTPException(400, "皮重不能为负")
        if tare >= gross:
            raise HTTPException(400, "皮重须小于毛重")

        try:
            cap = float(get_setting(conn, "毛重软上限", "40000"))
        except ValueError:
            cap = 40000.0
        if gross > cap:
            raise HTTPException(400, "毛重超过软上限")

        net = round(gross - tare - deduct, 3)
        if net <= 0:
            raise HTTPException(400, "扣杂后货重/净重须大于零")

        chain_no = ticket["chain_no"]
        if body.串号 is not None and str(body.串号).strip():
            chain_no = str(body.串号).strip()

        note = body.备注 if body.备注 is not None else ticket["note"]
        conn.execute(
            """
            UPDATE tickets
            SET gross_kg=?, tare_kg=?, deduct_kg=?, net_kg=?, chain_no=?, note=?
            WHERE id=?
            """,
            (gross, tare, deduct, net, chain_no, note, ticket_id),
        )
        audit_id = audit(
            conn,
            "磅条",
            ticket_id,
            "补称",
            f"{ticket['serial']} 更新{'/'.join(changed)}，扣杂后货重={net}",
        )
        view = ticket_view(conn, ticket_id)
        assert view is not None
        view["扣杂后货重"] = net
        view["补称事件编号"] = events
        view["审计编号"] = audit_id
        return view


def _insert_supplement_event(conn, ticket, weight: float, kind: str, label: str, chain_override: str | None) -> int:
    from app.services.seat import record_weight_event

    if weight < 0 or (kind == "毛重" and weight <= 0):
        raise HTTPException(400, f"补称{label}数值无效")
    chain_no = ticket["chain_no"]
    if chain_override is not None and str(chain_override).strip():
        chain_no = str(chain_override).strip()
    return record_weight_event(
        conn,
        ticket_id=ticket["id"],
        site_id=ticket["site_id"],
        scale_id=ticket["scale_id"],
        event_type="补称",
        weight=weight,
        weight_kind=kind,
        chain_no=chain_no,
        source="补称入口",
        note=label,
    )


@router.get("/毛皮事件")
@router.get("/毛皮事件流水")
@router.get("/过磅事件")
def list_weight_events(
    磅条编号: int | None = None,
    ticket_id: int | None = None,
    地磅编号: int | None = None,
    scale_id: int | None = None,
):
    ticket_ref = 磅条编号 if 磅条编号 is not None else ticket_id
    scale_ref = 地磅编号 if 地磅编号 is not None else scale_id
    where = []
    params = []
    if ticket_ref is not None:
        where.append("w.ticket_id=?")
        params.append(ticket_ref)
    if scale_ref is not None:
        where.append("w.scale_id=?")
        params.append(scale_ref)
    sql = WEIGH_EVENT_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY w.id DESC LIMIT 500"
    with db() as conn:
        return rows_to_list(conn.execute(sql, params).fetchall())
