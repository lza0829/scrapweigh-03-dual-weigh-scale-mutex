from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.services.common import audit, current_shift, get_setting, next_serial, now_str, ticket_view

OPEN_STATUSES_SQL = "('待定级', '待结价')"
ARCHITECTURE = "席位牌排队后补落：一秤最多一张未结正式磅条，后到领牌，空秤后按牌补落"


class WeighIn(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    地磅编号: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("地磅编号", "地磅ID", "秤编号", "磅编号", "scale_id", "scaleId"),
    )
    回收点编号: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("回收点编号", "回收点ID", "站点编号", "site_id", "siteId"),
    )
    毛重: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices(
            "毛重", "毛重kg", "gross_kg", "grossKg", "grossWeight", "gross_weight", "gross"
        ),
    )
    皮重: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices(
            "皮重", "皮重kg", "tare_kg", "tareKg", "tareWeight", "tare_weight", "tare"
        ),
    )
    扣杂: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices(
            "扣杂", "扣杂kg", "deduct_kg", "deductKg", "deductWeight", "deduct_weight", "deduct"
        ),
    )
    备注: str = Field(
        default="",
        validation_alias=AliasChoices("备注", "note", "remark"),
    )
    串号: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("串号", "过磅串号", "chain_no", "serial_no"),
    )
    席位有效秒数: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("席位有效秒数", "超时秒数", "ttl_seconds", "timeout_seconds"),
    )
    幂等键: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("幂等键", "idempotency_key", "request_id"),
    )
    模拟半截失败: Any = Field(
        default=False,
        validation_alias=AliasChoices(
            "模拟半截失败", "半截失败", "故障注入", "force_half_failure", "inject_failure"
        ),
    )


class SupplementIn(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    磅条编号: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("磅条编号", "磅条ID", "ticket_id", "ticketId", "id"),
    )
    磅条号: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("磅条号", "磅条序列号", "serial", "serial_no", "receipt_no"),
    )
    类型: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("类型", "称重类型", "重量类型", "weight_kind", "weightKind", "kind"),
    )
    重量: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("重量", "称重值", "weight_kg", "weightKg", "weight"),
    )
    毛重: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("毛重", "gross_kg", "grossKg", "grossWeight", "gross"),
    )
    皮重: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("皮重", "tare_kg", "tareKg", "tareWeight", "tare"),
    )
    扣杂: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("扣杂", "deduct_kg", "deductKg", "deduct"),
    )
    备注: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("备注", "note"),
    )
    串号: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("串号", "过磅串号", "chain_no"),
    )


CARD_SELECT = """
SELECT c.id AS 编号,
       c.card_no AS 席位牌号,
       c.verify_code AS 核验码,
       c.site_id AS 回收点编号,
       st.name AS 回收点,
       c.scale_id AS 地磅编号,
       sc.code AS 地磅,
       c.shift_id AS 班次编号,
       sh.name AS 班次,
       c.kind AS 牌类型,
       c.queue_no AS 队列序号,
       c.status AS 状态,
       c.ticket_id AS 磅条编号,
       t.serial AS 磅条号,
       c.ttl_seconds AS 有效秒数,
       c.issued_at AS 领取时间,
       c.expires_at AS 超时时间,
       c.activated_at AS 激活时间,
       c.finished_at AS 结局时间,
       c.outcome AS 结局,
       c.fail_reason AS 失败原因,
       c.request_json AS 请求快照JSON
FROM seat_cards c
LEFT JOIN sites st ON st.id = c.site_id
LEFT JOIN scales sc ON sc.id = c.scale_id
LEFT JOIN shifts sh ON sh.id = c.shift_id
LEFT JOIN tickets t ON t.id = c.ticket_id
"""

SEGMENT_SELECT = """
SELECT o.id AS 编号,
       o.segment_no AS 占用段号,
       o.site_id AS 回收点编号,
       st.name AS 回收点,
       o.scale_id AS 地磅编号,
       sc.code AS 地磅,
       o.ticket_id AS 磅条编号,
       t.serial AS 磅条号,
       o.seat_card_id AS 席位牌编号,
       c.card_no AS 席位牌号,
       o.status AS 状态,
       o.source AS 来源,
       o.started_at AS 开始时间,
       o.ended_at AS 结束时间,
       o.end_reason AS 结束原因
FROM occupancy_segments o
LEFT JOIN sites st ON st.id = o.site_id
LEFT JOIN scales sc ON sc.id = o.scale_id
LEFT JOIN tickets t ON t.id = o.ticket_id
LEFT JOIN seat_cards c ON c.id = o.seat_card_id
"""

