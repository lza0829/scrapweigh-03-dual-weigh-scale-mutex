from __future__ import annotations

import concurrent.futures
import threading

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "seat-mutex.db"
    monkeypatch.setenv("SCRAPWEIGH_DB", str(db_path))

    from app.db import db
    from app.seed import seed_if_empty

    seed_if_empty()
    # Importing main executes seed_if_empty(); environment is already pointed at tmp.
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    # Keep the helper import used by assertions obvious to readers.
    assert db is not None


def _make_scale(client: TestClient, name: str = "互斥点", code: str = "1号磅") -> int:
    site = client.post("/api/回收点", json={"名称": name, "地址": "测试巷"}).json()
    scale = client.post(
        "/api/地磅",
        json={"回收点编号": site["编号"], "地磅": code},
    ).json()
    return scale["编号"]


def _weigh(scale_id: int, **overrides):
    payload = {"地磅编号": scale_id, "毛重": 1200.0, "皮重": 500.0, "扣杂": 10.0}
    payload.update(overrides)
    return payload


def test_second_arrival_gets_verifiable_seat_card_without_second_formal_ticket(client: TestClient):
    scale_id = _make_scale(client)

    first = client.post("/api/过磅", json=_weigh(scale_id)).json()
    second = client.post("/api/过磅", json=_weigh(scale_id, 备注="后到")).json()

    assert first["正式落条"] is True
    assert first["磅条号"]
    assert second["正式落条"] is False
    assert second["回执类型"] == "排队席位牌回执"
    assert second["席位牌"]["状态"] == "候席"
    assert second["席位牌号"]

    cards = client.get("/api/席位牌册", params={"地磅编号": scale_id}).json()
    assert any(c["席位牌号"] == second["席位牌号"] and c["状态"] == "候席" for c in cards)

    gate = [g for g in client.get("/api/地磅席位闸").json() if g["地磅编号"] == scale_id][0]
    assert gate["未结正式磅条数"] == 1
    assert gate["钉住磅条号"] == first["磅条号"]
    assert gate["候席牌数"] == 1

    segments = client.get("/api/态势占用段", params={"地磅编号": scale_id, "状态": "占用中"}).json()
    assert len(segments) == 1
    assert segments[0]["磅条号"] == first["磅条号"]

    receipt = client.get(f"/api/回执/{second['席位牌号']}").json()
    assert receipt["席位牌"]["核验码"] == second["核验码"]
    assert receipt["重放"]["重放结局"] == "候席"

    cross = client.get("/api/对照", params={"地磅编号": scale_id}).json()
    assert cross["闭合"] is True
    assert cross["不变量"]["一秤一未结正式"] is True


def test_concurrent_same_scale_has_exactly_one_formal_ticket(client: TestClient):
    scale_id = _make_scale(client, name="并发点", code="C1")
    barrier = threading.Barrier(8)

    def submit(index: int):
        barrier.wait()
        response = client.post("/api/过磅", json=_weigh(scale_id, 备注=f"并发{index}"))
        return response.status_code, response.json()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))

    assert all(status == 200 for status, _ in results)
    formal = [body for _, body in results if body.get("正式落条") is True]
    queued = [body for _, body in results if body.get("回执类型") == "排队席位牌回执"]
    assert len(formal) == 1
    assert len(queued) == 7

    gate = [g for g in client.get("/api/地磅席位闸").json() if g["地磅编号"] == scale_id][0]
    assert gate["未结正式磅条数"] == 1
    assert gate["候席牌数"] == 7

    violations = client.get("/api/未结正式约束").json()
    assert violations["成立"] is True
    assert violations["冲突秤"] == []


