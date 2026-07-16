"""Seed initial data on startup: default admins, default rooms, sample menu, KB, prompt, settings."""
from auth import hash_password
from db import new_id, utc_now_iso


DEFAULT_KB = [
    {"category": "checkin", "title": "Waktu Check-in",
     "content": "Check-in mulai pukul 14:00 WIB. Early check-in tergantung ketersediaan kamar."},
    {"category": "checkout", "title": "Waktu Check-out",
     "content": "Check-out paling lambat pukul 12:00 WIB. Late check-out dikenakan biaya 50% dari tarif kamar."},
    {"category": "breakfast", "title": "Breakfast",
     "content": "Sarapan disajikan pukul 07:00 - 10:00 WIB di area resto. Menu: nasi goreng, roti bakar, teh/kopi. Sudah termasuk untuk tipe kamar Deluxe & Suite."},
    {"category": "parking", "title": "Parkir",
     "content": "Parkir gratis untuk motor dan mobil. Kapasitas terbatas, first come first served."},
    {"category": "location", "title": "Lokasi",
     "content": "Jl. Melati No. 12, Ubud, Bali. 15 menit dari pusat kota, 5 menit ke Monkey Forest."},
    {"category": "facilities", "title": "Fasilitas Umum",
     "content": "Kolam renang, resto, garden, free WiFi, resepsionis 24 jam, laundry service, sewa motor."},
    {"category": "airport_pickup", "title": "Jemput Bandara",
     "content": "Layanan antar-jemput bandara Rp 250.000 (Ngurah Rai). Booking minimal 1 hari sebelumnya via WhatsApp."},
    {"category": "motor_rental", "title": "Sewa Motor",
     "content": "Scooter matic tersedia Rp 80.000/hari (Beat/Vario). Wajib SIM & KTP/paspor. Bensin diisi pelanggan."},
    {"category": "laundry", "title": "Laundry",
     "content": "Laundry kiloan Rp 15.000/kg (regular 24 jam) atau Rp 25.000/kg (express 6 jam)."},
    {"category": "promo", "title": "Promo Weekday",
     "content": "Diskon 15% untuk booking Senin-Kamis, minimal 2 malam."},
    {"category": "policy", "title": "Kebijakan Pembatalan",
     "content": "Pembatalan H-3 sebelum check-in: refund 100% DP. H-1: refund 50%. Hari-H: hangus."},
    {"category": "attractions", "title": "Tempat Wisata Sekitar",
     "content": "Monkey Forest (5 menit), Tegalalang Rice Terrace (20 menit), Ubud Palace (10 menit), Campuhan Ridge Walk (15 menit)."},
]

DEFAULT_ROOMS = [
    {"name": "Standard Room", "room_type": "standard", "price_per_night": 350000, "capacity": 2,
     "photo_url": "https://images.unsplash.com/photo-1582719478250-c89cae4dc85b?w=800",
     "facilities": ["AC", "WiFi", "TV", "Kamar mandi dalam"], "total_units": 4, "is_available": True,
     "description": "Kamar nyaman untuk 2 orang dengan pemandangan taman."},
    {"name": "Deluxe Room", "room_type": "deluxe", "price_per_night": 550000, "capacity": 2,
     "photo_url": "https://images.unsplash.com/photo-1611892440504-42a792e24d32?w=800",
     "facilities": ["AC", "WiFi", "TV", "Kamar mandi dalam", "Balkon", "Breakfast included"],
     "total_units": 3, "is_available": True,
     "description": "Kamar lebih luas dengan balkon pribadi menghadap kolam renang."},
    {"name": "Family Suite", "room_type": "suite", "price_per_night": 950000, "capacity": 4,
     "photo_url": "https://images.unsplash.com/photo-1618773928121-c32242e63f39?w=800",
     "facilities": ["AC", "WiFi", "Smart TV", "Ruang tamu", "2 Kamar mandi", "Breakfast included"],
     "total_units": 2, "is_available": True,
     "description": "Suite dengan 2 kamar tidur, cocok untuk keluarga hingga 4 orang."},
]