EVENT_SELECT = """
SELECT e.id AS 编号, e.card_id AS 席位牌编号, e.scale_id AS 地磅编号,
       e.seq AS 序号, e.event_type AS 事件, e.from_status AS 原状态,
       e.to_status AS 新状态, e.ticket_id AS 磅条编号, e.note AS 备注,
       e.payload_json AS 载荷JSON, e.created_at AS 时间
FROM seat_events e
"""

WEIGH_EVENT_SELECT = """
SELECT w.id AS 编号, w.event_no AS 事件号, w.ticket_id AS 磅条编号,
       t.serial AS 磅条号, w.site_id AS 回收点编号, st.name AS 回收点,
       w.scale_id AS 地磅编号, sc.code AS 地磅, w.event_type AS 事件类型,
       w.weight_kind AS 重量类别, w.weight_kg AS 重量, w.chain_no AS 串号,
       w.source AS 来源, w.note AS 备注, w.created_at AS 时间
FROM weigh_events w
LEFT JOIN tickets t ON t.id = w.ticket_id
LEFT JOIN sites st ON st.id = w.site_id
LEFT JOIN scales sc ON sc.id = w.scale_id
"""


def begin_write(conn) -> None:
    """Acquire the SQLite write gate before reading mutable seat state."""
    conn.execute("BEGIN IMMEDIATE")


