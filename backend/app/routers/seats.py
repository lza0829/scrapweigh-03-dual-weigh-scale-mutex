from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.db import db, rows_to_list
from app.services.common import audit, now_str, ticket_view
from app.services.seat import (
    ARCHITECTURE,
    SEGMENT_SELECT,
    active_segment,
    begin_write,
    build_receipt,
    expire_due_cards,
    find_card,
    list_cards,
    promote_card,
    replay_card,
)

router = APIRouter(tags=["席位牌与态势"])


def _scale_ref(地磅编号: int | None, scale_id: int | None) -> int | None:
    return 地磅编号 if 地磅编号 is not None else scale_id


@router.get("/席位牌")
@router.get("/席位牌册")
@router.get("/排队席位牌")
@router.get("/席位")
@router.get("/席位册")
def get_cards(
    状态: str | None = None,
    地磅编号: int | None = None,
    scale_id: int | None = None,
):
    scale_ref = _scale_ref(地磅编号, scale_id)
    with db() as conn:
        begin_write(conn)
        # Ledger reads also settle TTL expiry so a timed-out card cannot remain
        # visually "候席" merely because the sweeper has not been called.
        expire_due_cards(conn, scale_id=scale_ref)
        return list_cards(conn, status=状态, scale_id=scale_ref)


@router.api_route("/席位牌/超时作废", methods=["GET", "POST"])
@router.api_route("/席位牌超时作废", methods=["GET", "POST"])
@router.post("/席位牌/清理超时")
def expire_cards(
    payload: dict[str, Any] | None = Body(default=None),
    地磅编号: int | None = None,
    scale_id: int | None = None,
    席位牌号: str | None = None,
    席位牌编号: int | None = None,
    card_no: str | None = None,
    card_id: int | None = None,
    核验码: str | None = None,
    verify_code: str | None = None,
    ref: str | None = None,
):
    payload = payload or {}
    body_scale = payload.get("地磅编号", payload.get("scale_id"))
    scale_ref = _scale_ref(地磅编号, scale_id)
    if scale_ref is None and body_scale is not None:
        try:
            scale_ref = int(body_scale)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "地磅编号无效") from exc

    card_ref = (
        ref
        or 席位牌号
        or card_no
        or 核验码
        or verify_code
        or 席位牌编号
        or card_id
        or payload.get("ref")
        or payload.get("席位牌号")
        or payload.get("席位牌编号")
        or payload.get("card_no")
        or payload.get("card_id")
        or payload.get("核验码")
        or payload.get("verify_code")
    )
    with db() as conn:
        begin_write(conn)
        if card_ref is not None:
            card = find_card(conn, card_ref)
            if not card:
                raise HTTPException(404, "席位牌不存在")
            if card["状态"] == "候席":
                conn.execute(
                    "UPDATE seat_cards SET expires_at='2000-01-01 00:00:00' WHERE id=? AND status='候席'",
                    (card["编号"],),
                )
            changed = expire_due_cards(conn, scale_id=card["地磅编号"])
            latest = find_card(conn, card_ref)
            if latest is None:
                raise HTTPException(404, "席位牌不存在")
            latest["作废数量"] = len(changed)
            latest["席位牌"] = changed or [latest]
            return latest

        changed = expire_due_cards(conn, scale_id=scale_ref)
        return {"作废数量": len(changed), "席位牌": changed}


@router.get("/席位牌/{card_ref}")
@router.get("/席位牌册/{card_ref}")
def card_detail(card_ref: str):
    with db() as conn:
        begin_write(conn)
        expire_due_cards(conn)
        card = find_card(conn, card_ref)
        if not card:
            raise HTTPException(404, "席位牌不存在")
        return card


