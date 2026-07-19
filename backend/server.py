"""Pelangi AI — AI Customer Service Platform (FastAPI backend).

Brain Platform reusable lintas channel (WhatsApp/website chat/dst, lihat
connectors/waha_connector.py untuk adapter WhatsApp) & lintas bisnis (Business System
Connector, lihat connectors/pms_connector.py untuk integrasi Pelangi PMS) - bukan
"AI WhatsApp Bot" yang terikat satu channel/satu bisnis (PRD v2, 2026-07-19).
"""
import os
import asyncio
import logging
import random
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, APIRouter, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# --- Imports after load_dotenv so envs are ready ---
from auth import (
    create_access_token, get_current_user, hash_password,
    require_super_admin, verify_password,
)
from db import client, db, new_id, utc_now_iso
from models import (
    AIBot, AIBotIn, AIBotUpdate,
    Booking, BookingIn, BookingUpdate,
    ChatMessage, ChatSendRequest, Conversation,
    IntentCatalogItem, IntentIn,
    KB_CATEGORIES, KnowledgeItem, KnowledgeItemIn,
    LoginRequest, LoginResponse,
    MenuItem, MenuItemIn,
    PromptIn, PromptVersion,
    Room, RoomIn,
    Settings, SettingsIn,
    ToolCatalogItem, ToolIn,
    User,
    Workflow, WorkflowIn, WorkflowStep,
)
from ai_service import (
    ALL_TOOL_CODES, DEFAULT_MODEL, DEFAULT_PROVIDER, DEFAULT_SYSTEM_PROMPT, ai_reply,
    compact_history, build_context_block, build_dynamic_prompt, parse_tool_call, parse_img_markers,
    LLM_PROVIDER_OPTIONS, SERVICE_MAP,
)
from cloudinary_service import upload_image, upload_raw, delete_asset
from rag_service import extract_text, chunk_text, hybrid_search, build_rag_context, get_embeddings_batch
from seed import seed_all

# ---------------------------------------------------------------------------
# `client`/`db` diimpor dari db.py (satu-satunya tempat koneksi Mongo dibuat).
#
# Connector Layer (PRD v2, 2026-07-19): integrasi ke sistem luar (WAHA, Pelangi PMS)
# dipindahkan ke modul connectors/ terpisah - server.py (AI Customer Platform) pakai
# fungsinya lewat import di bawah, tidak lagi tahu detail HTTP/auth sistem luar.
# Lihat connectors/__init__.py untuk penjelasan pembagian tanggung jawabnya.
from connectors.waha_connector import (
    WAHA_BASE_URL, WAHA_API_KEY, WAHA_SESSION, WAHA_WEBHOOK_TOKEN,
    _waha_call, _waha_send_text, _waha_send_image, _waha_list_sessions, _waha_ensure_session,
)
from connectors.pms_connector import (
    PMS_API_BASE_URL, PMS_API_KEY, PMS_DEFAULT_ENDPOINTS,
    PMS_CAPABILITY_WIRED, PMS_DEFAULT_CAPABILITIES, PMS_INTEGRATION_DEFAULT,
    SYNC_KINDS,
    _pms_config, _pms_log, _pms_ketersediaan, _pms_buat_booking_request,
    _pms_buat_tiket, _pms_status_booking, _pms_ajukan_pembatalan, _sync_business_rules,
)
from connectors.webpelangi_connector import (
    _web_content_config, _sync_hotel_profile, _sync_faq, WEBSITE_ROOMS_URL,
)


# ---- Rate Limiting ----
# In-memory murni (tanpa dependency baru/Redis) - cukup untuk deployment single-instance
# seperti sekarang. Melindungi endpoint yang benar-benar publik lewat internet:
# /auth/login (brute force password) dan /webhook/waha (endpoint token-only, bisa
# dihajar dari IP mana pun kalau token bocor/ditebak).
_rate_limit_buckets: Dict[str, List[float]] = {}


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limiter(max_requests: int, window_seconds: int):
    async def _check(request: Request) -> None:
        key = f"{request.url.path}:{_client_ip(request)}"
        now = time.time()
        cutoff = now - window_seconds
        bucket = [t for t in _rate_limit_buckets.get(key, []) if t >= cutoff]
        if len(bucket) >= max_requests:
            _rate_limit_buckets[key] = bucket
            raise HTTPException(429, "Terlalu banyak permintaan, coba lagi sebentar lagi")
        bucket.append(now)
        _rate_limit_buckets[key] = bucket
        # Katup pengaman terhadap pertumbuhan dict tak terbatas (banyak IP unik/serangan
        # terdistribusi) - jarang kena di skala 1 homestay, tapi murah untuk dijaga.
        if len(_rate_limit_buckets) > 20000:
            _rate_limit_buckets.clear()
    return _check


async def _audit_log(user: dict, action: str, detail: str = "") -> None:
    """AuditLogger - "siapa ubah apa kapan" untuk aksi admin sensitif. Pola sama dengan
    `log_activity` di Pelangi PMS (collection `audit_log`, dibaca dashboard sendiri lewat
    GET /audit-log). Cakupan tahap 1: konfigurasi Integrasi PMS (URL/API key/capability/
    webhook token), koneksi WAHA (connect/disconnect), dan Human Handover (handover/
    resume/reply/close) - permukaan admin paling sensitif & paling baru dibangun. CRUD
    entity lain (rooms/menu/kb/dst) belum diinstrumentasi, menyusul kalau dibutuhkan -
    pola & tabelnya sudah siap dipakai tanpa perubahan skema."""
    try:
        await db.audit_log.insert_one({
            "id": new_id(), "user_id": user.get("id"), "user_email": user.get("email"),
            "action": action, "detail": (detail or "")[:500], "at": utc_now_iso(),
        })
    except Exception:
        pass  # logging tidak boleh menggagalkan alur utama


app = FastAPI(title="Pelangi AI — Customer Service Platform")
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
async def login(body: LoginRequest, _: None = Depends(rate_limiter(10, 60))):
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


# SERVICE REQUESTS: dihapus 2026-07-19 — sebelumnya endpoint lokal (db.service_requests)
# yang tidak pernah dilihat staf PMS (bug). Sekarang create_service_request diteruskan
# langsung ke Pelangi PMS (lihat _tool_create_service_request, reuse endpoint tiket) - PMS
# jadi satu-satunya tempat staf melihat & menyelesaikan permintaan ini (halaman baru
# /service-requests di PMS), tidak ada lagi salinan lokal di ai-chat-bot.


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
    await _audit_log(user, "conversation_handover", f"conv {conv_id} ({doc.get('guest_name') or doc.get('whatsapp') or '-'})")
    doc["id"] = doc.pop("_id")
    return doc


@api.patch("/conversations/{conv_id}/close")
async def convs_close(conv_id: str, user=Depends(get_current_user)):
    await db.conversations.update_one(
        {"_id": conv_id},
        {"$set": {"status": "closed", "updated_at": utc_now_iso()}},
    )
    await _audit_log(user, "conversation_close", f"conv {conv_id}")
    return {"ok": True}


@api.patch("/conversations/{conv_id}/resume")
async def convs_resume(conv_id: str, user=Depends(get_current_user)):
    """Kebalikan dari handover - staf selesai menangani, AI aktif lagi menjawab pesan
    tamu berikutnya secara otomatis."""
    conv = await db.conversations.find_one({"_id": conv_id})
    if not conv:
        raise HTTPException(404, "Not found")
    await db.conversations.update_one(
        {"_id": conv_id},
        {"$set": {"status": "active", "resolution": "handover", "updated_at": utc_now_iso()}},
    )
    await _audit_log(user, "conversation_resume_ai", f"conv {conv_id} ({conv.get('guest_name') or conv.get('whatsapp') or '-'})")
    doc = await db.conversations.find_one({"_id": conv_id})
    doc["id"] = doc.pop("_id")
    return doc


class ConvReplyIn(BaseModel):
    message: str


