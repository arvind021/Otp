from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from pymongo import MongoClient
from datetime import datetime
import os
from dotenv import load_dotenv
import secrets

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"))
db = client["numberstore"]

numbers_col = db["numbers"]
orders_col = db["orders"]
payments_col = db["payments"]

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

# ─── Admin Auth ─────────────────────────────────────────────────────────────

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        data = request.json
        if data.get("username") == ADMIN_USERNAME and data.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return jsonify({"success": True})
        return jsonify({"success": False, "msg": "Wrong credentials"})
    return render_template("admin.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin_login"))

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return jsonify({"success": False, "msg": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper

# ─── Admin - Numbers ─────────────────────────────────────────────────────────

@app.route("/admin/numbers", methods=["GET"])
@admin_required
def get_numbers():
    nums = list(numbers_col.find({}, {"_id": 0}))
    return jsonify(nums)

@app.route("/admin/numbers/add", methods=["POST"])
@admin_required
def add_number():
    data = request.json
    number = data.get("number", "").strip()
    price = float(data.get("price", 0))
    country = data.get("country", "").strip()

    if not number or not country or price <= 0:
        return jsonify({"success": False, "msg": "Invalid input"})

    existing = numbers_col.find_one({"number": number})
    if existing:
        return jsonify({"success": False, "msg": "Number already exists"})

    numbers_col.insert_one({
        "number": number,
        "country": country,
        "price": price,
        "otps": [],
        "otp_used": 0,
        "status": "available",  # available / sold
        "added_at": datetime.utcnow().isoformat()
    })
    return jsonify({"success": True, "msg": "Number added!"})

@app.route("/admin/numbers/delete", methods=["POST"])
@admin_required
def delete_number():
    number = request.json.get("number")
    numbers_col.delete_one({"number": number})
    return jsonify({"success": True})

@app.route("/admin/otp/add", methods=["POST"])
@admin_required
def add_otp():
    data = request.json
    number = data.get("number")
    otp = data.get("otp", "").strip()

    if not number or not otp:
        return jsonify({"success": False, "msg": "Invalid input"})

    result = numbers_col.update_one(
        {"number": number},
        {"$push": {"otps": {"code": otp, "used": False, "added_at": datetime.utcnow().isoformat()}}}
    )
    if result.matched_count == 0:
        return jsonify({"success": False, "msg": "Number not found"})
    return jsonify({"success": True, "msg": "OTP added!"})

# ─── Admin - Orders & Payments ────────────────────────────────────────────────

@app.route("/admin/orders", methods=["GET"])
@admin_required
def get_orders():
    orders = list(orders_col.find({}, {"_id": 0}).sort("created_at", -1))
    return jsonify(orders)

@app.route("/admin/payments", methods=["GET"])
@admin_required
def get_payments():
    payments = list(payments_col.find({}, {"_id": 0}).sort("submitted_at", -1))
    return jsonify(payments)

@app.route("/admin/payments/verify", methods=["POST"])
@admin_required
def verify_payment():
    data = request.json
    payment_id = data.get("payment_id")
    action = data.get("action")  # approve / reject

    payment = payments_col.find_one({"payment_id": payment_id})
    if not payment:
        return jsonify({"success": False, "msg": "Payment not found"})

    if action == "approve":
        # Mark payment
        payments_col.update_one({"payment_id": payment_id}, {"$set": {"status": "approved"}})
        # Mark number as sold
        number = payment.get("number")
        numbers_col.update_one({"number": number}, {"$set": {"status": "sold", "buyer_name": payment.get("buyer_name")}})
        # Create order
        orders_col.insert_one({
            "order_id": payment_id,
            "number": number,
            "buyer_name": payment.get("buyer_name"),
            "buyer_contact": payment.get("buyer_contact"),
            "amount": payment.get("amount"),
            "created_at": datetime.utcnow().isoformat()
        })
        return jsonify({"success": True, "msg": "Payment approved, number assigned!"})

    elif action == "reject":
        payments_col.update_one({"payment_id": payment_id}, {"$set": {"status": "rejected"}})
        return jsonify({"success": True, "msg": "Payment rejected"})

    return jsonify({"success": False, "msg": "Invalid action"})

# ─── Admin - Stats ─────────────────────────────────────────────────────────────

@app.route("/admin/stats", methods=["GET"])
@admin_required
def get_stats():
    total = numbers_col.count_documents({})
    available = numbers_col.count_documents({"status": "available"})
    sold = numbers_col.count_documents({"status": "sold"})
    pending_payments = payments_col.count_documents({"status": "pending"})
    total_orders = orders_col.count_documents({})
    return jsonify({
        "total_numbers": total,
        "available": available,
        "sold": sold,
        "pending_payments": pending_payments,
        "total_orders": total_orders
    })

# ─── User Routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/numbers/list", methods=["GET"])
def list_numbers():
    country = request.args.get("country", "")
    query = {"status": "available"}
    if country:
        query["country"] = {"$regex": country, "$options": "i"}
    nums = list(numbers_col.find(query, {"_id": 0, "number": 1, "country": 1, "price": 1}))
    return jsonify(nums)

@app.route("/buy", methods=["POST"])
def buy_number():
    data = request.json
    number = data.get("number")
    buyer_name = data.get("buyer_name", "").strip()
    buyer_contact = data.get("buyer_contact", "").strip()
    utr = data.get("utr", "").strip()

    if not all([number, buyer_name, buyer_contact, utr]):
        return jsonify({"success": False, "msg": "All fields required"})

    num = numbers_col.find_one({"number": number, "status": "available"})
    if not num:
        return jsonify({"success": False, "msg": "Number not available"})

    payment_id = "PAY" + secrets.token_hex(4).upper()
    payments_col.insert_one({
        "payment_id": payment_id,
        "number": number,
        "amount": num["price"],
        "buyer_name": buyer_name,
        "buyer_contact": buyer_contact,
        "utr": utr,
        "status": "pending",
        "submitted_at": datetime.utcnow().isoformat()
    })
    return jsonify({"success": True, "msg": "Payment submitted! Waiting for admin approval.", "payment_id": payment_id})

@app.route("/otp/get", methods=["POST"])
def get_otp():
    data = request.json
    number = data.get("number")
    buyer_contact = data.get("buyer_contact", "").strip()

    # Verify this user bought this number
    order = orders_col.find_one({"number": number, "buyer_contact": buyer_contact})
    if not order:
        return jsonify({"success": False, "msg": "No order found for this number"})

    num = numbers_col.find_one({"number": number})
    if not num:
        return jsonify({"success": False, "msg": "Number not found"})

    if num.get("otp_used", 0) >= 3:
        return jsonify({"success": False, "msg": "OTP limit reached (3/3). Number removed."})

    unused_otps = [o for o in num.get("otps", []) if not o["used"]]
    if not unused_otps:
        return jsonify({"success": False, "msg": "No OTP available yet. Please wait."})

    otp = unused_otps[0]["code"]
    new_used_count = num.get("otp_used", 0) + 1

    # Mark OTP as used
    numbers_col.update_one(
        {"number": number, "otps.code": otp},
        {
            "$set": {"otps.$.used": True, "otp_used": new_used_count}
        }
    )

    # If 3 OTPs used, remove from stock
    if new_used_count >= 3:
        numbers_col.update_one({"number": number}, {"$set": {"status": "expired"}})
        return jsonify({"success": True, "otp": otp, "used": new_used_count, "msg": "OTP limit reached. Number expired."})

    return jsonify({"success": True, "otp": otp, "used": new_used_count, "remaining": 3 - new_used_count})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
