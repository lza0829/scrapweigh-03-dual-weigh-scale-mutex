from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list, row_to_dict
from app.services.seat import begin_write, expire_due_cards

router = APIRouter(tags=["总览"])

TICKET_STATUS = ("待定级", "待结价", "已结价", "已销")


@router.get("/总览")
def overview():
    with db() as conn:
        begin_write(conn)
        expire_due_cards(conn)
        shift = conn.execute(
            """
            SELECT id AS 编号, business_day AS 营业日, name AS 班次名,
                   status AS 状态, stamped_at AS 落印时间
            FROM shifts ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        counts = {}
        for name in TICKET_STATUS:
            counts[name] = conn.execute(
                "SELECT COUNT(*) AS c FROM tickets WHERE status=?", (name,)
            ).fetchone()["c"]
        scales = rows_to_list(conn.execute(
            """
            SELECT sc.id AS 编号, sc.code AS 地磅, sc.status AS 状态,
                   sc.pinned_ticket_id AS 钉住磅条编号, pt.serial AS 钉住磅条号,
                   st.name AS 回收点, st.status AS 回收点状态
            FROM scales sc
            JOIN sites st ON st.id = sc.site_id
            LEFT JOIN tickets pt ON pt.id = sc.pinned_ticket_id
            ORDER BY st.id, sc.id
            """
        ).fetchall())
        active_segments = rows_to_list(conn.execute(
            """
            SELECT o.id AS 编号, o.segment_no AS 占用段号, o.scale_id AS 地磅编号,
                   sc.code AS 地磅, o.ticket_id AS 磅条编号, t.serial AS 磅条号,
                   o.status AS 状态, o.started_at AS 开始时间
            FROM occupancy_segments o
            JOIN scales sc ON sc.id=o.scale_id
            LEFT JOIN tickets t ON t.id=o.ticket_id
            WHERE o.status='占用中'
            ORDER BY o.id DESC
            """
        ).fetchall())
        waiting_cards = conn.execute(
            "SELECT COUNT(*) AS c FROM seat_cards WHERE status='候席'"
        ).fetchone()["c"]
        return {
            "班次": row_to_dict(shift),
            "分布": counts,
            "地磅": scales,
            "活跃占用段": active_segments,
            "候席席位牌数": waiting_cards,
            "席位口径": "排队席位牌后补落；一秤最多一张未结正式磅条",
        }