@api.post("/conversations/{conv_id}/reply")
async def convs_reply(conv_id: str, body: ConvReplyIn, user=Depends(get_current_user)):
    """Staf mengetik & mengirim balasan manual ke tamu - mengisi gap "Human Response" yang
    sebelumnya tidak ada (handover cuma menandai status, tidak pernah benar-benar
    mengirim apa pun ke tamu). Kalau channel WhatsApp, balasan sungguhan dikirim lewat WAHA
    persis seperti balasan AI. Status TIDAK otomatis berubah - staf tetap pegang kendali
    sampai eksplisit menekan "Aktifkan AI Lagi" (`/resume`)."""
    conv = await db.conversations.find_one({"_id": conv_id})
    if not conv:
        raise HTTPException(404, "Not found")
    text = (body.message or "").strip()
    if not text:
        raise HTTPException(400, "Pesan tidak boleh kosong")

    admin_msg = {
        "role": "assistant", "content": text, "timestamp": utc_now_iso(),
        "intent": None, "from_admin": True, "admin_name": user.get("email") or user.get("id"),
    }
    messages = conv.get("messages", []) + [admin_msg]
    await db.conversations.update_one(
        {"_id": conv_id}, {"$set": {"messages": messages, "updated_at": utc_now_iso()}},
    )

    sent_to_whatsapp = False
    if conv.get("channel") == "whatsapp" and conv.get("whatsapp"):
        # Balas lewat nomor WA yang SAMA dengan yang tamu hubungi (bisa beda-beda sejak
        # multi-nomor per AI bot, 2026-07-19) - fallback ke default kalau conv lama belum
        # punya field ini (dibuat sebelum fitur ini ada).
        await _waha_send_text(f"{conv['whatsapp']}@c.us", text, session=conv.get("waha_session") or WAHA_SESSION)
        sent_to_whatsapp = True

    await _audit_log(user, "conversation_manual_reply", f"conv {conv_id}: {text[:200]}")
    return {"ok": True, "sent_to_whatsapp": sent_to_whatsapp}


# ---------------------------------------------------------------------------
# CHAT (AI Guest Assistant)
# ---------------------------------------------------------------------------
async def _load_active_prompt() -> str:
    doc = await db.prompts.find_one({"is_active": True})
    return (doc or {}).get("content") or DEFAULT_SYSTEM_PROMPT


async def _system_prompt_for(bot: Optional[dict], room_types: Optional[List[str]] = None) -> str:
    """Satu-satunya jalur pembentuk system prompt, dipakai baik ada AIBot spesifik maupun
    tidak (jalur legacy /prompt) - GUARDRAIL/MENGIRIM FOTO/daftar Tool/tipe kamar SELALU
    dirender fresh oleh build_dynamic_prompt() dari data live, tidak pernah dari salinan
    statis yang bisa basi atau nama tipe kamar yang di-hardcode."""
    if bot:
        return build_dynamic_prompt(bot, room_types=room_types)
    header = await _load_active_prompt()
    return build_dynamic_prompt({"prompt": header, "tool_codes": ALL_TOOL_CODES}, room_types=room_types)


async def _load_bot(bot_id: Optional[str], bot_code: Optional[str]) -> dict:
    """Load a bot config; falls back to booking_marketing."""
    if bot_id:
        doc = await db.ai_bots.find_one({"_id": bot_id})
        if doc:
            return doc
    if bot_code:
        doc = await db.ai_bots.find_one({"code": bot_code})
        if doc:
            return doc
    doc = await db.ai_bots.find_one({"code": "booking_marketing"})
    if doc:
        return doc
    return await db.ai_bots.find_one({}) or {}










def _normalize_phone(no_hp: str) -> str:
    """Bentuk kanonik 62xxx - mencegah 1 tamu punya 2 profil terpisah gara-gara format
    nomor beda (0812... vs 62812...), sama pola dengan normalisasi di sisi PMS."""
    digits = re.sub(r"\D", "", no_hp or "")
    if digits.startswith("0"):
        digits = "62" + digits[1:]
    return digits


async def _touch_guest_profile(whatsapp: Optional[str], guest_name: Optional[str], is_new_conversation: bool) -> None:
    """Memory (tahap 1 - short/long/preference): dipanggil tiap giliran chat supaya profil
    tamu selalu punya nama & waktu terakhir dilihat terkini, DAN supaya percakapan baru
    dari nomor yang sama tercatat sebagai kunjungan berulang (total_conversations)."""
    key = _normalize_phone(whatsapp or "")
    if not key:
        return
    updates: Dict[str, Any] = {"last_seen_at": utc_now_iso()}
    if guest_name:
        updates["nama"] = guest_name
    op: Dict[str, Any] = {"$set": updates, "$setOnInsert": {"created_at": utc_now_iso()}}
    if is_new_conversation:
        op["$inc"] = {"total_conversations": 1}
    await db.guest_profiles.update_one({"_id": key}, op, upsert=True)


async def _get_guest_profile(whatsapp: Optional[str]) -> Optional[dict]:
    key = _normalize_phone(whatsapp or "")
    if not key:
        return None
    return await db.guest_profiles.find_one({"_id": key})


async def _build_context(query: Optional[str] = None, bot: Optional[dict] = None, whatsapp: Optional[str] = None,
                          rooms: Optional[List[dict]] = None) -> str:
    if rooms is None:
        rooms = await _pms_ketersediaan()
    menu = await db.menu.find({}).to_list(500)
    kb_q = {"is_active": True}
    if bot and bot.get("knowledge_categories"):
        kb_q["category"] = {"$in": bot["knowledge_categories"]}
    kb = await db.knowledge_base.find(kb_q).to_list(500)
    settings = await db.settings.find_one({"_id": "singleton"}) or {}
    # Foto kamar (nama + galeri) - koleksi db.rooms LOKAL ai-chat-bot (bukan _pms_ketersediaan
    # di atas, yang cuma tipe/tarif/stok live dari PMS, TIDAK ADA field foto sama sekali).
    # Ditemukan 2026-07-19 dari laporan user: tanpa ini AI cuma punya akses ke foto KB
    # (galeri/facilities umum), jadi saat diminta foto kamar AI asal kirim foto KB yang
    # salah - bukan foto kamar sungguhan.
    room_photos = await db.rooms.find({}, {"name": 1, "room_type": 1, "photo_url": 1, "images": 1}).to_list(50)
    for r in room_photos:
        # 1 link rapi ke halaman Rooms publik (deep-link ?room=<slug> auto-buka galeri
        # kamar itu) - permintaan user 2026-07-19: banyak link foto Cloudinary mentah
        # bikin tamu bingung, satu link ke halaman website jauh lebih rapi.
        r["website_url"] = f"{WEBSITE_ROOMS_URL}?room={(r.get('room_type') or '').lower()}"
    base = build_context_block(rooms, menu, kb, settings, room_photos)

    # Business Rules (Rule Engine tahap 1) - SENGAJA terpisah dari Knowledge Base (KB isinya
    # info umum hotel/wisata/FAQ, ini kebijakan operasional dari PMS: DP/cancellation/
    # checkin/checkout/promo/dll). Cache hasil sync, bukan realtime call per pesan.
    rules = await db.business_rules_cache.find({}).to_list(200)
    if rules:
        parts = ["\n# ATURAN BISNIS (dari PMS, WAJIB diikuti - jangan mengarang kebijakan sendiri)"]
        for r in rules:
            parts.append(f"- [{r.get('category')}] {r.get('title')}: {r.get('description')}")
        base = base + "\n" + "\n".join(parts)

    # Memory (Long Memory + Preference) - profil tamu lintas-percakapan, BUKAN riwayat
    # pesan mentah (itu Short Memory, sudah otomatis lewat conv["messages"] per sesi).
    # Cuma ditampilkan kalau tamu ini pernah muncul sebelumnya - tamu baru tidak dapat
    # section ini sama sekali (tidak ada yang perlu diingat).
    profile = await _get_guest_profile(whatsapp)
    if profile and (profile.get("total_conversations", 0) > 0):
        parts = [f"\n# PROFIL TAMU (dari percakapan sebelumnya, kunjungan ke-{profile.get('total_conversations', 1) + 1})"]
        if profile.get("nama"):
            parts.append(f"- Nama: {profile['nama']}")
        for fact in (profile.get("preferensi") or []):
            parts.append(f"- {fact}")
        parts.append("(Gunakan info ini untuk menyapa lebih personal & tidak menanyakan ulang hal yang sudah diketahui - TETAP verifikasi untuk data sensitif seperti booking.)")
        base = base + "\n" + "\n".join(parts)

    # RAG augmentation
    if query:
        try:
            chunks = await db.rag_chunks.find({}, {"_id": 1, "doc_id": 1, "doc_title": 1, "text": 1, "embedding": 1}).to_list(2000)
            chunks_norm = [{"id": c["_id"], "doc_id": c["doc_id"], "doc_title": c.get("doc_title", "doc"), "text": c["text"], "embedding": c.get("embedding")} for c in chunks]
            hits = await hybrid_search(query, chunks_norm, k=5)
            rag = build_rag_context(hits)
            if rag:
                base = base + "\n\n" + rag
        except Exception as e:
            logger.warning(f"RAG failed: {e}")
    return base


