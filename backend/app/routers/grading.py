from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.db import db
from app.services.common import assert_shift_writable, audit, require_ticket, ticket_view
from app.services.seat import begin_write

router = APIRouter(tags=["定级"])


class GradeLockIn(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    品级编号: int = Field(
        validation_alias=AliasChoices("品级编号", "品级ID", "grade_id", "gradeId", "id")
    )


class GradeRequestIn(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    磅条编号: int | None = Field(
        default=None,
        validation_alias=AliasChoices("磅条编号", "ticket_id", "ticketId"),
    )
    磅条号: str | None = Field(
        default=None,
        validation_alias=AliasChoices("磅条号", "serial", "receipt_no"),
    )
    品级编号: int | None = Field(
        default=None,
        validation_alias=AliasChoices("品级编号", "品级ID", "grade_id", "gradeId", "id"),
    )
    品级: str | None = Field(default=None, validation_alias=AliasChoices("品级", "grade_name"))


def _resolve_ticket_id(conn, ticket_id: int | None, body: GradeRequestIn | GradeLockIn) -> int:
    if ticket_id is not None:
        return ticket_id
    if isinstance(body, GradeRequestIn):
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


def _resolve_grade_id(conn, body: GradeRequestIn | GradeLockIn) -> int:
    grade_id = getattr(body, "品级编号", None)
    if grade_id is not None:
        return int(grade_id)
    grade_name = getattr(body, "品级", None)
    if grade_name:
        row = conn.execute(
            "SELECT id FROM grades WHERE grade_name=? ORDER BY id LIMIT 1",
            (grade_name,),
        ).fetchone()
        if row:
            return int(row["id"])
        raise HTTPException(404, "品级不存在")
    raise HTTPException(400, "品级编号必填")


def _lock_grade(conn, ticket_id: int, grade_id: int):
    ticket = require_ticket(conn, ticket_id)
    assert_shift_writable(conn, ticket["shift_id"])
    if ticket["status"] != "待定级":
        raise HTTPException(400, "仅待定级可锁价")
    grade = conn.execute("SELECT * FROM grades WHERE id=?", (grade_id,)).fetchone()
    if not grade:
        raise HTTPException(404, "品级不存在")
    if grade["unit_price"] <= 0:
        raise HTTPException(400, "单价无效")
    cur = conn.execute(
        """
        UPDATE tickets
        SET status='待结价', grade_id=?, locked_price=?
        WHERE id=? AND status='待定级'
        """,
        (grade["id"], grade["unit_price"], ticket_id),
    )
    if cur.rowcount != 1:
        raise HTTPException(409, "定级锁价未生效：磅条状态已被改变")
    audit(conn, "磅条", ticket_id, "定级锁价", f"{grade['category']}{grade['grade_name']}")
    return ticket_view(conn, ticket_id)


@router.post("/定级/{ticket_id}")
@router.post("/定级入口/{ticket_id}")
def lock_grade(ticket_id: int, body: GradeLockIn):
    with db() as conn:
        begin_write(conn)
        return _lock_grade(conn, ticket_id, _resolve_grade_id(conn, body))


@router.post("/定级")
@router.post("/定级入口")
@router.post("/grading")
def lock_grade_by_body(body: GradeRequestIn):
    with db() as conn:
        begin_write(conn)
        ticket_id = _resolve_ticket_id(conn, None, body)
        grade_id = _resolve_grade_id(conn, body)
        return _lock_grade(conn, ticket_id, grade_id)
