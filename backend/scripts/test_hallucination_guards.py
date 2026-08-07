"""Tes regresi jaring pengaman AI Chat Bot (2026-08-04, permintaan Agus - "solusi terbaik
karena guard kadang melakukan kesalahan"; diperluas 2026-08-07 jadi gerbang deploy resmi -
Modul 19 PRD "AI Self-Healing & Bug Prevention" usulan Agus).

Dua jenis tes di sini, SENGAJA dipisah karena beda kebutuhan:

1. **Skenario LIVE** (`skenario_*`, async, lewat Chat Simulator/`_run_chat_turn`) - untuk
   bug yang akar masalahnya ada di PERILAKU MODEL (prompt tidak selalu dipatuhi), bukan di
   logika kode murni. Tiap skenario mengambil data ASLI langsung dari PMS (ground truth)
   SEBELUM menjalankan chat, lalu bandingkan balasan AKHIR AI dengan angka asli itu - PASS
   kalau cocok, FAIL kalau tidak ada angka sama sekali atau beda dari data asli. Ini genuinely
   butuh panggilan OpenAI asli (tidak bisa disimulasikan murah), jadi jumlahnya SENGAJA
   dijaga sedikit & bernilai tinggi - bukan ratusan skenario sintetis (lihat PRD asli Agus
   Modul 21/22 yang minta 1000-5000 percakapan/malam - ditolak, lihat diskusi 2026-08-07:
   traffic asli bot ini cuma ~39 percakapan/24 jam, biaya segitu banyak tidak proporsional
   untuk skala bisnis ini).
2. **Unit test murni** (`test_*`, sync, tanpa DB/LLM sama sekali) - untuk guard yang berupa
   LOGIKA KODE murni (regex deteksi + substitusi, spt Contradiction Checker) - lebih cepat,
   lebih murah, lebih deterministik daripada mencoba memancing LLM berperilaku salah secara
   konsisten di tiap run. Menguji fungsi guard itu sendiri terhadap regresi (skenario nyata
   pernah terjadi: guard baru tanpa sengaja merusak guard lama yang sudah ada).

Tiap kali salah satu guard di server.py diubah, jalankan skrip ini SEBELUM lapor selesai ke
Agus / SEBELUM restart ai-chat-bot-backend.service - WAJIB, bukan opsional (lihat CLAUDE.md
di root repo ini). Semua percakapan tes LIVE dihapus otomatis di akhir (bukan tersisa di DB).

Jalankan manual: `venv/bin/python -m scripts.test_hallucination_guards`
"""
import asyncio
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import (  # noqa: E402
    db, _run_chat_turn, _tool_check_availability, _tool_preview_booking, _cek_kontradiksi_total,
    _deteksi_loop_kirim_beruntun, LOOP_DETECTOR_THRESHOLD, LOOP_DETECTOR_WINDOW_MINUTES,
)
from connectors.pms_connector import _pms_http_retry, _sync_business_rules  # noqa: E402

import httpx

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


async def skenario_harga_sarapan() -> tuple:
    """Bug asli (2026-08-07, tamu Riyan Sumardika): tamu minta Menginap "dengan sarapan", AI
    benar sebut harga termasuk sarapan di kalimat TAPI parameter dengan_sarapan tidak diisi
    ke tool - booking tersimpan pakai tarif TANPA sarapan yang lebih murah, beda dari yang
    dijanjikan. Regresi kalau AI tidak menyebut angka tarif+sarapan asli dari PMS sama sekali."""
    besok = (datetime.now(timezone.utc) + timedelta(hours=7, days=1)).strftime("%Y-%m-%d")
    lusa = (datetime.now(timezone.utc) + timedelta(hours=7, days=2)).strftime("%Y-%m-%d")
    wa = _wa_unik()
    bot = await db.ai_bots.find_one({"_id": BOT_ID_PELANGI})
    fake_conv = {"_pms_api_key_override": (bot or {}).get("pms_property_api_key"), "whatsapp": wa}
    asli = await _tool_preview_booking({
        "whatsapp": wa, "tipe": "menginap", "room_tipe": "Standard",
        "tanggal_checkin": besok, "tanggal_checkout": lusa, "dengan_sarapan": True,
    }, fake_conv)
    if not asli.get("ok") or not asli.get("rincian_harga"):
        return ("harga_sarapan", None, f"SKIP (preview_booking asli gagal: {asli.get('error')})")
    rincian = asli["rincian_harga"]
    # Cocokkan angka mentah ("175000") MAUPUN yang diformat titik-ribuan ala Indonesia
    # ("175.000") - AI wajar menulis salah satu gaya, bug nyata ditemukan di sini SEBELUM
    # perbaikan: skenario ini sendiri sempat FALSE FAIL krn cuma cek angka mentah padahal
    # balasan AI yang BENAR pakai format "Rp175.000".
    angka_asli = set()
    for v in (rincian.get("tarif_kamar"), rincian.get("total")):
        if v:
            angka_asli.add(str(v))
            angka_asli.add(f"{v:,}".replace(",", "."))

    session = f"test-sarapan-{uuid.uuid4().hex[:8]}"
    r = await _run_chat_turn(
        session, f"kamar standard menginap tanggal {besok} untuk 2 orang, kalau dengan sarapan berapa ya kak?",
        "Test Regresi", wa, BOT_ID_PELANGI, None, channel="simulator",
    )
    reply = r.get("reply") or ""
    cocok = any(a in reply for a in angka_asli)
    return ("harga_sarapan", session, "PASS" if cocok else f"FAIL - balasan: {reply!r}, angka asli (dgn sarapan): {angka_asli}")


