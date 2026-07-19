"""Pelangi PMS Connector (Business System Connector).

Satu-satunya modul yang tahu cara bicara ke Pelangi PMS (auth Bearer API key, path
endpoint, bentuk payload) - lihat backend/routes/integrasi_ai_bot.py di repo PMS untuk
sisi baca/tulisnya. server.py (AI Customer Platform) memanggil fungsi di sini, tidak
pernah membentuk request PMS sendiri.

Sumber kebenaran ketersediaan/tarif kamar & tujuan booking request (non-binding) - AI
TIDAK PERNAH menyimpan ketersediaan/booking sungguhan di database sendiri, supaya tidak
ada data ganda yang bisa menyimpang dari PMS.
"""
import logging
import os
import secrets
import time
from typing import Any, Dict, List, Optional

import httpx

from db import db, new_id, utc_now_iso
from connectors.waha_connector import WAHA_WEBHOOK_TOKEN

PMS_API_BASE_URL = os.environ.get("PMS_API_BASE_URL", "")
PMS_API_KEY = os.environ.get("PMS_API_KEY", "")

PMS_DEFAULT_ENDPOINTS = {
    "ketersediaan_path": "/api/integrasi-ai-bot/ketersediaan",
    "booking_request_path": "/api/integrasi-ai-bot/booking-request",
    "tiket_path": "/api/integrasi-ai-bot/tiket",
    "rules_path": "/api/integrasi-ai-bot/rules",
    "booking_status_path": "/api/integrasi-ai-bot/booking-status",
    "cancel_request_path": "/api/integrasi-ai-bot/cancel-request",
}

# Kapabilitas yang BENAR-BENAR tersambung ke kode (toggle di luar daftar ini boleh
# disimpan tapi tidak akan pernah bikin AI melakukan apa pun - endpoint PMS-nya belum ada).
# Jangan tambah entry baru di sini tanpa juga menyambungkan handler-nya di server.py.
PMS_CAPABILITY_WIRED = {"check_availability", "create_booking", "create_maintenance_ticket", "check_booking_status",
                         "create_service_request", "cancel_booking"}
PMS_DEFAULT_CAPABILITIES = {
    "check_availability": True,
    "create_booking": True,
    "check_booking_status": True,
    "create_maintenance_ticket": True,
    "create_service_request": True,  # reuse endpoint tiket yang sama (tipe="service_request")
    "cancel_booking": True,          # non-binding - PMS cuma mencatat permintaan, staf approve/reject manual
    "refund": False,                 # belum diimplementasikan (transfer uang tetap manual staf)
    "ota_sync": False,                # belum diimplementasikan
    "payment": False,                 # belum diimplementasikan
    "checkin": False,                 # belum diimplementasikan
}

PMS_INTEGRATION_DEFAULT = {
    "_id": "singleton",
    "pms_base_url": "",
    "pms_api_key": "",
    "webhook_token": None,
    "send_message_api_key": None,
    "bot_whatsapp_number": "",
    "endpoints": dict(PMS_DEFAULT_ENDPOINTS),
    "capabilities": dict(PMS_DEFAULT_CAPABILITIES),
    "last_test_at": None, "last_test_ok": None, "last_test_latency_ms": None,
    "last_test_message": None, "last_test_version": None,
    "last_sync": {},  # {"hotel_profile": {"at":..., "ok":..., "message":...}, "faq":..., "prompt":..., "rule":...}
    "updated_at": None,
}


async def _pms_config() -> dict:
    """Config PMS Integration siap pakai - auto-seed dari env lama + auto-generate
    webhook_token/send_message_api_key kalau belum ada (migrasi aman, sama pola dengan
    `webhook_token` di Pelangi PMS sendiri)."""
    cfg = await db.pms_integration_config.find_one({"_id": "singleton"})
    if not cfg:
        cfg = dict(PMS_INTEGRATION_DEFAULT)
        cfg["pms_base_url"] = PMS_API_BASE_URL
        cfg["pms_api_key"] = PMS_API_KEY
        cfg["webhook_token"] = WAHA_WEBHOOK_TOKEN or secrets.token_hex(20)
        cfg["send_message_api_key"] = secrets.token_hex(20)
        cfg["updated_at"] = utc_now_iso()
        await db.pms_integration_config.insert_one(cfg)
    elif not cfg.get("send_message_api_key"):
        cfg["send_message_api_key"] = secrets.token_hex(20)
        await db.pms_integration_config.update_one({"_id": "singleton"}, {"$set": {"send_message_api_key": cfg["send_message_api_key"]}})
    merged = {**PMS_INTEGRATION_DEFAULT, **cfg}
    merged["endpoints"] = {**PMS_DEFAULT_ENDPOINTS, **(cfg.get("endpoints") or {})}
    merged["capabilities"] = {**PMS_DEFAULT_CAPABILITIES, **(cfg.get("capabilities") or {})}
    if not merged.get("pms_base_url"):
        merged["pms_base_url"] = PMS_API_BASE_URL
    if not merged.get("pms_api_key"):
        merged["pms_api_key"] = PMS_API_KEY
    if not merged.get("webhook_token"):
        merged["webhook_token"] = WAHA_WEBHOOK_TOKEN
    return merged


