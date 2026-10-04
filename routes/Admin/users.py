"""
routes/Admin/users.py  -  Admin: manage users

GET    /admin/users                 list + search + filters + sort + pagination (+ stats)
GET    /admin/users/{id}            full details (profile, wallet, relationships, activity)
POST   /admin/users                 add user
PUT    /admin/users/{id}/block      block
PUT    /admin/users/{id}/unblock    unblock
PUT    /admin/users/bulk-status     block / unblock many
DELETE /admin/users/{id}            delete user and all related data
"""

import math
import os
from datetime import date
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database import get_db
from models import *
from auth.permissions import role_required
from auth.password import hash_password


app = APIRouter()


# Wallet transaction types jo paisa kam karte hain (amount ko "-" dikhane ke liye)
DEBIT_KEYWORDS = ("premium", "debit", "withdraw", "invest", "purchase", "payment")

# frontend ke sort keys -> DB columns (sirf yahi allowed hain)
SORT_COLUMNS = {
    "id": User.id,
    "name": User.name,
    "email": User.email,
    "gender": User.gender,
    "age": User.age,
    "relationship": User.relationship_status,
    "loyalty": User.loyalty_score,
    "status": User.is_blocked,
    "joined": User.created_date,
}


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _row(u: User) -> dict:
    return {
        "id": u.id,
        "user_id": u.user_id,
        "name": u.name,
        "email": u.email,
        "gender": u.gender,
        "age": u.age,
        "state": u.state,
        "profile_image": u.profile_image,
        "relationship_status": u.relationship_status,
        "loyalty_score": u.loyalty_score,
        "total_partners": u.total_partners,
        "status": "blocked" if u.is_blocked else "active",
        "role": u.role,
        "created_date": u.created_date,
    }


def _get_user_or_404(db: Session, user_pk: int) -> User:
    user = db.query(User).filter(User.id == user_pk, User.role == "user").first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user


def _signed_amount(w: Wallet) -> float:
    amount = float(w.amount)

    if amount < 0:
        return amount

    t = (w.transaction_type or "").lower()

    return -amount if any(k in t for k in DEBIT_KEYWORDS) else amount


def _label(text: str) -> str:
    return (text or "").replace("_", " ").title()


def _stats(db: Session) -> dict:
    total = db.query(func.count(User.id)).filter(User.role == "user").scalar() or 0

    blocked = (
        db.query(func.count(User.id))
        .filter(User.role == "user", User.is_blocked.is_(True))
        .scalar()
        or 0
    )

    new_this_month = (
        db.query(func.count(User.id))
        .filter(User.role == "user", User.created_date >= date.today().replace(day=1))
        .scalar()
        or 0
    )

    return {
        "total": total,
        "active": total - blocked,
        "blocked": blocked,
        "new_this_month": new_this_month,
    }


def _admin_name(db: Session, current_user) -> str:
    admin = db.query(User).filter(User.id == int(current_user["sub"])).first()
    return admin.name if admin else "Admin"


# ---------------------------------------------------------------------
# GET /admin/users
# ---------------------------------------------------------------------

@app.get("/admin/users")
def list_users(
    search: str = "",
    gender: str = "",
    relationship: str = "",
    status: str = "",
    sort: str = "joined",
    order: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db),
):
    q = db.query(User).filter(User.role == "user")

    text = search.strip()

    if text:
        like = f"%{text}%"
        conditions = [
            User.name.ilike(like),
            User.email.ilike(like),
            User.user_id.ilike(like),
            User.state.ilike(like),
        ]

        if text.isdigit():
            conditions.append(User.id == int(text))

        q = q.filter(or_(*conditions))

    if gender.strip():
        q = q.filter(func.lower(User.gender) == gender.strip().lower())

    if relationship.strip():
        q = q.filter(User.relationship_status == relationship.strip())

    if status == "active":
        q = q.filter(User.is_blocked.is_(False))
    elif status == "blocked":
        q = q.filter(User.is_blocked.is_(True))

    total = q.count()

    column = SORT_COLUMNS.get(sort, User.created_date)
    column = column.asc() if order == "asc" else column.desc()

    users = (
        q.order_by(column, User.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "admin": {"name": _admin_name(db, current_user)},
        "items": [_row(u) for u in users],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, math.ceil(total / page_size)),
        "stats": _stats(db),
    }


