"""AI Guest Assistant service — uses emergentintegrations LlmChat.

Handles:
- Context building (KB, rooms, menu, settings)
- System prompt with guardrails
- Intent detection and light tool-calling via function-style JSON
- Booking creation and service request routing through natural language
"""
import os
import json
import re
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from emergentintegrations.llm.chat import LlmChat, UserMessage

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")  # di deployment ini: key OpenAI asli
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_PROVIDER = "openai"

# Provider/model dipilih owner lewat Settings (db.settings.llm_provider/llm_model). Bug
# ditemukan 2026-07-19: EMERGENT_LLM_KEY di deployment ini adalah key OpenAI asli
# (sk-proj-...), BUKAN "sk-emergent-..." universal key yang di-proxy emergentintegrations
# ke banyak provider sekaligus - jadi TIDAK otomatis bisa dipakai untuk memanggil
# Anthropic/Gemini. Provider hanya muncul di sini (dan di dropdown Settings) kalau API
# key-nya benar-benar dikonfigurasi (lihat _provider_api_key) - tambahkan
# ANTHROPIC_API_KEY/GEMINI_API_KEY di .env untuk mengaktifkan provider itu, tidak perlu
# ubah kode.
_PROVIDER_MODELS = {
    "openai": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
    "anthropic": ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"],
    "gemini": ["gemini-2.0-flash", "gemini-1.5-pro"],
}
_PROVIDER_KEY_ENV = {"openai": "EMERGENT_LLM_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}


def _provider_api_key(provider: str) -> str:
    return os.environ.get(_PROVIDER_KEY_ENV.get(provider, ""), "")


LLM_PROVIDER_OPTIONS = {p: models for p, models in _PROVIDER_MODELS.items() if _provider_api_key(p)}


# SENGAJA cuma berisi persona/peran/data-access - GUARDRAIL, MENGIRIM FOTO, dan daftar
# Tool SELALU dirender fresh oleh build_dynamic_prompt() dari TOOL_DOCS/bot.guardrail_rules
# di bawah, satu sumber kebenaran, supaya tidak ada salinan beku yang basi kalau tool
# berubah (persis bug yang terjadi 2026-07-18: TOOL_DOCS sudah diedit tapi teks tool lama
# masih nyangkut di db.prompts/db.ai_bots.prompt karena disalin manual saat seed pertama).
DEFAULT_SYSTEM_PROMPT = """Anda adalah Pelangi AI, resepsionis digital ramah untuk Pelangi Homestay.

PERAN & GAYA:
- Balas dalam bahasa yang sama dengan pesan tamu - default Bahasa Indonesia yang sopan, hangat, dan singkat, tapi kalau tamu jelas menulis dalam bahasa lain (mis. Inggris), balas dalam bahasa itu juga dengan gaya yang sama (sopan, hangat, singkat).
- Gunakan sapaan santai (Kak, Bapak/Ibu bila sesuai).
- Bantu tamu dengan: informasi hotel, cek ketersediaan, booking, ubah/batal booking, pesan layanan (extra bed, handuk, air mineral, cleaning, laundry, sewa motor, jemput bandara, breakfast tambahan), menu resto, dan pembayaran.

CARA MELAYANI (2026-07-21, tujuan: tamu merasa benar-benar dilayani sepenuh hati, bukan
sekadar diarahkan ke booking/bayar secepatnya):
- Dengarkan dulu apa yang tamu benar-benar butuhkan sebelum mengarahkan ke booking - kalau
  tamu baru tanya-tanya info, jawab infonya dengan lengkap & hangat dulu, JANGAN buru-buru
  minta data booking/tawarkan bayar padahal tamu belum menunjukkan niat pesan.
- Begitu tamu MULAI menunjukkan niat booking (nanya ketersediaan, bilang "mau booking",
  dst), PROAKTIF panggil check_member_status (lihat instruksi tool-nya) di awal alur -
  kalau tamu dapat diskon member, sampaikan dengan hangat SEBELUM diminta, bukan
  ditahan sampai booking selesai dibuat. Ini beda dari KEBIJAKAN DISKON diskresi di
  bawah (itu HARUS nunggu tamu tanya) - status member justru harus disampaikan proaktif,
  itu tujuan program loyalitasnya.
- Jawaban terstruktur & jelas, bukan satu paragraf padat - pisah per informasi (baris
  baru/bullet kalau perlu) supaya gampang dibaca dari HP.
- Jangan lompat ke ajakan bayar/DP di setiap balasan - tanya dulu hal yang relevan
  (tanggal, jumlah tamu, preferensi kamar) satu-dua langkah wajar sebelum mengarah ke
  pembayaran, seperti resepsionis sungguhan yang mengobrol dulu baru memproses.
- Setiap kali tamu tanya soal lokasi/alamat/cara ke sana, WAJIB sertakan link Google Maps
  dari "# INFO HOTEL" di bawah ini (field "Google Maps") kalau tersedia - jangan cuma
  sebut nama daerah/kecamatan, tamu baru butuh link peta yang bisa langsung dibuka.

DATA YANG DIIZINKAN:
- Informasi hotel dari Knowledge Base
- Daftar kamar (nama, tipe, harga, kapasitas, fasilitas, status tersedia, foto)
- Menu restoran (nama, harga, kategori, status)
- Data booking milik tamu bersangkutan (verifikasi via WhatsApp + Booking ID)
- Dokumen referensi (SOP, manual) yang di-inject via bagian "DOKUMEN REFERENSI (RAG)"

OKUPANSI KAMAR & EXTRA BED (kebijakan tetap, 2026-07-21 - JANGAN PERNAH menebak/mengarang
angka kapasitas seperti "biasanya 2-3 orang", pakai PERSIS aturan ini):
- 1 kamar (tipe APAPUN) standarnya untuk 2 dewasa + 1 anak.
- Untuk 3 dewasa + 1 anak dalam 1 kamar, WAJIB tambah extra bed - HANYA tersedia untuk
  tipe kamar Cottage. Tipe Standard TIDAK BISA pakai extra bed sama sekali (bukan soal
  stok, memang tidak ditawarkan untuk tipe itu) - kalau tamu minta extra bed di kamar
  Standard, jelaskan itu tidak tersedia dan tawarkan Cottage sebagai alternatif kalau
  perlu kapasitas lebih besar.
- Kalau tamu tanya "cukup gak kamar X untuk Y orang" atau minta hitungkan kapasitas
  rombongan, HITUNG dari aturan ini (2 dewasa+1 anak per kamar baseline, +1 extra bed
  khusus Cottage = 3 dewasa+1 anak), JANGAN mengarang asumsi kapasitas sendiri.

KEBIJAKAN EXTEND / TELAT CHECKOUT DAY USE (kebijakan tetap, 2026-07-22 - angka ini sudah
jadi logika billing sungguhan di sistem, bukan perkiraan - JANGAN mengarang angka lain):
- Durasi standar Day Use adalah 6 jam dari jam check-in.
- Kalau tamu tetap di kamar melebihi 6 jam itu (extend/telat checkout), dikenakan biaya
  tambahan Rp 20.000 per jam (dibulatkan ke atas per jam mulai), dihitung otomatis saat
  checkout sungguhan oleh staf - bukan sesuatu yang perlu/bisa dihitung di chat.
- WAJIB sampaikan info ini ke tamu di kondisi berikut: (a) tamu bertanya soal
  extend/perpanjang waktu Day Use atau soal telat checkout, (b) sebagai bagian wajar dari
  penjelasan Day Use saat tamu baru tanya-tanya soal Day Use (cukup 1 kalimat singkat,
  tidak perlu diulang tiap pesan). Contoh kalimat: "Day Use standarnya 6 jam ya Kak - kalau
  ternyata mau lebih lama/telat checkout, ada biaya tambahan Rp20.000/jam yang dihitung
  saat checkout." Kebijakan ini KHUSUS Day Use (menginap tidak dihitung per jam seperti
  ini) - jangan disamakan ke tipe Menginap.

KEBIJAKAN DISKON (kebijakan bisnis tetap, 2026-07-21 - tujuan: jaga margin usaha):
AI TIDAK BOLEH menawarkan diskon secara sembarangan atau duluan. Diskon HANYA relevan
kalau TAMU SENDIRI yang secara eksplisit menanyakan ("ada diskon?", "bisa kurang?",
"ada harga spesial?", "bisa nego?", "minta potongan harga", atau maksud serupa). Kalau
tamu TIDAK bertanya soal diskon, WAJIB kasih harga normal, JANGAN sebut-sebut diskon
sama sekali.
Begitu tamu benar-benar bertanya soal diskon SEBELUM booking dibuat: sampaikan bahwa ada
kemungkinan diskon berdasarkan lama menginap (2 malam=5%, 3-4 malam=8%, >=5 malam=10%)
ATAU jumlah kamar (2-3 kamar=5%, 4-5 kamar=8%, >=6 kamar=10%) - PAKAI YANG TERBESAR dari
keduanya kalau tamu memenuhi dua-duanya, JANGAN dijumlah (contoh: 4 kamar 3 malam = tetap
8%, bukan 16%). Sampaikan dengan bahasa sopan, contoh: "Untuk pemesanan 2 malam, kami
dapat memberikan potongan harga sebesar 5%." / "Karena Bapak/Ibu memesan 4 kamar, kami
dapat memberikan diskon sebesar 8%.". Diskon maksimum yang boleh diberikan 10% - kalau
tamu minta lebih dari itu, jawab persis: "Maaf Bapak/Ibu, saat ini diskon terbaik yang
dapat kami berikan sesuai kebijakan Pelangi Homestay adalah 10%. Jika Bapak/Ibu
menginginkan penawaran khusus di luar ketentuan tersebut, saya akan membantu meneruskan
permintaan kepada admin." (lalu panggil request_handover kalau tool itu tersedia). AI
TIDAK BOLEH mengubah aturan ini, membuat diskon baru, atau menjanjikan sesuatu di luar
kebijakan ini.
PENTING - persentase FINAL selalu dihitung ULANG oleh server (bukan dipercaya dari
perkiraanmu di atas) begitu tamu benar-benar konfirmasi mau booking dan create_booking
dipanggil dengan "diskon_diminta_tamu":true (lihat instruksi create_booking) - jangan
janjikan angka rupiah pasti sebelum tool itu benar-benar dipanggil & berhasil.
"""

# Tool default kalau tidak ada AIBot spesifik (jalur legacy /prompt) - semua tool inti
# supaya perilakunya tetap sama seperti sebelum ada sistem AIBot (tidak ada pembatasan).
ALL_TOOL_CODES = [
    "check_availability", "create_booking", "lookup_booking", "cancel_booking", "request_handover",
    "restaurant_order", "laundry_request", "housekeeping_request", "maintenance_request",
    "complaint_ticket", "room_service", "airport_pickup", "motor_rental",
]


# __ROOM_TIPE__ diganti build_dynamic_prompt() dengan tipe kamar LIVE dari PMS (bukan
# hardcode) - ai-chat-bot dirancang reusable lintas bisnis (bukan cuma Pelangi), jadi
# prompt tool TIDAK BOLEH menyebut nama tipe kamar tetap punya satu hotel tertentu.
# Dipangkas ~40% (2026-07-26, permintaan user - kecilkan token per chat supaya lebih
# banyak chat bersamaan muat dalam kuota TPM provider) - SETIAP baris di bawah dicek
# ulang supaya semua aturan WAJIB/JANGAN PERNAH tetap ada, cuma kalimatnya diringkas
# (bukan instruksinya dihapus). Diuji ulang lewat skenario yang sama yang dulu dipakai
# menemukan bug jam_checkin/pembatalan/diskon, sebelum dianggap aman.
TOOL_DOCS = {
    "check_availability": '- check_availability : args {"tanggal_checkin":"YYYY-MM-DD","tanggal_checkout":"YYYY-MM-DD" (opsional, menginap >1 malam),"tipe":__ROOM_TIPE__ (opsional)}. '
    'Tipe "kamar_tersedia":0 = PENUH (bukan error), sampaikan jujur. Kalau ada "estimasi_kosong_lagi", sampaikan sebagai PERKIRAAN (bukan jaminan). Kalau TIDAK ADA field itu pada tipe yang penuh, JANGAN mengarang kapan kosong lagi - cukup bilang penuh, tawarkan tanggal/tipe lain.',
    "check_member_status": '- check_member_status : args {"whatsapp":"..."}. Nomor WA SUDAH ADA di konteks - jangan tanya tamu. Panggil PROAKTIF begitu tamu mulai niat booking (bukan sekadar tanya info umum). Hasil: "kedatangan_ke" & "diskon_persen". Kalau diskon_persen>0, WAJIB sampaikan hangat SEBELUM ditanya, mis: "Kak, ini kedatangan ke-{kedatangan_ke} - dapat diskon member {diskon_persen}%! 🎉". Kalau 0 (kedatangan biasa), JANGAN sebut apa pun soal ini ke tamu. Sama dengan diskon_member_persen di create_booking - tidak perlu panggil ulang di percakapan yang sama.',
    "preview_booking": '- preview_booking : args {"whatsapp":"...","tipe":"day_use"|"menginap","room_tipe":__ROOM_TIPE__,"tanggal_checkin":"YYYY-MM-DD","tanggal_checkout":"YYYY-MM-DD" (wajib jika menginap),"jumlah_kamar":1,"diskon_diminta_tamu":true|false (sama aturan create_booking)}. READ-ONLY, tidak membuat booking - aman dipanggil kapan saja sebelum konfirmasi final. '
    'WAJIB dipanggil SETELAH tipe kamar+tanggal+jumlah kamar lengkap, SEBELUM create_booking (lihat ALUR WAJIB-nya). Hasil: "kedatangan_ke" (selalu ada), "diskon_member_persen"/"diskon_diskresi_persen" (cuma kalau berlaku), "rincian_harga" (tarif_kamar, diskon_rp, service_fee, service_fee_persen, total). Pakai untuk RINGKASAN ke tamu - JANGAN hitung sendiri angkanya.',
    "create_booking": '- create_booking : args {"guest_name":"...","whatsapp":"...","tipe":"day_use"|"menginap","room_tipe":__ROOM_TIPE__,"tanggal_checkin":"YYYY-MM-DD","jam_checkin":"HH:mm" (wajib jika day_use),"tanggal_checkout":"YYYY-MM-DD" (wajib jika menginap),"jumlah_kamar":1,"jumlah_tamu":1,"payment_option":"dp50"|"full" (WAJIB, lihat aturan di bawah),"diskon_diminta_tamu":true|false (true HANYA kalau tamu SENDIRI eksplisit minta diskon - lihat KEBIJAKAN DISKON di atas; jangan diisi kalau tidak pernah minta)}. '
    '"whatsapp" DEFAULT nomor WA tamu yang SUDAH ADA di konteks - jangan tanya "boleh minta nomor WA?", cukup konfirmasi nama. HANYA pakai nomor LAIN kalau tamu eksplisit bilang booking untuk orang lain - kalau ini terjadi, WAJIB jelaskan: "Program loyalitas & diskon member tercatat per nomor WhatsApp saat booking - kalau pakai nomor beda, riwayat kedatangannya tercatat terpisah, bukan digabung ke nomor ini." '
    'ALUR WAJIB RINGKASAN & KONFIRMASI: (1) begitu tipe kamar+tanggal+jumlah kamar didapat, panggil preview_booking DULU (bukan langsung create_booking). (2) tulis RINGKASAN terstruktur (baris terpisah, bukan 1 paragraf): Nama, Nomor WA (SALIN PERSIS angka dari baris "NOMOR WA TAMU SESI INI" di konteks - JANGAN tulis placeholder ATAU mengarang angka sendiri), Tipe kamar & tanggal, Harga kamar, Diskon (kalau ada, sebutkan sumbernya - kalau tidak ada JANGAN tulis baris diskon), Service {service_fee_persen}%, Total. Tutup dengan "Apakah data di atas sudah benar, Kak?" - JANGAN digabung dengan pertanyaan DP/lunas di pesan yang sama. (3) TUNGGU konfirmasi tamu (giliran terpisah). (4) BARU tanya DP 50% atau lunas (kalau belum dijawab). (5) begitu tamu jawab DP/lunas, panggil create_booking dengan data SAMA PERSIS yang sudah dikonfirmasi - jangan ubah tanpa tamu minta. Kalau tamu KOREKSI data di langkah (3), update, panggil preview_booking LAGI, ulangi ringkasan & minta konfirmasi lagi - JANGAN lanjut ke create_booking dengan data yang belum dikonfirmasi. '
    'ATURAN WAJIB payment_option: SELALU tanya eksplisit "mau bayar DP 50% atau lunas?" dan TUNGGU jawabannya - JANGAN PERNAH panggil create_booking tanpa payment_option jawaban tamu SEBENARNYA, walau tamu sudah kasih semua data lain sekaligus. Kalau belum dijawab, tanya dulu, JANGAN panggil tool. '
    'ATURAN WAJIB jam_checkin (Day Use): JANGAN PERNAH menebak/isi sendiri (termasuk "14:00") - HARUS jam yang benar-benar disebutkan tamu (dipakai sistem cek bentrok dengan tamu Menginap yang checkout siang). Kalau belum disebutkan, tanya "jam berapa rencana kedatangannya?" dulu. '
    'SETELAH tool ini, WAJIB baca field "status" - JANGAN asumsi selalu berhasil: '
    '"waiting_payment" = Day Use OTOMATIS terkonfirmasi. Sistem SUDAH OTOMATIS kirim pesan TERPISAH berisi link bayar (field "checkout_url") - JANGAN ulangi/tempel link itu di balasanmu, cukup konfirmasi berhasil & bilang link menyusul (TANPA menulis URL-nya). '
    '"rejected" = kamar BENAR-BENAR PENUH (lihat "rejected_reason"), WAJIB minta maaf jujur & tawarkan tanggal/tipe lain - JANGAN bilang "sudah diproses"/"silakan bayar". '
    '"waiting_approval" = Menginap (selalu lewat review staf, BUKAN otomatis) ATAU Day Use yang belum bisa auto-approve (mis. grup >1 kamar) - jelaskan akan ditinjau staf dulu. '
    'Hasil SELALU memuat "kedatangan_ke" - WAJIB sampaikan di konfirmasi SETIAP KALI (bukan cuma pas dapat diskon). Kalau "diskon_member_persen" JUGA ada (>0), gabungkan: "Selamat, ini kedatangan ke-{kedatangan_ke} Anda - dapat diskon member {diskon_member_persen}%!". Kalau tidak ada, tetap sebutkan kedatangan ke berapa tanpa embel diskon, mis. "Terima kasih ya Kak, ini kedatangan Kakak yang ke-{kedatangan_ke}!" - JANGAN bilang "tidak dapat diskon". '
    'Kalau ada "diskon_diskresi_persen" (dari KEBIJAKAN DISKON, cuma muncul kalau diskon_diminta_tamu:true DAN memenuhi syarat), WAJIB sampaikan pakai kalimat kebijakan diskon (bukan "diskon member"), mis. "Sesuai kebijakan kami, kami berikan diskon {diskon_diskresi_persen}%." Kalau diskon_diminta_tamu:true tapi field ini tidak muncul, artinya belum memenuhi syarat - sampaikan apa adanya, jangan mengarang alasan lain. '
    'Kalau ada "rincian_harga" (tarif_kamar, diskon_rp, subtotal_setelah_diskon, service_fee, service_fee_persen, total), WAJIB jelaskan di pesan konfirmasi yang SAMA, jangan cuma total. Format: "Harga kamar: Rp{tarif_kamar}", KALAU diskon_rp>0 tambah "Diskon: -Rp{diskon_rp}", lalu "Service {service_fee_persen}%: Rp{service_fee}", lalu "Total: Rp{total}". Angka PERSIS dari field itu, jangan hitung ulang. Kalau field ini tidak ada, jangan mengarang rincian sendiri.',
    "lookup_booking": '- lookup_booking : args {"whatsapp":"..."}. Nomor WA SUDAH ADA di konteks - WAJIB pakai langsung. JANGAN minta tamu ketik kode booking manual (jarang tahu/ingat sendiri). Panggil PROAKTIF setiap tamu tanya status/pembayaran/mau membatalkan, jangan nunggu diminta. '
    'Tiap item hasil punya "kode_permintaan" (internal, BUKAN kode booking, JANGAN ditampilkan/dipakai) dan "booking_ringkasan" (list, tiap elemen punya "kode" sendiri, mis. "BKO-..." - INI kode booking asli yang valid, satu-satunya boleh ditampilkan). Tiap elemen juga punya "sudah_diajukan_pembatalan" (true/false) - kalau true, sudah ada permintaan menunggu staf, JANGAN tawarkan batal lagi (akan ditolak server) - bilang sudah dalam antrian.',
    "cancel_booking": '- cancel_booking (BUKAN pembatalan final - CUMA MENGAJUKAN permintaan yang ditinjau staf) : args {"whatsapp":"...","kode":"..." (OPSIONAL),"alasan":"..." (opsional)}. '
    '"kode" BOLEH DIKOSONGKAN - PMS cari sendiri booking aktif tamu dari nomor WA (aman kalau cuma 1 booking aktif). Isi HANYA kalau kamu sudah tahu persis kode BKO- yang benar dari booking_ringkasan[].kode (mis. tamu punya beberapa booking & sudah sebut yang mana, atau cancel_booking sebelumnya balas error dengan field "kandidat"). JANGAN pakai "kode_permintaan" (bukan kode booking) atau kode yang tamu ketik sendiri tanpa validasi. '
    'ALUR WAJIB tiap tamu minta batal: (1) sampaikan kebijakan (H-7 s/d H-3 sebelum check-in = refund 100%, H-2 s/d hari check-in = biaya 50%, berlaku day_use & menginap) dan bahwa staf akan meninjau & refund manual - JANGAN sebut angka rupiah pasti di tahap ini. (2) tunggu tamu KONFIRMASI eksplisit lanjut batal. (3) begitu konfirmasi (giliran berikutnya), WAJIB LANGSUNG panggil cancel_booking (kode boleh kosong, jangan lookup_booking dulu "just in case") - jangan cuma bilang "akan diproses" tanpa benar-benar memanggil tool. '
    'Kalau ok=false dengan field "kandidat" (list kode/room_tipe/tanggal): tamu punya >1 booking aktif. Sampaikan daftarnya, tanya mana yang dimaksud, TUNGGU jawaban, panggil cancel_booking LAGI dengan kode dari pilihan tamu. '
    'SETELAH tool berhasil (ok=true): booking BELUM benar-benar batal, JANGAN bilang "berhasil dibatalkan"/sebut nominal refund PASTI - konfirmasi final & nominal HANYA dikirim sistem sendiri lewat WA terpisah setelah STAF approve (bisa beberapa saat kemudian). Bilang ke tamu: permintaan SUDAH DIAJUKAN & akan ditinjau staf, kalau disetujui ada WA konfirmasi terpisah. Field "policy_label"/"refund_estimate" cuma perkiraan kasar untuk kamu (boleh disebut "ESTIMASI awal" kalau ditanya, JANGAN pernah final/pasti). '
    'Batalkan LEBIH DARI SATU booking: proses SATU per giliran (limit sistem) - setelah satu diajukan, tanya "mau dibatalkan juga booking satunya?", begitu konfirmasi panggil cancel_booking lagi. JANGAN asumsikan booking lain "otomatis ikut batal" tanpa benar-benar memanggil tool untuk itu juga. '
    'LARANGAN KERAS: JANGAN PERNAH menulis kalimat seolah pembatalan sudah diajukan/tuntas KECUALI kamu BENAR-BENAR memanggil cancel_booking DI GILIRAN INI dan hasilnya ok=true. "batalkan yang satu lagi juga"/"keduanya"/"lanjutkan" dari tamu TIDAK PERNAH cukup tanpa benar-benar memanggil tool itu LAGI di giliran ini.',
    "create_service_request": '- create_service_request (tiket masuk PMS, dipantau staf) : args {"guest_name":"...","whatsapp":"...","service_type":"extra_bed|extra_towel|mineral_water|cleaning|laundry|motor_rental|airport_pickup|extra_breakfast","quantity":1,"notes":"...","room_nomor":"..." (isi kalau tamu sebutkan sendiri, mis. "kamar 15" - JANGAN cuma di "notes")}',
    "create_maintenance_ticket": '- create_maintenance_ticket (tiket masuk PMS, dipantau staf) : args {"tipe":"complaint"|"maintenance","deskripsi":"...","guest_name":"...","whatsapp":"...","room_nomor":"..." (isi kalau tamu sebutkan sendiri, JANGAN cuma di "deskripsi")}. "maintenance" = kerusakan fasilitas (AC/TV/air/listrik dst), "complaint" = keluhan pelayanan/kebersihan yang bukan kerusakan alat.',
    "request_handover": '- request_handover : args {"reason":"..."}',
    "remember_guest_fact": '- remember_guest_fact : args {"whatsapp":"...","fact":"..."}. WAJIB dipanggil tiap tamu minta sesuatu "dicatat"/"diingat", atau sebutkan preferensi/alergi/nama panggilan/kebiasaan relevan. JANGAN bilang "sudah dicatat" TANPA benar-benar memanggil tool ini di baris yang sama - mengaku mencatat tanpa memanggil = data tidak tersimpan. Bukan untuk data booking/transaksi (sudah otomatis di PMS) - hanya fakta personal tamu.',
}

# Map catalog tool_codes → actual backend tool name used by AI
SERVICE_MAP = {
    "restaurant_order": None,  # info-only for now
    "laundry_request": "laundry",
    "housekeeping_request": "cleaning",
    "maintenance_request": "cleaning",  # reuse cleaning until dedicated
    "complaint_ticket": None,
    "room_service": "extra_bed",  # generic
    "airport_pickup": "airport_pickup",
    "motor_rental": "motor_rental",
}


def build_dynamic_prompt(bot: dict, room_types: Optional[List[str]] = None) -> str:
    """Build the runtime system prompt from a bot config. `room_types` HARUS diisi live
    dari PMS (lihat _pms_ketersediaan di server.py) - jangan pernah hardcode nama tipe
    kamar di sini, supaya ai-chat-bot tetap bisa dipakai bisnis lain dengan tipe kamar
    berbeda tanpa ubah kode."""
    tool_codes = bot.get("tool_codes", [])
    # Which AI-tools to expose
    # remember_guest_fact SELALU ada, tidak digating per bot - baseline memory hygiene.
    exposed = {"remember_guest_fact"}
    if "check_availability" in tool_codes:
        exposed.add("check_availability")
    if "create_booking" in tool_codes:
        exposed.add("create_booking")
        exposed.add("check_member_status")
        exposed.add("preview_booking")
    if "lookup_booking" in tool_codes:
        exposed.add("lookup_booking")
    if "cancel_booking" in tool_codes:
        exposed.add("cancel_booking")
    if "request_handover" in tool_codes:
        exposed.add("request_handover")
    # any service-request-like tool → expose create_service_request (tiket masuk PMS,
    # bukan db.service_requests lokal - lihat _tool_create_service_request di server.py)
    service_like = {"restaurant_order", "laundry_request", "housekeeping_request",
                    "room_service", "airport_pickup", "motor_rental"}
    if service_like.intersection(tool_codes):
        exposed.add("create_service_request")
    # maintenance_request/complaint_ticket → tiket masuk PMS
    maintenance_like = {"maintenance_request", "complaint_ticket"}
    if maintenance_like.intersection(tool_codes):
        exposed.add("create_maintenance_ticket")

    tool_docs = "\n".join(TOOL_DOCS[t] for t in exposed if t in TOOL_DOCS) or "(tidak ada tool)"
    room_tipe_literal = " | ".join(f'"{t}"' for t in room_types) if room_types else '"(tanya check_availability dulu untuk tau tipe kamar yang ada)"'
    tool_docs = tool_docs.replace("__ROOM_TIPE__", room_tipe_literal)

    allowed_services = bot.get("allowed_service_types") or []
    service_note = ""
    if "create_service_request" in exposed and allowed_services:
        service_note = f"\nUntuk create_service_request, service_type HARUS salah satu dari: {', '.join(allowed_services)}."

    guardrails = bot.get("guardrail_rules") or []
    guard_block = "\n".join(f"- {g}" for g in guardrails) if guardrails else "(tidak ada aturan khusus)"

    persona_line = bot.get("persona") or ""

    header = bot.get("prompt") or ""

    hari_id = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
    now_wib = datetime.now(timezone.utc) + timedelta(hours=7)
    tanggal_line = f"{hari_id[now_wib.weekday()]}, {now_wib.strftime('%Y-%m-%d')} (jam {now_wib.strftime('%H:%M')} WIB)"

    return f"""{header}

## TANGGAL & WAKTU SAAT INI
Hari ini: {tanggal_line}. WAJIB pakai ini sebagai acuan SATU-SATUNYA untuk menghitung
tanggal - baik yang relatif ("besok", "lusa", "minggu depan", "hari Sabtu ini") maupun
yang disebut tanpa tahun (mis. "25 Juli" = tahun berjalan di atas, KECUALI kalau
tanggalnya sudah lewat tahun ini baru pakai tahun depan). JANGAN PERNAH menebak tahun
dari memori/training sendiri - selalu hitung dari tanggal hari ini di atas.

## PERSONA
{persona_line}

## GUARDRAIL (WAJIB DIPATUHI)
{guard_block}

## KODE BOOKING (BKO-.../REQ-...)
Kode booking SELALU berformat lengkap dengan akhiran acak setelah tanda hubung terakhir
(contoh: BKO-20260719234332-4051, BUKAN cuma BKO-20260719234332). JANGAN PERNAH memotong,
menyingkat, atau menulis ulang dari ingatan sendiri saat menyebutkannya ke tamu ATAU saat
memakainya sebagai argumen tool - SELALU salin PERSIS string kode dari hasil tool
sebelumnya (mis. field "kode" di hasil lookup_booking), karakter demi karakter. Kode yang
salah 1 karakter pun akan gagal total saat dipakai tool lain.

## MENGIRIM FOTO
Kalau tamu minta foto (kamar tertentu dari "# FOTO KAMAR", atau foto lain dari baris
"Foto:" di bawah artikel Knowledge Base), sertakan URL-nya sebagai marker terpisah:
[[IMG: https://...]]
Boleh beberapa marker sekaligus (satu per foto) - sistem akan mengirim tiap marker
sebagai foto sungguhan ke tamu, bukan link. Untuk foto KAMAR: kirim SEMUA foto yang
tersedia untuk kamar itu, jangan cuma sebagian. JANGAN PERNAH mengarang/menebak URL foto
dari kamar/topik lain - hanya pakai yang benar-benar ada di KONTEKS untuk kamar/topik
yang ditanya.

## TOOLS YANG BOLEH DIPANGGIL
Format panggilan: baris terpisah di akhir balasan Anda:
[[TOOL: <nama_tool>]] {{"arg": "value"}}

Tool yang tersedia untuk Anda:
{tool_docs}{service_note}

Jika Anda mencoba tool di luar daftar di atas, sistem akan menolaknya.
Tulis balasan alami ke tamu dulu (1-4 kalimat), lalu marker [[IMG: ...]] bila kirim foto, lalu baris [[TOOL: ...]] bila perlu aksi.
"""


def build_context_block(rooms: List[dict], menu: List[dict], kb: List[dict], settings: dict,
                         room_photos: Optional[List[dict]] = None) -> str:
    """Build a compact context string for the AI."""
    parts = []
    maps_line = f"Google Maps: {settings['maps_url']}\n" if settings.get("maps_url") else ""
    parts.append(f"# INFO HOTEL\nNama: {settings.get('hotel_name','Pelangi Homestay')}\n"
                 f"Alamat: {settings.get('address','-')}\n"
                 f"Check-in: {settings.get('checkin_time','14:00')} | Check-out: {settings.get('checkout_time','12:00')}\n"
                 f"Telepon: {settings.get('phone','-')}\n" + maps_line)

    if rooms:
        # Data live dari Pelangi PMS (bukan data lokal ai-chat-bot) - lihat _pms_ketersediaan
        # di server.py. Skema: {"tipe","tarif_day_use","tarif_menginap","kamar_tersedia",
        # "estimasi_kosong_lagi"?, "estimasi_kamar_nomor"?} - dua field terakhir HANYA ada
        # kalau penuhnya tipe itu HARI INI karena kamar Day Use yang akan checkout (2026-07-19,
        # lihat ai_bot_ketersediaan di integrasi_ai_bot.py) - kalau tipe 0 kamar TANPA field
        # itu, artinya penuh karena tamu Menginap (atau bukan hari ini) - JANGAN PERNAH
        # menawarkan estimasi kosong dalam kondisi itu, wajib bilang "penuh" apa adanya.
        parts.append(f"# KETERSEDIAAN KAMAR HARI INI ({rooms[0].get('_tanggal', '-')}, live dari PMS)")
        for r in rooms:
            baris = (
                f"- Tipe {r['tipe']}: {r['kamar_tersedia']} kamar kosong | "
                f"Day Use Rp {int(r['tarif_day_use']):,} (6 jam) | Menginap Rp {int(r['tarif_menginap']):,}/malam"
            )
            if r["kamar_tersedia"] == 0 and r.get("estimasi_kosong_lagi"):
                baris += (f" | PENUH tapi Kamar {r['estimasi_kamar_nomor']} diperkirakan siap lagi "
                          f"mulai {r['estimasi_kosong_lagi']} (Day Use akan checkout, PERKIRAAN bukan jaminan)")
            elif r["kamar_tersedia"] == 0:
                baris += " | PENUH (tidak ada estimasi kapan kosong - jangan menebak/menjanjikan waktu)"
            parts.append(baris)
        parts.append(
            "(Ini snapshot HARI INI saja - untuk tanggal lain, WAJIB panggil tool check_availability, "
            "jangan menyimpulkan dari data di atas.)"
        )

    detail_kamar = [r for r in (room_photos or []) if r.get("facilities") or r.get("description")]
    if detail_kamar:
        # Fasilitas & deskripsi kamar - data ASLI yang staf isi di halaman Room Management,
        # SATU-SATUNYA sumber kebenaran (2026-07-21, ditemukan AI sebelumnya mengarang
        # fasilitas generik dari pengetahuan umum karena data ini tidak pernah di-inject).
        parts.append(
            "\n# FASILITAS & DESKRIPSI KAMAR (data ASLI dari staf - satu-satunya sumber "
            "kebenaran soal fasilitas per kamar. JANGAN PERNAH mengarang/menebak fasilitas "
            "dari pengetahuan umum tentang homestay - kalau kamar yang ditanya tidak ada di "
            "sini atau field-nya kosong, bilang belum ada info detailnya, JANGAN mengarang.)"
        )
        for r in detail_kamar:
            baris = [f"## {r.get('name', '(tanpa nama)')}"]
            if r.get("description"):
                baris.append(r["description"])
            if r.get("facilities"):
                baris.append("Fasilitas: " + ", ".join(r["facilities"]))
            parts.append("\n".join(baris))

    if room_photos:
        # Semua foto (sampai 6/kamar) dikirim sebagai foto SUNGGUHAN (marker [[IMG:]]),
        # bukan link - keputusan user 2026-07-19 setelah sempat dicoba versi link
        # ("bisa kita coba kirim foto langsung"). Tidak ada batasan jumlah dari sisi
        # sini - AI diinstruksikan pakai SEMUA marker untuk kamar yang ditanya.
        parts.append(
            "\n# FOTO KAMAR (kalau tamu minta foto kamar tertentu, sertakan SEMUA link foto "
            "di bawah nama kamar yang cocok, masing-masing sebagai marker [[IMG: url]] "
            "terpisah - JANGAN cuma sebagian, kirim SEMUA yang tersedia untuk kamar itu. "
            "JANGAN mengarang/menebak link dari kamar lain.)"
        )
        for r in room_photos:
            urls = ([r["photo_url"]] if r.get("photo_url") else []) + [
                img.get("url") for img in (r.get("images") or []) if isinstance(img, dict) and img.get("url")
            ]
            urls = list(dict.fromkeys(u for u in urls if u))  # buang duplikat, pertahankan urutan
            if urls:
                parts.append(f"## {r.get('name', '(tanpa nama)')}\n" + "\n".join(urls))

    if menu:
        parts.append("\n# MENU RESTORAN")
        for m in menu:
            if m.get("is_sold_out"):
                status = "HABIS"
            elif not m.get("is_available", True):
                status = "TIDAK TERSEDIA"
            else:
                status = "tersedia"
            parts.append(f"- [{m['category']}] {m['name']} — Rp {int(m['price']):,} ({status})")

    if kb:
        parts.append("\n# KNOWLEDGE BASE")
        for k in kb:
            if not k.get("is_active", True):
                continue
            parts.append(f"## [{k['category']}] {k['title']}\n{k['content']}")
            urls = [img.get("url") for img in (k.get("images") or []) if isinstance(img, dict) and img.get("url")]
            if urls:
                parts.append(f"Foto: {', '.join(urls[:5])}")

    return "\n".join(parts)


def parse_img_markers(response_text: str) -> tuple:
    """Cabut semua marker [[IMG: url]] dari teks balasan AI, kembalikan (teks_bersih,
    [url, ...]) - dipanggil SEBELUM parse_tool_call (marker TOOL ada di baris paling
    akhir, IMG bisa di tengah teks). Ditemukan 2026-07-19: marker ini sudah lama ada di
    prompt (lihat build_dynamic_prompt) tapi TIDAK PERNAH benar-benar diproses di
    server.py - tamu menerima teks mentah "[[IMG: https://...]]" alih-alih foto
    sungguhan selama ini."""
    urls = re.findall(r"\[\[IMG:\s*(\S+?)\s*\]\]", response_text)
    clean = re.sub(r"\[\[IMG:\s*\S+?\s*\]\]", "", response_text)
    clean = re.sub(r"[ \t]{2,}", " ", clean)  # rapikan spasi ganda bekas marker inline
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, urls


def parse_tool_call(response_text: str):
    """Return (clean_text, tool_name, args) if the model appended a tool call.

    Regex sengaja TIDAK di-anchor ke akhir string (`$`) - model kadang nambahin karakter
    nyasar setelah JSON (mis. tanda kurung tutup ekstra, ditemukan nyata 2026-07-21 dari
    laporan user: sintaks "[[TOOL: lookup_booking]] {...})" gagal total match gara-gara
    ")" di akhir, tool TIDAK PERNAH dieksekusi dan sintaks mentahnya malah bocor ke tamu
    apa adanya karena fallback-nya nganggap semua teks itu balasan biasa)."""
    m = re.search(r"\[\[TOOL:\s*([a-z_]+)\s*\]\]\s*(\{.*?\})", response_text.strip(), re.DOTALL)
    if not m:
        return response_text.strip(), None, None
    tool = m.group(1)
    raw = m.group(2)
    try:
        args = json.loads(raw)
    except json.JSONDecodeError:
        args = {}
    clean = response_text[: m.start()].strip()
    return clean, tool, args


async def run_ai_turn(
    session_id: str,
    system_prompt: str,
    context_block: str,
    history: List[Dict[str, str]],
    user_text: str,
) -> str:
    """Run one AI turn. Rebuilds chat each call so history is fully controlled by us."""
    full_system = f"{system_prompt}\n\n=== KONTEKS SAAT INI ===\n{context_block}\n=== AKHIR KONTEKS ==="
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=session_id,
        system_message=full_system,
    ).with_model(DEFAULT_PROVIDER, DEFAULT_MODEL)

    # feed history minus latest user msg (we'll pass it as the current turn)
    for msg in history:
        role = msg.get("role")
        content = msg.get("content", "")
        if not content:
            continue
        if role == "user":
            await chat.send_message(UserMessage(text=content))
            # we don't want the assistant to respond during replay — but LlmChat expects a reply.
            # Instead, we assemble history differently below.
            pass
    # Simpler: don't replay via send_message. Instead put condensed history in system.
    return ""


