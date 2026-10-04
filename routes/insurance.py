"""
LoveVest - Insurance  (routes/insurance.py)

Tumhare models ke hisaab se (InsurancePlan table + InsurancePolicy.plan_id):
    InsurancePlan, InsurancePolicy, PremiumPayment, InsuranceFund, Reward, Wallet

Endpoints:
    GET  /user/insurance/plans                    -> plan catalog (DB se) + can_buy / owned
    POST /user/insurance/policies                 -> plan lena   {"plan_id": 1}
    GET  /user/insurance/policies                 -> meri / couple ki policies + wallet balance
    GET  /user/insurance/policies/{id}            -> ek policy ki details + payments history
    POST /user/insurance/policies/{id}/pay        -> ek installment (monthly premium) pay karo
    GET  /user/insurance/transactions             -> wallet transactions (premium, reward, bonus ...)

Startup par agar insurance_plans table khaali ho to seed_insurance_plans() 3 default plans daal deta hai.

Rules (simple rakhe hain):
    * Policy couple ke naam hoti hai, plan lene ke liye ACTIVE relationship zaroori.
    * Plan lene par policy ban jati hai; pehla premium "due" hota hai (Pay Premium se bharo).
    * Premium wallet se katta hai (dono partners mein se koi bhi pay kar sakta hai).
    * Saare installments pay hote hi policy "completed" ho jati hai aur
      reward = total premium x reward_percentage / 100, dono partners ko aadha-aadha wallet mein jata hai.
    * Relationship end hone par policy "cancelled" dikhti hai.
    * Policy ki amount / premium / duration plan se aati hai - plan edit karoge to policy mein bhi badlega.
"""

import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import get_db, SessionLocal
from models import (
    User, Couple, InsurancePlan, InsurancePolicy, PremiumPayment,
    InsuranceFund, Reward, Wallet
)

from auth.permissions import role_required
from routes.notification import notify


app = APIRouter()

DEBUG = True


def _log(*args):
    if DEBUG:
        print("[INSURANCE]", *args)


# ============================================================
# Default plans (sirf pehli baar, jab table khaali ho)
# ============================================================

DEFAULT_PLANS = [
    {
        "plan_name": "Love Secure Plan",
        "description": "For a stronger tomorrow",
        "policy_amount": 50000,
        "monthly_premium": 2000,
        "duration_months": 12,
        "reward_percentage": 105,
    },
    {
        "plan_name": "Together Forever Plan",
        "description": "For long-term commitment",
        "policy_amount": 100000,
        "monthly_premium": 3500,
        "duration_months": 24,
        "reward_percentage": 110,
    },
    {
        "plan_name": "Golden Bond Plan",
        "description": "Maximum rewards for your journey",
        "policy_amount": 200000,
        "monthly_premium": 5000,
        "duration_months": 36,
        "reward_percentage": 120,
    },
]


def seed_insurance_plans():
    """main.py se startup par call hota hai. Table khaali ho tabhi plans daalta hai."""
    db = SessionLocal()

    try:
        if db.query(InsurancePlan).count() > 0:
            return

        for p in DEFAULT_PLANS:
            db.add(
                InsurancePlan(
                    **p,
                    status="active",
                    created_date=date.today()
                )
            )

        db.commit()

        _log("default plans seeded:", len(DEFAULT_PLANS))

    except Exception as e:
        db.rollback()
        _log("seed ERROR:", repr(e))
        raise

    finally:
        db.close()


# plan ke saath icon DB mein store nahi hota - id se (heart / crown / star) ghoomta hai
PLAN_ICONS = ["heart", "crown", "star"]


def _icon(plan: InsurancePlan) -> str:
    return PLAN_ICONS[(plan.id - 1) % len(PLAN_ICONS)]


# ============================================================
# Small helpers
# ============================================================

def _uid(current) -> int:
    return int(current["sub"])


def _d(value) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _code(policy_id: int) -> str:
    return f"LV{policy_id:03d}"


def _add_months(d: date, months: int) -> date:
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _months_elapsed(start: date, today: date) -> int:
    months = (today.year - start.year) * 12 + (today.month - start.month)

    if today.day < start.day:
        months -= 1

    return months


