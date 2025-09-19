from fastapi import FastAPI, Request, Form, File, UploadFile, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime, timedelta
import hashlib
import secrets
import os
from typing import Optional
import shutil

# Database setup
DATABASE_URL = "sqlite:///./donations.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# Models
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    password_hash = Column(String)
    full_name = Column(String)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Donation(Base):
    __tablename__ = "donations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer)
    donor_name = Column(String)
    email = Column(String)
    amount = Column(Float)
    status = Column(String, default="received")  # received, allocated, used
    purpose = Column(Text)
    usage_details = Column(Text)
    receipt_filename = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer)
    token = Column(String, unique=True)
    expires_at = Column(DateTime)


# Create directories first
os.makedirs("templates", exist_ok=True)
os.makedirs("static", exist_ok=True)
os.makedirs("uploads", exist_ok=True)

# Create tables
Base.metadata.create_all(bind=engine)

# Security
security = HTTPBearer(auto_error=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def create_session_token() -> str:
    return secrets.token_urlsafe(32)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    if not credentials:
        return None

    session = db.query(Session).filter(
        Session.token == credentials.credentials,
        Session.expires_at > datetime.utcnow()
    ).first()

    if not session:
        return None

    user = db.query(User).filter(User.id == session.user_id).first()
    return user


def get_current_user_from_cookie(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("token")
    if not token:
        return None

    session = db.query(Session).filter(
        Session.token == token,
        Session.expires_at > datetime.utcnow()
    ).first()

    if not session:
        return None

    user = db.query(User).filter(User.id == session.user_id).first()
    return user


# Create default admin account
def create_default_admin():
    db = SessionLocal()
    try:
        admin_exists = db.query(User).filter(User.email == "admin@donatetracker.com").first()
        if not admin_exists:
            admin_user = User(
                email="admin@donatetracker.com",
                password_hash=hash_password("admin123"),
                full_name="System Administrator",
                is_admin=True
            )
            db.add(admin_user)
            db.commit()
            print("✓ Default admin account created: admin@donatetracker.com / admin123")
    except Exception as e:
        print(f"Error creating admin account: {e}")
    finally:
        db.close()


# FastAPI app
app = FastAPI(title="Donation Management System")

# Templates and static files
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Create admin account after defining hash_password
create_default_admin()


# Routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # Get query parameters for success messages
    registered = request.query_params.get("registered")
    linked = request.query_params.get("linked")
    password_changed = request.query_params.get("password_changed")

    success_message = None
    if registered:
        if linked and int(linked) > 0:
            success_message = f"Account created successfully! {linked} existing donation(s) have been linked to your account."
        else:
            success_message = "Account created successfully! You can now login."
    elif password_changed:
        success_message = "Password changed successfully! Please login with your new password."

    return templates.TemplateResponse("login.html", {
        "request": request,
        "success": success_message
    })


@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()

    if not user or user.password_hash != hash_password(password):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid credentials"
        })

    # Create session
    token = create_session_token()
    session = Session(
        user_id=user.id,
        token=token,
        expires_at=datetime.utcnow() + timedelta(days=7)
    )
    db.add(session)
    db.commit()

    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie("token", token, httponly=True, max_age=7 * 24 * 3600)
    return response


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/register")
async def register(
        request: Request,
        email: str = Form(...),
        password: str = Form(...),
        full_name: str = Form(...),
        db: Session = Depends(get_db)
):
    try:
        # Check if user exists
        if db.query(User).filter(User.email == email).first():
            return templates.TemplateResponse("register.html", {
                "request": request,
                "error": "Email already registered"
            })

        # Create user
        user = User(
            email=email,
            password_hash=hash_password(password),
            full_name=full_name
        )
        db.add(user)
        db.commit()
        db.refresh(user)  # Refresh to get the ID

        # Auto-fetch existing donations with matching email and link to new user
        existing_donations = db.query(Donation).filter(
            Donation.email == email,
            Donation.user_id.is_(None)  # Only unlinked donations
        ).all()

        linked_count = 0
        for donation in existing_donations:
            donation.user_id = user.id
            linked_count += 1

        if linked_count > 0:
            db.commit()
            print(f"✓ Linked {linked_count} existing donation(s) to new user: {email}")

        # Redirect with success message including linked donations info
        redirect_url = f"/login?registered=true&linked={linked_count}"
        return RedirectResponse(redirect_url, status_code=303)

    except Exception as e:
        print(f"Error during registration: {e}")
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "Registration failed. Please try again."
        })


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    if user.is_admin:
        return RedirectResponse("/admin/dashboard", status_code=303)

    donations = db.query(Donation).filter(Donation.user_id == user.id).all()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "donations": donations
    })


@app.get("/change_password", response_class=HTMLResponse)
async def change_password_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse("change_password.html", {
        "request": request,
        "user": user
    })


