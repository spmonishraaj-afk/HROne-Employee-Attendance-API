"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import json
from bson import json_util
from pymongo.errors import DuplicateKeyError
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pymongo import MongoClient

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

def create_indexes():
    db.employees.create_index("emp_code", unique=True)

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True
    )

    db.attendance_logs.create_index([("date", -1)])
    db.attendance_logs.create_index([("status", 1), ("date", -1)])
    db.employees.create_index([("department", 1), ("joined_on", 1)])
    db.employees.create_index("joined_on")


@app.on_event("startup")
def startup():
    create_indexes()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    punch_in = punch_in.astimezone(IST)
    shift_hour, shift_minute = map(int, shift_start.split(":"))
    shift_time = punch_in.replace(
        hour=shift_hour, minute=shift_minute, second=0, microsecond=0
    )
    minutes = int((punch_in - shift_time).total_seconds() // 60)
    return minutes if minutes > 10 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    from decimal import Decimal, ROUND_HALF_UP

    seconds = int((punch_out - punch_in).total_seconds())
    hours = Decimal(seconds) / Decimal(3600)
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(
        punch_out: datetime, shift_start: str, shift_end: str, date_str: str
) -> int:
    punch_out = punch_out.astimezone(IST)
    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))

    shift_end_time = datetime.fromisoformat(date_str).replace(
        hour=end_hour, minute=end_minute, second=0, microsecond=0, tzinfo=IST
    )
    if (end_hour, end_minute) <= (start_hour, start_minute):
        shift_end_time += timedelta(days=1)

    minutes = int((punch_out - shift_end_time).total_seconds() // 60)
    return minutes if minutes >= 30 else 0


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def timestamp_to_utc(value: Optional[int]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).replace(microsecond=0)
    except (OverflowError, OSError, ValueError):
        raise HTTPException(422, "Invalid timestamp")


def validate_month(month: str):
    from datetime import date as date_type

    try:
        year, month_num = map(int, month.split("-"))
        if len(month) != 7 or month[4] != "-" or not 1 <= month_num <= 12:
            raise ValueError
        date_type(year, month_num, 1)
        return year, month_num
    except (ValueError, TypeError):
        raise HTTPException(422, "Month must be YYYY-MM")


def validate_date(value: str):
    from datetime import date as date_type

    try:
        parsed = date_type.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise HTTPException(422, "Date must be YYYY-MM-DD")

# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


class RegularizeIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    regularized_by: str
    reason: str

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None

# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "Database unavailable")

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    from pymongo.errors import DuplicateKeyError

    validate_date(body.joined_on)
    for value in [body.shift_start, body.shift_end]:
        try:
            hour, minute = map(int, value.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59) or len(value) != 5:
                raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(422, "Shift times must use HH:MM")

    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    doc.pop("_id", None)
    return doc

@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    now = timestamp_to_utc(body.punched_at)
    local_time = now.astimezone(IST)
    date = local_time.date().isoformat()

    record = db.attendance_logs.find_one({"emp_code": body.emp_code, "date": date})
    if record is None:
        yesterday = (local_time.date() - timedelta(days=1)).isoformat()
        record = db.attendance_logs.find_one(
            {"emp_code": body.emp_code, "date": yesterday}
        )

    if record is None:
        if db.employees.find_one({"emp_code": body.emp_code}) is None:
            raise HTTPException(404, "Employee not found")
        raise HTTPException(404, "Attendance record not found")

    if record.get("punch_out") is not None:
        raise HTTPException(409, "Already punched out")

    punch_in_time = utc_datetime(record["punch_in"])
    if now <= punch_in_time:
        raise HTTPException(422, "Punch-out must be after punch-in")
    if (now - punch_in_time).total_seconds() > 86400:
        raise HTTPException(422, "Punch duration cannot exceed 24 hours")

    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(404, "Employee not found")

    work_hours = compute_work_hours(punch_in_time, now)
    overtime_minutes = compute_overtime(
        now, employee["shift_start"], employee["shift_end"], record["date"]
    )

    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {
            "punch_out": now,
            "work_hours": work_hours,
            "overtime_minutes": overtime_minutes,
            "half_day": work_hours < 4.5
        }}
    )
    if result.modified_count == 0:
        raise HTTPException(409, "Attendance changed; try again")

    return db.attendance_logs.find_one({"_id": record["_id"]}, {"_id": 0})

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    validate_date(date)
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if record is None:
        if db.employees.find_one({"emp_code": emp_code}) is None:
            raise HTTPException(404, "Employee not found")
        raise HTTPException(404, "Attendance record not found")

    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(404, "Employee not found")

    changes = {}
    if "status" in body.model_fields_set:
        if body.status not in ["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]:
            raise HTTPException(422, "Invalid status")
        if body.status != record.get("status"):
            changes["status"] = body.status

    for field in ["punch_in", "punch_out"]:
        if field in body.model_fields_set:
            value = getattr(body, field)
            changes[field] = timestamp_to_utc(value) if value is not None else None

    new_status = changes.get("status", record.get("status"))
    new_in = changes.get("punch_in", record.get("punch_in"))
    new_out = changes.get("punch_out", record.get("punch_out"))

    if new_status in ["ABSENT", "LEAVE"]:
        if ("punch_in" in body.model_fields_set and body.punch_in is not None) or (
                "punch_out" in body.model_fields_set and body.punch_out is not None
        ):
            raise HTTPException(422, "ABSENT and LEAVE cannot have punch times")
        new_in = None
        new_out = None
        changes["punch_in"] = None
        changes["punch_out"] = None

    if new_in is not None:
        local_in = utc_datetime(new_in).astimezone(IST)
        if local_in.date().isoformat() != date:
            raise HTTPException(422, "Punch-in must be on the attendance date")

    if new_out is not None:
        if new_in is None or utc_datetime(new_out) <= utc_datetime(new_in):
            raise HTTPException(422, "Punch-out must be after punch-in")
        if (utc_datetime(new_out) - utc_datetime(new_in)).total_seconds() > 86400:
            raise HTTPException(422, "Punch duration cannot exceed 24 hours")
        if utc_datetime(new_out).astimezone(IST).date().isoformat() not in [
            date, (validate_date(date) + timedelta(days=1)).isoformat()
        ]:
            raise HTTPException(422, "Punch-out must be within 24 hours of attendance date")

    if not changes:
        raise HTTPException(422, "No changes provided")

    work_hours = (
        compute_work_hours(utc_datetime(new_in), utc_datetime(new_out))
        if new_in is not None and new_out is not None and new_status not in ["ABSENT", "LEAVE"]
        else None
    )
    late_minutes = (
        compute_late_minutes(utc_datetime(new_in), employee["shift_start"])
        if new_in is not None and new_status not in ["ABSENT", "LEAVE"] else 0
    )
    overtime_minutes = (
        compute_overtime(
            utc_datetime(new_out), employee["shift_start"], employee["shift_end"], date
        )
        if new_out is not None and new_status not in ["ABSENT", "LEAVE"] else 0
    )
    changes.update({
        "work_hours": work_hours,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "half_day": work_hours is not None and work_hours < 4.5
    })

    history_changes = {
        key: {"from": record.get(key), "to": value}
        for key, value in changes.items()
        if record.get(key) != value
    }
    if not history_changes:
        raise HTTPException(422, "No changes provided")

    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": history_changes
    }
    history_filter = (
        {"history": record["history"]}
        if "history" in record
        else {"history": {"$exists": False}}
    )
    result = db.attendance_logs.update_one(
        {"_id": record["_id"], **history_filter},
        {"$set": changes, "$push": {"history": history_entry}}
    )
    if result.modified_count == 0:
        raise HTTPException(409, "Attendance changed; try again")

    return db.attendance_logs.find_one({"_id": record["_id"]}, {"_id": 0})

