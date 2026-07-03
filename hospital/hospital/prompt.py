"""
HOSPITAL APPOINTMENT MANAGEMENT SYSTEM
========================================
Prompt Engineering Framework + FastAPI Implementation

--------------------------------------------------------------------
1. ROLE & CONTEXT
--------------------------------------------------------------------
You are a Senior Backend Developer specializing in healthcare scheduling
systems. You design REST APIs using FastAPI/Node.js, follow HIPAA-conscious
data handling practices, and build systems that prevent double-booking and
enforce doctor availability windows.

Context: We are building a Hospital Appointment Management System that
ingests booking requests and classifies them as CONFIRMED, WAITLISTED, or
REJECTED based on doctor availability, slot capacity, and patient priority
(emergency vs routine).

--------------------------------------------------------------------
2. FEW-SHOT EXAMPLES
--------------------------------------------------------------------
Example 1:
Input: doctor_id=D12, slot=10:00-10:30, capacity=1, current_bookings=0
Output: {"status": "CONFIRMED", "reason": "Slot available"}

Example 2:
Input: doctor_id=D12, slot=10:00-10:30, capacity=1, current_bookings=1
Output: {"status": "WAITLISTED", "reason": "Slot full, added to queue"}

Example 3:
Input: doctor_id=D12, slot=10:00-10:30, doctor_on_leave=true
Output: {"status": "REJECTED", "reason": "Doctor unavailable on requested date"}

--------------------------------------------------------------------
3. STRUCTURED OUTPUT SCHEMA
--------------------------------------------------------------------
{
  "appointment_id": string,
  "patient_id": int,
  "doctor_id": string,
  "status": "CONFIRMED" | "WAITLISTED" | "REJECTED",
  "reason": string,
  "scheduled_time": string (ISO 8601) | null,
  "queue_position": int | null
}

--------------------------------------------------------------------
4. CHAIN OF THOUGHT
--------------------------------------------------------------------
1. Validate the requested slot format and check clinic hours.
2. Check doctor availability (on-leave, existing bookings, buffer time).
3. Check slot capacity vs current_bookings count.
4. If full, check patient_type - emergency cases may override capacity/queue.
5. Check for conflicting appointments for the same patient at overlapping times.
6. Apply final decision: CONFIRMED / WAITLISTED / REJECTED.

--------------------------------------------------------------------
5. MULTI-STEP CHAINING (pipeline design)
--------------------------------------------------------------------
Step 1 (Validation Agent)   -> validate booking payload
Step 2 (Availability Agent) -> check doctor calendar, leave, capacity
Step 3 (Priority Agent)     -> resolve conflicts using urgency score
Step 4 (Persistence Agent)  -> lock slot, write record, audit trail
Step 5 (Notification Agent) -> notify patient + sync doctor calendar
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime
from enum import Enum
import uuid

app = FastAPI(title="Hospital Appointment Management System")

# In-memory stores (replace with real DB + row-level locking in production)
DOCTOR_SCHEDULE: dict[str, list[dict]] = {}   # doctor_id -> list of booked appointments
DOCTOR_LEAVE: dict[str, list[str]] = {}       # doctor_id -> list of leave dates (YYYY-MM-DD)
SLOT_CAPACITY = 1                             # default; could be per-doctor config
APPOINTMENTS: dict[str, dict] = {}


class PatientType(str, Enum):
    EMERGENCY = "emergency"
    ROUTINE = "routine"


class BookingRequest(BaseModel):
    patient_id: int
    doctor_id: str
    requested_start: datetime
    requested_end: datetime
    patient_type: PatientType = PatientType.ROUTINE


class BookingResponse(BaseModel):
    appointment_id: str
    patient_id: int
    doctor_id: str
    status: str
    reason: str
    scheduled_time: datetime | None
    queue_position: int | None = None


# ---------- Step 1: Validation Agent ----------
def validate_booking(payload: BookingRequest) -> None:
    if payload.requested_end <= payload.requested_start:
        raise HTTPException(400, "requested_end must be after requested_start")
    if payload.requested_start < datetime.utcnow():
        raise HTTPException(400, "Cannot book an appointment in the past")


# ---------- Step 2: Availability Agent ----------
def check_availability(payload: BookingRequest) -> tuple[bool, int]:
    date_str = payload.requested_start.date().isoformat()
    if date_str in DOCTOR_LEAVE.get(payload.doctor_id, []):
        raise HTTPException(409, "Doctor unavailable (on leave) for requested date")

    existing = DOCTOR_SCHEDULE.get(payload.doctor_id, [])
    overlapping = [
        a for a in existing
        if a["start"] < payload.requested_end and a["end"] > payload.requested_start
    ]
    capacity_remaining = SLOT_CAPACITY - len(overlapping)
    return capacity_remaining > 0, capacity_remaining


# ---------- Step 3: Priority Agent ----------
def resolve_priority(payload: BookingRequest, capacity_remaining: int) -> tuple[str, str, int | None]:
    if capacity_remaining > 0:
        return "CONFIRMED", "Slot available", None

    if payload.patient_type == PatientType.EMERGENCY:
        # Emergency overrides capacity — bump a routine patient to waitlist in real system
        return "CONFIRMED", "Emergency override: slot capacity exceeded", None

    queue_position = len(DOCTOR_SCHEDULE.get(payload.doctor_id, [])) + 1
    return "WAITLISTED", "Slot full, added to queue", queue_position


# ---------- Step 4: Persistence Agent ----------
def persist_appointment(payload: BookingRequest, status: str) -> str:
    appointment_id = str(uuid.uuid4())
    if status == "CONFIRMED":
        DOCTOR_SCHEDULE.setdefault(payload.doctor_id, []).append({
            "start": payload.requested_start,
            "end": payload.requested_end,
            "patient_id": payload.patient_id,
            "appointment_id": appointment_id,
        })
    APPOINTMENTS[appointment_id] = {
        "patient_id": payload.patient_id,
        "doctor_id": payload.doctor_id,
        "status": status,
    }
    return appointment_id


# ---------- Step 5: Notification Agent (stub) ----------
def notify(status: str, patient_id: int) -> None:
    print(f"[Notify] Patient {patient_id}: appointment {status}")


@app.post("/api/v1/appointments/book", response_model=BookingResponse, status_code=201)
def book_appointment(payload: BookingRequest):
    validate_booking(payload)
    available, capacity_remaining = check_availability(payload)
    status, reason, queue_position = resolve_priority(payload, capacity_remaining)
    appointment_id = persist_appointment(payload, status)
    notify(status, payload.patient_id)

    return BookingResponse(
        appointment_id=appointment_id,
        patient_id=payload.patient_id,
        doctor_id=payload.doctor_id,
        status=status,
        reason=reason,
        scheduled_time=payload.requested_start if status == "CONFIRMED" else None,
        queue_position=queue_position,
    )