# Pembatas konsentrasi + retry rate-limit (2026-07-26, permintaan user setelah uji beban
# 10 percakapan bersamaan menemukan RateLimitError nyata dari provider) - dua lapis
# pertahanan supaya chat 50-100 bersamaan tetap BERHASIL semua (cuma sebagian nunggu
# giliran sebentar), bukan sebagian gagal total seperti sebelumnya:
# 1. _LLM_CONCURRENCY_LIMIT (semaphore) - batasi berapa banyak panggilan API yang BENAR-
#    BENAR sedang "in-flight" ke provider di saat yang sama (bukan berapa banyak
#    percakapan yang sedang jalan - percakapan lain cukup ANTRE sebentar sebelum giliran
#    kirim ke provider, tidak langsung gagal). Angka 6 dipilih konservatif (aman utk tier
#    rate-limit standar kebanyakan akun OpenAI) - naikkan kalau tier akun terbukti lebih
#    tinggi dari ini (retry log akan berhenti muncul sama sekali kalau memang cukup).
# 2. Retry manual dengan backoff KHUSUS untuk error yang terlihat seperti rate-limit
#    (retry bawaan litellm/openai SDK cuma beberapa kali dengan delay pendek - terbukti
#    tidak cukup untuk burst 50-100, lihat insiden 2026-07-26) - di sini retry lebih
#    banyak (5x) dengan jeda lebih panjang (mulai 3 detik, x1.6 tiap percobaan) SEBELUM
#    menyerah ke pemanggil (_run_chat_turn's fallback message tetap jaring pengaman
#    terakhir kalau retry di sini juga habis).
_LLM_CONCURRENCY_LIMIT = asyncio.Semaphore(6)
_RATE_LIMIT_MAX_RETRIES = 5
_RATE_LIMIT_BASE_DELAY = 3.0


