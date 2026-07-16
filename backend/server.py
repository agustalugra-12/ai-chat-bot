"""Pelangi Homestay Guest AI — FastAPI backend."""
import os
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from motor.motor_asyncio import AsyncIOMotorClient
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# --- Imports after load_dotenv so envs are ready ---
from auth import (
    create_access_token, get_current_user, hash_password,
    require_super_admin, verify_password,
)
from db import new_id, utc_now_iso
from models import (
    Booking, BookingIn, BookingUpdate,
    ChatMessage, ChatSendRequest, Conversation,
    KB_CATEGORIES, KnowledgeItem, KnowledgeItemIn,
    LoginRequest, LoginResponse,
    MenuItem, MenuItemIn,
    PromptIn, PromptVersion,
    Room, RoomIn,
    ServiceRequest, ServiceRequestIn, ServiceRequestUpdate,
    Settings, SettingsIn,
    User,
)
from ai_service import (
    DEFAULT_SYSTEM_PROMPT, ai_reply, compact_history,
    build_context_block, parse_tool_call,
)
from seed import seed_all

# ---------------------------------------------------------------------------
mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

app = FastAPI(title="Pelangi Homestay Guest AI")
api = APIRouter(prefix="/api")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("pelangi")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    await seed_all(db)
    logger.info("Seed complete")


@app.on_event("shutdown")
async def on_shutdown():
    client.close()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@api.get("/")
async def root():
    return {"status": "ok", "service": "pelangi-homestay-guest-ai"}


# ---------------------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------------------
@api.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    user = await db.users.find_one({"email": body.email.lower()})
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Email atau password salah")
    token = create_access_token(sub=user["_id"], role=user["role"], email=user["email"])
    return LoginResponse(
        token=token,
        user={"id": user["_id"], "email": user["email"], "name": user["name"], "role": user["role"]},
    )


@api.get("/auth/me")
async def me(user=Depends(get_current_user)):
    doc = await db.users.find_one({"_id": user["id"]})
    if not doc:
        raise HTTPException(404, "User not found")
    return {"id": str(doc["_id"]), "email": doc["email"], "name": doc["name"], "role": doc["role"]}


# ---------------------------------------------------------------------------
# KNOWLEDGE BASE
# ---------------------------------------------------------------------------
@api.get("/knowledge-base/categories")
async def kb_categories(user=Depends(get_current_user)):
    return {"categories": KB_CATEGORIES}


@api.get("/knowledge-base")
async def kb_list(category: Optional[str] = None, user=Depends(get_current_user)):
    q = {}
    if category:
        q["category"] = category
    docs = await db.knowledge_base.find(q).sort("created_at", -1).to_list(500)
    return [{**d, "id": d.pop("_id")} for d in docs]


@api.post("/knowledge-base")
async def kb_create(body: KnowledgeItemIn, user=Depends(get_current_user)):
    if body.category not in KB_CATEGORIES:
        raise HTTPException(400, "Invalid category")
    doc = {
        "_id": new_id(), **body.model_dump(),
        "created_at": utc_now_iso(), "updated_at": utc_now_iso(),
    }
    await db.knowledge_base.insert_one(doc)
    return {**doc, "id": doc.pop("_id")}


@api.put("/knowledge-base/{item_id}")
async def kb_update(item_id: str, body: KnowledgeItemIn, user=Depends(get_current_user)):
    if body.category not in KB_CATEGORIES:
        raise HTTPException(400, "Invalid category")
    upd = {**body.model_dump(), "updated_at": utc_now_iso()}
    res = await db.knowledge_base.update_one({"_id": item_id}, {"$set": upd})
    if not res.matched_count:
        raise HTTPException(404, "Not found")
    doc = await db.knowledge_base.find_one({"_id": item_id})
    return {**doc, "id": doc.pop("_id")}


@api.delete("/knowledge-base/{item_id}")
async def kb_delete(item_id: str, user=Depends(get_current_user)):
    await db.knowledge_base.delete_one({"_id": item_id})
    return {"ok": True}


# ---------------------------------------------------------------------------
# ROOMS
# ---------------------------------------------------------------------------
@api.get("/rooms")
async def rooms_list(user=Depends(get_current_user)):
    docs = await db.rooms.find({}).sort("price_per_night", 1).to_list(200)
    return [{**d, "id": d.pop("_id")} for d in docs]


@api.post("/rooms")
async def rooms_create(body: RoomIn, user=Depends(get_current_user)):
    doc = {"_id": new_id(), **body.model_dump(), "created_at": utc_now_iso()}
    await db.rooms.insert_one(doc)
    return {**doc, "id": doc.pop("_id")}