# ---------------------------------------------------------------------
# PUT /admin/users/bulk-status   (/{id} wale routes se pehle likha hai)
# ---------------------------------------------------------------------

class BulkStatus(BaseModel):
    ids: List[int] = Field(min_length=1, max_length=200)
    blocked: bool


@app.put("/admin/users/bulk-status")
def bulk_status(
    data: BulkStatus,
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db),
):
    updated = (
        db.query(User)
        .filter(User.id.in_(data.ids), User.role == "user")
        .update({User.is_blocked: data.blocked}, synchronize_session=False)
    )

    db.commit()

    return {"updated": updated}


# ---------------------------------------------------------------------
# GET /admin/users/{id}
# ---------------------------------------------------------------------

@app.get("/admin/users/{user_pk}")
def user_detail(
    user_pk: int,
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db),
):
    u = _get_user_or_404(db, user_pk)

    # ---- wallet ----
    wallet_rows = (
        db.query(Wallet)
        .filter(Wallet.user_id == u.id)
        .order_by(Wallet.id.desc())
        .limit(10)
        .all()
    )

    wallet = {
        "balance": float(wallet_rows[0].available_balance) if wallet_rows else 0,
        "transactions": [
            {
                "type": _label(w.transaction_type),
                "description": w.description,
                "amount": _signed_amount(w),
                "date": w.created_date,
            }
            for w in wallet_rows
        ],
    }

    # ---- relationships + activity ----
    activity = [{"title": "Joined LoveVest", "date": u.created_date}]

    couples = (
        db.query(Couple)
        .filter(or_(Couple.partner1_id == u.id, Couple.partner2_id == u.id))
        .order_by(Couple.relationship_start_date.desc(), Couple.id.desc())
        .all()
    )

    relationships = []

    for c in couples:
        partner_id = c.partner2_id if c.partner1_id == u.id else c.partner1_id
        partner = db.get(User, partner_id)

        if not partner:
            continue

        relationships.append({
            "partner_name": partner.name,
            "partner_user_id": partner.user_id,
            "partner_gender": partner.gender,
            "partner_profile_image": partner.profile_image,
            "start_date": c.relationship_start_date,
            "end_date": c.relationship_end_date,
            "status": c.status,
        })

        activity.append({
            "title": f"Started a relationship with {partner.name}",
            "date": c.relationship_start_date,
        })

        if c.relationship_end_date:
            activity.append({
                "title": f"Relationship with {partner.name} ended",
                "date": c.relationship_end_date,
            })

    for w in wallet_rows:
        sign = "-" if _signed_amount(w) < 0 else "+"

        activity.append({
            "title": f"{_label(w.transaction_type)} {sign}\u20b9{abs(float(w.amount)):,.0f}",
            "date": w.created_date,
        })

    activity.sort(key=lambda a: a["date"], reverse=True)

    return {
        **_row(u),
        "wallet": wallet,
        "relationships": relationships,
        "activity": activity[:15],
    }


# ---------------------------------------------------------------------
# POST /admin/users   (Add User)
# ---------------------------------------------------------------------

class AdminUserCreate(BaseModel):
    name: str = Field(min_length=2, max_length=50)
    user_id: str = Field(min_length=3, max_length=30, pattern=r"^[A-Za-z0-9_.]+$")
    email: str = Field(max_length=100, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=6, max_length=100)
    age: int = Field(ge=18, le=100)
    gender: str
    state: str = Field(min_length=2, max_length=50)

    @field_validator("gender")
    @classmethod
    def valid_gender(cls, v):
        v = v.strip().lower()

        if v not in ("male", "female", "other"):
            raise ValueError("Gender must be male, female or other")

        return v