@app.get("/employees")
def list_employees(
        department: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
):
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(422, "Invalid page or page_size")

    q = {}
    if department:
        q["department"] = department

    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size
    }




@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "Employee not found")
    if body.status not in ["PRESENT", "WFH", "ON_DUTY"]:
        raise HTTPException(422, "Invalid status")

    punch_time = timestamp_to_utc(body.punched_at)
    local_time = punch_time.astimezone(IST)
    date = local_time.date().isoformat()
    doc = {
        "emp_code": body.emp_code,
        "date": date,
        "status": body.status,
        "punch_in": punch_time,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(local_time, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": []
    }
    try:
        result = db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "Already punched in")

    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc

@app.get("/attendance")
def list_attendance(
        emp_code: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
):
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(422, "Invalid page or page_size")
    if date_from:
        validate_date(date_from)
    if date_to:
        validate_date(date_to)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be on or before date_to")
    if status and status not in ["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]:
        raise HTTPException(422, "Invalid status")

    query = {}
    if emp_code:
        query["emp_code"] = emp_code
    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to
    if status:
        query["status"] = status

    total = db.attendance_logs.count_documents(query)
    items = list(
        db.attendance_logs.find(query, {"_id": 1, "emp_code": 1, "date": 1, "status": 1,
                                        "punch_in": 1, "punch_out": 1, "work_hours": 1,
                                        "late_minutes": 1, "overtime_minutes": 1, "half_day": 1,
                                        "history": 1})
        .sort("date", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    for item in items:
        item["id"] = str(item.pop("_id"))
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    from calendar import monthrange
    from datetime import date as date_type
    from decimal import Decimal, ROUND_HALF_UP

    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(404, "Employee not found")

    year, month_num = validate_month(month)
    last_day = monthrange(year, month_num)[1]
    month_start = date_type(year, month_num, 1)
    month_end = date_type(year, month_num, last_day)
    joined_on = validate_date(employee["joined_on"])
    first_workday = max(month_start, joined_on)
    working_days = sum(
        1 for day in range(first_workday.day, month_end.day + 1)
        if date_type(year, month_num, day).weekday() < 5
    ) if first_workday <= month_end else 0

    pipeline = [
        {"$match": {"emp_code": emp_code, "date": {"$gte": month + "-01", "$lte": month + "-31"}}},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {"$cond": [{"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]}, 1, 0]}},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}}
        }}
    ]
    result = list(db.attendance_logs.aggregate(pipeline))
    totals = result[0] if result else {
        "present_days": 0, "leave_days": 0, "late_count": 0,
        "total_late_minutes": 0, "total_overtime_minutes": 0
    }
    attendance_pct = float(
        (Decimal(totals["present_days"]) * 100 / Decimal(working_days))
        .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    ) if working_days else 0.0
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": totals["present_days"],
        "leave_days": totals["leave_days"],
        "late_count": totals["late_count"],
        "total_late_minutes": totals["total_late_minutes"],
        "total_overtime_minutes": totals["total_overtime_minutes"],
        "attendance_pct": attendance_pct
    }

@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    from calendar import monthrange
    from datetime import date as date_type
    from decimal import Decimal, ROUND_HALF_UP

    year, month_num = validate_month(month)
    last_day = monthrange(year, month_num)[1]
    month_end = f"{month}-{last_day:02d}"

    employee_match = {"joined_on": {"$lte": month_end}}
    if department:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$gte": ["$date", month + "-01"]},
                                    {"$lte": ["$date", month + "-31"]}
                                ]
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            1, 0
                        ]
                    }
                },
                "work_hours": {
                    "$avg": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$ne": ["$logs.work_hours", None]}
                                ]
                            },
                            "$logs.work_hours",
                            None
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$logs.late_minutes", 0]}, 1, 0]
                    }
                },
                "total_late_minutes": {"$sum": "$logs.late_minutes"},
                "total_overtime_minutes": {"$sum": "$logs.overtime_minutes"},
                "leave_count": {
                    "$sum": {
                        "$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]
                    }
                }
            }
        },
        {"$sort": {"_id": 1}}
    ]

    results = list(db.employees.aggregate(pipeline))
    items = []

    for item in results:
        avg_hours = item["work_hours"]
        if avg_hours is not None:
            avg_hours = float(
                Decimal(str(avg_hours)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            )

        items.append({
            "department": item["_id"],
            "headcount": len(item["headcount"]),
            "present_days": item["present_days"],
            "avg_work_hours": avg_hours,
            "late_count": item["late_count"],
            "total_late_minutes": item["total_late_minutes"],
            "total_overtime_minutes": item["total_overtime_minutes"],
            "leave_count": item["leave_count"]
        })

    return {"month": month, "items": items}
@app.get("/analytics/leaderboard/late")
def late_leaderboard(
        month: str,
        limit: int = 10,
        department: Optional[str] = None
):
    if limit < 1 or limit > 50:
        raise HTTPException(422, "Limit must be between 1 and 50")

    try:
        year, month_num = map(int, month.split("-"))
        if month_num < 1 or month_num > 12:
            raise ValueError
    except ValueError:
        raise HTTPException(422, "Month must be YYYY-MM")

    pipeline = [
        {
            "$match": {
                "date": {"$gte": month + "-01", "$lte": month + "-31"},
                "late_minutes": {"$gt": 0}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"}
            }
        },
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"}
    ]

    if department:
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {
                    "total_late_minutes": -1
                },
                "output": {
                    "rank": {"$rank": {}}
                }
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "rank": 1
            }
        },
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}}
    ])

    items = list(db.attendance_logs.aggregate(pipeline))

    return {"month": month, "items": items}
