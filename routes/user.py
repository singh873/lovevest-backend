from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import or_

from database import get_db
from models import *
from auth.permissions import role_required, get_current_user
from routes.notification import notify, mark_read_for
from schemas import *
from auth.password import verify_password, hash_password

from datetime import date
import os
import shutil
from pathlib import Path

app = APIRouter()


@app.get("/user/dashboard")
def user_dashboard(current_user=Depends(role_required("user")), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == int(current_user["sub"])).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "message": "Welcome to User Dashboard",
        "user_id": user.user_id,
        "name": user.name,
        "gender": user.gender,
        "relationship_status": user.relationship_status,
        "loyalty_score": user.loyalty_score,
        "profile_image": user.profile_image,
    }

@app.get("/user/wallet")
def get_wallet(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    user_id = int(current_user["sub"])

    wallet = db.query(Wallet).filter(
        Wallet.user_id == user_id
    ).order_by(Wallet.id.desc()).first()

    if not wallet:
        raise HTTPException(
            status_code=404,
            detail="Wallet not found"
        )

    return {
        "available_balance": float(wallet.available_balance),
        "amount": float(wallet.amount),
        "transaction_type": wallet.transaction_type,
        "description": wallet.description,
        "created_date": wallet.created_date
    }


@app.get("/user/profile")
def user_profile(current_user=Depends(role_required("user")), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == int(current_user["sub"])).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "user_id": user.user_id,
        "name": user.name,
        "age": user.age,
        "gender": user.gender,
        "state": user.state,
        "email": user.email,
        "profile_image": user.profile_image,
        "relationship_status": user.relationship_status,
        "total_partners": user.total_partners,
        "loyalty_score": user.loyalty_score,
        "created_date": user.created_date
    }

@app.post("/user/profile/photo")
def upload_profile_photo(
    file: UploadFile = File(...),
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == int(current_user["sub"])).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    allowed_types = ["image/jpeg", "image/png", "image/webp"]

    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Only JPG, PNG and WEBP images are allowed")

    upload_dir = Path("uploads/profile")
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_extension = Path(file.filename).suffix.lower()
    file_name = f"user_{user.id}{file_extension}"
    file_path = upload_dir / file_name

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    user.profile_image = f"/uploads/profile/{file_name}"
    db.commit()
    db.refresh(user)

    return {
        "message": "Profile photo updated successfully",
        "profile_image": user.profile_image
    }


@app.put("/user/profile")
def update_profile(
    profile_data: ProfileUpdate,
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == int(current_user["sub"])).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = profile_data.model_dump(exclude_unset=True)

    if not update_data:
        raise HTTPException(status_code=400, detail="At least one field is required")

    if "email" in update_data:
        existing_email = db.query(User).filter(
            User.email == update_data["email"],
            User.id != user.id
        ).first()

        if existing_email:
            raise HTTPException(status_code=400, detail="Email already registered")

    for field, value in update_data.items():
        setattr(user, field, value)

    db.commit()
    db.refresh(user)

    return {
        "message": "Profile updated successfully",
        "updated_fields": update_data
    }