def fault_enabled(value: Any) -> bool:
    """Interpret permissive black-box fault-injection payloads."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text not in ("", "0", "false", "no", "off", "none", "正常", "关闭", "cancel")


def open_ticket_count(conn, scale_id: int) -> int:
    return int(
        conn.execute(
            f"SELECT COUNT(*) AS c FROM tickets WHERE scale_id=? AND status IN {OPEN_STATUSES_SQL}",
            (scale_id,),
        ).fetchone()["c"]
    )


def active_segment(conn, scale_id: int) -> dict | None:
    row = conn.execute(
        SEGMENT_SELECT + " WHERE o.scale_id=? AND o.status='占用中' ORDER BY o.id DESC LIMIT 1",
        (scale_id,),
    ).fetchone()
    return _segment_dict(row)


def _segment_dict(row) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    data["id"] = data.get("编号")
    data["segment_id"] = data.get("编号")
    data["占用段编号"] = data.get("编号")
    data["调度占用段号"] = data.get("占用段号")
    data["segment_no"] = data.get("占用段号")
    data["态势状态"] = data.get("状态")
    data["status"] = data.get("状态")
    return data


def segment_view(conn, segment_id: int) -> dict | None:
    row = conn.execute(SEGMENT_SELECT + " WHERE o.id=?", (segment_id,)).fetchone()
    return _segment_dict(row)


def _card_dict(row) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    raw = data.pop("请求快照JSON", "{}") or "{}"
    try:
        data["请求快照"] = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        data["请求快照"] = {}
    # Compatibility aliases for different ledger wording.
    data["id"] = data.get("编号")
    data["card_id"] = data.get("编号")
    data["席位牌编号"] = data.get("编号")
    data["卡号"] = data.get("席位牌号")
    data["card_no"] = data.get("席位牌号")
    data["cardNo"] = data.get("席位牌号")
    data["排队序号"] = data.get("队列序号")
    data["队列号"] = data.get("队列序号")
    data["queue_no"] = data.get("队列序号")
    data["过期时间"] = data.get("超时时间")
    data["expires_at"] = data.get("超时时间")
    data["核验码"] = data.get("核验码")
    data["token"] = data.get("核验码")
    data["verify_code"] = data.get("核验码")
    data["verifyCode"] = data.get("核验码")
    data["status"] = data.get("状态")
    return data


def card_view(conn, card_id: int) -> dict | None:
    row = conn.execute(CARD_SELECT + " WHERE c.id=?", (card_id,)).fetchone()
    return _card_dict(row)


def find_card_row(conn, ref: Any):
    if ref is None:
        return None
    text = str(ref).strip()
    if text.isdigit():
        row = conn.execute(CARD_SELECT + " WHERE c.id=?", (int(text),)).fetchone()
        if row:
            return row
    return conn.execute(
        CARD_SELECT + " WHERE c.card_no=? OR c.verify_code=?",
        (text, text),
    ).fetchone()


def find_card(conn, ref: Any) -> dict | None:
    return _card_dict(find_card_row(conn, ref))


def find_idempotent_card(conn, idempotency_key: str | None) -> dict | None:
    key = (idempotency_key or "").strip()
    if not key:
        return None
    row = conn.execute(
        CARD_SELECT + " WHERE c.idempotency_key=? ORDER BY c.id LIMIT 1",
        (key,),
    ).fetchone()
    return _card_dict(row)


def list_cards(conn, status: str | None = None, scale_id: int | None = None) -> list[dict]:
    where = []
    params: list[Any] = []
    if status:
        where.append("c.status=?")
        params.append(status)
    if scale_id is not None:
        where.append("c.scale_id=?")
        params.append(scale_id)
    sql = CARD_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.id DESC LIMIT 500"
    return [_card_dict(r) for r in conn.execute(sql, params).fetchall()]


def next_card_no(conn) -> str:
    day = datetime.now().strftime("%Y%m%d")
    prefix = f"XP{day}"
    row = conn.execute(
        "SELECT card_no FROM seat_cards WHERE card_no LIKE ? ORDER BY card_no DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    n = 1
    if row:
        try:
            n = int(row["card_no"][-6:]) + 1
        except (ValueError, IndexError):
            n = 1
    return f"{prefix}{n:06d}"


def next_event_no(conn) -> str:
    day = datetime.now().strftime("%Y%m%d")
    prefix = f"MP{day}"
    row = conn.execute(
        "SELECT event_no FROM weigh_events WHERE event_no LIKE ? ORDER BY event_no DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    n = 1
    if row:
        try:
            n = int(row["event_no"][-6:]) + 1
        except (ValueError, IndexError):
            n = 1
    return f"{prefix}{n:06d}"


def resolve_ttl(conn, override: int | None) -> int:
    if override is not None:
        try:
            ttl = int(override)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "席位有效秒数必须是整数") from exc
        if ttl < 0:
            raise HTTPException(400, "席位有效秒数不能为负")
        return ttl
    raw = get_setting(conn, "席位牌有效秒数", "1800")
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return 1800


def add_seat_event(
    conn,
    card_id: int,
    scale_id: int,
    event_type: str,
    from_status: str = "",
    to_status: str = "",
    ticket_id: int | None = None,
    note: str = "",
    payload: dict | None = None,
) -> int:
    seq_row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM seat_events WHERE card_id=?",
        (card_id,),
    ).fetchone()
    seq = int(seq_row["seq"])
    cur = conn.execute(
        """
        INSERT INTO seat_events(
          card_id, scale_id, seq, event_type, from_status, to_status,
          ticket_id, note, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            card_id,
            scale_id,
            seq,
            event_type,
            from_status,
            to_status,
            ticket_id,
            note,
            json.dumps(payload or {}, ensure_ascii=False),
            now_str(),
        ),
    )
    return int(cur.lastrowid)


def expire_due_cards(conn, scale_id: int | None = None) -> list[dict]:
    """Expire due waiting cards in the caller's transaction."""
    current = now_str()
    sql = "SELECT * FROM seat_cards WHERE status='候席' AND expires_at <= ?"
    params: list[Any] = [current]
    if scale_id is not None:
        sql += " AND scale_id=?"
        params.append(scale_id)
    sql += " ORDER BY id"
    due = conn.execute(sql, params).fetchall()
    changed: list[dict] = []
    for row in due:
        conn.execute(
            """
            UPDATE seat_cards
            SET status='已超时作废', finished_at=?, outcome='超时作废', fail_reason=?
            WHERE id=? AND status='候席'
            """,
            (current, f"席位牌已于 {row['expires_at']} 超时", row["id"]),
        )
        add_seat_event(
            conn,
            row["id"],
            row["scale_id"],
            "超时作废",
            "候席",
            "已超时作废",
            note=row["card_no"],
        )
        audit(conn, "席位牌", row["id"], "席位超时作废", row["card_no"])
        view = card_view(conn, row["id"])
        if view:
            changed.append(view)
    return changed


