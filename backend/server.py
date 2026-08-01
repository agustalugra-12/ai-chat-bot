"""Pelangi AI — AI Customer Service Platform (FastAPI backend).

Brain Platform reusable lintas channel (WhatsApp lewat Fonnte/Cloud API, website chat/dst)
& lintas bisnis (Business System Connector, lihat connectors/pms_connector.py untuk
integrasi Pelangi PMS) - bukan "AI WhatsApp Bot" yang terikat satu channel/satu bisnis
(PRD v2, 2026-07-19). WAHA (gateway self-hosted lama) dihapus 2026-08-01, digantikan
Fonnte sepenuhnya.
"""
import os
import asyncio
import hashlib
import hmac
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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
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
from connectors.pms_connector import (
    PMS_API_BASE_URL, PMS_API_KEY, PMS_DEFAULT_ENDPOINTS,
    PMS_CAPABILITY_WIRED, PMS_DEFAULT_CAPABILITIES, PMS_INTEGRATION_DEFAULT,
    SYNC_KINDS,
    _pms_config, _pms_log, _pms_ketersediaan, _pms_buat_booking_request,
    _pms_buat_tiket, _pms_status_booking, _pms_status_member, _pms_ajukan_pembatalan, _sync_business_rules,
    _pms_alert_owner, _pms_preview_harga, _pms_menu, _pms_timeline_kamar,
)
from connectors.webpelangi_connector import (
    _web_content_config, _sync_hotel_profile, _sync_faq,
)
from connectors.whatsapp_cloud_connector import (
    WHATSAPP_CLOUD_PHONE_NUMBER_ID, _wa_cloud_send_text, _wa_cloud_send_image, _wa_cloud_send_document,
    _wa_cloud_send_template,
)
from connectors.fonnte_connector import _fonnte_send_text, _fonnte_send_link_message


# ---- Rate Limiting ----
# In-memory murni (tanpa dependency baru/Redis) - cukup untuk deployment single-instance
# seperti sekarang. Melindungi endpoint yang benar-benar publik lewat internet:
# /auth/login (brute force password) dan /webhook/waha (endpoint token-only, bisa
# dihajar dari IP mana pun kalau token bocor/ditebak).
_rate_limit_buckets: Dict[str, List[float]] = {}

# Kunci per-percakapan (2026-08-01, bug nyata ditemukan lewat laporan Agus "AI seperti
# spam ke konsumen setelah kirim link payment") - _run_chat_turn tidak pernah punya
# concurrency guard: kalau tamu kirim 2 pesan cepat berturut-turut SEBELUM giliran
# pertama selesai diproses (LLM+tool-calling bisa makan puluhan detik), webhook Fonnte
# mengirim 2 request terpisah yang KEDUANYA diproses bersamaan oleh 2 pemanggilan
# _run_chat_turn yang overlap - masing-masing baca `conv["messages"]` versi LAMA (giliran
# pertama belum sempat simpan balasannya), jadi giliran kedua TIDAK TAHU booking baru saja
# dibuat & bisa membuat booking KEDUA yang duplikat (dikonfirmasi nyata: 1 tamu, 1 kali
# bilang "dp aja kak" 2x karena tidak sabar, hasilnya 2 booking asli + 2 link pembayaran +
# 1 voucher - persis pola yang dilaporkan). Server ini 1 proses/1 worker (uvicorn tanpa
# --workers), jadi asyncio.Lock in-process per session_id sudah cukup, tidak perlu lock
# terdistribusi (Redis/Mongo).
_conversation_locks: Dict[str, asyncio.Lock] = {}


def _get_conversation_lock(session_id: str) -> asyncio.Lock:
    lock = _conversation_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _conversation_locks[session_id] = lock
        # Katup pengaman sama pola dgn _rate_limit_buckets - jarang kena di skala 1-2
        # hotel, tapi murah dijaga supaya dict tidak tumbuh tak terbatas.
        if len(_conversation_locks) > 20000:
            _conversation_locks.clear()
            _conversation_locks[session_id] = lock
    return lock


# Debounce pesan Fonnte (2026-08-01, permintaan Agus - laporan nyata: tamu yang ngetik
# beberapa pesan cepat berturut-turut sebelumnya dapat balasan AI TERPISAH utk TIAP
# pesan, terasa seperti di-spam) - lihat _fonnte_debounced_dispatch di dekat
# fonnte_webhook_receive utk detail alurnya. State ini in-process (server 1 worker,
# sama seperti _conversation_locks) - KETERBATASAN DISADARI: kalau proses restart PAS
# giliran sedang menunggu jeda debounce (maks FONNTE_DEBOUNCE_SECONDS detik), pesan yang
# belum sempat diproses akan hilang (webhook sudah lanjut balas 200 OK ke Fonnte lebih
# dulu, jadi Fonnte tidak akan kirim ulang) - risiko kecil & disengaja diterima demi
# jendela debounce yang singkat, sudah didiskusikan & disetujui Agus.
FONNTE_DEBOUNCE_SECONDS = 4.0
_fonnte_pending_messages: Dict[str, List[str]] = {}
_fonnte_pending_ctx: Dict[str, dict] = {}
_fonnte_debounce_tasks: Dict[str, asyncio.Task] = {}


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


