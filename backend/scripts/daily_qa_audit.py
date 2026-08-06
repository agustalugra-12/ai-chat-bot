"""Audit QA harian percakapan tamu (2026-08-06, permintaan Agus - "cari bug lain di
chat bot ... buat agent yang setiap hari memeriksanya agar tetap aman").

Latar belakang: insiden nyata hari ini - AI bot Pelangi salah mengklaim "semua kamar
dilengkapi AC" ke tamu Lisa Pratiwi (padahal fasilitas asli TIDAK ada AC), lalu ngotot
dgn alasan karangan saat tamu mengoreksi. Guard prompt (guardrail_rules) sudah ditambah
utk kasus AC spesifik, tapi itu reaktif (nunggu laporan). Script ini PROAKTIF: scan
percakapan 24 jam terakhir tiap hari, cari pola KELAS BUG YANG SAMA (klaim fasilitas
yang tidak didukung data asli) plus sinyal lain yang layak ditinjau staf - supaya kalau
ada kasus serupa dgn keyword LAIN (bukan cuma AC), Agus tahu dari digest harian, bukan
baru ketahuan lewat komplain tamu.

SENGAJA heuristik regex (bukan LLM-as-judge) utk deteksi utama - jauh lebih murah/cepat
dijalankan tiap hari drpd re-review tiap percakapan pakai GPT, dan false-positive di
sini cuma berarti staf baca 1 baris ekstra di digest, bukan aksi otomatis apa pun ke
tamu (sama filosofi cost/risk dgn check_stale_bookings.py - beda mekanisme deteksi,
sama prinsip "beri tahu staf, staf yang putuskan").

Reuse _pms_alert_owner (koneksi Telegram owner yang SUDAH ada, dipakai jalur handover/
error lain) utk kirim digest - TIDAK bikin jalur notifikasi baru.

Jalankan manual: `venv/bin/python -m scripts.daily_qa_audit`
"""
import asyncio
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import db  # noqa: E402
from connectors.pms_connector import _pms_alert_owner  # noqa: E402

JENDELA_JAM = 26  # sedikit overlap dari 24 jam murni, cegah kelewat kalau cron telat jalan
AMBANG_HANDOVER_MENGGANTUNG_JAM = 3

# Fasilitas hotel generik yang LAZIM "ditebak" LLM dari pengetahuan umum ttg
# penginapan, tapi TIDAK ada di Pelangi/Harmoni manapun (keduanya homestay/cottage
# sederhana dataran tinggi Bedugul, bukan resort) - kelas bug yang sama dgn insiden AC
# hari ini. "teras"/"balkon" sengaja DIPISAH - Cottage Pelangi memang punya teras asli,
# yang tidak ada itu BALKON PRIBADI spesifik.
FASILITAS_BERISIKO = [
    (r"\bac\b", "AC"),
    (r"kolam renang|swimming pool", "kolam renang"),
    (r"balkon pribadi|balkon privasi|private balcony", "balkon pribadi"),
    (r"\bjacuzzi\b", "jacuzzi"),
    (r"\bsauna\b", "sauna"),
    (r"\bgym\b|fitness center", "gym"),
    (r"\bspa\b", "spa"),
    (r"bathtub|bak mandi rendam", "bathtub"),
    (r"mini ?bar", "minibar"),
    (r"brankas|safe deposit", "brankas"),
]
NEGASI = [
    "tidak", "gak ada", "tanpa", "belum", "bukan", "tidak ada", "tak ada",
    "nggak ada", "maaf.{0,20}tidak",
]
_NEGASI_PAT = re.compile("|".join(NEGASI), re.IGNORECASE)


def _pecah_kalimat(teks: str) -> list:
    return re.split(r"(?<=[.!?\n])\s+", teks)


async def _bangun_data_fasilitas_asli() -> dict:
    """{property_slug: teks gabungan semua nama+deskripsi+fasilitas kamar} - dipakai
    sbg 'ground truth' - kalau keyword berisiko TIDAK muncul di sini utk properti
    terkait, klaim AI soal itu pasti karangan."""
    rooms = await db.rooms.find({}).to_list(200)
    per_property: dict = {}
    for r in rooms:
        slug = r.get("property_slug") or "?"
        blob = " ".join([
            r.get("name", ""), r.get("description", ""),
            " ".join(r.get("facilities") or []),
        ])
        per_property[slug] = per_property.get(slug, "") + " " + blob
    return per_property


