"""
Database Schemas for Subscription App

Each Pydantic model represents a MongoDB collection. The collection name is the
lowercased class name.
"""
from __future__ import annotations
from pydantic import BaseModel, Field, EmailStr, field_validator
from typing import Optional, Literal
from datetime import date, datetime

PlanName = Literal["basic", "professional", "enterprise"]

class Subscription(BaseModel):
    plan: PlanName = Field(..., description="Current subscription plan")
    started_at: datetime = Field(default_factory=datetime.utcnow)
    renews_at: Optional[datetime] = None
    status: Literal["active", "past_due", "canceled"] = "active"

class User(BaseModel):
    name: str = Field(..., description="Full name")
    email: EmailStr = Field(..., description="Email address")
    password_hash: str = Field(..., description="Hashed password")
    company_name: Optional[str] = None
    company_tin: Optional[str] = Field(None, description="Angolan TIN (9 digits)")
    phone: Optional[str] = Field(None, description="+244 followed by 9 digits")
    subscription: Subscription
    role: Literal["owner", "admin", "user"] = "owner"
    is_active: bool = True

    @field_validator("company_tin")
    @classmethod
    def validate_tin(cls, v: Optional[str]):
        if v is None or v == "":
            return v
        if not (len(v) == 9 and v.isdigit()):
            raise ValueError("TIN must be 9 digits (Angola)")
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]):
        if v is None or v == "":
            return v
        if not (v.startswith("+244") and len(v) == 13 and v[1:].isdigit()):
            raise ValueError("Phone must be +244 followed by 9 digits")
        return v

class Session(BaseModel):
    user_id: str
    token: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None

class Client(BaseModel):
    owner_id: str = Field(..., description="User ID who owns this client")
    name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, description="+244 followed by 9 digits")
    tin: Optional[str] = Field(None, description="Angolan TIN (9 digits)")
    address: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("tin")
    @classmethod
    def validate_tin(cls, v: Optional[str]):
        if v is None or v == "":
            return v
        if not (len(v) == 9 and v.isdigit()):
            raise ValueError("TIN must be 9 digits (Angola)")
        return v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]):
        if v is None or v == "":
            return v
        if not (v.startswith("+244") and len(v) == 13 and v[1:].isdigit()):
            raise ValueError("Phone must be +244 followed by 9 digits")
        return v

class Invoice(BaseModel):
    owner_id: str = Field(..., description="User ID who owns this invoice")
    client_id: str = Field(..., description="Client ID")
    amount: float = Field(..., gt=0)
    currency: Literal["AOA", "USD", "EUR"] = "AOA"
    description: Optional[str] = None
    date_issued: date = Field(default_factory=date.today)
    due_date: Optional[date] = None
    status: Literal["draft", "sent", "paid", "overdue", "canceled"] = "draft"

    @field_validator("date_issued", mode="before")
    @classmethod
    def no_future_issue_date(cls, v):
        d = v if isinstance(v, date) else date.fromisoformat(v)
        if d > date.today():
            raise ValueError("Issuance date cannot be in the future")
        return v

# Simple schema exposer for viewer tools
class SchemaInfo(BaseModel):
    name: str
    fields: dict