@router.api_route("/席位牌/{card_ref}/重放", methods=["GET", "POST"])
@router.api_route("/席位牌册/{card_ref}/重放", methods=["GET", "POST"])
@router.api_route("/排队席位牌/{card_ref}/重放", methods=["GET", "POST"])
@router.api_route("/席位牌/{card_ref}/replay", methods=["GET", "POST"])
@router.get("/席位牌/{card_ref}/结局")
def card_replay(card_ref: str):
    with db() as conn:
        begin_write(conn)
        expire_due_cards(conn)
        return replay_card(conn, card_ref)


@router.post("/席位牌/{card_ref}/补落")
@router.post("/席位牌册/{card_ref}/补落")
@router.post("/排队席位牌/{card_ref}/补落")
@router.post("/席位牌/{card_ref}/promote")
@router.post("/补落/{card_ref}")
def promote(card_ref: str):
    with db() as conn:
        begin_write(conn)
        result = promote_card(conn, card_ref)
        return build_receipt(
            conn,
            ticket_id=result["ticket_id"],
            card_id=result["card_id"],
            audit_id=result["audit_id"],
            idempotent=result["idempotent"],
        )


def _promote_from_payload(payload: dict[str, Any]):
    ref = (
        payload.get("席位牌编号")
        or payload.get("席位牌号")
        or payload.get("card_id")
        or payload.get("card_no")
        or payload.get("verify_code")
        or payload.get("核验码")
    )
    if ref is None:
        raise HTTPException(400, "席位牌编号/席位牌号必填")
    with db() as conn:
        begin_write(conn)
        result = promote_card(conn, ref)
        return build_receipt(
            conn,
            ticket_id=result["ticket_id"],
            card_id=result["card_id"],
            audit_id=result["audit_id"],
            idempotent=result["idempotent"],
        )


@router.post("/过磅/补落")
@router.post("/补落")
@router.post("/席位牌/补落")
@router.post("/席位牌册/补落")
@router.post("/排队席位牌/补落")
@router.post("/席位牌补落")
def promote_by_body(payload: dict[str, Any] = Body(...)):
    return _promote_from_payload(payload)


@router.post("/席位牌/{card_ref}/取消")
@router.post("/席位牌/{card_ref}/作废")
def cancel_card(card_ref: str):
    with db() as conn:
        begin_write(conn)
        card = find_card(conn, card_ref)
        if not card:
            raise HTTPException(404, "席位牌不存在")
        if card["状态"] != "候席":
            raise HTTPException(409, f"席位牌状态 {card['状态']} 不能取消")
        conn.execute(
            """
            UPDATE seat_cards
            SET status='已取消', finished_at=?, outcome='人工取消'
            WHERE id=? AND status='候席'
            """,
            (now_str(), card["编号"]),
        )
        from app.services.seat import add_seat_event

        add_seat_event(conn, card["编号"], card["地磅编号"], "取消", "候席", "已取消")
        audit_id = audit(conn, "席位牌", card["编号"], "取消席位牌", card["席位牌号"])
        return build_receipt(conn, card_id=card["编号"], audit_id=audit_id)


@router.post("/席位牌/{card_ref}/超时作废")
@router.post("/席位牌/{card_ref}/超时")
@router.post("/席位牌/{card_ref}/timeout")
@router.post("/超时作废/{card_ref}")
def expire_one_card(card_ref: str):
    with db() as conn:
        begin_write(conn)
        card = find_card(conn, card_ref)
        if not card:
            raise HTTPException(404, "席位牌不存在")
        if card["状态"] == "候席":
            conn.execute(
                "UPDATE seat_cards SET expires_at='2000-01-01 00:00:00' WHERE id=? AND status='候席'",
                (card["编号"],),
            )
            expire_due_cards(conn, scale_id=card["地磅编号"])
        latest = find_card(conn, card_ref)
        return latest


def _segments(conn, scale_ref: int | None, status: str | None):
    where = []
    params: list[Any] = []
    if scale_ref is not None:
        where.append("o.scale_id=?")
        params.append(scale_ref)
    if status:
        where.append("o.status=?")
        params.append(status)
    sql = SEGMENT_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY o.id DESC LIMIT 500"
    return rows_to_list(conn.execute(sql, params).fetchall())


