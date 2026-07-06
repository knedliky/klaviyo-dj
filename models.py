from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr


class User(BaseModel):
    id: UUID
    name: str
    email: EmailStr


class Preset(BaseModel):
    """A curated mood preset with predefined keywords."""
    id: str
    name: str
    description: str
    keywords: list[str]
    icon: str  # Emoji for UI display


class PlaylistRequest(BaseModel):
    """A request to generate a playlist from keywords."""
    email: EmailStr
    keywords: list[str]
    source: Literal["preset", "custom"]
    preset_id: str | None = None