async def skenario_extra_bed_standard_ditolak() -> tuple:
    """Bug asli (2026-07-24, ditemukan lewat Chat Simulator): meski prompt tegas "extra bed
    HANYA Cottage, Standard TIDAK BISA", model tetap mengarang kebijakan & menyetujui extra
    bed untuk Standard lengkap dengan harga karangan sendiri. Regresi kalau AI menyetujui/
    memberi harga extra bed untuk kamar Standard alih-alih menolak."""
    session = f"test-extrabed-std-{uuid.uuid4().hex[:8]}"
    wa = _wa_unik()
    r = await _run_chat_turn(session, "kamar standard bisa nambah extra bed ga kak?", "Test Regresi", wa, BOT_ID_PELANGI, None, channel="simulator")
    reply = (r.get("reply") or "").lower()
    menolak = bool(re.search(
        r"(tidak|nggak|blm|belum|maaf)[^.\n]{0,25}(bisa|boleh|tersedia|ada)[^.\n]{0,20}extra\s*bed"
        r"|extra\s*bed[^.\n]{0,25}(tidak|nggak|ga)\s*(bisa|boleh|tersedia)"
        r"|(hanya|cuma)[^.\n]{0,20}cottage[^.\n]{0,20}extra\s*bed"
        r"|extra\s*bed[^.\n]{0,20}(hanya|cuma)[^.\n]{0,20}cottage",
        reply,
    ))
    menyetujui_dgn_harga = bool(re.search(r"extra\s*bed[^.\n]{0,40}rp\s?[\d.,]+|rp\s?[\d.,]+[^.\n]{0,40}extra\s*bed", reply))
    status = "PASS" if (menolak and not menyetujui_dgn_harga) else f"FAIL - AI tidak jelas menolak (menolak={menolak}) atau malah kasih harga (menyetujui_dgn_harga={menyetujui_dgn_harga}): {reply!r}"
    return ("extra_bed_standard_ditolak", session, status)


