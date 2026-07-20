"""WAHA Health Monitor (2026-07-20) — cegah koneksi WhatsApp diblokir WhatsApp gara-gara
percobaan sambung ulang beruntun.

Insiden nyata: WAHA (engine NOWEB/Baileys yang dipakai di sini) mencoba reconnect setiap
2 detik TANPA backoff begitu koneksi putus, sampai maksimal 60x percobaan (~2 menit)
sebelum akhirnya menyerah sendiri dan pindah ke status FAILED (lihat
StatusTracker.STUCK_IN_STARTING_THRESHOLD & WhatsappSessionNoWebCore.START_ATTEMPT_DELAY_SECONDS
di source WAHA, dicek langsung di container - TIDAK ADA env var untuk mengubah perilaku ini).
Percobaan sambung ulang beruntun sebanyak itu ternyata BENAR-BENAR memicu WhatsApp memblokir
sementara akun WA Admin (dikonfirmasi user 2026-07-20) - bukan cuma teori risiko.

Solusi: monitor ini polling status tiap sesi WAHA secara berkala. Begitu sebuah sesi
terpantau STUCK di status STARTING lebih dari STARTING_STUCK_THRESHOLD_SECONDS, monitor
PROAKTIF menghentikan sesi (POST /api/sessions/{name}/stop) - jauh lebih cepat daripada
menunggu WAHA menyerah sendiri di percobaan ke-60 - supaya jumlah percobaan sambung
beruntun yang benar-benar terjadi jauh lebih sedikit sebelum berhenti. Setiap transisi
status penting (FAILED baru, STARTING macet dihentikan paksa, atau WORKING pulih) dikabari
ke owner lewat Telegram (relay lewat PMS, `_pms_alert_owner`) SAAT ITU JUGA supaya staf tahu
tanpa perlu menemukan sendiri berjam-jam kemudian, dan supaya staf TIDAK langsung mencoba
scan ulang QR berkali-kali dalam waktu berdekatan (pola itu sendiri yang berisiko memicu
blokir lagi) - lebih baik tunggu jeda dulu.
"""
import asyncio
import logging
import time
from typing import Any, Dict

from connectors.waha_connector import _waha_list_sessions, _waha_call
from connectors.pms_connector import _pms_alert_owner

logger = logging.getLogger("waha_health")

POLL_INTERVAL_SECONDS = 10
STARTING_STUCK_THRESHOLD_SECONDS = 15  # WAHA sendiri baru menyerah di percobaan ke-60 (~2 menit) - kita potong jauh lebih awal

_state: Dict[str, Dict[str, Any]] = {}  # session_name -> {"status", "since", "alerted", "stopped"}


async def waha_health_monitor_loop():
    """Jalan selamanya di background sejak startup (lihat on_startup di server.py). Sengaja
    tidak pernah crash total (try/except per-iterasi) supaya monitor ini sendiri tidak jadi
    titik kegagalan baru."""
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        try:
            sessions = await _waha_list_sessions()
            now = time.time()
            for s in sessions:
                name = s.get("name")
                status = s.get("status")
                if not name or not status:
                    continue
                prev = _state.get(name)
                if not prev or prev["status"] != status:
                    was_bermasalah = prev is not None and prev["status"] in ("FAILED", "STARTING")
                    _state[name] = {"status": status, "since": now, "alerted": False, "stopped": False}
                    if status == "WORKING" and was_bermasalah:
                        await _pms_alert_owner(f"✅ Koneksi WhatsApp ({name}) sudah tersambung normal kembali.")
                    continue
                durasi = now - prev["since"]
                if status == "STARTING" and durasi >= STARTING_STUCK_THRESHOLD_SECONDS and not prev["stopped"]:
                    # Macet coba sambung berkali-kali - hentikan PAKSA sebelum WAHA
                    # menghabiskan puluhan percobaan sendiri (lihat catatan modul).
                    prev["stopped"] = True
                    await _waha_call("POST", f"/api/sessions/{name}/stop")
                    logger.warning(f"Session {name} stuck di STARTING {durasi:.0f}s - dihentikan paksa")
                    await _pms_alert_owner(
                        f"🔴 Koneksi WhatsApp ({name}) bermasalah (gagal sambung berulang) - "
                        f"sistem sudah HENTIKAN OTOMATIS supaya tidak terus mencoba (mencegah "
                        f"risiko diblokir WhatsApp). Tunggu beberapa saat dulu, baru scan ulang "
                        f"QR dari halaman AI List - jangan langsung coba berkali-kali."
                    )
                elif status == "FAILED" and not prev["alerted"]:
                    prev["alerted"] = True
                    await _pms_alert_owner(
                        f"🔴 Koneksi WhatsApp ({name}) terputus (status FAILED). Silakan scan "
                        f"ulang QR dari halaman AI List kalau diperlukan - tunggu beberapa saat "
                        f"dulu sebelum mencoba, jangan langsung berulang kali."
                    )
        except Exception as e:
            logger.warning(f"Health monitor error: {e}")
