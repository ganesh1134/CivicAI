from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
import base64
import json
import sqlite3
from datetime import datetime
import os
import uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
CORS(app)

IS_VERCEL = os.environ.get("VERCEL") == "1"
RUNTIME_DIR = os.environ.get("CIVICAI_DATA_DIR", "/tmp" if IS_VERCEL else BASE_DIR)
DATABASE = os.environ.get("DATABASE_PATH", os.path.join(RUNTIME_DIR, "civicai.db"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(RUNTIME_DIR, "uploads"))
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_IMAGE_SIZE + 128 * 1024
ISSUE_CATEGORIES = {
    "Pothole / Road Damage",
    "Garbage / Waste",
    "Drainage / Waterlogging",
    "Streetlight",
    "Water Leakage",
}
NEEDS_REVIEW = "Needs AI review"
NO_ISSUE = "No issue detected"


# -----------------------------
# Database
# -----------------------------
def init_db():
    conn = sqlite3.connect(DATABASE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            complaint_id TEXT UNIQUE,
            category TEXT,
            description TEXT,
            location TEXT,
            landmark TEXT,
            public_impact TEXT,
            severity TEXT,
            priority INTEGER,
            status TEXT,
            department TEXT,
            created_at TEXT,
            photo_filename TEXT
        )
    """)

    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(reports)")
    }
    if "photo_filename" not in columns:
        conn.execute("ALTER TABLE reports ADD COLUMN photo_filename TEXT")

    conn.commit()
    conn.close()


init_db()


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify({"success": False, "message": "Photo must be 10 MB or smaller"}), 413


# -----------------------------
# Serve frontend
# -----------------------------
@app.route("/")
def home():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:filename>")
def serve_frontend(filename):
    return send_from_directory(FRONTEND_DIR, filename)


@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)


def classify_description(description):
    text = description.lower()
    rules = (
        (("garbage", "waste", "trash", "litter"), "Garbage / Waste"),
        (("drain", "waterlogging", "flooding"), "Drainage / Waterlogging"),
        (("streetlight", "street light", "lamp post"), "Streetlight"),
        (("water leak", "leaking pipe", "burst pipe"), "Water Leakage"),
        (("pothole", "road damage", "road crack", "broken road"), "Pothole / Road Damage"),
    )
    for phrases, category in rules:
        if any(phrase in text for phrase in phrases):
            return category
    return None


def analyze_image(image_bytes, mime_type, description):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {
            "status": "not_configured",
            "issue_detected": None,
            "category": None,
            "confidence": 0,
            "summary": "Image analysis needs a server-side GEMINI_API_KEY.",
        }

    prompt = (
        "Inspect this photo for a visible civic infrastructure problem. "
        "Do not assume a problem exists: a clean, intact road must be reported "
        "as no issue. Distinguish shadows, road markings, and normal seams from "
        "damage. Use the description only as context, not proof. Return only JSON "
        "with issue_detected (boolean), category (one of Pothole / Road Damage, "
        "Garbage / Waste, Drainage / Waterlogging, Streetlight, Water Leakage, "
        "No issue detected, or Needs AI review), confidence (number 0 to 1), "
        "and summary (one short factual sentence). Description: "
        f"{description or 'None provided'}"
    )

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        interaction = client.interactions.create(
            model=os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
            input=[
                {"type": "text", "text": prompt},
                {
                    "type": "image",
                    "data": base64.b64encode(image_bytes).decode("ascii"),
                    "mime_type": mime_type,
                },
            ],
        )
        result = json.loads(interaction.output_text)
        raw_issue_detected = result.get("issue_detected")
        issue_detected = raw_issue_detected if isinstance(raw_issue_detected, bool) else None
        category = result.get("category")
        if issue_detected is False:
            category = NO_ISSUE
        elif issue_detected is None or category not in ISSUE_CATEGORIES:
            category = NEEDS_REVIEW

        try:
            confidence = max(0, min(1, float(result.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0

        return {
            "status": "complete",
            "issue_detected": issue_detected,
            "category": category,
            "confidence": confidence,
            "summary": str(result.get("summary", "Image analyzed."))[:400],
        }
    except Exception:
        return {
            "status": "unavailable",
            "issue_detected": None,
            "category": None,
            "confidence": 0,
            "summary": "Image analysis is temporarily unavailable.",
        }


# -----------------------------
# Submit Report
# -----------------------------
@app.route("/api/report", methods=["POST"])
def create_report():

    data = (request.get_json(silent=True) or {}) if request.is_json else request.form
    photo = request.files.get("photo")
    photo_filename = None
    photo_bytes = None
    photo_mime_type = None

    if photo and photo.filename:
        original_filename = secure_filename(photo.filename)
        extension = os.path.splitext(original_filename)[1].lower()
        photo_bytes = photo.read()
        photo_size = len(photo_bytes)
        if photo_size > MAX_IMAGE_SIZE:
            return jsonify({
                "success": False,
                "message": "Photo must be 10 MB or smaller"
            }), 413

        signature = photo_bytes[:8]
        is_png = signature.startswith(b"\x89PNG\r\n\x1a\n")
        is_jpeg = signature.startswith(b"\xff\xd8\xff")
        if extension not in ALLOWED_IMAGE_EXTENSIONS or not (
            (extension == ".png" and is_png)
            or (extension in {".jpg", ".jpeg"} and is_jpeg)
        ):
            return jsonify({
                "success": False,
                "message": "Upload a valid JPG or PNG image"
            }), 400

        photo_filename = f"{uuid.uuid4().hex}{extension}"
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        photo.stream.seek(0)
        photo.save(os.path.join(UPLOAD_DIR, photo_filename))
        photo_mime_type = "image/png" if extension == ".png" else "image/jpeg"

    requested_category = data.get("category", "Let AI detect")
    description = (data.get("description") or "").strip()
    location = data.get("location", "")
    landmark = data.get("landmark", "")
    public_impact = data.get("public_impact", "Medium")
    analysis = analyze_image(photo_bytes, photo_mime_type, description) if photo_bytes else {
        "status": "not_analyzed",
        "issue_detected": None,
        "category": None,
        "confidence": 0,
        "summary": "No photo was provided for visual analysis.",
    }

    if analysis["status"] == "complete":
        category = analysis["category"]
        issue_detected = analysis["issue_detected"]
    elif requested_category in ISSUE_CATEGORIES:
        category = requested_category
        issue_detected = True
        if analysis["status"] == "not_analyzed":
            analysis["status"] = "manual"
            analysis["summary"] = "Using the category selected in the form."
    else:
        category = classify_description(description) or NEEDS_REVIEW
        issue_detected = category != NEEDS_REVIEW
        if not photo_bytes and issue_detected:
            analysis["status"] = "description_only"
            analysis["summary"] = "Category inferred from the description, not the photo."

    if category == NO_ISSUE:
        severity = "None"
        priority = 0
    elif not issue_detected:
        severity = "Needs review"
        priority = 0
    elif public_impact == "High — affecting many people":
        severity = "High"
        priority = 87
    elif public_impact == "Medium":
        severity = "Medium"
        priority = 65
    else:
        severity = "Low"
        priority = 40

    # Department assignment
    departments = {
        "Pothole / Road Damage": "Roads & Infrastructure",
        "Garbage / Waste": "Sanitation Department",
        "Drainage / Waterlogging": "Drainage Department",
        "Streetlight": "Electrical Department",
        "Water Leakage": "Water Supply Department"
    }

    department = departments.get(
        category,
        "No action needed" if category == NO_ISSUE else "General Civic Department"
    )

    # Generate complaint ID
    conn = sqlite3.connect(DATABASE)

    cursor = conn.cursor()

    cursor.execute(
        "SELECT COUNT(*) FROM reports"
    )

    count = cursor.fetchone()[0] + 1

    complaint_id = f"CIV-2026-{1000 + count}"

    created_at = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # Save report
    cursor.execute("""
        INSERT INTO reports (
            complaint_id,
            category,
            description,
            location,
            landmark,
            public_impact,
            severity,
            priority,
            status,
            department,
            created_at,
            photo_filename
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        complaint_id,
        category,
        description,
        location,
        landmark,
        public_impact,
        severity,
        priority,
        "Submitted",
        department,
        created_at,
        photo_filename
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "message": "Report submitted successfully",
        "complaint_id": complaint_id,
        "photo_url": f"/uploads/{photo_filename}" if photo_filename else None,
        "ai_analysis": {
            "category": category,
            "severity": severity,
            "priority": priority,
            "department": department,
            "status": analysis["status"],
            "issue_detected": issue_detected,
            "confidence": analysis["confidence"],
            "summary": analysis["summary"],
        }
    })