async def skenario_business_rules_isolasi_properti() -> tuple:
    """Bug asli (2026-08-07, Modul 7 PRD ASHB "Memory Validator"): `business_rules_cache`
    sebelumnya SAMA SEKALI tidak ditandai per-properti - sync 1 properti menimpa cache utk
    SEMUA properti, kedua bot Pelangi & Harmoni sama-sama membaca DP%/kebijakan pembatalan/
    jam checkout dari properti yang SAMA sebagai "ATURAN BISNIS WAJIB DIIKUTI". Regresi
    kalau sync 1 properti (test_scope_a) diam-diam menghapus/mengganggu cache properti
    LAIN (test_scope_b) yang tidak sedang di-sync. Pakai label properti PALSU (bukan
    "pelangi"/"harmoni" asli) supaya tidak menyentuh data produksi sama sekali."""
    label_a, label_b = "test_scope_a", "test_scope_b"
    try:
        hasil_a = await _sync_business_rules(property_slug=label_a)
        hasil_b = await _sync_business_rules(property_slug=label_b)
        if not hasil_a.get("ok") or not hasil_b.get("ok"):
            return ("business_rules_isolasi_properti", None, f"SKIP (sync asli gagal: a={hasil_a}, b={hasil_b})")
        count_b_sebelum = await db.business_rules_cache.count_documents({"property_slug": label_b})
        # Sync ULANG label_a - TIDAK BOLEH mengganggu dokumen label_b sama sekali.
        await _sync_business_rules(property_slug=label_a)
        count_b_sesudah = await db.business_rules_cache.count_documents({"property_slug": label_b})
        count_a = await db.business_rules_cache.count_documents({"property_slug": label_a})
        ok = count_a > 0 and count_b_sesudah > 0 and count_b_sebelum == count_b_sesudah
        status = "PASS" if ok else (
            f"FAIL - isolasi bocor: count_a={count_a}, count_b_sebelum={count_b_sebelum}, count_b_sesudah={count_b_sesudah}"
        )
        return ("business_rules_isolasi_properti", None, status)
    finally:
        await db.business_rules_cache.delete_many({"property_slug": {"$in": [label_a, label_b]}})


async def skenario_multi_tipe_kamar() -> tuple:
    """Bug asli: tamu tanya ketersediaan 2 tipe kamar sekaligus dalam 1 pertanyaan, AI cuma
    jawab 1 dari 2 tipe yang ditanya. Regresi kalau balasan akhir tidak menyebut status
    KEDUA tipe kamar yang ditanyakan."""
    besok = (datetime.now(timezone.utc) + timedelta(hours=7, days=1)).strftime("%Y-%m-%d")
    asli = await _hasil_bersih(BOT_ID_PELANGI, besok)
    if len(asli) < 2:
        return ("multi_tipe_kamar", None, "SKIP (properti ini tidak punya >=2 tipe kamar utk dibandingkan)")

    session = f"test-multitipe-{uuid.uuid4().hex[:8]}"
    wa = _wa_unik()
    r = await _run_chat_turn(session, "besok ada kamar Standard atau Cottage yang kosong kak?", "Test Regresi", wa, BOT_ID_PELANGI, None, channel="simulator")
    reply = (r.get("reply") or "").lower()
    sebut_standard, sebut_cottage = "standard" in reply, "cottage" in reply
    status = "PASS" if (sebut_standard and sebut_cottage) else (
        f"FAIL - balasan cuma sebut sebagian tipe (standard={sebut_standard}, cottage={sebut_cottage}): {reply!r}"
    )
    return ("multi_tipe_kamar", session, status)


# ---------------------------------------------------------------------------
# Unit test murni (sync, TANPA DB/LLM) - Contradiction Checker (Modul 10 PRD ASHB)
# ---------------------------------------------------------------------------

def test_kontradiksi_total_dikoreksi() -> tuple:
    messages = [{"role": "assistant", "content": "Ringkasan booking:\n💰 Harga kamar: Rp150.000\n💳 Service 3%\n*Total: Rp154.500*"}]
    hasil = _cek_kontradiksi_total("Baik kak, *Total: Rp200.000* ya untuk booking ini.", "some_other_tool", messages)
    ok = "Rp154.500" in hasil and "Rp200.000" not in hasil
    return ("kontradiksi_total_dikoreksi", "PASS" if ok else f"FAIL - hasil: {hasil!r}")


def test_kontradiksi_total_sama_tidak_diubah() -> tuple:
    messages = [{"role": "assistant", "content": "*Total: Rp154.500*"}]
    teks = "Baik, *Total: Rp154.500* sudah dikonfirmasi."
    hasil = _cek_kontradiksi_total(teks, None, messages)
    return ("kontradiksi_total_sama_tidak_diubah", "PASS" if hasil == teks else f"FAIL - hasil berubah padahal totalnya sama: {hasil!r}")


def test_kontradiksi_total_skip_saat_preview_booking() -> tuple:
    """Kalau preview_booking/create_booking BARU dipanggil giliran ini, angka baru itu SAH
    (mis. tamu tambah extra bed) - guard TIDAK BOLEH menimpanya dengan angka lama."""
    messages = [{"role": "assistant", "content": "*Total: Rp154.500*"}]
    teks = "Baik, *Total: Rp200.000* (harga baru setelah tambah extra bed)."
    hasil = _cek_kontradiksi_total(teks, "preview_booking", messages)
    return ("kontradiksi_total_skip_saat_preview_booking", "PASS" if hasil == teks else f"FAIL - guard salah trigger padahal preview_booking baru dipanggil: {hasil!r}")


