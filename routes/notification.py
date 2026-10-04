"""
LoveVest - Notifications  (chat.py jaisa alag router)

Kaam:
  1. notify(...)            -> DB mein notification save + live push (dusre files yahi call karte hain)
  2. REST endpoints         -> list, unread count, read, read-all, clear
  3. WebSocket              -> /ws/notifications  (bell live update ke liye)

Debugging:
  - Saare logs "[NOTIFY]" se shuru hote hain. Band karna ho to DEBUG = False.
  - GET /user/notifications/debug  -> kitne sockets connected hain + unread count
"""

from datetime import datetime, timezone
from typing import Optional

from anyio import from_thread
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from jose import jwt
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from database import get_db, SessionLocal
from models import User, Notification, RelationshipRequest

from auth.permissions import role_required
from auth.jwt import SECRET_KEY, ALGORITHM


app = APIRouter()

DEBUG = True


def _log(*args):
    if DEBUG:
        print("[NOTIFY]", *args)


# ============================================================
# Helpers
# ============================================================

def _uid(current):
    if isinstance(current, dict):
        value = current.get("sub")
    else:
        value = getattr(current, "id", None)

    if value is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    return int(value)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _unread_count(db: Session, user_id: int) -> int:
    return (
        db.query(func.count(Notification.id))
        .filter(
            Notification.user_id == user_id,
            Notification.is_read == False
        )
        .scalar()
    ) or 0


def _serialize(db: Session, n: Notification) -> dict:
    actor = db.get(User, n.actor_id) if n.actor_id else None

    data = {
        "id": n.id,
        "type": n.type,
        "title": n.title,
        "body": n.body or "",
        "ref_id": n.ref_id,
        "count": n.count or 1,
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat(),
        "actor": {
            "user_id": actor.user_id,
            "name": actor.name,
            "gender": actor.gender,
            "profile_image": actor.profile_image
        } if actor else None
    }

    # invite notification par Accept / Reject buttons tabhi dikhane hain jab request abhi pending ho
    if n.type == "invite":
        req = db.get(RelationshipRequest, n.ref_id) if n.ref_id else None
        data["invite_status"] = req.status if req else "missing"

    return data


# ============================================================
# WebSocket Connection Manager  (chat.py wale jaisa)
# ============================================================

class NotificationManager:

    def __init__(self):
        self._connections = {}

    async def connect(self, user_id: int, websocket: WebSocket):
        await websocket.accept()
        self._connections.setdefault(user_id, set()).add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket):
        sockets = self._connections.get(user_id)

        if sockets:
            sockets.discard(websocket)

            if not sockets:
                del self._connections[user_id]

    def count(self, user_id: int) -> int:
        return len(self._connections.get(user_id, set()))

    async def send_to_user(self, user_id: int, data: dict):
        sockets = list(self._connections.get(user_id, set()))

        for websocket in sockets:
            try:
                await websocket.send_json(data)
            except Exception:
                self.disconnect(user_id, websocket)


notification_manager = NotificationManager()


# ============================================================
# Push helpers
# ============================================================

def _push_sync(user_id: int, payload: dict):
    """
    Sync code (normal `def` endpoint / run_in_threadpool wale functions) se live push.
    Fail ho jaye to bhi notification DB mein save rehti hai - bell polling se bhi mil jati hai.
    """
    try:
        from_thread.run(notification_manager.send_to_user, user_id, payload)
        _log("pushed ->", user_id, payload.get("type"))
    except Exception as e:
        _log("push skipped (DB mein saved hai):", repr(e))


def _unread_for(user_id: int) -> int:
    with SessionLocal() as db:
        return _unread_count(db, user_id)


async def push_unread_count(user_id: int):
    """`async def` endpoints se bell ka badge update karne ke liye (jaise chat read hone par)."""
    count = await run_in_threadpool(_unread_for, user_id)

    await notification_manager.send_to_user(
        user_id,
        {"type": "unread_count", "unread_count": count}
    )


# ============================================================
# notify()  -  dusre files (chat.py, relationship) yahi call karte hain
# ============================================================

