from datetime import datetime

from sqlalchemy import Column, Integer, String, Date, Numeric, ForeignKey, Boolean,DateTime
from sqlalchemy.orm import relationship
from database import Base



class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(30), unique=True, nullable=False)
    name = Column(String(50), nullable=False)
    age = Column(Integer, nullable=False)
    gender = Column(String(20), nullable=False)
    state = Column(String(50), nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    profile_image = Column(String(255), nullable=True)
    relationship_status = Column(String(20), default="single", nullable=False)
    total_partners = Column(Integer, default=0, nullable=False)
    couple_id = Column(Integer, nullable=True)
    loyalty_score = Column(Integer, default=100, nullable=False)
    role = Column(String(20), default="user", nullable=False)
    created_date = Column(Date, nullable=False)
    is_blocked = Column(Boolean, default=False, nullable=False)

    wallets = relationship("Wallet", backref="user", cascade="all, delete-orphan")


class Couple(Base):
    __tablename__ = "couples"

    id = Column(Integer, primary_key=True)
    partner1_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    partner2_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    relationship_start_date = Column(Date, nullable=False)
    relationship_end_date = Column(Date, nullable=True)
    status = Column(String(20), default="active", nullable=False)


class RelationshipRequest(Base):
    __tablename__ = "relationship_requests"

    id = Column(Integer, primary_key=True)
    sender_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    receiver_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(20), default="pending", nullable=False)
    created_date = Column(Date, nullable=False)


class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    transaction_type = Column(String(30), nullable=False)
    description = Column(String(255), nullable=True)
    available_balance = Column(Numeric(15, 2), nullable=False)
    created_date = Column(Date, nullable=False)

class InsurancePlan(Base):
    __tablename__ = "insurance_plans"

    id = Column(Integer, primary_key=True)
    plan_name = Column(String(100), nullable=False)
    policy_amount = Column(Numeric(15, 2), nullable=False)
    monthly_premium = Column(Numeric(15, 2), nullable=False)
    duration_months = Column(Integer, nullable=False)
    reward_percentage = Column(Numeric(5, 2), nullable=False)
    description = Column(String(500), nullable=True)
    status = Column(String(20), default="active", nullable=False)
    created_date = Column(Date, nullable=False)

class InsurancePolicy(Base):
    __tablename__ = "insurance_policies"

    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("insurance_plans.id"), nullable=False)
    couple_id = Column(Integer, ForeignKey("couples.id"), nullable=False)
    start_date = Column(Date, nullable=False)
    maturity_date = Column(Date, nullable=False)
    status = Column(String(20), default="active", nullable=False)

    plan = relationship("InsurancePlan", backref="policies")
    couple = relationship("Couple", backref="insurance_policies")

class Reward(Base):
    __tablename__ = "rewards"

    id = Column(Integer, primary_key=True)
    policy_id = Column(Integer, ForeignKey("insurance_policies.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    reward_amount = Column(Numeric(15, 2), nullable=False)
    reward_date = Column(Date, nullable=False)
    status = Column(String(20), default="pending", nullable=False)

    policy = relationship("InsurancePolicy", backref="rewards")
    user = relationship("User", backref="rewards")

class InsuranceFund(Base):
    __tablename__ = "insurance_funds"

    id = Column(Integer, primary_key=True)
    policy_id = Column(Integer, ForeignKey("insurance_policies.id", ondelete="CASCADE"), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    fund_date = Column(Date, nullable=False)
    description = Column(String(255), nullable=True)
    policy = relationship("InsurancePolicy", backref="InsuranceFunds")


class PremiumPayment(Base):
    __tablename__ = "premium_payments"

    id = Column(Integer, primary_key=True)
    policy_id = Column(Integer, ForeignKey("insurance_policies.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    payment_date = Column(Date, nullable=False)
    status = Column(String(20), default="pending", nullable=False)

    policy = relationship("InsurancePolicy", backref="premium_payments")
    user = relationship("User", backref="premium_payments")

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    couple_id = Column(Integer, ForeignKey("couples.id", ondelete="CASCADE"), nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    receiver_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    message = Column(String(1000), nullable=False)
    sent_date = Column(DateTime, nullable=False)
    is_read = Column(Boolean, default=False, nullable=False)

    couple = relationship("Couple", backref="messages")
    sender = relationship("User", foreign_keys=[sender_id], backref="sent_messages")
    receiver = relationship("User", foreign_keys=[receiver_id], backref="received_messages")


# =====================================================================
# NOTIFICATIONS  (naya - bell icon ke liye)
# =====================================================================

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)

    # jise notification milegi
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # jisne trigger kiya (message / invite bhejne wala) - photo aur naam dikhane ke liye
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)

    # "message" | "invite" | "invite_accepted" | "invite_rejected"
    type = Column(String(30), nullable=False)

    title = Column(String(120), nullable=False)
    body = Column(String(300), nullable=True)

    # message -> couple_id  |  invite / accepted / rejected -> RelationshipRequest.id
    ref_id = Column(Integer, nullable=True)

    # ek chat ke kai unread messages 1 notification mein collapse hote hain
    count = Column(Integer, default=1, nullable=False)

    is_read = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class PasswordReset(Base):
    __tablename__ = "password_resets"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, nullable=False)