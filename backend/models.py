"""Pydantic models for Pelangi Homestay Guest AI."""
from typing import List, Optional
from pydantic import BaseModel, Field, EmailStr
from datetime import datetime

from db import BaseDocument, new_id, utc_now_iso


# ---------- Users ----------
class User(BaseDocument):
    email: str
    password_hash: str
    name: str
    role: str  # "admin" | "super_admin"
    created_at: str = Field(default_factory=utc_now_iso)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    token: str
    user: dict


# ---------- Knowledge Base ----------
KB_CATEGORIES = [
    "faq", "policy", "checkin", "checkout", "facilities", "location",
    "attractions", "parking", "breakfast", "laundry", "motor_rental",
    "airport_pickup", "promo",
]


class KnowledgeItem(BaseDocument):
    category: str
    title: str
    content: str
    is_active: bool = True
    images: List[dict] = Field(default_factory=list)  # [{url, public_id}]
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class KnowledgeItemIn(BaseModel):
    category: str
    title: str
    content: str
    is_active: bool = True
    images: List[dict] = Field(default_factory=list)


# ---------- Rooms ----------
class Room(BaseDocument):
    name: str
    room_type: str
    price_per_night: float
    capacity: int
    photo_url: Optional[str] = None
    images: List[dict] = Field(default_factory=list)  # [{url, public_id}]
    facilities: List[str] = Field(default_factory=list)
    total_units: int = 1
    is_available: bool = True
    description: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)


class RoomIn(BaseModel):
    name: str
    room_type: str
    price_per_night: float
    capacity: int
    photo_url: Optional[str] = None
    images: List[dict] = Field(default_factory=list)
    facilities: List[str] = Field(default_factory=list)
    total_units: int = 1
    is_available: bool = True
    description: Optional[str] = None


# ---------- Restaurant Menu ----------
class MenuItem(BaseDocument):
    name: str
    category: str  # "food" | "beverage" | "snack" | ...
    price: float
    description: Optional[str] = None
    is_available: bool = True
    is_sold_out: bool = False
    photo_url: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)


class MenuItemIn(BaseModel):
    name: str
    category: str
    price: float
    description: Optional[str] = None
    is_available: bool = True
    is_sold_out: bool = False
    photo_url: Optional[str] = None


# ---------- Booking ----------
class Booking(BaseDocument):
    guest_name: str
    whatsapp: str
    check_in: str  # ISO date
    check_out: str  # ISO date
    room_type: str
    room_ids: List[str] = Field(default_factory=list)
    num_rooms: int = 1
    num_guests: int = 1
    status: str = "pending"  # pending | confirmed | cancelled
    total_amount: float = 0.0
    dp_amount: float = 0.0
    payment_status: str = "unpaid"  # unpaid | partial | paid
    notes: Optional[str] = None
    source: str = "manual"  # ai | manual
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class BookingIn(BaseModel):
    guest_name: str
    whatsapp: str
    check_in: str
    check_out: str
    room_type: str
    num_rooms: int = 1
    num_guests: int = 1
    total_amount: float = 0.0
    dp_amount: float = 0.0
    notes: Optional[str] = None
    source: str = "manual"


class BookingUpdate(BaseModel):
    guest_name: Optional[str] = None
    whatsapp: Optional[str] = None
    check_in: Optional[str] = None
    check_out: Optional[str] = None
    room_type: Optional[str] = None
    num_rooms: Optional[int] = None
    num_guests: Optional[int] = None
    status: Optional[str] = None
    total_amount: Optional[float] = None
    dp_amount: Optional[float] = None
    payment_status: Optional[str] = None
    notes: Optional[str] = None


# ---------- Service Request ----------
SERVICE_TYPES = [
    "extra_bed", "extra_towel", "mineral_water", "cleaning",
    "laundry", "motor_rental", "airport_pickup", "extra_breakfast",
]


class ServiceRequest(BaseDocument):
    guest_name: str
    whatsapp: str
    booking_id: Optional[str] = None
    service_type: str
    quantity: int = 1
    notes: Optional[str] = None
    status: str = "new"  # new | in_progress | done | cancelled
    created_at: str = Field(default_factory=utc_now_iso)


class ServiceRequestIn(BaseModel):
    guest_name: str
    whatsapp: str
    booking_id: Optional[str] = None
    service_type: str
    quantity: int = 1
    notes: Optional[str] = None


class ServiceRequestUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None


# ---------- Conversation ----------
class ChatMessage(BaseModel):
    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: str = Field(default_factory=utc_now_iso)
    intent: Optional[str] = None


class Conversation(BaseDocument):
    session_id: str
    guest_name: Optional[str] = None
    whatsapp: Optional[str] = None
    channel: str = "simulator"  # simulator | whatsapp
    messages: List[ChatMessage] = Field(default_factory=list)
    status: str = "active"  # active | waiting_admin | closed
    resolution: str = "unresolved"  # ai_resolved | handover | unresolved
    booking_created: bool = False
    last_intent: Optional[str] = None
    response_time_ms: int = 0
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class ChatSendRequest(BaseModel):
    session_id: Optional[str] = None
    message: str
    guest_name: Optional[str] = None
    whatsapp: Optional[str] = None
    bot_id: Optional[str] = None
    bot_code: Optional[str] = None


