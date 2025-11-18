import os
import secrets
from datetime import datetime, timedelta, date
from typing import Optional, Literal

from fastapi import FastAPI, HTTPException, Depends, Cookie, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

from database import db, create_document, get_documents
from schemas import User, Subscription, Client, Invoice

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Plan definitions ----------
class PlanLimits(BaseModel):
    name: Literal["basic", "professional", "enterprise"]
    client_limit: Optional[int]
    invoice_monthly_limit: Optional[int]

PLANS = {
    "basic": PlanLimits(name="basic", client_limit=15, invoice_monthly_limit=20),
    "professional": PlanLimits(name="professional", client_limit=50, invoice_monthly_limit=None),
    "enterprise": PlanLimits(name="enterprise", client_limit=None, invoice_monthly_limit=None),
}

# ---------- Auth helpers ----------
SESSION_COOKIE = "session_token"

class AuthRequest(BaseModel):
    name: Optional[str] = None
    email: EmailStr
    password: str
    plan: Literal["basic", "professional", "enterprise"] = "basic"
    company_name: Optional[str] = None
    company_tin: Optional[str] = None
    phone: Optional[str] = None

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class MeResponse(BaseModel):
    name: str
    email: EmailStr
    plan: str
    role: str

# naive password hash placeholder (do NOT use in production)
def hash_password(p: str) -> str:
    return secrets.token_hex(8) + str(abs(hash(p)))

# Obtain user by session
async def get_current_user(request: Request) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    sess = db["session"].find_one({"token": token})
    if not sess:
        raise HTTPException(status_code=401, detail="Invalid session")
    user = db["user"].find_one({"_id": sess["user_id"]})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

@app.get("/")
def read_root():
    return {"message": "Subscription Backend Running"}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set",
        "database_name": "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["connection_status"] = "Connected"
            response["collections"] = db.list_collection_names()
            response["database"] = "✅ Connected & Working"
    except Exception as e:
        response["database"] = f"⚠️ Error: {str(e)[:80]}"
    return response

# ---------- Auth endpoints ----------
@app.post("/auth/register")
def register(payload: AuthRequest):
    # ensure unique email
    if db["user"].find_one({"email": payload.email}):
        raise HTTPException(400, detail="Email already registered")

    subscription = Subscription(plan=payload.plan)
    user = User(
        name=payload.name or "User",
        email=payload.email,
        password_hash=hash_password(payload.password),
        company_name=payload.company_name,
        company_tin=payload.company_tin,
        phone=payload.phone,
        subscription=subscription,
    )
    user_id = db["user"].insert_one(user.model_dump()).inserted_id

    token = secrets.token_urlsafe(32)
    db["session"].insert_one({
        "user_id": user_id,
        "token": token,
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow() + timedelta(days=30)
    })

    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")
    return resp

@app.post("/auth/login")
def login(payload: LoginRequest):
    user = db["user"].find_one({"email": payload.email})
    if not user:
        raise HTTPException(401, detail="Invalid credentials")
    # naive password check (only for demo)
    if str(abs(hash(payload.password))) not in user.get("password_hash", ""):
        raise HTTPException(401, detail="Invalid credentials")

    token = secrets.token_urlsafe(32)
    db["session"].insert_one({
        "user_id": user["_id"],
        "token": token,
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow() + timedelta(days=30)
    })

    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")
    return resp

@app.post("/auth/logout")
def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db["session"].delete_many({"token": token})
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp

@app.get("/auth/me", response_model=MeResponse)
def me(user = Depends(get_current_user)):
    sub = user.get("subscription", {})
    return {
        "name": user.get("name"),
        "email": user.get("email"),
        "plan": sub.get("plan", "basic"),
        "role": user.get("role", "owner")
    }

# ---------- Plan enforcement helpers ----------

def get_plan_limits(user_doc: dict) -> PlanLimits:
    plan = user_doc.get("subscription", {}).get("plan", "basic")
    return PLANS.get(plan, PLANS["basic"])


