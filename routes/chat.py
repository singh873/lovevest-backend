from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from jose import jwt

from database import get_db, SessionLocal
from models import User, Couple, Message

from auth.permissions import role_required

from routes.notification import notify, mark_read_for, push_unread_count
from auth.jwt import SECRET_KEY, ALGORITHM


app = APIRouter()


# ============================================================
# Helper: Get current user ID from JWT
# ============================================================

def _uid(current):
    if isinstance(current, dict):
        value = current.get("sub")
    else:
        value = getattr(current, "id", None)

    if value is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    return int(value)


# ============================================================
# Helper: Current UTC time
# ============================================================

def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ============================================================
# Helper: Message response
# ============================================================

def _out(message: Message):
    return {
        "id": message.id,
        "sender_id": message.sender_id,
        "receiver_id": message.receiver_id,
        "message": message.message,
        "sent_date": message.sent_date.isoformat(),
        "is_read": message.is_read
    }


# ============================================================
# Helper: Get couple information (ACTIVE couple only - user.couple_id se)
# Sirf WebSocket chat / naye message bhejne ke liye use hota hai.
# ============================================================

def _get_couple_context(db: Session, user_id: int, require_active: bool):
    user = db.get(User, user_id)

    if not user or not user.couple_id:
        raise HTTPException(
            status_code=403,
            detail="You are not in a couple"
        )

    couple = db.get(Couple, user.couple_id)

    if not couple:
        raise HTTPException(
            status_code=403,
            detail="Couple not found"
        )

    if user_id not in (couple.partner1_id, couple.partner2_id):
        raise HTTPException(
            status_code=403,
            detail="Not allowed"
        )

    if require_active and couple.status != "active":
        raise HTTPException(
            status_code=403,
            detail="Your relationship is not active"
        )

    if user_id == couple.partner1_id:
        partner_id = couple.partner2_id
    else:
        partner_id = couple.partner1_id

    return couple, partner_id


# ============================================================
# Helper: Koi bhi couple jisme user partner hai (active ya ended)
# Purani chat history dekhne ke liye - breakup ke baad bhi chalta hai
# (breakup par user.couple_id None ho jata hai, isliye couples table se dhundhte hain)
# ============================================================

def _get_my_couple(db: Session, user_id: int, couple_id: Optional[int] = None):
    query = db.query(Couple).filter(
        or_(
            Couple.partner1_id == user_id,
            Couple.partner2_id == user_id
        )
    )

    if couple_id is not None:
        couple = query.filter(Couple.id == couple_id).first()
    else:
        # default: active couple, nahi to sabse recent wala
        couple = (
            query.filter(Couple.status == "active").first()
            or query.order_by(
                Couple.relationship_start_date.desc(),
                Couple.id.desc()
            ).first()
        )

    if not couple:
        raise HTTPException(
            status_code=403,
            detail="You are not in a couple"
        )

    if user_id == couple.partner1_id:
        partner_id = couple.partner2_id
    else:
        partner_id = couple.partner1_id

    return couple, partner_id


# ============================================================
# WebSocket Connection Manager
# ============================================================

class ConnectionManager:

    def __init__(self):
        self._connections = {}

    async def connect(self, user_id: int, websocket: WebSocket):
        await websocket.accept()

        if user_id not in self._connections:
            self._connections[user_id] = set()

        self._connections[user_id].add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket):
        sockets = self._connections.get(user_id)

        if sockets:
            sockets.discard(websocket)

            if not sockets:
                del self._connections[user_id]

    async def send_to_user(self, user_id: int, data: dict):
        sockets = list(self._connections.get(user_id, set()))

        for websocket in sockets:
            try:
                await websocket.send_json(data)
            except Exception:
                self.disconnect(user_id, websocket)


manager = ConnectionManager()


# ============================================================
# Get Chat List  (active + ended - purani history bhi)
# ============================================================

