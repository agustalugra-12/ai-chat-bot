"""Connector Layer - integrasi ke sistem bisnis eksternal (Business System).

Setiap connector adalah modul mandiri yang tahu cara "bicara" ke satu sistem luar
(protokol/auth/format request-nya), dipanggil dari server.py (AI Customer Platform)
lewat fungsi biasa - server.py TIDAK PERNAH tahu detail HTTP/auth sistem luar,
cukup panggil connector-nya.

- pms_connector: Pelangi PMS (ketersediaan, booking request, tiket, status booking,
  business rules) - lihat backend/routes/integrasi_ai_bot.py di repo PMS untuk sisinya.
- waha_connector: WAHA (WhatsApp gateway self-hosted) - channel adapter untuk kirim
  pesan & kelola sesi WhatsApp.
"""