# ---------- Prompt Management ----------
class PromptVersion(BaseDocument):
    version: int
    content: str
    is_active: bool = False
    created_by: Optional[str] = None
    created_at: str = Field(default_factory=utc_now_iso)


class PromptIn(BaseModel):
    content: str


# ---------- Settings ----------
class Settings(BaseDocument):
    hotel_name: str = "Pelangi Homestay"
    address: str = ""
    phone: str = ""
    email: str = ""
    checkin_time: str = "14:00"
    checkout_time: str = "12:00"
    whatsapp_enabled: bool = False
    show_stock_count: bool = False
    updated_at: str = Field(default_factory=utc_now_iso)


class SettingsIn(BaseModel):
    hotel_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    maps_url: Optional[str] = None
    checkin_time: Optional[str] = None
    checkout_time: Optional[str] = None
    whatsapp_enabled: Optional[bool] = None
    show_stock_count: Optional[bool] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None


# ---------- Availability ----------
class AvailabilityQuery(BaseModel):
    check_in: str
    check_out: str
    room_type: Optional[str] = None


# ---------- AI Bot Management (V2) ----------
BOT_STATUSES = ["active", "inactive", "maintenance"]

TOOL_CATALOG_DEFAULT = [
    {"code": "check_availability", "name": "Availability", "description": "Cek ketersediaan kamar berdasarkan tanggal", "endpoint": "GET /api/guest/availability", "category": "booking"},
    {"code": "create_booking", "name": "Booking", "description": "Membuat reservasi baru", "endpoint": "POST /api/bookings", "category": "booking"},
    {"code": "lookup_booking", "name": "Lookup Booking", "description": "Melihat booking milik tamu", "endpoint": "GET /api/guest/bookings", "category": "booking"},
    {"code": "cancel_booking", "name": "Cancel Booking", "description": "Membatalkan booking (butuh verifikasi)", "endpoint": "PUT /api/bookings/{id}", "category": "booking"},
    {"code": "send_payment_link", "name": "Payment Link", "description": "Kirim link pembayaran ke tamu (placeholder)", "endpoint": "POST /api/payment/link", "category": "payment"},
    {"code": "send_invoice", "name": "Invoice", "description": "Kirim invoice ke tamu (placeholder)", "endpoint": "POST /api/invoice", "category": "payment"},
    {"code": "show_promotion", "name": "Promotion", "description": "Menampilkan promo yang berlaku", "endpoint": "GET /api/knowledge-base?category=promo", "category": "marketing"},
    {"code": "faq_answer", "name": "FAQ", "description": "Menjawab pertanyaan umum dari KB & RAG", "endpoint": "internal", "category": "info"},
    {"code": "restaurant_order", "name": "Restaurant Order", "description": "Membuat pesanan menu restoran", "endpoint": "POST /api/service-requests", "category": "guest_service"},
    {"code": "laundry_request", "name": "Laundry", "description": "Request laundry", "endpoint": "POST /api/service-requests", "category": "guest_service"},
    {"code": "housekeeping_request", "name": "Housekeeping", "description": "Request cleaning / turn-down", "endpoint": "POST /api/service-requests", "category": "guest_service"},
    {"code": "maintenance_request", "name": "Maintenance", "description": "Laporan kerusakan fasilitas kamar", "endpoint": "POST /api/service-requests", "category": "guest_service"},
    {"code": "complaint_ticket", "name": "Complaint", "description": "Membuat ticket keluhan tamu", "endpoint": "POST /api/service-requests", "category": "guest_service"},
    {"code": "room_service", "name": "Room Service", "description": "Extra bed, extra towel, mineral water", "endpoint": "POST /api/service-requests", "category": "guest_service"},
    {"code": "airport_pickup", "name": "Airport Pickup", "description": "Jadwalkan jemput/antar bandara", "endpoint": "POST /api/service-requests", "category": "guest_service"},
    {"code": "motor_rental", "name": "Motor Rental", "description": "Sewa motor tamu", "endpoint": "POST /api/service-requests", "category": "guest_service"},
    {"code": "knowledge_lookup", "name": "Knowledge Lookup", "description": "Cari info dari RAG documents", "endpoint": "internal", "category": "info"},
    {"code": "request_handover", "name": "Human Handover", "description": "Alihkan percakapan ke admin", "endpoint": "PATCH /api/conversations/{id}/handover", "category": "system"},
]

