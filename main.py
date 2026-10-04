from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from database import engine, Base, SessionLocal
import models
from models import User


app = FastAPI()


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
    "http://localhost:5173",
    "https://lovevest-frontend.vercel.app"
]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


# Upload folder
Path("uploads/profile").mkdir(parents=True, exist_ok=True)


# Database
Base.metadata.create_all(bind=engine)


# Static files
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


# Create admin
def create_admin():
    db = SessionLocal()

    try:
        admin = db.query(User).filter(User.role == "admin").first()

        if admin:
            return

        admin = User(
            user_id="admin",
            name="LoveVest Admin",
            age=0,
            gender="other",
            state="system",
            email="admin@lovevest.com",
            password_hash="Admin@1234",
            relationship_status="single",
            total_partners=0,
            loyalty_score=100,
            role="admin",
            is_blocked = False,
            created_date=date.today()
        )

        db.add(admin)
        db.commit()

    except Exception as e:
        db.rollback()
        raise e

    finally:
        db.close()


create_admin()



# Routes
from routes.Admin.admin import app as admin_router
from routes.auth import app as auth_router
from routes.user import app as user_router
from routes.chat import app as chat_router
from routes.notification import app as notification_router
from routes.Admin.users import app as admin_users_router
from routes.Admin.relationships import app as admin_relationships_router
from routes.Admin import insurance
from routes.insurance import app as insurance_router, seed_insurance_plans
from routes.password_reset import app as password_reset_router






app.include_router(password_reset_router)
app.include_router(insurance_router)
app.include_router(insurance.app)
app.include_router(admin_relationships_router)
app.include_router(admin_users_router)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(user_router)
app.include_router(chat_router)
app.include_router(notification_router)


seed_insurance_plans()
