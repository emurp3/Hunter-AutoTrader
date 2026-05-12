"""Forge models for Opportunity Forge Engine."""
from __future__ import annotations
import json
from datetime import date, datetime
from typing import Optional
from sqlmodel import Field, SQLModel

class ForgeOpportunity(SQLModel, table=True):
    __tablename__ = "forge_opportunities"
    id: Optional[int] = Field(default=None, primary_key=True)
    trigger_type: str
    trigger_name: str = Field(index=True)
    trigger_date: Optional[date] = None
    window_open: Optional[date] = None
    window_close: Optional[date] = None
    opportunity_type: str = "merchandise"
    title: str
    description: Optional[str] = None
    target_audience: Optional[str] = None
    product_ideas_json: Optional[str] = None
    landing_page_spec_json: Optional[str] = None
    fulfillment_model: str = "print_on_demand"
    vendor_name: Optional[str] = None
    vendor_order_url: Optional[str] = None
    landing_page_url: Optional[str] = None
    price_point: Optional[float] = None
    cogs_estimate: Optional[float] = None
    estimated_margin_pct: Optional[float] = None
    estimated_units: Optional[int] = None
    estimated_revenue: Optional[float] = None
    orders_count: int = 0
    revenue_realized: float = 0.0
    confidence_score: float = 0.0
    effort_level: str = "medium"
    days_to_launch: Optional[int] = None
    days_to_cash: Optional[int] = None
    status: str = "detected"
    approved_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    launched_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def product_ideas(self) -> list:
        try: return json.loads(self.product_ideas_json or "[]")
        except Exception: return []

class ForgeCampaign(SQLModel, table=True):
    __tablename__ = "forge_campaigns"
    id: Optional[int] = Field(default=None, primary_key=True)
    opportunity_id: int = Field(foreign_key="forge_opportunities.id", index=True)
    channel: str
    status: str = "draft"
    url: Optional[str] = None
    impressions: int = 0
    clicks: int = 0
    conversions: int = 0
    revenue: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
