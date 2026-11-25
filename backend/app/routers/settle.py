from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import AliasChoices, BaseModel, Field

from app.db import db
from app.services.common import (
    assert_shift_writable,
    audit,
    now_str,
    release_scale,
    require_ticket,
    ticket_view,
)
from app.services.seat import begin_write, card_view, expire_due_cards, open_ticket_count

router = APIRouter(tags=["结价"])


class TicketRefIn(BaseModel):
    磅条编号: int | None = Field(
        default=None,
        validation_alias=AliasChoices("磅条编号", "ticket_id", "ticketId", "id"),
    )
    磅条号: str | None = Field(
        default=None,
        validation_alias=AliasChoices("磅条号", "serial", "receipt_no"),
    )


def _resolve_ticket_id(conn, ticket_id: int | None, body: TicketRefIn | None) -> int:
    if ticket_id is not None:
        return ticket_id
    body = body or TicketRefIn()
    if body.磅条编号 is not None:
        return int(body.磅条编号)
    if body.磅条号:
        row = conn.execute(
            "SELECT id FROM tickets WHERE serial=? OR receipt_no=?",
            (body.磅条号, body.磅条号),
        ).fetchone()
        if row:
            return int(row["id"])
        raise HTTPException(404, "磅条不存在")
    raise HTTPException(400, "磅条编号必填")


def _settle(conn, ticket_id: int):
    ticket = require_ticket(conn, ticket_id)
    assert_shift_writable(conn, ticket["shift_id"])
    if ticket["status"] != "待结价":
        raise HTTPException(400, "仅待结价可结价")
    if ticket["locked_price"] is None:
        raise HTTPException(400, "尚未锁价")
    expire_due_cards(conn, scale_id=ticket["scale_id"])
    if open_ticket_count(conn, ticket["scale_id"]) > 1:
        raise HTTPException(409, "同秤存在多张未结正式磅条，席位口径未闭，禁止结价")
    amount = round(float(ticket["net_kg"]) * float(ticket["locked_price"]), 2)
    cur = conn.execute(
        """
        UPDATE tickets
        SET status='已结价', amount=?, settled_at=?
        WHERE id=? AND status='待结价'
        """,
        (amount, now_str(), ticket_id),
    )
    if cur.rowcount != 1:
        raise HTTPException(409, "结价未生效：终态已被另一操作改变")
    release_scale(conn, ticket["scale_id"], ticket_id, "结价释放")
    audit_id = audit(conn, "磅条", ticket_id, "结算关账", str(amount))
    view = ticket_view(conn, ticket_id)
    next_card = conn.execute(
        "SELECT id FROM seat_cards WHERE scale_id=? AND status='候席' ORDER BY id LIMIT 1",
        (ticket["scale_id"],),
    ).fetchone()
    if view is not None:
        view["审计编号"] = audit_id
        view["下一张席位牌"] = card_view(conn, next_card["id"]) if next_card else None
        view["next_card"] = view["下一张席位牌"]
        view["席位口径"] = "结价后秤位空闲；候席牌须经补落才生成下一张正式磅条"
    return view


@router.post("/结价/{ticket_id}")
@router.post("/结价入口/{ticket_id}")
@router.post("/settle/{ticket_id}")
def settle(ticket_id: int):
    with db() as conn:
        begin_write(conn)
        return _settle(conn, ticket_id)


@router.post("/结价")
@router.post("/结价入口")
@router.post("/settle")
def settle_by_body(body: TicketRefIn):
    with db() as conn:
        begin_write(conn)
        ticket_id = _resolve_ticket_id(conn, None, body)
        return _settle(conn, ticket_id)