INTENT_CATALOG_DEFAULT = [
    {"code": "BOOK_ROOM", "name": "Book Room", "description": "Tamu ingin membuat booking", "tool_codes": ["create_booking", "check_availability"]},
    {"code": "CHECK_AVAILABILITY", "name": "Check Availability", "description": "Cek ketersediaan kamar", "tool_codes": ["check_availability"]},
    {"code": "CHECK_PRICE", "name": "Check Price", "description": "Cek harga & promo", "tool_codes": ["show_promotion", "faq_answer"]},
    {"code": "MODIFY_BOOKING", "name": "Modify Booking", "description": "Ubah tanggal / kamar booking", "tool_codes": ["lookup_booking", "create_booking"]},
    {"code": "CANCEL_BOOKING", "name": "Cancel Booking", "description": "Batalkan booking", "tool_codes": ["lookup_booking", "cancel_booking"]},
    {"code": "ORDER_FOOD", "name": "Order Food", "description": "Pesan menu resto", "tool_codes": ["restaurant_order"]},
    {"code": "ORDER_LAUNDRY", "name": "Order Laundry", "description": "Request laundry", "tool_codes": ["laundry_request"]},
    {"code": "REQUEST_CLEANING", "name": "Request Cleaning", "description": "Minta housekeeping", "tool_codes": ["housekeeping_request", "room_service"]},
    {"code": "REPORT_DAMAGE", "name": "Report Damage", "description": "Laporkan kerusakan", "tool_codes": ["maintenance_request"]},
    {"code": "REQUEST_PICKUP", "name": "Request Pickup", "description": "Minta jemput bandara", "tool_codes": ["airport_pickup"]},
    {"code": "REQUEST_RENTAL", "name": "Request Rental", "description": "Sewa motor", "tool_codes": ["motor_rental"]},
    {"code": "COMPLAINT", "name": "Complaint", "description": "Tamu mengeluh", "tool_codes": ["complaint_ticket", "request_handover"]},
    {"code": "GENERAL_FAQ", "name": "General FAQ", "description": "Pertanyaan umum", "tool_codes": ["faq_answer", "knowledge_lookup"]},
]


class WorkflowStep(BaseModel):
    order: int
    name: str
    description: Optional[str] = ""


class Workflow(BaseDocument):
    code: str
    name: str
    description: Optional[str] = ""
    steps: List[WorkflowStep] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class WorkflowIn(BaseModel):
    code: Optional[str] = None
    name: str
    description: Optional[str] = ""
    steps: List[WorkflowStep] = Field(default_factory=list)


class ToolCatalogItem(BaseDocument):
    code: str
    name: str
    description: Optional[str] = ""
    endpoint: Optional[str] = ""
    category: str = "general"
    status: str = "active"  # active | inactive | maintenance


class ToolIn(BaseModel):
    code: Optional[str] = None
    name: str
    description: Optional[str] = ""
    endpoint: Optional[str] = ""
    category: str = "general"
    status: str = "active"


class IntentCatalogItem(BaseDocument):
    code: str
    name: str
    description: Optional[str] = ""
    tool_codes: List[str] = Field(default_factory=list)


class IntentIn(BaseModel):
    code: Optional[str] = None
    name: str
    description: Optional[str] = ""
    tool_codes: List[str] = Field(default_factory=list)


class AIBot(BaseDocument):
    code: str  # unique slug
    name: str
    description: Optional[str] = ""
    persona: Optional[str] = ""
    status: str = "active"
    language: str = "id"
    channel_type: Optional[str] = None  # whatsapp | telegram | website | mobile | simulator
    channel_id: Optional[str] = ""
    prompt: str = ""
    workflow_id: Optional[str] = None
    tool_codes: List[str] = Field(default_factory=list)
    knowledge_categories: List[str] = Field(default_factory=list)
    allowed_service_types: List[str] = Field(default_factory=list)
    guardrail_rules: List[str] = Field(default_factory=list)
    allowed_intents: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class AIBotIn(BaseModel):
    code: Optional[str] = None
    name: str
    description: Optional[str] = ""
    persona: Optional[str] = ""
    status: str = "active"
    language: str = "id"
    channel_type: Optional[str] = None
    channel_id: Optional[str] = ""
    prompt: str = ""
    workflow_id: Optional[str] = None
    tool_codes: List[str] = Field(default_factory=list)
    knowledge_categories: List[str] = Field(default_factory=list)
    allowed_service_types: List[str] = Field(default_factory=list)
    guardrail_rules: List[str] = Field(default_factory=list)
    allowed_intents: List[str] = Field(default_factory=list)


class AIBotUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    persona: Optional[str] = None
    status: Optional[str] = None
    language: Optional[str] = None
    channel_type: Optional[str] = None
    channel_id: Optional[str] = None
    prompt: Optional[str] = None
    workflow_id: Optional[str] = None
    tool_codes: Optional[List[str]] = None
    knowledge_categories: Optional[List[str]] = None
    allowed_service_types: Optional[List[str]] = None
    guardrail_rules: Optional[List[str]] = None
    allowed_intents: Optional[List[str]] = None
