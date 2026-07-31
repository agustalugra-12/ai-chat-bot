"""Connector Layer - integrasi ke sistem bisnis eksternal (Business System).

Setiap connector adalah modul mandiri yang tahu cara "bicara" ke satu sistem luar
(protokol/auth/format request-nya), dipanggil dari server.py (AI Customer Platform)
lewat fungsi biasa - server.py TIDAK PERNAH tahu detail HTTP/auth sistem luar,
cukup panggil connector-nya.

- pms_connector: Pelangi PMS (ketersediaan, booking request, tiket, status booking,
  business rules) - lihat backend/routes/integrasi_ai_bot.py di repo PMS untuk sisinya.
- fonnte_connector: Fonnte (WhatsApp gateway unofficial) - channel adapter aktif utk
  kirim pesan (WAHA lama dihapus 2026-08-01, digantikan sepenuhnya oleh ini).
- whatsapp_cloud_connector: WhatsApp Cloud API resmi Meta - channel alternatif.
"""
