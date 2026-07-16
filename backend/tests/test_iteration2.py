"""Iteration 2 backend tests — Cloudinary uploads, RAG documents, KB/Rooms images,
and RAG-augmented / photo-embedding AI chat.
Reuses fixtures via /app/backend/tests/backend_test.py in the same directory (conftest imports).
"""
import io
import os
import time
import uuid
import struct
import zlib
import pytest
import requests

# --- BASE_URL discovery ---
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL") or ""
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


# ---------- Helpers ----------
@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def h(token):
    return {"Authorization": f"Bearer {token}"}


def _make_png(w=8, h=8, color=(200, 80, 60)) -> bytes:
    """Build a minimal valid PNG in-memory (RGB, no compression tricks)."""
    def chunk(tag, data):
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB
    raw = b""
    for _y in range(h):
        raw += b"\x00" + bytes(color) * w  # filter byte + row
    idat = zlib.compress(raw)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# ---------- Cloudinary image upload ----------
class TestUploadsImage:
    def test_upload_png_returns_cloudinary_url(self, h):
        png = _make_png()
        files = {"file": ("test_iter2.png", png, "image/png")}
        r = requests.post(f"{API}/uploads/image?folder=pelangi/kb",
                          headers=h, files=files, timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("url", "public_id", "format", "bytes"):
            assert k in d
        assert "res.cloudinary.com" in d["url"]
        assert d["bytes"] > 0

    def test_upload_rejects_bad_extension(self, h):
        files = {"file": ("virus.exe", b"MZ\x90\x00", "application/octet-stream")}
        r = requests.post(f"{API}/uploads/image", headers=h, files=files, timeout=30)
        assert r.status_code == 400

    def test_upload_requires_auth(self):
        png = _make_png()
        files = {"file": ("noauth.png", png, "image/png")}
        r = requests.post(f"{API}/uploads/image", files=files, timeout=30)
        assert r.status_code in (401, 403)


# ---------- KB with images ----------
class TestKBImages:
    def test_kb_create_with_images(self, h):
        png = _make_png(color=(20, 120, 200))
        files = {"file": ("kb_iter2.png", png, "image/png")}
        up = requests.post(f"{API}/uploads/image?folder=pelangi/kb", headers=h, files=files, timeout=60)
        assert up.status_code == 200
        img = {"url": up.json()["url"], "public_id": up.json()["public_id"]}

        payload = {"category": "facilities", "title": "TEST_KB_WITH_IMG",
                   "content": "Kolam renang buka 06.00-21.00.",
                   "is_active": True, "images": [img]}
        c = requests.post(f"{API}/knowledge-base", headers=h, json=payload, timeout=30)
        assert c.status_code == 200
        item = c.json()
        assert item["images"] and item["images"][0]["url"] == img["url"]

        # Verify persistence via list
        lst = requests.get(f"{API}/knowledge-base", headers=h, timeout=30).json()
        got = next(i for i in lst if i["id"] == item["id"])
        assert got["images"][0]["url"] == img["url"]

        # Update preserves images
        upd = {**payload, "title": "TEST_KB_WITH_IMG_v2"}
        u = requests.put(f"{API}/knowledge-base/{item['id']}", headers=h, json=upd, timeout=30)
        assert u.status_code == 200
        assert len(u.json()["images"]) == 1

        # cleanup
        requests.delete(f"{API}/knowledge-base/{item['id']}", headers=h, timeout=15)


# ---------- Rooms with images (used for AI photo delivery) ----------
class TestRoomsImages:
    def test_attach_image_to_deluxe(self, h):
        # find deluxe
        rooms = requests.get(f"{API}/rooms", headers=h, timeout=15).json()
        dlx = next(r for r in rooms if r["room_type"] == "deluxe")

        png = _make_png(color=(60, 180, 90))
        files = {"file": ("deluxe_iter2.png", png, "image/png")}
        up = requests.post(f"{API}/uploads/image?folder=pelangi/rooms",
                           headers=h, files=files, timeout=60).json()
        img = {"url": up["url"], "public_id": up["public_id"]}

        payload = {
            "name": dlx["name"], "room_type": dlx["room_type"],
            "price_per_night": dlx["price_per_night"], "capacity": dlx["capacity"],
            "photo_url": dlx.get("photo_url"), "images": [img],
            "facilities": dlx.get("facilities", []),
            "total_units": dlx.get("total_units", 1),
            "is_available": dlx.get("is_available", True),
            "description": dlx.get("description"),
        }
        u = requests.put(f"{API}/rooms/{dlx['id']}", headers=h, json=payload, timeout=30)
        assert u.status_code == 200
        assert u.json()["images"][0]["url"] == img["url"]

        # persist check
        rooms2 = requests.get(f"{API}/rooms", headers=h, timeout=15).json()
        dlx2 = next(r for r in rooms2 if r["id"] == dlx["id"])
        assert dlx2["images"][0]["url"] == img["url"]

        # keep the image for AI test; cleanup handled per-test suite


# ---------- RAG documents ----------
class TestRag:
    RAG_TEXT = (
        "SOP KEBIJAKAN HEWAN PELANGI HOMESTAY\n\n"
        "Pelangi Homestay adalah pet-friendly untuk kucing dan anjing kecil "
        "dengan berat maksimal 10 kg. Dikenakan biaya tambahan sebesar "
        "Rp 100000 per malam untuk hewan peliharaan. Pemilik wajib membawa "
        "kandang portabel dan alas makan sendiri. Hewan tidak diperbolehkan di area restoran.\n\n"
        "PROSEDUR CEK IN HEWAN\n"
        "1. Tamu wajib menginformasikan saat booking bahwa membawa hewan.\n"
        "2. Petugas front office memverifikasi jenis dan berat hewan saat check-in.\n"
        "3. Deposit tambahan Rp 200000 dibebankan dan dikembalikan saat check-out jika tidak ada kerusakan.\n"
    )

    @pytest.fixture(scope="class")
    def doc(self, h):
        files = {"file": ("TEST_pet_sop.txt", self.RAG_TEXT.encode("utf-8"), "text/plain")}
        r = requests.post(f"{API}/rag/documents", headers=h, files=files, timeout=60)
        assert r.status_code == 200, r.text
        yield r.json()
        # teardown
        try:
            requests.delete(f"{API}/rag/documents/{r.json()['id']}", headers=h, timeout=30)
        except Exception:
            pass

    def test_upload_creates_chunks(self, doc):
        assert doc["chunk_count"] >= 1
        assert doc["char_count"] >= 100
        # cloudinary raw url is optional but should be present given creds
        assert doc.get("url") is None or "res.cloudinary.com" in doc["url"]

    def test_list_includes_doc(self, h, doc):
        lst = requests.get(f"{API}/rag/documents", headers=h, timeout=30).json()
        assert any(d["id"] == doc["id"] for d in lst)

    def test_search_finds_pet_query(self, h, doc):
        r = requests.get(f"{API}/rag/search",
                         headers=h,
                         params={"q": "kucing pet berat kg biaya"}, timeout=30)
        assert r.status_code == 200
        hits = r.json()["hits"]
        assert hits, "expected at least one BM25 hit for pet query"
        assert hits[0]["score"] > 0
        assert any("10" in h_["text"] or "kucing" in h_["text"].lower() for h_ in hits)

    def test_search_zero_overlap_returns_empty(self, h, doc):
        r = requests.get(f"{API}/rag/search", headers=h,
                         params={"q": "xylophone quantum blockchain"}, timeout=30)
        assert r.status_code == 200
        assert r.json()["hits"] == []

    def test_reject_unsupported_type(self, h):
        files = {"file": ("bad.zip", b"PK\x03\x04", "application/zip")}
        r = requests.post(f"{API}/rag/documents", headers=h, files=files, timeout=30)
        assert r.status_code == 400


# ---------- AI Chat: RAG + photo delivery ----------
class TestChatRagAndPhoto:

    RAG_TEXT = TestRag.RAG_TEXT

    @pytest.fixture(scope="class")
    def rag_doc(self, h):
        files = {"file": ("TEST_pet_sop_chat.txt", self.RAG_TEXT.encode("utf-8"), "text/plain")}
        r = requests.post(f"{API}/rag/documents", headers=h, files=files, timeout=60)
        assert r.status_code == 200, r.text
        yield r.json()
        try:
            requests.delete(f"{API}/rag/documents/{r.json()['id']}", headers=h, timeout=30)
        except Exception:
            pass

    def test_rag_augmented_reply_mentions_figures(self, h, rag_doc):
        sid = f"TEST_pet_{uuid.uuid4().hex[:6]}"
        r = requests.post(f"{API}/chat/message", headers=h,
                          json={"message": "Bolehkah bawa kucing? Ada biaya tambahan?",
                                "session_id": sid,
                                "guest_name": "TEST_Ari", "whatsapp": "+62811777222"},
                          timeout=120)
        assert r.status_code == 200
        reply = r.json()["reply"].lower()
        # Must mention 10 kg (weight limit) and 100.000 / 100000 (fee) — RAG injected
        assert "10" in reply and "kg" in reply, f"missing 10kg in reply: {reply}"
        assert ("100.000" in reply or "100000" in reply or "100 000" in reply
                or "100rb" in reply or "100k" in reply), f"missing fee in reply: {reply}"

    def test_photo_delivery_from_room_images(self, h):
        # Ensure Deluxe has at least one image (from TestRoomsImages OR add now)
        rooms = requests.get(f"{API}/rooms", headers=h, timeout=15).json()
        dlx = next(r for r in rooms if r["room_type"] == "deluxe")
        if not dlx.get("images"):
            png = _make_png(color=(120, 30, 200))
            files = {"file": ("deluxe_chat.png", png, "image/png")}
            up = requests.post(f"{API}/uploads/image?folder=pelangi/rooms",
                               headers=h, files=files, timeout=60).json()
            payload = {
                "name": dlx["name"], "room_type": dlx["room_type"],
                "price_per_night": dlx["price_per_night"], "capacity": dlx["capacity"],
                "photo_url": dlx.get("photo_url"),
                "images": [{"url": up["url"], "public_id": up["public_id"]}],
                "facilities": dlx.get("facilities", []),
                "total_units": dlx.get("total_units", 1),
                "is_available": dlx.get("is_available", True),
                "description": dlx.get("description"),
            }
            requests.put(f"{API}/rooms/{dlx['id']}", headers=h, json=payload, timeout=30)
            rooms = requests.get(f"{API}/rooms", headers=h, timeout=15).json()
            dlx = next(r for r in rooms if r["id"] == dlx["id"])

        expected_url = dlx["images"][0]["url"]

        sid = f"TEST_photo_{uuid.uuid4().hex[:6]}"
        r = requests.post(f"{API}/chat/message", headers=h,
                          json={"message": "Boleh liat foto kamar Deluxe kak?",
                                "session_id": sid,
                                "guest_name": "TEST_Bibi", "whatsapp": "+62811555444"},
                          timeout=120)
        assert r.status_code == 200
        reply = r.json()["reply"]
        # AI should embed the room image URL either as [[IMG: url]] or plain URL
        assert (expected_url in reply) or ("res.cloudinary.com" in reply and "pelangi/rooms" in reply), \
            f"AI reply did not include a room photo URL. Reply: {reply}"
