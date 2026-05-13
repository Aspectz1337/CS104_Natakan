"""SmartBuild Maintenance · v2 (5 tables)"""
import os
import sqlite3
from functools import wraps
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, g, session
from werkzeug.security import generate_password_hash, check_password_hash

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SMARTBUILD_DB") or os.path.join(APP_DIR, "database.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SMARTBUILD_SECRET", "smart-building-secret-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# ---------- Role / Status mappings ----------
ROLE_LEVELS = {"user": 1, "technician": 2, "manager": 3, "admin": 4}
ROLE_LABELS = {"user": "ผู้ใช้ทั่วไป", "technician": "ช่าง",
               "manager": "หัวหน้าฝ่าย", "admin": "ผู้ดูแลระบบ"}

STATUS_LABELS = {"pending": "รอดำเนินการ", "in_progress": "กำลังซ่อม",
                 "done": "ซ่อมเสร็จ", "cancelled": "ยกเลิก"}
PRIORITY_LABELS = {"low": "ต่ำ", "normal": "ปกติ", "high": "สูง", "urgent": "ด่วนมาก"}
EQUIP_STATUS_LABELS = {"active": "ใช้งานได้", "broken": "ชำรุด",
                       "repairing": "กำลังซ่อม", "retired": "ปลดระวาง"}