def notify(
    db: Session,
    user_id: int,
    type_: str,
    title: str,
    body: str = "",
    actor_id: Optional[int] = None,
    ref_id: Optional[int] = None,
    collapse: bool = False
):
    """
    user_id  : jise notification milegi
    type_    : "message" | "invite" | "invite_accepted" | "invite_rejected"
    collapse : True ho to same (user, type, ref_id) ki unread notification update hoti hai
               (chat ke 10 messages = 1 notification, count 10)

    Ye kabhi exception nahi uthata - notification fail hone se main kaam (message bhejna,
    invite accept karna) nahi rukna chahiye.
    """
    try:
        n = None

        if collapse:
            n = (
                db.query(Notification)
                .filter(
                    Notification.user_id == user_id,
                    Notification.type == type_,
                    Notification.ref_id == ref_id,
                    Notification.is_read == False
                )
                .first()
            )

        if n:
            n.title = title[:120]
            n.body = (body or "")[:300]
            n.actor_id = actor_id
            n.count = (n.count or 1) + 1
            n.created_at = _utcnow()
        else:
            n = Notification(
                user_id=user_id,
                actor_id=actor_id,
                type=type_,
                title=title[:120],
                body=(body or "")[:300],
                ref_id=ref_id,
                count=1,
                is_read=False,
                created_at=_utcnow()
            )
            db.add(n)

        db.commit()
        db.refresh(n)

        _log("saved:", {"id": n.id, "to": user_id, "type": type_, "ref_id": ref_id})

        _push_sync(
            user_id,
            {
                "type": "notification",
                "notification": _serialize(db, n),
                "unread_count": _unread_count(db, user_id)
            }
        )

        return n

    except Exception as e:
        db.rollback()
        _log("notify ERROR:", repr(e))
        return None


def mark_read_for(db: Session, user_id: int, type_: str, ref_id: Optional[int]) -> int:
    """Kisi chat/invite se judi notifications read karo (chat kholne par, invite accept/reject par)."""
    updated = (
        db.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.type == type_,
            Notification.ref_id == ref_id,
            Notification.is_read == False
        )
        .update({Notification.is_read: True}, synchronize_session=False)
    )

    db.commit()

    _log("mark_read_for:", {"user": user_id, "type": type_, "ref_id": ref_id, "updated": updated})

    return updated


# ============================================================
# REST: list   GET /user/notifications?limit=30
# ============================================================

@app.get("/user/notifications")
def list_notifications(
    limit: int = Query(30, ge=1, le=100),
    current=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user_id = _uid(current)

    rows = (
        db.query(Notification)
        .filter(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
        .all()
    )

    return {
        "unread_count": _unread_count(db, user_id),
        "items": [_serialize(db, n) for n in rows]
    }


# ============================================================
# REST: unread count   GET /user/notifications/unread-count
# ============================================================

@app.get("/user/notifications/unread-count")
def unread_count(
    current=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    return {"unread_count": _unread_count(db, _uid(current))}


# ============================================================
# REST: debug   GET /user/notifications/debug
# ============================================================

@app.get("/user/notifications/debug")
def debug_info(
    current=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user_id = _uid(current)

    return {
        "user_id": user_id,
        "connected_sockets": notification_manager.count(user_id),
        "unread_count": _unread_count(db, user_id),
        "total_notifications": (
            db.query(func.count(Notification.id))
            .filter(Notification.user_id == user_id)
            .scalar()
        ) or 0
    }


# ============================================================
# REST: sab read   PUT /user/notifications/read-all
# (/{id}/read se pehle define hona chahiye)
# ============================================================

@app.put("/user/notifications/read-all")
def read_all(
    current=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user_id = _uid(current)

    updated = (
        db.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.is_read == False
        )
        .update({Notification.is_read: True}, synchronize_session=False)
    )

    db.commit()

    return {"marked_read": updated, "unread_count": 0}


# ============================================================
# REST: ek read   PUT /user/notifications/{id}/read
# ============================================================

@app.put("/user/notifications/{notification_id}/read")
def read_one(
    notification_id: int,
    current=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user_id = _uid(current)

    n = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.user_id == user_id
        )
        .first()
    )

    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")

    n.is_read = True
    db.commit()

    return {"unread_count": _unread_count(db, user_id)}


# ============================================================
# REST: clear all   DELETE /user/notifications
# ============================================================

@app.delete("/user/notifications")
def clear_all(
    current=Depends(role_required("user")),
    db: Session = Depends(get_db)
):
    user_id = _uid(current)

    deleted = (
        db.query(Notification)
        .filter(Notification.user_id == user_id)
        .delete(synchronize_session=False)
    )

    db.commit()

    return {"deleted": deleted, "unread_count": 0}


# ============================================================
# WebSocket   /ws/notifications?token=...
# ============================================================

@app.websocket("/ws/notifications")
async def notifications_ws(
    websocket: WebSocket,
    token: str = Query(...)
):
    _log("WS connection request")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        if payload.get("role") != "user":
            _log("WS rejected: role is not user")
            await websocket.close(code=1008)
            return

        user_id = _uid(payload)

    except Exception as e:
        _log("WS JWT ERROR:", repr(e))
        await websocket.close(code=1008)
        return

    await notification_manager.connect(user_id, websocket)

    _log("WS connected, user:", user_id, "| sockets:", notification_manager.count(user_id))

    try:
        count = await run_in_threadpool(_unread_for, user_id)

        await websocket.send_json({"type": "ready", "unread_count": count})

        while True:
            text = (await websocket.receive_text()).strip()

            if text == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        _log("WS disconnected:", user_id)

    except Exception as e:
        _log("WS ERROR:", repr(e))

    finally:
        notification_manager.disconnect(user_id, websocket)
        _log("WS closed:", user_id)