# -----------------------------
# Get all reports
# -----------------------------
@app.route("/api/reports", methods=["GET"])
def get_reports():

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row

    reports = conn.execute(
        "SELECT * FROM reports ORDER BY id DESC"
    ).fetchall()

    conn.close()

    result = []
    for report in reports:
        item = dict(report)
        item["photo_url"] = (
            f"/uploads/{item['photo_filename']}"
            if item.get("photo_filename") else None
        )
        result.append(item)
    return jsonify(result)


# -----------------------------
# Track a report
# -----------------------------
@app.route("/api/report/<complaint_id>", methods=["GET"])
def get_report(complaint_id):

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row

    report = conn.execute(
        "SELECT * FROM reports WHERE complaint_id = ?",
        (complaint_id,)
    ).fetchone()

    conn.close()

    if report is None:
        return jsonify({
            "success": False,
            "message": "Complaint not found"
        }), 404

    result = dict(report)
    result["photo_url"] = (
        f"/uploads/{result['photo_filename']}"
        if result.get("photo_filename") else None
    )

    return jsonify({
        "success": True,
        "report": result
    })


# -----------------------------
# Run server
# -----------------------------
if __name__ == "__main__":
    print("================================")
    print(" CivicAI Backend")
    print(" Server: http://127.0.0.1:5000")
    print("================================")

    app.run(
        debug=False,
        port=5000
    )