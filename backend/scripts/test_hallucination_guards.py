"""Tes regresi jaring pengaman ketersediaan kamar (2026-08-04, permintaan Agus - "solusi
terbaik karena guard kadang melakukan kesalahan").

Kumpulan skenario yang PERNAH benar-benar bermasalah (ditemukan lewat laporan Agus/audit
sesi ini), dijalankan lewat Chat Simulator (`_run_chat_turn`, bukan WhatsApp asli) - tiap
kali salah satu guard di server.py diubah, jalankan skrip ini SEBELUM lapor selesai ke
Agus, supaya perbaikan baru tidak diam-diam merusak perbaikan lama (persis masalah yang
baru saja terjadi: guard besok/lusa dibangun hari ini TIDAK sengaja menyentuh guard day-
use-klaim-ketersediaan yang lebih lama, yang ternyata sudah lama punya bug "tipe":
"day_use" - baru ketahuan lewat laporan tamu Dar).

Tiap skenario mengambil data ASLI langsung dari PMS (ground truth) SEBELUM menjalankan
chat, lalu bandingkan balasan AKHIR AI dengan angka asli itu - PASS kalau angka yang
disebut AI cocok, FAIL kalau tidak ada angka sama sekali atau angkanya beda dari data
asli. Semua percakapan tes dihapus otomatis di akhir (bukan tersisa di database).

Jalankan manual: `venv/bin/python -m scripts.test_hallucination_guards`
"""
import asyncio
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import db, _run_chat_turn, _tool_check_availability  # noqa: E402

BOT_ID_HARMONI = "d7ca2ed3-e940-4d86-b032-ec5bd203a735"
BOT_ID_PELANGI = "c063c2bc-4f93-4304-a3f6-6a2b36c7e3c5"


def _wa_unik() -> str:
    return "6281299" + uuid.uuid4().hex[:6]


async def _hasil_bersih(bot_id: str, tanggal: str, tipe: str = None) -> dict:
    """Data ASLI langsung dari PMS (ground truth) - dipakai bandingkan balasan AI."""
    fake_conv = {"_pms_api_key_override": None, "_id": "test"}
    bot = await db.ai_bots.find_one({"_id": bot_id})
    if bot:
        fake_conv["_pms_api_key_override"] = bot.get("pms_property_api_key")
    args = {"tanggal_checkin": tanggal}
    if tipe:
        args["tipe"] = tipe
    hasil = await _tool_check_availability(args, fake_conv)
    return {r["tipe"]: r["kamar_tersedia"] for r in hasil.get("result", [])}


async def skenario_besok_jumlah_kamar() -> tuple:
    """Bug asli (2026-08-04, tamu 'Ajus'): AI jawab jumlah kamar besok pakai data hari
    ini tanpa cek ulang - regresi kalau balasan akhir TIDAK menyebut angka asli besok."""
    besok = (datetime.now(timezone.utc) + timedelta(hours=7, days=1)).strftime("%Y-%m-%d")
    asli = await _hasil_bersih(BOT_ID_HARMONI, besok)
    if not asli:
        return ("besok_jumlah_kamar", None, "SKIP (tidak ada data ketersediaan besok utk dibandingkan)")

    session = f"test-besok-jumlah-{uuid.uuid4().hex[:8]}"
    wa = _wa_unik()
    r = await _run_chat_turn(session, "Selamat sore kak, untuk besok apakah masih ada room kosong?",
                              "Test Regresi", wa, BOT_ID_HARMONI, None, channel="simulator")
    reply = r.get("reply") or ""
    cocok = any(str(v) in reply for v in asli.values())
    return ("besok_jumlah_kamar", session, "PASS" if cocok else f"FAIL - balasan: {reply!r}, data asli: {asli}")


