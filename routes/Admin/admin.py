from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from database import get_db
from models import *
from auth.permissions import role_required


app = APIRouter()


# Wallet transaction types jo paisa kam karte hain (amount ko "-" dikhane ke liye).
# Apne transaction_type ke naam ke hisaab se badal sakte ho.
DEBIT_KEYWORDS = ("premium", "debit", "withdraw", "invest", "purchase", "payment")


def _pct_change(current, previous):
    """Pichhle mahine ke muqable % growth."""
    current = float(current or 0)
    previous = float(previous or 0)

    if previous == 0:
        return 100 if current > 0 else 0

    return round((current - previous) / previous * 100)


def _month_start(d: date) -> date:
    return d.replace(day=1)


# ---------------------------------------------------------------------
# Stats helpers  (value abhi + value pichhle mahine ke shuru tak)
# ---------------------------------------------------------------------

def _count_users(db: Session, before=None) -> int:
    q = db.query(func.count(User.id)).filter(User.role == "user")

    if before:
        q = q.filter(User.created_date < before)

    return q.scalar() or 0


def _count_active_relationships(db: Session, before=None) -> int:
    q = db.query(func.count(Couple.id))

    if before:
        # us din tak shuru ho chuke aur tab tak end nahi hue the
        q = q.filter(
            Couple.relationship_start_date < before,
            (Couple.relationship_end_date.is_(None))
            | (Couple.relationship_end_date >= before),
        )
    else:
        q = q.filter(Couple.status == "active")

    return q.scalar() or 0


def _total_virtual_money(db: Session, before=None) -> float:
    """Har user ki latest wallet entry ka available_balance, sab ka jod."""
    latest = (
        db.query(func.max(Wallet.id).label("mid"))
        .join(User, User.id == Wallet.user_id)
        .filter(User.role == "user")
    )

    if before:
        latest = latest.filter(Wallet.created_date < before)

    sub = latest.group_by(Wallet.user_id).subquery()

    total = (
        db.query(func.coalesce(func.sum(Wallet.available_balance), 0))
        .join(sub, Wallet.id == sub.c.mid)
        .scalar()
    )

    return round(float(total or 0), 2)


def _count_active_policies(db: Session, before=None) -> int:
    q = db.query(func.count(InsurancePolicy.id)).filter(
        InsurancePolicy.status == "active"
    )

    if before:
        q = q.filter(InsurancePolicy.start_date < before)

    return q.scalar() or 0


def _trading_fund(db: Session, before=None) -> float:
    # Trading ka alag model nahi hai, isliye InsuranceFund ka total use kiya hai.
    q = db.query(func.coalesce(func.sum(InsuranceFund.amount), 0))

    if before:
        q = q.filter(InsuranceFund.fund_date < before)

    return round(float(q.scalar() or 0), 2)


def _stat(now_value, before_value):
    return {"value": now_value, "change": _pct_change(now_value, before_value)}


# ---------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------

def _user_growth(db: Session, months: int = 12):
    """Har mahine kitne naye users aaye (pichhle `months` mahine)."""
    first = _month_start(date.today())

    y, m = first.year, first.month - (months - 1)

    while m <= 0:
        m += 12
        y -= 1

    start = date(y, m, 1)

    year_expr = extract("year", User.created_date)
    month_expr = extract("month", User.created_date)

    rows = (
        db.query(year_expr, month_expr, func.count(User.id))
        .filter(User.role == "user", User.created_date >= start)
        .group_by(year_expr, month_expr)
        .all()
    )

    counts = {(int(r[0]), int(r[1])): r[2] for r in rows}

    result = []
    y, m = start.year, start.month

    for _ in range(months):
        result.append({
            "label": date(y, m, 1).strftime("%b"),
            "value": counts.get((y, m), 0),
        })

        m += 1

        if m > 12:
            m = 1
            y += 1

    return result


def _relationship_status(db: Session):
    rows = (
        db.query(User.relationship_status, func.count(User.id))
        .filter(User.role == "user")
        .group_by(User.relationship_status)
        .all()
    )

    counts = {"single": 0, "in_relationship": 0, "mingle": 0, "other": 0}

    for status, count in rows:
        key = status if status in counts else "other"
        counts[key] += count

    return [
        {"key": "single",          "label": "Single",          "count": counts["single"]},
        {"key": "in_relationship", "label": "In Relationship", "count": counts["in_relationship"]},
        {"key": "mingle",          "label": "Mingle",          "count": counts["mingle"]},
        {"key": "other",           "label": "Other",           "count": counts["other"]},
    ]


# ---------------------------------------------------------------------
# Recent tables
# ---------------------------------------------------------------------

def _person(u: User):
    return {
        "name": u.name,
        "gender": u.gender,
        "profile_image": u.profile_image,
    }


def _recent_users(db: Session):
    users = (
        db.query(User)
        .filter(User.role == "user")
        .order_by(User.created_date.desc(), User.id.desc())
        .limit(5)
        .all()
    )

    return [
        {
            "user_id": u.user_id,
            "name": u.name,
            "email": u.email,
            "gender": u.gender,
            "profile_image": u.profile_image,
            "created_date": u.created_date,
        }
        for u in users
    ]


def _recent_relationships(db: Session):
    couples = (
        db.query(Couple)
        .order_by(Couple.relationship_start_date.desc(), Couple.id.desc())
        .limit(5)
        .all()
    )

    result = []

    for c in couples:
        u1 = db.get(User, c.partner1_id)
        u2 = db.get(User, c.partner2_id)

        if not u1 or not u2:
            continue

        result.append({
            "user1": _person(u1),
            "user2": _person(u2),
            "status": "in_relationship" if c.status == "active" else "ended",
            "since": c.relationship_start_date,
        })

    return result


def _signed_amount(w: Wallet) -> float:
    amount = float(w.amount)

    if amount < 0:
        return amount

    t = (w.transaction_type or "").lower()

    if any(k in t for k in DEBIT_KEYWORDS):
        return -amount

    return amount


def _recent_transactions(db: Session):
    rows = (
        db.query(Wallet, User)
        .join(User, User.id == Wallet.user_id)
        .order_by(Wallet.id.desc())
        .limit(5)
        .all()
    )

    return [
        {
            "name": u.name,
            "gender": u.gender,
            "profile_image": u.profile_image,
            "type": (w.transaction_type or "").replace("_", " ").title(),
            "amount": _signed_amount(w),
            "date": w.created_date,
        }
        for w, u in rows
    ]


# ---------------------------------------------------------------------
# GET /admin/dashboard
# ---------------------------------------------------------------------

@app.get("/admin/dashboard")
def admin_dashboard(
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db),
):
    admin = db.query(User).filter(User.id == int(current_user["sub"])).first()

    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    prev = _month_start(date.today())  # pichhle mahine ke end tak ka data

    return {
        "admin": {"name": admin.name},

        "stats": {
            "total_users": _stat(_count_users(db), _count_users(db, prev)),
            "active_relationships": _stat(
                _count_active_relationships(db),
                _count_active_relationships(db, prev),
            ),
            "total_virtual_money": _stat(
                _total_virtual_money(db), _total_virtual_money(db, prev)
            ),
            "active_policies": _stat(
                _count_active_policies(db), _count_active_policies(db, prev)
            ),
            "trading_fund": _stat(_trading_fund(db), _trading_fund(db, prev)),
        },

        "user_growth": _user_growth(db, 12),
        "relationship_status": _relationship_status(db),
        "recent_users": _recent_users(db),
        "recent_relationships": _recent_relationships(db),
        "recent_transactions": _recent_transactions(db),
    }