"""WAHA Connector (Channel Adapter) - WhatsApp gateway self-hosted.

Satu-satunya modul yang tahu cara bicara ke REST API WAHA (auth X-Api-Key, bentuk
payload sendText/sesi). server.py (dan connector lain seperti pms_connector untuk
fallback webhook_token) memanggil fungsi di sini, tidak pernah membentuk request WAHA
sendiri - supaya kalau WAHA diganti provider WA lain suatu saat, cuma modul ini yang
perlu diubah.

Multi-session (2026-07-19): WAHA mendukung banyak nomor WhatsApp sekaligus, tiap nomor
adalah 1 "session" bernama bebas (bukan cuma "default" bawaan). Dipakai supaya tiap AI
bot (lihat AI List) bisa punya nomor WA sendiri-sendiri (mis. satu untuk booking/info,
satu lagi untuk komplain/layanan tamu) - lihat server.py webhook_waha() untuk cara
routing pesan masuk ke bot yang tepat berdasarkan session mana yang menerimanya.
"""
import logging
import os
from typing import Any, Dict, Optional

import httpx

# WAHA memanggil /webhook/waha di server.py saat ada pesan WhatsApp masuk (payload-nya
# selalu menyertakan nama session pengirim); balasan AI dikirim balik ke tamu dengan PMS
# ini memanggil WAHA (arah keluar, harus lewat session yang SAMA dengan yang menerima
# pesan), bukan lewat response webhook.
WAHA_BASE_URL = os.environ.get("WAHA_BASE_URL", "")
WAHA_API_KEY = os.environ.get("WAHA_API_KEY", "")
WAHA_SESSION = os.environ.get("WAHA_SESSION", "default")  # session/nomor default/utama
WAHA_WEBHOOK_TOKEN = os.environ.get("WAHA_WEBHOOK_TOKEN", "")


async def _waha_call(method: str, path: str, json_body: Optional[dict] = None) -> tuple[int, dict]:
    """Proxy tipis ke REST API WAHA sendiri - dipakai endpoint dashboard di bawah supaya
    owner bisa kelola koneksi WhatsApp (status/connect/disconnect) tanpa perlu masuk
    terminal server. WAHA_BASE_URL cukup reachable dari host ini (bukan API publik)."""
    if not WAHA_BASE_URL or not WAHA_API_KEY:
        return 503, {"error": "WAHA_BASE_URL/WAHA_API_KEY belum dikonfigurasi"}
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.request(
                method, f"{WAHA_BASE_URL.rstrip('/')}{path}",
                headers={"X-Api-Key": WAHA_API_KEY}, json=json_body,
            )
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:300]}
        return resp.status_code, data
    except Exception as e:
        return 502, {"error": f"Gagal menghubungi WAHA: {e}"}


async def _waha_send_text(chat_id: str, text: str, session: str = WAHA_SESSION) -> bool:
    if not WAHA_BASE_URL or not WAHA_API_KEY:
        logging.getLogger("waha").warning("WAHA_BASE_URL/WAHA_API_KEY belum diisi — balasan tidak terkirim ke tamu")
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                f"{WAHA_BASE_URL.rstrip('/')}/api/sendText",
                headers={"X-Api-Key": WAHA_API_KEY},
                json={"session": session, "chatId": chat_id, "text": text},
            )
            if resp.status_code >= 400:
                logging.getLogger("waha").warning(f"WAHA sendText gagal HTTP {resp.status_code}: {resp.text[:300]}")
                return False
            return True
    except Exception as e:
        logging.getLogger("waha").warning(f"Gagal memanggil WAHA sendText: {e}")
        return False


async def _waha_send_image(chat_id: str, image_url: str, caption: str = "", session: str = WAHA_SESSION) -> bool:
    """Kirim foto SUNGGUHAN (bukan cuma link teks) - dipanggil untuk tiap marker
    [[IMG: url]] yang AI sisipkan di balasannya (lihat parse_img_markers di
    ai_service.py). Ditemukan 2026-07-19 lewat riwayat chat nyata: marker itu
    SEBELUMNYA tidak pernah diproses sama sekali, jadi tamu menerima teks mentah
    "[[IMG: https://...]]" alih-alih foto - bug ditemukan & baru diperbaiki di sini."""
    if not WAHA_BASE_URL or not WAHA_API_KEY:
        logging.getLogger("waha").warning("WAHA_BASE_URL/WAHA_API_KEY belum diisi — foto tidak terkirim ke tamu")
        return False
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(
                f"{WAHA_BASE_URL.rstrip('/')}/api/sendImage",
                headers={"X-Api-Key": WAHA_API_KEY},
                json={
                    "session": session, "chatId": chat_id,
                    "file": {"mimetype": "image/jpeg", "url": image_url},
                    "caption": caption or "",
                },
            )
            if resp.status_code >= 400:
                logging.getLogger("waha").warning(f"WAHA sendImage gagal HTTP {resp.status_code}: {resp.text[:300]}")
                return False
            return True
    except Exception as e:
        logging.getLogger("waha").warning(f"Gagal memanggil WAHA sendImage: {e}")
        return False


async def _waha_list_sessions() -> list:
    """Semua session WAHA (nomor WA) yang ada, apapun statusnya - dipakai panel koneksi
    supaya owner lihat semua nomor sekaligus, bukan cuma satu."""
    status, data = await _waha_call("GET", "/api/sessions?all=true")
    return data if status < 400 and isinstance(data, list) else []


async def _waha_ensure_session(session: str, webhook_url: str) -> tuple[int, dict]:
    """Pastikan session dengan nama ini ADA di WAHA (bukan cuma 'default' bawaan) -
    dipanggil sebelum start/pairing kalau session belum pernah dibuat. Aman dipanggil
    berkali-kali (kalau sudah ada, WAHA balas error yang kita abaikan)."""
    status, data = await _waha_call("GET", f"/api/sessions/{session}")
    if status < 400:
        return status, data
    return await _waha_call("POST", "/api/sessions", {
        "name": session, "start": False,
        "config": {"webhooks": [{"url": webhook_url, "events": ["message"]}]},
    })