@app.post("/change_password")
async def change_password(
        request: Request,
        current_password: str = Form(...),
        new_password: str = Form(...),
        confirm_password: str = Form(...),
        db: Session = Depends(get_db)
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    # Validate current password
    if user.password_hash != hash_password(current_password):
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user,
            "error": "Current password is incorrect"
        })

    # Validate new password confirmation
    if new_password != confirm_password:
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user,
            "error": "New passwords do not match"
        })

    # Validate password strength
    if len(new_password) < 6:
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user,
            "error": "New password must be at least 6 characters long"
        })

    # Update password
    try:
        user.password_hash = hash_password(new_password)
        db.commit()

        # Invalidate all existing sessions for security
        db.query(Session).filter(Session.user_id == user.id).delete()
        db.commit()

        # Redirect to login with success message
        response = RedirectResponse("/login?password_changed=true", status_code=303)
        response.delete_cookie("token")
        return response

    except Exception as e:
        print(f"Error changing password: {e}")
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user,
            "error": "Password change failed. Please try again."
        })


@app.get("/public", response_class=HTMLResponse)
async def public_donations(request: Request, db: Session = Depends(get_db)):
    donations = db.query(Donation).all()

    # Anonymize data for public view
    anonymous_donations = []
    for donation in donations:
        anonymous_donations.append({
            "donor_name": "Anonymous Donor",
            "amount": donation.amount,
            "purpose": donation.purpose,
            "status": donation.status,
            "usage_details": donation.usage_details,
            "created_at": donation.created_at
        })

    return templates.TemplateResponse("public.html", {
        "request": request,
        "donations": anonymous_donations
    })


# Admin Routes
@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_cookie(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/login", status_code=303)

    donations = db.query(Donation).order_by(Donation.created_at.desc()).all()
    users = db.query(User).filter(User.is_admin == False).all()

    # Calculate statistics
    total_donations = len(donations)
    total_amount = sum(d.amount for d in donations) if donations else 0
    used_donations = len([d for d in donations if d.status == "used"])
    unlinked_donations = len([d for d in donations if d.user_id is None])

    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request,
        "user": user,
        "donations": donations,
        "users": users,
        "total_donations": total_donations,
        "total_amount": total_amount,
        "used_donations": used_donations,
        "unlinked_donations": unlinked_donations
    })


@app.get("/admin/add_donation", response_class=HTMLResponse)
async def admin_add_donation_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_cookie(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/login", status_code=303)

    users = db.query(User).filter(User.is_admin == False).all()
    return templates.TemplateResponse("admin_add_donation.html", {
        "request": request,
        "user": user,
        "users": users
    })


@app.post("/admin/add_donation")
async def admin_add_donation(
        request: Request,
        donor_name: str = Form(...),
        email: str = Form(...),
        amount: float = Form(...),
        purpose: str = Form(...),
        status: str = Form("received"),
        usage_details: str = Form(""),
        receipt: UploadFile = File(None),
        db: Session = Depends(get_db)
):
    user = get_current_user_from_cookie(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/login", status_code=303)

    # Handle file upload
    receipt_filename = None
    if receipt and receipt.filename:
        receipt_filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{receipt.filename}"
        with open(f"uploads/{receipt_filename}", "wb") as buffer:
            shutil.copyfileobj(receipt.file, buffer)

    # Check if user exists and link donation
    donor_user = db.query(User).filter(User.email == email).first()
    user_id = donor_user.id if donor_user else None

    # Create donation
    donation = Donation(
        user_id=user_id,
        donor_name=donor_name,
        email=email,
        amount=amount,
        purpose=purpose,
        status=status,
        usage_details=usage_details,
        receipt_filename=receipt_filename
    )
    db.add(donation)
    db.commit()

    return RedirectResponse("/admin/dashboard", status_code=303)


@app.post("/admin/update_donation/{donation_id}")
async def update_donation_status(
        donation_id: int,
        request: Request,
        status: str = Form(...),
        usage_details: str = Form(...),
        db: Session = Depends(get_db)
):
    user = get_current_user_from_cookie(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/login", status_code=303)

    donation = db.query(Donation).filter(Donation.id == donation_id).first()
    if donation:
        donation.status = status
        donation.usage_details = usage_details
        donation.updated_at = datetime.utcnow()
        db.commit()

    return RedirectResponse("/admin/dashboard", status_code=303)


@app.post("/admin/link_donations")
async def admin_link_donations(request: Request, db: Session = Depends(get_db)):
    """Admin function to retroactively link all unlinked donations to existing users"""
    user = get_current_user_from_cookie(request, db)
    if not user or not user.is_admin:
        return RedirectResponse("/login", status_code=303)

    # Get all unlinked donations
    unlinked_donations = db.query(Donation).filter(Donation.user_id.is_(None)).all()
    linked_count = 0

    for donation in unlinked_donations:
        # Find user with matching email
        matching_user = db.query(User).filter(User.email == donation.email).first()
        if matching_user:
            donation.user_id = matching_user.id
            linked_count += 1

    if linked_count > 0:
        db.commit()
        print(f"✓ Admin linked {linked_count} donation(s) to existing users")

    return RedirectResponse("/admin/dashboard", status_code=303)


@app.get("/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("token")
    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)