async def skenario_hari_nama_salah_hitung() -> tuple:
    """Bug asli (2026-08-04): model salah hitung tanggal dari nama hari ('hari minggu'
    dari hari Selasa dihitung ke hari Jumat) - regresi kalau tanggal yg disebut AI SALAH."""
    now_wib = datetime.now(timezone.utc) + timedelta(hours=7)
    hari_diminta = 6  # Minggu (index di _HARI_INDO: senin=0..minggu=6)
    selisih = (hari_diminta - now_wib.weekday()) % 7 or 7
    minggu_asli = (now_wib.date() + timedelta(days=selisih)).strftime("%Y-%m-%d")
    tgl_readable = str(int(minggu_asli.split("-")[2]))  # tanggal (hari) saja, mis. "9"

    session = f"test-hari-minggu-{uuid.uuid4().hex[:8]}"
    wa = _wa_unik()
    r = await _run_chat_turn(session, "kalau hari minggu ada kamar cottage kosong ga kak?",
                              "Test Regresi", wa, BOT_ID_HARMONI, None, channel="simulator")
    reply = r.get("reply") or ""
    cocok = minggu_asli in reply or f" {tgl_readable} " in f" {reply} " or f" {tgl_readable}," in reply
    return ("hari_nama_salah_hitung", session,
            "PASS" if cocok else f"FAIL - balasan: {reply!r}, tanggal Minggu asli: {minggu_asli}")


async def skenario_konsistensi_dar() -> tuple:
    """Bug asli (2026-08-04, tamu 'Dar'): AI kasih jawaban SALING BERTENTANGAN soal
    Cottage/Standard hari Minggu dalam percakapan yang sama - regresi kalau status
    tersedia/penuh utk Standard di jawaban akhir BERTENTANGAN dgn data asli."""
    now_wib = datetime.now(timezone.utc) + timedelta(hours=7)
    selisih = (6 - now_wib.weekday()) % 7 or 7
    minggu = (now_wib.date() + timedelta(days=selisih)).strftime("%Y-%m-%d")
    asli = await _hasil_bersih(BOT_ID_PELANGI, minggu)
    standard_kosong = asli.get("Standard", 0) > 0
    if "Standard" not in asli:
        return ("konsistensi_dar", None, "SKIP (tipe Standard tidak ada di data properti ini)")

    session = f"test-dar-{uuid.uuid4().hex[:8]}"
    wa = _wa_unik()
    await _run_chat_turn(session, "Untuk hari minggu ada kosong kamarnya kak", "Test Regresi", wa, BOT_ID_PELANGI, None, channel="simulator")
    await _run_chat_turn(session, "Jam 9/10 an kak", "Test Regresi", wa, BOT_ID_PELANGI, None, channel="simulator")
    r3 = await _run_chat_turn(session, "Yg standard ready ya kak?", "Test Regresi", wa, BOT_ID_PELANGI, None, channel="simulator")
    reply = (r3.get("reply") or "").lower()
    klaim_penuh = bool(re.search(r"standard[^.\n]{0,40}(penuh|terisi|tidak\s+tersedia)", reply))
    klaim_tersedia = bool(re.search(r"standard[^.\n]{0,40}tersedia", reply)) or bool(re.search(r"tersedia[^.\n]{0,40}standard", reply))

    if standard_kosong and klaim_penuh:
        status = f"FAIL - AI bilang Standard penuh, padahal data asli kamar_tersedia={asli['Standard']}"
    elif not standard_kosong and klaim_tersedia:
        status = f"FAIL - AI bilang Standard tersedia, padahal data asli kamar_tersedia=0"
    else:
        status = "PASS"
    return ("konsistensi_dar", session, status)


async def main():
    skenario_list = [
        skenario_besok_jumlah_kamar,
        skenario_hari_nama_salah_hitung,
        skenario_konsistensi_dar,
    ]
    sesi_test = []
    hasil_semua = []
    for fn in skenario_list:
        nama, session, status = await fn()
        if session:
            sesi_test.append(session)
        hasil_semua.append((nama, status))
        print(f"[{status.split(' ')[0]}] {nama}: {status}")

    if sesi_test:
        r = await db.conversations.delete_many({"session_id": {"$in": sesi_test}})
        print(f"\ncleanup: {r.deleted_count} percakapan tes dihapus")

    gagal = [h for h in hasil_semua if h[1].startswith("FAIL")]
    print(f"\n=== RINGKASAN: {len(hasil_semua) - len(gagal)}/{len(hasil_semua)} PASS ===")
    if gagal:
        print("ADA REGRESI - jangan deploy sebelum ini diperbaiki:")
        for nama, status in gagal:
            print(f"  - {nama}: {status}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