def build_department_trend_pipeline(department: str, from_date: str, to_date: str):
    start = datetime.combine(validate_date(from_date), datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(validate_date(to_date), datetime.min.time(), tzinfo=timezone.utc)
    day_count = (validate_date(to_date) - validate_date(from_date)).days + 1

    return [
        {"$match": {"department": department}},
        {"$set": {"day_dates": {"$map": {
            "input": {"$range": [0, day_count]},
            "as": "offset",
            "in": {"$dateAdd": {"startDate": start, "unit": "day", "amount": "$$offset"}}
        }}}},
        {"$unwind": "$day_dates"},
        {"$set": {"day_string": {"$dateToString": {"format": "%Y-%m-%d", "date": "$day_dates"}}}},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"code": "$emp_code", "day": "$day_string"},
            "pipeline": [{"$match": {"$expr": {"$and": [
                {"$eq": ["$emp_code", "$$code"]},
                {"$eq": ["$date", "$$day"]}
            ]}}}],
            "as": "log"
        }},
        {"$unwind": {"path": "$log", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$day_string",
            "headcount": {"$sum": {"$cond": [{"$lte": ["$joined_on", "$day_string"]}, 1, 0]}},
            "present_count": {"$sum": {"$cond": [
                {"$in": ["$log.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                {"$cond": [{"$eq": ["$log.half_day", True]}, 0.5, 1]},
                0
            ]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$log.late_minutes", 0]}, 0]}, 1, 0]}}
        }},
        {"$set": {
            "day_date": {"$dateFromString": {"dateString": "$_id"}},
            "is_working_day": {"$and": [
                {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$_id"}}}, 2]},
                {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$_id"}}}, 6]}
            ]}
        }},
        {"$set": {"attendance_rate": {"$cond": [
            {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
            {"$divide": [
                {"$floor": {"$add": [{"$multiply": [{"$divide": ["$present_count", "$headcount"]}, 10000]}, 0.5]}},
                10000
            ]},
            None
        ]}}},
        {"$setWindowFields": {
            "sortBy": {"_id": 1},
            "output": {"moving_avg_7d_raw": {
                "$avg": "$attendance_rate",
                "window": {"documents": [-6, 0]}
            }}
        }},
        {"$set": {"moving_avg_7d": {"$cond": [
            {"$ne": ["$moving_avg_7d_raw", None]},
            {"$divide": [
                {"$floor": {"$add": [{"$multiply": ["$moving_avg_7d_raw", 10000]}, 0.5]}},
                10000
            ]},
            None
        ]}}},
        {"$project": {
            "_id": 0,
            "date": "$_id",
            "is_working_day": 1,
            "headcount": 1,
            "present_count": 1,
            "late_count": 1,
            "attendance_rate": 1,
            "moving_avg_7d": 1
        }},
        {"$sort": {"date": 1}}
    ]


@app.get("/analytics/departments/{department}/trend")
def department_trend(
        department: str,
        from_date: str = Query(..., alias="from"),
        to_date: str = Query(..., alias="to")
):
    from datetime import date

    if db.employees.find_one({"department": department}) is None:
        raise HTTPException(404, "Department not found")
    start = validate_date(from_date)
    end = validate_date(to_date)
    if end < start or (end - start).days + 1 > 92:
        raise HTTPException(422, "Invalid date range")

    pipeline = build_department_trend_pipeline(department, from_date, to_date)
    items = list(db.employees.aggregate(pipeline))
    return {"department": department, "from": from_date, "to": to_date, "items": items}

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
        endpoint: str,
        emp_code: Optional[str] = None,
        month: Optional[str] = None,
        department: Optional[str] = None,
        limit: int = 10,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        from_date: Optional[str] = Query(None, alias="from"),
        to_date: Optional[str] = Query(None, alias="to")
):
    if endpoint not in [
        "attendance_list", "employee_monthly", "department_summary",
        "late_leaderboard", "department_trend"
    ]:
        raise HTTPException(422, "Invalid endpoint")

    if endpoint == "attendance_list":
        if page < 1 or page_size < 1 or page_size > 100:
            raise HTTPException(422, "Invalid page or page_size")
        query = {}
        if emp_code:
            query["emp_code"] = emp_code
        if date_from:
            validate_date(date_from)
            query.setdefault("date", {})["$gte"] = date_from
        if date_to:
            validate_date(date_to)
            query.setdefault("date", {})["$lte"] = date_to
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "date_from must be on or before date_to")
        if status:
            query["status"] = status
        explain = db.command(
            "explain",
            {"find": "attendance_logs", "filter": query, "sort": {"date": -1},
             "skip": (page - 1) * page_size, "limit": page_size},
            verbosity="executionStats"
        )
        collection = "attendance_logs"

    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month are required")
        validate_month(month)
        pipeline = [
            {"$match": {"emp_code": emp_code, "date": {"$gte": month + "-01", "$lte": month + "-31"}}},
            {"$group": {
                "_id": None,
                "present_days": {"$sum": {"$cond": [{"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]}, 1, 0]}},
                "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                "late_count": {"$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}},
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}}
            }}
        ]
        explain = db.command(
            "explain",
            {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats"
        )
        collection = "attendance_logs"

    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month is required")
        from calendar import monthrange
        year, month_num = validate_month(month)
        month_end = f"{month}-{monthrange(year, month_num)[1]:02d}"
        employee_match = {"joined_on": {"$lte": month_end}}
        if department:
            employee_match["department"] = department
        pipeline = [
            {"$match": employee_match},
            {"$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [{"$match": {"$expr": {"$and": [
                    {"$eq": ["$emp_code", "$$code"]},
                    {"$gte": ["$date", month + "-01"]},
                    {"$lte": ["$date", month + "-31"]}
                ]}}}],
                "as": "logs"
            }},
            {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
            {"$group": {
                "_id": "$department",
                "headcount": {"$addToSet": "$emp_code"},
                "present_days": {"$sum": {"$cond": [{"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]}, 1, 0]}},
                "work_hours": {"$avg": {"$cond": [
                    {"$and": [
                        {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$ne": ["$logs.work_hours", None]}
                    ]},
                    "$logs.work_hours", None
                ]}},
                "late_count": {"$sum": {"$cond": [{"$gt": ["$logs.late_minutes", 0]}, 1, 0]}},
                "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
                "total_overtime_minutes": {"$sum": {"$ifNull": ["$logs.overtime_minutes", 0]}},
                "leave_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}}
            }},
            {"$sort": {"_id": 1}}
        ]
        explain = db.command(
            "explain",
            {"aggregate": "employees", "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats"
        )
        collection = "employees"

    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month is required")
        validate_month(month)
        if limit < 1 or limit > 50:
            raise HTTPException(422, "Limit must be between 1 and 50")
        pipeline = [
            {"$match": {"date": {"$gte": month + "-01", "$lte": month + "-31"}, "late_minutes": {"$gt": 0}}},
            {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}}},
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
            {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
            {"$unwind": "$employee"}
        ]
        if department:
            pipeline.append({"$match": {"employee.department": department}})
        pipeline.extend([
            {"$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {"rank": {"$rank": {}}}
            }},
            {"$match": {"rank": {"$lte": limit}}},
            {"$project": {
                "_id": 0, "emp_code": "$_id", "name": "$employee.name",
                "department": "$employee.department", "total_late_minutes": 1, "rank": 1
            }},
            {"$sort": {"total_late_minutes": -1, "emp_code": 1}}
        ])
        explain = db.command(
            "explain",
            {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats"
        )
        collection = "attendance_logs"

    else:
        if not department or not from_date or not to_date:
            raise HTTPException(422, "department, from, and to are required")
        start = validate_date(from_date)
        end = validate_date(to_date)
        if end < start or (end - start).days + 1 > 92:
            raise HTTPException(422, "Invalid date range")
        if db.employees.find_one({"department": department}) is None:
            raise HTTPException(404, "Department not found")
        pipeline = build_department_trend_pipeline(department, from_date, to_date)
        explain = db.command(
            "explain",
            {"aggregate": "employees", "pipeline": pipeline, "cursor": {}},
            verbosity="executionStats"
        )
        collection = "employees"

    explain = json.loads(json_util.dumps(explain))
    return {"endpoint": endpoint, "collection": collection, "explain": explain}