def _reward_total(total_paid: Decimal, percent) -> Decimal:
    """Reward ka formula - yahin badal sakte ho."""
    return (total_paid * _d(percent) / Decimal(100)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _active_couple(db: Session, user: User):
    if not user or not user.couple_id:
        return None

    couple = db.get(Couple, user.couple_id)

    return couple if couple and couple.status == "active" else None


def _partner_id(couple: Couple, user_id: int) -> int:
    return couple.partner2_id if couple.partner1_id == user_id else couple.partner1_id


# ============================================================
# Wallet helpers
# ============================================================

def _balance(db: Session, user_id: int) -> Decimal:
    row = (
        db.query(Wallet)
        .filter(Wallet.user_id == user_id)
        .order_by(Wallet.id.desc())
        .first()
    )

    return _d(row.available_balance) if row else Decimal("0")


def _wallet_add(db: Session, user_id: int, delta: Decimal, kind: str, description: str) -> Decimal:
    """delta + ho to credit, - ho to debit. Running balance wali nayi row banati hai."""
    new_balance = _balance(db, user_id) + delta

    db.add(
        Wallet(
            user_id=user_id,
            amount=abs(delta),
            transaction_type=kind,
            description=description,
            available_balance=new_balance,
            created_date=date.today()
        )
    )

    db.flush()   # agle _balance() ko ye row dikhe

    return new_balance


# ============================================================
# Policy view (list + detail dono yahi use karte hain)
# ============================================================

def _paid_stats(db: Session, policy_id: int):
    rows = (
        db.query(PremiumPayment)
        .filter(
            PremiumPayment.policy_id == policy_id,
            PremiumPayment.status == "paid"
        )
        .all()
    )

    return len(rows), sum((_d(r.amount) for r in rows), Decimal("0"))


def _view(db: Session, policy: InsurancePolicy) -> dict:
    couple = db.get(Couple, policy.couple_id)
    plan = db.get(InsurancePlan, policy.plan_id)

    duration = plan.duration_months
    monthly = _d(plan.monthly_premium)
    total = monthly * duration

    paid_count, paid_amount = _paid_stats(db, policy.id)

    today = date.today()

    # kitne installments ab tak "due" ho chuke (pehla policy start par hi due hota hai)
    due_count = min(duration, max(0, _months_elapsed(policy.start_date, today)) + 1)

    if policy.status == "completed" or paid_count >= duration:
        status = "completed"
    elif policy.status == "cancelled" or not couple or couple.status != "active":
        status = "cancelled"
    elif paid_count < due_count:
        status = "pending_payment"
    else:
        status = "active"

    return {
        "id": policy.id,
        "code": _code(policy.id),
        "plan_id": plan.id,
        "name": plan.plan_name,
        "icon": _icon(plan),
        "policy_amount": float(plan.policy_amount),
        "monthly_premium": float(monthly),
        "duration_months": duration,
        "total_premium": float(total),
        "premium_paid": float(paid_amount),
        "paid_count": paid_count,
        "progress": int(round(paid_amount / total * 100)) if total else 0,
        "start_date": policy.start_date,
        "maturity_date": policy.maturity_date,
        "next_due_date": _add_months(policy.start_date, paid_count) if paid_count < duration else None,
        "reward_percent": float(plan.reward_percentage),
        "expected_reward": float(_reward_total(total, plan.reward_percentage)),
        "status": status,       # active | pending_payment | completed | cancelled
        "can_pay": status in ("active", "pending_payment")
    }


def _get_my_policy(db: Session, policy_id: int, user_id: int):
    policy = db.get(InsurancePolicy, policy_id)

    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    couple = db.get(Couple, policy.couple_id)

    if not couple or user_id not in (couple.partner1_id, couple.partner2_id):
        raise HTTPException(status_code=404, detail="Policy not found")

    return policy, couple


# ============================================================
# 1) Plan catalog   GET /user/insurance/plans
# ============================================================

@app.get("/user/insurance/plans")
def get_plans(
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user = db.get(User, _uid(current_user))

    couple = _active_couple(db, user)

    owned_plan_ids = set()

    if couple:
        policies = (
            db.query(InsurancePolicy)
            .filter(InsurancePolicy.couple_id == couple.id)
            .all()
        )

        for p in policies:
            if _view(db, p)["status"] in ("active", "pending_payment"):
                owned_plan_ids.add(p.plan_id)

    plans = (
        db.query(InsurancePlan)
        .filter(InsurancePlan.status == "active")
        .order_by(InsurancePlan.id.asc())
        .all()
    )

    result = []

    for plan in plans:
        total = _d(plan.monthly_premium) * plan.duration_months

        result.append({
            "id": plan.id,
            "name": plan.plan_name,
            "tagline": plan.description or "",
            "icon": _icon(plan),
            "policy_amount": float(plan.policy_amount),
            "monthly_premium": float(plan.monthly_premium),
            "duration_months": plan.duration_months,
            "reward_percent": float(plan.reward_percentage),
            "total_premium": float(total),
            "expected_reward": float(_reward_total(total, plan.reward_percentage)),
            "owned": plan.id in owned_plan_ids
        })

    return {
        "can_buy": bool(couple),
        "reason": None if couple else "Active relationship required to buy a plan.",
        "plans": result
    }


# ============================================================
# 2) Plan lena   POST /user/insurance/policies   {"plan_id": 1}
# ============================================================

class BuyPlan(BaseModel):
    plan_id: int


@app.post("/user/insurance/policies")
def buy_plan(
    data: BuyPlan,
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user_id = _uid(current_user)

    user = db.get(User, user_id)

    couple = _active_couple(db, user)

    if not couple:
        raise HTTPException(
            status_code=400,
            detail="Insurance lene ke liye aap relationship mein hone chahiye"
        )

    plan = db.get(InsurancePlan, data.plan_id)

    if not plan or plan.status != "active":
        raise HTTPException(status_code=404, detail="Plan not found")

    existing = (
        db.query(InsurancePolicy)
        .filter(
            InsurancePolicy.couple_id == couple.id,
            InsurancePolicy.plan_id == plan.id,
            InsurancePolicy.status == "active"
        )
        .all()
    )

    for p in existing:
        if _view(db, p)["status"] in ("active", "pending_payment"):
            raise HTTPException(
                status_code=400,
                detail="Ye plan aapke relationship ke liye pehle se active hai"
            )

    today = date.today()

    policy = InsurancePolicy(
        plan_id=plan.id,
        couple_id=couple.id,
        start_date=today,
        maturity_date=_add_months(today, plan.duration_months),
        status="active"
    )

    db.add(policy)
    db.commit()
    db.refresh(policy)

    _log("policy created:", policy.id, plan.plan_name, "couple", couple.id)

    notify(
        db,
        _partner_id(couple, user_id),
        "insurance",
        "New insurance plan 🛡️",
        f"{user.name} ne {plan.plan_name} le liya. Pehla premium bharna baaki hai.",
        actor_id=user_id,
        ref_id=policy.id
    )

    return {
        "message": "Policy ban gayi! Ab pehla premium pay karo.",
        "policy": _view(db, policy)
    }


# ============================================================
# 3) Meri policies   GET /user/insurance/policies
# ============================================================

@app.get("/user/insurance/policies")
def my_policies(
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user_id = _uid(current_user)

    couple_ids = [
        c.id
        for c in db.query(Couple)
        .filter(or_(Couple.partner1_id == user_id, Couple.partner2_id == user_id))
        .all()
    ]

    policies = []

    if couple_ids:
        policies = (
            db.query(InsurancePolicy)
            .filter(InsurancePolicy.couple_id.in_(couple_ids))
            .order_by(InsurancePolicy.id.desc())
            .all()
        )

    return {
        "wallet_balance": float(_balance(db, user_id)),
        "policies": [_view(db, p) for p in policies]
    }


# ============================================================
# 4) Policy details   GET /user/insurance/policies/{id}
# ============================================================

@app.get("/user/insurance/policies/{policy_id}")
def policy_detail(
    policy_id: int,
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user_id = _uid(current_user)

    policy, couple = _get_my_policy(db, policy_id, user_id)

    payments = (
        db.query(PremiumPayment)
        .filter(PremiumPayment.policy_id == policy.id)
        .order_by(PremiumPayment.id.desc())
        .all()
    )

    names = {
        u.id: u.name
        for u in db.query(User)
        .filter(User.id.in_([couple.partner1_id, couple.partner2_id]))
        .all()
    }

    return {
        **_view(db, policy),
        "partners": [names.get(couple.partner1_id, ""), names.get(couple.partner2_id, "")],
        "payments": [
            {
                "id": p.id,
                "amount": float(p.amount),
                "payment_date": p.payment_date,
                "status": p.status,
                "paid_by": names.get(p.user_id, "")
            }
            for p in payments
        ]
    }


# ============================================================
# 5) Premium pay   POST /user/insurance/policies/{id}/pay
# ============================================================

@app.post("/user/insurance/policies/{policy_id}/pay")
def pay_premium(
    policy_id: int,
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user_id = _uid(current_user)

    user = (
        db.query(User)
        .filter(User.id == user_id)
        .with_for_update()
        .first()
    )

    policy, couple = _get_my_policy(db, policy_id, user_id)

    if couple.status != "active":
        raise HTTPException(
            status_code=400,
            detail="Relationship active nahi hai, premium pay nahi ho sakta"
        )

    view = _view(db, policy)

    if not view["can_pay"]:
        raise HTTPException(
            status_code=400,
            detail="Is policy ka premium ab pay nahi ho sakta"
        )

    today = date.today()

    # Premium sirf month ki 1 se 7 tarik tak pay hoga
    if today.day < 1 or today.day > 7:
        raise HTTPException(
            status_code=400,
            detail="Premium sirf har month ki 1 se 7 tarik ke beech pay kar sakte hain"
        )

    # Is month me already premium paid hai ya nahi
    existing_payment = (
        db.query(PremiumPayment)
        .filter(
            PremiumPayment.policy_id == policy.id,
            PremiumPayment.user_id == user_id,
            PremiumPayment.payment_date >= today.replace(day=1),
            PremiumPayment.payment_date <= today
        )
        .first()
    )

    if existing_payment:
        raise HTTPException(
            status_code=400,
            detail="Is month ka premium already paid hai"
        )

    plan = db.get(InsurancePlan, policy.plan_id)

    amount = _d(plan.monthly_premium)
    balance = _balance(db, user_id)

    if balance < amount:
        raise HTTPException(
            status_code=400,
            detail=f"Wallet balance kam hai. Available: ₹{int(balance):,}"
        )

    paid_count, paid_amount = _paid_stats(db, policy.id)

    n = paid_count + 1
    duration = plan.duration_months
    code = _code(policy.id)

    db.add(
        PremiumPayment(
            policy_id=policy.id,
            user_id=user_id,
            amount=amount,
            payment_date=today,
            status="paid"
        )
    )

    _wallet_add(
        db,
        user_id,
        -amount,
        "insurance_premium",
        f"Premium {n}/{duration} - {plan.plan_name} (#{code})"
    )

    db.add(
        InsuranceFund(
            policy_id=policy.id,
            amount=amount,
            fund_date=today,
            description=f"Installment {n}/{duration} by {user.name}"
        )
    )

    completed = n >= duration
    reward_total = None

    if completed:
        policy.status = "completed"

        reward_total = _reward_total(
            paid_amount + amount,
            plan.reward_percentage
        )

        half = (
            reward_total / 2
        ).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )

        for pid in (couple.partner1_id, couple.partner2_id):
            db.add(
                Reward(
                    policy_id=policy.id,
                    user_id=pid,
                    reward_amount=half,
                    reward_date=today,
                    status="paid"
                )
            )

            _wallet_add(
                db,
                pid,
                half,
                "insurance_reward",
                f"Reward - {plan.plan_name} (#{code})"
            )

    db.commit()

    _log(
        "premium paid:",
        {
            "policy": policy.id,
            "user": user_id,
            "n": n,
            "of": duration,
            "completed": completed
        }
    )

    partner_id = _partner_id(couple, user_id)

    if completed:
        text = f"{plan.plan_name} complete ho gaya! Reward wallet mein aa gaya."

        notify(
            db,
            partner_id,
            "insurance",
            "Policy completed 🎉",
            text,
            actor_id=user_id,
            ref_id=policy.id
        )

        notify(
            db,
            user_id,
            "insurance",
            "Policy completed 🎉",
            text,
            actor_id=partner_id,
            ref_id=policy.id
        )

    else:
        notify(
            db,
            partner_id,
            "insurance",
            "Premium paid 💸",
            f"{user.name} ne {plan.plan_name} ka premium ₹{int(amount):,} bhar diya ({n}/{duration})",
            actor_id=user_id,
            ref_id=policy.id
        )

    return {
        "message": (
            f"Policy complete! Reward ₹{int(reward_total):,} dono ke wallet mein aa gaya."
            if completed
            else f"₹{int(amount):,} premium pay ho gaya ({n}/{duration})"
        ),
        "policy": _view(db, policy),
        "wallet_balance": float(_balance(db, user_id))
    }

# ============================================================
# 6) Transactions   GET /user/insurance/transactions
#    (wallet ki saari entries - bonus, premium, reward)
# ============================================================

@app.get("/user/insurance/transactions")
def my_transactions(
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user_id = _uid(current_user)

    rows = (
        db.query(Wallet)
        .filter(Wallet.user_id == user_id)
        .order_by(Wallet.id.asc())
        .all()
    )

    items = []
    previous = Decimal("0")

    for row in rows:
        balance = _d(row.available_balance)

        # credit / debit balance ke badlav se pata chalta hai (transaction_type ke naam par depend nahi)
        direction = "credit" if balance >= previous else "debit"
        previous = balance

        items.append({
            "id": row.id,
            "type": row.transaction_type,
            "title": row.description or row.transaction_type,
            "amount": float(row.amount),
            "direction": direction,
            "balance_after": float(balance),
            "date": row.created_date
        })

    items.reverse()   # naye upar

    return {
        "wallet_balance": float(_balance(db, user_id)),
        "items": items
    }