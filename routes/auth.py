from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import *
from schemas import UserRegister, UserLogin
from auth.password import hash_password, verify_password
from auth.jwt import create_access_token
from datetime import date
from fastapi import APIRouter

app = APIRouter()


@app.post("/register")
def register(user_data: UserRegister, db: Session = Depends(get_db)):
    existing_email = db.query(User).filter(User.email == user_data.email).first()

    if existing_email:
        raise HTTPException(status_code=400, detail="Email already registered")

    existing_user_id = db.query(User).filter(User.user_id == user_data.user_id).first()

    if existing_user_id:
        raise HTTPException(status_code=400, detail="User ID already taken")

    new_user = User(
        name=user_data.name,
        age=user_data.age,
        gender=user_data.gender,
        state=user_data.state,
        user_id=user_data.user_id,
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        relationship_status="single",
        total_partners=0,
        loyalty_score=100,
        role="user",
        created_date=date.today()
    )

    db.add(new_user)
    db.flush()

    new_wallet = Wallet(
        user_id=new_user.id,
        amount=20000,
        transaction_type="initial_bonus",
        description="Welcome bonus",
        available_balance=20000,
        created_date=date.today()
    )

    db.add(new_wallet)
    db.commit()
    db.refresh(new_user)

    return {
        "message": "User registered successfully",
        "user_id": new_user.user_id
    }

@app.post("/login")
def login(user_data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == user_data.email).first()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(user_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if user.is_blocked:
        raise HTTPException(
            status_code=403,
            detail="Your account has been blocked. Please contact support."
        )

    token = create_access_token(user.id, user.role)

    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role
    }