# ---------- DB ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS buildings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            address TEXT,
            floors INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            building_id INTEGER NOT NULL,
            room_number TEXT NOT NULL,
            room_type TEXT,
            floor INTEGER DEFAULT 1,
            capacity INTEGER,
            UNIQUE (building_id, room_number),
            FOREIGN KEY (building_id) REFERENCES buildings(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_rooms_building ON rooms(building_id);

        CREATE TABLE IF NOT EXISTS equipment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            category TEXT,
            serial_no TEXT UNIQUE,
            status TEXT DEFAULT 'active',
            installed_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_equip_room ON equipment(room_id);
        CREATE INDEX IF NOT EXISTS idx_equip_status ON equipment(status);

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            role TEXT NOT NULL DEFAULT 'user',
            specialty TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_login TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
        CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active);

        CREATE TABLE IF NOT EXISTS repairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equipment_id INTEGER NOT NULL,
            reporter_id INTEGER NOT NULL,
            assignee_id INTEGER,
            description TEXT NOT NULL,
            resolution_note TEXT,
            priority TEXT NOT NULL DEFAULT 'normal',
            status TEXT NOT NULL DEFAULT 'pending',
            reported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT,
            completed_at TEXT,
            FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
            FOREIGN KEY (reporter_id) REFERENCES users(id) ON DELETE RESTRICT,
            FOREIGN KEY (assignee_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_repairs_equip ON repairs(equipment_id);
        CREATE INDEX IF NOT EXISTS idx_repairs_assignee ON repairs(assignee_id);
        CREATE INDEX IF NOT EXISTS idx_repairs_queue ON repairs(status, priority);
        CREATE INDEX IF NOT EXISTS idx_repairs_reported ON repairs(reported_at);
        """
    )

    # Seed buildings/rooms/equipment
    cur.execute("SELECT COUNT(*) FROM buildings")
    if cur.fetchone()[0] == 0:
        cur.executescript(
            """
            INSERT INTO buildings(code,name,address,floors) VALUES
                ('ENG','อาคารวิศวกรรม','123 ถ.พหลโยธิน',5),
                ('ADM','อาคารบริหาร','456 ถ.วิภาวดี',8);
            INSERT INTO rooms(building_id,room_number,room_type,floor,capacity) VALUES
                (1,'101','ห้องเรียน',1,40),
                (1,'205','ห้องแลปคอมพิวเตอร์',2,30),
                (2,'301','ห้องประชุม',3,20);
            INSERT INTO equipment(room_id,name,category,serial_no,status,installed_at) VALUES
                (1,'แอร์ Daikin 24000 BTU','เครื่องปรับอากาศ','AC-001','active','2023-06-15'),
                (1,'โปรเจคเตอร์ Epson EB-X51','โสตทัศนูปกรณ์','PJ-001','active','2023-08-20'),
                (2,'คอมพิวเตอร์ Dell OptiPlex','คอมพิวเตอร์','PC-101','broken','2022-01-10'),
                (3,'ทีวี Samsung 65"','โสตทัศนูปกรณ์','TV-001','active','2024-02-05');
            """
        )

    # Seed users
    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        users_seed = [
            ("admin", "admin123", "ผู้ดูแลระบบ", "admin@smartbuild.local", "02-111-0000", "admin", None),
            ("manager", "manager123", "หัวหน้าฝ่ายอาคาร", "manager@smartbuild.local", "02-111-0001", "manager", None),
            ("tech1", "tech123", "สมชาย ใจดี", "somchai@smartbuild.local", "081-111-1111", "technician", "ไฟฟ้า"),
            ("tech2", "tech123", "สมหญิง รักงาน", "somying@smartbuild.local", "082-222-2222", "technician", "เครื่องปรับอากาศ"),
            ("tech3", "tech123", "มานะ พากเพียร", "mana@smartbuild.local", "083-333-3333", "technician", "คอมพิวเตอร์"),
            ("user1", "user123", "อาจารย์สมศักดิ์", "somsak@smartbuild.local", "089-999-9999", "user", None),
        ]
        for u in users_seed:
            cur.execute(
                "INSERT INTO users(username,password_hash,full_name,email,phone,role,specialty) VALUES(?,?,?,?,?,?,?)",
                (u[0], generate_password_hash(u[1]), u[2], u[3], u[4], u[5], u[6]),
            )

    # Seed repairs
    cur.execute("SELECT COUNT(*) FROM repairs")
    if cur.fetchone()[0] == 0:
        cur.executescript(
            """
            INSERT INTO repairs(equipment_id,reporter_id,assignee_id,description,priority,status,reported_at,started_at) VALUES
                (3,6,5,'คอมพิวเตอร์เปิดไม่ติด หน้าจอไม่แสดงผล','high','in_progress','2026-05-12 09:30:00','2026-05-12 13:00:00'),
                (1,6,NULL,'แอร์ไม่เย็น มีเสียงดังผิดปกติ','normal','pending','2026-05-13 06:10:00',NULL);
            """
        )

    conn.commit()
    conn.close()


# ---------- Auth ----------
def current_user():
    if "user_id" not in session:
        return None
    if "_user" not in g:
        g._user = get_db().execute(
            "SELECT * FROM users WHERE id=? AND is_active=1", (session["user_id"],)
        ).fetchone()
    return g._user


@app.context_processor
def inject_globals():
    return {
        "current_user": current_user(),
        "ROLE_LABELS": ROLE_LABELS,
        "STATUS_LABELS": STATUS_LABELS,
        "PRIORITY_LABELS": PRIORITY_LABELS,
        "EQUIP_STATUS_LABELS": EQUIP_STATUS_LABELS,
    }


def role_required(min_role):
    def deco(view):
        @wraps(view)
        def wrapped(*a, **kw):
            u = current_user()
            if not u:
                return redirect(url_for("login", next=request.path))
            if ROLE_LEVELS.get(u["role"], 0) < ROLE_LEVELS[min_role]:
                flash("คุณไม่มีสิทธิ์เข้าถึงส่วนนี้", "error")
                return redirect(url_for("index"))
            return view(*a, **kw)
        return wrapped
    return deco


@app.before_request
def require_login():
    if request.endpoint in (None, "login", "static"):
        return
    if not current_user():
        return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        db = get_db()
        u = db.execute("SELECT * FROM users WHERE username=? AND is_active=1",
                       (request.form["username"],)).fetchone()
        if u and check_password_hash(u["password_hash"], request.form["password"]):
            session.clear()
            session["user_id"] = u["id"]
            db.execute("UPDATE users SET last_login=? WHERE id=?", (now_str(), u["id"]))
            db.commit()
            flash(f"ยินดีต้อนรับ {u['full_name']}", "success")
            return redirect(request.args.get("next") or url_for("index"))
        flash("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("ออกจากระบบเรียบร้อย", "success")
    return redirect(url_for("login"))


# ---------- Dashboard ----------
@app.route("/")
def index():
    db = get_db()
    stats = {
        "buildings": db.execute("SELECT COUNT(*) FROM buildings").fetchone()[0],
        "rooms": db.execute("SELECT COUNT(*) FROM rooms").fetchone()[0],
        "equipment": db.execute("SELECT COUNT(*) FROM equipment").fetchone()[0],
        "technicians": db.execute("SELECT COUNT(*) FROM users WHERE role='technician' AND is_active=1").fetchone()[0],
        "pending": db.execute("SELECT COUNT(*) FROM repairs WHERE status='pending'").fetchone()[0],
        "in_progress": db.execute("SELECT COUNT(*) FROM repairs WHERE status='in_progress'").fetchone()[0],
        "done": db.execute("SELECT COUNT(*) FROM repairs WHERE status='done'").fetchone()[0],
    }
    recent = db.execute(
        """SELECT r.*, e.name AS equip_name,
                  rep.full_name AS reporter_name, asg.full_name AS assignee_name
           FROM repairs r
           JOIN equipment e ON e.id=r.equipment_id
           JOIN users rep ON rep.id=r.reporter_id
           LEFT JOIN users asg ON asg.id=r.assignee_id
           ORDER BY r.id DESC LIMIT 6"""
    ).fetchall()
    tech_load = db.execute(
        """SELECT u.full_name AS name,
                  SUM(CASE WHEN r.status IN ('pending','in_progress') THEN 1 ELSE 0 END) AS cnt
           FROM users u
           LEFT JOIN repairs r ON r.assignee_id=u.id
           WHERE u.role='technician' AND u.is_active=1
           GROUP BY u.id ORDER BY cnt DESC LIMIT 5"""
    ).fetchall()
    max_load = max([t["cnt"] or 0 for t in tech_load], default=0)
    return render_template("index.html", stats=stats, recent=recent,
                           tech_load=tech_load, max_load=max_load)


# ---------- Buildings ----------
@app.route("/buildings")
@role_required("technician")
def buildings():
    rows = get_db().execute("SELECT * FROM buildings ORDER BY id DESC").fetchall()
    return render_template("buildings.html", rows=rows)


@app.route("/buildings/add", methods=["POST"])
@role_required("manager")
def buildings_add():
    db = get_db()
    try:
        db.execute(
            "INSERT INTO buildings(code,name,address,floors) VALUES(?,?,?,?)",
            (request.form["code"].strip().upper(), request.form["name"],
             request.form.get("address", ""), int(request.form.get("floors") or 1)),
        )
        db.commit()
        flash("เพิ่มอาคารเรียบร้อย", "success")
    except sqlite3.IntegrityError:
        flash("รหัสอาคารซ้ำ", "error")
    return redirect(url_for("buildings"))


@app.route("/buildings/edit/<int:bid>", methods=["POST"])
@role_required("manager")
def buildings_edit(bid):
    db = get_db()
    db.execute(
        "UPDATE buildings SET code=?,name=?,address=?,floors=? WHERE id=?",
        (request.form["code"].strip().upper(), request.form["name"],
         request.form.get("address", ""), int(request.form.get("floors") or 1), bid),
    )
    db.commit()
    flash("แก้ไขเรียบร้อย", "success")
    return redirect(url_for("buildings"))


@app.route("/buildings/delete/<int:bid>")
@role_required("admin")
def buildings_delete(bid):
    get_db().execute("DELETE FROM buildings WHERE id=?", (bid,))
    get_db().commit()
    flash("ลบเรียบร้อย", "success")
    return redirect(url_for("buildings"))


# ---------- Rooms ----------
@app.route("/rooms")
@role_required("technician")
def rooms():
    db = get_db()
    rows = db.execute(
        """SELECT r.*, b.name AS building_name, b.code AS building_code
           FROM rooms r JOIN buildings b ON b.id=r.building_id
           ORDER BY b.code, r.room_number"""
    ).fetchall()
    blist = db.execute("SELECT * FROM buildings ORDER BY code").fetchall()
    return render_template("rooms.html", rows=rows, buildings=blist)


@app.route("/rooms/add", methods=["POST"])
@role_required("manager")
def rooms_add():
    db = get_db()
    try:
        db.execute(
            "INSERT INTO rooms(building_id,room_number,room_type,floor,capacity) VALUES(?,?,?,?,?)",
            (int(request.form["building_id"]), request.form["room_number"],
             request.form.get("room_type", ""), int(request.form.get("floor") or 1),
             int(request.form.get("capacity") or 0) or None),
        )
        db.commit()
        flash("เพิ่มห้องเรียบร้อย", "success")
    except sqlite3.IntegrityError:
        flash("เลขห้องซ้ำในอาคารนี้", "error")
    return redirect(url_for("rooms"))


@app.route("/rooms/edit/<int:rid>", methods=["POST"])
@role_required("manager")
def rooms_edit(rid):
    db = get_db()
    db.execute(
        "UPDATE rooms SET building_id=?,room_number=?,room_type=?,floor=?,capacity=? WHERE id=?",
        (int(request.form["building_id"]), request.form["room_number"],
         request.form.get("room_type", ""), int(request.form.get("floor") or 1),
         int(request.form.get("capacity") or 0) or None, rid),
    )
    db.commit()
    flash("แก้ไขเรียบร้อย", "success")
    return redirect(url_for("rooms"))


@app.route("/rooms/delete/<int:rid>")
@role_required("admin")
def rooms_delete(rid):
    get_db().execute("DELETE FROM rooms WHERE id=?", (rid,))
    get_db().commit()
    flash("ลบเรียบร้อย", "success")
    return redirect(url_for("rooms"))


# ---------- Equipment ----------
@app.route("/equipment")
@role_required("technician")
def equipment():
    db = get_db()
    rows = db.execute(
        """SELECT e.*, r.room_number, b.name AS building_name, b.code AS building_code
           FROM equipment e
           JOIN rooms r ON r.id=e.room_id
           JOIN buildings b ON b.id=r.building_id
           ORDER BY e.id DESC"""
    ).fetchall()
    rooms_list = db.execute(
        """SELECT r.id, r.room_number, b.name AS building_name, b.code AS building_code
           FROM rooms r JOIN buildings b ON b.id=r.building_id
           ORDER BY b.code, r.room_number"""
    ).fetchall()
    return render_template("equipment.html", rows=rows, rooms=rooms_list)


@app.route("/equipment/add", methods=["POST"])
@role_required("manager")
def equipment_add():
    db = get_db()
    try:
        db.execute(
            "INSERT INTO equipment(room_id,name,category,serial_no,status,installed_at) VALUES(?,?,?,?,?,?)",
            (int(request.form["room_id"]), request.form["name"],
             request.form.get("category", ""), request.form.get("serial_no") or None,
             request.form.get("status", "active"), request.form.get("installed_at") or None),
        )
        db.commit()
        flash("เพิ่มอุปกรณ์เรียบร้อย", "success")
    except sqlite3.IntegrityError:
        flash("Serial Number ซ้ำ", "error")
    return redirect(url_for("equipment"))


@app.route("/equipment/edit/<int:eid>", methods=["POST"])
@role_required("manager")
def equipment_edit(eid):
    db = get_db()
    db.execute(
        "UPDATE equipment SET room_id=?,name=?,category=?,serial_no=?,status=?,installed_at=? WHERE id=?",
        (int(request.form["room_id"]), request.form["name"],
         request.form.get("category", ""), request.form.get("serial_no") or None,
         request.form.get("status", "active"), request.form.get("installed_at") or None, eid),
    )
    db.commit()
    flash("แก้ไขเรียบร้อย", "success")
    return redirect(url_for("equipment"))


@app.route("/equipment/delete/<int:eid>")
@role_required("admin")
def equipment_delete(eid):
    get_db().execute("DELETE FROM equipment WHERE id=?", (eid,))
    get_db().commit()
    flash("ลบเรียบร้อย", "success")
    return redirect(url_for("equipment"))


# ---------- Users (admin) ----------
@app.route("/users")
@role_required("admin")
def users():
    rows = get_db().execute("SELECT * FROM users ORDER BY role DESC, id").fetchall()
    return render_template("users.html", rows=rows)


@app.route("/users/add", methods=["POST"])
@role_required("admin")
def users_add():
    db = get_db()
    try:
        db.execute(
            """INSERT INTO users(username,password_hash,full_name,email,phone,role,specialty)
               VALUES(?,?,?,?,?,?,?)""",
            (request.form["username"].strip(),
             generate_password_hash(request.form["password"]),
             request.form["full_name"].strip(),
             request.form.get("email") or None,
             request.form.get("phone") or None,
             request.form["role"],
             request.form.get("specialty") or None),
        )
        db.commit()
        flash("เพิ่มผู้ใช้เรียบร้อย", "success")
    except sqlite3.IntegrityError:
        flash("ชื่อผู้ใช้ซ้ำ", "error")
    return redirect(url_for("users"))


@app.route("/users/edit/<int:uid>", methods=["POST"])
@role_required("admin")
def users_edit(uid):
    db = get_db()
    pw = request.form.get("password", "").strip()
    fields = [
        request.form["full_name"], request.form.get("email") or None,
        request.form.get("phone") or None, request.form["role"],
        request.form.get("specialty") or None, 1 if request.form.get("is_active") else 0,
    ]
    if pw:
        db.execute(
            """UPDATE users SET full_name=?,email=?,phone=?,role=?,specialty=?,
               is_active=?,password_hash=? WHERE id=?""",
            fields + [generate_password_hash(pw), uid],
        )
    else:
        db.execute(
            "UPDATE users SET full_name=?,email=?,phone=?,role=?,specialty=?,is_active=? WHERE id=?",
            fields + [uid],
        )
    db.commit()
    flash("แก้ไขผู้ใช้เรียบร้อย", "success")
    return redirect(url_for("users"))


@app.route("/users/delete/<int:uid>")
@role_required("admin")
def users_delete(uid):
    if uid == session.get("user_id"):
        flash("ไม่สามารถลบบัญชีของตัวเองได้", "error")
        return redirect(url_for("users"))
    db = get_db()
    try:
        db.execute("DELETE FROM users WHERE id=?", (uid,))
        db.commit()
        flash("ลบผู้ใช้เรียบร้อย", "success")
    except sqlite3.IntegrityError:
        # Has repairs → soft delete
        db.execute("UPDATE users SET is_active=0 WHERE id=?", (uid,))
        db.commit()
        flash("ผู้ใช้มีประวัติแจ้งซ่อม — ตั้งสถานะเป็นไม่ใช้งานแทน", "success")
    return redirect(url_for("users"))


# ---------- Repairs ----------
@app.route("/repairs")
def repairs():
    db = get_db()
    rows = db.execute(
        """SELECT r.*, e.name AS equip_name, b.code AS building_code,
                  rm.room_number, rep.full_name AS reporter_name,
                  asg.full_name AS assignee_name
           FROM repairs r
           JOIN equipment e ON e.id=r.equipment_id
           JOIN rooms rm ON rm.id=e.room_id
           JOIN buildings b ON b.id=rm.building_id
           JOIN users rep ON rep.id=r.reporter_id
           LEFT JOIN users asg ON asg.id=r.assignee_id
           ORDER BY
             CASE r.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2
                             WHEN 'normal' THEN 3 ELSE 4 END,
             CASE r.status WHEN 'in_progress' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END,
             r.reported_at DESC"""
    ).fetchall()
    equipment_list = db.execute(
        """SELECT e.id, e.name, rm.room_number, b.code AS building_code
           FROM equipment e
           JOIN rooms rm ON rm.id=e.room_id
           JOIN buildings b ON b.id=rm.building_id
           ORDER BY b.code, rm.room_number, e.name"""
    ).fetchall()
    techs = db.execute(
        "SELECT id,full_name,specialty FROM users WHERE role='technician' AND is_active=1 ORDER BY full_name"
    ).fetchall()
    return render_template("repairs.html", rows=rows, equipment=equipment_list, technicians=techs)


@app.route("/repairs/<int:rid>")
def repairs_detail(rid):
    db = get_db()
    r = db.execute(
        """SELECT r.*, e.name AS equip_name, e.category, e.serial_no,
                  rm.room_number, rm.room_type, b.name AS building_name, b.code AS building_code,
                  rep.full_name AS reporter_name, rep.phone AS reporter_phone,
                  asg.full_name AS assignee_name, asg.phone AS assignee_phone,
                  asg.specialty AS assignee_specialty
           FROM repairs r
           JOIN equipment e ON e.id=r.equipment_id
           JOIN rooms rm ON rm.id=e.room_id
           JOIN buildings b ON b.id=rm.building_id
           JOIN users rep ON rep.id=r.reporter_id
           LEFT JOIN users asg ON asg.id=r.assignee_id
           WHERE r.id=?""",
        (rid,),
    ).fetchone()
    if not r:
        flash("ไม่พบใบแจ้งซ่อม", "error")
        return redirect(url_for("repairs"))
    return render_template("repair_detail.html", r=r)


@app.route("/repairs/add", methods=["POST"])
def repairs_add():
    db = get_db()
    u = current_user()
    # Users can only submit as themselves; tech+ can assign
    assignee = request.form.get("assignee_id") or None
    status = request.form.get("status", "pending")
    priority = request.form.get("priority", "normal")
    if u["role"] == "user":
        assignee, status, priority = None, "pending", request.form.get("priority", "normal")
    db.execute(
        """INSERT INTO repairs(equipment_id,reporter_id,assignee_id,description,priority,status,reported_at)
           VALUES(?,?,?,?,?,?,?)""",
        (int(request.form["equipment_id"]), u["id"],
         int(assignee) if assignee else None,
         request.form["description"], priority, status, now_str()),
    )
    db.commit()
    flash("แจ้งซ่อมเรียบร้อย", "success")
    return redirect(url_for("repairs"))


@app.route("/repairs/edit/<int:rid>", methods=["POST"])
@role_required("technician")
def repairs_edit(rid):
    db = get_db()
    assignee = request.form.get("assignee_id") or None
    status = request.form.get("status", "pending")
    priority = request.form.get("priority", "normal")
    resolution = request.form.get("resolution_note", "")

    # Auto-fill timestamps based on status transition
    r = db.execute("SELECT * FROM repairs WHERE id=?", (rid,)).fetchone()
    started = r["started_at"]
    completed = r["completed_at"]
    if status == "in_progress" and not started:
        started = now_str()
    if status == "done":
        completed = completed or now_str()
        if not started:
            started = completed
    if status in ("pending", "cancelled"):
        completed = None

    db.execute(
        """UPDATE repairs SET equipment_id=?,assignee_id=?,description=?,
           resolution_note=?,priority=?,status=?,started_at=?,completed_at=? WHERE id=?""",
        (int(request.form["equipment_id"]), int(assignee) if assignee else None,
         request.form["description"], resolution, priority, status, started, completed, rid),
    )
    db.commit()
    flash("อัพเดทเรียบร้อย", "success")
    return redirect(url_for("repairs"))


@app.route("/repairs/delete/<int:rid>")
@role_required("manager")
def repairs_delete(rid):
    get_db().execute("DELETE FROM repairs WHERE id=?", (rid,))
    get_db().commit()
    flash("ลบเรียบร้อย", "success")
    return redirect(url_for("repairs"))


# Auto-initialize DB on import (idempotent) — required for WSGI hosts like PythonAnywhere
init_db()

if __name__ == "__main__":
    app.run(debug=True)
