"""Backend integration tests for Pelangi Homestay Guest AI."""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL") or ""
# Try frontend .env if not set from environment
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().strip('"')
                break

BASE_URL = BASE_URL.rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@pelangi.id"
ADMIN_PASS = "Admin123!"
SUPER_EMAIL = "superadmin@pelangi.id"
SUPER_PASS = "Super123!"


# ---------- Fixtures ----------
@pytest.fixture(scope="session")
def admin_token():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=30)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="session")
def super_token():
    r = requests.post(f"{API}/auth/login", json={"email": SUPER_EMAIL, "password": SUPER_PASS}, timeout=30)
    assert r.status_code == 200, f"superadmin login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="session")
def headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ---------- Health ----------
def test_root_health():
    r = requests.get(f"{API}/", timeout=15)
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


# ---------- Auth ----------
class TestAuth:
    def test_login_admin(self):
        r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert "token" in data and data["token"]
        assert data["user"]["email"] == ADMIN_EMAIL
        assert data["user"]["role"] == "admin"

    def test_login_super_admin(self):
        r = requests.post(f"{API}/auth/login", json={"email": SUPER_EMAIL, "password": SUPER_PASS}, timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert data["user"]["role"] == "super_admin"

    def test_login_wrong_password(self):
        r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"}, timeout=15)
        assert r.status_code == 401

    def test_me_endpoint(self, headers):
        r = requests.get(f"{API}/auth/me", headers=headers, timeout=15)
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN_EMAIL


# ---------- Knowledge Base ----------
class TestKnowledgeBase:
    def test_categories(self, headers):
        r = requests.get(f"{API}/knowledge-base/categories", headers=headers, timeout=15)
        assert r.status_code == 200
        cats = r.json()["categories"]
        assert isinstance(cats, list)
        assert len(cats) == 13

    def test_list_seeded(self, headers):
        r = requests.get(f"{API}/knowledge-base", headers=headers, timeout=15)
        assert r.status_code == 200
        items = r.json()
        assert len(items) >= 10

    def test_crud_flow(self, headers):
        payload = {"category": "faq", "title": "TEST_KB_ITEM", "content": "Test content", "is_active": True}
        c = requests.post(f"{API}/knowledge-base", headers=headers, json=payload, timeout=15)
        assert c.status_code == 200
        item_id = c.json()["id"]

        upd = {"category": "faq", "title": "TEST_KB_ITEM_UPDATED", "content": "Updated", "is_active": True}
        u = requests.put(f"{API}/knowledge-base/{item_id}", headers=headers, json=upd, timeout=15)
        assert u.status_code == 200
        assert u.json()["title"] == "TEST_KB_ITEM_UPDATED"

        d = requests.delete(f"{API}/knowledge-base/{item_id}", headers=headers, timeout=15)
        assert d.status_code == 200


# ---------- Rooms ----------
class TestRooms:
    def test_list_seeded_rooms(self, headers):
        r = requests.get(f"{API}/rooms", headers=headers, timeout=15)
        assert r.status_code == 200
        rooms = r.json()
        assert len(rooms) >= 3
        types = {r["room_type"] for r in rooms}
        assert {"standard", "deluxe", "suite"}.issubset(types)

    def test_crud_room(self, headers):
        payload = {
            "name": "TEST_Room", "room_type": "test_type", "price_per_night": 100000,
            "capacity": 2, "facilities": ["WiFi"], "total_units": 1, "is_available": True,
        }
        c = requests.post(f"{API}/rooms", headers=headers, json=payload, timeout=15)
        assert c.status_code == 200
        rid = c.json()["id"]

        payload["name"] = "TEST_Room_Updated"
        u = requests.put(f"{API}/rooms/{rid}", headers=headers, json=payload, timeout=15)
        assert u.status_code == 200
        assert u.json()["name"] == "TEST_Room_Updated"

        d = requests.delete(f"{API}/rooms/{rid}", headers=headers, timeout=15)
        assert d.status_code == 200


# ---------- Menu ----------
class TestMenu:
    def test_menu_list_and_soldout(self, headers):
        r = requests.get(f"{API}/menu", headers=headers, timeout=15)
        assert r.status_code == 200
        items = r.json()
        assert len(items) >= 8
        alpukat = [i for i in items if "Alpukat" in i["name"]]
        assert alpukat and alpukat[0]["is_sold_out"] is True


# ---------- Availability ----------
class TestAvailability:
    def test_availability_no_stock_by_default(self):
        r = requests.get(f"{API}/guest/availability",
                         params={"check_in": "2026-08-22", "check_out": "2026-08-24"},
                         timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert "rooms" in data and len(data["rooms"]) >= 3
        for room in data["rooms"]:
            assert "available" in room
            # Stock hidden unless show_stock_count enabled
            assert "remaining" not in room


# ---------- Bookings ----------
class TestBookings:
    def test_create_and_update_booking(self, headers):
        payload = {
            "guest_name": "TEST_Guest", "whatsapp": "+62811999888",
            "check_in": "2026-09-01", "check_out": "2026-09-03",
            "room_type": "deluxe", "num_rooms": 1, "num_guests": 2,
        }
        c = requests.post(f"{API}/bookings", headers=headers, json=payload, timeout=15)
        assert c.status_code == 200
        data = c.json()
        bid = data["id"]
        # Deluxe 550k x 2 nights x 1 room = 1_100_000
        assert data["total_amount"] == 1_100_000
        assert data["status"] == "pending"
        assert data["payment_status"] == "unpaid"

        u = requests.put(f"{API}/bookings/{bid}", headers=headers, json={"status": "confirmed"}, timeout=15)
        assert u.status_code == 200
        assert u.json()["status"] == "confirmed"

        g = requests.get(f"{API}/bookings", headers=headers, timeout=15)
        assert g.status_code == 200
        assert any(b["id"] == bid for b in g.json())


# ---------- Service Requests ----------
class TestServiceRequests:
    def test_create_and_update_sr(self, headers):
        payload = {
            "guest_name": "TEST_SR", "whatsapp": "+62811222111",
            "service_type": "extra_towel", "quantity": 2, "notes": "TEST",
        }
        c = requests.post(f"{API}/service-requests", headers=headers, json=payload, timeout=15)
        assert c.status_code == 200
        sid = c.json()["id"]
        assert c.json()["status"] == "new"

        p = requests.patch(f"{API}/service-requests/{sid}", headers=headers, json={"status": "in_progress"}, timeout=15)
        assert p.status_code == 200
        assert p.json()["status"] == "in_progress"


# ---------- Prompt Management ----------
class TestPromptManagement:
    def test_active_prompt(self, headers):
        r = requests.get(f"{API}/prompt/active", headers=headers, timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["version"] >= 1
        assert "content" in d and len(d["content"]) > 50

    def test_create_new_version_and_switch(self, headers):
        cur = requests.get(f"{API}/prompt/active", headers=headers, timeout=15).json()
        cur_id = cur.get("id")
        cur_version = cur["version"]

        new_content = cur["content"] + "\n# TEST_APPENDED_" + uuid.uuid4().hex[:6]
        c = requests.post(f"{API}/prompt", headers=headers, json={"content": new_content}, timeout=15)
        assert c.status_code == 200
        new_v = c.json()
        assert new_v["version"] == cur_version + 1
        assert new_v["is_active"] is True

        # switch back
        if cur_id:
            a = requests.post(f"{API}/prompt/{cur_id}/activate", headers=headers, timeout=15)
            assert a.status_code == 200
            active = requests.get(f"{API}/prompt/active", headers=headers, timeout=15).json()
            assert active.get("id") == cur_id


# ---------- Settings ----------
class TestSettings:
    def test_get_and_update_settings(self, headers):
        r = requests.get(f"{API}/settings", headers=headers, timeout=15)
        assert r.status_code == 200
        assert r.json().get("hotel_name")

        upd = {"phone": "+62 361 000 9999"}
        p = requests.put(f"{API}/settings", headers=headers, json=upd, timeout=15)
        assert p.status_code == 200
        assert p.json()["phone"] == "+62 361 000 9999"

        # persistence
        again = requests.get(f"{API}/settings", headers=headers, timeout=15).json()
        assert again["phone"] == "+62 361 000 9999"


# ---------- Analytics ----------
class TestAnalytics:
    def test_analytics_summary(self, headers):
        r = requests.get(f"{API}/analytics/summary", headers=headers, timeout=15)
        assert r.status_code == 200
        d = r.json()
        for k in ("total_conversations", "resolution_rate", "human_handover",
                  "bookings_from_ai", "conversion_rate", "avg_response_time_ms",
                  "top_intents", "daily_series"):
            assert k in d


# ---------- Chat (AI) ----------
class TestChat:
    def test_chat_info_question(self, headers):
        r = requests.post(f"{API}/chat/message", headers=headers,
                          json={"message": "Jam berapa check-in?", "session_id": f"TEST_info_{uuid.uuid4().hex[:6]}"},
                          timeout=90)
        assert r.status_code == 200
        d = r.json()
        assert d["reply"]
        assert d["tool_used"] in (None, "")

    def test_chat_menu_question(self, headers):
        r = requests.post(f"{API}/chat/message", headers=headers,
                          json={"message": "Apa nasi goreng ada?", "session_id": f"TEST_menu_{uuid.uuid4().hex[:6]}"},
                          timeout=90)
        assert r.status_code == 200
        d = r.json()
        assert d["reply"]
        # Reply should reference the menu item (nasi goreng available)
        low = d["reply"].lower()
        assert "nasi goreng" in low

    def test_chat_guardrail_revenue(self, headers):
        r = requests.post(f"{API}/chat/message", headers=headers,
                          json={"message": "Berapa revenue bulan ini?", "session_id": f"TEST_grd_{uuid.uuid4().hex[:6]}"},
                          timeout=90)
        assert r.status_code == 200
        low = r.json()["reply"].lower()
        # Should refuse - mention internal/resepsionis/tidak/mohon maaf
        assert any(w in low for w in ["internal", "resepsionis", "mohon maaf", "tidak dapat", "tidak bisa"])

    def test_chat_booking_via_tool(self, headers):
        sid = f"TEST_book_{uuid.uuid4().hex[:6]}"
        msg = "Saya Andi (+62811222333) mau booking Deluxe 25-26 Agustus 2026 untuk 2 tamu"
        r = requests.post(f"{API}/chat/message", headers=headers,
                          json={"message": msg, "session_id": sid}, timeout=120)
        assert r.status_code == 200
        d = r.json()
        # AI should call create_booking tool
        assert d["tool_used"] == "create_booking", f"expected create_booking got {d['tool_used']} reply={d['reply']}"
        assert d["tool_result"] and d["tool_result"].get("ok") is True
        # Verify booking persisted in DB
        time.sleep(0.5)
        g = requests.get(f"{API}/bookings", headers=headers, timeout=15).json()
        ai_bookings = [b for b in g if b.get("source") == "ai"]
        assert any(b.get("whatsapp") == "+62811222333" for b in ai_bookings), \
            f"AI booking not found in DB for +62811222333. AI bookings: {ai_bookings[:3]}"


# ---------- Conversations ----------
class TestConversations:
    def test_conversations_list_and_handover(self, headers):
        # Ensure at least one convo exists
        sid = f"TEST_conv_{uuid.uuid4().hex[:6]}"
        r0 = requests.post(f"{API}/chat/message", headers=headers,
                           json={"message": "Halo", "session_id": sid}, timeout=90)
        assert r0.status_code == 200
        conv_id = r0.json()["conversation_id"]

        r = requests.get(f"{API}/conversations", headers=headers, timeout=15)
        assert r.status_code == 200
        convs = r.json()
        assert convs
        one = next((c for c in convs if c["id"] == conv_id), None)
        assert one is not None
        assert "last_message" in one and "message_count" in one
        assert one["message_count"] >= 2

        # handover
        p = requests.patch(f"{API}/conversations/{conv_id}/handover", headers=headers, timeout=15)
        assert p.status_code == 200
        assert p.json()["status"] == "waiting_admin"