async def _pms_log(endpoint: str, method: str, status_code: Optional[int], latency_ms: int,
                    ok: bool, detail: str = "") -> None:
    """Riwayat request/response AI Bot <-> PMS - dipakai dashboard (tab Log) untuk debug
    tanpa perlu SSH baca journalctl. Auto-buang entri lama supaya koleksi tidak membengkak
    tanpa batas (cap longgar, bukan TTL index - cukup untuk kebutuhan debug jangka pendek)."""
    try:
        await db.pms_integration_logs.insert_one({
            "_id": new_id(), "endpoint": endpoint, "method": method,
            "status_code": status_code, "latency_ms": latency_ms, "ok": ok,
            "detail": (detail or "")[:500], "at": utc_now_iso(),
        })
        count = await db.pms_integration_logs.count_documents({})
        if count > 500:
            old = await db.pms_integration_logs.find({}).sort("at", 1).limit(count - 500).to_list(count - 500)
            await db.pms_integration_logs.delete_many({"_id": {"$in": [o["_id"] for o in old]}})
    except Exception:
        pass  # logging tidak boleh menggagalkan alur utama


async def _pms_ketersediaan(tanggal: Optional[str] = None, tipe: Optional[str] = None,
                             tanggal_checkout: Optional[str] = None) -> List[dict]:
    """Ketersediaan & tarif kamar LIVE dari Pelangi PMS - satu-satunya sumber kebenaran,
    bukan koleksi `db.rooms` lokal ai-chat-bot (itu cuma dipakai fitur admin lokal lain,
    bukan untuk menjawab tamu)."""
    cfg = await _pms_config()
    if not cfg["capabilities"].get("check_availability"):
        return []
    if not cfg["pms_base_url"] or not cfg["pms_api_key"]:
        logging.getLogger("pms").warning("PMS URL/API Key belum diisi - tidak bisa cek ketersediaan PMS")
        return []
    params = {}
    if tanggal:
        params["tanggal"] = tanggal
    if tipe:
        params["tipe"] = tipe
    if tanggal_checkout:
        params["tanggal_checkout"] = tanggal_checkout
    path = cfg["endpoints"]["ketersediaan_path"]
    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(
                f"{cfg['pms_base_url'].rstrip('/')}{path}",
                headers={"Authorization": f"Bearer {cfg['pms_api_key']}"}, params=params,
            )
        latency_ms = int((time.time() - started) * 1000)
        if resp.status_code >= 400:
            await _pms_log(path, "GET", resp.status_code, latency_ms, False, resp.text)
            logging.getLogger("pms").warning(f"PMS ketersediaan gagal HTTP {resp.status_code}: {resp.text[:300]}")
            return []
        await _pms_log(path, "GET", resp.status_code, latency_ms, True)
        data = resp.json()
        out = data.get("ketersediaan") or []
        for r in out:
            r["_tanggal"] = data.get("tanggal")
        return out
    except Exception as e:
        await _pms_log(path, "GET", None, int((time.time() - started) * 1000), False, str(e))
        logging.getLogger("pms").warning(f"Gagal menghubungi PMS ketersediaan: {e}")
        return []


async def _pms_buat_booking_request(args: dict) -> dict:
    """Kirim permintaan booking NON-BINDING ke Pelangi PMS (db.booking_requests) -
    resepsionis yang Terima/Tolak manual, sama seperti alur AI WhatsApp internal PMS.
    ai-chat-bot TIDAK PERNAH membuat booking sungguhan sendiri."""
    cfg = await _pms_config()
    if not cfg["capabilities"].get("create_booking"):
        return {"ok": False, "error": "Fitur Buat Booking dinonaktifkan di panel Integrasi PMS"}
    if not cfg["pms_base_url"] or not cfg["pms_api_key"]:
        return {"ok": False, "error": "PMS URL/API Key belum dikonfigurasi"}
    payload = {
        "nama_tamu": args.get("guest_name"), "no_hp": args.get("whatsapp"),
        "tipe": args.get("tipe"), "room_tipe": args.get("room_tipe"),
        "tanggal_checkin": args.get("tanggal_checkin"), "jam_checkin": args.get("jam_checkin"),
        "tanggal_checkout": args.get("tanggal_checkout"),
        "jumlah_kamar": args.get("jumlah_kamar"), "jumlah_tamu": args.get("jumlah_tamu"),
        "payment_option": args.get("payment_option"),
    }
    path = cfg["endpoints"]["booking_request_path"]
    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                f"{cfg['pms_base_url'].rstrip('/')}{path}",
                headers={"Authorization": f"Bearer {cfg['pms_api_key']}"}, json=payload,
            )
        latency_ms = int((time.time() - started) * 1000)
        if resp.status_code >= 400:
            await _pms_log(path, "POST", resp.status_code, latency_ms, False, resp.text)
            return {"ok": False, "error": f"PMS menolak: HTTP {resp.status_code} {resp.text[:200]}"}
        await _pms_log(path, "POST", resp.status_code, latency_ms, True)
        data = resp.json()
        return {"ok": True, "booking_request": data.get("booking_request")}
    except Exception as e:
        await _pms_log(path, "POST", None, int((time.time() - started) * 1000), False, str(e))
        return {"ok": False, "error": f"Gagal menghubungi PMS: {e}"}


