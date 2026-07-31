"""Fonnte WhatsApp API Connector (unofficial WA gateway, Indonesia).

Dipakai Agus mulai 2026-07-31 sbg pengganti WAHA (WAHA sempat memicu blokir WhatsApp
lewat reconnect-storm - lihat memory proyek - dan migrasi ke WABA Cloud API resmi masih
kena hambatan verifikasi nomor produksi). BEDA dgn WABA (1 access token global dipakai
banyak nomor via WABA/system-user) - tiap device Fonnte punya TOKEN SENDIRI-SENDIRI,
jadi token disimpan PER BOT di `ai_bots.channel_id` (bukan 1 env var global spt
WHATSAPP_CLOUD_ACCESS_TOKEN), diteruskan eksplisit ke tiap fungsi di sini.

Paket Fonnte yang dipakai Agus TIDAK support attachment (dikonfirmasi 2026-07-31 lewat
endpoint /device: `"attachment": false`, "package": "Free" - parameter url/file di API
kirim Fonnte cuma berlaku paket super/advanced/ultra ke atas) - voucher PDF & foto kamar
SENGAJA dikirim sbg LINK di teks biasa, bukan attachment native (keputusan eksplisit
Agus "pakai link saja dulu", lihat _fonnte_send_link_message).
"""
import logging

import httpx

FONNTE_API_BASE = "https://api.fonnte.com"

logger = logging.getLogger("fonnte")


async def _fonnte_send_text(token: str, to: str, message: str) -> bool:
    """Kirim teks biasa. HTTP 200 TIDAK berarti sukses (Fonnte selalu balas 200, bahkan
    utk device disconnected/token salah) - WAJIB cek field `status` di body JSON, bukan
    cuma status code (ditemukan 2026-07-31 lewat tes langsung ke API asli: device yang
    belum di-scan/connect tetap balas HTTP 200 dgn status:false + reason penjelasannya)."""
    if not token:
        logger.warning("Fonnte token kosong - pesan tidak terkirim")
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                f"{FONNTE_API_BASE}/send",
                headers={"Authorization": token},
                data={"target": to, "message": message, "countryCode": "62"},
            )
            body = resp.json()
            if resp.status_code >= 400 or body.get("status") is False:
                logger.warning(f"Fonnte sendText gagal: HTTP {resp.status_code}, body {body}")
                return False
            return True
    except Exception as e:
        logger.warning(f"Gagal memanggil Fonnte sendText: {e}")
        return False


async def _fonnte_send_link_message(token: str, to: str, caption: str, url: str, label: str = "Lihat di sini") -> bool:
    """Pengganti kirim attachment (paket Fonnte Agus tidak support attachment sama
    sekali - lihat catatan modul). Dipakai utk voucher booking (link PDF publik yang
    sudah ada, lihat email_service.py di repo PMS) & foto kamar ([[IMG: url]] marker,
    lihat ai_service.py) - keduanya sudah lama disimpan sbg URL publik jadi tidak perlu
    infrastruktur hosting baru, cukup ganti CARA mengirimkannya utk channel Fonnte."""
    teks = f"{caption}\n\n{label}: {url}" if caption else f"{label}: {url}"
    return await _fonnte_send_text(token, to, teks)
