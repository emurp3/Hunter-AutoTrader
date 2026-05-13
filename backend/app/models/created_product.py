from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from sqlmodel import Field, SQLModel


class CreatedProduct(SQLModel, table=True):
    """Tracks every real product/store/listing Hunter creates or packages."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    source_opportunity: Optional[str] = Field(default=None)  # source opp id/title
    platform: str  # etsy, gumroad, shopify, jetprint, popcustoms, manual
    status: str = Field(default="draft", index=True)  # draft | created | launched | blocked
    url: Optional[str] = Field(default=None)  # live URL when created/launched
    next_action: Optional[str] = Field(default=None)
    price: Optional[float] = Field(default=None)
    estimated_margin: Optional[float] = Field(default=None)  # 0.0-1.0
    product_pack: Optional[str] = Field(default=None)  # full JSON product pack
    design_variant: Optional[str] = Field(default=None)  # e.g. 'lion_insignia'
    manufacturer: Optional[str] = Field(default=None)  # JetPrint, Popcustoms, etc.
    notes: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    launched_at: Optional[datetime] = Field(default=None)
    updated_at: Optional[datetime] = Field(default=None)
