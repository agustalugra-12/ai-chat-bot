"""MongoDB helpers and Pydantic base document.

Juga satu-satunya tempat koneksi Mongo (`client`/`db`) dibuat - dipindahkan dari
server.py (2026-07-19, bagian Connector Layer PRD v2) supaya modul lain (termasuk
connectors/) bisa import `db` tanpa circular import balik ke server.py.
"""
from typing import Annotated, Any, Optional
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from bson import ObjectId
from datetime import datetime, timezone
import os
import uuid

from dotenv import load_dotenv
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(Path(__file__).parent / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]


def _validate_object_id(v: Any) -> str:
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, str):
        return v
    raise ValueError("Invalid ObjectId")


PyObjectId = Annotated[str, BeforeValidator(_validate_object_id)]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


class BaseDocument(BaseModel):
    """Base for MongoDB-backed models."""
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    id: str = Field(default_factory=new_id, alias="_id")

    def to_mongo(self) -> dict:
        d = self.model_dump(by_alias=True)
        # serialize datetimes to iso
        for k, v in list(d.items()):
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    @classmethod
    def from_mongo(cls, doc: Optional[dict]):
        if not doc:
            return None
        doc = dict(doc)
        if "_id" in doc and not isinstance(doc["_id"], str):
            doc["_id"] = str(doc["_id"])
        return cls(**doc)
