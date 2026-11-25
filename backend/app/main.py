from __future__ import annotations

import sqlite3

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import PROJECT_NAME
from app.routers import (
    audit,
    dashboard,
    grades,
    grading,
    operations,
    scales,
    seats,
    settle,
    settings,
    shifts,
    sites,
    tickets,
    voids,
    weigh,
)
from app.seed import seed_if_empty

seed_if_empty()

app = FastAPI(title=PROJECT_NAME)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(sqlite3.IntegrityError)
async def sqlite_integrity_handler(_request: Request, exc: sqlite3.IntegrityError):
    message = str(exc)
    if "uq_open_ticket_per_scale" in message or "tickets.scale_id" in message:
        detail = "同秤已有一张未结正式磅条；后到请求只能领取席位牌，禁止双正式未结"
    elif "uq_active_occupancy_per_scale" in message or "occupancy_segments.scale_id" in message:
        detail = "同秤已有一个活跃态势占用段"
    elif "uq_seat_idempotency_key" in message or "seat_cards.idempotency_key" in message:
        detail = "幂等键已被使用，请重放原席位牌回执"
    else:
        detail = f"数据约束冲突: {message}"
    return JSONResponse(status_code=409, content={"detail": detail, "类别": "完整性约束"})


@app.exception_handler(sqlite3.OperationalError)
async def sqlite_operational_handler(_request: Request, exc: sqlite3.OperationalError):
    if "locked" in str(exc).lower():
        return JSONResponse(status_code=409, content={"detail": "地磅席位闸繁忙，请重放/重试席位牌请求"})
    return JSONResponse(status_code=500, content={"detail": f"数据库操作失败: {exc}"})


for mod in (
    dashboard,
    sites,
    scales,
    grades,
    weigh,
    tickets,
    seats,
    operations,
    grading,
    settle,
    voids,
    shifts,
    settings,
    audit,
):
    app.include_router(mod.router, prefix="/api")


@app.get("/api/健康")
@app.get("/api/health")
@app.get("/health")
def health():
    return {"产品": PROJECT_NAME, "状态": "正常", "status": "ok", "席位架构": "排队席位牌后补落"}
