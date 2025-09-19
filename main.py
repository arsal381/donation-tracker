from fastapi import FastAPI, Request, Form, File, UploadFile, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timedelta
import hashlib
import secrets
import os
import shutil
import json
from typing import Optional, List, Dict, Any

import firebase_admin
from firebase_admin import credentials, firestore


# Initialize Firebase
def initialize_firebase():
    try:
        # Try to get credentials from environment variable (for Render deployment)
        firebase_creds = os.environ.get('FIREBASE_CREDENTIALS')
        if firebase_creds:
            # Parse the JSON credentials from environment variable
            cred_dict = json.loads(firebase_creds)
            cred = credentials.Certificate(cred_dict)
        else:
            # For local development, use service account file
            cred = credentials.Certificate("firebase-service-account.json")

        firebase_admin.initialize_app(cred)
        print("✓ Firebase initialized successfully")
    except Exception as e:
        print(f"Error initializing Firebase: {e}")
        # For development without Firebase setup
        return None

    return firestore.client()


# Create directories first
os.makedirs("templates", exist_ok=True)
os.makedirs("static", exist_ok=True)
os.makedirs("uploads", exist_ok=True)

# Initialize Firebase
db = initialize_firebase()

# FastAPI app
app = FastAPI(title="Donation Management System")

# Templates and static files
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


# Helper functions
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def create_session_token() -> str:
    return secrets.token_urlsafe(32)


# Database helper functions
def get_user_by_email(email: str) -> Optional[Dict]:
    if not db:
        return None
    try:
        users_ref = db.collection('users')
        query = users_ref.where('email', '==', email).limit(1)
        docs = query.stream()
        for doc in docs:
            user_data = doc.to_dict()
            user_data['id'] = doc.id
            return user_data
        return None
    except Exception as e:
        print(f"Error getting user: {e}")
        return None


def get_user_by_id(user_id: str) -> Optional[Dict]:
    if not db:
        return None
    try:
        user_ref = db.collection('users').document(user_id)
        doc = user_ref.get()
        if doc.exists:
            user_data = doc.to_dict()
            user_data['id'] = doc.id
            return user_data
        return None
    except Exception as e:
        print(f"Error getting user by ID: {e}")
        return None


def create_user(email: str, password: str, full_name: str, is_admin: bool = False) -> Optional[str]:
    if not db:
        return None
    try:
        user_data = {
            'email': email,
            'password_hash': hash_password(password),
            'full_name': full_name,
            'is_admin': is_admin,
            'created_at': datetime.utcnow()
        }
        doc_ref = db.collection('users').add(user_data)
        return doc_ref[1].id
    except Exception as e:
        print(f"Error creating user: {e}")
        return None


def create_session(user_id: str, token: str) -> bool:
    if not db:
        return False
    try:
        session_data = {
            'user_id': user_id,
            'token': token,
            'expires_at': datetime.utcnow() + timedelta(days=7)
        }
        db.collection('sessions').add(session_data)
        return True
    except Exception as e:
        print(f"Error creating session: {e}")
        return False


def get_user_from_token(token: str) -> Optional[Dict]:
    if not db:
        return None
    try:
        # Get session
        sessions_ref = db.collection('sessions')
        query = sessions_ref.where('token', '==', token).where('expires_at', '>', datetime.utcnow()).limit(1)
        docs = query.stream()

        for doc in docs:
            session_data = doc.to_dict()
            user_id = session_data.get('user_id')
            return get_user_by_id(user_id)
        return None
    except Exception as e:
        print(f"Error getting user from token: {e}")
        return None


def get_user_donations(user_id: str) -> List[Dict]:
    if not db:
        return []
    try:
        donations_ref = db.collection('donations')
        query = donations_ref.where('user_id', '==', user_id).order_by('created_at',
                                                                       direction=firestore.Query.DESCENDING)
        docs = query.stream()

        donations = []
        for doc in docs:
            donation_data = doc.to_dict()
            donation_data['id'] = doc.id
            donations.append(donation_data)
        return donations
    except Exception as e:
        print(f"Error getting user donations: {e}")
        return []


def get_all_donations() -> List[Dict]:
    if not db:
        return []
    try:
        donations_ref = db.collection('donations')
        query = donations_ref.order_by('created_at', direction=firestore.Query.DESCENDING)
        docs = query.stream()

        donations = []
        for doc in docs:
            donation_data = doc.to_dict()
            donation_data['id'] = doc.id
            donations.append(donation_data)
        return donations
    except Exception as e:
        print(f"Error getting all donations: {e}")
        return []


