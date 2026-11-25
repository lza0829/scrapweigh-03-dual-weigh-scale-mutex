from __future__ import annotations
from datetime import date

from app.db import db, init_db
from app.services.common import audit, next_serial, now_str
from app.services.seat import create_direct_card, record_weight_event


def seed_if_empty() -> None:
    init_db()
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM sites").fetchone()["c"]
        if n:
            return
        now = now_str()
        day = date.today().isoformat()
        conn.execute(
            "INSERT INTO sites(name, status, address) VALUES (?, '营业中', ?), (?, '停收', ?)",
            ("城南旧物点", "城南巷口", "河西旧物点", "河西渡口"),
        )
        south = conn.execute("SELECT id FROM sites WHERE name=?", ("城南旧物点",)).fetchone()["id"]
        west = conn.execute("SELECT id FROM sites WHERE name=?", ("河西旧物点",)).fetchone()["id"]
        for site_id, code, status, note in (
            (south, "甲磅", "空闲", ""),
            (south, "乙磅", "空闲", ""),
            (south, "丙磅", "故障", "传感器异常"),
            (west, "甲磅", "空闲", ""),
        ):
            conn.execute(
                "INSERT INTO scales(site_id, code, status, note) VALUES (?, ?, ?, ?)",
                (site_id, code, status, note),
            )
        for cat, name, price in (
            ("废纸", "黄板", 1.35),
            ("废纸", "书刊", 0.90),
            ("废金属", "薄铁", 1.60),
            ("塑料", "白瓶", 2.10),
        ):
            conn.execute(
                "INSERT INTO grades(category, grade_name, unit_price) VALUES (?, ?, ?)",
                (cat, name, price),
            )
        conn.execute(
            "INSERT INTO shifts(business_day, name, status) VALUES (?, '早班', '未落印')",
            (day,),
        )
        shift_id = conn.execute(
            "SELECT id FROM shifts WHERE business_day=? AND name='早班'",
            (day,),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?), (?, ?), (?, ?)",
            ("扣杂默认", "0", "毛重软上限", "40000", "席位牌有效秒数", "1800"),
        )
        scale_id = conn.execute(
            "SELECT id FROM scales WHERE site_id=? AND code='乙磅'",
            (south,),
        ).fetchone()["id"]
        gross, tare, deduct = 1500.0, 920.0, 20.0
        net = round(gross - tare - deduct, 3)
        serial = next_serial(conn)
        cur = conn.execute(
            """
            INSERT INTO tickets(
              serial, site_id, scale_id, shift_id, status,
              gross_kg, tare_kg, deduct_kg, net_kg, note,
              chain_no, receipt_no, created_at
            ) VALUES (?, ?, ?, ?, '待定级', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                serial, south, scale_id, shift_id, gross, tare, deduct, net,
                "种子待定级", "SEED-CHAIN-0001", serial, now,
            ),
        )
        ticket_id = int(cur.lastrowid)
        conn.execute(
            """
            UPDATE scales
            SET status='占用', pinned_ticket_id=?, pinned_at=?
            WHERE id=?
            """,
            (ticket_id, now, scale_id),
        )
        segment_no = f"ZY-SEED-{ticket_id:06d}"
        conn.execute(
            """
            INSERT INTO occupancy_segments(
              segment_no, site_id, scale_id, ticket_id, status,
              source, started_at
            ) VALUES (?, ?, ?, ?, '占用中', '种子正式磅条', ?)
            """,
            (segment_no, south, scale_id, ticket_id, now),
        )
        record_weight_event(
            conn,
            ticket_id=ticket_id,
            site_id=south,
            scale_id=scale_id,
            event_type="毛重",
            weight=gross,
            weight_kind="毛重",
            chain_no="SEED-CHAIN-0001",
            source="种子",
        )
        record_weight_event(
            conn,
            ticket_id=ticket_id,
            site_id=south,
            scale_id=scale_id,
            event_type="皮重",
            weight=tare,
            weight_kind="皮重",
            chain_no="SEED-CHAIN-0001",
            source="种子",
        )
        validated = {
            "site_id": south,
            "scale_id": scale_id,
            "gross": gross,
            "tare": tare,
            "deduct": deduct,
            "net": net,
            "note": "种子待定级",
            "chain_no": "SEED-CHAIN-0001",
            "ttl_override": 1800,
        }
        card_id = create_direct_card(conn, validated, shift_id, ticket_id)
        conn.execute("UPDATE occupancy_segments SET seat_card_id=? WHERE ticket_id=?", (card_id, ticket_id))
        audit(conn, "磅条", ticket_id, "种子初始化", serial)
        audit(conn, "系统", None, "种子初始化", "旧物磅条与席位态势")