def validate_weigh(conn, body: WeighIn) -> dict:
    scale_id = body.地磅编号
    if scale_id is None:
        raise HTTPException(400, "地磅编号必填")
    scale = conn.execute("SELECT * FROM scales WHERE id=?", (scale_id,)).fetchone()
    if not scale:
        raise HTTPException(404, "地磅不存在")

    site_id = body.回收点编号 if body.回收点编号 is not None else scale["site_id"]
    site = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    if not site:
        raise HTTPException(400, "回收点不存在")
    if site_id != scale["site_id"]:
        raise HTTPException(400, "地磅不属于该回收点")
    if site["status"] != "营业中":
        raise HTTPException(400, f"回收点{site['status']}，不能过磅")
    if scale["status"] == "故障":
        raise HTTPException(400, "地磅故障，不能过磅")

    gross = body.毛重
    if gross is None:
        raise HTTPException(400, "毛重必填")
    try:
        gross = float(gross)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "毛重必须是数字") from exc
    if gross <= 0:
        raise HTTPException(400, "毛重须大于零")

    tare = 0.0 if body.皮重 is None else float(body.皮重)
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

    if body.扣杂 is None:
        try:
            deduct = float(get_setting(conn, "扣杂默认", "0"))
        except ValueError:
            deduct = 0.0
    else:
        deduct = float(body.扣杂)
    if deduct < 0:
        raise HTTPException(400, "扣杂不能为负")

    net = round(gross - tare - deduct, 3)
    if net <= 0:
        raise HTTPException(400, "净重须大于零")

    chain_no = "" if body.串号 is None else str(body.串号).strip()
    return {
        "site_id": site["id"],
        "scale_id": scale["id"],
        "gross": gross,
        "tare": tare,
        "deduct": deduct,
        "net": net,
        "note": body.备注 or "",
        "chain_no": chain_no,
        "ttl_override": body.席位有效秒数,
        "idempotency_key": body.幂等键,
    }


def record_weight_event(
    conn,
    *,
    ticket_id: int | None,
    site_id: int,
    scale_id: int,
    event_type: str,
    weight: float,
    weight_kind: str = "",
    chain_no: str = "",
    source: str = "过磅入口",
    note: str = "",
) -> int:
    event_no = next_event_no(conn)
    cur = conn.execute(
        """
        INSERT INTO weigh_events(
          event_no, ticket_id, site_id, scale_id, event_type, weight_kind,
          weight_kg, chain_no, source, note, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_no,
            ticket_id,
            site_id,
            scale_id,
            event_type,
            weight_kind,
            float(weight),
            chain_no,
            source,
            note,
            now_str(),
        ),
    )
    return int(cur.lastrowid)


def create_formal_ticket(
    conn,
    validated: dict,
    shift_id: int,
    *,
    seat_card_id: int | None = None,
    source: str = "过磅入口",
) -> tuple[int, str, int]:
    now = now_str()
    serial = next_serial(conn)
    cur = conn.execute(
        """
        INSERT INTO tickets(
          serial, site_id, scale_id, shift_id, status,
          gross_kg, tare_kg, deduct_kg, net_kg, note,
          chain_no, receipt_no, seat_card_id, created_at
        ) VALUES (?, ?, ?, ?, '待定级', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            serial,
            validated["site_id"],
            validated["scale_id"],
            shift_id,
            validated["gross"],
            validated["tare"],
            validated["deduct"],
            validated["net"],
            validated.get("note", ""),
            validated.get("chain_no", ""),
            serial,
            seat_card_id,
            now,
        ),
    )
    ticket_id = int(cur.lastrowid)

    record_weight_event(
        conn,
        ticket_id=ticket_id,
        site_id=validated["site_id"],
        scale_id=validated["scale_id"],
        event_type="毛重",
        weight=validated["gross"],
        weight_kind="毛重",
        chain_no=validated.get("chain_no", ""),
        source=source,
    )
    record_weight_event(
        conn,
        ticket_id=ticket_id,
        site_id=validated["site_id"],
        scale_id=validated["scale_id"],
        event_type="皮重",
        weight=validated["tare"],
        weight_kind="皮重",
        chain_no=validated.get("chain_no", ""),
        source=source,
    )

    day = datetime.now().strftime("%Y%m%d")
    segment_no = f"ZY{day}{ticket_id:06d}"
    seg_cur = conn.execute(
        """
        INSERT INTO occupancy_segments(
          segment_no, site_id, scale_id, ticket_id, seat_card_id,
          status, source, started_at
        ) VALUES (?, ?, ?, ?, ?, '占用中', ?, ?)
        """,
        (
            segment_no,
            validated["site_id"],
            validated["scale_id"],
            ticket_id,
            seat_card_id,
            "席位牌补落" if seat_card_id else "空秤直落",
            now,
        ),
    )
    segment_id = int(seg_cur.lastrowid)

    conn.execute(
        """
        UPDATE scales
        SET status='占用', pinned_ticket_id=?, pinned_at=?
        WHERE id=?
        """,
        (ticket_id, now, validated["scale_id"]),
    )
    return ticket_id, serial, segment_id


