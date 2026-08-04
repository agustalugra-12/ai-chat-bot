"""Cek percakapan dengan booking yang "menggantung" (2026-08-04, permintaan Agus, PRD
"deteksi & koreksi otomatis") - temuan nyata dari audit chat "Artiniiyaa_": tamu dapat
ringkasan booking lengkap (harga/tipe kamar) tapi TIDAK PERNAH konfirmasi eksplisit,
percakapan lanjut ke topik lain lalu diam total - tidak ada mekanisme apa pun yang
memberi tahu staf ada booking yang belum selesai. Kali itu aman karena staf sigap
menangani tamu sbg walk-in begitu dia sampai di lokasi, tapi itu keberuntungan, bukan
jaminan sistem.

Sinyal yang dipakai (SUDAH ADA di conversation doc, tidak perlu tracking baru):
`booking_draft` terisi (diisi dari args preview_booking/create_booking - lihat
_run_chat_turn_locked di server.py) TAPI `booking_created` belum True, `status` masih
"active" (belum ter-eskalasi jalur lain), & `updated_at` sudah lewat AMBANG_MENIT tanpa
aktivitas apa pun (tamu maupun AI diam).

SENGAJA bikin TIKET ke staf (reuse _pms_buat_tiket, sama seperti create_service_request/
catat_kedatangan_tamu/catat_klaim_stamp_member), BUKAN suruh AI follow-up otomatis ke
tamu - Agus sudah pernah tegas menolak pola "AI mengejar-ngejar tamu yang masih
pertimbangkan" (lihat larangan resend-link otomatis di TOOL_DOCS). Staf yang putuskan
sendiri apakah perlu follow-up, sesuai konteks yang staf tahu tapi sistem tidak (mis.
tamu mungkin sudah batal niat, atau sudah booking lewat jalur lain).

`stale_booking_notified` (flag baru di conversation doc) mencegah tiket duplikat kalau
draft yang sama tetap menggantung di run berikutnya - dilonggarkan lagi (reset) kalau
booking_draft berubah isinya (tamu mulai obrolan booking baru), lihat logic di bawah.

Dijalankan cron tiap 30 menit (lihat crontab -l) - dampak salah paling buruk cuma staf
dapat 1 tiket informasional yang ternyata tidak perlu ditindaklanjuti, BUKAN aksi
mengubah data booking/kirim pesan ke tamu apa pun.
"""
import asyncio
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import db  # noqa: E402
from connectors.pms_connector import _pms_buat_tiket  # noqa: E402

AMBANG_MENIT = 45


async def main():
    batas_waktu = (datetime.now(timezone.utc) - timedelta(minutes=AMBANG_MENIT)).isoformat()
    kandidat = await db.conversations.find({
        "status": "active",
        "booking_created": {"$ne": True},
        "booking_draft": {"$exists": True, "$ne": {}},
        "updated_at": {"$lt": batas_waktu},
    }).to_list(200)

    print(f"[{datetime.now(timezone.utc).isoformat()}] kandidat ditemukan: {len(kandidat)}")

    bots_cache = {}
    dibuat = 0
    for conv in kandidat:
        draft = conv.get("booking_draft") or {}
        if not draft:
            continue

        # Sudah pernah dinotifikasi utk draft PERSIS ini - skip (cegah tiket duplikat
        # tiap 30 menit selama tamu tetap diam). Draft berubah isinya (mis. tamu mulai
        # obrolan booking baru) -> notified lama otomatis tidak match lagi, boleh tiket baru.
        if conv.get("stale_booking_notified") == draft:
            continue

        bot_id = conv.get("bot_id")
        if bot_id not in bots_cache:
            bots_cache[bot_id] = await db.ai_bots.find_one({"_id": bot_id}) if bot_id else None
        bot = bots_cache[bot_id]

        ringkasan_draft = ", ".join(f"{k}={v}" for k, v in draft.items() if v)
        deskripsi = (
            f"⏳ Tamu sempat mulai proses booking via chat AI (draft: {ringkasan_draft}) tapi "
            f"belum konfirmasi final & sudah diam >{AMBANG_MENIT} menit - booking BELUM tercipta "
            f"di sistem. Mungkin perlu di-follow up manual kalau relevan (bisa saja tamu sudah "
            f"booking lewat jalur lain/berubah pikiran, cek dulu sebelum follow-up)."
        )
        hasil = await _pms_buat_tiket(
            "service_request", deskripsi, conv.get("whatsapp", ""), conv.get("guest_name", ""), "",
            api_key_override=(bot.get("pms_property_api_key") if bot else None),
        )
        if hasil.get("ok"):
            await db.conversations.update_one(
                {"_id": conv["_id"]}, {"$set": {"stale_booking_notified": draft}},
            )
            dibuat += 1
            print(f"  tiket dibuat: conv {conv['_id']} ({conv.get('guest_name')}, {conv.get('whatsapp')})")
        else:
            print(f"  GAGAL bikin tiket utk conv {conv['_id']}: {hasil.get('error')}")

    print(f"ringkasan: {dibuat} tiket baru dibuat dari {len(kandidat)} kandidat")


if __name__ == "__main__":
    asyncio.run(main())
