from __future__ import annotations
from fastapi import APIRouter, HTTPException
from app.db import db, rows_to_list
from app.db import TICKET_STATUSES
from app.services.common import TICKET_SELECT

router = APIRouter(tags=["磅条"])


@router.get("/未结正式磅条")
@router.get("/磅条/未结正式")
@router.get("/未结正式磅条册")
def list_open_formal_tickets():
    with db() as conn:
        return rows_to_list(conn.execute(
            TICKET_SELECT
            + " WHERE t.status IN ('待定级','待结价') ORDER BY t.scale_id, t.id DESC LIMIT 500"
        ).fetchall())


@router.get("/磅条")
@router.get("/磅条册")
@router.get("/正式磅条")
@router.get("/正式磅条册")
def list_tickets(状态: str | None = None):
    if 状态 is not None and 状态 not in TICKET_STATUSES:
        raise HTTPException(400, "状态无效")
    with db() as conn:
        if 状态:
            rows = conn.execute(
                TICKET_SELECT + " WHERE t.status=? ORDER BY t.id DESC LIMIT 200",
                (状态,),
            ).fetchall()
        else:
            rows = conn.execute(TICKET_SELECT + " ORDER BY t.id DESC LIMIT 200").fetchall()
        return rows_to_list(rows)