@api.put("/rooms/{room_id}")
async def rooms_update(room_id: str, body: RoomIn, user=Depends(get_current_user)):
    res = await db.rooms.update_one({"_id": room_id}, {"$set": body.model_dump()})
    if not res.matched_count:
        raise HTTPException(404, "Not found")
    doc = await db.rooms.find_one({"_id": room_id})
    return {**doc, "id": doc.pop("_id")}


@api.delete("/rooms/{room_id}")
async def rooms_delete(room_id: str, user=Depends(get_current_user)):
    await db.rooms.delete_one({"_id": room_id})
    return {"ok": True}


# ---------------------------------------------------------------------------
# RESTAURANT MENU
# ---------------------------------------------------------------------------
@api.get("/menu")
async def menu_list(user=Depends(get_current_user)):
    docs = await db.menu.find({}).sort("category", 1).to_list(500)
    return [{**d, "id": d.pop("_id")} for d in docs]


@api.post("/menu")
async def menu_create(body: MenuItemIn, user=Depends(get_current_user)):
    doc = {"_id": new_id(), **body.model_dump(), "created_at": utc_now_iso()}
    await db.menu.insert_one(doc)
    return {**doc, "id": doc.pop("_id")}


@api.put("/menu/{item_id}")
async def menu_update(item_id: str, body: MenuItemIn, user=Depends(get_current_user)):
    res = await db.menu.update_one({"_id": item_id}, {"$set": body.model_dump()})
    if not res.matched_count:
        raise HTTPException(404, "Not found")
    doc = await db.menu.find_one({"_id": item_id})
    return {**doc, "id": doc.pop("_id")}


@api.delete("/menu/{item_id}")
async def menu_delete(item_id: str, user=Depends(get_current_user)):
    await db.menu.delete_one({"_id": item_id})
    return {"ok": True}


# ---------------------------------------------------------------------------
# BOOKINGS
# ---------------------------------------------------------------------------
async def _compute_room_price(room_type: str, num_rooms: int, check_in: str, check_out: str) -> float:
    room = await db.rooms.find_one({"room_type": room_type})
    if not room:
        return 0.0
    try:
        d1 = datetime.fromisoformat(check_in).date()
        d2 = datetime.fromisoformat(check_out).date()
        nights = max((d2 - d1).days, 1)
    except Exception:
        nights = 1
    return float(room["price_per_night"]) * nights * max(num_rooms, 1)


@api.get("/bookings")
async def bookings_list(status_filter: Optional[str] = Query(None, alias="status"),
                        user=Depends(get_current_user)):
    q = {}
    if status_filter:
        q["status"] = status_filter
    docs = await db.bookings.find(q).sort("created_at", -1).to_list(500)
    return [{**d, "id": d.pop("_id")} for d in docs]


@api.post("/bookings")
async def bookings_create(body: BookingIn, user=Depends(get_current_user)):
    payload = body.model_dump()
    if not payload.get("total_amount"):
        payload["total_amount"] = await _compute_room_price(
            payload["room_type"], payload["num_rooms"], payload["check_in"], payload["check_out"]
        )
    doc = {
        "_id": new_id(), **payload,
        "status": "pending", "payment_status": "unpaid",
        "room_ids": [],
        "created_at": utc_now_iso(), "updated_at": utc_now_iso(),
    }
    await db.bookings.insert_one(doc)
    return {**doc, "id": doc.pop("_id")}


@api.put("/bookings/{booking_id}")
async def bookings_update(booking_id: str, body: BookingUpdate, user=Depends(get_current_user)):
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    if not upd:
        raise HTTPException(400, "Empty update")
    upd["updated_at"] = utc_now_iso()
    res = await db.bookings.update_one({"_id": booking_id}, {"$set": upd})
    if not res.matched_count:
        raise HTTPException(404, "Not found")
    doc = await db.bookings.find_one({"_id": booking_id})
    return {**doc, "id": doc.pop("_id")}


@api.delete("/bookings/{booking_id}")
async def bookings_delete(booking_id: str, user=Depends(require_super_admin)):
    await db.bookings.delete_one({"_id": booking_id})
    return {"ok": True}