async def _pms_buat_tiket(tipe: str, deskripsi: str, whatsapp: str, guest_name: str = "") -> dict:
    """Kirim tiket komplain/maintenance/service_request ke Pelangi PMS (reuse endpoint yang
    SUDAH ADA sejak awal di sisi PMS, `/api/integrasi-ai-bot/tiket` - sebelumnya tidak pernah
    dipanggil dari ai-chat-bot, tiket AI selalu nyasar ke `db.service_requests` lokal
    yang tidak pernah dilihat staf PMS. `tipe` menentukan capability mana yang dicek -
    complaint & maintenance masih dipayungi `create_maintenance_ticket`, service_request
    (extra bed/towel/laundry/dll dari tool create_service_request) toggle sendiri."""
    cap_key = "create_service_request" if tipe == "service_request" else "create_maintenance_ticket"
    cfg = await _pms_config()
    if not cfg["capabilities"].get(cap_key):
        return {"ok": False, "error": f"Fitur '{cap_key}' dinonaktifkan di panel Integrasi PMS"}
    if not cfg["pms_base_url"] or not cfg["pms_api_key"]:
        return {"ok": False, "error": "PMS URL/API Key belum dikonfigurasi"}
    payload = {"tipe": tipe, "deskripsi": deskripsi, "no_hp": whatsapp, "nama_tamu": guest_name}
    path = cfg["endpoints"]["tiket_path"]
    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                f"{cfg['pms_base_url'].rstrip('/')}{path}",
                headers={"Authorization": f"Bearer {cfg['pms_api_key']}"}, json=payload,
            )
        latency_ms = int((time.time() - started) * 1000)
        if resp.status_code >= 400:
            await _pms_log(path, "POST", resp.status_code, latency_ms, False, resp.text)
            return {"ok": False, "error": f"PMS menolak: HTTP {resp.status_code} {resp.text[:200]}"}
        await _pms_log(path, "POST", resp.status_code, latency_ms, True)
        data = resp.json()
        return {"ok": True, "tiket": data.get("tiket")}
    except Exception as e:
        await _pms_log(path, "POST", None, int((time.time() - started) * 1000), False, str(e))
        return {"ok": False, "error": f"Gagal menghubungi PMS: {e}"}


async def _pms_status_booking(whatsapp: str) -> dict:
    """Status booking request tamu, LIVE dari Pelangi PMS (`db.booking_requests`) - bukan
    `db.bookings` lokal ai-chat-bot yang isinya cuma sisa fitur admin generik, tidak pernah
    diisi jalur AI sejak create_booking dialihkan ke PMS."""
    cfg = await _pms_config()
    if not cfg["capabilities"].get("check_booking_status"):
        return {"ok": False, "error": "Fitur Cek Status Booking dinonaktifkan di panel Integrasi PMS"}
    if not cfg["pms_base_url"] or not cfg["pms_api_key"]:
        return {"ok": False, "error": "PMS URL/API Key belum dikonfigurasi"}
    path = cfg["endpoints"].get("booking_status_path", PMS_DEFAULT_ENDPOINTS["booking_status_path"])
    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(
                f"{cfg['pms_base_url'].rstrip('/')}{path}",
                headers={"Authorization": f"Bearer {cfg['pms_api_key']}"}, params={"no_hp": whatsapp},
            )
        latency_ms = int((time.time() - started) * 1000)
        if resp.status_code >= 400:
            await _pms_log(path, "GET", resp.status_code, latency_ms, False, resp.text)
            return {"ok": False, "error": f"PMS menolak: HTTP {resp.status_code} {resp.text[:200]}"}
        await _pms_log(path, "GET", resp.status_code, latency_ms, True)
        data = resp.json()
        return {"ok": True, "permintaan": data.get("permintaan") or []}
    except Exception as e:
        await _pms_log(path, "GET", None, int((time.time() - started) * 1000), False, str(e))
        return {"ok": False, "error": f"Gagal menghubungi PMS: {e}"}


