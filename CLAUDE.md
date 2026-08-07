# AI Chat Bot — Working Agreement untuk Claude Code

Dibaca otomatis tiap sesi Claude Code dibuka di folder ini. Repo ini terpisah dari Pelangi
PMS (`/root/agusta`) - AI WhatsApp chat bot untuk Pelangi Homestay + Harmoni Hills, tiap
properti punya nomor WA & bot sendiri, terhubung ke PMS lewat API key per-bot.

## Peran & Mode Kerja

Sama seperti PMS: Lead Full Stack Engineer, mode otonom untuk keputusan teknis kecil,
berhenti & tanya cuma untuk keputusan bisnis (perubahan alur/kebijakan, biaya berulang
yang signifikan, kredensial/akses baru, perubahan prompt/perilaku AI yang besar).

**Tech stack**: FastAPI + Python (async, motor/MongoDB) + LiteLLM (OpenAI, model default
`gpt-4.1-mini`, eskalasi ke `gpt-4.1` untuk topik rawan - lihat Model Router di
`ai_service.py`). Deploy: `systemctl restart ai-chat-bot-backend.service` manual setelah
push (repo ini TIDAK punya GitHub Actions auto-deploy, beda dari PMS - selalu restart
manual, jangan asumsikan ada pipeline otomatis).

## WAJIB: Regression Gate Sebelum Restart

**Sebelum me-restart `ai-chat-bot-backend.service` setelah mengubah PERILAKU AI apa pun**
(guard di `server.py`, prompt/TOOL_DOCS di `ai_service.py`, koneksi PMS di
`connectors/pms_connector.py`), **WAJIB** jalankan dulu:

```bash
cd backend && venv/bin/python -m scripts.test_hallucination_guards
```

Kalau ada `FAIL` (exit code 1) - **JANGAN restart**, perbaiki dulu. Ini bukan saran,
ini gerbang wajib (Modul 19 PRD "AI Self-Healing & Bug Prevention" usulan Agus,
2026-08-07 - lihat riwayat commit repo ini untuk konteks lengkap).

Skrip ini berisi 2 jenis tes, keduanya WAJIB tetap hijau:
1. **Skenario LIVE** (lewat Chat Simulator, `_run_chat_turn`) - membandingkan balasan AI
   terhadap data ASLI dari PMS (ground truth). Menguji apakah PROMPT+MODEL saat ini masih
   patuh terhadap bug yang PERNAH ditemukan nyata.
2. **Unit test murni** (sync, tanpa DB/LLM) - menguji fungsi guard (regex deteksi +
   substitusi) itu sendiri terhadap regresi. Insiden nyata yang mendasari ini: guard baru
   pernah tanpa sengaja merusak guard lama yang sudah ada.

**Kalau menambah guard baru** di `server.py`: kalau guard itu murni logika kode (regex +
substitusi, bukan butuh panggilan LLM/PMS), **ekstrak jadi fungsi murni** (pola:
`_cek_kontradiksi_total()`) supaya bisa di-unit-test langsung tanpa biaya API - jangan
biarkan inline di tengah fungsi besar seperti guard-guard lama (itu utang teknis warisan,
bukan pola yang harus diikuti untuk kode baru).

**Kalau menemukan bug perilaku AI nyata** (laporan Agus atau audit sendiri): tambahkan
skenario regresi baru ke `test_hallucination_guards.py` yang merepro bug itu (ground truth
dari PMS asli, bukan asumsi) SEBELUM memperbaiki guard-nya - supaya begitu fix selesai,
langsung ada bukti otomatis bug itu tidak akan lolos lagi diam-diam di masa depan.

## Skala & Biaya (penting untuk keputusan proporsi)

Bot ini melayani ~39 percakapan/24 jam gabungan 2 properti, biaya LLM harian sekitar
Rp20.000-an. **Jangan usulkan/bangun sesuatu yang skalanya jauh melebihi ini** (mis. ribuan
percakapan simulasi per malam) tanpa mengecek dulu angka traffic real terkini - keputusan
skala HARUS berdasar data live, bukan asumsi "supaya aman", karena biaya API riil bisa jadi
tidak proporsional untuk bisnis skala ini.

## Audit Harian Otomatis

`scripts/daily_qa_audit.py` jalan tiap hari jam 08:17 WIB (cron) - scan 26 jam percakapan
terakhir, deteksi klaim fasilitas karangan (auto-tambal jadi guardrail_rules kalau aman),
cek handover macet >3 jam, plus audit AI Blog & KontenPilot dalam run yang sama. Kirim
ringkasan ke Telegram owner kalau ada temuan.