# ---------------------------------------------------------------------------
# TOOL MANAGER (PRD v2) - registry tool AI: satu sumber kebenaran untuk nama, handler,
# DAN syarat izin (tool_codes bot apa yang membuka tool ini) per tool. Nambah tool baru
# = tulis 1 fungsi + `@register_tool(...)`, tidak perlu sentuh dispatcher (_handle_tool)
# atau permission-gating di _run_chat_turn - keduanya baca registry yang sama.
# ---------------------------------------------------------------------------
TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {}


def register_tool(name: str, required_tool_codes: Optional[set] = None):
    """`required_tool_codes` kosong/None = tool selalu diizinkan untuk semua bot (baseline
    capability seperti remember_guest_fact, bukan aksi bisnis yang perlu dibatasi)."""
    def deco(fn):
        TOOL_REGISTRY[name] = {"handler": fn, "required_tool_codes": required_tool_codes or set()}
        return fn
    return deco


@register_tool("check_availability", {"check_availability"})
async def _tool_check_availability(args: dict, conv: dict) -> dict:
    try:
        rooms = await _pms_ketersediaan(
            tanggal=args.get("tanggal_checkin"), tipe=args.get("tipe"),
            tanggal_checkout=args.get("tanggal_checkout"),
        )
        return {"ok": True, "tool": "check_availability", "result": rooms}
    except Exception as e:
        return {"ok": False, "tool": "check_availability", "error": str(e)}


@register_tool("create_booking", {"create_booking"})
async def _tool_create_booking(args: dict, conv: dict) -> dict:
    try:
        logging.getLogger("pms").info(f"create_booking args diterima dari AI: {args}")
        required = ["guest_name", "whatsapp", "tipe", "room_tipe", "tanggal_checkin"]
        for k in required:
            if not args.get(k):
                return {"ok": False, "tool": "create_booking", "error": f"missing {k}"}
        if args["tipe"] not in ("day_use", "menginap"):
            return {"ok": False, "tool": "create_booking", "error": "tipe harus 'day_use' atau 'menginap'"}
        hasil = await _pms_buat_booking_request(args)
        if not hasil.get("ok"):
            return {"ok": False, "tool": "create_booking", "error": hasil.get("error")}
        await db.conversations.update_one({"_id": conv["_id"]}, {"$set": {"booking_created": True}})
        br = hasil.get("booking_request") or {}
        result = {"ok": True, "tool": "create_booking", "booking_request_id": br.get("id"), "kode": br.get("kode")}
        # Program Loyalitas Kedatangan - kalau tamu dapat diskon member, sampaikan di sini
        # supaya AI menyebutkannya ke tamu (lihat instruksi di TOOL_DOCS ai_service.py).
        if br.get("preview_diskon_persen"):
            result["diskon_member_persen"] = br["preview_diskon_persen"]
            result["kedatangan_ke"] = br["preview_kedatangan_ke"]
        return result
    except Exception as e:
        return {"ok": False, "tool": "create_booking", "error": str(e)}


SERVICE_TYPE_LABEL = {
    "extra_bed": "Extra Bed", "extra_towel": "Extra Towel", "mineral_water": "Air Mineral",
    "cleaning": "Cleaning", "laundry": "Laundry", "motor_rental": "Sewa Motor",
    "airport_pickup": "Airport Pickup", "extra_breakfast": "Extra Breakfast",
}


@register_tool("create_service_request", {"restaurant_order", "laundry_request", "housekeeping_request",
                                           "room_service", "airport_pickup", "motor_rental"})
async def _tool_create_service_request(args: dict, conv: dict) -> dict:
    """Diteruskan ke Pelangi PMS sebagai tiket (tipe='service_request', reuse mekanisme
    komplain/maintenance yang sama supaya staf benar-benar melihat & bisa menindaklanjuti -
    sebelumnya cuma tersimpan di db.service_requests lokal ai-chat-bot yang tidak pernah
    dilihat staf PMS (bug ditemukan 2026-07-19)."""
    try:
        service_type = args.get("service_type")
        if not service_type:
            return {"ok": False, "tool": "create_service_request", "error": "missing service_type"}
        label = SERVICE_TYPE_LABEL.get(service_type, service_type)
        qty = int(args.get("quantity", 1))
        notes = (args.get("notes") or "").strip()
        deskripsi = f"{label} x{qty}" + (f". Catatan: {notes}" if notes else "")
        whatsapp = args.get("whatsapp") or conv.get("whatsapp") or ""
        guest_name = args.get("guest_name") or conv.get("guest_name") or ""
        hasil = await _pms_buat_tiket("service_request", deskripsi, whatsapp, guest_name)
        if not hasil.get("ok"):
            return {"ok": False, "tool": "create_service_request", "error": hasil.get("error")}
        tiket = hasil.get("tiket") or {}
        return {"ok": True, "tool": "create_service_request", "request_id": tiket.get("id")}
    except Exception as e:
        return {"ok": False, "tool": "create_service_request", "error": str(e)}


@register_tool("create_maintenance_ticket", {"maintenance_request", "complaint_ticket"})
async def _tool_create_maintenance_ticket(args: dict, conv: dict) -> dict:
    try:
        tipe = args.get("tipe")
        if tipe not in ("complaint", "maintenance"):
            return {"ok": False, "tool": "create_maintenance_ticket", "error": "tipe harus 'complaint' atau 'maintenance'"}
        deskripsi = (args.get("deskripsi") or "").strip()
        if not deskripsi:
            return {"ok": False, "tool": "create_maintenance_ticket", "error": "missing deskripsi"}
        whatsapp = args.get("whatsapp") or conv.get("whatsapp") or ""
        guest_name = args.get("guest_name") or conv.get("guest_name") or ""
        hasil = await _pms_buat_tiket(tipe, deskripsi, whatsapp, guest_name)
        if not hasil.get("ok"):
            return {"ok": False, "tool": "create_maintenance_ticket", "error": hasil.get("error")}
        tiket = hasil.get("tiket") or {}
        return {"ok": True, "tool": "create_maintenance_ticket", "tiket_id": tiket.get("id")}
    except Exception as e:
        return {"ok": False, "tool": "create_maintenance_ticket", "error": str(e)}


@register_tool("lookup_booking", {"lookup_booking"})
async def _tool_lookup_booking(args: dict, conv: dict) -> dict:
    wa = args.get("whatsapp") or conv.get("whatsapp")
    if not wa:
        return {"ok": False, "tool": "lookup_booking", "error": "missing whatsapp"}
    hasil = await _pms_status_booking(wa)
    if not hasil.get("ok"):
        return {"ok": False, "tool": "lookup_booking", "error": hasil.get("error")}
    return {"ok": True, "tool": "lookup_booking", "result": hasil.get("permintaan") or []}


@register_tool("cancel_booking", {"cancel_booking"})
async def _tool_cancel_booking(args: dict, conv: dict) -> dict:
    """Non-binding - AI TIDAK PERNAH langsung membatalkan booking sungguhan (sama seperti
    create_booking), cuma menyampaikan info ke PMS lewat _pms_ajukan_pembatalan; PMS
    mencatat & staf approve/reject manual. `kode` WAJIB kode booking sungguhan (BKO-...,
    didapat dari lookup_booking -> booking_ringkasan.kode), bukan kode booking_request."""
    kode = (args.get("kode") or "").strip()
    if not kode:
        return {"ok": False, "tool": "cancel_booking", "error": "missing kode - pakai lookup_booking dulu untuk dapat kode booking"}
    wa = args.get("whatsapp") or conv.get("whatsapp")
    if not wa:
        return {"ok": False, "tool": "cancel_booking", "error": "missing whatsapp"}
    hasil = await _pms_ajukan_pembatalan(kode, wa, args.get("alasan") or "")
    if not hasil.get("ok"):
        return {"ok": False, "tool": "cancel_booking", "error": hasil.get("error")}
    return {
        "ok": True, "tool": "cancel_booking", "kode": hasil.get("kode"),
        "policy_label": hasil.get("policy_label"), "refund_estimate": hasil.get("refund_estimate"),
    }


