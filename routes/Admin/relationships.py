"""
routes/Admin/relationships.py  -  Admin: view and manage couples

GET /admin/relationships        list + search + filters + sort + pagination (+ stats)
GET /admin/relationships/{id}   full details of one couple
"""

import calendar
import math
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, aliased

from database import get_db
from models import *
from auth.permissions import role_required


app = APIRouter()


# PremiumPayment ke jin status ko "contribution" maana jayega.
# Aapke DB mein paid ka naam alag ho to yahan badal do.
PAID_STATUSES = ("paid", "completed", "success")

SORT_COLUMNS = {
    "id": Couple.id,
    "start": Couple.relationship_start_date,
    "end": Couple.relationship_end_date,
    "status": Couple.status,
}


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _status(c: Couple) -> str:
    return "active" if c.status == "active" else "ended"


def _partner(u: User) -> dict:
    return {
        "id": u.id,
        "user_id": u.user_id,
        "name": u.name,
        "email": u.email,
        "gender": u.gender,
        "profile_image": u.profile_image,
    }


def _partner_full(u: User) -> dict:
    return {
        **_partner(u),
        "age": u.age,
        "state": u.state,
        "loyalty_score": u.loyalty_score,
        "total_partners": u.total_partners,
        "relationship_status": u.relationship_status,
        "account_status": "blocked" if u.is_blocked else "active",
        "created_date": u.created_date,
    }


def _pct_change(current, previous):
    current = float(current or 0)
    previous = float(previous or 0)

    if previous == 0:
        return 100 if current > 0 else 0

    return round((current - previous) / previous * 100)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _duration(start: date, end: Optional[date]) -> str:
    """'2 months 5 days' jaisa text (end na ho to aaj tak)."""
    end = end or date.today()

    if end < start:
        end = start

    months = (end.year - start.year) * 12 + (end.month - start.month)

    if end.day < start.day:
        months -= 1

    months = max(months, 0)

    idx = start.month - 1 + months
    ay, am = start.year + idx // 12, idx % 12 + 1
    anchor = date(ay, am, min(start.day, calendar.monthrange(ay, am)[1]))

    days = (end - anchor).days
    years, mons = divmod(months, 12)

    parts = []

    if years:
        parts.append(_plural(years, "year"))

    if mons:
        parts.append(_plural(mons, "month"))

    if days or not parts:
        parts.append(_plural(days, "day"))

    return " ".join(parts)


def _admin_name(db: Session, current_user) -> str:
    admin = db.query(User).filter(User.id == int(current_user["sub"])).first()
    return admin.name if admin else "Admin"


def _count(db: Session, *filters) -> int:
    return db.query(func.count(Couple.id)).filter(*filters).scalar() or 0


def _stats(db: Session) -> dict:
    today = date.today()
    this_month = today.replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)

    total = _count(db)
    active = _count(db, Couple.status == "active")
    ended = total - active

    new_this = _count(db, Couple.relationship_start_date >= this_month)

    new_last = _count(
        db,
        Couple.relationship_start_date >= last_month,
        Couple.relationship_start_date < this_month,
    )

    # mahine ke shuru mein kitne active / ended the
    active_before = _count(
        db,
        Couple.relationship_start_date < this_month,
        or_(
            Couple.relationship_end_date.is_(None),
            Couple.relationship_end_date >= this_month,
        ),
    )

    ended_before = _count(
        db,
        Couple.status != "active",
        Couple.relationship_end_date < this_month,
    )

    return {
        "total": total,
        "active": {"value": active, "change": _pct_change(active, active_before)},
        "ended": {"value": ended, "change": _pct_change(ended, ended_before)},
        "new_this_month": {"value": new_this, "change": _pct_change(new_this, new_last)},
    }


# ---------------------------------------------------------------------
# GET /admin/relationships
# ---------------------------------------------------------------------