@router.get("/态势占用段")
@router.get("/回收态势占用段")
@router.get("/回收态势")
@router.get("/占用段")
@router.get("/调度/占用段")
@router.get("/调度/态势占用段")
def occupancy_segments(
    状态: str | None = None,
    地磅编号: int | None = None,
    scale_id: int | None = None,
):
    scale_ref = _scale_ref(地磅编号, scale_id)
    with db() as conn:
        return _segments(conn, scale_ref, 状态)


@router.get("/地磅席位闸")
@router.get("/席位闸")
@router.get("/写闸")
def seat_gate(地磅编号: int | None = None, scale_id: int | None = None):
    scale_ref = _scale_ref(地磅编号, scale_id)
    where = "WHERE sc.id=?" if scale_ref is not None else ""
    params = (scale_ref,) if scale_ref is not None else ()
    with db() as conn:
        begin_write(conn)
        expire_due_cards(conn, scale_id=scale_ref)
        rows = conn.execute(
            f"""
            SELECT sc.id AS 地磅编号,
                   st.name AS 回收点,
                   st.id AS 回收点编号,
                   sc.code AS 地磅,
                   sc.status AS 钉住灯,
                   sc.status AS 钉住灯状态,
                   sc.pinned_ticket_id AS 钉住磅条编号,
                   pt.serial AS 钉住磅条号,
                   sc.pinned_at AS 钉住时间,
                   (
                     SELECT COUNT(*) FROM tickets t
                     WHERE t.scale_id=sc.id AND t.status IN ('待定级','待结价')
                   ) AS 未结正式磅条数,
                   (
                     SELECT COUNT(*) FROM seat_cards c
                     WHERE c.scale_id=sc.id AND c.status='候席'
                   ) AS 候席牌数,
                   o.id AS 活跃占用段编号,
                   o.segment_no AS 活跃占用段号
            FROM scales sc
            JOIN sites st ON st.id=sc.site_id
            LEFT JOIN tickets pt ON pt.id=sc.pinned_ticket_id
            LEFT JOIN occupancy_segments o ON o.scale_id=sc.id AND o.status='占用中'
            {where}
            ORDER BY st.id, sc.id
            """,
            params,
        ).fetchall()
        result = rows_to_list(rows)
        for row in result:
            row["口径"] = ARCHITECTURE
            row["未结正式磅条上限"] = 1
            row["一秤一未结正式"] = row["未结正式磅条数"] <= 1
            row["钉住"] = row["钉住磅条编号"] is not None
            row["活跃占用段"] = active_segment(conn, row["地磅编号"])
        return result


@router.get("/未结正式约束")
@router.get("/约束/未结正式磅条")
@router.get("/约束/一秤一未结正式")
def open_ticket_constraint():
    with db() as conn:
        violations = rows_to_list(conn.execute(
            """
            SELECT scale_id AS 地磅编号, COUNT(*) AS 未结正式磅条数,
                   GROUP_CONCAT(serial) AS 磅条号列表
            FROM tickets
            WHERE status IN ('待定级','待结价')
            GROUP BY scale_id
            HAVING COUNT(*) > 1
            """
        ).fetchall())
        return {
            "口径": ARCHITECTURE,
            "architecture": ARCHITECTURE,
            "每秤未结正式磅条上限": 1,
            "limit": 1,
            "数据库部分唯一索引": "uq_open_ticket_per_scale",
            "index": "uq_open_ticket_per_scale",
            "成立": not violations,
            "ok": not violations,
            "冲突秤": violations,
            "violations": violations,
        }


