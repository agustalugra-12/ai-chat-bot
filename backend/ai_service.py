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
from typing import Optional, Dict, Any, List

from emergentintegrations.llm.chat import LlmChat, UserMessage

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "")
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_PROVIDER = "openai"


# SENGAJA cuma berisi persona/peran/data-access - GUARDRAIL, MENGIRIM FOTO, dan daftar
# Tool SELALU dirender fresh oleh build_dynamic_prompt() dari TOOL_DOCS/bot.guardrail_rules
# di bawah, satu sumber kebenaran, supaya tidak ada salinan beku yang basi kalau tool
# berubah (persis bug yang terjadi 2026-07-18: TOOL_DOCS sudah diedit tapi teks tool lama
# masih nyangkut di db.prompts/db.ai_bots.prompt karena disalin manual saat seed pertama).
DEFAULT_SYSTEM_PROMPT = """Anda adalah Pelangi AI, resepsionis digital ramah untuk Pelangi Homestay.

PERAN & GAYA:
- Selalu balas dalam Bahasa Indonesia yang sopan, hangat, dan singkat.
- Gunakan sapaan santai (Kak, Bapak/Ibu bila sesuai).
- Bantu tamu dengan: informasi hotel, cek ketersediaan, booking, ubah/batal booking, pesan layanan (extra bed, handuk, air mineral, cleaning, laundry, sewa motor, jemput bandara, breakfast tambahan), menu resto, dan pembayaran.

DATA YANG DIIZINKAN:
- Informasi hotel dari Knowledge Base
- Daftar kamar (nama, tipe, harga, kapasitas, fasilitas, status tersedia, foto)
- Menu restoran (nama, harga, kategori, status)
- Data booking milik tamu bersangkutan (verifikasi via WhatsApp + Booking ID)
- Dokumen referensi (SOP, manual) yang di-inject via bagian "DOKUMEN REFERENSI (RAG)"
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
TOOL_DOCS = {
    "check_availability": '- check_availability : args {"tanggal_checkin":"YYYY-MM-DD","tanggal_checkout":"YYYY-MM-DD" (opsional, hanya menginap >1 malam),"tipe":__ROOM_TIPE__ (opsional)}',
    "create_booking": '- create_booking (BUKAN booking final, cuma permintaan yang ditinjau resepsionis) : args {"guest_name":"...","whatsapp":"...","tipe":"day_use"|"menginap","room_tipe":__ROOM_TIPE__,"tanggal_checkin":"YYYY-MM-DD","jam_checkin":"HH:mm" (wajib jika day_use),"tanggal_checkout":"YYYY-MM-DD" (wajib jika menginap),"jumlah_kamar":1,"jumlah_tamu":1,"payment_option":"dp50"|"full" (opsional)}',
    "lookup_booking": '- lookup_booking : args {"whatsapp":"..."}',
    "cancel_booking": '- cancel_booking : args {"booking_id":"..."}',
    "create_service_request": '- create_service_request : args {"guest_name":"...","whatsapp":"...","service_type":"extra_bed|extra_towel|mineral_water|cleaning|laundry|motor_rental|airport_pickup|extra_breakfast","quantity":1,"notes":"..."}',
    "create_maintenance_ticket": '- create_maintenance_ticket (tiket masuk ke PMS, dipantau staf) : args {"tipe":"complaint"|"maintenance","deskripsi":"...","guest_name":"...","whatsapp":"..."}. Pakai "maintenance" utk kerusakan fasilitas (AC/TV/air/listrik dst), "complaint" utk keluhan pelayanan/kebersihan yang BUKAN kerusakan alat.',
    "request_handover": '- request_handover : args {"reason":"..."}',
    "remember_guest_fact": '- remember_guest_fact : args {"whatsapp":"...","fact":"..."}. WAJIB dipanggil SETIAP KALI tamu minta sesuatu "dicatat"/"diingat", atau menyebutkan preferensi kamar, alergi/pantangan, nama panggilan, kebiasaan yang relevan untuk kunjungan berikutnya. JANGAN PERNAH bilang "sudah saya catat"/"baik, dicatat" ke tamu TANPA benar-benar memanggil tool ini di baris yang sama - mengaku mencatat tanpa memanggil tool = BOHONG, datanya tidak benar-benar tersimpan. JANGAN dipakai untuk data booking/transaksi (itu sudah otomatis tersimpan di PMS) - HANYA fakta personal/preferensi tamu.',
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
    if "lookup_booking" in tool_codes:
        exposed.add("lookup_booking")
    if "cancel_booking" in tool_codes:
        exposed.add("cancel_booking")
    if "request_handover" in tool_codes:
        exposed.add("request_handover")
    # any service-request-like tool → expose create_service_request
    service_like = {"restaurant_order", "laundry_request", "housekeeping_request",
                    "room_service", "airport_pickup", "motor_rental"}
    if service_like.intersection(tool_codes):
        exposed.add("create_service_request")
    # maintenance_request/complaint_ticket → tiket masuk PMS (bukan db.service_requests lokal)
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

    return f"""{header}

