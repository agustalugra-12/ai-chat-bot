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
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, APIRouter, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import PlainTextResponse
from pymongo.errors import DuplicateKeyError
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
from emergentintegrations.llm.chat import ChatError
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
    _waha_call, _waha_send_text, _waha_send_image, _waha_send_file, _waha_list_sessions, _waha_ensure_session,
)
from connectors.pms_connector import (
    PMS_API_BASE_URL, PMS_API_KEY, PMS_DEFAULT_ENDPOINTS,
    PMS_CAPABILITY_WIRED, PMS_DEFAULT_CAPABILITIES, PMS_INTEGRATION_DEFAULT,
    SYNC_KINDS,
    _pms_config, _pms_log, _pms_ketersediaan, _pms_buat_booking_request,
    _pms_buat_tiket, _pms_status_booking, _pms_status_member, _pms_ajukan_pembatalan, _sync_business_rules,
    _pms_alert_owner, _pms_preview_harga,
)
from connectors.webpelangi_connector import (
    _web_content_config, _sync_hotel_profile, _sync_faq,
)
from waha_health_monitor import waha_health_monitor_loop
from connectors.whatsapp_cloud_connector import (
    WHATSAPP_CLOUD_PHONE_NUMBER_ID, _wa_cloud_send_text, _wa_cloud_send_image, _wa_cloud_send_document,
    _wa_cloud_send_template,
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
    asyncio.create_task(waha_health_monitor_loop())
    await db.wa_cloud_dedup.create_index("wamid", unique=True)
    await db.wa_cloud_dedup.create_index("ts", expireAfterSeconds=86400)


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
# GUEST BOOKING VERIFICATION: dihapus 2026-07-27 (audit keamanan) — endpoint lama TANPA
# AUTH SAMA SEKALI, siapa saja bisa tebak nomor WA & dapat riwayat booking tamu lain
# (nama/tanggal/harga). Sudah dead code sejak lama: tool AI `lookup_booking` yang benar-benar
# jalan (register_tool("lookup_booking", ...) di bawah) pakai _pms_status_booking (PMS asli),
# bukan endpoint ini - db.bookings lokal ai-chat-bot bukan lagi sumber kebenaran booking.
# ---------------------------------------------------------------------------


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

    # Nomor Cloud API yang MASIH aktif sekarang (dipakai salah satu AI bot) - dashboard ini
    # SATU-SATUNYA tempat staf bisa baca chat tamu (WA Business App tidak lagi kepakai sejak
    # migrasi Cloud API), jadi 2 percakapan dengan nama tamu sama tapi nomor WA beda (mis.
    # sisa tes nomor lama yang sudah ditinggalkan) WAJIB gampang dibedakan - ditemukan lewat
    # laporan nyata 2026-07-27: staf sempat buka percakapan lama/sudah tidak aktif & mengira
    # chat tamu "cuma sampai setengah" padahal percakapan yang benar ada di entry lain.
    active_cloud_ids = {
        b["channel_id"] for b in await db.ai_bots.find(
            {"channel_type": "whatsapp_cloud", "channel_id": {"$nin": [None, ""]}}
        ).to_list(50)
    }

    out = []
    for d in docs:
        d["id"] = d.pop("_id")
        d["last_message"] = (d["messages"][-1]["content"] if d.get("messages") else "")
        d["message_count"] = len(d.get("messages", []))
        if d.get("channel") == "whatsapp_cloud" or (d.get("session_id") or "").startswith("wac-"):
            _, phone_number_id = _channel_info_from_conv(d)
            d["nomor_aktif"] = phone_number_id in active_cloud_ids
        else:
            d["nomor_aktif"] = True
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
    persis seperti balasan AI.

    Status otomatis ikut pindah ke "waiting_admin" begitu staf kirim balasan manual
    (2026-07-26, permintaan Agus: sebelumnya harus klik "Handover ke Admin" terpisah dulu -
    kalau lupa, AI bisa saja ikut membalas pesan tamu berikutnya berbarengan dengan balasan
    manual staf, membingungkan tamu). Staf tetap pegang kendali sampai eksplisit menekan
    "Aktifkan AI Lagi" (`/resume`)."""
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
    update = {"messages": messages, "updated_at": utc_now_iso()}
    if conv.get("status") not in ("waiting_admin", "closed"):
        update["status"] = "waiting_admin"
        update["resolution"] = "handover"
    await db.conversations.update_one({"_id": conv_id}, {"$set": update})

    sent_to_whatsapp = False
    if conv.get("channel") in ("whatsapp", "whatsapp_cloud") and conv.get("whatsapp"):
        # Balas lewat channel & nomor WA yang SAMA dengan yang tamu hubungi (WAHA atau
        # Cloud API, bisa beda-beda sejak multi-nomor per AI bot 2026-07-19 dan migrasi
        # Cloud API 2026-07-21) - fallback ke default kalau conv lama belum punya field ini
        # (dibuat sebelum fitur ini ada).
        sent_to_whatsapp = await _send_wa_smart(conv, text)

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
    # Foto + fasilitas/deskripsi kamar - koleksi db.rooms LOKAL ai-chat-bot (bukan
    # _pms_ketersediaan di atas, yang cuma tipe/tarif/stok live dari PMS, TIDAK ADA field
    # foto/fasilitas sama sekali). Ditemukan 2026-07-19 (foto) & 2026-07-21 (fasilitas) dari
    # laporan user: tanpa ini AI mengarang fasilitas kamar generik dari pengetahuan umum
    # (mis. "AC, lemari pakaian") - staf sudah isi fasilitas asli di halaman Room Management
    # tapi datanya tidak pernah sampai ke context AI sama sekali, cuma foto yang disertakan.
    room_photos = await db.rooms.find({}, {"name": 1, "photo_url": 1, "images": 1, "facilities": 1, "description": 1}).to_list(50)
    base = build_context_block(rooms, menu, kb, settings, room_photos)

    # Nomor WA tamu SUNGGUHAN (2026-07-26, bug ditemukan saat regression test trimming
    # prompt) - sebelum ini, angka WA tamu TIDAK PERNAH benar-benar dikirim sebagai teks
    # ke LLM (cuma dipakai lookup profil di bawah), padahal TOOL_DOCS create_booking
    # instruksikan AI "pakai nomor WA dari konteks apa adanya" dan kasih CONTOH angka
    # "6281234567890" - tanpa angka asli di context, AI malah menyalin angka CONTOH itu ke
    # ringkasan booking (tamu dikonfirmasi dgn nomor WA orang lain). Sekarang disuntik
    # eksplisit supaya ada angka asli untuk dibaca AI.
    if whatsapp:
        base = f"# NOMOR WA TAMU SESI INI (angka asli, WAJIB dipakai apa adanya kalau perlu ditampilkan/dipakai tool)\n{whatsapp}\n\n" + base

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
            api_key_override=conv.get("_pms_api_key_override"),
        )
        return {"ok": True, "tool": "check_availability", "result": rooms}
    except Exception as e:
        return {"ok": False, "tool": "check_availability", "error": str(e)}


@register_tool("preview_booking", {"create_booking"})
async def _tool_preview_booking(args: dict, conv: dict) -> dict:
    """Preview rincian harga (SEBELUM booking_request sungguhan dibuat, tidak menulis apapun
    ke PMS) - dipakai AI untuk ringkas & minta konfirmasi tamu sebelum create_booking
    (2026-07-21, permintaan user). Args sama dengan create_booking (minus guest_name/
    payment_option/jumlah_tamu, tidak relevan utk hitung harga)."""
    if not args.get("whatsapp"):
        args = {**args, "whatsapp": conv.get("whatsapp")}
    required = ["whatsapp", "tipe", "room_tipe", "tanggal_checkin"]
    for k in required:
        if not args.get(k):
            return {"ok": False, "tool": "preview_booking", "error": f"missing {k}"}
    hasil = await _pms_preview_harga(args, api_key_override=conv.get("_pms_api_key_override"))
    if not hasil.get("ok"):
        return {"ok": False, "tool": "preview_booking", "error": hasil.get("error")}
    result = {"ok": True, "tool": "preview_booking", "kedatangan_ke": hasil.get("kedatangan_ke")}
    if hasil.get("diskon_member_persen"):
        result["diskon_member_persen"] = hasil["diskon_member_persen"]
    if hasil.get("diskon_ai_persen"):
        result["diskon_diskresi_persen"] = hasil["diskon_ai_persen"]
    if hasil.get("preview_harga"):
        result["rincian_harga"] = hasil["preview_harga"]
    return result


@register_tool("create_booking", {"create_booking"})
async def _tool_create_booking(args: dict, conv: dict) -> dict:
    try:
        logging.getLogger("pms").info(f"create_booking args diterima dari AI: {args}")
        # Fallback nomor WA (2026-07-21) - prompt sudah instruksikan AI pakai nomor tamu dari
        # konteks tanpa nanya, tapi jangan sampai booking gagal cuma karena AI lupa
        # menyertakan field ini di tool call - default ke nomor sesi chat yang sesungguhnya.
        if not args.get("whatsapp") and conv.get("whatsapp"):
            args["whatsapp"] = conv["whatsapp"]
        required = ["guest_name", "whatsapp", "tipe", "room_tipe", "tanggal_checkin"]
        for k in required:
            if not args.get(k):
                return {"ok": False, "tool": "create_booking", "error": f"missing {k}"}
        if args["tipe"] not in ("day_use", "menginap"):
            return {"ok": False, "tool": "create_booking", "error": "tipe harus 'day_use' atau 'menginap'"}
        if args.get("payment_option") not in ("dp50", "full"):
            return {"ok": False, "tool": "create_booking",
                     "error": "payment_option wajib diisi 'dp50' atau 'full' - TANYA dulu ke tamu mau DP 50% atau lunas, JANGAN panggil tool ini sebelum tamu menjawab"}
        # Jaring pengaman (2026-07-25) - insiden nyata ditemukan lewat pengujian: tamu
        # kirim pesan susulan/afirmasi ("iya lanjut", "oke booking ya") SETELAH booking
        # sebelumnya sudah sukses di giliran sebelumnya - model sempat memanggil
        # create_booking LAGI dengan parameter identik, bikin 2 Booking Request kembar
        # untuk 1 permintaan tamu yang sama (staf lihat approval dobel di antrian). Kalau
        # tipe/room_tipe/tanggal_checkin PERSIS sama dengan booking terakhir yang berhasil
        # di percakapan ini, jangan create ulang ke PMS - balikin hasil yang sudah ada
        # (idempotent), sama seperti pola jaring pengaman lain di bawah (service-fee,
        # checkout_url dobel, extra bed non-Cottage).
        last = conv.get("last_booking_request")
        if last and (last.get("tipe"), last.get("room_tipe"), last.get("tanggal_checkin")) == (
            args.get("tipe"), args.get("room_tipe"), args.get("tanggal_checkin")
        ):
            return {**last, "tool": "create_booking", "already_created": True}
        hasil = await _pms_buat_booking_request(args, api_key_override=conv.get("_pms_api_key_override"))
        if not hasil.get("ok"):
            return {"ok": False, "tool": "create_booking", "error": hasil.get("error")}
        await db.conversations.update_one({"_id": conv["_id"]}, {"$set": {"booking_created": True}})
        br = hasil.get("booking_request") or {}
        # "status" WAJIB disertakan - ini satu-satunya cara AI tahu hasil sebenarnya:
        # waiting_payment (Day Use auto-approved, ada checkout_url), rejected (Day Use
        # auto-ditolak krn benar-benar penuh, 2026-07-19), atau waiting_approval (Menginap/
        # fallback, staf yang proses). Tanpa ini AI bisa salah bilang "sedang diproses" utk
        # booking yang sebenarnya sudah ditolak.
        result = {
            "ok": True, "tool": "create_booking", "booking_request_id": br.get("id"),
            "kode": br.get("kode"), "status": br.get("status"),
        }
        if br.get("checkout_url"):
            result["checkout_url"] = br["checkout_url"]
        if br.get("status") == "rejected":
            result["rejected_reason"] = br.get("rejected_reason")
        # Program Loyalitas Kedatangan - "kedatangan_ke" SELALU disertakan (permintaan user
        # 2026-07-21: tamu harus selalu tahu ini kedatangan ke berapa, bukan cuma pas dapat
        # diskon - bagian dari kesan "dilayani sepenuh hati"), "diskon_member_persen" cuma
        # ada kalau >0. DIPISAH dari diskon diskresi - preview_diskon_persen di PMS angka
        # GABUNGAN (max member vs diskresi), field mentah member sendiri ada di
        # preview_diskon_member_persen supaya AI pakai kalimat yang BENAR sesuai sumbernya
        # (member = "kedatangan ke-N", diskresi = kalimat kebijakan diskon di TOOL_DOCS).
        if br.get("preview_kedatangan_ke"):
            result["kedatangan_ke"] = br["preview_kedatangan_ke"]
        if br.get("preview_diskon_member_persen"):
            result["diskon_member_persen"] = br["preview_diskon_member_persen"]
        if br.get("diskon_ai_persen"):
            result["diskon_diskresi_persen"] = br["diskon_ai_persen"]
        # Rincian harga (2026-07-20, permintaan user: tamu WAJIB dijelaskan harga kamar,
        # service fee 3%, & diskon setelah menawarkan DP/lunas) - None kalau room_tipe tidak
        # valid, AI tetap lanjut tanpa rincian (lihat _hitung_preview_harga di PMS).
        if br.get("preview_harga"):
            result["rincian_harga"] = br["preview_harga"]
        conv["last_booking_request"] = {
            "tipe": args.get("tipe"), "room_tipe": args.get("room_tipe"),
            "tanggal_checkin": args.get("tanggal_checkin"), **result,
        }
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
        room_nomor = (args.get("room_nomor") or "").strip()
        hasil = await _pms_buat_tiket("service_request", deskripsi, whatsapp, guest_name, room_nomor,
                                      api_key_override=conv.get("_pms_api_key_override"))
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
        room_nomor = (args.get("room_nomor") or "").strip()
        hasil = await _pms_buat_tiket(tipe, deskripsi, whatsapp, guest_name, room_nomor,
                                      api_key_override=conv.get("_pms_api_key_override"))
        if not hasil.get("ok"):
            return {"ok": False, "tool": "create_maintenance_ticket", "error": hasil.get("error")}
        tiket = hasil.get("tiket") or {}
        return {"ok": True, "tool": "create_maintenance_ticket", "tiket_id": tiket.get("id")}
    except Exception as e:
        return {"ok": False, "tool": "create_maintenance_ticket", "error": str(e)}


def _rename_kode_permintaan(items: list) -> list:
    """PMS ngembaliin tiap item dengan 2 field "kode" yang beda arti - top-level "kode"
    itu kode PERMINTAAN booking (REQ-...), sedangkan booking_ringkasan[].kode itu kode
    BOOKING ASLI (BKO-...) yang valid dipakai cancel_booking. Ditemukan 2026-07-21 lewat
    laporan user: AI ketuker pakai kode REQ- utk cancel_booking (nama field sama-sama
    "kode", gampang salah), PMS diam-diam tidak menemukan booking dengan kode itu, jadi
    permintaan pembatalan TIDAK PERNAH benar-benar tersimpan. Ganti nama field top-level
    supaya SECARA STRUKTUR tidak mungkin ketuker lagi - cuma booking_ringkasan[].kode yang
    masih bernama "kode"."""
    out = []
    for it in items:
        it2 = dict(it)
        it2["kode_permintaan"] = it2.pop("kode", None)
        out.append(it2)
    return out


@register_tool("lookup_booking", {"lookup_booking"})
async def _tool_lookup_booking(args: dict, conv: dict) -> dict:
    wa = args.get("whatsapp") or conv.get("whatsapp")
    if not wa:
        return {"ok": False, "tool": "lookup_booking", "error": "missing whatsapp"}
    hasil = await _pms_status_booking(wa, api_key_override=conv.get("_pms_api_key_override"))
    if not hasil.get("ok"):
        return {"ok": False, "tool": "lookup_booking", "error": hasil.get("error")}
    return {"ok": True, "tool": "lookup_booking", "result": _rename_kode_permintaan(hasil.get("permintaan") or [])}


@register_tool("check_member_status", {"create_booking"})
async def _tool_check_member_status(args: dict, conv: dict) -> dict:
    """Status Program Loyalitas Kedatangan (2026-07-21, permintaan user: AI proaktif sebut
    diskon member di AWAL percakapan begitu tamu tunjukkan niat booking, bukan cuma pas
    booking sudah dibuat). Digate di tool_codes yang sama dengan create_booking - lihat
    _pms_status_member."""
    wa = args.get("whatsapp") or conv.get("whatsapp")
    if not wa:
        return {"ok": False, "tool": "check_member_status", "error": "missing whatsapp"}
    hasil = await _pms_status_member(wa, api_key_override=conv.get("_pms_api_key_override"))
    if not hasil.get("ok"):
        return {"ok": False, "tool": "check_member_status", "error": hasil.get("error")}
    return {"ok": True, "tool": "check_member_status", "kedatangan_ke": hasil.get("kedatangan_ke"), "diskon_persen": hasil.get("diskon_persen")}


@register_tool("cancel_booking", {"cancel_booking"})
async def _tool_cancel_booking(args: dict, conv: dict) -> dict:
    """Non-binding - AI TIDAK PERNAH langsung membatalkan booking sungguhan (sama seperti
    create_booking), cuma menyampaikan info ke PMS lewat _pms_ajukan_pembatalan; PMS
    mencatat & staf approve/reject manual. `kode` OPSIONAL (2026-07-21) - kosong = PMS cari
    otomatis dari nomor WA (aman kalau tamu cuma punya 1 booking aktif; kalau lebih dari 1,
    PMS balas error dengan field "kandidat" - lihat instruksi tool ini di TOOL_DOCS)."""
    kode = (args.get("kode") or "").strip()
    wa = args.get("whatsapp") or conv.get("whatsapp")
    if not wa:
        return {"ok": False, "tool": "cancel_booking", "error": "missing whatsapp"}
    hasil = await _pms_ajukan_pembatalan(kode, wa, args.get("alasan") or "", api_key_override=conv.get("_pms_api_key_override"))
    if not hasil.get("ok"):
        result = {"ok": False, "tool": "cancel_booking", "error": hasil.get("error")}
        if hasil.get("kandidat"):
            result["kandidat"] = hasil["kandidat"]
        return result
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
    # Beda dari 4 _pms_alert_owner lain (semua soal AI SALAH/gagal) - ini AI sendiri yang
    # sadar butuh manusia (2026-07-26, permintaan Agus: sebelumnya harus buka halaman
    # Percakapan sendiri buat tahu ada yang butuh dia; sekarang begitu AI handover, dia
    # dapat ping Telegram tanpa perlu mantau terus).
    try:
        await _pms_alert_owner(
            f"🙋 AI perlu bantuan admin - percakapan dgn {conv.get('guest_name') or conv.get('whatsapp') or 'tamu'} "
            f"dialihkan ke Admin. Alasan dari AI: {args.get('reason') or '(tidak disebutkan)'}"
        )
    except Exception:
        logging.getLogger("handover_alert").warning(f"Gagal kirim alert Telegram utk handover conv {conv.get('_id')}")
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


def _channel_info_from_conv(conv: dict) -> tuple[str, Optional[str]]:
    """Balik (channel, identifier) dari sebuah percakapan - pakai `session_id` sbg sumber
    kebenaran (self-describing: prefix "wac-{phone_number_id}-..." utk Cloud API,
    "wa-{waha_session}-..." utk WAHA) BUKAN cuma field `channel`/`cloud_phone_number_id`,
    karena field itu baru mulai diisi 2026-07-21 - percakapan yg dibuat sebelum tanggal itu
    (channel cuma "whatsapp" generik, cloud_phone_number_id None) tetap harus ke-detect
    benar lewat session_id-nya, bukan diam-diam jatuh ke WAHA."""
    sid = conv.get("session_id") or ""
    if sid.startswith("wac-"):
        parts = sid.split("-")
        phone_number_id = conv.get("cloud_phone_number_id") or (parts[1] if len(parts) > 1 else None)
        return "whatsapp_cloud", phone_number_id
    return "whatsapp", conv.get("waha_session")


async def _send_wa_smart(conv: dict, text: str) -> bool:
    """Kirim pesan ke tamu WhatsApp lewat channel yang SAMA dengan yang dipakai tamu itu
    ngobrol (WAHA atau Cloud API), lihat `_channel_info_from_conv`. Dipakai baik untuk
    balasan manual staf (human handover) maupun relay notifikasi dari PMS (/send-message,
    /send-document) - sebelum ada fungsi ini, KEDUANYA selalu hardcode ke WAHA meski
    tamunya chat lewat Cloud API (ditemukan 2026-07-21 lewat laporan user: voucher booking
    gagal terkirim krn WAHA session down, padahal tamu itu chat via Cloud API yang sehat)."""
    if not conv.get("whatsapp"):
        return False
    channel, identifier = _channel_info_from_conv(conv)
    if channel == "whatsapp_cloud":
        return await _wa_cloud_send_text(conv["whatsapp"], text, phone_number_id=identifier or "")
    return await _waha_send_text(f"{conv['whatsapp']}@c.us", text, session=identifier or WAHA_SESSION)


def _last_inbound_at(conv: Optional[dict]) -> Optional[datetime]:
    """Waktu pesan TERAKHIR dari tamu (role=user) di suatu percakapan - dipakai
    `_send_wa_transactional` menentukan apakah masih dalam jendela layanan 24 jam Meta."""
    if not conv:
        return None
    last = None
    for m in conv.get("messages", []):
        if m.get("role") == "user" and m.get("timestamp"):
            try:
                ts = datetime.fromisoformat(m["timestamp"])
            except ValueError:
                continue
            if last is None or ts > last:
                last = ts
    return last


async def _send_wa_transactional(conv: Optional[dict], text: str, whatsapp_fallback: Optional[str] = None,
                                  template_name: Optional[str] = None, template_params: Optional[List[str]] = None) -> bool:
    """Kirim notifikasi TRANSAKSIONAL yang PMS picu sendiri (approve booking, pembatalan,
    kamar siap, dst) - BEDA dari `_send_wa_smart` (balasan chat langsung/staf): fungsi ini
    sadar jendela layanan 24 jam Meta (2026-07-26, ditemukan lewat audit - 8 titik notifikasi
    proaktif sebelumnya selalu kirim teks bebas & hasil gagalnya tidak pernah dicek; teks
    bebas Cloud API ditolak Meta kalau di luar jendela 24 jam sejak pesan terakhir tamu).
    Kalau di luar jendela (atau tidak ada percakapan WA sama sekali, mis. tamu batalkan
    lewat web publik bukan WA), WAJIB pakai Message Template yang sudah disetujui Meta -
    `template_name`/`template_params` optional supaya pemanggil lama tanpa template masih
    jalan (nyoba teks bebas dulu, WAHA tidak kena aturan ini sama sekali)."""
    whatsapp = (conv or {}).get("whatsapp") or whatsapp_fallback
    if not whatsapp:
        return False

    if conv:
        channel, identifier = _channel_info_from_conv(conv)
    else:
        channel, identifier = "whatsapp_cloud", WHATSAPP_CLOUD_PHONE_NUMBER_ID

    if channel != "whatsapp_cloud":
        # WAHA - bukan API resmi Meta, tidak ada pembatasan jendela 24 jam.
        return await _waha_send_text(f"{whatsapp}@c.us", text, session=identifier or WAHA_SESSION)

    last_inbound = _last_inbound_at(conv)
    within_window = bool(last_inbound) and (datetime.now(timezone.utc) - last_inbound) < timedelta(hours=24)

    if within_window:
        if await _wa_cloud_send_text(whatsapp, text, phone_number_id=identifier or ""):
            return True

    if template_name:
        if await _wa_cloud_send_template(whatsapp, template_name, template_params or [], phone_number_id=identifier or ""):
            return True

    if not within_window:
        # upaya terakhir kalau tidak ada template (atau template gagal) - lebih baik coba
        # daripada pasti tidak terkirim sama sekali.
        return await _wa_cloud_send_text(whatsapp, text, phone_number_id=identifier or "")
    return False


async def _send_wa_document_smart(conv: dict, filename: str, mimetype: str, data_base64: str, caption: str = "") -> bool:
    """Sibling dokumen dari `_send_wa_smart` - sama polanya, dipakai relay /send-document."""
    if not conv.get("whatsapp"):
        return False
    channel, identifier = _channel_info_from_conv(conv)
    if channel == "whatsapp_cloud":
        return await _wa_cloud_send_document(conv["whatsapp"], filename, data_base64, caption, phone_number_id=identifier or "")
    return await _waha_send_file(f"{conv['whatsapp']}@c.us", filename, mimetype, data_base64, caption, session=identifier or WAHA_SESSION)


async def _run_chat_turn(
    session_id: str, message: str, guest_name: Optional[str], whatsapp: Optional[str],
    bot_id: Optional[str], bot_code: Optional[str], channel: str = "simulator",
    waha_session: Optional[str] = None, cloud_phone_number_id: Optional[str] = None,
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
            "cloud_phone_number_id": cloud_phone_number_id,
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
        # str() wajib (2026-07-27, ditemukan lewat bug nyata) - kalau bot_id ternyata
        # ObjectId mentah (sisa data lama sebelum ai_bots pindah ke id string uuid),
        # GET /conversations gagal serialize SATU dokumen ini & bikin SELURUH daftar
        # percakapan gagal tampil (satu error di jsonable_encoder mengorbankan semua).
        conv["bot_id"] = str(bot.get("_id")) if bot.get("_id") is not None else None
        conv["bot_code"] = bot.get("code")
    allowed_tool_codes = set(bot.get("tool_codes", [])) if bot else set()
    allowed_services = set(bot.get("allowed_service_types", [])) if bot else set()
    # Multi-properti PMS (Fase 4, 2026-07-25) - kalau bot ini punya API key propertinya
    # sendiri, dipakai untuk SEMUA panggilan PMS di giliran ini (dari sini sampai tool
    # calling di bawah baca lewat conv.get("_pms_api_key_override")) - bukan field
    # permanen di `conv` yang tersimpan ke DB, cuma "in-memory" selama giliran ini.
    conv["_pms_api_key_override"] = bot.get("pms_property_api_key") if bot else None

    # Build prompt inputs - ketersediaan diambil SEKALI, dipakai untuk context (harga/stok)
    # DAN prompt (daftar tipe kamar valid untuk tool), supaya tidak 2x panggil PMS per pesan
    # dan supaya tipe kamar yang disebut AI selalu konsisten dengan yang di-tampilkan.
    rooms_now = await _pms_ketersediaan(api_key_override=conv["_pms_api_key_override"])
    room_types = sorted({r["tipe"] for r in rooms_now if r.get("tipe")})
    system_prompt = await _system_prompt_for(bot, room_types=room_types)
    context = await _build_context(query=message, bot=bot, whatsapp=conv.get("whatsapp") or whatsapp, rooms=rooms_now)
    history_text = compact_history(conv["messages"][:-1], max_turns=12)

    settings_doc = await db.settings.find_one({"_id": "singleton"}) or {}
    llm_provider = settings_doc.get("llm_provider") or DEFAULT_PROVIDER
    llm_model = settings_doc.get("llm_model") or DEFAULT_MODEL

    # First AI turn
    # Jaring pengaman (2026-07-26, temuan nyata lewat uji beban 10 percakapan bersamaan -
    # permintaan user) - burst chat bersamaan bisa memicu RateLimitError/ChatError dari
    # provider (OpenAI 429 dkk), yang SEBELUM perbaikan ini merambat ke luar _run_chat_turn
    # dan tertangkap diam-diam oleh except Exception generik di webhook (whatsapp_cloud/
    # waha) - hasilnya tamu TIDAK PERNAH dapat balasan APAPUN, dan TIDAK ADA alert ke staf
    # sama sekali (beda dari kegagalan KIRIM yang sudah punya alert, lihat _wa_cloud_send_text
    # di bawah - ini kegagalan AI GENERATE balasan, lebih awal). Sekarang ditangkap di sini
    # (satu titik, berlaku utk simulator/WAHA/Cloud API sekaligus) - tamu tetap dapat
    # balasan fallback yang jujur, staf dapat alert supaya sadar ada beban tinggi.
    tool = None
    tool_result = None
    try:
        raw = await ai_reply(session_id, system_prompt, context, history_text, message, llm_provider, llm_model)
        clean_text, tool, args = parse_tool_call(raw)

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
            # PENGETATAN 2026-07-21 (insiden nyata berulang: AI narasikan "pembatalan sudah
            # diajukan" padahal tool yang barusan dipanggil cuma lookup_booking, cancel_booking
            # TIDAK PERNAH benar-benar dipanggil - lihat catatan cancel_booking di TOOL_DOCS).
            # Instruksi umum "sampaikan konfirmasi natural" ternyata tidak cukup mencegah model
            # menyimpulkan/mengarang hasil tool LAIN yang belum dipanggil - dipertegas di sini
            # supaya narasi TERIKAT KETAT ke tool yang BENAR-BENAR baru saja dieksekusi.
            follow_up_user = (
                f"[SISTEM] Hasil tool `{tool}`: {tool_result}. "
                f"Sampaikan konfirmasi natural ke tamu (Bahasa Indonesia, sopan, singkat) - TAPI JANGAN PERNAH mengklaim "
                f"hasil/status dari tool LAIN yang BELUM dipanggil di giliran ini. Contoh nyata yang HARUS dihindari: kalau "
                f"tool yang dipanggil cuma `lookup_booking` (mencari data), JANGAN bilang 'pembatalan sudah diajukan'/'sudah "
                f"diproses' - itu klaim dari tool `cancel_booking` yang BELUM dipanggil. Kalau tamu jelas ingin lanjut ke "
                f"aksi berikutnya (mis. batalkan) berdasar hasil lookup ini, TANYA konfirmasi & sebutkan kamu akan proses di "
                f"langkah berikutnya - JANGAN klaim sudah selesai. Jangan panggil tool lagi kecuali tamu memintanya."
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
    except ChatError as e:
        final_text = (
            "Mohon maaf, saat ini sedang banyak permintaan yang kami proses sehingga balasan "
            "sedikit terlambat. Bisa kirim ulang pesan Kakak dalam beberapa saat lagi? 🙏"
        )
        logging.getLogger("ai_overload").warning(f"AI gagal generate balasan (conv {conv.get('_id')}, session {session_id}): {e}")
        try:
            await _pms_alert_owner(
                f"⚠️ AI GAGAL memproses chat (kemungkinan overload/rate-limit provider) - "
                f"tamu {whatsapp or conv.get('whatsapp')}, sesi {session_id}. Tamu sudah dikirimi "
                f"pesan fallback minta kirim ulang - cek kalau ini sering terjadi (mungkin perlu "
                f"naikkan tier rate-limit provider AI)."
            )
        except Exception:
            pass

    # Jaring pengaman level KODE (2026-07-21) - insiden BERULANG (3x dalam 1 sesi, makin
    # parah): AI (gpt-4o-mini) kadang mengklaim pembatalan "sudah diajukan"/"sedang
    # ditinjau" TANPA memanggil cancel_booking SAMA SEKALI di giliran ini (bukan cuma salah
    # tool - kali ini tool==None total). Prompt sudah diperkuat 3x, tetap kambuh - ini
    # BUKAN lagi masalah instruksi kurang jelas, model ini genuinely tidak reliable untuk
    # pola ini. Deteksi klaim itu di TEKS BALASAN, kalau cancel_booking TIDAK benar-benar
    # dipanggil giliran ini, PAKSA panggil sungguhan (kode kosong - PMS auto-cari, aman)
    # supaya tamu SELALU dapat balasan yang mencerminkan REALITAS, bukan karangan model.
    if tool != "cancel_booking" and re.search(
        r"pembatalan[^.]{0,120}(sudah|telah)\s+(di)?ajukan"
        r"|(sudah|telah)\s+dalam\s+proses\s+pembatalan"
        r"|permintaan\s+pembatalan[^.]{0,120}(sudah|telah)"
        r"|(sudah|telah)\s+(kami\s+)?(setujui|disetujui)",
        final_text, re.IGNORECASE,
    ):
        wa_guard = conv.get("whatsapp") or whatsapp
        koreksi = await _pms_ajukan_pembatalan("", wa_guard, "", api_key_override=conv.get("_pms_api_key_override")) if wa_guard else {"ok": False, "error": "missing whatsapp"}
        if koreksi.get("ok"):
            final_text = (
                f"Baik, permintaan pembatalan booking {koreksi.get('kode')} sudah saya ajukan ke staf kami. "
                f"Sesuai kebijakan: {koreksi.get('policy_label')}. Setelah staf setujui, Anda akan dapat "
                f"konfirmasi terpisah dengan rincian refund pastinya."
            )
        elif koreksi.get("kandidat"):
            daftar = "\n".join(f"- {k['kode']} ({k.get('room_tipe')}, {k.get('tanggal')})" for k in koreksi["kandidat"])
            final_text = f"Kak, Anda punya beberapa booking aktif - yang mana yang mau dibatalkan?\n{daftar}"
        else:
            final_text = koreksi.get("error") or "Maaf, saya belum bisa memproses pembatalan ini - boleh sebutkan kode booking yang mau dibatalkan?"
        logging.getLogger("hallucination_guard").warning(
            f"cancel_booking hallucination terdeteksi & dikoreksi - conv {conv.get('_id')}, wa {wa_guard}, hasil: {koreksi}"
        )
        try:
            await _pms_alert_owner(
                f"⚠️ AI sempat mengklaim pembatalan tanpa memproses sungguhan (auto-dikoreksi sistem) - "
                f"tamu {wa_guard}, hasil: {'berhasil diajukan' if koreksi.get('ok') else koreksi.get('error')}"
            )
        except Exception:
            pass

    # Jaring pengaman level KODE (2026-07-22) - insiden nyata: AI menulis RINGKASAN harga
    # (Nama/Tipe/Harga/Service X%/Total) TANPA memanggil preview_booking sama sekali (tool
    # giliran ini None/lain), lalu mengarang service fee "10%" dan bahkan NGOTOT ketika tamu
    # koreksi ("service fee kami 10%") - padahal server SELALU pakai 3% tetap (SERVICE_FEE_PCT
    # di PMS core.py). Beda dari kasus cancel_booking, di sini kita TIDAK punya cara aman
    # untuk auto-panggil preview_booking (butuh tipe kamar/tanggal terstruktur yang belum
    # tentu lengkap) - tapi angka 3% itu KONSTANTA GLOBAL yang tidak pernah berubah per
    # booking, jadi aman dikoreksi langsung di teks tanpa perlu tool call apapun.
    _SERVICE_FEE_PERSEN_BENAR = 3
    def _koreksi_service_fee_persen(m):
        return m.group(0).replace(m.group(1), str(_SERVICE_FEE_PERSEN_BENAR))
    final_text_koreksi, _n_koreksi = re.subn(
        r"[Ss]ervice(?:\s+[Ff]ee)?[^%\n]{0,30}?(\d+)\s*%",
        _koreksi_service_fee_persen, final_text,
    )
    if _n_koreksi and final_text_koreksi != final_text:
        logging.getLogger("hallucination_guard").warning(
            f"service fee persen salah terdeteksi & dikoreksi ke {_SERVICE_FEE_PERSEN_BENAR}% - "
            f"conv {conv.get('_id')}, teks asli: {final_text!r}"
        )
        final_text = final_text_koreksi

    # Jaring pengaman level KODE (2026-07-27) - insiden nyata (laporan user, terlihat di
    # riwayat chat): AI menulis ringkasan harga (mirip kasus service-fee di atas) TANPA
    # memanggil preview_booking, sehingga baris "Kedatangan ke-N, dapat diskon member X%"
    # ikut hilang dari ringkasan - tamu confirm bayar tanpa tahu dia dapat diskon, padahal
    # kebijakan WAJIB sampaikan proaktif SEBELUM ditanya. Beda dari preview_booking (butuh
    # tipe kamar/tanggal terstruktur yang belum tentu lengkap saat ini), check_member_status
    # CUMA butuh nomor WA - aman dipanggil ulang di sini sebagai jaring pengaman tanpa
    # risiko data tidak lengkap. Deteksi: teks terlihat seperti ringkasan harga ("Total:
    # Rp...") tapi sama sekali tidak menyebut "kedatangan"/"diskon".
    if (
        tool != "preview_booking"
        and re.search(r"total\s*:?\s*rp", final_text, re.IGNORECASE)
        and not re.search(r"kedatangan|diskon", final_text, re.IGNORECASE)
    ):
        wa_guard = conv.get("whatsapp") or whatsapp
        if wa_guard:
            status_member = await _pms_status_member(wa_guard, api_key_override=conv.get("_pms_api_key_override"))
            diskon_persen = status_member.get("diskon_persen") or 0
            kedatangan_ke = status_member.get("kedatangan_ke")
            if diskon_persen > 0 and kedatangan_ke:
                final_text = (
                    final_text.rstrip()
                    + f"\n\n📌 Catatan: Ini kedatangan Kakak yang ke-{kedatangan_ke}, dapat diskon "
                    f"member {diskon_persen}% - total final (setelah diskon) akan lebih rendah dari "
                    f"angka di atas, dihitung otomatis saat booking diproses."
                )
                logging.getLogger("hallucination_guard").warning(
                    f"ringkasan harga tanpa info diskon member terdeteksi & ditambahkan - "
                    f"conv {conv.get('_id')}, kedatangan_ke={kedatangan_ke}, diskon={diskon_persen}%"
                )

    # Jaring pengaman level KODE (2026-07-24) - insiden nyata BERULANG: instruksi prompt
    # "JANGAN ulangi link checkout_url di balasan sendiri" (ditambahkan 2026-07-21 setelah
    # laporan user pertama) TERBUKTI tidak selalu dipatuhi model - tamu tetap dapat link
    # yang sama 2x (1x dari pesan WA terpisah yang PMS kirim otomatis, 1x lagi ditempel AI
    # di balasannya sendiri). Sama seperti kasus service-fee di atas, prompt-only tidak
    # cukup - paksa hapus link checkout_url dari teks apa pun yang ditulis AI kalau tool
    # giliran ini create_booking DAN hasilnya benar-benar punya checkout_url (artinya PMS
    # SUDAH mengirim pesan WA terpisah berisi link itu).
    if tool == "create_booking" and isinstance(tool_result, dict) and tool_result.get("checkout_url"):
        url = tool_result["checkout_url"]
        if url in final_text:
            # Ikut buang frasa pengantar tepat sebelum link ("...melalui link berikut:")
            # supaya tidak menyisakan kalimat menggantung ("link berikut: .") - kalau
            # frasa pengantar tidak ketemu, minimal link/markdown-nya tetap terhapus.
            final_text_tanpa_link = re.sub(
                r"(?:[^.\n]*?(?:link|tautan)[^.\n]*?:\s*)?"          # frasa pengantar opsional
                r"(?:\[[^\]]*\]\(" + re.escape(url) + r"\)|" + re.escape(url) + r")"
                r"\.?",
                "", final_text, flags=re.IGNORECASE,
            )
            final_text_tanpa_link = re.sub(r"[ \t]{2,}", " ", final_text_tanpa_link)
            final_text_tanpa_link = re.sub(r"\n{3,}", "\n\n", final_text_tanpa_link).strip()
            logging.getLogger("hallucination_guard").warning(
                f"AI menulis ulang link pembayaran padahal PMS sudah kirim terpisah - dihapus dari balasan. "
                f"conv {conv.get('_id')}, teks asli: {final_text!r}"
            )
            final_text = final_text_tanpa_link or "Baik, link pembayaran sudah dikirim ke WhatsApp Kakak secara terpisah ya."

    # Jaring pengaman level KODE (2026-07-24) - insiden nyata ditemukan lewat pengujian
    # Chat Simulator (bukan laporan tamu): meski prompt SUDAH menegaskan "extra bed HANYA
    # tersedia untuk tipe Cottage, Standard TIDAK BISA pakai extra bed sama sekali" (lihat
    # SYSTEM_PROMPT di ai_service.py), model tetap mengarang kebijakan usia-anak palsu &
    # menyetujui extra bed untuk kamar Standard lengkap dengan harga karangan sendiri -
    # bahkan lanjut menanyakan tanggal check-in seolah booking itu valid. Sama seperti
    # kasus service-fee/cancel_booking, prompt-only tidak cukup. Sinyal deteksi presisi
    # tinggi: kalau balasan menyebut "extra bed" BERSAMA nama tipe kamar non-Cottage tanpa
    # sama sekali menyebut "Cottage" - jawaban yang BENAR (baik menyetujui utk Cottage
    # maupun menolak utk tipe lain) SELALU harus menyebut "Cottage" karena itu satu-satunya
    # tipe yang valid, jadi ketidakhadirannya adalah sinyal kuat klaim tsb salah/karangan.
    if re.search(r"extra\s*bed", final_text, re.IGNORECASE) and "cottage" not in final_text.lower():
        logging.getLogger("hallucination_guard").warning(
            f"extra bed non-Cottage hallucination terdeteksi & dikoreksi - conv {conv.get('_id')}, "
            f"teks asli: {final_text!r}"
        )
        final_text = (
            "Mohon maaf Kak, ada koreksi ya - extra bed hanya tersedia untuk tipe kamar Cottage "
            "(kapasitas jadi 3 dewasa + 1 anak). Untuk tipe kamar lain (termasuk Standard), extra bed "
            "tidak tersedia sama sekali, bukan soal stok/usia anak, memang tidak ditawarkan untuk tipe "
            "itu. Kalau Kakak butuh kapasitas lebih dari 2 dewasa + 1 anak per kamar, saya sarankan "
            "pilih kamar Cottage ya. Mau saya bantu cek ketersediaannya? 😊"
        )
        try:
            await _pms_alert_owner(
                f"⚠️ AI sempat mengarang kebijakan extra bed untuk kamar non-Cottage (auto-dikoreksi sistem) - "
                f"tamu {conv.get('whatsapp') or whatsapp}"
            )
        except Exception:
            pass

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
        update["bot_id"] = str(bot.get("_id")) if bot.get("_id") is not None else None
        update["bot_code"] = bot.get("code")
    if conv.get("last_booking_request"):
        update["last_booking_request"] = conv["last_booking_request"]
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


async def _catat_pesan_sistem(conv: Optional[dict], whatsapp: str, content: str) -> None:
    """Catat pesan yang dikirim SISTEM (link pembayaran Tripay, voucher/invoice PDF,
    konfirmasi approve/tolak dari PMS lewat /send-message & /send-document) ke riwayat
    percakapan - BEDA dari balasan AI maupun balasan manual staf (`from_admin`). Ditemukan
    2026-07-27 lewat laporan user: pesan-pesan ini SUNGGUHAN terkirim ke tamu tapi tidak
    pernah tercatat sama sekali di dashboard Percakapan - staf tidak pernah lihat jejaknya,
    padahal dashboard ini satu-satunya tempat baca chat tamu sejak migrasi Cloud API. Kalau
    belum ada percakapan sama sekali utk nomor ini (mis. kirim ke tamu yang belum pernah
    chat AI, atau ke nomor staf utk slip gaji), buat percakapan baru supaya tetap ada
    jejaknya, bukan didiamkan begitu saja."""
    entry = {
        "role": "assistant", "content": content, "timestamp": utc_now_iso(),
        "intent": None, "from_system": True,
    }
    if conv:
        messages = conv.get("messages", []) + [entry]
        await db.conversations.update_one(
            {"_id": conv["_id"]}, {"$set": {"messages": messages, "updated_at": utc_now_iso()}},
        )
    else:
        await db.conversations.insert_one({
            "_id": new_id(), "session_id": f"sys-{whatsapp}-{new_id()[:8]}",
            "guest_name": None, "whatsapp": whatsapp, "channel": "whatsapp_cloud",
            "waha_session": None, "cloud_phone_number_id": WHATSAPP_CLOUD_PHONE_NUMBER_ID,
            "messages": [entry], "status": "active", "resolution": "unresolved",
            "booking_created": False, "last_intent": None, "response_time_ms": 0,
            "created_at": utc_now_iso(), "updated_at": utc_now_iso(),
        })


class SendMessageIn(BaseModel):
    to: str
    message: str
    template_name: Optional[str] = None
    template_params: Optional[List[str]] = None


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
    punya kredensial masing-masing yang bisa di-revoke terpisah.
    `template_name`/`template_params` (2026-07-26, opsional) - dipakai `_send_wa_transactional`
    kalau nomor ini Cloud API DAN sudah di luar jendela layanan 24 jam Meta, WAJIB pakai
    Message Template yang sudah disetujui (teks bebas biasa akan ditolak Meta)."""
    cfg = await _pms_config()
    auth = request.headers.get("Authorization", "")
    key = auth[7:] if auth.startswith("Bearer ") else ""
    if not cfg.get("send_message_api_key") or not key or not secrets.compare_digest(key, cfg["send_message_api_key"]):
        raise HTTPException(401, "API key tidak valid")

    # Ditemukan 2026-07-19 dari laporan user (link pembayaran diklik "Terima" di PMS tapi
    # tidak sampai ke tamu): nomor yang tamu ketik sendiri lewat chat sering format lokal
    # "0877..." (bukan "62877..."), sebelumnya cuma dibuang karakter non-digit tanpa
    # dikonversi ke 62 - "0877...@c.us" BUKAN JID WhatsApp yang valid, WAHA gagal kirim
    # diam-diam. Reuse _normalize_phone yang sama dipakai Guest Profile supaya konsisten.
    digits = _normalize_phone(body.to or "")
    if not digits or not body.message.strip():
        raise HTTPException(400, "to/message tidak valid")
    # Cari percakapan terakhir nomor ini utk tau channel yg benar (WAHA atau Cloud API) -
    # ditemukan 2026-07-21: sebelum ini SELALU hardcode WAHA meski tamunya chat via Cloud
    # API, jadi notifikasi (voucher, link bayar dst) gagal diam-diam saat WAHA down padahal
    # Cloud API-nya sehat. Fallback ke WAHA kalau belum pernah ada percakapan (mis. nomor
    # dari input manual staf) - itu perilaku lama, tetap aman dipertahankan.
    conv = await db.conversations.find_one({"whatsapp": digits}, sort=[("updated_at", -1)])
    ok = await _send_wa_transactional(conv, body.message, whatsapp_fallback=digits,
                                       template_name=body.template_name, template_params=body.template_params)
    await _pms_log("/send-message", "POST", 200 if ok else 502, 0, ok, f"to {digits}")
    if ok:
        await _catat_pesan_sistem(conv, digits, body.message)
    if not ok:
        raise HTTPException(502, "Gagal mengirim pesan lewat WhatsApp")
    return {"ok": True}


class SendDocumentIn(BaseModel):
    to: str
    filename: str
    mimetype: str = "application/pdf"
    data_base64: str
    caption: str = ""


@api.post("/send-document")
async def send_document_relay(body: SendDocumentIn, request: Request, _rl: None = Depends(rate_limiter(10, 10))):
    """Sibling dari /send-message tapi untuk FILE (2026-07-20, dipakai routes/payroll.py di
    repo PMS untuk kirim slip gaji PDF ke WA staf) - auth & pola identik dengan
    send_message_relay, cuma payloadnya dokumen base64 bukan teks."""
    cfg = await _pms_config()
    auth = request.headers.get("Authorization", "")
    key = auth[7:] if auth.startswith("Bearer ") else ""
    if not cfg.get("send_message_api_key") or not key or not secrets.compare_digest(key, cfg["send_message_api_key"]):
        raise HTTPException(401, "API key tidak valid")

    digits = _normalize_phone(body.to or "")
    if not digits or not body.data_base64:
        raise HTTPException(400, "to/data_base64 tidak valid")
    conv = await db.conversations.find_one({"whatsapp": digits}, sort=[("updated_at", -1)])
    ok = (
        await _send_wa_document_smart(conv, body.filename, body.mimetype, body.data_base64, body.caption)
        if conv else
        await _waha_send_file(f"{digits}@c.us", body.filename, body.mimetype, body.data_base64, body.caption)
    )
    await _pms_log("/send-document", "POST", 200 if ok else 502, 0, ok, f"to {digits}")
    if ok:
        catatan = f"📎 Dokumen dikirim: {body.filename}" + (f" — {body.caption}" if body.caption else "")
        await _catat_pesan_sistem(conv, digits, catatan)
    if not ok:
        raise HTTPException(502, "Gagal mengirim dokumen lewat WhatsApp")
    return {"ok": True}


WHATSAPP_CLOUD_VERIFY_TOKEN = os.environ.get("WHATSAPP_CLOUD_VERIFY_TOKEN", "")


@api.get("/webhook/whatsapp-cloud")
async def whatsapp_cloud_webhook_verify(request: Request):
    """Verifikasi webhook Meta Cloud API (2026-07-20, tahap awal migrasi WAHA -> WABA resmi,
    dipicu insiden blokir WhatsApp dari reconnect-storm WAHA). Meta memanggil GET ini SEKALI
    saat tombol "Verify and Save" diklik di dashboard Meta, kirim hub.mode=subscribe,
    hub.verify_token, & hub.challenge lewat query string. Kalau token cocok, WAJIB balas
    PERSIS isi hub.challenge sebagai plain text (bukan JSON/objek) - itu aturan resmi Meta,
    format lain akan ditolak dan verifikasi gagal."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge") or ""
    if mode == "subscribe" and WHATSAPP_CLOUD_VERIFY_TOKEN and secrets.compare_digest(token or "", WHATSAPP_CLOUD_VERIFY_TOKEN):
        return PlainTextResponse(challenge)
    raise HTTPException(403, "Verifikasi webhook gagal - token tidak cocok")


@api.post("/webhook/whatsapp-cloud")
async def whatsapp_cloud_webhook_receive(request: Request):
    """Terima pesan masuk dari Meta Cloud API & balas lewat AI - pola SAMA PERSIS dengan
    webhook_waha (reuse _run_chat_turn) supaya AI yang menjawab tamu konsisten apa pun
    jalur WhatsApp-nya (WAHA lama vs Cloud API baru). Bedanya cuma bentuk payload masuk
    (struktur resmi Meta: entry[0].changes[0].value.messages[0]) & fungsi kirim balasan
    (_wa_cloud_send_text/_wa_cloud_send_image, bukan _waha_send_*).

    TAHAP AWAL (2026-07-20): kredensial di .env masih WABA & nomor UJI COBA Meta, bukan
    nomor Admin asli - aman untuk tes end-to-end dulu. `phone_number_id` dipakai sebagai
    padanan `channel_id` WAHA untuk multi-nomor (channel_type="whatsapp_cloud" di ai_bots),
    fallback ke bot default (booking_marketing) kalau belum ada yang ditautkan."""
    payload = await request.json()
    try:
        entry = (payload.get("entry") or [{}])[0]
        change = (entry.get("changes") or [{}])[0]
        value = change.get("value") or {}
        messages = value.get("messages") or []
        if not messages:
            statuses = value.get("statuses") or []
            for s in statuses:
                if s.get("status") == "failed":
                    logging.getLogger("whatsapp_cloud").warning(f"Pesan GAGAL terkirim: {s}")
            return {"ok": True, "diabaikan": "bukan pesan masuk (mis. status update pengiriman)"}
        msg = messages[0]
        if msg.get("type") != "text":
            return {"ok": True, "diabaikan": f"tipe pesan '{msg.get('type')}' belum didukung"}

        wamid = msg.get("id") or ""
        if wamid:
            try:
                await db.wa_cloud_dedup.insert_one({"wamid": wamid, "ts": datetime.now(timezone.utc)})
            except DuplicateKeyError:
                # Meta kirim webhook yang sama lebih dari sekali (at-least-once delivery,
                # terkonfirmasi nyata 2026-07-20 - 1 pesan tamu memicu 2 balasan AI sebelum
                # dedup ini ada) - pesan dengan wamid yang sama diabaikan diam-diam.
                return {"ok": True, "diabaikan": "duplikat webhook (wamid sudah diproses)"}

        phone = msg.get("from") or ""
        message_text = ((msg.get("text") or {}).get("body") or "").strip()
        if not phone or not message_text:
            return {"ok": True, "diabaikan": "tanpa nomor pengirim/isi pesan"}

        contacts = value.get("contacts") or []
        guest_name = ((contacts[0].get("profile") or {}) if contacts else {}).get("name") or phone
        phone_number_id = (value.get("metadata") or {}).get("phone_number_id") or WHATSAPP_CLOUD_PHONE_NUMBER_ID

        linked_bot = await db.ai_bots.find_one({"channel_type": "whatsapp_cloud", "channel_id": phone_number_id})
        bot_id = linked_bot["_id"] if linked_bot else None
        session_id = f"wac-{phone_number_id}-{phone}"

        hasil = await _run_chat_turn(
            session_id, message_text, guest_name, phone, bot_id, None,
            channel="whatsapp_cloud", cloud_phone_number_id=phone_number_id,
        )
        if hasil.get("reply"):
            await asyncio.sleep(random.uniform(3, 5))
            clean_text, image_urls = parse_img_markers(hasil["reply"])
            if clean_text:
                # Jaring pengaman (2026-07-25) - ditemukan lewat evaluasi: hasil kirim
                # sebelumnya dibuang begitu saja, jadi kegagalan (mis. tamu di luar jendela
                # 24 jam customer-service, error 131047) gagal TOTAL secara diam-diam - staf
                # tidak pernah tahu tamu tidak benar-benar menerima balasan.
                terkirim = await _wa_cloud_send_text(phone, clean_text, phone_number_id=phone_number_id)
                if not terkirim:
                    logging.getLogger("whatsapp_cloud").error(
                        f"Gagal kirim balasan WA ke {phone} (conv session {session_id}) - "
                        f"kemungkinan di luar jendela 24 jam customer-service atau error Cloud API lain."
                    )
                    try:
                        await _pms_alert_owner(
                            f"⚠️ AI GAGAL kirim balasan WhatsApp ke tamu {phone} - "
                            f"kemungkinan tamu belum kirim pesan dalam 24 jam terakhir. "
                            f"Cek percakapan & hubungi tamu manual kalau perlu."
                        )
                    except Exception:
                        pass
            for i, url in enumerate(image_urls):
                if i > 0:
                    await asyncio.sleep(random.uniform(1, 2))
                room = await db.rooms.find_one({"$or": [{"photo_url": url}, {"images.url": url}]})
                caption = room["name"] if room else ""
                await _wa_cloud_send_image(phone, url, caption, phone_number_id=phone_number_id)
        return {"ok": True}
    except Exception as e:
        logging.getLogger("whatsapp_cloud").warning(f"Gagal proses webhook Cloud API: {e}")
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
        for i, url in enumerate(image_urls):
            if i > 0:
                # Jeda singkat ANTAR foto (beda dari jeda 3-5 detik di atas, yang cuma
                # sekali sebelum mulai kirim) - supaya kiriman 6 foto sekaligus terasa
                # seperti dikirim satu-satu secara wajar, bukan burst instan yang
                # mencurigakan (2026-07-19, dikonfirmasi user kirim SEMUA foto kamar).
                await asyncio.sleep(random.uniform(1, 2))
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
