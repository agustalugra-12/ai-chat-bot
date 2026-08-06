#!/bin/bash
# Dipanggil cron tiap 30 menit (2026-08-06, permintaan Agus setelah audit temukan
# handover yang tidak pernah ditindaklanjuti). Lihat escalate_stale_handovers.py utk
# detail lengkap. Log ke file terpisah spy bisa dicek kalau ada kegagalan tanpa perlu
# masuk journalctl.
cd /root/ai-chat-bot/backend || exit 1
set -a
source .env
set +a
venv/bin/python -m scripts.escalate_stale_handovers >> /var/log/ai_chat_bot_handover_escalation.log 2>&1
