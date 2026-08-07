"""Audit QA harian LINTAS 3 SISTEM - AI Chat Bot, AI Blog (web-pelangi), KontenPilot AI
(2026-08-06, permintaan Agus - "cari bug lain ... buat agent yang setiap hari
memeriksanya agar tetap aman", lalu diperluas: "aku mau agentmu melakukan pencarian bug
dan langsung memperbaikinya setiap ketemu").

Cakupan auto-fix (persetujuan eksplisit Agus lewat pertanyaan pilihan - "auto-fix yang
aman saja"): HANYA kelas bug yang perbaikannya berbentuk TAMBAH pengetahuan/aturan
defensif (guardrail_rules) berdasar pola yang TERBUKTI nyata dari percakapan tamu asli -
sama seperti fix AC manual hari ini. Ini murni ADDITIVE (nambah larangan, tidak pernah
menghapus/mengubah perilaku lain) & gampang di-undo (tinggal hapus 1 baris dari
guardrail_rules). TIDAK PERNAH auto-fix: perubahan logika kode, threshold/trade-off
(qualitas vs volume, dst), atau apapun yang bisa berdampak ke booking/pembayaran/konten
yang sudah publish - itu SELALU cuma dilaporkan, nunggu direview manual.

Latar belakang bug class yang jadi trigger: AI bot Pelangi salah klaim "semua kamar
dilengkapi AC" ke tamu Lisa Pratiwi, lalu ngotot dgn alasan karangan saat dikoreksi.
Guard reaktif utk AC sudah ditambah manual - script ini PROAKTIF & general utk keyword
fasilitas LAIN yg mungkin muncul ke depan, plus scan sistem lain yg blm pernah diaudit.

SENGAJA baca 3 database LANGSUNG (bukan panggil API masing2 sistem) - ai_chat_bot &
pelangi_web sama2 di mongod localhost:27017 (beda db name saja), KontenPilot di SQLite
lokal /root/kontenpilot-ai/data/kontenpilot.db - semua di server yang sama, jadi tidak
perlu API key/auth lintas sistem baru, cukup baca read-only.

Kirim 1 digest gabungan lewat _pms_alert_owner (jalur Telegram owner yg sudah ada).

Jalankan manual: `venv/bin/python -m scripts.daily_qa_audit`
"""
import asyncio
import re
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from db import db, client as _chatbot_mongo_client  # noqa: E402
from connectors.pms_connector import _pms_alert_owner  # noqa: E402

JENDELA_JAM = 26  # sedikit overlap dari 24 jam murni, cegah kelewat kalau cron telat jalan
AMBANG_HANDOVER_MENGGANTUNG_JAM = 3
AMBANG_KONTENPILOT_STUCK_JAM = 2
KONTENPILOT_DB_PATH = "/root/kontenpilot-ai/data/kontenpilot.db"
db_blog = _chatbot_mongo_client["pelangi_web"]  # mongod SAMA, db_name beda - lihat docstring

# ---------------------------------------------------------------------------
# BAGIAN A: AI Chat Bot - deteksi + AUTO-FIX (kategori aman, lihat docstring)
# ---------------------------------------------------------------------------

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


async def cek_klaim_fasilitas_karangan(convs: list) -> tuple:
    """Return (temuan, bots_map) - bots_map dipakai lagi utk auto-fix di bawah."""
    fasilitas_asli = await _bangun_data_fasilitas_asli()
    bots = {b["_id"]: b for b in await db.ai_bots.find({}).to_list(50)}

    temuan = []
    for conv in convs:
        bot = bots.get(conv.get("bot_id"))
        slug = bot.get("property_slug") if bot else None
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
                        continue  # AI menyangkal - ini jawaban BENAR
                    if slug and re.search(pola, ground_truth):
                        continue  # properti ini MEMANG punya fasilitas ini
                    temuan.append({
                        "label": label, "pola": pola, "bot_id": conv.get("bot_id"),
                        "bot_code": bot.get("code") if bot else "?",
                        "kalimat": kalimat.strip()[:200],
                        "guest": conv.get("guest_name") or conv.get("whatsapp") or "?",
                    })
    return temuan, bots


def _rule_sudah_ada(guardrail_rules: list, label: str) -> bool:
    for r in guardrail_rules:
        if label.lower() in r.lower() and re.search(r"tidak\b", r, re.IGNORECASE):
            return True
    return False