def create_direct_card(conn, validated: dict, shift_id: int, ticket_id: int) -> int:
    card_no = next_card_no(conn)
    verify_code = secrets.token_hex(6)
    ttl = resolve_ttl(conn, validated.get("ttl_override"))
    now_dt = datetime.now()
    now = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    expires = (now_dt + timedelta(seconds=ttl)).strftime("%Y-%m-%d %H:%M:%S")
    queue_no = int(
        conn.execute(
            "SELECT COALESCE(MAX(queue_no), 0) + 1 AS q FROM seat_cards WHERE scale_id=?",
            (validated["scale_id"],),
        ).fetchone()["q"]
    )
    payload = {
        "回收点编号": validated["site_id"],
        "地磅编号": validated["scale_id"],
        "毛重": validated["gross"],
        "皮重": validated["tare"],
        "扣杂": validated["deduct"],
        "备注": validated.get("note", ""),
        "串号": validated.get("chain_no", ""),
    }
    cur = conn.execute(
        """
        INSERT INTO seat_cards(
          card_no, verify_code, site_id, scale_id, shift_id, kind, queue_no,
          status, request_json, ticket_id, ttl_seconds, issued_at, expires_at,
          activated_at, finished_at, outcome, created_at
        ) VALUES (?, ?, ?, ?, ?, '单席直落牌', ?, '已落条', ?, ?, ?, ?, ?, ?, ?, '空秤单席正式落条', ?)
        """,
        (
            card_no,
            verify_code,
            validated["site_id"],
            validated["scale_id"],
            shift_id,
            queue_no,
            json.dumps(payload, ensure_ascii=False),
            ticket_id,
            ttl,
            now,
            expires,
            now,
            now,
            now,
        ),
    )
    card_id = int(cur.lastrowid)
    idem_key = (validated.get("idempotency_key") or "").strip()
    if idem_key:
        conn.execute("UPDATE seat_cards SET idempotency_key=? WHERE id=?", (idem_key, card_id))
    add_seat_event(conn, card_id, validated["scale_id"], "直落", "", "已落条", ticket_id, card_no)
    conn.execute("UPDATE tickets SET seat_card_id=? WHERE id=?", (card_id, ticket_id))
    conn.execute("UPDATE occupancy_segments SET seat_card_id=? WHERE ticket_id=? AND status='占用中'", (card_id, ticket_id))
    return card_id


def issue_waiting_card(conn, validated: dict, shift: Any) -> int:
    card_no = next_card_no(conn)
    verify_code = secrets.token_hex(6)
    ttl = resolve_ttl(conn, validated.get("ttl_override"))
    now_dt = datetime.now()
    now = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    expires = (now_dt + timedelta(seconds=ttl)).strftime("%Y-%m-%d %H:%M:%S")
    queue_no = int(
        conn.execute(
            """
            SELECT COALESCE(MAX(queue_no), 0) + 1 AS q
            FROM seat_cards
            WHERE scale_id=? AND kind='排队席位牌'
            """,
            (validated["scale_id"],),
        ).fetchone()["q"]
    )
    payload = {
        "回收点编号": validated["site_id"],
        "地磅编号": validated["scale_id"],
        "毛重": validated["gross"],
        "皮重": validated["tare"],
        "扣杂": validated["deduct"],
        "备注": validated.get("note", ""),
        "串号": validated.get("chain_no", ""),
        "席位有效秒数": validated.get("ttl_override"),
    }
    cur = conn.execute(
        """
        INSERT INTO seat_cards(
          card_no, verify_code, site_id, scale_id, shift_id, kind, queue_no,
          status, request_json, ttl_seconds, issued_at, expires_at, created_at
        ) VALUES (?, ?, ?, ?, ?, '排队席位牌', ?, '候席', ?, ?, ?, ?, ?)
        """,
        (
            card_no,
            verify_code,
            validated["site_id"],
            validated["scale_id"],
            shift["id"] if shift is not None else None,
            queue_no,
            json.dumps(payload, ensure_ascii=False),
            ttl,
            now,
            expires,
            now,
        ),
    )
    card_id = int(cur.lastrowid)
    idem_key = (validated.get("idempotency_key") or "").strip()
    if idem_key:
        conn.execute("UPDATE seat_cards SET idempotency_key=? WHERE id=?", (idem_key, card_id))
    add_seat_event(conn, card_id, validated["scale_id"], "领取", "", "候席", note=card_no)
    return card_id


