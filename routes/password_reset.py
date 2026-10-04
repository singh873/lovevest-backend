"""
routes/password_reset.py  -  Forgot / Reset password

POST /forgot-password        email do -> reset link email par jata hai
GET  /reset-password/check   link abhi valid hai ya nahi
POST /reset-password         token + naya password -> password badal jata hai
"""

import hashlib
import os
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from dotenv import load_dotenv

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import *
from auth.password import hash_password


load_dotenv()

app = APIRouter()


TOKEN_MINUTES = 30
RESEND_SECONDS = 60

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")

GENERIC_MESSAGE = "If this email is registered, a password reset link has been sent."


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _send_reset_email(to_email: str, name: str, link: str):
    """
    SMTP settings environment variables se aati hain:
      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, MAIL_FROM
    """

    host = os.getenv("SMTP_HOST")

    if not host:
        print(f"\n[DEV] Password reset link for {to_email}:\n{link}\n")
        return

    sender = os.getenv("MAIL_FROM") or os.getenv("SMTP_USER") or "no-reply@lovevest.com"

    msg = EmailMessage()
    msg["Subject"] = "Reset your LoveVest password"
    msg["From"] = sender
    msg["To"] = to_email

    msg.set_content(
        f"Hi {name},\n\n"
        f"We received a request to reset your LoveVest password.\n"
        f"Click the link below to choose a new password "
        f"(valid for {TOKEN_MINUTES} minutes):\n\n{link}\n\n"
        f"If you did not ask for this, you can safely ignore this email."
    )

    msg.add_alternative(
        f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto">
          <h2 style="color:#e23a80">LoveVest</h2>

          <p>Hi {name},</p>

          <p>
            We received a request to reset your password.
            Click the button below to choose a new one.
            This link is valid for {TOKEN_MINUTES} minutes.
          </p>

          <p>
            <a href="{link}"
               style="display:inline-block;
                      padding:12px 22px;
                      background:#e23a80;
                      color:#fff;
                      text-decoration:none;
                      border-radius:10px;
                      font-weight:bold">
              Reset Password
            </a>
          </p>

          <p style="color:#667085;font-size:13px">
            If you did not ask for this, you can safely ignore this email.
          </p>
        </div>
        """,
        subtype="html",
    )

    try:
        with smtplib.SMTP(
            host,
            int(os.getenv("SMTP_PORT", "587")),
            timeout=15
        ) as server:

            server.starttls()

            user = os.getenv("SMTP_USER")

            if user:
                server.login(
                    user,
                    os.getenv("SMTP_PASSWORD", "")
                )

            server.send_message(msg)

            print(f"[MAIL] Password reset email sent to {to_email}")

    except Exception as e:
        print(f"[MAIL ERROR] could not send reset email: {e}")


def _valid_reset(db: Session, token: str):
    record = (
        db.query(PasswordReset)
        .filter(PasswordReset.token_hash == _hash(token))
        .first()
    )

    if not record or record.used or record.expires_at < _now():
        return None

    return record


# ---------------------------------------------------------------------
# POST /forgot-password
# ---------------------------------------------------------------------

class ForgotPassword(BaseModel):
    email: str = Field(
        max_length=100,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )


@app.post("/forgot-password")
def forgot_password(
    data: ForgotPassword,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    email = data.email.strip().lower()

    user = db.query(User).filter(
        func.lower(User.email) == email
    ).first()

    if user:
        recent = (
            db.query(PasswordReset)
            .filter(
                PasswordReset.user_id == user.id,
                PasswordReset.created_at > _now() - timedelta(seconds=RESEND_SECONDS),
            )
            .first()
        )

        if not recent:

            db.query(PasswordReset).filter(
                PasswordReset.user_id == user.id,
                PasswordReset.used.is_(False),
            ).update(
                {PasswordReset.used: True},
                synchronize_session=False
            )

            raw_token = secrets.token_urlsafe(32)

            db.add(
                PasswordReset(
                    user_id=user.id,
                    token_hash=_hash(raw_token),
                    expires_at=_now() + timedelta(minutes=TOKEN_MINUTES),
                    used=False,
                    created_at=_now(),
                )
            )

            db.commit()

            link = f"{FRONTEND_URL}/reset-password?token={raw_token}"

            background_tasks.add_task(
                _send_reset_email,
                user.email,
                user.name,
                link
            )

    return {"message": GENERIC_MESSAGE}


# ---------------------------------------------------------------------
# GET /reset-password/check
# ---------------------------------------------------------------------

@app.get("/reset-password/check")
def check_reset_token(
    token: str,
    db: Session = Depends(get_db)
):
    if not _valid_reset(db, token):
        raise HTTPException(
            status_code=400,
            detail="This reset link is invalid or has expired.",
        )

    return {"valid": True}


# ---------------------------------------------------------------------
# POST /reset-password
# ---------------------------------------------------------------------

class ResetPassword(BaseModel):
    token: str = Field(
        min_length=10,
        max_length=200
    )

    new_password: str = Field(
        min_length=6,
        max_length=100
    )


@app.post("/reset-password")
def reset_password(
    data: ResetPassword,
    db: Session = Depends(get_db)
):
    record = _valid_reset(db, data.token)

    if not record:
        raise HTTPException(
            status_code=400,
            detail="This reset link is invalid or has expired.",
        )

    user = db.get(User, record.user_id)

    if not user:
        raise HTTPException(
            status_code=400,
            detail="This reset link is invalid or has expired.",
        )

    user.password_hash = hash_password(data.new_password)

    db.query(PasswordReset).filter(
        PasswordReset.user_id == user.id,
        PasswordReset.used.is_(False),
    ).update(
        {PasswordReset.used: True},
        synchronize_session=False
    )

    db.commit()

    return {
        "message": "Password reset successfully. You can now log in."
    }