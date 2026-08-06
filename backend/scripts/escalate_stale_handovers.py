"""Eskalasi handover yang menggantung (2026-08-06, permintaan Agus setelah audit temukan
12 percakapan handover yang tidak pernah ditindaklanjuti staf, sebagian sampai 2 hari).

Akar masalah (lihat server.py `_run_chat_turn_locked` blok "Human Handover"): begitu
status="waiting_admin", AI SENGAJA berhenti total membalas (desain 2026-07-26, supaya AI
& staf tidak bareng-bareng balas tamu yang sama). Notifikasi Telegram cuma tembak SEKALI
pas momen handover terjadi (_tool_request_handover) - kalau ping itu kelewat, tidak ada
jaring pengaman lain, percakapan diam selamanya sampai staf buka halaman Percakapan
sendiri secara manual.

Script ini murni MENGULANG notifikasi yang SUDAH ada (bukan channel/aksi baru, bukan
menyentuh booking/data tamu apa pun) - kategori aman yang sama dgn auto-fix guardrail di
daily_qa_audit.py. `handover_escalated_at` (field baru) mencegah spam - re-ping cuma
kalau belum pernah dieskalasi ATAU eskalasi terakhir sudah >AMBANG_MENIT lalu, jadi
staf terus diingatkan berkala SELAMA belum ditangani, bukan cuma sekali lagi lalu diam.

Dijalankan cron tiap 30 menit (sama cadence dgn check_stale_bookings.py, keduanya cek
kondisi yang bisa berubah kapan saja).

Jalankan manual: `venv/bin/python -m scripts.escalate_stale_handovers`
"""
import asyncio
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import db  # noqa: E402
from connectors.pms_connector import _pms_alert_owner  # noqa: E402

AMBANG_MENIT = 180  # 3 jam - sama ambang dgn yg dipakai daily_qa_audit.py utk "menggantung"


async def main():
    batas = (datetime.now(timezone.utc) - timedelta(minutes=AMBANG_MENIT)).isoformat()
    kandidat = await db.conversations.find({
        "status": "waiting_admin", "resolution": "handover", "updated_at": {"$lt": batas},
    }).to_list(200)

    print(f"[{datetime.now(timezone.utc).isoformat()}] kandidat handover menggantung: {len(kandidat)}")

    dieskalasi = 0
    for conv in kandidat:
        terakhir = conv.get("handover_escalated_at")
        if terakhir and terakhir > batas:
            continue  # sudah dieskalasi dalam AMBANG_MENIT terakhir - jangan spam

        lama_menit = int((datetime.now(timezone.utc) - datetime.fromisoformat(conv["updated_at"])).total_seconds() / 60)
        pesan = (
            f"🔁 PENGINGAT: percakapan handover BELUM ditangani ({lama_menit} menit sejak "
            f"pesan terakhir) - tamu {conv.get('guest_name') or conv.get('whatsapp') or '?'} "
            f"({conv.get('whatsapp') or '-'}). Alasan: {conv.get('handover_reason') or '(tidak dicatat)'}. "
            f"Ini pengingat SUSULAN (ping pertama sudah terkirim sebelumnya tapi belum ditindaklanjuti) - "
            f"cek halaman Percakapan."
        )
        terkirim = await _pms_alert_owner(pesan)
        if terkirim:
            await db.conversations.update_one(
                {"_id": conv["_id"]}, {"$set": {"handover_escalated_at": datetime.now(timezone.utc).isoformat()}},
            )
            dieskalasi += 1
            print(f"  dieskalasi: {conv.get('guest_name')} ({conv.get('whatsapp')}), {lama_menit} menit")
        else:
            print(f"  GAGAL kirim eskalasi utk {conv['_id']}")

    print(f"ringkasan: {dieskalasi} eskalasi terkirim dari {len(kandidat)} kandidat")


if __name__ == "__main__":
    asyncio.run(main())
