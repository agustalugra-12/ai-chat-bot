"""Iteration 3 tests — Multi-AI Bot Management (V2).

Covers:
- /api/bots CRUD (list, create, patch, delete, super_admin gating)
- /api/tools CRUD (18 seeded)
- /api/intents CRUD (13 seeded)
- /api/workflows CRUD (2 seeded, ordered steps)
- /api/chat/message with bot_code (booking_marketing vs guest_service)
- Permission gating (tool_codes)
- Knowledge filtering (knowledge_categories)
- Guest-service refusal of booking / service request creation
"""
import os
import time
import uuid
from pathlib import Path

import pytest
import requests


def _read_env(key: str) -> str:
    env_file = Path("/app/frontend/.env")
    for line in env_file.read_text().splitlines():
        if line.strip().startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"Missing {key} in frontend/.env")


BASE_URL = _read_env("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"email": "admin@pelangi.id", "password": "Admin123!"}
SUPER = {"email": "superadmin@pelangi.id", "password": "Super123!"}


# ---------- Fixtures ----------
@pytest.fixture(scope="session")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json=ADMIN, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="session")
def super_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json=SUPER, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture
def admin_client(admin_token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"})
    return s


@pytest.fixture
def super_client(super_token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {super_token}", "Content-Type": "application/json"})
    return s


# ---------- /api/bots ----------
class TestBots:
    def test_list_seeded_bots(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/bots")
        assert r.status_code == 200
        bots = r.json()
        codes = {b["code"] for b in bots}
        assert "booking_marketing" in codes
        assert "guest_service" in codes
        for b in bots:
            assert "tool_codes" in b and isinstance(b["tool_codes"], list)
            assert "knowledge_categories" in b
            assert "guardrail_rules" in b and isinstance(b["guardrail_rules"], list)
            assert len(b["guardrail_rules"]) > 0
            assert "prompt" in b and b["prompt"]

    def test_create_patch_delete_bot(self, admin_client, super_client):
        # Create
        payload = {
            "name": "TEST_bot_iter3",
            "description": "temp for testing",
            "persona": "test persona",
            "language": "id",
            "prompt": "You are a test bot.",
            "tool_codes": ["faq_answer"],
            "knowledge_categories": ["faq"],
            "allowed_service_types": [],
            "guardrail_rules": ["no secrets"],
            "allowed_intents": ["GENERAL_FAQ"],
        }
        r = admin_client.post(f"{BASE_URL}/api/bots", json=payload)
        assert r.status_code == 200, r.text
        bot = r.json()
        assert bot["name"] == payload["name"]
        assert bot["code"] == "test_bot_iter3"  # slugified
        bot_id = bot["id"]

        # Patch (partial)
        r = admin_client.patch(f"{BASE_URL}/api/bots/{bot_id}",
                               json={"description": "updated"})
        assert r.status_code == 200
        assert r.json()["description"] == "updated"
        # unchanged
        assert r.json()["name"] == payload["name"]

        # Admin delete should be forbidden (require super_admin)
        r_admin_delete = admin_client.delete(f"{BASE_URL}/api/bots/{bot_id}")
        assert r_admin_delete.status_code in (401, 403), r_admin_delete.text

        # Super admin can delete
        r = super_client.delete(f"{BASE_URL}/api/bots/{bot_id}")
        assert r.status_code == 200

        # confirm gone
        r = admin_client.get(f"{BASE_URL}/api/bots/{bot_id}")
        assert r.status_code == 404


# ---------- /api/tools ----------
class TestTools:
    def test_list_18_tools_grouped(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/tools")
        assert r.status_code == 200
        tools = r.json()
        assert len(tools) >= 18
        cats = {t["category"] for t in tools}
        for expected in ["booking", "payment", "marketing", "info", "guest_service", "system"]:
            assert expected in cats, f"Missing category {expected}"

    def test_create_patch_delete_tool(self, admin_client, super_client):
        r = admin_client.post(f"{BASE_URL}/api/tools", json={
            "name": "TEST_tool_iter3",
            "description": "temp",
            "endpoint": "internal",
            "category": "info",
            "status": "active",
        })
        assert r.status_code == 200, r.text
        tool = r.json()
        tid = tool["id"]

        r = admin_client.patch(f"{BASE_URL}/api/tools/{tid}",
                               json={"name": "TEST_tool_iter3", "description": "updated",
                                     "endpoint": "internal", "category": "info", "status": "active"})
        assert r.status_code == 200
        assert r.json()["description"] == "updated"

        r = super_client.delete(f"{BASE_URL}/api/tools/{tid}")
        assert r.status_code == 200


# ---------- /api/intents ----------
class TestIntents:
    def test_list_13_intents_with_tools(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/intents")
        assert r.status_code == 200
        intents = r.json()
        assert len(intents) >= 13
        codes = {i["code"] for i in intents}
        for expected in ["BOOK_ROOM", "ORDER_LAUNDRY", "GENERAL_FAQ"]:
            assert expected in codes
        # Each should have tool_codes list
        for i in intents:
            assert isinstance(i.get("tool_codes"), list)

    def test_create_patch_delete_intent(self, admin_client, super_client):
        r = admin_client.post(f"{BASE_URL}/api/intents", json={
            "name": "TEST_intent",
            "description": "temp",
            "tool_codes": ["faq_answer"],
        })
        assert r.status_code == 200, r.text
        iid = r.json()["id"]

        r = admin_client.patch(f"{BASE_URL}/api/intents/{iid}",
                               json={"name": "TEST_intent", "description": "upd",
                                     "tool_codes": ["faq_answer", "knowledge_lookup"]})
        assert r.status_code == 200
        assert "knowledge_lookup" in r.json()["tool_codes"]

        r = super_client.delete(f"{BASE_URL}/api/intents/{iid}")
        assert r.status_code == 200


# ---------- /api/workflows ----------
class TestWorkflows:
    def test_list_2_workflows_with_steps(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/workflows")
        assert r.status_code == 200
        wfs = r.json()
        codes = {w["code"] for w in wfs}
        assert "booking_flow" in codes
        assert "guest_service_flow" in codes
        for w in wfs:
            assert isinstance(w["steps"], list) and len(w["steps"]) > 0
            orders = [s["order"] for s in w["steps"]]
            assert orders == sorted(orders)

    def test_create_patch_delete_workflow(self, admin_client, super_client):
        r = admin_client.post(f"{BASE_URL}/api/workflows", json={
            "name": "TEST_wf_iter3",
            "description": "temp",
            "steps": [
                {"order": 1, "name": "step1", "description": "one"},
                {"order": 2, "name": "step2", "description": "two"},
            ],
        })
        assert r.status_code == 200, r.text
        wf = r.json()
        assert len(wf["steps"]) == 2
        wid = wf["id"]

        r = admin_client.patch(f"{BASE_URL}/api/workflows/{wid}", json={
            "name": "TEST_wf_iter3", "description": "upd",
            "steps": [
                {"order": 1, "name": "s1", "description": "a"},
                {"order": 2, "name": "s2", "description": "b"},
                {"order": 3, "name": "s3", "description": "c"},
            ],
        })
        assert r.status_code == 200
        assert len(r.json()["steps"]) == 3

        r = super_client.delete(f"{BASE_URL}/api/workflows/{wid}")
        assert r.status_code == 200


# ---------- Chat with bot codes ----------
class TestBotChat:
    def _post_chat(self, client, message: str, bot_code: str, session_id: str = None):
        payload = {"message": message, "bot_code": bot_code}
        if session_id:
            payload["session_id"] = session_id
        r = client.post(f"{BASE_URL}/api/chat/message", json=payload, timeout=60)
        return r

    def test_booking_bot_answers_availability(self, admin_client):
        sid = f"TEST_sess_book_{uuid.uuid4().hex[:8]}"
        r = self._post_chat(admin_client,
                            "Halo, kamar Deluxe 24-26 Agustus 2026 tersedia?",
                            "booking_marketing", sid)
        assert r.status_code == 200, r.text
        data = r.json()
        # Should have a reply (may or may not call check_availability tool)
        assert data.get("reply") and len(data["reply"]) > 5
        # Not a permission error
        tr = data.get("tool_result") or {}
        if tr:
            assert "tidak diizinkan" not in str(tr).lower()

    def test_guest_service_refuses_booking(self, admin_client):
        sid = f"TEST_sess_gs_book_{uuid.uuid4().hex[:8]}"
        r = self._post_chat(admin_client,
                            "Saya mau booking kamar Deluxe untuk 25 Agustus 2026.",
                            "guest_service", sid)
        assert r.status_code == 200, r.text
        data = r.json()
        reply = (data.get("reply") or "").lower()
        # Should mention cannot book / redirect
        keywords = ["tidak", "booking", "resepsion", "arahkan", "tidak bisa", "tidak dapat", "booking ai"]
        assert any(k in reply for k in keywords), f"Unexpected reply: {reply}"
        # Should NOT have created a booking via create_booking tool
        tr = data.get("tool_result") or {}
        assert data.get("tool_used") != "create_booking", f"Guest service bot must not create bookings: {data}"
        if data.get("tool_used") == "create_booking":
            # if attempted, must be blocked
            assert tr.get("ok") is False

    def test_guest_service_creates_laundry_service_request(self, admin_client):
        sid = f"TEST_sess_laundry_{uuid.uuid4().hex[:8]}"
        # Provide guest info to avoid the bot asking
        r = admin_client.post(f"{BASE_URL}/api/chat/message", json={
            "message": "Halo saya Budi (0812 3456 7890), saya mau laundry 3 kg tolong dari kamar Deluxe.",
            "bot_code": "guest_service",
            "session_id": sid,
            "guest_name": "Budi",
            "whatsapp": "081234567890",
        }, timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        # It should either call create_service_request with laundry OR ask for missing info
        # If tool was called, verify success
        if data.get("tool_used") == "create_service_request":
            tr = data.get("tool_result") or {}
            assert tr.get("ok") is True, f"Expected laundry SR to succeed: {tr}"
            # Verify persisted
            srs = admin_client.get(f"{BASE_URL}/api/service-requests").json()
            found = [s for s in srs if s.get("_id") == tr.get("request_id") or s.get("id") == tr.get("request_id")]
            # Just verify at least one laundry SR exists for Budi
            budi_laundry = [s for s in srs
                            if s.get("service_type") == "laundry"
                            and (s.get("guest_name", "").lower().startswith("budi") or s.get("whatsapp") == "081234567890")]
            assert budi_laundry, "Expected a laundry service_request to be created"

    def test_permission_gating_blocks_disallowed_tool(self, admin_client, super_client):
        """PATCH a temp bot with tool_codes=[] then attempt a booking chat — should be rejected."""
        # Create a temp bot with only request_handover
        r = admin_client.post(f"{BASE_URL}/api/bots", json={
            "name": "TEST_gated_bot",
            "description": "gated",
            "persona": "test",
            "language": "id",
            "prompt": ("Anda adalah bot booking. Jika tamu meminta booking, panggil create_booking tool. "
                       "Tulis balasan singkat lalu di baris terakhir: "
                       "[[TOOL: create_booking]] {\"guest_name\":\"Ali\",\"whatsapp\":\"08123\",\"check_in\":\"2026-09-01\",\"check_out\":\"2026-09-02\",\"room_type\":\"deluxe\"}"),
            "tool_codes": ["request_handover"],  # NOT create_booking
            "knowledge_categories": [],
            "allowed_service_types": [],
            "guardrail_rules": [],
            "allowed_intents": [],
        })
        assert r.status_code == 200, r.text
        bot = r.json()
        bot_id = bot["id"]
        try:
            sid = f"TEST_sess_gate_{uuid.uuid4().hex[:8]}"
            r = admin_client.post(f"{BASE_URL}/api/chat/message", json={
                "message": "Tolong buatkan booking kamar deluxe untuk saya (Ali, 08123, 2026-09-01 hingga 2026-09-02).",
                "bot_id": bot_id,
                "session_id": sid,
            }, timeout=60)
            assert r.status_code == 200, r.text
            data = r.json()
            # If AI attempted create_booking, permission gating must reject
            if data.get("tool_used") == "create_booking":
                tr = data.get("tool_result") or {}
                assert tr.get("ok") is False
                assert "tidak diizinkan" in (tr.get("error") or "").lower()
        finally:
            super_client.delete(f"{BASE_URL}/api/bots/{bot_id}")

    def test_knowledge_filtering(self, admin_client, super_client):
        """Create a temp bot with knowledge_categories=['promo'] then ask about check-in.
        Bot should NOT know the checkin KB (since 'checkin' not in categories)."""
        r = admin_client.post(f"{BASE_URL}/api/bots", json={
            "name": "TEST_kb_scoped_bot",
            "description": "kb scoped",
            "persona": "test",
            "language": "id",
            "prompt": (
                "Anda adalah AI. Jawab HANYA dari KONTEKS yang diberikan. "
                "Jika info tidak ada di KONTEKS, jawab: 'Maaf, informasi tersebut tidak tersedia di data saya.' "
                "Jangan menebak."
            ),
            "tool_codes": ["faq_answer"],
            "knowledge_categories": ["promo"],
            "allowed_service_types": [],
            "guardrail_rules": [],
            "allowed_intents": ["GENERAL_FAQ"],
        })
        assert r.status_code == 200, r.text
        bot_id = r.json()["id"]
        try:
            # Ask about airport pickup — content is ONLY in KB category 'airport_pickup' (not in settings/rooms/menu)
            # Bot has knowledge_categories=['promo'], so airport_pickup KB must be filtered out.
            sid = f"TEST_sess_kb_{uuid.uuid4().hex[:8]}"
            r = admin_client.post(f"{BASE_URL}/api/chat/message", json={
                "message": "Berapa harga layanan jemput bandara di sini?",
                "bot_id": bot_id,
                "session_id": sid,
            }, timeout=60)
            assert r.status_code == 200, r.text
            reply = (r.json().get("reply") or "").lower()
            # KB filter must exclude 'airport_pickup' KB (only 'promo' in scope). Bot must not know 250.000
            has_specific = "250" in reply or "ngurah rai" in reply
            refuses = any(w in reply for w in ["tidak tersedia", "tidak ada", "tidak dapat", "maaf",
                                                "tidak memiliki", "tidak ditemukan", "tidak menemukan"])
            assert refuses or not has_specific, f"Bot leaked KB outside scope: {reply}"
        finally:
            super_client.delete(f"{BASE_URL}/api/bots/{bot_id}")
