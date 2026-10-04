from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import get_db
from models import (
    Couple,
    InsurancePlan,
    InsurancePolicy,
    PremiumPayment,
    Reward,
    InsuranceFund,
    User
)
from auth.permissions import role_required


app = APIRouter()


# =========================
# HELPERS
# =========================

def get_plan_or_404(plan_id: int, db: Session):

    plan = db.query(InsurancePlan).filter(
        InsurancePlan.id == plan_id
    ).first()

    if not plan:
        raise HTTPException(
            status_code=404,
            detail="Insurance plan not found"
        )

    return plan


def get_policy_or_404(policy_id: int, db: Session):

    policy = db.query(InsurancePolicy).filter(
        InsurancePolicy.id == policy_id
    ).first()

    if not policy:
        raise HTTPException(
            status_code=404,
            detail="Insurance policy not found"
        )

    return policy


def couple_data(couple, db):

    partner1 = db.query(User).filter(
        User.id == couple.partner1_id
    ).first()

    partner2 = db.query(User).filter(
        User.id == couple.partner2_id
    ).first()

    return {
        "couple_id": couple.id,

        "partner1": {
            "id": partner1.id if partner1 else None,
            "user_id": partner1.user_id if partner1 else None,
            "name": partner1.name if partner1 else None,
            "profile_image": partner1.profile_image if partner1 else None
        },

        "partner2": {
            "id": partner2.id if partner2 else None,
            "user_id": partner2.user_id if partner2 else None,
            "name": partner2.name if partner2 else None,
            "profile_image": partner2.profile_image if partner2 else None
        },

        "status": couple.status
    }


def plan_data(plan):

    return {
        "id": plan.id,
        "plan_name": plan.plan_name,
        "policy_amount": float(plan.policy_amount),
        "monthly_premium": float(plan.monthly_premium),
        "duration_months": plan.duration_months,
        "reward_percentage": float(plan.reward_percentage),
        "description": plan.description,
        "status": plan.status,
        "created_date": plan.created_date
    }


def policy_data(policy, db):

    couple = db.query(Couple).filter(
        Couple.id == policy.couple_id
    ).first()

    plan = db.query(InsurancePlan).filter(
        InsurancePlan.id == policy.plan_id
    ).first()

    return {
        "id": policy.id,
        "plan_id": policy.plan_id,
        "couple_id": policy.couple_id,
        "start_date": policy.start_date,
        "maturity_date": policy.maturity_date,
        "status": policy.status,

        "plan": plan_data(plan) if plan else None,

        "couple": (
            couple_data(couple, db)
            if couple
            else None
        )
    }


# =========================
# PLAN SCHEMAS
# =========================

class InsurancePlanCreate(BaseModel):

    plan_name: str = Field(
        min_length=2,
        max_length=100
    )

    policy_amount: float = Field(gt=0)

    monthly_premium: float = Field(gt=0)

    duration_months: int = Field(
        gt=0,
        le=120
    )

    reward_percentage: float = Field(
        ge=0,
        le=100
    )

    description: str | None = Field(
        default=None,
        max_length=500
    )

    status: str = "active"


class InsurancePlanUpdate(BaseModel):

    plan_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=100
    )

    policy_amount: float | None = Field(
        default=None,
        gt=0
    )

    monthly_premium: float | None = Field(
        default=None,
        gt=0
    )

    duration_months: int | None = Field(
        default=None,
        gt=0,
        le=120
    )

    reward_percentage: float | None = Field(
        default=None,
        ge=0,
        le=100
    )

    description: str | None = Field(
        default=None,
        max_length=500
    )

    status: str | None = None


class PlanStatusUpdate(BaseModel):

    status: str


# =========================
# SUMMARY
# =========================