async def auto_fix_guardrail_fasilitas(temuan: list, bots: dict) -> list:
    """AUTO-FIX kategori aman (approved) - tambah 1 baris guardrail per (bot, label)
    yang belum pernah dicover, kalau memang ada bukti nyata (temuan) dari percakapan
    tamu sungguhan. Idempotent - _rule_sudah_ada cegah duplikat run berikutnya."""
    sudah_diperbaiki = []
    unik = {(t["bot_id"], t["label"]) for t in temuan}
    for bot_id, label in unik:
        bot = bots.get(bot_id)
        if not bot:
            continue
        rules = bot.get("guardrail_rules") or []
        if _rule_sudah_ada(rules, label):
            continue
        contoh = next(t["kalimat"] for t in temuan if t["bot_id"] == bot_id and t["label"] == label)
        rule_baru = (
            f"Kamar {bot.get('name', bot.get('code', 'properti ini'))} TIDAK dilengkapi {label} "
            f"(fasilitas asli kamar HANYA sesuai daftar di blok \"FASILITAS & DESKRIPSI KAMAR\" - "
            f"JANGAN PERNAH mengklaim/menjamin ada {label} dalam bentuk apapun. Kalau tamu tanya/"
            f"menagih, jawab JUJUR bahwa tidak tersedia - JANGAN mengarang alasan seperti "
            f"\"mungkin belum tercantum di foto/deskripsi\" (auto-terdeteksi {datetime.now(timezone.utc).date()} "
            f"dari klaim AI nyata ke tamu: \"{contoh}\")."
        )
        rules.append(rule_baru)
        await db.ai_bots.update_one({"_id": bot_id}, {"$set": {"guardrail_rules": rules}})
        sudah_diperbaiki.append({"bot_code": bot.get("code"), "label": label})
    return sudah_diperbaiki


async def cek_handover_menggantung() -> list:
    batas = (datetime.now(timezone.utc) - timedelta(hours=AMBANG_HANDOVER_MENGGANTUNG_JAM)).isoformat()
    kandidat = await db.conversations.find({
        "status": "waiting_admin", "resolution": "handover", "updated_at": {"$lt": batas},
    }).to_list(100)
    # Staf SUDAH balas manual TAPI belum klik "Aktifkan AI Lagi" (2026-08-07, bug nyata
    # SAMA yang dilaporkan Agus di escalate_stale_handovers.py - status "waiting_admin"
    # tetap nempel SELAMANYA sampai staf resume eksplisit, walau staf sudah benar2
    # balas). Kalau pesan TERAKHIR di percakapan dari admin (from_admin=True), itu
    # BUKAN "menggantung" - staf sudah merespons, jangan laporkan sbg belum ditangani.
    return [c for c in kandidat if not (c.get("messages") and c["messages"][-1].get("from_admin"))]


# ---------------------------------------------------------------------------
# BAGIAN B: AI Blog (web-pelangi) - deteksi SAJA (tidak ada kelas fix yang aman
# di-auto - target/threshold semua trade-off kualitas vs volume, butuh keputusan Agus)
# ---------------------------------------------------------------------------

async def cek_ai_blog() -> list:
    catatan = []
    try:
        # Awal hari WITA (UTC+8) dikonversi ke UTC: geser now MAJU 8 jam dulu buat cari
        # tanggal WITA-nya, potong ke tengah malam WITA, baru geser BALIK -8 jam jadi UTC
        # (bukan geser now MUNDUR 8 jam lalu potong tengah malam - itu keliru, hasilnya
        # beda 8 jam dari batas WITA yang benar).
        wita_now = datetime.now(timezone.utc) + timedelta(hours=8)
        wita_midnight = wita_now.replace(hour=0, minute=0, second=0, microsecond=0)
        awal_hari_wita = (wita_midnight - timedelta(hours=8)).isoformat()
        for site in ("pelangi", "harmoni"):
            terbit = await db_blog.blog_posts.count_documents(
                {"site": site, "created_at": {"$gte": awal_hari_wita}}
            )
            belum_dibuat = await db_blog.seo_keywords.count_documents(
                {"site": site, "status": "belum_dibuat"}
            )
            total_pool = await db_blog.seo_keywords.count_documents({"site": site})
            if terbit == 0:
                catatan.append(f"AI Blog {site}: 0 artikel terbit hari ini - cek log/gate kualitas")
            if total_pool > 0 and belum_dibuat < 10:
                catatan.append(
                    f"AI Blog {site}: pool keyword segar tersisa {belum_dibuat} (menipis) - "
                    f"mungkin perlu tambah cluster keyword baru"
                )
    except Exception as e:
        catatan.append(f"AI Blog: gagal cek ({type(e).__name__}: {e})")
    return catatan


# ---------------------------------------------------------------------------
# BAGIAN C: KontenPilot AI - deteksi SAJA (mengubah konten yg sudah/akan publish
# bukan kategori aman utk auto-fix - selalu lapor, staf yang putuskan)
# ---------------------------------------------------------------------------