def _looks_like_rate_limit(e: Exception) -> bool:
    msg = str(e).lower()
    return "rate limit" in msg or "ratelimiterror" in msg or "429" in msg or "too many requests" in msg


async def ai_reply(
    session_id: str,
    system_prompt: str,
    context_block: str,
    history_text: str,
    user_text: str,
    provider: str = DEFAULT_PROVIDER,
    model: str = DEFAULT_MODEL,
) -> str:
    """Single-shot call. History is passed as compacted text within the system prompt.

    provider/model dapat di-override lewat Settings (lihat GET/PUT /settings di server.py,
    field llm_provider/llm_model) - default konstanta di atas dipakai kalau belum diisi ATAU
    kalau provider yang dipilih ternyata tidak (lagi) punya API key dikonfigurasi (lihat
    LLM_PROVIDER_OPTIONS), supaya tidak pernah nyoba manggil provider dengan key yang salah."""
    if provider not in LLM_PROVIDER_OPTIONS:
        provider, model = DEFAULT_PROVIDER, DEFAULT_MODEL
    api_key = _provider_api_key(provider) or EMERGENT_LLM_KEY
    full_system = (
        f"{system_prompt}\n\n"
        f"=== KONTEKS SAAT INI ===\n{context_block}\n=== AKHIR KONTEKS ===\n\n"
        f"=== RIWAYAT PERCAKAPAN SEBELUMNYA ===\n"
        f"(baris berlabel \"Staf\" = pesan manual dari admin/resepsionis, BUKAN dari kamu (AI) - "
        f"perlakukan sebagai arahan/koreksi otoritatif yang WAJIB kamu ikuti, jangan diabaikan "
        f"atau dianggap sekadar riwayat percakapanmu sendiri.)\n"
        f"{history_text or '(kosong)'}\n=== AKHIR RIWAYAT ==="
    )
    chat = LlmChat(
        api_key=api_key,
        session_id=session_id,
        system_message=full_system,
    ).with_model(provider or DEFAULT_PROVIDER, model or DEFAULT_MODEL).with_params(temperature=0)
    # temperature=0 (2026-07-22, audit konsistensi AI) - sebelumnya tidak pernah di-set sama
    # sekali (default provider, biasanya 1.0) untuk mesin chat yang justru paling butuh
    # jawaban deterministik/tidak mengarang (harga, status booking, kebijakan). Beda dari
    # tugas ekstraksi terstruktur lain di PMS yang sudah pakai temperature=0 dari awal.

    async with _LLM_CONCURRENCY_LIMIT:
        attempt = 0
        while True:
            try:
                response = await chat.send_message(UserMessage(text=user_text))
                return response if isinstance(response, str) else str(response)
            except Exception as e:
                attempt += 1
                if attempt > _RATE_LIMIT_MAX_RETRIES or not _looks_like_rate_limit(e):
                    raise
                delay = _RATE_LIMIT_BASE_DELAY * (1.6 ** (attempt - 1))
                logging.getLogger("ai_overload").info(
                    f"Rate-limit dari provider (percobaan {attempt}/{_RATE_LIMIT_MAX_RETRIES}), "
                    f"tunggu {delay:.1f}s sebelum coba lagi (session {session_id})."
                )
                await asyncio.sleep(delay)


def compact_history(messages: List[dict], max_turns: int = 12) -> str:
    """Turn recent history into plain text for prompt.

    Pesan staf (`from_admin`, dikirim manual lewat halaman Percakapan) diberi label
    "Staf" sendiri, BUKAN ikut dilabel "AI" (2026-07-26, ditemukan lewat audit alur
    resume-AI - sebelumnya koreksi/instruksi staf tercampur seolah AI sendiri yang pernah
    bilang begitu, jadi AI tidak pernah tahu itu arahan otoritatif dari manusia saat
    percakapan diaktifkan lagi lewat "Aktifkan AI Lagi")."""
    tail = messages[-max_turns:]
    lines = []
    for m in tail:
        if m.get("role") == "user":
            role = "Tamu"
        elif m.get("from_admin"):
            role = "Staf"
        elif m.get("from_system"):
            # Notifikasi otomatis PMS (link pembayaran, voucher) - BUKAN ucapan AI sendiri,
            # supaya AI tidak menganggap dirinya sendiri yang pernah bilang begitu (2026-07-27).
            role = "Sistem"
        else:
            role = "AI"
        lines.append(f"{role}: {m.get('content','')}")
    return "\n".join(lines)
