# Pelangi Homestay Guest AI — PRD (living)

## Original Problem Statement
Membangun **AI Guest Assistant** yang terintegrasi dengan PMS Pelangi Homestay untuk melayani tamu secara otomatis. AI berfungsi sebagai resepsionis digital yang menjawab pertanyaan, membuat booking, mengecek ketersediaan, mengelola reservasi, memberikan info fasilitas, dan membantu kebutuhan tamu. AI hanya melayani tamu — tidak boleh mengakses data internal bisnis (revenue, occupancy, staff, dll).

## Architecture
- **Backend**: FastAPI (`/app/backend/server.py`) + MongoDB via Motor
- **Frontend**: React 19 + Tailwind + shadcn/ui + Recharts (`/app/frontend`)
- **AI**: OpenAI GPT-5.4-mini via `emergentintegrations` LlmChat (Emergent Universal Key)
- **Auth**: JWT + bcrypt (roles: `admin`, `super_admin`)
- **Channel**: Chat Simulator (WhatsApp integration ready, disabled by default)

## Personas
- **Super Admin** – full access incl. delete bookings
- **Admin** – all modules, no destructive booking deletion
- **Guest AI (Pelangi AI)** – automated agent, tightly guardrailed

## Core Requirements (static)
1. Modul Knowledge Base (13 kategori, CRUD)
2. Modul Room (CRUD, foto, fasilitas, status availability)
3. Modul Availability (`GET /api/guest/availability`)
4. Modul Booking (create/edit/cancel, status pending/confirmed/cancelled, source AI vs manual)
5. Modul Restaurant Menu (CRUD + sold-out flag)
6. Modul Service Request (8 tipe: extra_bed, extra_towel, mineral_water, cleaning, laundry, motor_rental, airport_pickup, extra_breakfast)
7. Modul Booking Verification (WhatsApp + Booking ID)
8. Conversation History (semua sesi tersimpan)
9. Human Handover (`waiting_admin`)
10. Prompt Management (versioned)
11. AI Guardrail (menolak permintaan data internal)
12. Dashboard Admin dengan 11 menu
13. Analytics (total conv, resolution rate, handover, bookings AI, conversion, response time, top intents, daily series)

## Implemented — 2026-02

### Iteration 1 (MVP)
- ✅ Auth (JWT, bcrypt, seeded admin + super_admin)
- ✅ Knowledge Base CRUD (seeded 12 items)
- ✅ Rooms CRUD (seeded 3 rooms)
- ✅ Restaurant Menu CRUD (seeded 8 items)
- ✅ Bookings CRUD + auto-price calculation
- ✅ Availability endpoint (with optional stock display)
- ✅ Service Requests CRUD
- ✅ Conversations viewer + handover
- ✅ Chat Simulator (WhatsApp-style UI)
- ✅ AI Guest Assistant with **tool calls**: check_availability, create_booking, create_service_request, lookup_booking, request_handover
- ✅ Prompt Management (versioning + activate)
- ✅ Analytics dashboard (recharts)
- ✅ Settings (hotel info + AI options)
- ✅ AI Guardrail (refuses internal data)

### Iteration 2 (Photos + RAG)
- ✅ **Cloudinary photo upload** — reusable `ImageUploader` in KB items & Rooms (max 5-6 per item)
- ✅ **AI sends photos in chat** — `[[IMG: url]]` marker parsing + host-based Cloudinary/Unsplash detection in `ChatMessageContent`
- ✅ **RAG for PDF/DOCX/TXT** — upload SOP/manual → auto-extract text (pypdf/python-docx) → chunk 600 chars → BM25 index
- ✅ **RAG Documents admin page** — upload, list, delete, live retrieval preview
- ✅ AI context automatically augmented with top-5 BM25 hits per query
- ✅ 100% test coverage (iteration 1 + iteration 2)

## Backlog / Next
### P0
- WhatsApp channel integration (Twilio / Meta Cloud API / VPS webhook)
- Payment link generation & invoice PDF (`create_payment_link` tool)

### P1
- RAG for FAQ (upload PDF/DOCX → embed → vector search)
- Streaming SSE responses in Chat Simulator (currently non-streaming)
- Multi-language toggle (currently Bahasa Indonesia only)
- Booking payment webhook + payment_status auto-update

### P2
- Telegram Owner AI dashboard (separate persona, business analytics)
- Mobile app / website chat widget SDK
- Voice assistant (OpenAI TTS/STT)
- Two-factor authentication for admins
- Rate limiting on chat endpoint

## Next Action Items
- Hubungkan channel WhatsApp Anda (Twilio SID/Token atau Meta Cloud API access token)
- Aktifkan mode RAG jika akan upload SOP/manual PDF
- Konfigurasi payment gateway (Midtrans / Xendit / Stripe)