@app.put("/user/change-password")
def change_password(
    password_data: ChangePassword,
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(
        User.id == int(current_user["sub"])
    ).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    if not verify_password(
        password_data.current_password,
        user.password_hash
    ):
        raise HTTPException(
            status_code=400,
            detail="Current password is incorrect"
        )

    if password_data.current_password == password_data.new_password:
        raise HTTPException(
            status_code=400,
            detail="New password must be different"
        )

    user.password_hash = hash_password(
        password_data.new_password
    )

    db.commit()

    return {
        "message": "Password changed successfully"
    }

@app.get("/user/partners")
def get_partners(
    search: str = "",
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    current_user_id = int(current_user["sub"])

    query = db.query(User).filter(
        User.id != current_user_id,User.role != "admin"
    )

    if search.strip():
        search_text = f"%{search.strip()}%"

        query = query.filter(
            (User.name.ilike(search_text)) |
            (User.user_id.ilike(search_text))
        )

    users = query.all()

    result = []

    for user in users:

        request = db.query(RelationshipRequest).filter(
            RelationshipRequest.sender_id == current_user_id,
            RelationshipRequest.receiver_id == user.id
        ).order_by(
            RelationshipRequest.id.desc()
        ).first()

        result.append({
            "user_id": user.user_id,
            "name": user.name,
            "age": user.age,
            "gender": user.gender,
            "state": user.state,
            "profile_image": user.profile_image,
            "relationship_status": user.relationship_status,
            "request_status": request.status if request else None
        })

    return result


@app.get("/user/relationship/requests")
def get_relationship_requests(
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    current_user_id = int(current_user["sub"])

    requests = db.query(RelationshipRequest).filter(
        RelationshipRequest.receiver_id == current_user_id,
        RelationshipRequest.status == "pending"
    ).order_by(
        RelationshipRequest.id.desc()
    ).all()

    result = []

    for request in requests:
        sender = db.query(User).filter(
            User.id == request.sender_id
        ).first()

        if not sender:
            continue

        result.append({
            "request_id": request.id,
            "user_id": sender.user_id,
            "name": sender.name,
            "age": sender.age,
            "gender": sender.gender,
            "state": sender.state,
            "profile_image": sender.profile_image
        })

    return result

@app.post("/user/partners/invite")
def send_partner_invite(
    invite_data: PartnerInvite,
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    sender = db.query(User).filter(
        User.id == int(current_user["sub"])
    ).first()

    if not sender:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    receiver = db.query(User).filter(
        User.user_id == invite_data.user_id
    ).first()

    if not receiver:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    if sender.id == receiver.id:
        raise HTTPException(
            status_code=400,
            detail="You cannot invite yourself"
        )

    if sender.relationship_status != "single":
        raise HTTPException(
            status_code=400,
            detail="You are already in a relationship"
        )

    if receiver.relationship_status != "single":
        raise HTTPException(
            status_code=400,
            detail="This user is already in a relationship"
        )

    existing_request = db.query(RelationshipRequest).filter(
        RelationshipRequest.sender_id == sender.id,
        RelationshipRequest.receiver_id == receiver.id,
        RelationshipRequest.status == "pending"
    ).first()

    if existing_request:
        raise HTTPException(
            status_code=400,
            detail="Invitation already sent"
        )

    new_request = RelationshipRequest(
        sender_id=sender.id,
        receiver_id=receiver.id,
        status="pending",
        created_date=date.today()
    )

    db.add(new_request)
    db.commit()
    db.refresh(new_request)

    # ---- notification: jise invite gaya usko bell mein dikhega ----
    notify(
        db,
        receiver.id,
        "invite",
        "New relationship invite 💌",
        f"{sender.name} aapko partner banana chahta hai",
        actor_id=sender.id,
        ref_id=new_request.id
    )

    return {
        "message": "Invitation sent successfully"
    }

@app.put("/user/relationship/accept")
def accept_relationship_request(
    request_data: RelationshipRequestAction,
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    receiver = db.query(User).filter(
        User.id == int(current_user["sub"])
    ).first()

    if not receiver:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    request = db.query(RelationshipRequest).filter(
        RelationshipRequest.id == request_data.request_id,
        RelationshipRequest.receiver_id == receiver.id,
        RelationshipRequest.status == "pending"
    ).first()

    if not request:
        raise HTTPException(
            status_code=404,
            detail="Relationship request not found"
        )

    sender = db.query(User).filter(
        User.id == request.sender_id
    ).first()

    if not sender:
        raise HTTPException(
            status_code=404,
            detail="Sender not found"
        )

    if receiver.relationship_status != "single":
        raise HTTPException(
            status_code=400,
            detail="You are already in a relationship"
        )

    if sender.relationship_status != "single":
        raise HTTPException(
            status_code=400,
            detail="This user is already in a relationship"
        )

    previous_relationship = db.query(Couple).filter(
        (
            (Couple.partner1_id == sender.id) &
            (Couple.partner2_id == receiver.id)
        ) |
        (
            (Couple.partner1_id == receiver.id) &
            (Couple.partner2_id == sender.id)
        )
    ).first()

    couple = Couple(
        partner1_id=sender.id,
        partner2_id=receiver.id,
        relationship_start_date=date.today(),
        status="active"
    )

    db.add(couple)
    db.flush()

    sender.couple_id = couple.id
    receiver.couple_id = couple.id

    # relationship mein jaate hi status single -> Mingle
    sender.relationship_status = "Mingle"
    receiver.relationship_status = "Mingle"

    if not previous_relationship:
        sender.total_partners += 1
        receiver.total_partners += 1

    request.status = "accepted"

    db.commit()

    # ---- notification ----
    # 1) mere paas jo "invite" notification thi wo read (Accept/Reject buttons hat jayenge)
    mark_read_for(db, receiver.id, "invite", request.id)

    # 2) invite bhejne wale ko batao
    notify(
        db,
        sender.id,
        "invite_accepted",
        "Invite accepted 🎉",
        f"{receiver.name} ne aapka invite accept kar liya",
        actor_id=receiver.id,
        ref_id=request.id
    )

    return {
        "message": "Relationship request accepted",
        "couple_id": couple.id
    }


@app.put("/user/relationship/reject")
def reject_relationship_request(
    request_data: RelationshipRequestAction,
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    receiver = db.query(User).filter(
        User.id == int(current_user["sub"])
    ).first()

    if not receiver:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    request = db.query(RelationshipRequest).filter(
        RelationshipRequest.id == request_data.request_id,
        RelationshipRequest.receiver_id == receiver.id,
        RelationshipRequest.status == "pending"
    ).first()

    if not request:
        raise HTTPException(
            status_code=404,
            detail="Relationship request not found"
        )

    request.status = "rejected"

    db.commit()

    # ---- notification ----
    mark_read_for(db, receiver.id, "invite", request.id)

    notify(
        db,
        request.sender_id,
        "invite_rejected",
        "Invite declined",
        f"{receiver.name} ne aapka invite decline kar diya",
        actor_id=receiver.id,
        ref_id=request.id
    )

    return {
        "message": "Relationship request rejected"
    }


# ============================================================
# Relationship History
# ============================================================

@app.get("/user/relationship/history")
def relationship_history(
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user_id = int(current_user["sub"])

    user = db.query(User).filter(
        User.id == user_id
    ).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    couples = db.query(Couple).filter(
        (Couple.partner1_id == user_id) |
        (Couple.partner2_id == user_id)
    ).order_by(
        Couple.relationship_start_date.desc(),
        Couple.id.desc()
    ).all()

    result = []

    for couple in couples:

        if couple.partner1_id == user_id:
            partner_id = couple.partner2_id
        else:
            partner_id = couple.partner1_id

        partner = db.query(User).filter(
            User.id == partner_id
        ).first()

        if not partner:
            continue

        result.append({
            "couple_id": couple.id,
            "partner_user_id": partner.user_id,
            "partner_name": partner.name,
            "partner_profile_image": partner.profile_image,
            "relationship_start_date": couple.relationship_start_date,
            "relationship_end_date": couple.relationship_end_date,
            "status": couple.status
        })

    return result


# ============================================================
# Breakup
# ============================================================

@app.put("/user/relationship/breakup")
def breakup_relationship(
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user_id = int(current_user["sub"])

    user = db.query(User).filter(
        User.id == user_id
    ).first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    if not user.couple_id:
        raise HTTPException(
            status_code=400,
            detail="You are not in a relationship"
        )

    couple = db.query(Couple).filter(
        Couple.id == user.couple_id
    ).first()

    if not couple:
        raise HTTPException(
            status_code=404,
            detail="Active relationship not found"
        )

    if couple.status != "active":
        raise HTTPException(
            status_code=400,
            detail="Relationship is already ended"
        )

    if user_id == couple.partner1_id:
        partner_id = couple.partner2_id
    elif user_id == couple.partner2_id:
        partner_id = couple.partner1_id
    else:
        raise HTTPException(
            status_code=403,
            detail="You are not part of this relationship"
        )

    partner = db.query(User).filter(
        User.id == partner_id
    ).first()

    if not partner:
        raise HTTPException(
            status_code=404,
            detail="Partner not found"
        )

    couple.status = "ended"
    couple.relationship_end_date = date.today()

    user.relationship_status = "single"
    user.couple_id = None

    partner.relationship_status = "single"
    partner.couple_id = None

    db.commit()

    return {
        "message": "Relationship ended successfully",
        "couple_id": couple.id,
        "relationship_end_date": couple.relationship_end_date
    }





def partner_payload(u):
    return {
        "user_id": u.user_id,
        "name": u.name,
        "gender": u.gender,
        "profile_image": u.profile_image,
    }


# Current relationship + poori history  ->  GET /user/relationship
@app.get("/user/relationship")
def get_relationship(
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db),
):
    me = db.query(User).filter(User.id == int(current_user["sub"])).first()

    if not me:
        raise HTTPException(status_code=404, detail="User not found")

    couples = (
        db.query(Couple)
        .filter(or_(Couple.partner1_id == me.id, Couple.partner2_id == me.id))
        .order_by(Couple.relationship_start_date.desc())
        .all()
    )

    history = []
    current = None

    for c in couples:
        partner_id = c.partner2_id if c.partner1_id == me.id else c.partner1_id
        partner = db.query(User).filter(User.id == partner_id).first()

        if not partner:
            continue

        item = {
            "couple_id": c.id,
            "partner": partner_payload(partner),
            "start_date": c.relationship_start_date,
            "end_date": c.relationship_end_date,
            "status": c.status,          # "active" / "ended"
        }

        history.append(item)

        if c.status == "active" and current is None:
            current = item

    return {"current": current, "history": history}


# Breakup  ->  PUT /user/relationship/breakup
@app.put("/user/relationship/breakup")
def breakup(
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db),
):
    me = db.query(User).filter(User.id == int(current_user["sub"])).first()

    if not me:
        raise HTTPException(status_code=404, detail="User not found")

    if not me.couple_id:
        raise HTTPException(status_code=400, detail="You are not in a relationship")

    couple = db.query(Couple).filter(
        Couple.id == me.couple_id,
        Couple.status == "active",
    ).first()

    if not couple:
        raise HTTPException(status_code=404, detail="Active relationship not found")

    partner_id = couple.partner2_id if couple.partner1_id == me.id else couple.partner1_id
    partner = db.query(User).filter(User.id == partner_id).first()

    couple.status = "ended"
    couple.relationship_end_date = date.today()

    for u in (me, partner):
        if u:
            u.relationship_status = "single"
            u.couple_id = None

    db.commit()

    return {"message": "Relationship ended"}



# ============================================================
# Delete Account  ->  DELETE /user/profile
# (user.py ke sabse neeche paste karo)
# ============================================================

# True  -> active relationship mein ho to delete band (pehle breakup karna padega)
# False -> active hone par bhi delete ho jayega; partner automatically "single" ho jayega
BLOCK_DELETE_IF_ACTIVE_COUPLE = False


def remove_profile_image_file(path):
    # sirf project folder ke andar ki file delete hogi (uploads/profile/...)
    if not path or path.startswith("http"):
        return

    base = os.path.abspath(os.getcwd())
    full = os.path.abspath(os.path.join(base, path.lstrip("/\\")))

    if full.startswith(base + os.sep) and os.path.isfile(full):
        try:
            os.remove(full)
        except OSError:
            pass


@app.delete("/user/profile")
def delete_account(
    current_user=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == int(current_user["sub"])).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    couples = db.query(Couple).filter(
        or_(Couple.partner1_id == user.id, Couple.partner2_id == user.id)
    ).all()

    if BLOCK_DELETE_IF_ACTIVE_COUPLE and any(c.status == "active" for c in couples):
        raise HTTPException(
            status_code=400,
            detail="Pehle apna relationship end karo, uske baad account delete ho sakta hai."
        )

    image_path = user.profile_image
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
                p.id for p in db.query(InsurancePolicy.id).filter(
                    InsurancePolicy.couple_id.in_(couple_ids)
                ).all()
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
                RelationshipRequest.receiver_id == user.id
            )
        ).delete(synchronize_session=False)

        # notifications: meri apni + jo mere kaaran dusron ko gayi (user delete se pehle hatana zaroori)
        db.query(Notification).filter(
            or_(
                Notification.user_id == user.id,
                Notification.actor_id == user.id
            )
        ).delete(synchronize_session=False)

        db.query(Wallet).filter(Wallet.user_id == user.id).delete(synchronize_session=False)

        db.query(User).filter(User.id == user.id).delete(synchronize_session=False)

        db.commit()

    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Account delete nahi ho paya.")

    remove_profile_image_file(image_path)

    return {"message": "Account deleted successfully"}