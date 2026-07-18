"""WAHA Connector (Channel Adapter) - WhatsApp gateway self-hosted.

Satu-satunya modul yang tahu cara bicara ke REST API WAHA (auth X-Api-Key, bentuk
payload sendText/sesi). server.py (dan connector lain seperti pms_connector untuk
fallback webhook_token) memanggil fungsi di sini, tidak pernah membentuk request WAHA
sendiri - supaya kalau WAHA diganti provider WA lain suatu saat, cuma modul ini yang
perlu diubah.
"""
import logging
import os
from typing import Optional

import httpx

# WAHA memanggil /webhook/waha di server.py saat ada pesan WhatsApp masuk; balasan AI
# dikirim balik ke tamu dengan PMS ini memanggil WAHA (arah keluar), bukan lewat response
# webhook.
WAHA_BASE_URL = os.environ.get("WAHA_BASE_URL", "")
WAHA_API_KEY = os.environ.get("WAHA_API_KEY", "")
WAHA_SESSION = os.environ.get("WAHA_SESSION", "default")
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


async def _waha_send_text(chat_id: str, text: str) -> bool:
    if not WAHA_BASE_URL or not WAHA_API_KEY:
        logging.getLogger("waha").warning("WAHA_BASE_URL/WAHA_API_KEY belum diisi — balasan tidak terkirim ke tamu")
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                f"{WAHA_BASE_URL.rstrip('/')}/api/sendText",
                headers={"X-Api-Key": WAHA_API_KEY},
                json={"session": WAHA_SESSION, "chatId": chat_id, "text": text},
            )
            if resp.status_code >= 400:
                logging.getLogger("waha").warning(f"WAHA sendText gagal HTTP {resp.status_code}: {resp.text[:300]}")
                return False
            return True
    except Exception as e:
        logging.getLogger("waha").warning(f"Gagal memanggil WAHA sendText: {e}")
        return False