@app.get("/admin/insurance/summary")
def insurance_summary(
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db)
):

    total_plans = db.query(
        InsurancePlan
    ).count()

    active_plans = db.query(
        InsurancePlan
    ).filter(
        InsurancePlan.status == "active"
    ).count()

    total_policies = db.query(
        InsurancePolicy
    ).count()

    active_policies = db.query(
        InsurancePolicy
    ).filter(
        InsurancePolicy.status == "active"
    ).count()

    matured_policies = db.query(
        InsurancePolicy
    ).filter(
        InsurancePolicy.status == "matured"
    ).count()

    cancelled_policies = db.query(
        InsurancePolicy
    ).filter(
        InsurancePolicy.status == "cancelled"
    ).count()

    total_premium = sum(
        float(value[0] or 0)
        for value in db.query(
            PremiumPayment.amount
        ).filter(
            PremiumPayment.status == "paid"
        ).all()
    )

    total_rewards = sum(
        float(value[0] or 0)
        for value in db.query(
            Reward.reward_amount
        ).filter(
            Reward.status == "paid"
        ).all()
    )

    return {
        "total_plans": total_plans,
        "active_plans": active_plans,

        "total_policies": total_policies,
        "active_policies": active_policies,
        "matured_policies": matured_policies,
        "cancelled_policies": cancelled_policies,

        "total_premium_collected": total_premium,
        "total_rewards_paid": total_rewards
    }


# =========================
# INSURANCE PLANS
# =========================

@app.get("/admin/insurance/plans")
def get_insurance_plans(
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db)
):

    query = db.query(InsurancePlan)

    if search:

        search_value = f"%{search}%"

        query = query.filter(
            or_(
                InsurancePlan.plan_name.ilike(search_value),
                InsurancePlan.description.ilike(search_value)
            )
        )

    if status and status != "all":

        query = query.filter(
            InsurancePlan.status == status
        )

    plans = query.order_by(
        InsurancePlan.id.desc()
    ).all()

    return [
        plan_data(plan)
        for plan in plans
    ]


@app.get("/admin/insurance/plans/{plan_id}")
def get_insurance_plan(
    plan_id: int,
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db)
):

    plan = get_plan_or_404(
        plan_id,
        db
    )

    return plan_data(plan)


@app.post("/admin/insurance/plans")
def create_insurance_plan(
    plan_data_input: InsurancePlanCreate,
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db)
):

    if plan_data_input.status not in [
        "active",
        "inactive"
    ]:
        raise HTTPException(
            status_code=400,
            detail="Invalid plan status"
        )

    plan = InsurancePlan(
        plan_name=plan_data_input.plan_name,
        policy_amount=plan_data_input.policy_amount,
        monthly_premium=plan_data_input.monthly_premium,
        duration_months=plan_data_input.duration_months,
        reward_percentage=plan_data_input.reward_percentage,
        description=plan_data_input.description,
        status=plan_data_input.status,
        created_date=date.today()
    )

    db.add(plan)
    db.commit()
    db.refresh(plan)

    return {
        "message": "Insurance plan created successfully",
        "plan": plan_data(plan)
    }


@app.put("/admin/insurance/plans/{plan_id}")
def update_insurance_plan(
    plan_id: int,
    plan_data_input: InsurancePlanUpdate,
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db)
):

    plan = get_plan_or_404(
        plan_id,
        db
    )

    update_data = plan_data_input.model_dump(
        exclude_unset=True
    )

    if not update_data:

        raise HTTPException(
            status_code=400,
            detail="At least one field is required"
        )

    if "status" in update_data:

        if update_data["status"] not in [
            "active",
            "inactive"
        ]:
            raise HTTPException(
                status_code=400,
                detail="Invalid plan status"
            )

    for field, value in update_data.items():

        setattr(
            plan,
            field,
            value
        )

    db.commit()
    db.refresh(plan)

    return {
        "message": "Insurance plan updated successfully",
        "plan": plan_data(plan)
    }


@app.put("/admin/insurance/plans/{plan_id}/status")
def update_plan_status(
    plan_id: int,
    status_data: PlanStatusUpdate,
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db)
):

    if status_data.status not in [
        "active",
        "inactive"
    ]:
        raise HTTPException(
            status_code=400,
            detail="Invalid plan status"
        )

    plan = get_plan_or_404(
        plan_id,
        db
    )

    plan.status = status_data.status

    db.commit()
    db.refresh(plan)

    return {
        "message": "Plan status updated successfully",
        "status": plan.status
    }


@app.delete("/admin/insurance/plans/{plan_id}")
def delete_insurance_plan(
    plan_id: int,
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db)
):

    plan = get_plan_or_404(
        plan_id,
        db
    )

    existing_policy = db.query(
        InsurancePolicy
    ).filter(
        InsurancePolicy.plan_id == plan.id
    ).first()

    if existing_policy:

        raise HTTPException(
            status_code=400,
            detail="This plan has purchased policies and cannot be deleted"
        )

    db.delete(plan)
    db.commit()

    return {
        "message": "Insurance plan deleted successfully"
    }