DEFAULT_MENU = [
    {"name": "Nasi Goreng Pelangi", "category": "food", "price": 45000, "is_available": True, "is_sold_out": False,
     "description": "Nasi goreng khas homestay dengan telur mata sapi, ayam, dan kerupuk."},
    {"name": "Mie Goreng Jawa", "category": "food", "price": 40000, "is_available": True, "is_sold_out": False,
     "description": "Mie goreng dengan sayuran segar dan ayam suwir."},
    {"name": "Ayam Bakar Bumbu Bali", "category": "food", "price": 65000, "is_available": True, "is_sold_out": False,
     "description": "Ayam bakar dengan bumbu bali lengkap dengan nasi, sambal matah, dan lalapan."},
    {"name": "Gado-Gado", "category": "food", "price": 35000, "is_available": True, "is_sold_out": False,
     "description": "Salad sayuran khas Indonesia dengan bumbu kacang."},
    {"name": "Es Kelapa Muda", "category": "beverage", "price": 25000, "is_available": True, "is_sold_out": False,
     "description": "Kelapa muda segar dengan sirup."},
    {"name": "Kopi Bali", "category": "beverage", "price": 20000, "is_available": True, "is_sold_out": False,
     "description": "Kopi lokal Bali diseduh manual brew."},
    {"name": "Jus Alpukat", "category": "beverage", "price": 25000, "is_available": True, "is_sold_out": True,
     "description": "Jus alpukat segar dengan susu coklat."},
    {"name": "Pisang Goreng", "category": "snack", "price": 20000, "is_available": True, "is_sold_out": False,
     "description": "Pisang goreng crispy dengan gula palem."},
]


async def seed_all(db):
    # Users
    users_col = db["users"]
    if await users_col.count_documents({}) == 0:
        await users_col.insert_many([
            {
                "_id": new_id(),
                "email": "superadmin@pelangi.id",
                "password_hash": hash_password("Super123!"),
                "name": "Super Admin",
                "role": "super_admin",
                "created_at": utc_now_iso(),
            },
            {
                "_id": new_id(),
                "email": "admin@pelangi.id",
                "password_hash": hash_password("Admin123!"),
                "name": "Pelangi Admin",
                "role": "admin",
                "created_at": utc_now_iso(),
            },
        ])

    # Knowledge Base
    kb_col = db["knowledge_base"]
    if await kb_col.count_documents({}) == 0:
        docs = []
        for k in DEFAULT_KB:
            docs.append({"_id": new_id(), **k, "is_active": True,
                         "created_at": utc_now_iso(), "updated_at": utc_now_iso()})
        await kb_col.insert_many(docs)

    # Rooms
    rooms_col = db["rooms"]
    if await rooms_col.count_documents({}) == 0:
        docs = []
        for r in DEFAULT_ROOMS:
            docs.append({"_id": new_id(), **r, "created_at": utc_now_iso()})
        await rooms_col.insert_many(docs)

    # Menu
    menu_col = db["menu"]
    if await menu_col.count_documents({}) == 0:
        docs = []
        for m in DEFAULT_MENU:
            docs.append({"_id": new_id(), **m, "created_at": utc_now_iso()})
        await menu_col.insert_many(docs)

    # Prompt
    prompt_col = db["prompts"]
    if await prompt_col.count_documents({}) == 0:
        from ai_service import DEFAULT_SYSTEM_PROMPT
        await prompt_col.insert_one({
            "_id": new_id(),
            "version": 1,
            "content": DEFAULT_SYSTEM_PROMPT,
            "is_active": True,
            "created_at": utc_now_iso(),
        })

    # Settings
    settings_col = db["settings"]
    if await settings_col.count_documents({}) == 0:
        await settings_col.insert_one({
            "_id": "singleton",
            "hotel_name": "Pelangi Homestay",
            "address": "Jl. Melati No. 12, Ubud, Bali",
            "phone": "+62 361 000 1234",
            "email": "hello@pelangi.id",
            "checkin_time": "14:00",
            "checkout_time": "12:00",
            "whatsapp_enabled": False,
            "show_stock_count": False,
            "updated_at": utc_now_iso(),
        })
