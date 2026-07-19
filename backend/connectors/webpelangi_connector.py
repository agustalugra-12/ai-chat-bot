"""web-pelangi Connector (Marketing Site Content Source).

Sumber kebenaran untuk profil hotel (nama/alamat/kontak) & FAQ publik - BUKAN Pelangi PMS
(PMS murni operasional: booking/kamar/tarif, tidak pernah menyimpan konten marketing ini).
web-pelangi (`/var/www/web-pelangi`, domain pelangihomestay.com) sudah punya endpoint
publik `GET /api/content` (tanpa auth, dipakai frontend marketing site sendiri untuk
hydrate halaman) yang berisi field "site" (profil hotel) & "faqs" (daftar FAQ) - modul ini
cuma menariknya secara read-only, sama sekali tidak pernah menulis balik ke web-pelangi.
"""
import os
import time
from typing import Any, Dict

import httpx

from db import db, new_id, utc_now_iso

WEB_CONTENT_DEFAULT_URL = os.environ.get("WEB_CONTENT_URL", "https://pelangihomestay.com/api/content")

WEB_CONTENT_INTEGRATION_DEFAULT = {
    "_id": "singleton",
    "base_url": WEB_CONTENT_DEFAULT_URL,
    "last_sync": {},  # {"hotel_profile": {"at":..., "ok":..., "message":...}, "faq": {...}}
    "updated_at": None,
}


async def _web_content_config() -> dict:
    cfg = await db.web_content_integration_config.find_one({"_id": "singleton"})
    if not cfg:
        cfg = dict(WEB_CONTENT_INTEGRATION_DEFAULT)
        await db.web_content_integration_config.insert_one(dict(cfg))
    merged = {**WEB_CONTENT_INTEGRATION_DEFAULT, **cfg}
    if not merged.get("base_url"):
        merged["base_url"] = WEB_CONTENT_DEFAULT_URL
    return merged


async def _fetch_web_content() -> Dict[str, Any]:
    cfg = await _web_content_config()
    async with httpx.AsyncClient(timeout=10) as http:
        resp = await http.get(cfg["base_url"])
        resp.raise_for_status()
        return resp.json()


async def _sync_hotel_profile() -> dict:
    """site.brand/address/whatsappDisplay/email -> db.settings (hotel_name/address/phone/
    email). checkin_time/checkout_time SENGAJA tidak disentuh - web-pelangi tidak punya
    field terstruktur untuk itu (cuma teks bebas "hours"), tetap domain staf/PMS."""
    try:
        content = await _fetch_web_content()
        site = content.get("site") or {}
        if not site:
            return {"ok": False, "message": "Field 'site' kosong/tidak ditemukan di web-pelangi", "at": utc_now_iso()}
        updates = {
            "hotel_name": site.get("brand"), "address": site.get("address"),
            "phone": site.get("whatsappDisplay") or site.get("whatsapp"), "email": site.get("email"),
            "updated_at": utc_now_iso(),
        }
        updates = {k: v for k, v in updates.items() if v}
        await db.settings.update_one({"_id": "singleton"}, {"$set": updates}, upsert=True)
        return {"ok": True, "message": f"Tersinkron: {', '.join(updates.keys())}", "at": utc_now_iso()}
    except Exception as e:
        return {"ok": False, "message": f"Gagal sync hotel profile: {e}", "at": utc_now_iso()}


async def _sync_faq() -> dict:
    """faqs[] -> db.knowledge_base kategori 'faq', ditandai source='web_sync' supaya
    replace-all di sini tidak pernah menghapus item FAQ yang staf tambahkan manual sendiri
    (yang tidak punya field source ini) - sama prinsipnya dengan sync Business Rules dari
    PMS (replace-all, tapi discoped hanya ke data yang benar-benar berasal dari sync)."""
    try:
        content = await _fetch_web_content()
        faqs = content.get("faqs") or []
        await db.knowledge_base.delete_many({"category": "faq", "source": "web_sync"})
        now = utc_now_iso()
        docs = [
            {
                "_id": f"websync_{item.get('id') or new_id()}",
                "category": "faq", "title": item.get("q", ""), "content": item.get("a", ""),
                "is_active": True, "images": [], "source": "web_sync",
                "created_at": now, "updated_at": now,
            }
            for item in faqs if item.get("q") and item.get("a")
        ]
        if docs:
            await db.knowledge_base.insert_many(docs)
        return {"ok": True, "message": f"{len(docs)} FAQ tersinkron dari web-pelangi", "at": now}
    except Exception as e:
        return {"ok": False, "message": f"Gagal sync FAQ: {e}", "at": utc_now_iso()}