async def _bangun_bot_property_map() -> dict:
    bots = await db.ai_bots.find({}).to_list(50)
    return {b["_id"]: b.get("property_slug") for b in bots}


async def cek_klaim_fasilitas_karangan(conv: dict, fasilitas_asli: dict, bot_property: dict) -> list:
    temuan = []
    slug = bot_property.get(conv.get("bot_id"))
    ground_truth = (fasilitas_asli.get(slug) or "").lower()
    for m in conv.get("messages", []):
        if m.get("role") != "assistant":
            continue
        teks = m.get("content") or ""
        for kalimat in _pecah_kalimat(teks):
            kalimat_lower = kalimat.lower()
            for pola, label in FASILITAS_BERISIKO:
                if not re.search(pola, kalimat_lower):
                    continue
                if _NEGASI_PAT.search(kalimat_lower):
                    continue  # AI menyangkal/menolak klaim - ini justru jawaban BENAR
                if slug and re.search(pola, ground_truth):
                    continue  # properti ini MEMANG punya fasilitas ini di data asli
                temuan.append({
                    "jenis": f"klaim fasilitas '{label}' tidak didukung data asli",
                    "kalimat": kalimat.strip()[:200],
                    "timestamp": m.get("timestamp"),
                })
    return temuan


async def cek_handover_menggantung() -> list:
    batas = (datetime.now(timezone.utc) - timedelta(hours=AMBANG_HANDOVER_MENGGANTUNG_JAM)).isoformat()
    convs = await db.conversations.find({
        "status": "waiting_admin", "resolution": "handover", "updated_at": {"$lt": batas},
    }).to_list(100)
    return convs


async def main():
    sejak = (datetime.now(timezone.utc) - timedelta(hours=JENDELA_JAM)).isoformat()
    convs = await db.conversations.find({"updated_at": {"$gte": sejak}}).to_list(2000)
    print(f"[{datetime.now(timezone.utc).isoformat()}] cek {len(convs)} percakapan {JENDELA_JAM} jam terakhir")

    fasilitas_asli = await _bangun_data_fasilitas_asli()
    bot_property = await _bangun_bot_property_map()

    semua_temuan = []
    for conv in convs:
        temuan_conv = await cek_klaim_fasilitas_karangan(conv, fasilitas_asli, bot_property)
        for t in temuan_conv:
            semua_temuan.append({**t, "conv_id": conv["_id"], "guest": conv.get("guest_name") or conv.get("whatsapp") or "?", "whatsapp": conv.get("whatsapp")})

    handover_menggantung = await cek_handover_menggantung()

    print(f"  klaim fasilitas karangan: {len(semua_temuan)}")
    print(f"  handover menggantung >{AMBANG_HANDOVER_MENGGANTUNG_JAM} jam: {len(handover_menggantung)}")

    if not semua_temuan and not handover_menggantung:
        print("  aman, tidak ada temuan - tidak kirim alert (hindari notif capek tiap hari kalau memang tidak ada apa-apa)")
        return

    baris = [f"🔍 Audit QA harian AI Chat Bot ({JENDELA_JAM} jam terakhir, {len(convs)} percakapan dicek):"]
    if semua_temuan:
        baris.append(f"\n⚠️ {len(semua_temuan)} kemungkinan klaim fasilitas yang tidak didukung data asli:")
        for t in semua_temuan[:6]:
            baris.append(f"- {t['jenis']} - tamu {t['guest']}: \"{t['kalimat']}\"")
        if len(semua_temuan) > 6:
            baris.append(f"  (+{len(semua_temuan) - 6} lainnya, cek log server utk detail lengkap)")
    if handover_menggantung:
        baris.append(f"\n⏳ {len(handover_menggantung)} percakapan handover BELUM ditangani >{AMBANG_HANDOVER_MENGGANTUNG_JAM} jam:")
        for c in handover_menggantung[:6]:
            baris.append(f"- {c.get('guest_name') or c.get('whatsapp')}: {c.get('handover_reason') or '(alasan tidak dicatat)'}")

    pesan = "\n".join(baris)
    terkirim = await _pms_alert_owner(pesan)
    print(f"  alert terkirim: {terkirim}")


if __name__ == "__main__":
    asyncio.run(main())