@app.post("/admin/users")
def add_user(
    data: AdminUserCreate,
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db),
):
    email = data.email.strip().lower()
    user_id = data.user_id.strip()

    if db.query(User).filter(func.lower(User.email) == email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    if db.query(User).filter(func.lower(User.user_id) == user_id.lower()).first():
        raise HTTPException(status_code=400, detail="User ID already taken")

    user = User(
        user_id=user_id,
        name=data.name.strip(),
        age=data.age,
        gender=data.gender,
        state=data.state.strip(),
        email=email,
        password_hash=hash_password(data.password),
        relationship_status="single",
        total_partners=0,
        loyalty_score=100,
        role="user",
        is_blocked=False,
        created_date=date.today(),
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return {"message": "User added successfully", "user": _row(user)}


# ---------------------------------------------------------------------
# PUT /admin/users/{id}/block  &  /unblock
# ---------------------------------------------------------------------

def _set_blocked(db: Session, user_pk: int, blocked: bool):
    user = _get_user_or_404(db, user_pk)

    user.is_blocked = blocked
    db.commit()

    return {
        "message": "User blocked" if blocked else "User unblocked",
        "status": "blocked" if blocked else "active",
    }


@app.put("/admin/users/{user_pk}/block")
def block_user(
    user_pk: int,
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db),
):
    return _set_blocked(db, user_pk, True)


@app.put("/admin/users/{user_pk}/unblock")
def unblock_user(
    user_pk: int,
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db),
):
    return _set_blocked(db, user_pk, False)


# ---------------------------------------------------------------------
# DELETE /admin/users/{id}
# ---------------------------------------------------------------------

def _remove_profile_image(path):
    if not path or path.startswith("http"):
        return

    base = os.path.abspath(os.getcwd())
    full = os.path.abspath(os.path.join(base, path.lstrip("/\\")))

    if full.startswith(base + os.sep) and os.path.isfile(full):
        try:
            os.remove(full)
        except OSError:
            pass


@app.delete("/admin/users/{user_pk}")
def delete_user(
    user_pk: int,
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db),
):
    user = _get_user_or_404(db, user_pk)

    image_path = user.profile_image

    couples = (
        db.query(Couple)
        .filter(or_(Couple.partner1_id == user.id, Couple.partner2_id == user.id))
        .all()
    )

    couple_ids = [c.id for c in couples]

    partner_ids = {
        pid
        for c in couples
        for pid in (c.partner1_id, c.partner2_id)
        if pid != user.id
    }

    try:
        if couple_ids:
            policy_ids = [
                p.id
                for p in db.query(InsurancePolicy.id)
                .filter(InsurancePolicy.couple_id.in_(couple_ids))
                .all()
            ]

            if policy_ids:
                db.query(Reward).filter(Reward.policy_id.in_(policy_ids)).delete(synchronize_session=False)
                db.query(InsuranceFund).filter(InsuranceFund.policy_id.in_(policy_ids)).delete(synchronize_session=False)
                db.query(PremiumPayment).filter(PremiumPayment.policy_id.in_(policy_ids)).delete(synchronize_session=False)
                db.query(InsurancePolicy).filter(InsurancePolicy.id.in_(policy_ids)).delete(synchronize_session=False)

            db.query(Message).filter(Message.couple_id.in_(couple_ids)).delete(synchronize_session=False)

            db.query(User).filter(User.couple_id.in_(couple_ids)).update(
                {User.couple_id: None}, synchronize_session=False
            )

            db.query(Couple).filter(Couple.id.in_(couple_ids)).delete(synchronize_session=False)

        if partner_ids:
            db.query(User).filter(User.id.in_(partner_ids)).update(
                {User.relationship_status: "single"}, synchronize_session=False
            )

        db.query(Reward).filter(Reward.user_id == user.id).delete(synchronize_session=False)
        db.query(PremiumPayment).filter(PremiumPayment.user_id == user.id).delete(synchronize_session=False)

        db.query(Message).filter(
            or_(Message.sender_id == user.id, Message.receiver_id == user.id)
        ).delete(synchronize_session=False)

        db.query(RelationshipRequest).filter(
            or_(
                RelationshipRequest.sender_id == user.id,
                RelationshipRequest.receiver_id == user.id,
            )
        ).delete(synchronize_session=False)

        db.query(Wallet).filter(Wallet.user_id == user.id).delete(synchronize_session=False)

        db.query(User).filter(User.id == user.id).delete(synchronize_session=False)

        db.commit()

    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="User delete nahi ho paya.")

    _remove_profile_image(image_path)

    return {"message": "User deleted successfully"}