@router.get("/态势")
@router.get("/调度")
def situation():
    with db() as conn:
        gates = seat_gate()
        active_segments = _segments(conn, None, "占用中")
        waiting = list_cards(conn, status="候席")
        return {
            "口径": ARCHITECTURE,
            "席位闸": gates,
            "活跃占用段": active_segments,
            "候席席位牌": waiting,
            "占用段": _segments(conn, None, None)[:100],
        }


def _audit_flow(conn, ticket_id: int | None = None, card_id: int | None = None):
    clauses = []
    params: list[Any] = []
    if ticket_id is not None:
        clauses.append("(kind='磅条' AND ref_id=?)")
        params.append(ticket_id)
    if card_id is not None:
        clauses.append("(kind='席位牌' AND ref_id=?)")
        params.append(card_id)
    if not clauses:
        return []
    return rows_to_list(conn.execute(
        f"""
        SELECT id AS 编号, kind AS 类别, ref_id AS 关联, action AS 动作,
               created_at AS 时间, note AS 备注
        FROM audit_events
        WHERE {' OR '.join(clauses)}
        ORDER BY id
        """,
        params,
    ).fetchall())


@router.get("/回执/{ref}")
@router.get("/过磅回执/{ref}")
@router.get("/审计回执/{ref}")
def get_receipt(ref: str, 类型: str | None = None):
    with db() as conn:
        begin_write(conn)
        expire_due_cards(conn)
        prefer_card = 类型 in ("席位牌", "card")
        card = find_card(conn, ref)
        ticket = None
        if not prefer_card:
            if ref.isdigit():
                ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (int(ref),)).fetchone()
            if ticket is None:
                ticket = conn.execute(
                    "SELECT * FROM tickets WHERE serial=? OR receipt_no=?",
                    (ref, ref),
                ).fetchone()

        if prefer_card:
            if card is None:
                raise HTTPException(404, "席位牌回执不存在")
            if card.get("磅条编号"):
                result = build_receipt(conn, ticket_id=card["磅条编号"], card_id=card["编号"])
            else:
                result = build_receipt(conn, card_id=card["编号"])
            result["重放"] = replay_card(conn, card["编号"])
            result["审计流"] = _audit_flow(conn, card_id=card["编号"])
            return result

        if ticket is not None and card is not None:
            linked = (
                ticket["seat_card_id"] == card["编号"]
                or card.get("磅条编号") == ticket["id"]
            )
            if not linked:
                ticket_receipt = build_receipt(conn, ticket_id=ticket["id"], card_id=ticket["seat_card_id"])
                card_receipt = (
                    build_receipt(conn, ticket_id=card["磅条编号"], card_id=card["编号"])
                    if card.get("磅条编号")
                    else build_receipt(conn, card_id=card["编号"])
                )
                return {
                    "回执类型": "身份歧义复合回执",
                    "提示": "数字编号同时命中不同的磅条与席位牌；请使用磅条号/席位牌号或类型参数",
                    "ref": ref,
                    "磅条回执": ticket_receipt,
                    "席位牌回执": card_receipt,
                    "磅条": ticket_receipt,
                    "席位牌": card_receipt.get("席位牌"),
                    "审计流": _audit_flow(conn, ticket_id=ticket["id"], card_id=card["编号"]),
                }

        if ticket is not None:
            card_id = ticket["seat_card_id"]
            result = build_receipt(conn, ticket_id=ticket["id"], card_id=card_id)
            result["审计流"] = _audit_flow(conn, ticket_id=ticket["id"], card_id=card_id)
            return result

        if card is not None:
            if card.get("磅条编号"):
                result = build_receipt(conn, ticket_id=card["磅条编号"], card_id=card["编号"])
            else:
                result = build_receipt(conn, card_id=card["编号"])
            result["重放"] = replay_card(conn, card["编号"])
            result["审计流"] = _audit_flow(conn, card_id=card["编号"])
            return result

        raise HTTPException(404, "回执不存在")


@router.get("/席位牌回执/{card_ref}")
def get_seat_receipt(card_ref: str):
    return get_receipt(card_ref, 类型="席位牌")