@app.get("/admin/relationships")
def list_relationships(
    search: str = "",
    status: str = "",
    date_range: str = "",
    sort: str = "start",
    order: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db),
):
    P1 = aliased(User)
    P2 = aliased(User)

    q = (
        db.query(Couple, P1, P2)
        .join(P1, P1.id == Couple.partner1_id)
        .join(P2, P2.id == Couple.partner2_id)
    )

    text = search.strip()

    if text:
        like = f"%{text}%"

        conditions = [
            P1.name.ilike(like), P1.email.ilike(like), P1.user_id.ilike(like),
            P2.name.ilike(like), P2.email.ilike(like), P2.user_id.ilike(like),
        ]

        if text.isdigit():
            conditions.append(Couple.id == int(text))

        q = q.filter(or_(*conditions))

    if status == "active":
        q = q.filter(Couple.status == "active")
    elif status == "ended":
        q = q.filter(Couple.status != "active")

    today = date.today()

    start_from = {
        "this_month": today.replace(day=1),
        "last_30_days": today - timedelta(days=30),
        "last_3_months": today - timedelta(days=90),
        "this_year": date(today.year, 1, 1),
    }.get(date_range)

    if start_from:
        q = q.filter(Couple.relationship_start_date >= start_from)

    total = q.with_entities(func.count(Couple.id)).scalar() or 0

    column = SORT_COLUMNS.get(sort, Couple.relationship_start_date)
    column = column.asc() if order == "asc" else column.desc()

    rows = (
        q.order_by(column.nulls_last(), Couple.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "admin": {"name": _admin_name(db, current_user)},
        "items": [
            {
                "couple_id": c.id,
                "partner1": _partner(a),
                "partner2": _partner(b),
                "start_date": c.relationship_start_date,
                "end_date": c.relationship_end_date,
                "status": _status(c),
            }
            for c, a, b in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, math.ceil(total / page_size)),
        "stats": _stats(db),
    }


# ---------------------------------------------------------------------
# GET /admin/relationships/{couple_id}
# ---------------------------------------------------------------------

@app.get("/admin/relationships/{couple_id}")
def relationship_detail(
    couple_id: int,
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db),
):
    couple = db.get(Couple, couple_id)

    if not couple:
        raise HTTPException(status_code=404, detail="Relationship not found")

    p1 = db.get(User, couple.partner1_id)
    p2 = db.get(User, couple.partner2_id)

    if not p1 or not p2:
        raise HTTPException(status_code=404, detail="Partner not found")

    # total contributions = is couple ki policies ke paid premiums ka jod
    policy_ids = [
        p.id
        for p in db.query(InsurancePolicy.id)
        .filter(InsurancePolicy.couple_id == couple.id)
        .all()
    ]

    contributions = 0
    last_payment = None

    if policy_ids:
        contributions = (
            db.query(func.coalesce(func.sum(PremiumPayment.amount), 0))
            .filter(
                PremiumPayment.policy_id.in_(policy_ids),
                func.lower(PremiumPayment.status).in_(PAID_STATUSES),
            )
            .scalar()
        )

        last_payment = (
            db.query(func.max(PremiumPayment.payment_date))
            .filter(PremiumPayment.policy_id.in_(policy_ids))
            .scalar()
        )

    # last activity = start / end / last message / last payment mein se sabse naya
    dates = [couple.relationship_start_date]

    if couple.relationship_end_date:
        dates.append(couple.relationship_end_date)

    last_message = (
        db.query(func.max(Message.sent_date))
        .filter(Message.couple_id == couple.id)
        .scalar()
    )

    if last_message:
        dates.append(last_message.date())

    if last_payment:
        dates.append(last_payment)

    return {
        "couple_id": couple.id,
        "status": _status(couple),
        "start_date": couple.relationship_start_date,
        "end_date": couple.relationship_end_date,
        "duration": _duration(couple.relationship_start_date, couple.relationship_end_date),
        "partner1": _partner_full(p1),
        "partner2": _partner_full(p2),
        "total_contributions": round(float(contributions or 0), 2),
        "last_activity": max(dates),
    }