## PERSONA
{persona_line}

## GUARDRAIL (WAJIB DIPATUHI)
{guard_block}

## MENGIRIM FOTO
Jika tamu meminta foto dan URL foto tersedia di KONTEKS ("Foto:"), sertakan sebagai marker:
[[IMG: https://...]]
Boleh beberapa marker. JANGAN mengarang URL.

## TOOLS YANG BOLEH DIPANGGIL
Format panggilan: baris terpisah di akhir balasan Anda:
[[TOOL: <nama_tool>]] {{"arg": "value"}}

Tool yang tersedia untuk Anda:
{tool_docs}{service_note}

Jika Anda mencoba tool di luar daftar di atas, sistem akan menolaknya.
Tulis balasan alami ke tamu dulu (1-4 kalimat), lalu marker [[IMG: ...]] bila kirim foto, lalu baris [[TOOL: ...]] bila perlu aksi.
"""


def build_context_block(rooms: List[dict], menu: List[dict], kb: List[dict], settings: dict) -> str:
    """Build a compact context string for the AI."""
    parts = []
    parts.append(f"# INFO HOTEL\nNama: {settings.get('hotel_name','Pelangi Homestay')}\n"
                 f"Alamat: {settings.get('address','-')}\n"
                 f"Check-in: {settings.get('checkin_time','14:00')} | Check-out: {settings.get('checkout_time','12:00')}\n"
                 f"Telepon: {settings.get('phone','-')}\n")

    if rooms:
        # Data live dari Pelangi PMS (bukan data lokal ai-chat-bot) - lihat _pms_ketersediaan
        # di server.py. Skema: {"tipe","tarif_day_use","tarif_menginap","kamar_tersedia"}.
        parts.append(f"# KETERSEDIAAN KAMAR HARI INI ({rooms[0].get('_tanggal', '-')}, live dari PMS)")
        for r in rooms:
            parts.append(
                f"- Tipe {r['tipe']}: {r['kamar_tersedia']} kamar kosong | "
                f"Day Use Rp {int(r['tarif_day_use']):,} (6 jam) | Menginap Rp {int(r['tarif_menginap']):,}/malam"
            )
        parts.append(
            "(Ini snapshot HARI INI saja - untuk tanggal lain atau tipe kamar yang di sini tampil "
            "0 kamar kosong, WAJIB panggil tool check_availability, jangan menyimpulkan dari data di atas.)"
        )

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


def parse_tool_call(response_text: str):
    """Return (clean_text, tool_name, args) if the model appended a tool call."""
    m = re.search(r"\[\[TOOL:\s*([a-z_]+)\s*\]\]\s*(\{.*?\})\s*$", response_text.strip(), re.DOTALL)
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


async def ai_reply(
    session_id: str,
    system_prompt: str,
    context_block: str,
    history_text: str,
    user_text: str,
) -> str:
    """Single-shot call. History is passed as compacted text within the system prompt."""
    full_system = (
        f"{system_prompt}\n\n"
        f"=== KONTEKS SAAT INI ===\n{context_block}\n=== AKHIR KONTEKS ===\n\n"
        f"=== RIWAYAT PERCAKAPAN SEBELUMNYA ===\n{history_text or '(kosong)'}\n=== AKHIR RIWAYAT ==="
    )
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=session_id,
        system_message=full_system,
    ).with_model(DEFAULT_PROVIDER, DEFAULT_MODEL)

    response = await chat.send_message(UserMessage(text=user_text))
    return response if isinstance(response, str) else str(response)


def compact_history(messages: List[dict], max_turns: int = 12) -> str:
    """Turn recent history into plain text for prompt."""
    tail = messages[-max_turns:]
    lines = []
    for m in tail:
        role = "Tamu" if m.get("role") == "user" else "AI"
        lines.append(f"{role}: {m.get('content','')}")
    return "\n".join(lines)