# ---------------------------------------------------------------------------
# AVAILABILITY (guest-facing but also used by admin dashboard)
# ---------------------------------------------------------------------------
@api.get("/guest/availability")
async def guest_availability(check_in: str, check_out: str, room_type: Optional[str] = None):
    """Returns Available/Not Available per room type. No stock unless admin enabled."""
    settings = await db.settings.find_one({"_id": "singleton"}) or {}
    show_stock = bool(settings.get("show_stock_count", False))

    rooms_q = {}
    if room_type:
        rooms_q["room_type"] = room_type
    rooms = await db.rooms.find(rooms_q).to_list(200)

    # Load overlapping bookings (confirmed only occupies capacity)
    bookings = await db.bookings.find({
        "status": {"$in": ["confirmed", "pending"]},
    }).to_list(1000)

    def overlaps(b):
        try:
            b_in = datetime.fromisoformat(b["check_in"]).date()
            b_out = datetime.fromisoformat(b["check_out"]).date()
            q_in = datetime.fromisoformat(check_in).date()
            q_out = datetime.fromisoformat(check_out).date()
            return not (b_out <= q_in or b_in >= q_out)
        except Exception:
            return False

    result = []
    for r in rooms:
        if not r.get("is_available", True):
            result.append({
                "room_type": r["room_type"], "name": r["name"],
                "available": False, "reason": "disabled",
            })
            continue
        used = sum(b.get("num_rooms", 1) for b in bookings
                   if b.get("room_type") == r["room_type"] and overlaps(b))
        remaining = max(int(r.get("total_units", 1)) - used, 0)
        entry = {
            "room_type": r["room_type"], "name": r["name"],
            "price_per_night": r["price_per_night"],
            "capacity": r["capacity"],
            "available": remaining > 0,
        }
        if show_stock:
            entry["remaining"] = remaining
        result.append(entry)
    return {"check_in": check_in, "check_out": check_out, "rooms": result}


# ---------------------------------------------------------------------------
# GUEST BOOKING VERIFICATION
# ---------------------------------------------------------------------------
@api.get("/guest/bookings")
async def guest_bookings(whatsapp: str, booking_id: Optional[str] = None):
    q = {"whatsapp": whatsapp}
    if booking_id:
        q["_id"] = booking_id
    docs = await db.bookings.find(q).sort("created_at", -1).to_list(50)
    return [{**d, "id": d.pop("_id")} for d in docs]


# ---------------------------------------------------------------------------
# SERVICE REQUESTS
# ---------------------------------------------------------------------------
@api.get("/service-requests")
async def sr_list(status_filter: Optional[str] = Query(None, alias="status"),
                  user=Depends(get_current_user)):
    q = {}
    if status_filter:
        q["status"] = status_filter
    docs = await db.service_requests.find(q).sort("created_at", -1).to_list(500)
    return [{**d, "id": d.pop("_id")} for d in docs]


@api.post("/service-requests")
async def sr_create(body: ServiceRequestIn, user=Depends(get_current_user)):
    doc = {"_id": new_id(), **body.model_dump(),
           "status": "new", "created_at": utc_now_iso()}
    await db.service_requests.insert_one(doc)
    return {**doc, "id": doc.pop("_id")}


@api.patch("/service-requests/{item_id}")
async def sr_update(item_id: str, body: ServiceRequestUpdate, user=Depends(get_current_user)):
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    if not upd:
        raise HTTPException(400, "Empty update")
    res = await db.service_requests.update_one({"_id": item_id}, {"$set": upd})
    if not res.matched_count:
        raise HTTPException(404, "Not found")
    doc = await db.service_requests.find_one({"_id": item_id})
    return {**doc, "id": doc.pop("_id")}


# ---------------------------------------------------------------------------
# CONVERSATIONS
# ---------------------------------------------------------------------------
@api.get("/conversations")
async def convs_list(status_filter: Optional[str] = Query(None, alias="status"),
                     user=Depends(get_current_user)):
    q = {}
    if status_filter:
        q["status"] = status_filter
    docs = await db.conversations.find(q).sort("updated_at", -1).to_list(500)
    out = []
    for d in docs:
        d["id"] = d.pop("_id")
        d["last_message"] = (d["messages"][-1]["content"] if d.get("messages") else "")
        d["message_count"] = len(d.get("messages", []))
        out.append(d)
    return out


@api.get("/conversations/{conv_id}")
async def convs_get(conv_id: str, user=Depends(get_current_user)):
    doc = await db.conversations.find_one({"_id": conv_id})
    if not doc:
        raise HTTPException(404, "Not found")
    doc["id"] = doc.pop("_id")
    return doc


