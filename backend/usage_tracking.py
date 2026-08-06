"""Pencatatan token/biaya OpenAI (2026-08-06, permintaan Agus - "efisiensi token,
aku tidak mau lagi buang buang tokennya"). Ditemukan lewat audit: app ini TIDAK PUNYA
visibilitas token/biaya sama sekali - kalau ada lonjakan pemakaian (spt yg kejadian di
AI Blog, sistem lain, 2026-08-05), Agus baru tahu dari dashboard OpenAI sendiri SETELAH
kejadian, bukan dari dalam app ini.

Desain SENGAJA non-invasif - TIDAK menyentuh ai_reply()/LlmChat/alur balas tamu sama
sekali (itu jalur inti yang dipakai SETIAP balasan ke tamu real, risiko mengubahnya
lebih besar drpd manfaat observability). emergentintegrations (LlmChat) memanggil
litellm.acompletion() di baliknya utk SEMUA provider - jadi cukup daftarkan 1 callback
global litellm sekali saat startup (lihat register_usage_logging(), dipanggil dari
server.py on_startup) - "menonton dari luar", tidak mengubah kontrol alur panggilan
AI manapun. Kalau logging ini sendiri gagal/error, TIDAK BOLEH ikut menggagalkan balasan
ke tamu - litellm sendiri sudah isolasi exception di callback (tidak propagate ke
caller), tapi tetap dibungkus try/except di sini sbg lapis kedua.
"""

import logging
from datetime import datetime, timezone

import litellm
from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger("usage_tracking")

_db = None  # di-set oleh register_usage_logging(), motor async db instance


class _UsageLogger(CustomLogger):
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        if _db is None:
            return
        try:
            usage = getattr(response_obj, "usage", None)
            model = kwargs.get("model") or getattr(response_obj, "model", None) or "unknown"
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            try:
                cost_usd = litellm.completion_cost(completion_response=response_obj)
            except Exception:
                # Model baru/tidak ada di price map litellm - jangan gagalkan pencatatan
                # cuma krn estimasi biaya tidak tersedia, token count tetap berguna sendiri.
                cost_usd = None
            await _db.llm_usage_log.insert_one({
                "ts": datetime.now(timezone.utc),
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "cost_usd": cost_usd,
            })
        except Exception as e:
            # Jangan pernah biarkan logging ini crash/mengganggu balasan tamu - ini murni
            # observability tambahan, bukan bagian kritis alur chat.
            logger.warning(f"gagal catat usage log: {type(e).__name__}: {e}")


def register_usage_logging(db) -> None:
    """Panggil SEKALI saat startup (lihat server.py on_startup)."""
    global _db
    _db = db
    litellm.callbacks = list(litellm.callbacks or []) + [_UsageLogger()]
    logger.info("Usage tracking terpasang (litellm success callback)")
