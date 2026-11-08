"""Work-order API for the Dispatch demo."""

from __future__ import annotations

import os
import socket
from datetime import datetime, timezone
from threading import Lock
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ProblemType = Literal[
    "no_hot_water",
    "leak",
    "no_heat_ac",
    "appliance",
    "lock_access",
    "electrical",
    "other",
]

PROBLEM_LABELS: dict[str, str] = {
    "no_hot_water": "No hot water",
    "leak": "Leak / flooding",
    "no_heat_ac": "No heat or A/C",
    "appliance": "Broken appliance",
    "lock_access": "Lock / access issue",
    "electrical": "Electrical problem",
    "other": "Something else",
}

Urgency = Literal["can_wait", "soon", "emergency"]

URGENCY_LABELS: dict[str, str] = {
    "can_wait": "Can wait",
    "soon": "Need help soon",
    "emergency": "Emergency",
}


class WorkOrderCreate(BaseModel):
    problem: ProblemType
    problem_other: str | None = Field(default=None, max_length=80)
    description: str = Field(min_length=1, max_length=1000)
    unit: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    phone: str | None = Field(default=None, max_length=40)
    urgency: Urgency = "soon"
    building: str = Field(default="1420 Elm St", max_length=120)


class WorkOrder(BaseModel):
    id: str
    created_at: str
    problem: ProblemType
    problem_label: str
    problem_other: str | None
    description: str
    unit: str
    name: str
    phone: str | None
    urgency: Urgency
    urgency_label: str
    building: str
    title: str
    location: str


app = FastAPI(title="Dispatch API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_lock = Lock()
_orders: list[WorkOrder] = []


def _title_for(payload: WorkOrderCreate) -> str:
    if payload.problem == "other":
        other = (payload.problem_other or "").strip()
        return other or "Something else"
    return PROBLEM_LABELS[payload.problem]


def _build_order(payload: WorkOrderCreate) -> WorkOrder:
    description = payload.description.strip()
    unit = payload.unit.strip()
    name = payload.name.strip()
    building = payload.building.strip() or "1420 Elm St"
    title = _title_for(payload)
    return WorkOrder(
        id=str(uuid4()),
        created_at=datetime.now(timezone.utc).isoformat(),
        problem=payload.problem,
        problem_label=PROBLEM_LABELS[payload.problem],
        problem_other=(payload.problem_other or "").strip() or None,
        description=description,
        unit=unit,
        name=name,
        phone=(payload.phone or "").strip() or None,
        urgency=payload.urgency,
        urgency_label=URGENCY_LABELS[payload.urgency],
        building=building,
        title=title,
        location=f"Unit {unit} · {building}",
    )


def _seed_orders() -> None:
    samples = [
        WorkOrderCreate(
            problem="leak",
            description="Water pouring from under the kitchen sink onto the floor.",
            unit="2A",
            name="Maya Chen",
            phone="555-0142",
            urgency="emergency",
        ),
        WorkOrderCreate(
            problem="no_hot_water",
            description="No hot water since this morning. Cold only in shower and sink.",
            unit="4B",
            name="Jordan Lee",
            urgency="soon",
        ),
        WorkOrderCreate(
            problem="no_hot_water",
            description="Water heater only lukewarm. Same building stack as 4B.",
            unit="4C",
            name="Chris Nguyen",
            urgency="soon",
        ),
        WorkOrderCreate(
            problem="lock_access",
            description="Front door lock sticks and sometimes won't open with the key.",
            unit="1F",
            name="Sam Ortiz",
            urgency="soon",
        ),
        WorkOrderCreate(
            problem="appliance",
            description="Dishwasher won't start. Tried resetting the breaker once.",
            unit="7C",
            name="Riley Park",
            urgency="can_wait",
        ),
    ]
    with _lock:
        if _orders:
            return
        _orders.extend(_build_order(sample) for sample in samples)


_seed_orders()


def _lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/demo-link")
def demo_link() -> dict[str, str]:
    """URL phones should open during a live demo (LAN, not localhost)."""
    port = os.getenv("FRONTEND_PORT", "5173")
    ip = _lan_ip()
    origin = f"http://{ip}:{port}"
    return {
        "origin": origin,
        "report_url": f"{origin}/report",
        "board_url": f"{origin}/",
    }


@app.get("/api/work-orders")
def list_work_orders() -> list[WorkOrder]:
    with _lock:
        return list(reversed(_orders))


@app.get("/api/work-orders/latest")
def latest_work_order() -> WorkOrder | None:
    with _lock:
        return _orders[-1] if _orders else None


@app.get("/api/work-orders/{order_id}")
def get_work_order(order_id: str) -> WorkOrder:
    with _lock:
        for order in _orders:
            if order.id == order_id:
                return order
    raise HTTPException(status_code=404, detail="Work order not found")


@app.post("/api/work-orders", status_code=201)
def create_work_order(payload: WorkOrderCreate) -> WorkOrder:
    description = payload.description.strip()
    unit = payload.unit.strip()
    name = payload.name.strip()
    if not description or not unit or not name:
        raise HTTPException(status_code=400, detail="Missing required fields")

    if payload.problem == "other" and not (payload.problem_other or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Please say what the problem is",
        )

    order = _build_order(payload)
    with _lock:
        _orders.append(order)

    return order


@app.post("/api/work-orders/seed")
def seed_work_orders() -> dict[str, int]:
    with _lock:
        _orders.clear()
    _seed_orders()
    with _lock:
        return {"seeded": len(_orders)}


@app.delete("/api/work-orders")
def clear_work_orders() -> dict[str, int]:
    with _lock:
        count = len(_orders)
        _orders.clear()
    return {"cleared": count}
