from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.db import db, rows_to_list
from app.services.common import TICKET_SELECT, audit, current_shift
from app.services.seat import (
    WeighIn,
    begin_write,
    build_receipt,
    create_direct_card,
    create_formal_ticket,
    expire_due_cards,
    fault_enabled,
    find_idempotent_card,
    issue_waiting_card,
    open_ticket_count,
    validate_weigh,
)

router = APIRouter(tags=["过磅"])


@router.get("/过磅")
def list_open_weighs():
    with db() as conn:
        return rows_to_list(conn.execute(
            TICKET_SELECT + " WHERE t.status IN ('待定级','待结价') ORDER BY t.id DESC LIMIT 100"
        ).fetchall())


@router.post("/过磅")
@router.post("/过磅入口")
@router.post("/weigh")
def admit(body: WeighIn):
    with db() as conn:
        # The write gate is acquired before the seat read so two concurrent
        # intents on one scale are ordered instead of both observing an empty seat.
        begin_write(conn)
        validated = validate_weigh(conn, body)
        scale_id = validated["scale_id"]

        expire_due_cards(conn, scale_id=scale_id)
        existing = find_idempotent_card(conn, validated.get("idempotency_key"))
        if existing is not None:
            if existing["地磅编号"] != scale_id:
                raise HTTPException(409, "幂等键已用于另一地磅，不能重复提交")
            return build_receipt(
                conn,
                ticket_id=existing.get("磅条编号"),
                card_id=existing["编号"],
                idempotent=True,
            )
        open_count = open_ticket_count(conn, scale_id)
        if open_count > 1:
            raise HTTPException(409, "同秤存在多张未结正式磅条，未结正式约束已破坏")

        if open_count == 1:
            shift = current_shift(conn)
            card_id = issue_waiting_card(conn, validated, shift)
            audit_id = audit(conn, "席位牌", card_id, "领取席位牌", f"地磅{scale_id}候席")
            audit(conn, "过磅", None, "后到领牌", f"地磅{scale_id}席位牌={card_id}，未落正式行")
            if fault_enabled(body.模拟半截失败):
                raise HTTPException(status_code=500, detail="模拟领牌半截失败：事务应整体回滚")
            return build_receipt(conn, card_id=card_id, audit_id=audit_id)

        scale = conn.execute("SELECT status, pinned_ticket_id FROM scales WHERE id=?", (scale_id,)).fetchone()
        if scale["status"] == "占用" or scale["pinned_ticket_id"] is not None:
            raise HTTPException(409, "秤位钉住但无未结正式磅条；请先核对过磅串号并解钉")

        shift = current_shift(conn)
        ticket_id, serial, _segment_id = create_formal_ticket(conn, validated, shift["id"])
        card_id = create_direct_card(conn, validated, shift["id"], ticket_id)
        audit_id = audit(conn, "磅条", ticket_id, "过磅入站", serial)
        audit(conn, "席位牌", card_id, "席位牌直落", f"{serial} 空秤单席")
        if fault_enabled(body.模拟半截失败):
            raise HTTPException(status_code=500, detail="模拟落条半截失败：事务应整体回滚")
        return build_receipt(conn, ticket_id=ticket_id, card_id=card_id, audit_id=audit_id)