@app.get("/user/chats")
def get_chats(
    current=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user_id = _uid(current)

    couples = (
        db.query(Couple)
        .filter(
            or_(
                Couple.partner1_id == user_id,
                Couple.partner2_id == user_id
            )
        )
        .all()
    )

    chats = []

    for couple in couples:

        if user_id == couple.partner1_id:
            partner_id = couple.partner2_id
        else:
            partner_id = couple.partner1_id

        partner = db.get(User, partner_id)

        if not partner:
            continue

        last_message = (
            db.query(Message)
            .filter(Message.couple_id == couple.id)
            .order_by(
                Message.sent_date.desc(),
                Message.id.desc()
            )
            .first()
        )

        is_active = couple.status == "active"

        # ended relationship jisme kabhi koi message nahi hua - list mein mat dikhao
        if not is_active and not last_message:
            continue

        unread = (
            db.query(func.count(Message.id))
            .filter(
                Message.couple_id == couple.id,
                Message.receiver_id == user_id,
                Message.is_read == False
            )
            .scalar()
        )

        chats.append({
            "couple_id": couple.id,
            "user_id": partner.user_id,
            "name": partner.name,
            "gender": partner.gender,
            "profile_image": partner.profile_image,
            "last_message": last_message.message if last_message else "",
            "last_time": last_message.sent_date.isoformat() if last_message else None,
            "unread": unread or 0,
            "is_active": is_active
        })

    # naye message wali chat upar, phir active chat sabse upar
    chats.sort(key=lambda c: c["last_time"] or "", reverse=True)
    chats.sort(key=lambda c: not c["is_active"])

    return chats


# ============================================================
# Get Chat Messages   (?couple_id=...  - na do to active/latest couple)
# ============================================================

@app.get("/user/chat/messages")
def get_messages(
    couple_id: Optional[int] = Query(None),
    current=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user_id = _uid(current)

    couple, _ = _get_my_couple(db, user_id, couple_id)

    messages = (
        db.query(Message)
        .filter(Message.couple_id == couple.id)
        .order_by(
            Message.sent_date.asc(),
            Message.id.asc()
        )
        .all()
    )

    return [_out(message) for message in messages]


# ============================================================
# Mark Messages As Read   (?couple_id=...)
# ============================================================

@app.put("/user/chat/messages/read")
async def mark_messages_read(
    couple_id: Optional[int] = Query(None),
    current=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user_id = _uid(current)

    couple, partner_id = _get_my_couple(db, user_id, couple_id)

    updated = (
        db.query(Message)
        .filter(
            Message.receiver_id == user_id,
            Message.couple_id == couple.id,
            Message.is_read == False
        )
        .update(
            {Message.is_read: True},
            synchronize_session=False
        )
    )

    db.commit()

    # is chat ki "message" notification bhi read + bell ka badge update
    mark_read_for(db, user_id, "message", couple.id)
    await push_unread_count(user_id)

    if updated > 0:
        await manager.send_to_user(
            partner_id,
            {
                "type": "messages_read",
                "couple_id": couple.id,
                "reader_id": user_id
            }
        )

    return {
        "marked_read": updated
    }


# ============================================================
# Get Unread Message Count  (saari chats ka total)
# ============================================================

@app.get("/user/chat/unread-count")
def unread_count(
    current=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user_id = _uid(current)

    count = (
        db.query(func.count(Message.id))
        .filter(
            Message.receiver_id == user_id,
            Message.is_read == False
        )
        .scalar()
    )

    return {
        "unread_count": count or 0
    }


# ============================================================
# Check Whether User Can Chat
# ============================================================

def _check_can_chat(user_id: int):

    with SessionLocal() as db:

        user = db.get(User, user_id)

        if not user or user.role != "user":
            raise HTTPException(
                status_code=403,
                detail="Not allowed"
            )

        _get_couple_context(
            db,
            user_id,
            require_active=True
        )


# ============================================================
# Save Message
# ============================================================

def _save_message(user_id: int, text: str):

    with SessionLocal() as db:

        couple, partner_id = _get_couple_context(
            db,
            user_id,
            require_active=True
        )

        message = Message(
            couple_id=couple.id,
            sender_id=user_id,
            receiver_id=partner_id,
            message=text,
            sent_date=_utcnow(),
            is_read=False
        )

        db.add(message)
        db.commit()
        db.refresh(message)

        out = _out(message)

        # partner ko notification (bell) - ek chat ke kai unread messages 1 notification mein collapse
        sender = db.get(User, user_id)

        notify(
            db,
            partner_id,
            "message",
            sender.name if sender else "New message",
            text[:120],
            actor_id=user_id,
            ref_id=couple.id,
            collapse=True
        )

        return out, partner_id


# ============================================================
# WebSocket Chat   (sirf ACTIVE relationship ke liye)
# ============================================================

@app.websocket("/ws/chat")
async def chat_ws(
    websocket: WebSocket,
    token: str = Query(...)
):
    print("====================================")
    print("WEBSOCKET CONNECTION REQUEST")
    print("====================================")

    try:
        print("Token received")

        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )

        print("JWT payload:", payload)

        if payload.get("role") != "user":
            print("ERROR: Role is not user")
            await websocket.close(code=1008)
            return

        user_id = _uid(payload)

        print("User ID:", user_id)

    except Exception as e:
        print("JWT ERROR:", repr(e))
        await websocket.close(code=1008)
        return


    try:
        print("Checking active relationship...")

        await run_in_threadpool(
            _check_can_chat,
            user_id
        )

        print("Active relationship check: OK")

    except Exception as e:
        print("CHAT PERMISSION ERROR:", repr(e))
        await websocket.close(code=1008)
        return


    try:
        await manager.connect(
            user_id,
            websocket
        )

        print("WEBSOCKET CONNECTED")
        print("User:", user_id)

    except Exception as e:
        print("WEBSOCKET CONNECT ERROR:", repr(e))
        return


    try:

        while True:

            text = (
                await websocket.receive_text()
            ).strip()

            print("MESSAGE RECEIVED:", text)


            if not (1 <= len(text) <= 1000):

                await websocket.send_json({
                    "type": "error",
                    "detail": "Message must be 1 to 1000 characters"
                })

                continue


            try:

                saved, partner_id = await run_in_threadpool(
                    _save_message,
                    user_id,
                    text
                )

                print(
                    "MESSAGE SAVED:",
                    saved
                )

                print(
                    "PARTNER ID:",
                    partner_id
                )

            except Exception as e:

                print(
                    "SAVE MESSAGE ERROR:",
                    repr(e)
                )

                await websocket.send_json({
                    "type": "error",
                    "detail": "Message could not be saved"
                })

                continue


            data = {
                "type": "message",
                **saved
            }


            await manager.send_to_user(
                partner_id,
                data
            )


            await manager.send_to_user(
                user_id,
                data
            )


    except WebSocketDisconnect:

        print(
            "WEBSOCKET DISCONNECTED:",
            user_id
        )


    except Exception as e:

        print(
            "WEBSOCKET ERROR:",
            repr(e)
        )


    finally:

        manager.disconnect(
            user_id,
            websocket
        )

        print(
            "WEBSOCKET CONNECTION CLOSED:",
            user_id
        )