def create_donation(user_id: Optional[str], donor_name: str, email: str, amount: float,
                    purpose: str, status: str = "received", usage_details: str = "",
                    receipt_filename: Optional[str] = None) -> Optional[str]:
    if not db:
        return None
    try:
        donation_data = {
            'user_id': user_id,
            'donor_name': donor_name,
            'email': email,
            'amount': amount,
            'status': status,
            'purpose': purpose,
            'usage_details': usage_details,
            'receipt_filename': receipt_filename,
            'created_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }
        doc_ref = db.collection('donations').add(donation_data)
        return doc_ref[1].id
    except Exception as e:
        print(f"Error creating donation: {e}")
        return None


def update_donation(donation_id: str, status: str, usage_details: str) -> bool:
    if not db:
        return False
    try:
        donation_ref = db.collection('donations').document(donation_id)
        donation_ref.update({
            'status': status,
            'usage_details': usage_details,
            'updated_at': datetime.utcnow()
        })
        return True
    except Exception as e:
        print(f"Error updating donation: {e}")
        return False


def update_user_password(user_id: str, new_password_hash: str) -> bool:
    if not db:
        return False
    try:
        user_ref = db.collection('users').document(user_id)
        user_ref.update({'password_hash': new_password_hash})
        return True
    except Exception as e:
        print(f"Error updating password: {e}")
        return False


def delete_user_sessions(user_id: str) -> bool:
    if not db:
        return False
    try:
        sessions_ref = db.collection('sessions')
        query = sessions_ref.where('user_id', '==', user_id)
        docs = query.stream()

        for doc in docs:
            doc.reference.delete()
        return True
    except Exception as e:
        print(f"Error deleting sessions: {e}")
        return False


def get_unlinked_donations() -> List[Dict]:
    if not db:
        return []
    try:
        donations_ref = db.collection('donations')
        query = donations_ref.where('user_id', '==', None)
        docs = query.stream()

        donations = []
        for doc in docs:
            donation_data = doc.to_dict()
            donation_data['id'] = doc.id
            donations.append(donation_data)
        return donations
    except Exception as e:
        print(f"Error getting unlinked donations: {e}")
        return []


def link_donations_to_user(email: str, user_id: str) -> int:
    if not db:
        return 0
    try:
        donations_ref = db.collection('donations')
        query = donations_ref.where('email', '==', email).where('user_id', '==', None)
        docs = query.stream()

        count = 0
        for doc in docs:
            doc.reference.update({'user_id': user_id})
            count += 1
        return count
    except Exception as e:
        print(f"Error linking donations: {e}")
        return 0


def get_all_users() -> List[Dict]:
    if not db:
        return []
    try:
        users_ref = db.collection('users')
        query = users_ref.where('is_admin', '==', False)
        docs = query.stream()

        users = []
        for doc in docs:
            user_data = doc.to_dict()
            user_data['id'] = doc.id
            users.append(user_data)
        return users
    except Exception as e:
        print(f"Error getting users: {e}")
        return []


# Auth helper
def get_current_user_from_cookie(request: Request) -> Optional[Dict]:
    token = request.cookies.get("token")
    if not token:
        return None
    return get_user_from_token(token)


# Create default admin account
def create_default_admin():
    if not db:
        print("⚠️ Firebase not initialized, skipping admin creation")
        return

    try:
        admin_exists = get_user_by_email("admin@donatetracker.com")
        if not admin_exists:
            admin_id = create_user(
                "admin@donatetracker.com",
                "admin123",
                "System Administrator",
                is_admin=True
            )
            if admin_id:
                print("✓ Default admin account created: admin@donatetracker.com / admin123")
        else:
            print("✓ Admin account already exists")
    except Exception as e:
        print(f"Error creating admin account: {e}")


# Create admin account
create_default_admin()


# Routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
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
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = get_user_by_email(email)

    if not user or user['password_hash'] != hash_password(password):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid credentials"
        })

    token = create_session_token()
    if create_session(user['id'], token):
        response = RedirectResponse("/dashboard", status_code=303)
        response.set_cookie("token", token, httponly=True, max_age=7 * 24 * 3600)
        return response
    else:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Login failed. Please try again."
        })


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/register")
async def register(
        request: Request,
        email: str = Form(...),
        password: str = Form(...),
        full_name: str = Form(...)
):
    try:
        if get_user_by_email(email):
            return templates.TemplateResponse("register.html", {
                "request": request,
                "error": "Email already registered"
            })

        user_id = create_user(email, password, full_name)
        if not user_id:
            return templates.TemplateResponse("register.html", {
                "request": request,
                "error": "Registration failed. Please try again."
            })

        # Auto-link existing donations
        linked_count = link_donations_to_user(email, user_id)
        if linked_count > 0:
            print(f"✓ Linked {linked_count} existing donation(s) to new user: {email}")

        redirect_url = f"/login?registered=true&linked={linked_count}"
        return RedirectResponse(redirect_url, status_code=303)

    except Exception as e:
        print(f"Error during registration: {e}")
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "Registration failed. Please try again."
        })


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_current_user_from_cookie(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    if user.get('is_admin'):
        return RedirectResponse("/admin/dashboard", status_code=303)

    donations = get_user_donations(user['id'])

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "donations": donations
    })


