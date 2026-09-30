from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import sqlite3
from datetime import datetime
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
CORS(app)

DATABASE = os.path.join(BASE_DIR, "civicai.db")


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
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


# -----------------------------
# Serve frontend
# -----------------------------
@app.route("/")
def home():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:filename>")
def serve_frontend(filename):
    return send_from_directory(FRONTEND_DIR, filename)


# -----------------------------
# Submit Report
# -----------------------------
@app.route("/api/report", methods=["POST"])
def create_report():

    data = request.get_json()

    category = data.get("category", "Unknown")
    description = data.get("description", "")
    location = data.get("location", "")
    landmark = data.get("landmark", "")
    public_impact = data.get("public_impact", "Medium")

    # Temporary AI logic
    # We will replace this with real AI later.

    description_lower = description.lower()

    if "garbage" in description_lower:
        category = "Garbage / Waste"

    elif "drain" in description_lower:
        category = "Drainage / Waterlogging"

    elif "streetlight" in description_lower:
        category = "Streetlight"

    elif "water" in description_lower:
        category = "Water Leakage"

    elif category == "Let AI detect":
        category = "Pothole / Road Damage"

    # Temporary severity logic
    if public_impact == "High — affecting many people":
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
        "General Civic Department"
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
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        created_at
    ))

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "message": "Report submitted successfully",
        "complaint_id": complaint_id,
        "ai_analysis": {
            "category": category,
            "severity": severity,
            "priority": priority,
            "department": department
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

    return jsonify([
        dict(report)
        for report in reports
    ])


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

    return jsonify({
        "success": True,
        "report": dict(report)
    })


# -----------------------------
# Run server
# -----------------------------
if __name__ == "__main__":
    init_db()

    print("================================")
    print(" CivicAI Backend")
    print(" Server: http://127.0.0.1:5000")
    print("================================")

    app.run(
        debug=False,
        port=5000
    )