# =========================
# PURCHASED POLICIES
# =========================

@app.get("/admin/insurance/policies")
def get_purchased_policies(
    search: str | None = Query(default=None),
    status: str | None = Query(default=None),
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db)
):

    query = db.query(
        InsurancePolicy
    )

    if search:

        search_value = f"%{search}%"

        matching_users = db.query(
            User.id
        ).filter(
            or_(
                User.name.ilike(search_value),
                User.user_id.ilike(search_value),
                User.email.ilike(search_value)
            )
        ).all()

        user_ids = [
            row[0]
            for row in matching_users
        ]

        matching_couples = []

        if user_ids:

            matching_couples = db.query(
                Couple.id
            ).filter(
                or_(
                    Couple.partner1_id.in_(user_ids),
                    Couple.partner2_id.in_(user_ids)
                )
            ).all()

        couple_ids = [
            row[0]
            for row in matching_couples
        ]

        matching_plans = db.query(
            InsurancePlan.id
        ).filter(
            or_(
                InsurancePlan.plan_name.ilike(
                    search_value
                ),
                InsurancePlan.description.ilike(
                    search_value
                )
            )
        ).all()

        plan_ids = [
            row[0]
            for row in matching_plans
        ]

        search_conditions = []

        if couple_ids:
            search_conditions.append(
                InsurancePolicy.couple_id.in_(
                    couple_ids
                )
            )

        if plan_ids:
            search_conditions.append(
                InsurancePolicy.plan_id.in_(
                    plan_ids
                )
            )

        if search.isdigit():

            search_id = int(search)

            search_conditions.append(
                InsurancePolicy.id == search_id
            )

            search_conditions.append(
                InsurancePolicy.couple_id == search_id
            )

            search_conditions.append(
                InsurancePolicy.plan_id == search_id
            )

        if search_conditions:

            query = query.filter(
                or_(*search_conditions)
            )

        else:

            return []

    if status and status != "all":

        query = query.filter(
            InsurancePolicy.status == status
        )

    policies = query.order_by(
        InsurancePolicy.id.desc()
    ).all()

    return [
        policy_data(
            policy,
            db
        )
        for policy in policies
    ]


@app.get("/admin/insurance/policies/{policy_id}")
def get_purchased_policy(
    policy_id: int,
    current_user=Depends(role_required("admin")),
    db: Session = Depends(get_db)
):

    policy = get_policy_or_404(
        policy_id,
        db
    )

    couple = db.query(
        Couple
    ).filter(
        Couple.id == policy.couple_id
    ).first()

    plan = db.query(
        InsurancePlan
    ).filter(
        InsurancePlan.id == policy.plan_id
    ).first()

    payments = db.query(
        PremiumPayment
    ).filter(
        PremiumPayment.policy_id == policy.id
    ).order_by(
        PremiumPayment.payment_date.desc()
    ).all()

    rewards = db.query(
        Reward
    ).filter(
        Reward.policy_id == policy.id
    ).order_by(
        Reward.reward_date.desc()
    ).all()

    funds = db.query(
        InsuranceFund
    ).filter(
        InsuranceFund.policy_id == policy.id
    ).order_by(
        InsuranceFund.fund_date.desc()
    ).all()

    return {
        "id": policy.id,

        "plan_id": policy.plan_id,
        "couple_id": policy.couple_id,

        "start_date": policy.start_date,
        "maturity_date": policy.maturity_date,
        "status": policy.status,

        "plan": (
            plan_data(plan)
            if plan
            else None
        ),

        "couple": (
            couple_data(couple, db)
            if couple
            else None
        ),

        "payments": [
            {
                "id": payment.id,
                "user_id": payment.user_id,
                "amount": float(payment.amount),
                "payment_date": payment.payment_date,
                "status": payment.status
            }
            for payment in payments
        ],

        "rewards": [
            {
                "id": reward.id,
                "user_id": reward.user_id,
                "reward_amount": float(
                    reward.reward_amount
                ),
                "reward_date": reward.reward_date,
                "status": reward.status
            }
            for reward in rewards
        ],

        "funds": [
            {
                "id": fund.id,
                "amount": float(fund.amount),
                "fund_date": fund.fund_date,
                "description": fund.description
            }
            for fund in funds
        ]
    }