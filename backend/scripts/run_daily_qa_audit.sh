#!/bin/bash
# Dipanggil cron 1x/hari (2026-08-06, permintaan Agus - "cari bug lain di chat bot,
# buat agent yang setiap hari memeriksanya agar tetap aman"). Lihat daily_qa_audit.py
# utk detail lengkap. Log ke file terpisah spy bisa dicek kalau ada kegagalan tanpa
# perlu masuk journalctl.
cd /root/ai-chat-bot/backend || exit 1
set -a
source .env
set +a
venv/bin/python -m scripts.daily_qa_audit >> /var/log/ai_chat_qa_audit.log 2>&1
