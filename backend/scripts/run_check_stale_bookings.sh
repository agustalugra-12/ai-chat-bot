#!/bin/bash
# Dipanggil cron tiap 30 menit (2026-08-04, permintaan Agus - PRD "deteksi & koreksi
# otomatis") - lihat check_stale_bookings.py utk detail lengkap. Log ke file terpisah
# spy bisa dicek kalau ada kegagalan tanpa perlu masuk journalctl.
cd /root/ai-chat-bot/backend || exit 1
set -a
source .env
set +a
venv/bin/python -m scripts.check_stale_bookings >> /var/log/ai_chat_bot_stale_bookings.log 2>&1