def test_kontradiksi_total_tanpa_riwayat_tidak_diubah() -> tuple:
    teks = "Baik, *Total: Rp200.000* untuk booking Kakak."
    hasil = _cek_kontradiksi_total(teks, None, [])
    return ("kontradiksi_total_tanpa_riwayat_tidak_diubah", "PASS" if hasil == teks else f"FAIL - guard salah trigger tanpa riwayat sama sekali: {hasil!r}")


def test_kontradiksi_total_tanpa_klaim_total_tidak_diubah() -> tuple:
    messages = [{"role": "assistant", "content": "*Total: Rp154.500*"}]
    teks = "Baik kak, kamar Standard masih tersedia untuk tanggal itu."
    hasil = _cek_kontradiksi_total(teks, None, messages)
    return ("kontradiksi_total_tanpa_klaim_total_tidak_diubah", "PASS" if hasil == teks else f"FAIL - guard salah trigger padahal tidak ada klaim Total sama sekali: {hasil!r}")


# ---------------------------------------------------------------------------
# Unit test murni - Loop Detector (Modul 8 PRD ASHB, insiden asli 2026-08-01 bot Pelangi/
# Harmoni saling kirim pesan tanpa henti)
# ---------------------------------------------------------------------------

def _pesan_assistant(menit_lalu: float) -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=menit_lalu)).isoformat()
    return {"role": "assistant", "content": "halo", "timestamp": ts}


def test_loop_terdeteksi_saat_melewati_ambang() -> tuple:
    messages = [_pesan_assistant(m * 0.1) for m in range(LOOP_DETECTOR_THRESHOLD)]
    hasil = _deteksi_loop_kirim_beruntun(messages)
    return ("loop_terdeteksi_saat_melewati_ambang", "PASS" if hasil is True else f"FAIL - {LOOP_DETECTOR_THRESHOLD} balasan dalam jendela waktu harusnya terdeteksi loop, hasil={hasil}")


def test_loop_tidak_terdeteksi_di_bawah_ambang() -> tuple:
    messages = [_pesan_assistant(m * 0.1) for m in range(LOOP_DETECTOR_THRESHOLD - 1)]
    hasil = _deteksi_loop_kirim_beruntun(messages)
    return ("loop_tidak_terdeteksi_di_bawah_ambang", "PASS" if hasil is False else f"FAIL - {LOOP_DETECTOR_THRESHOLD - 1} balasan (di bawah ambang) salah terdeteksi loop, hasil={hasil}")


def test_loop_tidak_terdeteksi_kalau_pesan_lama() -> tuple:
    """Percakapan wajar berlangsung LAMA (banyak balasan total) tapi TERSEBAR di luar
    jendela waktu - bukan loop, jangan salah tangkap."""
    messages = [_pesan_assistant(LOOP_DETECTOR_WINDOW_MINUTES + 5 + m) for m in range(LOOP_DETECTOR_THRESHOLD + 5)]
    hasil = _deteksi_loop_kirim_beruntun(messages)
    return ("loop_tidak_terdeteksi_kalau_pesan_lama", "PASS" if hasil is False else f"FAIL - pesan lama di luar jendela waktu salah terdeteksi loop, hasil={hasil}")


def test_loop_mengabaikan_pesan_user() -> tuple:
    """Pesan role=user (tamu asli membalas cepat) TIDAK BOLEH ikut dihitung - loop
    detector khusus soal balasan OTOMATIS kita, bukan seberapa aktif tamu chat."""
    messages = [{"role": "user", "content": "halo", "timestamp": (datetime.now(timezone.utc) - timedelta(seconds=i)).isoformat()} for i in range(20)]
    hasil = _deteksi_loop_kirim_beruntun(messages)
    return ("loop_mengabaikan_pesan_user", "PASS" if hasil is False else f"FAIL - pesan tamu (role=user) salah ikut dihitung sbg loop, hasil={hasil}")


# ---------------------------------------------------------------------------
# Unit test murni - Retry Engine (Modul 14 PRD ASHB)
# ---------------------------------------------------------------------------