# ---------- Clients ----------
class ClientCreate(BaseModel):
    name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    tin: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None

@app.get("/clients")
def list_clients(user = Depends(get_current_user)):
    items = list(db["client"].find({"owner_id": user["_id"]}))
    return [{**{k:v for k,v in i.items() if k != "_id"}, "id": str(i["_id"]) } for i in items]

@app.post("/clients")
def add_client(payload: ClientCreate, user = Depends(get_current_user)):
    limits = get_plan_limits(user)
    current_count = db["client"].count_documents({"owner_id": user["_id"]})
    if limits.client_limit is not None and current_count >= limits.client_limit:
        raise HTTPException(402, detail="Client limit reached for your plan. Please upgrade.")

    data = Client(owner_id=str(user["_id"]), **payload.model_dump())
    new_id = db["client"].insert_one(data.model_dump()).inserted_id
    return {"id": str(new_id)}

# ---------- Invoices ----------
class InvoiceCreate(BaseModel):
    client_id: str
    amount: float
    currency: Literal["AOA", "USD", "EUR"] = "AOA"
    description: Optional[str] = None
    date_issued: date = date.today()
    due_date: Optional[date] = None

@app.get("/invoices/monthly-count")
def invoices_monthly_count(user = Depends(get_current_user)):
    start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    count = db["invoice"].count_documents({"owner_id": user["_id"], "created_at": {"$gte": start}})
    return {"count": count}

@app.post("/invoices")
def create_invoice(payload: InvoiceCreate, user = Depends(get_current_user)):
    limits = get_plan_limits(user)
    if user.get("subscription", {}).get("plan") == "basic" and limits.invoice_monthly_limit is not None:
        start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        count = db["invoice"].count_documents({"owner_id": user["_id"], "created_at": {"$gte": start}})
        if count >= limits.invoice_monthly_limit:
            raise HTTPException(402, detail="Monthly invoice limit reached for BASIC plan.")

    inv = Invoice(owner_id=str(user["_id"]), **payload.model_dump())
    data = inv.model_dump()
    data["created_at"] = datetime.utcnow()
    data["updated_at"] = datetime.utcnow()
    new_id = db["invoice"].insert_one(data).inserted_id
    return {"id": str(new_id)}

# ---------- Dashboard summary ----------
@app.get("/dashboard/summary")
def dashboard_summary(user = Depends(get_current_user)):
    limits = get_plan_limits(user)
    clients_count = db["client"].count_documents({"owner_id": user["_id"]})
    start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    invoices_this_month = db["invoice"].count_documents({"owner_id": user["_id"], "created_at": {"$gte": start}})

    approaching_client_limit = False
    if limits.client_limit is not None and limits.client_limit > 0:
        approaching_client_limit = clients_count >= int(0.8 * limits.client_limit)

    approaching_invoice_limit = False
    if user.get("subscription", {}).get("plan") == "basic" and limits.invoice_monthly_limit is not None:
        approaching_invoice_limit = invoices_this_month >= int(0.8 * limits.invoice_monthly_limit)

    return {
        "plan": user.get("subscription", {}).get("plan"),
        "clients_count": clients_count,
        "invoices_this_month": invoices_this_month,
        "limits": {
            "client_limit": limits.client_limit,
            "invoice_monthly_limit": limits.invoice_monthly_limit
        },
        "warnings": {
            "clients": approaching_client_limit,
            "invoices": approaching_invoice_limit
        }
    }

# ---------- Minimal docs/support content ----------
@app.get("/support/resources")
def support_resources():
    return {
        "videos": [
            {"title": "Onboarding Tutorial", "url": "https://example.com/video-onboarding"}
        ],
        "faq": [
            {"q": "Como funciona o TIN em Angola?", "a": "O TIN possui 9 dígitos. Validação é automática."}
        ],
        "contacts": {
            "basic": ["Email"],
            "professional": ["Email", "Chat"],
            "enterprise": ["Gestor dedicado"]
        }
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