def test_seat_timeout_is_replayable_and_blocks_promotion(client: TestClient):
    scale_id = _make_scale(client, name="超时点", code="T1")
    first = client.post("/api/过磅", json=_weigh(scale_id)).json()
    queued = client.post("/api/过磅", json=_weigh(scale_id, 席位有效秒数=0)).json()
    card_id = queued["席位牌"]["编号"]

    from app.db import db

    with db() as conn:
        conn.execute(
            "UPDATE seat_cards SET expires_at='2000-01-01 00:00:00' WHERE id=?",
            (card_id,),
        )

    swept = client.post("/api/席位牌/超时作废").json()
    assert swept["作废数量"] >= 1

    replay = client.get(f"/api/席位牌/{card_id}/重放").json()
    assert replay["重放结局"] == "已超时作废"
    assert replay["结局一致"] is True

    promoted = client.post(f"/api/席位牌/{card_id}/补落")
    assert promoted.status_code == 409

    voided = client.post(f"/api/销单/{first['编号']}")
    assert voided.status_code == 200
    promoted_after_release = client.post(f"/api/席位牌/{card_id}/补落")
    assert promoted_after_release.status_code == 409


def test_half_transaction_rolls_back_ghost_ticket_card_pin_and_segment(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    scale_id = _make_scale(client, name="回滚点", code="R1")

    import app.routers.weigh as weigh_router
    from app.db import db

    real_audit = weigh_router.audit

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("simulated half-write failure")

    monkeypatch.setattr(weigh_router, "audit", fail_audit)
    with pytest.raises(RuntimeError):
        client.post("/api/过磅", json=_weigh(scale_id))

    with db() as conn:
        ticket_count = conn.execute(
            "SELECT COUNT(*) AS c FROM tickets WHERE scale_id=?",
            (scale_id,),
        ).fetchone()["c"]
        card_count = conn.execute(
            "SELECT COUNT(*) AS c FROM seat_cards WHERE scale_id=?",
            (scale_id,),
        ).fetchone()["c"]
        segment_count = conn.execute(
            "SELECT COUNT(*) AS c FROM occupancy_segments WHERE scale_id=?",
            (scale_id,),
        ).fetchone()["c"]
        scale = conn.execute(
            "SELECT status, pinned_ticket_id FROM scales WHERE id=?",
            (scale_id,),
        ).fetchone()

    assert ticket_count == card_count == segment_count == 0
    assert scale["status"] == "空闲"
    assert scale["pinned_ticket_id"] is None

    weigh_router.audit = real_audit
    first = client.post("/api/过磅", json=_weigh(scale_id)).json()
    assert first["正式落条"] is True

    weigh_router.audit = fail_audit
    with pytest.raises(RuntimeError):
        client.post("/api/过磅", json=_weigh(scale_id))

    with db() as conn:
        open_count = conn.execute(
            "SELECT COUNT(*) AS c FROM tickets WHERE scale_id=? AND status IN ('待定级','待结价')",
            (scale_id,),
        ).fetchone()["c"]
        waiting_count = conn.execute(
            "SELECT COUNT(*) AS c FROM seat_cards WHERE scale_id=? AND status='候席'",
            (scale_id,),
        ).fetchone()["c"]
    assert open_count == 1
    assert waiting_count == 0


def test_settle_and_void_race_has_one_terminal_winner(client: TestClient):
    scale_id = _make_scale(client, name="终态点", code="Z1")
    ticket_id = client.post("/api/过磅", json=_weigh(scale_id)).json()["编号"]
    grade = client.post(
        "/api/品级",
        json={"品类": "废金属", "品级名": "Race铁", "单价": 2.5},
    ).json()
    assert client.post(f"/api/定级/{ticket_id}", json={"品级编号": grade["编号"]}).status_code == 200

    barrier = threading.Barrier(2)

    def race(path: str):
        barrier.wait()
        response = client.post(path)
        return response.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(race, [f"/api/结价/{ticket_id}", f"/api/销单/{ticket_id}"]))

    assert sum(200 <= code < 300 for code in statuses) == 1
    assert sum(code >= 400 for code in statuses) == 1

    gate = [g for g in client.get("/api/地磅席位闸").json() if g["地磅编号"] == scale_id][0]
    assert gate["未结正式磅条数"] == 0
    assert gate["钉住磅条编号"] is None
