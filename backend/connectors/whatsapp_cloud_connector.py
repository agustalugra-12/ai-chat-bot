"""WhatsApp Cloud API Connector (resmi Meta/WABA).

Migrasi bertahap dari WAHA (self-hosted, tidak resmi) ke Cloud API resmi Meta - dipicu
insiden 2026-07-20: WAHA reconnect-storm (retry setiap 2 detik tanpa backoff, bawaan
library, tidak bisa diubah) memicu WhatsApp memblokir sementara nomor Admin. Cloud API
resmi tidak punya risiko itu sama sekali (bukan client tidak resmi, tidak ada sesi
"reconnect" yang bisa di-flag sebagai bot).

TAHAP AWAL (2026-07-20): kredensial yang dipakai masih WABA & nomor UJI COBA bawaan Meta
("Test WhatsApp Business Account"), BUKAN nomor Admin asli - aman untuk bangun & tes
integrasi tanpa risiko ke nomor produksi. Ganti WHATSAPP_CLOUD_PHONE_NUMBER_ID/WABA_ID di
.env setelah nomor asli selesai dihubungkan lewat Coexistence.
"""
import logging
import os

import httpx

GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

WHATSAPP_CLOUD_ACCESS_TOKEN = os.environ.get("WHATSAPP_CLOUD_ACCESS_TOKEN", "")
WHATSAPP_CLOUD_WABA_ID = os.environ.get("WHATSAPP_CLOUD_WABA_ID", "")
WHATSAPP_CLOUD_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_CLOUD_PHONE_NUMBER_ID", "")

logger = logging.getLogger("whatsapp_cloud")


async def _wa_cloud_send_text(to: str, text: str, phone_number_id: str = "") -> bool:
    """Kirim pesan teks lewat Cloud API. `phone_number_id` opsional - default ke nomor
    tunggal di .env (WHATSAPP_CLOUD_PHONE_NUMBER_ID), boleh override kalau nanti ada
    multi-nomor lewat Cloud API juga (pola sama dengan multi-session WAHA)."""
    pnid = phone_number_id or WHATSAPP_CLOUD_PHONE_NUMBER_ID
    if not WHATSAPP_CLOUD_ACCESS_TOKEN or not pnid:
        logger.warning("WHATSAPP_CLOUD_ACCESS_TOKEN/PHONE_NUMBER_ID belum diisi — balasan tidak terkirim")
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                f"{GRAPH_API_BASE}/{pnid}/messages",
                headers={"Authorization": f"Bearer {WHATSAPP_CLOUD_ACCESS_TOKEN}"},
                json={"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}},
            )
            if resp.status_code >= 400:
                logger.warning(f"Cloud API sendText gagal HTTP {resp.status_code}: {resp.text[:300]}")
                return False
            return True
    except Exception as e:
        logger.warning(f"Gagal memanggil Cloud API sendText: {e}")
        return False


async def _wa_cloud_send_image(to: str, image_url: str, caption: str = "", phone_number_id: str = "") -> bool:
    pnid = phone_number_id or WHATSAPP_CLOUD_PHONE_NUMBER_ID
    if not WHATSAPP_CLOUD_ACCESS_TOKEN or not pnid:
        logger.warning("WHATSAPP_CLOUD_ACCESS_TOKEN/PHONE_NUMBER_ID belum diisi — foto tidak terkirim")
        return False
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(
                f"{GRAPH_API_BASE}/{pnid}/messages",
                headers={"Authorization": f"Bearer {WHATSAPP_CLOUD_ACCESS_TOKEN}"},
                json={
                    "messaging_product": "whatsapp", "to": to, "type": "image",
                    "image": {"link": image_url, "caption": caption or ""},
                },
            )
            if resp.status_code >= 400:
                logger.warning(f"Cloud API sendImage gagal HTTP {resp.status_code}: {resp.text[:300]}")
                return False
            return True
    except Exception as e:
        logger.warning(f"Gagal memanggil Cloud API sendImage: {e}")
        return False


async def _wa_cloud_send_document(to: str, filename: str, data_base64: str, caption: str = "", phone_number_id: str = "") -> bool:
    """Dokumen (PDF dst) lewat Cloud API perlu diupload dulu ke Media endpoint (Cloud API
    tidak terima base64 langsung di body pesan seperti WAHA) - dua langkah: upload -> dapat
    media_id -> kirim pesan pakai media_id itu."""
    pnid = phone_number_id or WHATSAPP_CLOUD_PHONE_NUMBER_ID
    if not WHATSAPP_CLOUD_ACCESS_TOKEN or not pnid:
        logger.warning("WHATSAPP_CLOUD_ACCESS_TOKEN/PHONE_NUMBER_ID belum diisi — dokumen tidak terkirim")
        return False
    try:
        import base64
        raw = base64.b64decode(data_base64)
        async with httpx.AsyncClient(timeout=30) as http:
            upload = await http.post(
                f"{GRAPH_API_BASE}/{pnid}/media",
                headers={"Authorization": f"Bearer {WHATSAPP_CLOUD_ACCESS_TOKEN}"},
                data={"messaging_product": "whatsapp", "type": "application/pdf"},
                files={"file": (filename, raw, "application/pdf")},
            )
            if upload.status_code >= 400:
                logger.warning(f"Cloud API upload media gagal HTTP {upload.status_code}: {upload.text[:300]}")
                return False
            media_id = upload.json().get("id")
            if not media_id:
                logger.warning("Cloud API upload media tidak mengembalikan media id")
                return False
            resp = await http.post(
                f"{GRAPH_API_BASE}/{pnid}/messages",
                headers={"Authorization": f"Bearer {WHATSAPP_CLOUD_ACCESS_TOKEN}"},
                json={
                    "messaging_product": "whatsapp", "to": to, "type": "document",
                    "document": {"id": media_id, "filename": filename, "caption": caption or ""},
                },
            )
            if resp.status_code >= 400:
                logger.warning(f"Cloud API sendDocument gagal HTTP {resp.status_code}: {resp.text[:300]}")
                return False
            return True
    except Exception as e:
        logger.warning(f"Gagal memanggil Cloud API sendDocument: {e}")
        return False