@app.exception_handler(RequestValidationError)
async def _log_validation_error(request: Request, exc: RequestValidationError):
    # Dipakai 2026-07-31 utk lacak 422 berulang di /send-message (akar masalah: PMS
    # push_sync_event() salah kirim event non-guest ke endpoint ini, sudah diperbaiki di
    # sisi PMS) - access log bawaan cuma tampilkan "422 Unprocessable Entity" tanpa body,
    # jadi ditambah permanen supaya 422 di endpoint manapun ke depan langsung kelihatan
    # field mana yang gagal validasi, tidak perlu re-investigasi dari nol tiap kali.
    logging.getLogger("validation").warning(f"422 di {request.url.path}: {exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    await seed_all(db)
    logger.info("Seed complete")
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
    # Bug nyata ditemukan 2026-07-31 (Agus tanya apakah AI diam kalau dia ambil alih chat
    # langsung) - "fonnte" TIDAK PERNAH masuk daftar ini sebelumnya (cuma "whatsapp"/
    # "whatsapp_cloud", sisa dari sebelum migrasi Fonnte 2026-07-31) - balasan manual staf
    # KORBAN DIAM-DIAM tidak pernah benar-benar terkirim ke tamu via Fonnte (Pelangi &
    # Harmoni SEKARANG DUA-DUANYA Fonnte) meski AI sudah benar berhenti membalas & status
    # tersimpan "waiting_admin" - tamu tidak pernah lihat balasan staf sama sekali.
    if conv.get("channel") in ("whatsapp", "whatsapp_cloud", "fonnte") and conv.get("whatsapp"):
        # Balas lewat channel & nomor WA yang SAMA dengan yang tamu hubungi (WAHA, Cloud
        # API, atau Fonnte, bisa beda-beda sejak multi-nomor per AI bot 2026-07-19, migrasi
        # Cloud API 2026-07-21, migrasi Fonnte 2026-07-31) - fallback ke default kalau conv
        # lama belum punya field ini (dibuat sebelum fitur ini ada).
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


def _guest_profile_key(whatsapp: Optional[str], property_slug: str) -> str:
    """Kunci profil tamu di-scope PER PROPERTI (2026-07-31, bug nyata ditemukan - lihat
    memory proyek `project_guest_profile_property_leak`): sebelumnya `_id` cuma nomor HP
    polos, jadi kalau 1 nomor sama chat ke bot Pelangi DAN bot Harmoni, keduanya berbagi
    SATU profil global - nama/preferensi/jumlah kunjungan yang dipelajari dari properti A
    ikut disuntik ke prompt AI properti B (lewat `_get_guest_profile` di `_build_context`),
    padahal konten fasilitas/kamar/KB sudah benar disilo per properti sejak guard yang
    sama. Kunci sekarang "{nomor}:{property_slug}" supaya tiap properti punya memory
    tamu sendiri-sendiri, konsisten dengan `db.guests` PMS yang sudah disilo `property_id`
    (`core.py` `scoped()`)."""
    return f"{_normalize_phone(whatsapp or '')}:{property_slug}"


async def _touch_guest_profile(whatsapp: Optional[str], guest_name: Optional[str], is_new_conversation: bool,
                                property_slug: str = "pelangi") -> None:
    """Memory (tahap 1 - short/long/preference): dipanggil tiap giliran chat supaya profil
    tamu selalu punya nama & waktu terakhir dilihat terkini, DAN supaya percakapan baru
    dari nomor yang sama tercatat sebagai kunjungan berulang (total_conversations)."""
    phone = _normalize_phone(whatsapp or "")
    if not phone:
        return
    updates: Dict[str, Any] = {"last_seen_at": utc_now_iso(), "whatsapp": phone, "property_slug": property_slug}
    if guest_name:
        updates["nama"] = guest_name
    op: Dict[str, Any] = {"$set": updates, "$setOnInsert": {"created_at": utc_now_iso()}}
    if is_new_conversation:
        op["$inc"] = {"total_conversations": 1}
    await db.guest_profiles.update_one({"_id": _guest_profile_key(whatsapp, property_slug)}, op, upsert=True)


async def _get_guest_profile(whatsapp: Optional[str], property_slug: str = "pelangi") -> Optional[dict]:
    if not _normalize_phone(whatsapp or ""):
        return None
    return await db.guest_profiles.find_one({"_id": _guest_profile_key(whatsapp, property_slug)})


async def _build_context(query: Optional[str] = None, bot: Optional[dict] = None, whatsapp: Optional[str] = None,
                          rooms: Optional[List[dict]] = None, menu: Optional[List[dict]] = None,
                          timeline_kamar: Optional[List[dict]] = None) -> str:
    if rooms is None:
        rooms = await _pms_ketersediaan()
    if menu is None:
        menu = await _pms_menu()
    # Gambaran operasional kamar hari ini (2026-08-01, permintaan Agus: "AI mendapat info
    # dari PMS tentang apapun itu... PMS selalu sinkron ke AI bot") - SELALU disuntik tiap
    # giliran chat (bukan tool on-demand) supaya AI otomatis tahu ada Day Use yang akan
    # checkout jam sekian / kamar sedang dibersihkan, tanpa perlu tamu tanya spesifik dulu.
    # Pola sama dgn rooms/menu di atas - caller utama (_run_chat_turn) fetch duluan pakai
    # api_key_override yang benar (multi-properti), di sini cuma fallback kalau tidak diisi.
    if timeline_kamar is None:
        timeline_kamar = await _pms_timeline_kamar()

    # Guard multi-properti (2026-07-31) - db.settings/db.rooms(lokal)/db.knowledge_base/
    # db.menu SEMUA masih 1 data global berisi konten PELANGI SAJA (alamat, foto kamar,
    # fasilitas, FAQ lokasi) - belum ada skema per-properti (butuh migrasi + data ASLI
    # Harmoni yg belum tersedia, lihat memory proyek). Bug NYATA ditemukan lewat tes Chat
    # Simulator sebelum guard ini ada: bot Harmoni menjawab pakai alamat & foto kamar
    # Pelangi seolah-olah miliknya sendiri. `property_slug` bot (lihat models.py) kosong
    # ATAU "pelangi" = perilaku lama (dapat konten penuh, TIDAK ADA YANG BERUBAH utk bot
    # Pelangi/Resepsionis) - nilai lain ("harmoni") = konten Pelangi-only ini DILEWATI,
    # AI diberi tahu jujur "info detail belum tersedia" drpd menyamarkan data Pelangi.
    property_slug = (bot or {}).get("property_slug") or "pelangi"
    is_pelangi_content = property_slug == "pelangi"

    # Menu kasir (2026-08-01) - LIVE dari PMS (lihat _pms_menu), bukan lagi db.menu lokal
    # ai-chat-bot yang isinya data seed/demo tidak pernah sinkron dengan kasir asli
    # (permintaan Agus: "pengetahuan menu sebelumnya salah, ambil dari PMS bagian kasir").
    # Guard is_pelangi_content dipertahankan sama seperti sebelumnya - Harmoni memang
    # belum punya layanan resto/kasir sama sekali (0 produk, dikonfirmasi live).
    menu = menu if is_pelangi_content else []
    # `property_slug` (2026-08-01) - knowledge_base sekarang per-properti, sama pola dgn
    # db.rooms lokal di bawah (bukan lagi on/off total Pelangi-only) - 17 entri lama
    # di-backfill property_slug="pelangi", 8 entri asli baru ditambah utk Harmoni (dari
    # konten real yang sudah dipublish di web-pelangi site_content, BUKAN karangan).
    kb_q = {"is_active": True, "property_slug": property_slug}
    if bot and bot.get("knowledge_categories"):
        kb_q["category"] = {"$in": bot["knowledge_categories"]}
    kb = await db.knowledge_base.find(kb_q).to_list(500)
    # (2026-07-31) - JANGAN kirim {} kosong ke build_context_block utk bot non-Pelangi:
    # fallback default fungsi itu ("Nama: Pelangi Homestay") jadi ikut kepakai kalau
    # `settings` kosong - bug nyata ditemukan lewat tes: bot Harmoni bilang "Cottage di
    # Pelangi Homestay". Isi `hotel_name` dgn nama publik yg sudah dipakai luas di blog
    # web-pelangi (harmoniby.pelangihomestay.com).
    # `address`/`maps_url` Harmoni (2026-07-31) - Agus kirim embed Google Maps asli
    # (koordinat -8.2598629,115.1631686 + Place ID dari embed-nya sendiri) - dicocokkan
    # ke alamat nyata via reverse-geocode OSM Nominatim (bukan ditebak). Foto kamar & KB/
    # FAQ Harmoni MASIH belum ada data asli - itu sebabnya KETERBATASAN di bawah cuma
    # menyebut foto+KB sekarang, bukan alamat/maps lagi.
    settings = (
        (await db.settings.find_one({"_id": "singleton"}) or {}) if is_pelangi_content
        else {
            "hotel_name": "Harmoni Hills",
            "address": "Jl. Denpasar - Singaraja, Kembangkerta, Candikuning, Baturiti, Tabanan, Bali 82191",
            "maps_url": "https://www.google.com/maps/search/?api=1&query=-8.2598629,115.1631686&query_place_id=0x2dd189006b1f4b49:0xd64ae8ec12451c53",
            # Arah jalan kaki dari titik map (2026-08-01, permintaan Agus) - titik Maps
            # cuma antar sampai area umum, bukan pintu masuk persis, jadi tamu butuh
            # panduan lanjutan dari situ. Disuntik SETELAH link maps di build_context_block.
            "map_directions": (
                "Setelah sampai di titik lokasi, jalan ke arah timur sekitar 100m, "
                "lewati bangunan restoran Tepi Beratan di sebelah kanan. Di ujung jalan "
                "setelah melewati restoran itu, akan terlihat bangunan Harmoni Hills Village."
            ),
            # Kontak darurat (2026-08-01, permintaan Agus) - nomor pribadinya sendiri,
            # KHUSUS tamu tersesat/mau check-in mendesak di atas jam 23:00 (lihat guard di
            # build_context_block, ai_service.py).
            "emergency_phone": "087761611631",
        } if property_slug == "harmoni"
        else {}
    )
    # Foto + fasilitas/deskripsi kamar - koleksi db.rooms LOKAL ai-chat-bot (bukan
    # _pms_ketersediaan di atas, yang cuma tipe/tarif/stok live dari PMS, TIDAK ADA field
    # foto/fasilitas sama sekali). Ditemukan 2026-07-19 (foto) & 2026-07-21 (fasilitas) dari
    # laporan user: tanpa ini AI mengarang fasilitas kamar generik dari pengetahuan umum
    # (mis. "AC, lemari pakaian") - staf sudah isi fasilitas asli di halaman Room Management
    # tapi datanya tidak pernah sampai ke context AI sama sekali, cuma foto yang disertakan.
    # `property_slug` (2026-07-31) - tiap dokumen rooms sekarang ditandai per-properti
    # (lihat memory proyek), jadi query di-filter per-bot, BUKAN on/off total spt
    # settings/kb/menu di atas (yg masih 1 data global Pelangi-only) - begitu Harmoni
    # dapat entry rooms sendiri (spt Cottage yg sudah diisi 2026-07-31), otomatis muncul
    # ke bot Harmoni tanpa perlu ubah kode lagi.
    room_photos = await db.rooms.find(
        {"property_slug": property_slug}, {"name": 1, "photo_url": 1, "images": 1, "facilities": 1, "description": 1}
    ).to_list(50)
    base = build_context_block(rooms, menu, kb, settings, room_photos, timeline_kamar)

    if not is_pelangi_content:
        base += (
            "\n\n# KETERBATASAN DATA PROPERTI INI (WAJIB DIPATUHI)\n"
            "Data FAQ/kebijakan/fasilitas properti ini BELUM SELENGKAP Pelangi Homestay - "
            "yang tertulis di '# KNOWLEDGE BASE'/'# FASILITAS & DESKRIPSI KAMAR'/'# INFO "
            "HOTEL' di atas SUDAH data asli dan boleh disebutkan/dikirim ke tamu apa adanya. "
            "JANGAN PERNAH menyebutkan/mengirim data dari properti lain (mis. Pelangi "
            "Homestay) seolah itu milik properti ini - itu informasi yang SALAH bagi tamu. "
            "Kalau tamu menanyakan hal yang TIDAK tercantum di data di atas, JANGAN mengarang "
            "jawabannya sendiri - jawab jujur bahwa detailnya akan diinfokan staf, dan gunakan "
            "tool eskalasi/tiket kalau tersedia. Ketersediaan kamar & harga (di atas, dari PMS "
            "live) TETAP boleh dan HARUS dijawab seperti biasa."
        )

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
    # section ini sama sekali (tidak ada yang perlu diingat). Di-scope `property_slug`
    # yang sama dgn konten di atas (lihat `_guest_profile_key`) - tamu yang pernah chat
    # ke properti lain TIDAK dianggap "pernah muncul" di sini.
    profile = await _get_guest_profile(whatsapp, property_slug)
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
            tanggal_checkout=args.get("tanggal_checkout"), jumlah_kamar=args.get("jumlah_kamar"),
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
    # 2026-08-01, permintaan Agus - layanan KHUSUS yang butuh konfirmasi staf dulu (bisa/
    # tidak dikerjakan TERGANTUNG kesiapan saat itu, harganya juga ditentukan staf, bukan
    # tarif tetap) - beda dari layanan lain di atas yang staf tinggal sediakan/proses biasa.
    "room_decoration": "Dekorasi Kamar", "birthday_anniversary": "Ucapan/Kejutan Ulang Tahun-Anniversary",
}