@register_tool("request_handover", {"request_handover"})
async def _tool_request_handover(args: dict, conv: dict) -> dict:
    await db.conversations.update_one(
        {"_id": conv["_id"]},
        {"$set": {"status": "waiting_admin", "resolution": "handover", "updated_at": utc_now_iso()}},
    )
    return {"ok": True, "tool": "request_handover"}


@register_tool("remember_guest_fact")  # baseline memory hygiene, selalu diizinkan (lihat docstring register_tool)
async def _tool_remember_guest_fact(args: dict, conv: dict) -> dict:
    wa = args.get("whatsapp") or conv.get("whatsapp")
    fact = (args.get("fact") or "").strip()
    if not wa or not fact:
        return {"ok": False, "tool": "remember_guest_fact", "error": "missing whatsapp/fact"}
    key = _normalize_phone(wa)
    existing = await db.guest_profiles.find_one({"_id": key})
    facts = (existing or {}).get("preferensi") or []
    if fact not in facts:  # cegah duplikat kalau AI menyimpan hal yang sama berkali-kali
        facts.append(fact)
        facts = facts[-20:]  # cap wajar per tamu, fakta terlama otomatis terbuang
    await db.guest_profiles.update_one(
        {"_id": key}, {"$set": {"preferensi": facts}, "$setOnInsert": {"created_at": utc_now_iso()}}, upsert=True,
    )
    return {"ok": True, "tool": "remember_guest_fact"}


async def _handle_tool(tool: str, args: dict, conv: dict) -> Optional[dict]:
    """Tool Manager dispatch - cari handler tool di TOOL_REGISTRY (lihat @register_tool
    di atas)."""
    # Nomor WA & nama dari `conv` (asal koneksi WA/simulator sungguhan) SELALU dipakai kalau
    # ada, dan MENIMPA apa pun yang LLM tulis di args - ditemukan lewat pengujian nyata
    # (2026-07-18) bahwa LLM kadang mengisi whatsapp dengan teks placeholder literal dari
    # contoh di TOOL_DOCS (mis. "...") alih-alih nomor tamu asli, dan karena string itu
    # tidak kosong, logika `args.get("whatsapp") or conv.get("whatsapp")` yang lama tidak
    # pernah fallback - tiket/booking/fakta tersimpan dengan nomor sampah, bukan tamu asli.
    if conv.get("whatsapp"):
        args = {**args, "whatsapp": conv["whatsapp"]}
    if conv.get("guest_name"):
        args = {**args, "guest_name": args.get("guest_name") or conv["guest_name"]}

    entry = TOOL_REGISTRY.get(tool)
    if not entry:
        return {"ok": False, "tool": tool, "error": "unknown tool"}
    return await entry["handler"](args, conv)