@router.get("/重放/{card_ref}")
@router.get("/结局/{card_ref}")
def replay_alias(card_ref: str):
    return card_replay(card_ref)


@router.get("/席位牌结局")
def outcome_query(席位牌号: str | None = None, 席位牌编号: int | None = None):
    ref = 席位牌号 if 席位牌号 is not None else 席位牌编号
    if ref is None:
        raise HTTPException(400, "席位牌号或席位牌编号必填")
    return card_replay(str(ref))


@router.get("/对照")
@router.get("/定位面")
@router.get("/对照定位面")
def cross_check(地磅编号: int):
    with db() as conn:
        scale = conn.execute("SELECT * FROM scales WHERE id=?", (地磅编号,)).fetchone()
        if not scale:
            raise HTTPException(404, "地磅不存在")
        open_ticket_rows = conn.execute(
            """
            SELECT id
            FROM tickets
            WHERE scale_id=? AND status IN ('待定级','待结价')
            ORDER BY id
            """,
            (地磅编号,),
        ).fetchall()
        open_tickets = []
        for row in open_ticket_rows:
            ticket = ticket_view(conn, int(row["id"]))
            if ticket is not None:
                open_tickets.append(ticket)
        cards = list_cards(conn, scale_id=地磅编号)
        segments = _segments(conn, 地磅编号, None)
        events = rows_to_list(conn.execute(
            """
            SELECT id AS 编号, event_no AS 事件号, ticket_id AS 磅条编号,
                   event_type AS 事件类型, weight_kind AS 重量类别,
                   weight_kg AS 重量, chain_no AS 串号, created_at AS 时间
            FROM weigh_events
            WHERE scale_id=? ORDER BY id
            """,
            (地磅编号,),
        ).fetchall())
        audits = rows_to_list(conn.execute(
            """
            SELECT id AS 编号, kind AS 类别, ref_id AS 关联, action AS 动作,
                   created_at AS 时间, note AS 备注
            FROM audit_events
            WHERE note LIKE ? OR ref_id IN (
                SELECT id FROM seat_cards WHERE scale_id=?
            ) OR ref_id IN (
                SELECT id FROM tickets WHERE scale_id=?
            )
            ORDER BY id DESC LIMIT 300
            """,
            (f"%{scale['code']}%", 地磅编号, 地磅编号),
        ).fetchall())
        active_segments = [s for s in segments if s["状态"] == "占用中"]
        pin_ticket_id = scale["pinned_ticket_id"]
        invariant = {
            "一秤一未结正式": len(open_tickets) <= 1,
            "活跃占用段至多一条": len(active_segments) <= 1,
            "有正式票则有钉住": (not open_tickets) or (
                scale["status"] == "占用"
                and pin_ticket_id == open_tickets[0]["编号"]
            ),
            "无正式票则解钉": bool(open_tickets) or (
                scale["status"] in ("空闲", "故障") and pin_ticket_id is None
            ),
            "活跃段与正式票同票": (not open_tickets and not active_segments) or (
                len(open_tickets) == 1
                and len(active_segments) == 1
                and active_segments[0]["磅条编号"] == open_tickets[0]["编号"]
            ),
        }
        return {
            "地磅编号": 地磅编号,
            "地磅": dict(scale),
            "钉住磅条编号": pin_ticket_id,
            "口径": ARCHITECTURE,
            "architecture": ARCHITECTURE,
            "不变量": invariant,
            "invariants": invariant,
            "闭合": all(invariant.values()),
            "closed": all(invariant.values()),
            "未结正式磅条": open_tickets,
            "正式磅条": open_tickets,
            "磅条册": open_tickets,
            "席位牌": cards,
            "席位牌册": cards,
            "占用段": segments,
            "态势占用段": segments,
            "毛皮事件": events,
            "毛皮事件流水": events,
            "审计": audits,
            "审计流": audits,
        }