@register_tool("create_service_request", {"restaurant_order", "laundry_request", "housekeeping_request",
                                           "room_service", "airport_pickup", "motor_rental",
                                           "room_decoration", "birthday_anniversary"})
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


@register_tool("catat_kedatangan_tamu", {"guest_arrival"})
async def _tool_catat_kedatangan_tamu(args: dict, conv: dict) -> dict:
    """Tamu mengabarkan sudah tiba/dalam perjalanan ke properti (2026-07-28, permintaan
    user: "apakah AI sudah bisa membaca dan mencatat kedatangan tamu?" - jawabannya
    sebelum ini TIDAK ADA sama sekali, tidak ada tool/endpoint apa pun yang mendeteksi
    atau mencatat kedatangan tamu, cuma ada penghitung "kedatangan ke-N" utk loyalitas
    yang dihitung dari riwayat booking, bukan dari kedatangan real-time).

    BUKAN check-in resmi - staf tetap yang melakukan check-in sungguhan (verifikasi ID/
    pembayaran/serah kunci), sama seperti create_booking/cancel_booking yang juga tidak
    pernah jadi aksi final otomatis (lihat prinsip yang sama di TOOL_DOCS keduanya). Tool
    ini CUMA membuat tiket "Permintaan Layanan" ke PMS (reuse _pms_buat_tiket yang sama
    dipakai create_service_request - sudah teruji, staf sudah pantau halamannya & dapat
    alert push/Telegram otomatis) supaya staf tahu & siap menyambut begitu tamu sampai -
    BUKAN bikin kolom/status/halaman baru dari nol, biar "benar-benar tercatat" reuse
    jalur yang sudah terbukti reliable, bukan sistem paralel yang belum teruji."""
    whatsapp = args.get("whatsapp") or conv.get("whatsapp") or ""
    guest_name = args.get("guest_name") or conv.get("guest_name") or ""
    room_nomor = (args.get("room_nomor") or "").strip()
    catatan = (args.get("catatan") or "").strip()

    # Perkaya deskripsi tiket dgn konteks booking asli (kode/tipe kamar/tanggal) kalau
    # ketemu, supaya staf langsung tau siapa & booking yang mana tanpa buka PMS dulu -
    # gagal lookup TIDAK menghalangi tiket tetap dibuat (tamu bisa saja belum booking
    # lewat sistem, atau lookup gagal krn nomor beda) - staf tetap diberi tahu apa adanya.
    konteks_booking = ""
    try:
        status = await _pms_status_booking(whatsapp, api_key_override=conv.get("_pms_api_key_override"))
        if status.get("ok") and status.get("permintaan"):
            item = status["permintaan"][0]
            ringkasan = (item.get("booking_ringkasan") or [{}])[0]
            konteks_booking = (
                f" Booking: {ringkasan.get('kode', item.get('kode', '-'))}, "
                f"{item.get('room_tipe', '-')}, check-in {item.get('tanggal_checkin', '-')}."
            )
    except Exception:
        pass

    deskripsi = f"🛎️ Tamu mengabarkan sudah tiba/dalam perjalanan ke properti.{konteks_booking}"
    if catatan:
        deskripsi += f" Catatan tamu: {catatan}"

    hasil = await _pms_buat_tiket("service_request", deskripsi, whatsapp, guest_name, room_nomor,
                                  api_key_override=conv.get("_pms_api_key_override"))
    if not hasil.get("ok"):
        return {"ok": False, "tool": "catat_kedatangan_tamu", "error": hasil.get("error")}
    tiket = hasil.get("tiket") or {}
    return {"ok": True, "tool": "catat_kedatangan_tamu", "tiket_id": tiket.get("id")}


