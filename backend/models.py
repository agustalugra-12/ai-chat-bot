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
    checkin_time: Optional[str] = None
    checkout_time: Optional[str] = None
    whatsapp_enabled: Optional[bool] = None
    show_stock_count: Optional[bool] = None


# ---------- Availability ----------
class AvailabilityQuery(BaseModel):
    check_in: str
    check_out: str
    room_type: Optional[str] = None