@app.get("/change_password", response_class=HTMLResponse)
async def change_password_page(request: Request):
    user = get_current_user_from_cookie(request)
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
        confirm_password: str = Form(...)
):
    user = get_current_user_from_cookie(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    if user['password_hash'] != hash_password(current_password):
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user,
            "error": "Current password is incorrect"
        })

    if new_password != confirm_password:
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user,
            "error": "New passwords do not match"
        })

    if len(new_password) < 6:
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user,
            "error": "New password must be at least 6 characters long"
        })

    try:
        new_hash = hash_password(new_password)
        if update_user_password(user['id'], new_hash):
            delete_user_sessions(user['id'])
            response = RedirectResponse("/login?password_changed=true", status_code=303)
            response.delete_cookie("token")
            return response
        else:
            raise Exception("Password update failed")
    except Exception as e:
        print(f"Error changing password: {e}")
        return templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user,
            "error": "Password change failed. Please try again."
        })


@app.get("/public", response_class=HTMLResponse)
async def public_donations(request: Request):
    donations = get_all_donations()

    # Anonymize data for public view
    anonymous_donations = []
    for donation in donations:
        anonymous_donations.append({
            "donor_name": "Anonymous Donor",
            "amount": donation.get('amount', 0),
            "purpose": donation.get('purpose', ''),
            "status": donation.get('status', 'received'),
            "usage_details": donation.get('usage_details', ''),
            "created_at": donation.get('created_at')
        })

    return templates.TemplateResponse("public.html", {
        "request": request,
        "donations": anonymous_donations
    })


# Admin Routes
@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    user = get_current_user_from_cookie(request)
    if not user or not user.get('is_admin'):
        return RedirectResponse("/login", status_code=303)

    donations = get_all_donations()
    users = get_all_users()

    # Calculate statistics
    total_donations = len(donations)
    total_amount = sum(d.get('amount', 0) for d in donations)
    used_donations = len([d for d in donations if d.get('status') == "used"])
    unlinked_donations = len([d for d in donations if not d.get('user_id')])

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
async def admin_add_donation_page(request: Request):
    user = get_current_user_from_cookie(request)
    if not user or not user.get('is_admin'):
        return RedirectResponse("/login", status_code=303)

    users = get_all_users()
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
        receipt: UploadFile = File(None)
):
    user = get_current_user_from_cookie(request)
    if not user or not user.get('is_admin'):
        return RedirectResponse("/login", status_code=303)

    # Handle file upload
    receipt_filename = None
    if receipt and receipt.filename:
        receipt_filename = f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{receipt.filename}"
        with open(f"uploads/{receipt_filename}", "wb") as buffer:
            shutil.copyfileobj(receipt.file, buffer)

    # Check if user exists and link donation
    donor_user = get_user_by_email(email)
    user_id = donor_user['id'] if donor_user else None

    # Create donation
    donation_id = create_donation(
        user_id, donor_name, email, amount, purpose, status, usage_details, receipt_filename
    )

    return RedirectResponse("/admin/dashboard", status_code=303)


@app.post("/admin/update_donation/{donation_id}")
async def update_donation_status(
        donation_id: str,
        request: Request,
        status: str = Form(...),
        usage_details: str = Form(...)
):
    user = get_current_user_from_cookie(request)
    if not user or not user.get('is_admin'):
        return RedirectResponse("/login", status_code=303)

    update_donation(donation_id, status, usage_details)
    return RedirectResponse("/admin/dashboard", status_code=303)


@app.post("/admin/link_donations")
async def admin_link_donations(request: Request):
    user = get_current_user_from_cookie(request)
    if not user or not user.get('is_admin'):
        return RedirectResponse("/login", status_code=303)

    # Get all unlinked donations and try to link them
    unlinked_donations = get_unlinked_donations()
    linked_count = 0

    for donation in unlinked_donations:
        matching_user = get_user_by_email(donation.get('email', ''))
        if matching_user:
            if update_donation(donation['id'], donation.get('status', 'received'), donation.get('usage_details', '')):
                # Update user_id
                try:
                    db.collection('donations').document(donation['id']).update({'user_id': matching_user['id']})
                    linked_count += 1
                except Exception as e:
                    print(f"Error linking donation: {e}")

    if linked_count > 0:
        print(f"✓ Admin linked {linked_count} donation(s) to existing users")

    return RedirectResponse("/admin/dashboard", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("token")
    return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)