def cek_kontenpilot() -> list:
    catatan = []
    try:
        conn = sqlite3.connect(KONTENPILOT_DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        batas_stuck_ms = int((datetime.now(timezone.utc) - timedelta(hours=AMBANG_KONTENPILOT_STUCK_JAM)).timestamp() * 1000)

        stuck = cur.execute(
            "SELECT id, brand_id FROM projects WHERE status = 'processing' AND updated_at < ?",
            (batas_stuck_ms,),
        ).fetchall()
        if stuck:
            catatan.append(f"KontenPilot: {len(stuck)} project macet di status 'processing' >{AMBANG_KONTENPILOT_STUCK_JAM} jam (kemungkinan pipeline crash/hang)")

        sejak_ms = int((datetime.now(timezone.utc) - timedelta(hours=JENDELA_JAM)).timestamp() * 1000)
        gagal = cur.execute(
            "SELECT id, error_message FROM projects WHERE status = 'failed' AND updated_at >= ?",
            (sejak_ms,),
        ).fetchall()
        if gagal:
            contoh = gagal[0]["error_message"] or "(tanpa pesan error)"
            catatan.append(f"KontenPilot: {len(gagal)} project gagal {JENDELA_JAM} jam terakhir - contoh: {contoh[:150]}")

        # Regresi cek hashtag cap 5 (fix 2026-08-06) - kalau lolos >5, berarti ada jalur
        # generate yang TIDAK lewat capHashtags(), worth ditinjau kode-nya.
        recent = cur.execute(
            "SELECT id, generated_hashtags FROM projects WHERE updated_at >= ? AND generated_hashtags IS NOT NULL",
            (sejak_ms,),
        ).fetchall()
        lebih_dari_5 = 0
        for r in recent:
            try:
                import json
                tags = json.loads(r["generated_hashtags"])
                if isinstance(tags, list) and len(tags) > 5:
                    lebih_dari_5 += 1
            except Exception:
                continue
        if lebih_dari_5:
            catatan.append(f"KontenPilot: {lebih_dari_5} project hashtag-nya >5 (regresi dari fix cap hashtag) - cek capHashtags()")

        conn.close()
    except Exception as e:
        catatan.append(f"KontenPilot: gagal cek ({type(e).__name__}: {e})")
    return catatan


# ---------------------------------------------------------------------------
# Orkestrasi
# ---------------------------------------------------------------------------

async def main():
    sejak = (datetime.now(timezone.utc) - timedelta(hours=JENDELA_JAM)).isoformat()
    convs = await db.conversations.find({"updated_at": {"$gte": sejak}}).to_list(2000)
    print(f"[{datetime.now(timezone.utc).isoformat()}] Audit QA harian - 3 sistem")
    print(f"  [chat bot] cek {len(convs)} percakapan {JENDELA_JAM} jam terakhir")

    temuan_fasilitas, bots = await cek_klaim_fasilitas_karangan(convs)
    auto_fixed = await auto_fix_guardrail_fasilitas(temuan_fasilitas, bots)
    handover_menggantung = await cek_handover_menggantung()

    print(f"  [chat bot] klaim fasilitas karangan: {len(temuan_fasilitas)} (auto-fix baru: {len(auto_fixed)})")
    print(f"  [chat bot] handover menggantung >{AMBANG_HANDOVER_MENGGANTUNG_JAM} jam: {len(handover_menggantung)}")

    catatan_blog = await cek_ai_blog()
    print(f"  [AI Blog] catatan: {len(catatan_blog)}")

    catatan_kontenpilot = cek_kontenpilot()
    print(f"  [KontenPilot] catatan: {len(catatan_kontenpilot)}")

    if not (temuan_fasilitas or handover_menggantung or catatan_blog or catatan_kontenpilot or auto_fixed):
        print("  semua sistem aman, tidak ada temuan - tidak kirim alert")
        return

    baris = [f"🔍 Audit QA harian - 3 sistem ({JENDELA_JAM} jam terakhir)"]

    if auto_fixed:
        baris.append(f"\n✅ AUTO-DIPERBAIKI ({len(auto_fixed)}) - guardrail baru ditambahkan (kategori aman, tidak perlu review):")
        for f in auto_fixed:
            baris.append(f"- Bot {f['bot_code']}: tambah larangan klaim '{f['label']}'")

    sisa_belum_dicover = [t for t in temuan_fasilitas if not any(
        f["bot_code"] == t["bot_code"] and f["label"] == t["label"] for f in auto_fixed
    )]
    if sisa_belum_dicover:
        baris.append(f"\n⚠️ {len(sisa_belum_dicover)} klaim fasilitas lain (sudah tercover guardrail lama, tetap muncul - PERLU DITINJAU MANUAL, bukan auto-fix):")
        for t in sisa_belum_dicover[:5]:
            baris.append(f"- {t['label']} - tamu {t['guest']}: \"{t['kalimat']}\"")

    if handover_menggantung:
        baris.append(f"\n⏳ [Chat Bot] {len(handover_menggantung)} percakapan handover BELUM ditangani >{AMBANG_HANDOVER_MENGGANTUNG_JAM} jam (PERLU DITINJAU MANUAL):")
        for c in handover_menggantung[:6]:
            baris.append(f"- {c.get('guest_name') or c.get('whatsapp')}: {c.get('handover_reason') or '(alasan tidak dicatat)'}")

    if catatan_blog:
        baris.append("\n📝 [AI Blog] (PERLU DITINJAU MANUAL):")
        for c in catatan_blog:
            baris.append(f"- {c}")

    if catatan_kontenpilot:
        baris.append("\n🎬 [KontenPilot] (PERLU DITINJAU MANUAL):")
        for c in catatan_kontenpilot:
            baris.append(f"- {c}")

    pesan = "\n".join(baris)
    terkirim = await _pms_alert_owner(pesan)
    print(f"  alert terkirim: {terkirim}")


if __name__ == "__main__":
    asyncio.run(main())