async def _pms_ajukan_pembatalan(kode: str, whatsapp: str, alasan: str = "") -> dict:
    """Ajukan permintaan pembatalan booking ke Pelangi PMS - NON-BINDING, sama seperti
    _pms_buat_booking_request (AI TIDAK PERNAH mengeksekusi pembatalan sungguhan langsung,
    cuma menyampaikan info; PMS mencatat & staf approve/reject manual, lihat
    routes/pembatalan.py di repo PMS untuk kebijakan refund H-3/50%)."""
    cfg = await _pms_config()
    if not cfg["capabilities"].get("cancel_booking"):
        return {"ok": False, "error": "Fitur Cancel Booking dinonaktifkan di panel Integrasi PMS"}
    if not cfg["pms_base_url"] or not cfg["pms_api_key"]:
        return {"ok": False, "error": "PMS URL/API Key belum dikonfigurasi"}
    payload = {"kode": kode, "no_hp": whatsapp, "alasan": alasan}
    path = cfg["endpoints"].get("cancel_request_path", PMS_DEFAULT_ENDPOINTS["cancel_request_path"])
    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                f"{cfg['pms_base_url'].rstrip('/')}{path}",
                headers={"Authorization": f"Bearer {cfg['pms_api_key']}"}, json=payload,
            )
        latency_ms = int((time.time() - started) * 1000)
        if resp.status_code >= 400:
            await _pms_log(path, "POST", resp.status_code, latency_ms, False, resp.text)
            return {"ok": False, "error": f"PMS menolak: HTTP {resp.status_code} {resp.text[:200]}"}
        await _pms_log(path, "POST", resp.status_code, latency_ms, True)
        return resp.json()
    except Exception as e:
        await _pms_log(path, "POST", None, int((time.time() - started) * 1000), False, str(e))
        return {"ok": False, "error": f"Gagal menghubungi PMS: {e}"}


# hotel_profile/faq/prompt DIHAPUS dari sini 2026-07-19 - premis awalnya salah, PMS tidak
# pernah punya data ini (PMS murni operasional: booking/kamar/tarif). hotel_profile & FAQ
# sekarang sync dari web-pelangi (lihat connectors/webpelangi_connector.py), prompt 100%
# milik ai-chat-bot sendiri (tidak ada yang perlu disinkron).
SYNC_KINDS = {"rule"}


async def _sync_business_rules() -> dict:
    """Rule Engine tahap 1: PMS = pemilik kebenaran (routes/business_rules.py di repo PMS),
    ai-chat-bot cuma menyimpan CACHE read-only hasil sync ini di `business_rules_cache` -
    dipakai `_build_context` supaya AI menjawab kebijakan bisnis akurat, bukan menghafal
    teks bebas. Replace-all (bukan merge) supaya rule yang dihapus/dinonaktifkan di PMS
    ikut hilang dari cache, tidak nyangkut selamanya."""
    cfg = await _pms_config()
    if not cfg["pms_base_url"] or not cfg["pms_api_key"]:
        return {"ok": False, "message": "PMS URL / API Key belum diisi", "at": utc_now_iso()}
    path = cfg["endpoints"].get("rules_path", PMS_DEFAULT_ENDPOINTS["rules_path"])
    started = time.time()
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(
                f"{cfg['pms_base_url'].rstrip('/')}{path}",
                headers={"Authorization": f"Bearer {cfg['pms_api_key']}"},
            )
        latency_ms = int((time.time() - started) * 1000)
        if resp.status_code >= 400:
            await _pms_log(path, "GET", resp.status_code, latency_ms, False, "sync rule")
            return {"ok": False, "message": f"PMS merespons HTTP {resp.status_code}", "at": utc_now_iso()}
        await _pms_log(path, "GET", resp.status_code, latency_ms, True, "sync rule")
        rules = (resp.json().get("rules")) or []
        await db.business_rules_cache.delete_many({})
        if rules:
            await db.business_rules_cache.insert_many([{"_id": new_id(), **r, "synced_at": utc_now_iso()} for r in rules])
        return {"ok": True, "message": f"{len(rules)} business rule disinkronkan dari PMS", "at": utc_now_iso(), "count": len(rules)}
    except Exception as e:
        await _pms_log(path, "GET", None, int((time.time() - started) * 1000), False, f"sync rule: {e}")
        return {"ok": False, "message": f"Gagal menghubungi PMS: {e}", "at": utc_now_iso()}