async def _run_chat_turn(
    session_id: str, message: str, guest_name: Optional[str], whatsapp: Optional[str],
    bot_id: Optional[str], bot_code: Optional[str], channel: str = "simulator",
    waha_session: Optional[str] = None,
) -> dict:
    """Inti alur 1 giliran chat (load bot, build context, panggil AI, tool-calling,
    simpan percakapan) — dipakai `/chat/message` (simulator, staf login) DAN webhook WAHA
    (`/webhook/waha`, tamu WhatsApp asli) supaya tidak ada logika AI ganda yang bisa
    saling menyimpang antara jalur uji coba staf dan jalur tamu sungguhan.

    `waha_session` = nomor WA (session WAHA) mana yang menerima pesan ini - disimpan di
    percakapan supaya balasan staf manual (human handover, bisa terjadi jauh setelah
    webhook request ini selesai) tetap keluar lewat nomor yang SAMA dengan yang tamu
    hubungi, bukan selalu nomor default (2026-07-19, multi-nomor WA per AI bot)."""
    started = time.time()

    conv = await db.conversations.find_one({"session_id": session_id})
    is_new_conversation = conv is None
    if not conv:
        conv = {
            "_id": new_id(),
            "session_id": session_id,
            "guest_name": guest_name,
            "whatsapp": whatsapp,
            "channel": channel,
            "waha_session": waha_session,
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

    await _touch_guest_profile(conv.get("whatsapp") or whatsapp, conv.get("guest_name") or guest_name, is_new_conversation)

    # Append user message
    user_msg = {"role": "user", "content": message, "timestamp": utc_now_iso()}
    conv["messages"].append(user_msg)

    # Human Handover: staf sudah mengambil alih (status waiting_admin) - AI BERHENTI
    # menjawab sampai staf balas manual (POST /conversations/{id}/reply) atau aktifkan AI
    # lagi (PATCH .../resume). Pesan tamu tetap tersimpan supaya staf lihat riwayat lengkap,
    # cuma tidak dibalas otomatis - staf yang pegang kendali penuh.
    if conv.get("status") == "waiting_admin":
        await db.conversations.update_one(
            {"_id": conv["_id"]}, {"$set": {"messages": conv["messages"], "updated_at": utc_now_iso()}},
        )
        return {
            "session_id": session_id, "conversation_id": conv["_id"], "reply": None,
            "tool_used": None, "tool_result": None, "response_time_ms": int((time.time() - started) * 1000),
        }

    # Load bot + build dynamic prompt
    bot = await _load_bot(bot_id, bot_code)
    if bot:
        conv["bot_id"] = bot.get("_id")
        conv["bot_code"] = bot.get("code")
    allowed_tool_codes = set(bot.get("tool_codes", [])) if bot else set()
    allowed_services = set(bot.get("allowed_service_types", [])) if bot else set()

    # Build prompt inputs - ketersediaan diambil SEKALI, dipakai untuk context (harga/stok)
    # DAN prompt (daftar tipe kamar valid untuk tool), supaya tidak 2x panggil PMS per pesan
    # dan supaya tipe kamar yang disebut AI selalu konsisten dengan yang di-tampilkan.
    rooms_now = await _pms_ketersediaan()
    room_types = sorted({r["tipe"] for r in rooms_now if r.get("tipe")})
    system_prompt = await _system_prompt_for(bot, room_types=room_types)
    context = await _build_context(query=message, bot=bot, whatsapp=conv.get("whatsapp") or whatsapp, rooms=rooms_now)
    history_text = compact_history(conv["messages"][:-1], max_turns=12)

    settings_doc = await db.settings.find_one({"_id": "singleton"}) or {}
    llm_provider = settings_doc.get("llm_provider") or DEFAULT_PROVIDER
    llm_model = settings_doc.get("llm_model") or DEFAULT_MODEL

    # First AI turn
    raw = await ai_reply(session_id, system_prompt, context, history_text, message, llm_provider, llm_model)
    clean_text, tool, args = parse_tool_call(raw)

    tool_result = None
    if tool:
        # Permission gating - baca langsung dari TOOL_REGISTRY (Tool Manager), satu sumber
        # kebenaran yang sama dipakai _handle_tool untuk dispatch. required_tool_codes
        # kosong (mis. remember_guest_fact) otomatis lolos gate ini, tanpa special-case.
        tool_entry = TOOL_REGISTRY.get(tool)
        if not tool_entry:
            tool_result = {"ok": False, "tool": tool, "error": f"tool '{tool}' tidak dikenal"}
        elif allowed_tool_codes and tool_entry["required_tool_codes"] and not (tool_entry["required_tool_codes"] & allowed_tool_codes):
            tool_result = {"ok": False, "tool": tool, "error": f"tool '{tool}' tidak diizinkan untuk bot ini"}
        elif tool == "create_service_request" and allowed_services and args.get("service_type") not in allowed_services:
            tool_result = {"ok": False, "tool": tool, "error": f"service_type '{args.get('service_type')}' tidak diizinkan untuk bot ini"}
        else:
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
        follow_raw = await ai_reply(session_id, system_prompt, context, history_after, follow_up_user, llm_provider, llm_model)
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
    if bot:
        update["bot_id"] = bot.get("_id")
        update["bot_code"] = bot.get("code")
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


@api.post("/chat/message")
async def chat_message(body: ChatSendRequest, user=Depends(get_current_user)):
    session_id = body.session_id or str(uuid.uuid4())
    return await _run_chat_turn(
        session_id, body.message, body.guest_name, body.whatsapp,
        body.bot_id, body.bot_code, channel="simulator",
    )




def _webhook_url_for(token: str) -> str:
    return f"http://host.docker.internal:8002/api/webhook/waha?token={token}"


@api.get("/waha/sessions")
async def waha_sessions_list(user=Depends(get_current_user)):
    """Semua nomor WA yang ada (WAHA session) digabung dengan AI bot yang terhubung ke
    masing-masing (kalau ada) - satu tempat untuk lihat semua koneksi sekaligus, dipakai
    panel Koneksi WhatsApp di tiap bot (BotDetail) maupun ringkasan kalau dibutuhkan."""
    sessions = await _waha_list_sessions()
    bots = await db.ai_bots.find({"channel_type": "whatsapp", "channel_id": {"$ne": None, "$ne": ""}}).to_list(50)
    bot_by_session = {b["channel_id"]: {"id": b["_id"], "name": b.get("name")} for b in bots}
    out = []
    for s in sessions:
        out.append({**s, "linked_bot": bot_by_session.get(s.get("name"))})
    return out


@api.get("/waha/sessions/{session}/status")
async def waha_session_status(session: str, user=Depends(get_current_user)):
    _, data = await _waha_call("GET", f"/api/sessions/{session}")
    return data


class WahaConnectIn(BaseModel):
    phone_number: str
    bot_id: Optional[str] = None  # kalau diisi, session ini otomatis ditautkan ke bot ini


@api.post("/waha/sessions/{session}/connect")
async def waha_session_connect(session: str, body: WahaConnectIn, user=Depends(get_current_user)):
    """Mulai/pairing ulang 1 nomor WhatsApp (session WAHA) lewat kode angka (bukan QR -
    lebih gampang dipakai tanpa perlu scan gambar). Kalau session belum pernah dibuat di
    WAHA (nomor baru), otomatis dibuat dulu. PENTING: WhatsApp membatasi sementara akun
    yang terlalu sering connect/disconnect dalam waktu singkat ("reachout timelock") -
    jangan panggil endpoint ini berulang-ulang kalau baru saja gagal, tunggu beberapa menit."""
    phone = (body.phone_number or "").strip()
    if not phone:
        raise HTTPException(400, "phone_number wajib diisi (format 62xxx)")

    cfg = await _pms_config()
    token = cfg.get("webhook_token") or WAHA_WEBHOOK_TOKEN
    await _waha_ensure_session(session, _webhook_url_for(token))

    _, cur = await _waha_call("GET", f"/api/sessions/{session}")
    if cur.get("status") not in ("SCAN_QR_CODE",):
        await _waha_call("POST", f"/api/sessions/{session}/logout")
        await asyncio.sleep(2)
        start_status, start_data = await _waha_call("POST", f"/api/sessions/{session}/start")
        if start_status >= 400:
            raise HTTPException(start_status, start_data.get("error") or "Gagal memulai sesi WAHA")
        await asyncio.sleep(3)

    code_status, code_data = await _waha_call(
        "POST", f"/api/{session}/auth/request-code", {"phoneNumber": phone},
    )
    if code_status >= 400:
        raise HTTPException(code_status, code_data.get("message") or code_data.get("error") or "Gagal meminta kode pairing")

    if body.bot_id:
        await db.ai_bots.update_one(
            {"_id": body.bot_id}, {"$set": {"channel_type": "whatsapp", "channel_id": session}},
        )
    await _audit_log(user, "waha_connect", f"session {session}, phone {phone}")
    return code_data


@api.post("/waha/sessions/{session}/disconnect")
async def waha_session_disconnect(session: str, user=Depends(get_current_user)):
    status, data = await _waha_call("POST", f"/api/sessions/{session}/logout")
    if status >= 400:
        raise HTTPException(status, data.get("error") or "Gagal memutus sesi WAHA")
    await _audit_log(user, "waha_disconnect", f"session {session}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# PMS INTEGRATION PANEL (configuration layer - lihat catatan arsitektur di atas)
# ---------------------------------------------------------------------------
def _pms_config_public(cfg: dict) -> dict:
    out = {k: v for k, v in cfg.items() if k != "_id"}
    return out


@api.get("/pms-integration")
async def get_pms_integration(user=Depends(get_current_user)):
    return _pms_config_public(await _pms_config())


@api.put("/pms-integration")
async def update_pms_integration(body: dict, user=Depends(get_current_user)):
    updates = {}
    for k in ("pms_base_url", "pms_api_key", "bot_whatsapp_number"):
        if k in body and body[k] is not None:
            updates[k] = body[k]
    if "endpoints" in body and isinstance(body["endpoints"], dict):
        cfg = await _pms_config()
        updates["endpoints"] = {**cfg["endpoints"], **{k: v for k, v in body["endpoints"].items() if k in PMS_DEFAULT_ENDPOINTS}}
    if not updates:
        raise HTTPException(400, "Tidak ada field yang diubah")
    updates["updated_at"] = utc_now_iso()
    await db.pms_integration_config.update_one({"_id": "singleton"}, {"$set": updates}, upsert=True)
    # Field saja yang dicatat, BUKAN nilainya (pms_api_key rahasia, jangan bocor ke log)
    await _audit_log(user, "pms_integration_update", f"field diubah: {', '.join(sorted(updates.keys() - {'updated_at'}))}")
    return _pms_config_public(await _pms_config())


@api.post("/pms-integration/capabilities")
async def update_pms_capabilities(body: dict, user=Depends(get_current_user)):
    cfg = await _pms_config()
    caps = dict(cfg["capabilities"])
    changed = []
    for k, v in (body or {}).items():
        if k in PMS_DEFAULT_CAPABILITIES and isinstance(v, bool) and caps.get(k) != v:
            caps[k] = v
            changed.append(f"{k}={v}")
    await db.pms_integration_config.update_one(
        {"_id": "singleton"}, {"$set": {"capabilities": caps, "updated_at": utc_now_iso()}}, upsert=True,
    )
    if changed:
        await _audit_log(user, "pms_capability_toggle", ", ".join(changed))
    return _pms_config_public(await _pms_config())


@api.post("/pms-integration/regenerate-webhook-token")
async def regenerate_pms_webhook_token(user=Depends(get_current_user)):
    """Regenerate token webhook masuk (dipakai WAHA memanggil /webhook/waha di sini) -
    otomatis update juga konfigurasi webhook di WAHA supaya tidak perlu langkah manual
    tambahan (dulu ini harus di-sinkronkan manual lewat terminal server)."""
    new_token = secrets.token_hex(20)
    await db.pms_integration_config.update_one(
        {"_id": "singleton"}, {"$set": {"webhook_token": new_token, "updated_at": utc_now_iso()}}, upsert=True,
    )
    await _audit_log(user, "pms_webhook_token_regenerate")
    if WAHA_BASE_URL and WAHA_API_KEY:
        await _waha_call(
            "PUT", f"/api/sessions/{WAHA_SESSION}",
            {"config": {"webhooks": [{"url": f"http://host.docker.internal:8002/api/webhook/waha?token={new_token}", "events": ["message"]}]}},
        )
    return _pms_config_public(await _pms_config())


@api.post("/pms-integration/test")
async def test_pms_integration(user=Depends(get_current_user)):
    """Test Connection - HANYA memanggil endpoint baca (ketersediaan), TIDAK PERNAH
    memanggil endpoint tulis (booking-request/tiket) untuk uji coba, supaya tidak
    membuat data palsu di PMS produksi (pelajaran dari insiden testing WAHA hari ini)."""
    cfg = await _pms_config()
    result = {"ok": False, "message": "", "latency_ms": None, "version": None, "tested_at": utc_now_iso()}
    if not cfg["pms_base_url"] or not cfg["pms_api_key"]:
        result["message"] = "PMS URL / API Key belum diisi"
    else:
        started = time.time()
        path = cfg["endpoints"]["ketersediaan_path"]
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                resp = await http.get(
                    f"{cfg['pms_base_url'].rstrip('/')}{path}",
                    headers={"Authorization": f"Bearer {cfg['pms_api_key']}"},
                )
            latency_ms = int((time.time() - started) * 1000)
            result["latency_ms"] = latency_ms
            if resp.status_code == 200:
                data = resp.json()
                n = len(data.get("ketersediaan") or [])
                result["ok"] = True
                result["message"] = f"Terhubung - {n} tipe kamar ditemukan di PMS"
                await _pms_log(path, "GET", 200, latency_ms, True, "test connection")
            else:
                result["message"] = f"PMS merespons HTTP {resp.status_code}"
                await _pms_log(path, "GET", resp.status_code, latency_ms, False, "test connection")
        except Exception as e:
            result["message"] = f"Gagal terhubung: {e}"
            await _pms_log(path, "GET", None, int((time.time() - started) * 1000), False, f"test connection: {e}")

    await db.pms_integration_config.update_one(
        {"_id": "singleton"},
        {"$set": {
            "last_test_at": result["tested_at"], "last_test_ok": result["ok"],
            "last_test_latency_ms": result["latency_ms"], "last_test_message": result["message"],
        }},
        upsert=True,
    )
    return result


@api.get("/pms-integration/logs")
async def pms_integration_logs(limit: int = Query(50, le=200), user=Depends(get_current_user)):
    docs = await db.pms_integration_logs.find({}).sort("at", -1).to_list(limit)
    return [{**d, "id": d.pop("_id")} for d in docs]


@api.get("/audit-log")
async def audit_log_list(action: Optional[str] = None, limit: int = Query(100, le=500), user=Depends(get_current_user)):
    q: Dict[str, Any] = {}
    if action:
        q["action"] = action
    docs = await db.audit_log.find(q, {"_id": 0}).sort("at", -1).to_list(limit)
    return docs


@api.get("/audit-log/actions")
async def audit_log_actions(user=Depends(get_current_user)):
    return sorted(await db.audit_log.distinct("action"))


@api.get("/guest-profiles")
async def guest_profiles_list(search: Optional[str] = None, limit: int = Query(100, le=500), user=Depends(get_current_user)):
    """Memory tahap 1 - profil tamu lintas-percakapan (nama, preferensi/fakta yang diingat
    AI, jumlah kunjungan). Read-only dari dashboard - AI yang mengisi lewat tool
    remember_guest_fact + pembaruan otomatis tiap giliran chat, staf cukup melihat."""
    q: Dict[str, Any] = {}
    if search:
        q["$or"] = [
            {"_id": {"$regex": re.escape(search)}},
            {"nama": {"$regex": re.escape(search), "$options": "i"}},
        ]
    docs = await db.guest_profiles.find(q).sort("last_seen_at", -1).to_list(limit)
    out = []
    for d in docs:
        d["whatsapp"] = d.pop("_id")
        out.append(d)
    return out






@api.post("/pms-integration/sync/{jenis}")
async def pms_integration_sync(jenis: str, user=Depends(get_current_user)):
    """Cuma `rule` (Business Rules) yang benar-benar dimiliki PMS - lihat
    connectors/webpelangi_connector.py untuk sync hotel_profile/FAQ (sumbernya web-pelangi,
    bukan PMS)."""
    if jenis not in SYNC_KINDS:
        raise HTTPException(404, f"Jenis sync tidak dikenal: {jenis}")
    result = await _sync_business_rules()
    await db.pms_integration_config.update_one(
        {"_id": "singleton"}, {"$set": {f"last_sync.{jenis}": result}}, upsert=True,
    )
    await _audit_log(user, f"pms_sync_{jenis}", result.get("message", ""))
    return result


# ---------------------------------------------------------------------------
# WEB CONTENT INTEGRATION (web-pelangi - sumber hotel_profile/FAQ, BUKAN PMS)
# ---------------------------------------------------------------------------
WEB_CONTENT_SYNC_KINDS = {"hotel_profile": _sync_hotel_profile, "faq": _sync_faq}


@api.get("/web-content-integration")
async def web_content_integration_get(user=Depends(get_current_user)):
    return await _web_content_config()


class WebContentIntegrationIn(BaseModel):
    base_url: Optional[str] = None


@api.put("/web-content-integration")
async def web_content_integration_update(body: WebContentIntegrationIn, user=Depends(get_current_user)):
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    upd["updated_at"] = utc_now_iso()
    await db.web_content_integration_config.update_one({"_id": "singleton"}, {"$set": upd}, upsert=True)
    await _audit_log(user, "update_web_content_integration", "Update konfigurasi sync konten web-pelangi")
    return await _web_content_config()


@api.post("/web-content-integration/sync/{jenis}")
async def web_content_integration_sync(jenis: str, user=Depends(get_current_user)):
    fn = WEB_CONTENT_SYNC_KINDS.get(jenis)
    if not fn:
        raise HTTPException(404, f"Jenis sync tidak dikenal: {jenis}")
    result = await fn()
    await db.web_content_integration_config.update_one(
        {"_id": "singleton"}, {"$set": {f"last_sync.{jenis}": result}}, upsert=True,
    )
    await _audit_log(user, f"web_content_sync_{jenis}", result.get("message", ""))
    return result


class SendMessageIn(BaseModel):
    to: str
    message: str


@api.post("/send-message")
async def send_message_relay(body: SendMessageIn, request: Request, _rl: None = Depends(rate_limiter(30, 10))):
    """Relay pesan keluar sistem (BUKAN balasan AI) - dipanggil Pelangi PMS untuk notifikasi
    yang PMS sendiri yang memutuskan isinya (link pembayaran Tripay saat booking request
    disetujui, konfirmasi tolak, dst - lihat routes/booking_requests.py di repo PMS).
    Sengaja kontraknya sama persis dengan provider WA generik lama ({to, message} +
    Authorization Bearer) supaya PMS bisa memakai mekanisme `_kirim_via_provider` yang SUDAH
    ADA tanpa perlu perubahan kode PMS - cukup arahkan Konfigurasi Webhook (provider
    "Lainnya/Custom API") ke endpoint ini. Auth pakai `send_message_api_key` sendiri
    (BUKAN pms_api_key - itu arah sebaliknya, ai-chat-bot->PMS), supaya kedua arah panggilan
    punya kredensial masing-masing yang bisa di-revoke terpisah."""
    cfg = await _pms_config()
    auth = request.headers.get("Authorization", "")
    key = auth[7:] if auth.startswith("Bearer ") else ""
    if not cfg.get("send_message_api_key") or not key or not secrets.compare_digest(key, cfg["send_message_api_key"]):
        raise HTTPException(401, "API key tidak valid")

    digits = re.sub(r"\D", "", body.to or "")
    if not digits or not body.message.strip():
        raise HTTPException(400, "to/message tidak valid")
    ok = await _waha_send_text(f"{digits}@c.us", body.message)
    await _pms_log("/send-message", "POST", 200 if ok else 502, 0, ok, f"to {digits}")
    if not ok:
        raise HTTPException(502, "Gagal mengirim pesan lewat WAHA")
    return {"ok": True}


@api.post("/webhook/waha")
async def webhook_waha(request: Request, token: Optional[str] = None, _: None = Depends(rate_limiter(30, 10))):
    """Dipanggil WAHA (gateway WhatsApp self-hosted) setiap ada pesan masuk. Publik (tidak
    ada login), jadi divalidasi lewat `?token=` yang harus cocok `WAHA_WEBHOOK_TOKEN` — pola
    sama seperti webhook masuk di Pelangi PMS (`webhook_config.webhook_token`). Reuse penuh
    `_run_chat_turn` (logika sama dengan simulator `/chat/message`) supaya AI yang menjawab
    tamu WhatsApp asli konsisten dengan yang staf uji coba di dashboard. Balasan dikirim
    balik ke tamu dengan MEMANGGIL WAHA (`_waha_send_text`) — bukan lewat response webhook,
    karena WAHA tidak merelai isi response webhook ke WhatsApp seperti sebagian provider lain.
    Token dicocokkan ke `webhook_token` di `pms_integration_config` (dashboard Settings ->
    PMS Integration), bisa di-regenerate dari sana - fallback ke env WAHA_WEBHOOK_TOKEN
    kalau dokumen config belum pernah dibuat.
    """
    cfg = await _pms_config()
    expected = cfg.get("webhook_token") or WAHA_WEBHOOK_TOKEN
    if not expected or token != expected:
        raise HTTPException(404, "Not Found")

    payload = await request.json()
    if payload.get("event") != "message":
        return {"ok": True, "diabaikan": f"event '{payload.get('event')}' tidak diproses"}

    data = payload.get("payload") or {}
    if data.get("fromMe"):
        return {"ok": True, "diabaikan": "pesan keluar dari nomor bot sendiri"}

    chat_id = data.get("from") or ""
    raw_id, _, domain = chat_id.partition("@")
    message = data.get("body") or ""
    if not raw_id or not message:
        return {"ok": True, "diabaikan": "tanpa nomor pengirim/isi pesan (kemungkinan pesan media)"}

    # Multi-nomor WA (2026-07-19): WAHA menyertakan nama session (nomor mana yang terima
    # pesan ini) di tiap payload webhook - dipakai cari AI bot mana yang ditautkan ke
    # nomor itu (lihat AiBot.channel_id/channel_type di BotDetail tab Koneksi WhatsApp).
    # Kalau belum ada bot yang ditautkan ke session ini, fallback ke perilaku lama
    # (bot_id=None -> _load_bot jatuh ke booking_marketing) supaya nomor yang sudah
    # terhubung dari sebelum fitur ini ada tetap jalan tanpa perlu setup ulang.
    waha_session = payload.get("session") or WAHA_SESSION
    linked_bot = await db.ai_bots.find_one({"channel_type": "whatsapp", "channel_id": waha_session})
    bot_id = linked_bot["_id"] if linked_bot else None

    # WhatsApp punya fitur privasi "LID" (Linked ID) - sebagian pengirim dilaporkan WAHA
    # lewat identifier "xxxx@lid", BUKAN "xxxx@c.us", dan angka di "xxxx" itu SAMA SEKALI
    # BUKAN nomor telepon asli (ditemukan lewat laporan user 2026-07-18: link pembayaran
    # gagal terkirim karena no_hp yang tersimpan ternyata LID, bukan nomor asli). Untuk
    # domain selain c.us/s.whatsapp.net, JANGAN perlakukan raw_id sebagai nomor telepon -
    # biarkan `whatsapp` kosong supaya AI (lewat create_booking dkk) tetap MEMINTA tamu
    # ketik nomor WA asli secara eksplisit, bukan diam-diam pakai LID yang salah.
    is_real_phone = domain in ("c.us", "s.whatsapp.net")
    phone = raw_id if is_real_phone else None
    guest_name = data.get("notifyName") or (phone if is_real_phone else "Tamu WhatsApp")
    # session_id disertakan nomor bot (waha_session) - tamu yang sama chat ke 2 nomor
    # berbeda (mis. tanya booking ke satu nomor, komplain ke nomor lain) harus jadi 2
    # percakapan terpisah, bukan tercampur jadi 1 riwayat.
    session_id = f"wa-{waha_session}-{raw_id}"

    hasil = await _run_chat_turn(
        session_id, message, guest_name, phone, bot_id, None,
        channel="whatsapp", waha_session=waha_session,
    )
    if hasil.get("reply"):
        # Jeda 3-5 detik sebelum kirim balasan (dikonfirmasi user 2026-07-19) - biar terasa
        # seperti orang mengetik balasan (bukan bot yang membalas instan dalam hitungan
        # milidetik, pola yang gampang dikenali WhatsApp sebagai bot & bisa memicu
        # pembatasan/reachout timelock), sekaligus meredam beban kalau banyak pesan masuk
        # bersamaan. HANYA di jalur WhatsApp asli - Chat Simulator (staf uji coba) tetap
        # instan supaya tidak memperlambat proses testing.
        await asyncio.sleep(random.uniform(3, 5))
        # Marker [[IMG: url]] dikonversi jadi foto SUNGGUHAN via WAHA sendImage, bukan
        # dikirim sebagai teks mentah (bug ditemukan 2026-07-19 dari riwayat chat nyata -
        # tamu menerima literal "[[IMG: https://...]]"). Caption tiap foto = nama room
        # kalau URL-nya cocok dengan foto room (photo_url/images) yang tersimpan, supaya
        # rapi & jelas foto kamar yang mana - bukan cuma link polos.
        clean_text, image_urls = parse_img_markers(hasil["reply"])
        if clean_text:
            await _waha_send_text(chat_id, clean_text, session=waha_session)
        for url in image_urls:
            room = await db.rooms.find_one({"$or": [{"photo_url": url}, {"images.url": url}]})
            caption = room["name"] if room else ""
            await _waha_send_image(chat_id, url, caption, session=waha_session)
    return {"ok": True}


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
@api.get("/settings/llm-options")
async def settings_llm_options(user=Depends(get_current_user)):
    return {"providers": LLM_PROVIDER_OPTIONS, "default_provider": DEFAULT_PROVIDER, "default_model": DEFAULT_MODEL}


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
# UPLOADS (Cloudinary)
# ---------------------------------------------------------------------------
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ALLOWED_DOC_EXT = {".pdf", ".docx", ".txt", ".md"}
MAX_UPLOAD_MB = 10


def _validate_upload(file: UploadFile, allowed: set) -> str:
    name = (file.filename or "").lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if ext not in allowed:
        raise HTTPException(400, f"Ekstensi {ext or '?'} tidak didukung. Diperbolehkan: {sorted(allowed)}")
    return ext


@api.post("/uploads/image")
async def upload_image_route(
    file: UploadFile = File(...),
    folder: str = Query("pelangi/kb"),
    user=Depends(get_current_user),
):
    _validate_upload(file, ALLOWED_IMAGE_EXT)
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"File melebihi {MAX_UPLOAD_MB}MB")
    if folder not in ("pelangi/kb", "pelangi/rooms", "pelangi/menu"):
        folder = "pelangi/kb"
    try:
        result = upload_image(data, folder=folder)
    except Exception as e:
        raise HTTPException(500, f"Cloudinary error: {e}")
    return result


# ---------------------------------------------------------------------------
# RAG DOCUMENTS
# ---------------------------------------------------------------------------
@api.get("/rag/documents")
async def rag_docs_list(user=Depends(get_current_user)):
    docs = await db.rag_documents.find({}).sort("created_at", -1).to_list(200)
    out = []
    for d in docs:
        d["id"] = d.pop("_id")
        out.append(d)
    return out


@api.post("/rag/documents")
async def rag_docs_upload(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    _validate_upload(file, ALLOWED_DOC_EXT)
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"File melebihi {MAX_UPLOAD_MB}MB")

    filename = file.filename
    # 1. Extract text
    try:
        text = extract_text(filename, data)
    except Exception as e:
        raise HTTPException(400, f"Gagal ekstrak teks: {e}")
    if not text.strip():
        raise HTTPException(400, "Dokumen kosong / tidak dapat dibaca")

    # 2. Upload raw to Cloudinary (optional persistence)
    try:
        cloud = upload_raw(data, filename)
    except Exception as e:
        cloud = {"url": None, "public_id": None}
        logger.warning(f"Cloudinary raw upload failed: {e}")

    # 3. Chunk & store
    chunks = chunk_text(text, chunk_size=600, overlap=100)
    doc_id = new_id()

    # 4. Embed tiap chunk (batch 1x call) - gagal/tidak dikonfigurasi TIDAK PERNAH
    # menggagalkan upload, chunk-nya tetap tersimpan & tetap bisa dicari lewat BM25
    # (lihat hybrid_search di rag_service.py).
    embeddings = await get_embeddings_batch(chunks) if chunks else None

    doc = {
        "_id": doc_id,
        "title": filename,
        "filename": filename,
        "url": cloud.get("url"),
        "public_id": cloud.get("public_id"),
        "chunk_count": len(chunks),
        "char_count": len(text),
        "embedded": bool(embeddings),
        "created_at": utc_now_iso(),
        "created_by": user.get("email"),
    }
    await db.rag_documents.insert_one(doc)

    chunk_docs = [{
        "_id": new_id(), "doc_id": doc_id, "doc_title": filename,
        "index": i, "text": ch, "embedding": (embeddings[i] if embeddings else None),
        "created_at": utc_now_iso(),
    } for i, ch in enumerate(chunks)]
    if chunk_docs:
        await db.rag_chunks.insert_many(chunk_docs)

    doc["id"] = doc.pop("_id")
    return doc


@api.delete("/rag/documents/{doc_id}")
async def rag_docs_delete(doc_id: str, user=Depends(get_current_user)):
    doc = await db.rag_documents.find_one({"_id": doc_id})
    if not doc:
        raise HTTPException(404, "Not found")
    if doc.get("public_id"):
        delete_asset(doc["public_id"], resource_type="raw")
    await db.rag_chunks.delete_many({"doc_id": doc_id})
    await db.rag_documents.delete_one({"_id": doc_id})
    return {"ok": True}


@api.get("/rag/search")
async def rag_search(q: str, k: int = 5, user=Depends(get_current_user)):
    """Debug endpoint: run hybrid BM25+semantic search over all chunks."""
    chunks = await db.rag_chunks.find({}, {"_id": 1, "doc_id": 1, "doc_title": 1, "text": 1, "embedding": 1}).to_list(2000)
    norm = [{"id": c["_id"], "doc_id": c["doc_id"], "doc_title": c.get("doc_title", "doc"), "text": c["text"], "embedding": c.get("embedding")} for c in chunks]
    hits = await hybrid_search(q, norm, k=k)
    for h in hits:
        h.pop("embedding", None)
    return {"query": q, "hits": hits}


# ---------------------------------------------------------------------------
# AI BOTS (V2)
# ---------------------------------------------------------------------------
def _slugify(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s.lower()).strip("_")


@api.get("/bots")
async def bots_list(user=Depends(get_current_user)):
    docs = await db.ai_bots.find({}).sort("created_at", 1).to_list(200)
    return [{**d, "id": d.pop("_id")} for d in docs]


@api.get("/bots/{bot_id}")
async def bots_get(bot_id: str, user=Depends(get_current_user)):
    doc = await db.ai_bots.find_one({"_id": bot_id})
    if not doc:
        raise HTTPException(404, "Not found")
    return {**doc, "id": doc.pop("_id")}


@api.post("/bots")
async def bots_create(body: AIBotIn, user=Depends(get_current_user)):
    code = body.code or _slugify(body.name)
    if await db.ai_bots.find_one({"code": code}):
        raise HTTPException(400, f"Bot code '{code}' sudah dipakai")
    doc = {
        "_id": new_id(), **body.model_dump(),
        "code": code,
        "created_at": utc_now_iso(), "updated_at": utc_now_iso(),
    }
    await db.ai_bots.insert_one(doc)
    return {**doc, "id": doc.pop("_id")}


@api.patch("/bots/{bot_id}")
async def bots_update(bot_id: str, body: AIBotUpdate, user=Depends(get_current_user)):
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    if not upd:
        raise HTTPException(400, "Empty update")
    upd["updated_at"] = utc_now_iso()
    res = await db.ai_bots.update_one({"_id": bot_id}, {"$set": upd})
    if not res.matched_count:
        raise HTTPException(404, "Not found")
    doc = await db.ai_bots.find_one({"_id": bot_id})
    return {**doc, "id": doc.pop("_id")}


@api.delete("/bots/{bot_id}")
async def bots_delete(bot_id: str, user=Depends(require_super_admin)):
    await db.ai_bots.delete_one({"_id": bot_id})
    return {"ok": True}


# ---------------------------------------------------------------------------
# TOOLS CATALOG
# ---------------------------------------------------------------------------
@api.get("/tools")
async def tools_list(user=Depends(get_current_user)):
    docs = await db.tools.find({}).sort("category", 1).to_list(200)
    return [{**d, "id": d.pop("_id")} for d in docs]


@api.post("/tools")
async def tools_create(body: ToolIn, user=Depends(get_current_user)):
    code = body.code or _slugify(body.name)
    if await db.tools.find_one({"code": code}):
        raise HTTPException(400, f"Tool code '{code}' sudah dipakai")
    doc = {"_id": new_id(), **body.model_dump(), "code": code, "created_at": utc_now_iso()}
    await db.tools.insert_one(doc)
    return {**doc, "id": doc.pop("_id")}


@api.patch("/tools/{tool_id}")
async def tools_update(tool_id: str, body: ToolIn, user=Depends(get_current_user)):
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    res = await db.tools.update_one({"_id": tool_id}, {"$set": upd})
    if not res.matched_count:
        raise HTTPException(404, "Not found")
    doc = await db.tools.find_one({"_id": tool_id})
    return {**doc, "id": doc.pop("_id")}


@api.delete("/tools/{tool_id}")
async def tools_delete(tool_id: str, user=Depends(require_super_admin)):
    await db.tools.delete_one({"_id": tool_id})
    return {"ok": True}


# ---------------------------------------------------------------------------
# INTENTS CATALOG
# ---------------------------------------------------------------------------
@api.get("/intents")
async def intents_list(user=Depends(get_current_user)):
    docs = await db.intents.find({}).sort("code", 1).to_list(200)
    return [{**d, "id": d.pop("_id")} for d in docs]


@api.post("/intents")
async def intents_create(body: IntentIn, user=Depends(get_current_user)):
    code = body.code or _slugify(body.name).upper()
    if await db.intents.find_one({"code": code}):
        raise HTTPException(400, f"Intent code '{code}' sudah dipakai")
    doc = {"_id": new_id(), **body.model_dump(), "code": code, "created_at": utc_now_iso()}
    await db.intents.insert_one(doc)
    return {**doc, "id": doc.pop("_id")}


@api.patch("/intents/{intent_id}")
async def intents_update(intent_id: str, body: IntentIn, user=Depends(get_current_user)):
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    res = await db.intents.update_one({"_id": intent_id}, {"$set": upd})
    if not res.matched_count:
        raise HTTPException(404, "Not found")
    doc = await db.intents.find_one({"_id": intent_id})
    return {**doc, "id": doc.pop("_id")}


@api.delete("/intents/{intent_id}")
async def intents_delete(intent_id: str, user=Depends(require_super_admin)):
    await db.intents.delete_one({"_id": intent_id})
    return {"ok": True}


# ---------------------------------------------------------------------------
# WORKFLOWS
# ---------------------------------------------------------------------------
@api.get("/workflows")
async def workflows_list(user=Depends(get_current_user)):
    docs = await db.workflows.find({}).sort("name", 1).to_list(200)
    return [{**d, "id": d.pop("_id")} for d in docs]


@api.post("/workflows")
async def workflows_create(body: WorkflowIn, user=Depends(get_current_user)):
    code = body.code or _slugify(body.name)
    doc = {"_id": new_id(), **body.model_dump(),
           "code": code, "created_at": utc_now_iso()}
    # convert WorkflowStep pydantic to dict
    doc["steps"] = [s.model_dump() if hasattr(s, "model_dump") else s for s in doc["steps"]]
    await db.workflows.insert_one(doc)
    return {**doc, "id": doc.pop("_id")}


@api.patch("/workflows/{workflow_id}")
async def workflows_update(workflow_id: str, body: WorkflowIn, user=Depends(get_current_user)):
    upd = body.model_dump()
    upd["steps"] = [s.model_dump() if hasattr(s, "model_dump") else s for s in upd["steps"]]
    res = await db.workflows.update_one({"_id": workflow_id}, {"$set": upd})
    if not res.matched_count:
        raise HTTPException(404, "Not found")
    doc = await db.workflows.find_one({"_id": workflow_id})
    return {**doc, "id": doc.pop("_id")}


@api.delete("/workflows/{workflow_id}")
async def workflows_delete(workflow_id: str, user=Depends(require_super_admin)):
    await db.workflows.delete_one({"_id": workflow_id})
    return {"ok": True}


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