def assert_pin_consistent(conn, scale_id: int, ticket_id: int) -> None:
    scale = conn.execute("SELECT * FROM scales WHERE id=?", (scale_id,)).fetchone()
    if not scale:
        raise HTTPException(404, "地磅不存在")
    if scale["status"] != "占用":
        raise HTTPException(409, "补称挡板关闭：地磅未处于钉住占用状态")
    if scale["pinned_ticket_id"] != ticket_id:
        raise HTTPException(409, "补称挡板关闭：地磅钉住磅条与当前磅条不同票，疑似过磅串号")
    segment = conn.execute(
        "SELECT id FROM occupancy_segments WHERE scale_id=? AND ticket_id=? AND status='占用中'",
        (scale_id, ticket_id),
    ).fetchone()
    if not segment:
        raise HTTPException(409, "补称挡板关闭：缺少同票态势占用段")


def promote_card(conn, card_ref: Any) -> dict:
    view_row = find_card_row(conn, card_ref)
    if not view_row:
        raise HTTPException(404, "席位牌不存在")
    card_id = int(view_row["编号"])
    row = conn.execute("SELECT * FROM seat_cards WHERE id=?", (card_id,)).fetchone()
    scale_id = int(row["scale_id"])

    expire_due_cards(conn, scale_id=scale_id)
    row = conn.execute("SELECT * FROM seat_cards WHERE id=?", (card_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "席位牌不存在")

    if row["status"] in ("已补落", "已落条") and row["ticket_id"]:
        return {
            "ticket_id": int(row["ticket_id"]),
            "card_id": card_id,
            "audit_id": None,
            "idempotent": True,
        }
    if row["status"] == "已超时作废":
        raise HTTPException(409, "席位牌已超时作废，不能补落")
    if row["status"] == "已取消":
        raise HTTPException(409, "席位牌已取消，不能补落")
    if row["status"] == "已拒绝":
        raise HTTPException(409, "席位牌已拒绝，不能补落")
    if row["status"] != "候席":
        raise HTTPException(409, f"席位牌状态 {row['status']} 不能补落")

    earlier = conn.execute(
        """
        SELECT id, card_no FROM seat_cards
        WHERE scale_id=? AND status='候席' AND id<?
        ORDER BY id LIMIT 1
        """,
        (scale_id, card_id),
    ).fetchone()
    if earlier:
        raise HTTPException(409, f"请按席位顺序补落，前序席位牌 {earlier['card_no']} 尚未结局")

    count = open_ticket_count(conn, scale_id)
    if count > 1:
        raise HTTPException(409, "同秤存在多张未结正式磅条，未结正式约束已破坏，禁止补落")
    if count == 1:
        current = conn.execute(
            f"SELECT serial FROM tickets WHERE scale_id=? AND status IN {OPEN_STATUSES_SQL} LIMIT 1",
            (scale_id,),
        ).fetchone()
        serial = current["serial"] if current else ""
        raise HTTPException(409, f"秤位仍有未结正式磅条 {serial}，不能按牌补落")

    scale = conn.execute("SELECT * FROM scales WHERE id=?", (scale_id,)).fetchone()
    if scale and scale["status"] == "故障":
        raise HTTPException(409, "地磅故障，不能补落")
    if scale and (scale["status"] == "占用" or scale["pinned_ticket_id"] is not None):
        raise HTTPException(409, "正式磅条已终态但秤位仍钉住，请先串号解钉")

    try:
        snapshot = json.loads(row["request_json"] or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "席位牌请求快照损坏，不能补落") from exc
    body = WeighIn(**snapshot)
    validated = validate_weigh(conn, body)
    if validated["scale_id"] != scale_id or validated["site_id"] != row["site_id"]:
        raise HTTPException(400, "席位牌快照与牌面地磅/回收点不一致")

    shift = current_shift(conn)
    ticket_id, serial, _segment_id = create_formal_ticket(
        conn,
        validated,
        shift["id"],
        seat_card_id=card_id,
        source="席位牌补落",
    )
    now = now_str()
    conn.execute(
        """
        UPDATE seat_cards
        SET status='已补落', ticket_id=?, activated_at=?, finished_at=?,
            outcome='按牌补落正式磅条', fail_reason=''
        WHERE id=? AND status='候席'
        """,
        (ticket_id, now, now, card_id),
    )
    add_seat_event(conn, card_id, scale_id, "补落", "候席", "已补落", ticket_id, serial)
    audit_id = audit(conn, "席位牌", card_id, "席位牌补落", f"{row['card_no']} -> {serial}")
    return {"ticket_id": ticket_id, "card_id": card_id, "audit_id": audit_id, "idempotent": False}


def build_receipt(
    conn,
    *,
    ticket_id: int | None = None,
    card_id: int | None = None,
    audit_id: int | None = None,
    idempotent: bool = False,
) -> dict:
    result: dict[str, Any] = {
        "架构口径": ARCHITECTURE,
        "幂等重放": idempotent,
    }
    scale_id: int | None = None

    if ticket_id is not None:
        ticket = ticket_view(conn, ticket_id)
        if ticket:
            result.update(ticket)
            result["磅条"] = ticket
            result["ticket_id"] = ticket["编号"]
            result["serial"] = ticket["磅条号"]
            result["是否正式落条"] = True
            result["正式落条"] = True
            result["formal"] = True
            result["回执类型"] = "正式磅条回执"
            scale_id = int(ticket["地磅编号"])
            if card_id is None and ticket.get("席位牌编号"):
                card_id = int(ticket["席位牌编号"])

    if card_id is not None:
        card = card_view(conn, card_id)
        if card:
            result["席位牌"] = card
            result["席位牌编号"] = card["编号"]
            result["card_id"] = card["编号"]
            result["席位牌号"] = card["席位牌号"]
            result["card_no"] = card["席位牌号"]
            result["核验码"] = card["核验码"]
            result["verify_code"] = card["核验码"]
            result["席位状态"] = card["状态"]
            result["status"] = card["状态"]
            scale_id = int(card["地磅编号"])
            if ticket_id is None and card.get("磅条编号"):
                # Do not automatically promote this to a ticket response; preserve
                # the queued-card shape while exposing the linked ticket if present.
                result["磅条编号"] = card["磅条编号"]
                result["ticket_id"] = card["磅条编号"]
                result["磅条号"] = card["磅条号"]
                result["serial"] = card["磅条号"]

    if ticket_id is None:
        result["是否正式落条"] = False
        result["正式落条"] = False
        result["formal"] = False
        result["回执类型"] = "排队席位牌回执"
        if card_id is not None and "状态" not in result:
            card = card_view(conn, card_id)
            if card:
                result["状态"] = card["状态"]
                result["status"] = card["状态"]

    if scale_id is not None:
        result["态势占用段"] = active_segment(conn, scale_id)
        result["未结正式磅条数"] = open_ticket_count(conn, scale_id)

    if audit_id is not None:
        result["审计编号"] = audit_id
    return result


def replay_card(conn, card_ref: Any) -> dict:
    card = find_card(conn, card_ref)
    if not card:
        raise HTTPException(404, "席位牌不存在")
    events = [
        dict(r)
        for r in conn.execute(
            EVENT_SELECT + " WHERE e.card_id=? ORDER BY e.seq, e.id",
            (card["编号"],),
        ).fetchall()
    ]
    replayed_status = ""
    for event in events:
        if event.get("新状态"):
            replayed_status = event["新状态"]
    final_status = replayed_status or card["状态"]
    audit_rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id AS 编号, kind AS 类别, ref_id AS 关联, action AS 动作,
                   created_at AS 时间, note AS 备注
            FROM audit_events
            WHERE kind='席位牌' AND ref_id=?
            ORDER BY id
            """,
            (card["编号"],),
        ).fetchall()
    ]
    return {
        "席位牌": card,
        "card": card,
        "事件流": events,
        "席位事件": events,
        "events": events,
        "审计流": audit_rows,
        "audits": audit_rows,
        "重放结局": final_status,
        "replayed_outcome": final_status,
        "outcome": final_status,
        "册页结局": card["状态"],
        "ledger_outcome": card["状态"],
        "结局一致": final_status == card["状态"],
        "consistent": final_status == card["状态"],
        "事件数": len(events),
        "架构口径": ARCHITECTURE,
    }