@register_tool("catat_klaim_stamp_member", {"member_stamp_claim"})
async def _tool_catat_klaim_stamp_member(args: dict, conv: dict) -> dict:
    """Migrasi kartu member fisik ke pencatatan digital (2026-08-01, permintaan Agus) -
    banyak tamu member Pelangi masih pakai kartu stamp fisik yang sering TIDAK cocok
    dengan "Total Kunjungan" yang tercatat sistem (mis. kunjungan lama sebelum sistem ini
    ada). AI menanyakan & mencatat KLAIM tamu soal jumlah stamp kartunya - TAPI SENGAJA
    TIDAK LANGSUNG mengubah data resmi (total_kunjungan) yang menentukan diskon member
    (sampai 100% di kedatangan ke-10, risiko fraud nyata kalau dipercaya mentah-mentah
    tanpa verifikasi). Sama prinsipnya dengan create_booking/cancel_booking/
    catat_kedatangan_tamu - tool ini cuma membuat TIKET untuk staf verifikasi & proses
    manual (cek kartu fisik cocok/tidak saat tamu check-in, baru update Total Kunjungan
    di halaman Data Tamu kalau sesuai, sekalian ambil/simpan kartu fisiknya sesuai arahan
    Agus - "penggunaan kartu member mulai dikurangi, mulai dicatat otomatis" MELALUI
    proses verifikasi staf ini, bukan AI main percaya omongan tamu begitu saja)."""
    whatsapp = args.get("whatsapp") or conv.get("whatsapp") or ""
    guest_name = args.get("guest_name") or conv.get("guest_name") or ""
    try:
        jumlah_stamp = int(args.get("jumlah_stamp"))
    except (TypeError, ValueError):
        return {"ok": False, "tool": "catat_klaim_stamp_member", "error": "jumlah_stamp wajib angka"}
    if jumlah_stamp < 0 or jumlah_stamp > 9:
        return {"ok": False, "tool": "catat_klaim_stamp_member",
                 "error": "jumlah_stamp di luar rentang wajar (0-9, siklus kartu 10 stamp) - tanya ulang ke tamu, mungkin salah dengar/ketik"}
    deskripsi = (
        f"🎫 Tamu KLAIM kartu member fisik sudah stamp ke-{jumlah_stamp} (berarti kedatangan "
        f"ke-{jumlah_stamp + 1} kalau benar). PERLU VERIFIKASI STAF - klaim ini BELUM diverifikasi "
        f"kartu fisik, JANGAN langsung dipakai sebagai diskon final. Saat tamu check-in: (1) cek "
        f"kartu fisiknya, cocokkan jumlah stamp asli dengan klaim ini, (2) kalau cocok, update "
        f"'Total Kunjungan' tamu ini di halaman Data Tamu jadi {jumlah_stamp}, (3) ambil & simpan "
        f"kartu fisiknya (program migrasi ke pencatatan otomatis, sesuai arahan Agus)."
    )
    hasil = await _pms_buat_tiket("service_request", deskripsi, whatsapp, guest_name, "",
                                  api_key_override=conv.get("_pms_api_key_override"))
    if not hasil.get("ok"):
        return {"ok": False, "tool": "catat_klaim_stamp_member", "error": hasil.get("error")}
    tiket = hasil.get("tiket") or {}
    return {"ok": True, "tool": "catat_klaim_stamp_member", "tiket_id": tiket.get("id"),
            "klaim_kedatangan_ke": jumlah_stamp + 1}


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
    property_slug = conv.get("_property_slug") or "pelangi"
    key = _guest_profile_key(wa, property_slug)
    existing = await db.guest_profiles.find_one({"_id": key})
    facts = (existing or {}).get("preferensi") or []
    if fact not in facts:  # cegah duplikat kalau AI menyimpan hal yang sama berkali-kali
        facts.append(fact)
        facts = facts[-20:]  # cap wajar per tamu, fakta terlama otomatis terbuang
    await db.guest_profiles.update_one(
        {"_id": key},
        {"$set": {"preferensi": facts, "whatsapp": _normalize_phone(wa), "property_slug": property_slug},
         "$setOnInsert": {"created_at": utc_now_iso()}},
        upsert=True,
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
    "fon-{bot_id}-..." utk Fonnte, sisanya "wa-{waha_session}-..." utk WAHA) BUKAN cuma
    field `channel`/`cloud_phone_number_id`, karena field itu baru mulai diisi
    2026-07-21 - percakapan yg dibuat sebelum tanggal itu (channel cuma "whatsapp"
    generik, cloud_phone_number_id None) tetap harus ke-detect benar lewat
    session_id-nya, bukan diam-diam jatuh ke WAHA.

    Fonnte (2026-07-31) - `identifier` utk channel ini adalah TOKEN device Fonnte
    (bukan cuma ID publik spt phone_number_id Cloud API), krn tiap device Fonnte punya
    token sendiri-sendiri - disimpan di `conv["fonnte_token"]` saat percakapan dibuat
    (lihat _run_chat_turn), BUKAN dibaca ulang dari ai_bots di sini supaya fungsi ini
    tetap sync/tidak perlu query DB."""
    sid = conv.get("session_id") or ""
    if sid.startswith("wac-"):
        parts = sid.split("-")
        phone_number_id = conv.get("cloud_phone_number_id") or (parts[1] if len(parts) > 1 else None)
        return "whatsapp_cloud", phone_number_id
    if sid.startswith("fon-"):
        return "fonnte", conv.get("fonnte_token")
    # WAHA dihapus (2026-08-01, digantikan Fonnte) - sisa percakapan lama dari sebelum
    # migrasi (session_id tanpa prefix "wac-"/"fon-") tidak bisa dibalas otomatis lagi,
    # channel "whatsapp_legacy" ini cuma penanda supaya caller tahu utk gagal dgn baik
    # (bukan diam-diam salah kirim ke channel lain) - riwayatnya tetap bisa dibaca staf
    # di dashboard, cuma tidak bisa dikirimi balasan baru lewat jalur otomatis.
    return "whatsapp_legacy", conv.get("waha_session")


async def _channel_info_from_property_slug(property_slug: Optional[str]) -> Optional[tuple[str, Optional[str]]]:
    """Fallback channel resolution dari `property_slug` (2026-07-31, bug nyata ditemukan
    lewat audit alur booking/payment/invoice) - dipakai `_send_wa_transactional`/
    `_send_wa_document_smart` KHUSUS kalau tamu belum pernah punya percakapan AI sama
    sekali (jadi `_channel_info_from_conv` tidak bisa dipakai). Sebelumnya kasus ini SELALU
    hardcode `whatsapp_cloud` + nomor Pelangi - salah utk tamu Harmoni (device Fonnte
    sendiri), DAN ternyata juga sudah basi utk Pelangi sendiri (bot "Admin pelangi"
    sekarang channel_type-nya "fonnte", bukan Cloud API lagi). None kalau tidak ada bot
    non-simulator utk property_slug ini - caller WAJIB tetap punya fallback lama sendiri
    supaya tidak pernah gagal total kalau data ai_bots belum lengkap/salah setting."""
    if not property_slug:
        return None
    bot = await db.ai_bots.find_one({"property_slug": property_slug, "channel_type": {"$nin": [None, "", "simulator"]}})
    if not bot or not bot.get("channel_type"):
        return None
    return bot["channel_type"], bot.get("channel_id")


async def _send_wa_smart(conv: dict, text: str) -> bool:
    """Kirim pesan ke tamu WhatsApp lewat channel yang SAMA dengan yang dipakai tamu itu
    ngobrol (WAHA, Cloud API, atau Fonnte), lihat `_channel_info_from_conv`. Dipakai baik
    untuk balasan manual staf (human handover) maupun relay notifikasi dari PMS
    (/send-message, /send-document) - sebelum ada fungsi ini, KEDUANYA selalu hardcode ke
    WAHA meski tamunya chat lewat Cloud API (ditemukan 2026-07-21 lewat laporan user:
    voucher booking gagal terkirim krn WAHA session down, padahal tamu itu chat via Cloud
    API yang sehat)."""
    if not conv.get("whatsapp"):
        return False
    channel, identifier = _channel_info_from_conv(conv)
    if channel == "whatsapp_cloud":
        return await _wa_cloud_send_text(conv["whatsapp"], text, phone_number_id=identifier or "")
    if channel == "fonnte":
        return await _fonnte_send_text(identifier or "", conv["whatsapp"], text)
    # "whatsapp_legacy" - percakapan dari sebelum migrasi Fonnte, WAHA sudah dihapus,
    # tidak ada jalur otomatis lagi utk kirim balasan ke sini (lihat _channel_info_from_conv).
    logging.getLogger("send_wa").warning(
        f"Tidak bisa kirim balasan - percakapan {conv.get('_id')} pakai channel lama (WAHA, "
        f"sudah dihapus) yang tidak lagi didukung. Hubungi tamu manual kalau perlu."
    )
    return False


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
                                  template_name: Optional[str] = None, template_params: Optional[List[str]] = None,
                                  property_slug: Optional[str] = None) -> bool:
    """Kirim notifikasi TRANSAKSIONAL yang PMS picu sendiri (approve booking, pembatalan,
    kamar siap, dst) - BEDA dari `_send_wa_smart` (balasan chat langsung/staf): fungsi ini
    sadar jendela layanan 24 jam Meta (2026-07-26, ditemukan lewat audit - 8 titik notifikasi
    proaktif sebelumnya selalu kirim teks bebas & hasil gagalnya tidak pernah dicek; teks
    bebas Cloud API ditolak Meta kalau di luar jendela 24 jam sejak pesan terakhir tamu).
    Kalau di luar jendela (atau tidak ada percakapan WA sama sekali, mis. tamu batalkan
    lewat web publik bukan WA), WAJIB pakai Message Template yang sudah disetujui Meta -
    `template_name`/`template_params` optional supaya pemanggil lama tanpa template masih
    jalan (nyoba teks bebas dulu, WAHA tidak kena aturan ini sama sekali).
    `property_slug` (2026-07-31) - dipakai HANYA kalau `conv` kosong (tamu belum pernah
    chat AI) supaya channel-nya di-resolve dari bot properti yang benar (lihat
    `_channel_info_from_property_slug`), bukan hardcode Cloud API nomor Pelangi yang
    ternyata salah utk Harmoni (device Fonnte sendiri) DAN sudah basi jg utk Pelangi
    sendiri (bot Pelangi sekarang jg Fonnte)."""
    whatsapp = (conv or {}).get("whatsapp") or whatsapp_fallback
    if not whatsapp:
        return False

    if conv:
        channel, identifier = _channel_info_from_conv(conv)
    else:
        resolved = await _channel_info_from_property_slug(property_slug)
        channel, identifier = resolved if resolved else ("whatsapp_cloud", WHATSAPP_CLOUD_PHONE_NUMBER_ID)

    if channel == "fonnte":
        # Fonnte - bukan API resmi Meta, tidak ada pembatasan jendela 24 jam.
        return await _fonnte_send_text(identifier or "", whatsapp, text)

    if channel == "whatsapp_legacy":
        # Percakapan lama sebelum migrasi Fonnte - WAHA sudah dihapus (2026-08-01), tidak
        # ada jalur otomatis lagi. Lihat _channel_info_from_conv.
        logging.getLogger("send_wa").warning(
            f"Tidak bisa kirim notifikasi transaksional - whatsapp {whatsapp} pakai channel "
            f"lama (WAHA, sudah dihapus). Hubungi tamu manual kalau perlu."
        )
        return False

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


async def _send_wa_document_smart(conv: Optional[dict], filename: str, mimetype: str, data_base64: str, caption: str = "",
                                   url: str = "", whatsapp_fallback: Optional[str] = None,
                                   property_slug: Optional[str] = None) -> bool:
    """Sibling dokumen dari `_send_wa_smart` - sama polanya, dipakai relay /send-document.

    `url` (2026-07-31, Fonnte) - paket Fonnte yang dipakai Agus TIDAK support attachment
    sama sekali (lihat connectors/fonnte_connector.py), jadi utk channel ini dokumen
    dikirim sbg LINK di teks biasa, bukan attachment - caller (routes/pesan_whatsapp.py
    di PMS) WAJIB kirim `url` publik ke dokumennya kalau ingin dokumen itu sampai lewat
    Fonnte (base64 saja tidak cukup, beda dari whatsapp_cloud/WAHA yang upload
    langsung dari base64).
    `whatsapp_fallback`/`property_slug` (2026-07-31) - sama pola dgn `_send_wa_transactional`:
    kalau tamu/staf ini belum pernah punya percakapan AI (`conv` None, mis. voucher
    tamu baru atau slip gaji staf yang belum pernah chat), channel di-resolve dari bot
    properti yang benar via `property_slug` - BUKAN lagi hardcode Cloud API nomor Pelangi
    (bug nyata: salah utk Harmoni, dan sudah basi jg utk Pelangi sendiri sejak bot Pelangi
    pindah ke Fonnte)."""
    whatsapp = (conv or {}).get("whatsapp") or whatsapp_fallback
    if not whatsapp:
        return False
    if conv:
        channel, identifier = _channel_info_from_conv(conv)
    else:
        resolved = await _channel_info_from_property_slug(property_slug)
        channel, identifier = resolved if resolved else ("whatsapp_cloud", WHATSAPP_CLOUD_PHONE_NUMBER_ID)
    if channel == "whatsapp_cloud":
        return await _wa_cloud_send_document(whatsapp, filename, data_base64, caption, phone_number_id=identifier or "")
    if channel == "fonnte":
        if not url:
            logging.getLogger("fonnte").warning(f"Dokumen '{filename}' tidak ada url publik - tidak bisa dikirim lewat Fonnte (paket tanpa attachment)")
            return False
        return await _fonnte_send_link_message(identifier or "", whatsapp, caption, url, label="Unduh dokumen")
    # "whatsapp_legacy" - percakapan lama sebelum migrasi Fonnte, WAHA sudah dihapus
    # (2026-08-01), tidak ada jalur otomatis lagi. Lihat _channel_info_from_conv.
    logging.getLogger("send_wa").warning(
        f"Tidak bisa kirim dokumen '{filename}' - whatsapp {whatsapp} pakai channel lama "
        f"(WAHA, sudah dihapus). Hubungi tamu manual kalau perlu."
    )
    return False


# Model Router (2026-07-31, permintaan Agus) - gpt-4.1-mini tetap jadi model UTAMA/hemat
# biaya, TAPI dieskalasi ke gpt-4.1 (5x lebih mahal per token, tapi jauh lebih taat
# instruksi) HANYA utk giliran chat yang topiknya rawan salah - bukti nyata lewat tes live
# 2026-07-31: gpt-4.1-mini sempat mengarang "biasanya jam checkin Day Use 14:00" padahal
# instruksi eksplisit di TOOL_DOCS melarang menebak jam default itu sama sekali. Klasifikasi
# MURNI keyword + status tool giliran sebelumnya - SENGAJA TIDAK pakai panggilan LLM
# tambahan utk mengklasifikasi (itu justru menambah biaya/latensi, melawan tujuan
# "menekan biaya" itu sendiri) - kalau nanti Agus laporkan pola kesalahan topik baru,
# cukup tambahkan kata kuncinya di bawah, tidak perlu ubah arsitektur.
MODEL_ESCALATION_KEYWORDS = [
    # Jam/waktu spesifik - kasus nyata yang memicu perbaikan ini (jam check-in Day Use
    # yang sebenarnya fleksibel, tapi model kadang menebak jam tetap yang salah).
    "jam berapa", "jam check", "checkin jam", "check-in jam", "bebas jam", "jam bebas",
    "jam fleksibel", "jam segini", "checkout jam",
    # Kebijakan & uang - salah di sini langsung berdampak biaya/kepercayaan tamu.
    "batal", "pembatalan", "refund", "dikembalikan", "pengembalian", "diskon", "dp ",
    "deposit", "kebijakan", "denda", "biaya tambahan", "kena charge",
    # Fasilitas yang beda per properti (Pelangi ada sarapan/AC, Harmoni tidak) - rawan
    # model "mengingat" fasilitas properti lain dari pengetahuan umum.
    "sarapan", "breakfast", "extra bed", "tambahan kasur", " ac ", "pendingin",
]


def _perlu_model_kuat(message: str, last_intent: Optional[str]) -> bool:
    lower = f" {(message or '').lower()} "
    if any(kw in lower for kw in MODEL_ESCALATION_KEYWORDS):
        return True
    # Giliran sebelumnya baru saja panggil tool bertaruh tinggi (data booking/pembayaran
    # asli, atau status member yang menentukan diskon) - besar kemungkinan masih di
    # tengah alur yang sama, butuh ketelitian tinggi jg di giliran-giliran berikutnya.
    if last_intent in ("create_booking", "cancel_booking", "check_member_status"):
        return True
    return False


async def _run_chat_turn(
    session_id: str, message: str, guest_name: Optional[str], whatsapp: Optional[str],
    bot_id: Optional[str], bot_code: Optional[str], channel: str = "simulator",
    waha_session: Optional[str] = None, cloud_phone_number_id: Optional[str] = None,
    fonnte_token: Optional[str] = None,
) -> dict:
    """Wrapper penguncian (2026-08-01) - lihat `_get_conversation_lock`. Memastikan HANYA
    1 giliran chat per session_id yang benar-benar diproses dalam satu waktu, giliran
    kedua yang datang saat giliran pertama masih jalan akan MENUNGGU (bukan langsung
    diproses paralel dengan state percakapan yang basi) - mencegah booking duplikat kalau
    tamu kirim 2 pesan cepat berturut-turut. Isi asli fungsi ini ada di
    `_run_chat_turn_locked` di bawah."""
    async with _get_conversation_lock(session_id):
        return await _run_chat_turn_locked(
            session_id, message, guest_name, whatsapp, bot_id, bot_code, channel,
            waha_session, cloud_phone_number_id, fonnte_token,
        )


async def _run_chat_turn_locked(
    session_id: str, message: str, guest_name: Optional[str], whatsapp: Optional[str],
    bot_id: Optional[str], bot_code: Optional[str], channel: str = "simulator",
    waha_session: Optional[str] = None, cloud_phone_number_id: Optional[str] = None,
    fonnte_token: Optional[str] = None,
) -> dict:
    """Inti alur 1 giliran chat (load bot, build context, panggil AI, tool-calling,
    simpan percakapan) — dipakai `/chat/message` (simulator, staf login) DAN webhook WAHA
    (`/webhook/waha`, tamu WhatsApp asli) supaya tidak ada logika AI ganda yang bisa
    saling menyimpang antara jalur uji coba staf dan jalur tamu sungguhan.

    `waha_session` = nomor WA (session WAHA) mana yang menerima pesan ini - disimpan di
    percakapan supaya balasan staf manual (human handover, bisa terjadi jauh setelah
    webhook request ini selesai) tetap keluar lewat nomor yang SAMA dengan yang tamu
    hubungi, bukan selalu nomor default (2026-07-19, multi-nomor WA per AI bot).

    `fonnte_token` (2026-07-31) - token device Fonnte mana yang menerima pesan ini,
    sama alasannya dgn waha_session/cloud_phone_number_id di atas - disimpan supaya
    balasan (langsung maupun lewat human handover nanti) keluar lewat device Fonnte
    yang SAMA, bukan device/bot lain (tiap device Fonnte = token terpisah, beda dgn
    Cloud API yang 1 access token dipakai bareng banyak nomor)."""
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
            "fonnte_token": fonnte_token,
            "messages": [],
            "status": "active",
            "resolution": "unresolved",
            "booking_created": False,
            "last_intent": None,
            "booking_draft": {},
            "response_time_ms": 0,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }
        await db.conversations.insert_one(conv)

    # Bot & property_slug dimuat DULUAN (sebelum touch_guest_profile) - profil tamu
    # (nama/preferensi/jumlah kunjungan) di-scope per properti sejak 2026-07-31, lihat
    # `_guest_profile_key` - butuh property_slug bot ini sebelum menyentuh profil.
    bot = await _load_bot(bot_id, bot_code)
    property_slug = (bot or {}).get("property_slug") or "pelangi"
    conv["_property_slug"] = property_slug

    await _touch_guest_profile(conv.get("whatsapp") or whatsapp, conv.get("guest_name") or guest_name, is_new_conversation, property_slug)

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

    # Build dynamic prompt (bot sudah dimuat di atas, sebelum touch_guest_profile)
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
    menu_now = await _pms_menu(api_key_override=conv["_pms_api_key_override"])
    timeline_kamar_now = await _pms_timeline_kamar(api_key_override=conv["_pms_api_key_override"])
    room_types = sorted({r["tipe"] for r in rooms_now if r.get("tipe")})
    system_prompt = await _system_prompt_for(bot, room_types=room_types)
    context = await _build_context(query=message, bot=bot, whatsapp=conv.get("whatsapp") or whatsapp, rooms=rooms_now, menu=menu_now, timeline_kamar=timeline_kamar_now)
    # Slot memory (2026-08-01) - suntik apa yang SUDAH diketahui dari booking_draft (lihat
    # dispatch tool di bawah) supaya AI tidak menanyakan ulang field yang tamu sudah jawab
    # di giliran sebelumnya - beda dari mengandalkan model menyimpulkan sendiri dari
    # riwayat chat mentah tiap kali (rawan lupa/re-ask, laporan Agus).
    draft = conv.get("booking_draft") or {}
    if draft:
        label = {
            "tipe": "Menginap/Day Use", "room_tipe": "Tipe kamar",
            "tanggal_checkin": "Tanggal check-in", "tanggal_checkout": "Tanggal check-out",
            "jumlah_kamar": "Jumlah kamar", "jumlah_tamu": "Jumlah tamu",
            "jam_checkin": "Jam check-in (Day Use)", "payment_option": "Metode bayar (dp50/full)",
        }
        known = " | ".join(f"{label[k]}: {v}" for k, v in draft.items() if k in label)
        missing = [label[k] for k in ("tipe", "room_tipe", "tanggal_checkin", "jumlah_kamar")
                   if k not in draft or not draft.get(k)]
        context += (
            "\n\n# DATA BOOKING YANG SUDAH DIKETAHUI (jangan tanya ulang)\n"
            f"{known}\n"
            + (f"Yang BELUM diketahui: {', '.join(missing)}\n" if missing else "Semua data wajib sudah lengkap - lanjut ke ringkasan/konfirmasi.\n")
        )
    history_text = compact_history(conv["messages"][:-1], max_turns=12)

    settings_doc = await db.settings.find_one({"_id": "singleton"}) or {}
    llm_provider = settings_doc.get("llm_provider") or DEFAULT_PROVIDER
    llm_model_utama = settings_doc.get("llm_model") or DEFAULT_MODEL
    llm_model_eskalasi = settings_doc.get("llm_model_eskalasi") or "gpt-4.1"
    llm_model = llm_model_eskalasi if _perlu_model_kuat(message, conv.get("last_intent")) else llm_model_utama

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
            # Slot memory (2026-08-01, permintaan Agus - PRD Natural Conversation Engine
            # §13/14) - simpan info booking yang tamu SUDAH berikan (dari args tool, bukan
            # dari inferensi ulang teks chat tiap giliran) supaya AI tidak menanyakan ulang
            # field yang sudah dijawab. Diisi dari args APAPUN hasilnya (sukses/gagal - args
            # tetap mencerminkan apa yang tamu bilang), dikosongkan lagi setelah
            # create_booking BENAR-BENAR sukses (booking_created=True) karena setelah itu
            # status sebenarnya lebih baik dibaca live dari lookup_booking, bukan draft basi.
            if tool in ("preview_booking", "create_booking") and args:
                draft_keys = ("tipe", "room_tipe", "tanggal_checkin", "tanggal_checkout",
                              "jumlah_kamar", "jumlah_tamu", "jam_checkin", "payment_option")
                draft = dict(conv.get("booking_draft") or {})
                draft.update({k: args[k] for k in draft_keys if args.get(k)})
                conv["booking_draft"] = draft
            if tool == "create_booking" and tool_result and tool_result.get("ok"):
                conv["booking_draft"] = {}
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
                f"langkah berikutnya - JANGAN klaim sudah selesai. Jangan panggil tool lagi kecuali tamu memintanya. "
                f"Balasan ini akan digabung dengan draftmu sebelum tool dipanggil (kalau ada) menjadi SATU balasan akhir - "
                f"kalau draft sebelumnya cuma basa-basi menunggu (mis. 'saya cek dulu ya'), JANGAN ulangi/gemakan kalimat "
                f"itu di sini, langsung tulis hasil final secara utuh seolah ini satu-satunya balasan ke tamu. LEBIH PENTING "
                f"LAGI: kalau draft sebelumnya SUDAH TERLANJUR menebak status/detail (mis. 'belum lunas'/'saya kirim ulang "
                f"link' padahal belum tahu hasil tool) dan tebakan itu BEDA dari hasil tool yang sebenarnya di atas, jangan "
                f"cuma menambahkan koreksi setelahnya - balasan akhirmu harus MENGGANTIKAN tebakan yang salah itu sepenuhnya "
                f"dengan fakta dari hasil tool, supaya tamu tidak melihat 2 klaim yang saling bertentangan dalam 1 pesan."
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

    # Jaring pengaman level KODE (2026-07-28) - ditemukan lewat pengujian nyata (bukan
    # laporan user - diuji proaktif krn user tanya "apakah diskon member benar-benar
    # akurat"): model kadang menulis baris "Total"/"Harga kamar" TANPA angka Rupiah asli
    # (placeholder spt "(akan dihitung otomatis)") krn ternyata TIDAK benar-benar memanggil
    # preview_booking giliran ini (tool None), walau kedatangan_ke/diskon_persen di
    # KALIMAT NARATIF sebelumnya sudah benar (itu dari check_member_status yang memang
    # dipanggil, tool TERPISAH dari preview_booking - beda tool, jangan disamakan).
    # SAMA seperti catatan tim sebelumnya soal kasus mirip (lihat komentar service-fee di
    # atas): TIDAK ADA cara aman auto-panggil preview_booking di sini (perlu tipe kamar/
    # tanggal terstruktur yang belum tentu lengkap/valid) - beda dari kasus itu, di sini
    # model TIDAK mengarang angka salah (lebih aman drpd itu), cuma ringkasan jadi tidak
    # lengkap/tidak profesional. TIDAK ditimpa (tidak ada angka benar utk gantinya) -
    # cukup alert staf supaya sadar ada tamu yang mungkin bingung & perlu ditindaklanjuti
    # manual, konsisten dgn prinsip "hanya auto-koreksi yang aman, sisanya alert" yang
    # sudah dipakai di jaring pengaman lain.
    if tool != "preview_booking" and re.search(
        r"(total|harga\s+kamar)\s*:?\s*\(?\s*(akan\s+dihitung|menyusul|otomatis|tbd|-{2,})",
        final_text, re.IGNORECASE,
    ):
        logging.getLogger("hallucination_guard").warning(
            f"ringkasan harga placeholder (bukan angka asli) terdeteksi, preview_booking "
            f"TIDAK dipanggil giliran ini - conv {conv.get('_id')}, wa {conv.get('whatsapp') or whatsapp}, "
            f"teks: {final_text!r}"
        )
        try:
            await _pms_alert_owner(
                f"⚠️ AI menulis ringkasan harga TANPA angka asli (placeholder spt 'akan dihitung "
                f"otomatis') - tamu {conv.get('whatsapp') or whatsapp} mungkin bingung, tolong cek "
                f"percakapan & tindak lanjuti manual kalau perlu."
            )
        except Exception:
            pass

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
    # False-positive nyata ditemukan 2026-08-01 (laporan Agus: chat berakhir dengan koreksi
    # extra bed yang tidak nyambung sama sekali) - kondisi lama cuma cek "extra bed" TANPA
    # "cottage", jadi kalimat SAH seperti "mau tambah layanan (extra bed, handuk, antar-
    # jemput)?" (sekadar daftar layanan opsional, TIDAK ada klaim kelayakan apa pun) ikut
    # kena & DIGANTIKAN TOTAL oleh koreksi generik yang tidak relevan dgn pesan tamu,
    # plus alert Telegram palsu "AI mengarang kebijakan" padahal tidak ada yang salah.
    # Ditambah syarat "standard" HARUS ikut disebut - hanya scenario asli (model
    # menyetujui/menjelaskan extra bed UNTUK kamar Standard secara spesifik) yang punya
    # kombinasi ini; sekadar menyebut "extra bed" di daftar layanan tanpa nama tipe kamar
    # sama sekali tidak lagi ke-trigger.
    if (re.search(r"extra\s*bed", final_text, re.IGNORECASE) and "cottage" not in final_text.lower()
            and re.search(r"\bstandard\b", final_text, re.IGNORECASE)):
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
        # (2026-07-31) model AI yang benar-benar dipakai giliran ini - dipakai memantau
        # seberapa sering Model Router mengeskalasi ke gpt-4.1 (lihat _perlu_model_kuat),
        # supaya nanti bisa dicek biaya riil vs perkiraan, bukan kotak hitam.
        "llm_model_used": llm_model,
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
    update["booking_draft"] = conv.get("booking_draft") or {}
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
async def guest_profiles_list(search: Optional[str] = None, property_slug: Optional[str] = None,
                               limit: int = Query(100, le=500), user=Depends(get_current_user)):
    """Memory tahap 1 - profil tamu lintas-percakapan (nama, preferensi/fakta yang diingat
    AI, jumlah kunjungan). Read-only dari dashboard - AI yang mengisi lewat tool
    remember_guest_fact + pembaruan otomatis tiap giliran chat, staf cukup melihat.
    `_id` sekarang "{nomor}:{property_slug}" (lihat `_guest_profile_key`) - `whatsapp`/
    `property_slug` disimpan sebagai field asli, JANGAN derive dari `_id` lagi seperti
    kode lama (bakal ikut nempel suffix property_slug ke nomor HP yang ditampilkan)."""
    q: Dict[str, Any] = {}
    if search:
        q["$or"] = [
            {"whatsapp": {"$regex": re.escape(search)}},
            {"nama": {"$regex": re.escape(search), "$options": "i"}},
        ]
    if property_slug:
        q["property_slug"] = property_slug
    docs = await db.guest_profiles.find(q).sort("last_seen_at", -1).to_list(limit)
    out = []
    for d in docs:
        d["id"] = d.pop("_id")
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
    # (2026-07-31) properti mana notifikasi ini tentang (mis. "pelangi"/"harmoni") - dipakai
    # fallback channel resolution kalau nomor ini belum pernah punya percakapan AI, lihat
    # _channel_info_from_property_slug. Opsional supaya caller lama tanpa field ini tetap jalan
    # (fallback ke Cloud API Pelangi seperti sebelumnya).
    property_slug: Optional[str] = None


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
                                       template_name=body.template_name, template_params=body.template_params,
                                       property_slug=body.property_slug)
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
    # (2026-07-31, Fonnte) URL publik ke dokumen yang sama - WAJIB diisi caller (lihat
    # email_service.py di repo PMS) supaya dokumen bisa dikirim lewat Fonnte (paket
    # Agus tidak support attachment sama sekali) sbg link, bukan attachment base64.
    url: str = ""
    # (2026-07-31) sama alasannya dgn SendMessageIn.property_slug - lihat catatan di sana.
    property_slug: Optional[str] = None


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
    # Bug nyata ditemukan & diperbaiki (2026-07-31, audit alur booking/payment/invoice):
    # cabang "tidak ada percakapan" sebelumnya SELALU hardcode Cloud API nomor Pelangi
    # langsung (bypass _send_wa_document_smart sepenuhnya) - salah utk dokumen (voucher/
    # slip gaji) properti Harmoni (device Fonnte sendiri), dan basi jg utk Pelangi sendiri
    # (bot Pelangi sekarang Fonnte, bukan Cloud API lagi). Sekarang selalu lewat
    # _send_wa_document_smart yang resolve channel dari property_slug kalau conv kosong.
    conv = await db.conversations.find_one({"whatsapp": digits}, sort=[("updated_at", -1)])
    ok = await _send_wa_document_smart(conv, body.filename, body.mimetype, body.data_base64, body.caption,
                                       url=body.url, whatsapp_fallback=digits, property_slug=body.property_slug)
    await _pms_log("/send-document", "POST", 200 if ok else 502, 0, ok, f"to {digits}")
    if ok:
        catatan = f"📎 Dokumen dikirim: {body.filename}" + (f" — {body.caption}" if body.caption else "")
        await _catat_pesan_sistem(conv, digits, catatan)
    if not ok:
        raise HTTPException(502, "Gagal mengirim dokumen lewat WhatsApp")
    return {"ok": True}


WHATSAPP_CLOUD_VERIFY_TOKEN = os.environ.get("WHATSAPP_CLOUD_VERIFY_TOKEN", "")
WHATSAPP_CLOUD_APP_SECRET = os.environ.get("WHATSAPP_CLOUD_APP_SECRET", "")


def _verifikasi_signature_meta(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """Verifikasi X-Hub-Signature-256 yang Meta kirim di tiap webhook POST asli (HMAC-SHA256
    dari raw body pakai App Secret) - TANPA ini, siapa saja yang tahu URL webhook bisa kirim
    payload palsu yang diproses seolah pesan tamu sungguhan, termasuk memicu AI memanggil
    create_booking/cancel_booking dengan data karangan (2026-07-27, temuan audit keamanan).
    Kalau WHATSAPP_CLOUD_APP_SECRET belum diisi, loloskan dulu (tidak block migrasi awal yang
    sedang berjalan) tapi catat warning supaya ketahuan kalau lupa diisi."""
    if not WHATSAPP_CLOUD_APP_SECRET:
        logging.getLogger("whatsapp_cloud").warning(
            "WHATSAPP_CLOUD_APP_SECRET belum diisi - webhook TIDAK diverifikasi tanda tangannya!"
        )
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(WHATSAPP_CLOUD_APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header[len("sha256="):], expected)


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
    raw_body = await request.body()
    if not _verifikasi_signature_meta(raw_body, request.headers.get("X-Hub-Signature-256")):
        logging.getLogger("whatsapp_cloud").warning("Webhook ditolak - tanda tangan X-Hub-Signature-256 tidak cocok/tidak ada")
        raise HTTPException(403, "Invalid signature")
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
                # Caption bernomor urut (2026-08-01, permintaan Agus - PRD Natural
                # Conversation Engine §6) - "Foto 1/2/3" per foto, BUKAN label sudut/ruangan
                # spesifik ("Foto Depan"/"Kamar Mandi") karena data foto (db.rooms.images)
                # tidak menyimpan info sudut/ruangan sama sekali - menebaknya = mengarang.
                caption = f"{room['name']} - Foto {i + 1}" if room else f"Foto {i + 1}"
                await _wa_cloud_send_image(phone, url, caption, phone_number_id=phone_number_id)
        return {"ok": True}
    except Exception as e:
        logging.getLogger("whatsapp_cloud").warning(f"Gagal proses webhook Cloud API: {e}")
        return {"ok": True}


async def _fonnte_process_and_reply(session_id: str, message_text: str, guest_name: str, sender: str,
                                     bot_id: str, bot_code: Optional[str], token: str) -> None:
    """Proses 1 giliran chat & kirim balasannya lewat Fonnte - isi asli
    fonnte_webhook_receive, dipisah jadi fungsi sendiri (2026-08-01) supaya bisa dipanggil
    dari _fonnte_debounced_dispatch dengan `message_text` yang sudah digabung dari
    beberapa pesan tamu yang datang beruntun, bukan cuma 1 pesan mentah."""
    hasil = await _run_chat_turn(
        session_id, message_text, guest_name, sender, bot_id, bot_code,
        channel="fonnte", fonnte_token=token,
    )
    if hasil.get("reply"):
        # Jeda 3-5 detik "human-paced" SEBELUM kirim DIHAPUS di sini (2026-08-01) - bug
        # nyata ditemukan lewat laporan Agus: begitu create_booking sukses, PMS mengirim
        # notifikasi link pembayaran SENDIRI hampir seketika (lewat _kirim_dengan_alert,
        # jalur terpisah, tidak kena debounce/jeda apa pun) - balasan konfirmasi AI sendiri
        # (yang menjelaskan ringkasan & bilang "link menyusul") yang SEHARUSNYA sampai
        # LEBIH DULU malah tertunda ekstra 3-5 detik di atas waktu proses giliran ter-
        # debounce (lihat _fonnte_debounced_dispatch, sudah ada jeda alami dari situ) -
        # hasilnya tamu terima link duluan, ringkasan & penjelasan DP menyusul belakangan,
        # urutannya kebalik & membingungkan. Debounce+proses LLM sudah cukup memberi jeda
        # alami, jeda tambahan ini sekarang cuma bikin balapan lawan notifikasi PMS.
        clean_text, image_urls = parse_img_markers(hasil["reply"])
        if clean_text:
            terkirim = await _fonnte_send_text(token, sender, clean_text)
            if not terkirim:
                logging.getLogger("fonnte").error(
                    f"Gagal kirim balasan WA ke {sender} (conv session {session_id}) via Fonnte - "
                    f"cek status device di dashboard Fonnte (bisa jadi disconnected)."
                )
                try:
                    await _pms_alert_owner(
                        f"⚠️ AI GAGAL kirim balasan WhatsApp (Fonnte) ke tamu {sender} - "
                        f"cek status device Fonnte, kemungkinan disconnected. "
                        f"Hubungi tamu manual kalau perlu."
                    )
                except Exception:
                    pass
        # Fonnte (paket Agus) tidak support attachment sama sekali - foto kamar
        # ([[IMG: url]] marker) dikirim sbg LINK teks, bukan gambar native (beda dgn
        # Cloud API/WAHA di atas yang kirim gambar asli).
        for i, url in enumerate(image_urls):
            if i > 0:
                await asyncio.sleep(random.uniform(1, 2))
            room = await db.rooms.find_one({"$or": [{"photo_url": url}, {"images.url": url}]})
            # Caption bernomor urut (2026-08-01, permintaan Agus - PRD Natural
            # Conversation Engine §6) - "Foto 1/2/3" per foto, BUKAN label sudut/ruangan
            # spesifik ("Foto Depan"/"Kamar Mandi") karena data foto (db.rooms.images)
            # tidak menyimpan info sudut/ruangan sama sekali - menebaknya = mengarang.
            caption = f"{room['name']} - Foto {i + 1}" if room else f"Foto {i + 1}"
            await _fonnte_send_link_message(token, sender, caption, url, label="Lihat foto")


async def _fonnte_debounced_dispatch(session_id: str) -> None:
    """Tunggu FONNTE_DEBOUNCE_SECONDS - kalau ada pesan BARU masuk utk session ini
    sebelum jeda itu habis, task INI dibatalkan (lihat fonnte_webhook_receive, `.cancel()`
    dipanggil di sana) & digantikan task baru yang mulai menunggu dari awal lagi - jadi
    efektifnya, giliran cuma benar-benar diproses begitu tamu BERHENTI mengetik selama
    FONNTE_DEBOUNCE_SECONDS detik penuh. Semua pesan yang menumpuk selama itu digabung
    jadi SATU giliran (satu balasan AI), bukan dibalas satu-satu terpisah - laporan nyata
    Agus 2026-08-01: tamu yang ngetik beberapa pesan cepat berturut-turut ("halo", "iya
    lanjut", "iya boleh") sebelumnya dapat balasan AI terpisah utk TIAP pesan, terasa
    seperti di-spam walau tidak ada bug data (booking tetap benar tidak dobel, lihat
    _get_conversation_lock) - ini murni soal pengalaman tamu menerima banyak balasan
    beruntun."""
    try:
        await asyncio.sleep(FONNTE_DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return  # pesan susulan datang - giliran gabungan yang BARU akan menangani ini
    pending = _fonnte_pending_messages.pop(session_id, [])
    ctx = _fonnte_pending_ctx.pop(session_id, None)
    _fonnte_debounce_tasks.pop(session_id, None)
    if not pending or not ctx:
        return
    combined = "\n".join(pending)
    try:
        await _fonnte_process_and_reply(
            session_id, combined, ctx["guest_name"], ctx["sender"],
            ctx["bot_id"], ctx["bot_code"], ctx["token"],
        )
    except Exception as e:
        logging.getLogger("fonnte").warning(f"Gagal proses giliran ter-debounce utk {session_id}: {e}")


# Command staf via WA (2026-08-01, permintaan Agus - lihat fonnte_webhook_receive).
# Nomor pribadi Agus (sudah dikenal sistem sbg kontak darurat, ai_service.py) - tambahkan
# nomor lain (format 62xxx, lewat _normalize_phone) kalau ada staf tambahan nanti.
STAFF_COMMAND_NUMBERS = {"6287761611631"}
STAFF_STOP_KEYWORDS = {"stop", "pause", "ambilalih", "ambil"}
STAFF_RESUME_KEYWORDS = {"lanjut", "resume", "aktifkan", "lanjutkan"}


async def _handle_staff_command(bot: dict, token: str, staff_sender: str, message_text: str) -> None:
    """Parse & jalankan command staf ("stop 62812xxxx" / "lanjut 62812xxxx") yang dikirim
    dari nomor pribadi staf ke nomor bot - alternatif ambil-alih chat TANPA buka PMS
    (permintaan Agus: dia biasa balas tamu langsung dari WhatsApp HP, bukan dari kotak
    reply Percakapan di PMS, jadi AI tidak pernah tahu dia sudah turun tangan - lihat
    diskusi 2026-08-01). Selalu balas konfirmasi/error ke NOMOR STAF (bukan tamu) supaya
    dia tahu command-nya berhasil/tidak tanpa perlu cek PMS."""
    parts = message_text.strip().split(None, 1)
    keyword = (parts[0].lower() if parts else "")
    target_raw = parts[1] if len(parts) > 1 else ""
    target = _normalize_phone(target_raw)

    if keyword not in STAFF_STOP_KEYWORDS and keyword not in STAFF_RESUME_KEYWORDS:
        await _fonnte_send_text(
            token, staff_sender,
            "Format command tidak dikenali. Contoh:\n"
            "\"stop 6281234567890\" - hentikan AI utk tamu itu\n"
            "\"lanjut 6281234567890\" - aktifkan AI lagi utk tamu itu",
        )
        return
    if not target or sum(c.isdigit() for c in target) < 8:
        await _fonnte_send_text(token, staff_sender, "Nomor tamu tidak valid/tidak disertakan. Contoh: \"stop 6281234567890\"")
        return

    conv = await db.conversations.find_one(
        {"whatsapp": target, "bot_id": bot["_id"]}, sort=[("updated_at", -1)]
    )
    if not conv:
        await _fonnte_send_text(token, staff_sender, f"Tidak ketemu percakapan aktif dgn nomor {target} di bot ini.")
        return

    if keyword in STAFF_STOP_KEYWORDS:
        await db.conversations.update_one(
            {"_id": conv["_id"]},
            {"$set": {"status": "waiting_admin", "resolution": "handover", "updated_at": utc_now_iso()}},
        )
        await _fonnte_send_text(token, staff_sender, f"OK, AI dihentikan utk {conv.get('guest_name') or target}. Balas tamu langsung di WA seperti biasa.")
    else:
        await db.conversations.update_one(
            {"_id": conv["_id"]},
            {"$set": {"status": "active", "resolution": "handover", "updated_at": utc_now_iso()}},
        )
        await _fonnte_send_text(token, staff_sender, f"OK, AI aktif lagi utk {conv.get('guest_name') or target}.")


@api.post("/webhook/fonnte/{bot_id}")
async def fonnte_webhook_receive(bot_id: str, request: Request):
    """Terima pesan masuk dari Fonnte, DEBOUNCE dulu (lihat _fonnte_debounced_dispatch),
    baru balas lewat AI - pola dasar SAMA dgn webhook Cloud API/WAHA (ujung-ujungnya reuse
    _run_chat_turn lewat _fonnte_process_and_reply), bedanya cuma bentuk payload (JSON
    flat: device/sender/message/name, lihat docs.fonnte.com) & cara kirim balasan
    (_fonnte_send_text/_fonnte_send_link_message, bukan _wa_cloud_send_*/_waha_send_*).

    Auth (2026-07-31): Fonnte TIDAK punya skema tanda tangan webhook resmi (beda dari
    Meta yang punya X-Hub-Signature-256) - `bot_id` di path ITU SENDIRI dipakai sbg
    "secret" (UUID acak, tidak pernah dipublikasikan di luar Settings staf), sama
    prinsipnya dgn token WAHA lama. Dikonfigurasi manual sekali oleh Agus di dashboard
    Fonnte per device (Settings -> device -> Webhook URL), bukan lewat API (Fonnte tidak
    expose endpoint utk set webhook URL secara terprogram)."""
    bot = await db.ai_bots.find_one({"_id": bot_id, "channel_type": "fonnte"})
    if not bot:
        raise HTTPException(404, "Bot Fonnte tidak ditemukan")
    token = bot.get("channel_id") or ""

    try:
        payload = await request.json()
    except Exception:
        payload = dict(await request.form())

    try:
        sender = _normalize_phone(str(payload.get("sender") or ""))
        message_text = (payload.get("message") or "").strip()
        guest_name = payload.get("name") or sender
        if not sender or not message_text:
            return {"ok": True, "diabaikan": "tanpa nomor pengirim/isi pesan"}

        # Guard anti loop-antar-bot (2026-08-01, insiden nyata: nomor WA bot Pelangi &
        # bot Harmoni sempat saling kirim pesan - masing-masing bot menganggap pesan
        # sapaan bot lain sbg pesan tamu sungguhan, saling balas sapaan berulang tanpa
        # henti selama ~2 menit / puluhan pesan sebelum ketahuan & dihentikan manual.
        # Fonnte memang benar meneruskan pesan ini sbg "inbound" (nomor bot lain itu
        # secara teknis eksternal dari sudut pandang device ini) - PMS-nya sendiri yang
        # tidak tahu nomor itu adalah bot lain. Cek terhadap SEMUA nomor bot Fonnte aktif
        # lain (field `own_whatsapp_number`, diisi manual sekali per bot) - kalau
        # pengirim adalah bot kita sendiri yang lain, jangan proses sama sekali (bukan
        # cuma waiting_admin - tidak perlu direspons apa pun, ini bukan tamu).
        bot_numbers = {
            b["own_whatsapp_number"] async for b in db.ai_bots.find(
                {"channel_type": "fonnte", "own_whatsapp_number": {"$exists": True}, "_id": {"$ne": bot_id}},
                {"own_whatsapp_number": 1},
            )
        }
        if sender in bot_numbers:
            logging.getLogger("fonnte").warning(
                f"Pesan dari nomor bot kita sendiri ({sender}) ke bot {bot.get('code')} - diabaikan (anti loop-antar-bot)."
            )
            return {"ok": True, "diabaikan": "pengirim adalah nomor bot internal lain"}

        # Command staf lewat WA (2026-08-01, permintaan Agus) - ambil alih/lanjutkan AI
        # utk tamu tertentu langsung dari WhatsApp pribadi, TANPA buka PMS. Nomor
        # pribadinya sendiri sudah dikenal sistem sebagai "kontak darurat" (lihat
        # ai_service.py) - dipakai lagi di sini sbg identitas staf. Kalau nanti ada staf
        # lain, tambahkan nomornya (format 62xxx) ke set ini.
        #
        # Bug nyata ditemukan 2026-08-01 (keluhan Agus - dia mau test chat AI dari nomor
        # pribadinya sendiri jadi susah): SEBELUMNYA setiap pesan dari nomor staf ke bot
        # manapun langsung diintersep ke sini, apa pun isinya - kalau bukan persis "stop
        # 62xxx"/"lanjut 62xxx", staf cuma dapat balasan error "Format command tidak
        # dikenali", TIDAK PERNAH sampai ke AI sama sekali. Sekarang HANYA diintersep
        # kalau kata pertama pesannya benar-benar salah satu keyword command - selain itu
        # (termasuk staf sendiri mau ngetes chat sbg tamu biasa) lanjut normal ke pipeline
        # AI seperti nomor manapun.
        if sender in STAFF_COMMAND_NUMBERS:
            first_word = message_text.strip().split(None, 1)[0].lower() if message_text.strip() else ""
            if first_word in STAFF_STOP_KEYWORDS or first_word in STAFF_RESUME_KEYWORDS:
                await _handle_staff_command(bot, token, sender, message_text)
                return {"ok": True, "staff_command": True}

        # Foto/media dari tamu (2026-08-01, dikonfirmasi live lewat log payload asli -
        # paket Fonnte yg dipakai TIDAK menyertakan url/filename apa pun utk pesan media,
        # field message-nya cuma literal string "non-text message") - JANGAN diteruskan
        # apa adanya ke AI sbg kalau itu teks yang tamu ketik sungguhan (bisa bikin AI
        # bingung/salah tafsir "non-text message" sbg ucapan tamu) - ganti jadi instruksi
        # jujur, AI tidak bisa melihat gambar sama sekali di paket ini.
        if message_text.lower() in ("non-text message", "non-button message"):
            message_text = (
                "[SISTEM: tamu mengirim foto/gambar/media, TAPI paket WhatsApp yang dipakai TIDAK BISA "
                "menampilkan isinya ke kamu - kamu TIDAK melihat apa pun dari gambar itu, JANGAN berpura-pura "
                "melihatnya. Minta tamu jelaskan lewat teks apa yang ingin disampaikan.]"
            )

        session_id = f"fon-{bot_id}-{sender}"
        _fonnte_pending_messages.setdefault(session_id, []).append(message_text)
        _fonnte_pending_ctx[session_id] = {
            "guest_name": guest_name, "sender": sender, "bot_id": bot_id,
            "bot_code": bot.get("code"), "token": token,
        }
        old_task = _fonnte_debounce_tasks.get(session_id)
        if old_task and not old_task.done():
            old_task.cancel()
        _fonnte_debounce_tasks[session_id] = asyncio.create_task(_fonnte_debounced_dispatch(session_id))
        return {"ok": True, "debounced": True}
    except Exception as e:
        logging.getLogger("fonnte").warning(f"Gagal proses webhook Fonnte: {e}")
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