async def test_retry_berhasil_setelah_gagal_transient() -> tuple:
    calls = {"n": 0}
    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectTimeout("boom")
        return "SUCCESS"
    hasil = await _pms_http_retry(flaky, max_retries=2, backoff_sec=0.01)
    ok = hasil == "SUCCESS" and calls["n"] == 3
    return ("retry_berhasil_setelah_gagal_transient", "PASS" if ok else f"FAIL - hasil={hasil!r}, calls={calls['n']}")


async def test_retry_menyerah_setelah_max_percobaan() -> tuple:
    calls = {"n": 0}
    async def always_fails():
        calls["n"] += 1
        raise httpx.ConnectError("down")
    try:
        await _pms_http_retry(always_fails, max_retries=2, backoff_sec=0.01)
        return ("retry_menyerah_setelah_max_percobaan", f"FAIL - harusnya raise httpx.ConnectError, malah sukses (calls={calls['n']})")
    except httpx.ConnectError:
        ok = calls["n"] == 3  # 1 percobaan awal + 2 retry
        return ("retry_menyerah_setelah_max_percobaan", "PASS" if ok else f"FAIL - jumlah percobaan salah, calls={calls['n']} (harusnya 3)")


async def test_retry_tidak_retry_error_non_transient() -> tuple:
    """Error yang BUKAN masalah jaringan (mis. bug parsing respons) tidak boleh di-retry -
    retry cuma untuk kegagalan transient, mengulang error non-transient cuma buang waktu."""
    calls = {"n": 0}
    async def bad_parse():
        calls["n"] += 1
        raise ValueError("bukan masalah jaringan")
    try:
        await _pms_http_retry(bad_parse, max_retries=2, backoff_sec=0.01)
        return ("retry_tidak_retry_error_non_transient", f"FAIL - harusnya raise ValueError, malah sukses (calls={calls['n']})")
    except ValueError:
        ok = calls["n"] == 1  # TIDAK boleh retry - langsung gagal di percobaan pertama
        return ("retry_tidak_retry_error_non_transient", "PASS" if ok else f"FAIL - seharusnya TIDAK retry error non-transient, tapi calls={calls['n']}")


async def main():
    skenario_list = [
        skenario_besok_jumlah_kamar,
        skenario_hari_nama_salah_hitung,
        skenario_konsistensi_dar,
        skenario_harga_sarapan,
        skenario_extra_bed_standard_ditolak,
        skenario_multi_tipe_kamar,
        skenario_business_rules_isolasi_properti,
    ]
    unit_test_list = [
        test_kontradiksi_total_dikoreksi,
        test_kontradiksi_total_sama_tidak_diubah,
        test_kontradiksi_total_skip_saat_preview_booking,
        test_kontradiksi_total_tanpa_riwayat_tidak_diubah,
        test_kontradiksi_total_tanpa_klaim_total_tidak_diubah,
        test_loop_terdeteksi_saat_melewati_ambang,
        test_loop_tidak_terdeteksi_di_bawah_ambang,
        test_loop_tidak_terdeteksi_kalau_pesan_lama,
        test_loop_mengabaikan_pesan_user,
        test_retry_berhasil_setelah_gagal_transient,
        test_retry_menyerah_setelah_max_percobaan,
        test_retry_tidak_retry_error_non_transient,
    ]
    sesi_test = []
    hasil_semua = []

    print("--- Unit test (tanpa DB/LLM sungguhan - sebagian sync, sebagian async murni) ---")
    for fn in unit_test_list:
        hasil_fn = fn()
        nama, status = await hasil_fn if asyncio.iscoroutine(hasil_fn) else hasil_fn
        hasil_semua.append((nama, status))
        print(f"[{status.split(' ')[0]}] {nama}: {status}")

    print("\n--- Skenario LIVE (lewat Chat Simulator, panggilan OpenAI asli) ---")
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
    skip = [h for h in hasil_semua if h[1].startswith("SKIP")]
    print(f"\n=== RINGKASAN: {len(hasil_semua) - len(gagal) - len(skip)}/{len(hasil_semua)} PASS"
          + (f", {len(skip)} SKIP" if skip else "") + " ===")
    if gagal:
        print("ADA REGRESI - jangan deploy sebelum ini diperbaiki:")
        for nama, status in gagal:
            print(f"  - {nama}: {status}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