@api.patch("/conversations/{conv_id}/handover")
async def convs_handover(conv_id: str, user=Depends(get_current_user)):
    await db.conversations.update_one(
        {"_id": conv_id},
        {"$set": {"status": "waiting_admin", "resolution": "handover", "updated_at": utc_now_iso()}},
    )
    doc = await db.conversations.find_one({"_id": conv_id})
    if not doc:
        raise HTTPException(404, "Not found")
    doc["id"] = doc.pop("_id")
    return doc


@api.patch("/conversations/{conv_id}/close")
async def convs_close(conv_id: str, user=Depends(get_current_user)):
    await db.conversations.update_one(
        {"_id": conv_id},
        {"$set": {"status": "closed", "updated_at": utc_now_iso()}},
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# CHAT (AI Guest Assistant)
# ---------------------------------------------------------------------------
async def _load_active_prompt() -> str:
    doc = await db.prompts.find_one({"is_active": True})
    return (doc or {}).get("content") or DEFAULT_SYSTEM_PROMPT


async def _build_context() -> str:
    rooms = await db.rooms.find({}).to_list(200)
    menu = await db.menu.find({}).to_list(500)
    kb = await db.knowledge_base.find({"is_active": True}).to_list(500)
    settings = await db.settings.find_one({"_id": "singleton"}) or {}
    return build_context_block(rooms, menu, kb, settings)


async def _handle_tool(tool: str, args: dict, conv: dict) -> Optional[dict]:
    """Execute AI tool call. Returns tool_result dict for the AI to acknowledge next turn."""
    if tool == "check_availability":
        try:
            check_in = args.get("check_in"); check_out = args.get("check_out")
            room_type = args.get("room_type")
            rooms_q = {}
            if room_type:
                rooms_q["room_type"] = room_type
            rooms = await db.rooms.find(rooms_q).to_list(200)
            bookings = await db.bookings.find({"status": {"$in": ["confirmed", "pending"]}}).to_list(1000)

            def overlaps(b):
                try:
                    b_in = datetime.fromisoformat(b["check_in"]).date()
                    b_out = datetime.fromisoformat(b["check_out"]).date()
                    q_in = datetime.fromisoformat(check_in).date()
                    q_out = datetime.fromisoformat(check_out).date()
                    return not (b_out <= q_in or b_in >= q_out)
                except Exception:
                    return False

            report = []
            for r in rooms:
                if not r.get("is_available", True):
                    report.append({"room": r["name"], "available": False}); continue
                used = sum(b.get("num_rooms", 1) for b in bookings
                           if b.get("room_type") == r["room_type"] and overlaps(b))
                report.append({
                    "room": r["name"], "room_type": r["room_type"],
                    "available": used < int(r.get("total_units", 1)),
                    "price_per_night": r["price_per_night"],
                })
            return {"ok": True, "tool": tool, "result": report}
        except Exception as e:
            return {"ok": False, "tool": tool, "error": str(e)}

    if tool == "create_booking":
        try:
            required = ["guest_name", "whatsapp", "check_in", "check_out", "room_type"]
            for k in required:
                if not args.get(k):
                    return {"ok": False, "tool": tool, "error": f"missing {k}"}
            total = await _compute_room_price(
                args["room_type"], int(args.get("num_rooms", 1)),
                args["check_in"], args["check_out"],
            )
            doc = {
                "_id": new_id(),
                "guest_name": args["guest_name"],
                "whatsapp": args["whatsapp"],
                "check_in": args["check_in"],
                "check_out": args["check_out"],
                "room_type": args["room_type"],
                "num_rooms": int(args.get("num_rooms", 1)),
                "num_guests": int(args.get("num_guests", 1)),
                "total_amount": total,
                "dp_amount": 0.0,
                "status": "pending",
                "payment_status": "unpaid",
                "source": "ai",
                "room_ids": [],
                "notes": args.get("notes"),
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
            }
            await db.bookings.insert_one(doc)
            await db.conversations.update_one({"_id": conv["_id"]}, {"$set": {"booking_created": True}})
            return {"ok": True, "tool": tool, "booking_id": doc["_id"], "total_amount": total}
        except Exception as e:
            return {"ok": False, "tool": tool, "error": str(e)}

    if tool == "create_service_request":
        try:
            doc = {
                "_id": new_id(),
                "guest_name": args.get("guest_name") or conv.get("guest_name") or "Guest",
                "whatsapp": args.get("whatsapp") or conv.get("whatsapp") or "-",
                "booking_id": args.get("booking_id"),
                "service_type": args["service_type"],
                "quantity": int(args.get("quantity", 1)),
                "notes": args.get("notes"),
                "status": "new",
                "created_at": utc_now_iso(),
            }
            await db.service_requests.insert_one(doc)
            return {"ok": True, "tool": tool, "request_id": doc["_id"]}
        except Exception as e:
            return {"ok": False, "tool": tool, "error": str(e)}

    if tool == "lookup_booking":
        wa = args.get("whatsapp")
        if not wa:
            return {"ok": False, "tool": tool, "error": "missing whatsapp"}
        docs = await db.bookings.find({"whatsapp": wa}).sort("created_at", -1).to_list(20)
        summary = [{
            "booking_id": d["_id"], "guest_name": d["guest_name"],
            "check_in": d["check_in"], "check_out": d["check_out"],
            "room_type": d["room_type"], "num_rooms": d.get("num_rooms", 1),
            "status": d["status"], "total_amount": d.get("total_amount", 0),
            "payment_status": d.get("payment_status", "unpaid"),
        } for d in docs]
        return {"ok": True, "tool": tool, "result": summary}

    if tool == "request_handover":
        await db.conversations.update_one(
            {"_id": conv["_id"]},
            {"$set": {"status": "waiting_admin", "resolution": "handover", "updated_at": utc_now_iso()}},
        )
        return {"ok": True, "tool": tool}

    return {"ok": False, "tool": tool, "error": "unknown tool"}


@api.post("/chat/message")
async def chat_message(body: ChatSendRequest, user=Depends(get_current_user)):
    session_id = body.session_id or str(uuid.uuid4())
    started = time.time()

    conv = await db.conversations.find_one({"session_id": session_id})
    if not conv:
        conv = {
            "_id": new_id(),
            "session_id": session_id,
            "guest_name": body.guest_name,
            "whatsapp": body.whatsapp,
            "channel": "simulator",
            "messages": [],
            "status": "active",
            "resolution": "unresolved",
            "booking_created": False,
            "last_intent": None,
            "response_time_ms": 0,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }
        await db.conversations.insert_one(conv)

    # Append user message
    user_msg = {"role": "user", "content": body.message, "timestamp": utc_now_iso()}
    conv["messages"].append(user_msg)

    # Build prompt inputs
    system_prompt = await _load_active_prompt()
    context = await _build_context()
    history_text = compact_history(conv["messages"][:-1], max_turns=12)

    # First AI turn
    raw = await ai_reply(session_id, system_prompt, context, history_text, body.message)
    clean_text, tool, args = parse_tool_call(raw)

    tool_result = None
    if tool:
        tool_result = await _handle_tool(tool, args or {}, conv)
        # give AI a chance to acknowledge tool result with a second turn
        follow_up_user = (
            f"[SISTEM] Hasil tool `{tool}`: {tool_result}. "
            f"Sampaikan konfirmasi natural ke tamu (Bahasa Indonesia, sopan, singkat). "
            f"Jangan panggil tool lagi kecuali tamu memintanya."
        )
        history_after = compact_history(
            conv["messages"] + [{"role": "assistant", "content": clean_text or "(tool call)"}],
            max_turns=14,
        )
        follow_raw = await ai_reply(session_id, system_prompt, context, history_after, follow_up_user)
        follow_clean, _, _ = parse_tool_call(follow_raw)
        final_text = (clean_text + "\n\n" + follow_clean).strip() if clean_text else follow_clean
    else:
        final_text = clean_text

    ai_msg = {
        "role": "assistant",
        "content": final_text,
        "timestamp": utc_now_iso(),
        "intent": tool or None,
    }
    conv["messages"].append(ai_msg)

    elapsed_ms = int((time.time() - started) * 1000)

    update = {
        "messages": conv["messages"],
        "updated_at": utc_now_iso(),
        "last_intent": tool,
        "response_time_ms": elapsed_ms,
    }
    if tool == "request_handover":
        update["status"] = "waiting_admin"
        update["resolution"] = "handover"
    elif tool and tool_result and tool_result.get("ok"):
        update["resolution"] = "ai_resolved"

    await db.conversations.update_one({"_id": conv["_id"]}, {"$set": update})

    return {
        "session_id": session_id,
        "conversation_id": conv["_id"],
        "reply": final_text,
        "tool_used": tool,
        "tool_result": tool_result,
        "response_time_ms": elapsed_ms,
    }


# ---------------------------------------------------------------------------
# PROMPT MANAGEMENT
# ---------------------------------------------------------------------------
@api.get("/prompt/active")
async def prompt_active(user=Depends(get_current_user)):
    doc = await db.prompts.find_one({"is_active": True})
    if not doc:
        return {"content": DEFAULT_SYSTEM_PROMPT, "version": 0}
    return {"id": doc["_id"], "version": doc["version"], "content": doc["content"],
            "is_active": True, "created_at": doc["created_at"]}


@api.get("/prompt/versions")
async def prompt_versions(user=Depends(get_current_user)):
    docs = await db.prompts.find({}).sort("version", -1).to_list(100)
    return [{"id": d["_id"], "version": d["version"], "content": d["content"],
             "is_active": d.get("is_active", False), "created_at": d.get("created_at")}
            for d in docs]


@api.post("/prompt")
async def prompt_save(body: PromptIn, user=Depends(get_current_user)):
    # bump version and activate this one
    latest = await db.prompts.find({}).sort("version", -1).limit(1).to_list(1)
    next_version = (latest[0]["version"] + 1) if latest else 1
    await db.prompts.update_many({"is_active": True}, {"$set": {"is_active": False}})
    doc = {
        "_id": new_id(),
        "version": next_version,
        "content": body.content,
        "is_active": True,
        "created_by": user["email"],
        "created_at": utc_now_iso(),
    }
    await db.prompts.insert_one(doc)
    return {"id": doc["_id"], "version": doc["version"], "content": doc["content"],
            "is_active": True, "created_at": doc["created_at"]}


@api.post("/prompt/{prompt_id}/activate")
async def prompt_activate(prompt_id: str, user=Depends(get_current_user)):
    doc = await db.prompts.find_one({"_id": prompt_id})
    if not doc:
        raise HTTPException(404, "Not found")
    await db.prompts.update_many({"is_active": True}, {"$set": {"is_active": False}})
    await db.prompts.update_one({"_id": prompt_id}, {"$set": {"is_active": True}})
    return {"ok": True}


# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------
@api.get("/settings")
async def settings_get(user=Depends(get_current_user)):
    doc = await db.settings.find_one({"_id": "singleton"}) or {}
    doc["id"] = doc.pop("_id", "singleton")
    return doc


@api.put("/settings")
async def settings_update(body: SettingsIn, user=Depends(get_current_user)):
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    upd["updated_at"] = utc_now_iso()
    await db.settings.update_one({"_id": "singleton"}, {"$set": upd}, upsert=True)
    doc = await db.settings.find_one({"_id": "singleton"})
    doc["id"] = doc.pop("_id")
    return doc


# ---------------------------------------------------------------------------
# ANALYTICS
# ---------------------------------------------------------------------------
@api.get("/analytics/summary")
async def analytics_summary(user=Depends(get_current_user)):
    convs = await db.conversations.find({}).to_list(2000)
    bookings = await db.bookings.find({}).to_list(2000)
    total_conv = len(convs)
    resolved = sum(1 for c in convs if c.get("resolution") == "ai_resolved")
    handover = sum(1 for c in convs if c.get("resolution") == "handover")
    ai_bookings = sum(1 for b in bookings if b.get("source") == "ai")
    avg_rt = (sum(c.get("response_time_ms", 0) for c in convs) / total_conv) if total_conv else 0
    conversion_rate = (ai_bookings / total_conv * 100) if total_conv else 0
    resolution_rate = (resolved / total_conv * 100) if total_conv else 0

    # intent counts
    intent_counts = {}
    for c in convs:
        i = c.get("last_intent")
        if i:
            intent_counts[i] = intent_counts.get(i, 0) + 1
    top_intents = sorted(
        [{"intent": k, "count": v} for k, v in intent_counts.items()],
        key=lambda x: x["count"], reverse=True,
    )[:6]

    # conversations by day (last 7)
    from collections import Counter
    daily = Counter()
    for c in convs:
        try:
            d = c.get("created_at", "")[:10]
            if d:
                daily[d] += 1
        except Exception:
            pass
    daily_series = [{"date": d, "count": daily[d]} for d in sorted(daily.keys())[-14:]]

    return {
        "total_conversations": total_conv,
        "resolution_rate": round(resolution_rate, 1),
        "human_handover": handover,
        "bookings_from_ai": ai_bookings,
        "conversion_rate": round(conversion_rate, 1),
        "avg_response_time_ms": round(avg_rt),
        "top_intents": top_intents,
        "daily_series": daily_series,
    }


# ---------------------------------------------------------------------------
# App wiring
# ---------------------------------------------------------------------------
app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
