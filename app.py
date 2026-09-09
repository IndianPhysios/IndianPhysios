import os, re, secrets, sqlite3, smtplib, json, urllib.request, urllib.error
from email.message import EmailMessage
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from io import BytesIO

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "indian_physios.db")
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_PRODUCTION = os.environ.get("FLASK_ENV", "").lower() == "production" or bool(os.environ.get("RENDER"))
PORT = int(os.environ.get("PORT", "5050"))
APP_BASE_URL = os.environ.get("APP_BASE_URL", "").rstrip("/")
UPLOAD_DIR = os.path.join(BASE, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

app = Flask(__name__)
secret = os.environ.get("SECRET_KEY")
if IS_PRODUCTION and (not secret or len(secret) < 32):
    raise RuntimeError("SECRET_KEY must be set to a random value of at least 32 characters in production.")
app.secret_key = secret or "local-only-change-me"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
    MAX_CONTENT_LENGTH=3 * 1024 * 1024,
)

# Optional PostgreSQL support for production hosting. Local development remains SQLite.
try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.errors import UniqueViolation, IntegrityError as PGIntegrityError
except ImportError:
    psycopg = None
    dict_row = None
    UniqueViolation = PGIntegrityError = ()

class CursorAdapter:
    def __init__(self, cursor, postgres=False):
        self._cursor = cursor
        self.postgres = postgres
    def fetchone(self): return self._cursor.fetchone()
    def fetchall(self): return self._cursor.fetchall()
    @property
    def lastrowid(self):
        if not self.postgres:
            return self._cursor.lastrowid
        row = self._cursor.connection.execute("SELECT LASTVAL() AS id").fetchone()
        return row["id"] if isinstance(row, dict) else row[0]

class DBAdapter:
    def __init__(self, conn, postgres=False):
        self.conn, self.postgres = conn, postgres
    def _sql(self, sql):
        return sql.replace("?", "%s") if self.postgres else sql
    def execute(self, sql, params=()):
        return CursorAdapter(self.conn.execute(self._sql(sql), params), self.postgres)
    def executescript(self, script):
        if self.postgres:
            for statement in [s.strip() for s in script.split(";") if s.strip()]:
                self.conn.execute(statement)
        else:
            self.conn.executescript(script)
    def commit(self): self.conn.commit()
    def rollback(self): self.conn.rollback()
    def close(self): self.conn.close()


def db():
    if DATABASE_URL:
        if psycopg is None:
            raise RuntimeError("PostgreSQL is configured but psycopg is not installed.")
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        return DBAdapter(psycopg.connect(url, row_factory=dict_row), True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return DBAdapter(c, False)


def init_db():
    c = db()
    if c.postgres:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, phone TEXT, qualification TEXT, specialization TEXT, state TEXT, city TEXT, pincode TEXT, registration_no TEXT, verified_email INTEGER DEFAULT 0, verified_physio INTEGER DEFAULT 0, photo TEXT, photo_data BYTEA, photo_mime TEXT, bio TEXT, clinic TEXT, experience TEXT, education TEXT, website TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS verification_tokens (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, token TEXT UNIQUE NOT NULL, expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS connections (id BIGSERIAL PRIMARY KEY, requester_id BIGINT NOT NULL, receiver_id BIGINT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, UNIQUE(requester_id, receiver_id));
        CREATE TABLE IF NOT EXISTS posts (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, title TEXT NOT NULL, organization TEXT NOT NULL, city TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS home_visits (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, location TEXT NOT NULL, diagnosis TEXT NOT NULL, physio_need TEXT NOT NULL, details TEXT NOT NULL, pincode TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notifications (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, home_visit_id BIGINT, title TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT);
        CREATE TABLE IF NOT EXISTS events (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, title TEXT NOT NULL, location TEXT NOT NULL, event_date TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (id BIGSERIAL PRIMARY KEY, sender_id BIGINT NOT NULL, receiver_id BIGINT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT);
        """)
    else:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, phone TEXT, qualification TEXT, specialization TEXT, state TEXT, city TEXT, pincode TEXT, registration_no TEXT, verified_email INTEGER DEFAULT 0, verified_physio INTEGER DEFAULT 0, photo TEXT, photo_data BLOB, photo_mime TEXT, bio TEXT, clinic TEXT, experience TEXT, education TEXT, website TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS verification_tokens (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, token TEXT UNIQUE NOT NULL, expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS connections (id INTEGER PRIMARY KEY AUTOINCREMENT, requester_id INTEGER NOT NULL, receiver_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, UNIQUE(requester_id, receiver_id));
        CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT NOT NULL, organization TEXT NOT NULL, city TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS home_visits (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, location TEXT NOT NULL, diagnosis TEXT NOT NULL, physio_need TEXT NOT NULL, details TEXT NOT NULL, pincode TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, home_visit_id INTEGER, title TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT NOT NULL, location TEXT NOT NULL, event_date TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, sender_id INTEGER NOT NULL, receiver_id INTEGER NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT);
        """)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        for col, definition in [("bio","TEXT"),("clinic","TEXT"),("experience","TEXT"),("education","TEXT"),("website","TEXT"),("photo","TEXT"),("photo_data","BLOB"),("photo_mime","TEXT"),("pincode","TEXT")]:
            if col not in cols: c.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
        hv_cols = {r["name"] for r in c.execute("PRAGMA table_info(home_visits)").fetchall()}
        if "pincode" not in hv_cols: c.execute("ALTER TABLE home_visits ADD COLUMN pincode TEXT NOT NULL DEFAULT ''")
    c.commit(); c.close()
    sync_existing_photos()


def sync_existing_photos():
    # Import existing local profile images into the database so production restarts do not depend on local disk.
    c = db()
    try:
        rows = c.execute("SELECT id, photo, photo_data FROM users WHERE photo IS NOT NULL AND photo <> ''").fetchall()
        for u in rows:
            if u["photo_data"]: continue
            path = os.path.join(UPLOAD_DIR, secure_filename(u["photo"]))
            if os.path.isfile(path) and os.path.getsize(path) <= MAX_UPLOAD_BYTES:
                with open(path, "rb") as f: data = f.read()
                ext = u["photo"].rsplit(".",1)[-1].lower()
                mime = {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","webp":"image/webp"}.get(ext,"application/octet-stream")
                c.execute("UPDATE users SET photo_data=?, photo_mime=? WHERE id=?", (data,mime,u["id"]))
        c.commit()
    finally: c.close()


def now(): return datetime.utcnow().isoformat(timespec="seconds")
def make_token(): return secrets.token_urlsafe(32)
def valid_pincode(v): return bool(re.fullmatch(r"[0-9]{6}", v or ""))
def allowed_file(filename): return "." in filename and filename.rsplit(".",1)[1].lower() in ALLOWED_EXTENSIONS

# Lightweight rate limiting suitable for the free single-instance launch.
_rate = {}
def rate_limit(key, limit, window):
    current = datetime.utcnow().timestamp(); bucket = _rate.get(key, [])
    bucket = [t for t in bucket if current - t < window]
    if len(bucket) >= limit: return False
    bucket.append(current); _rate[key] = bucket
    return True


def csrf_value():
    if "csrf" not in session: session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]

@app.before_request
def security_checks():
    if request.method == "POST":
        token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not token or not secrets.compare_digest(token, session.get("csrf", "")):
            abort(400, "Invalid security token. Please refresh the page and try again.")
        if request.content_length and request.content_length > 3 * 1024 * 1024:
            abort(413)

@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

@app.context_processor
def inject():
    u=current_user(); unread=unread_notifications=0
    if u:
        c=db()
        unread=c.execute("SELECT COUNT(*) AS n FROM messages WHERE receiver_id=? AND read_at IS NULL",(u["id"],)).fetchone()["n"]
        unread_notifications=c.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND read_at IS NULL",(u["id"],)).fetchone()["n"]
        c.close()
    return {"current_user":u,"unread_messages":unread,"unread_notifications":unread_notifications,"csrf_token":csrf_value()}

def current_user():
    uid=session.get("user_id")
    if not uid: return None
    c=db(); u=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone(); c.close(); return u

def send_email(to, subject, body):
    # Prefer Brevo's HTTPS API in production. This works on Render Free,
    # where outbound SMTP ports are restricted.
    brevo_key = os.environ.get("BREVO_API_KEY", "").strip()
    brevo_from = os.environ.get("BREVO_FROM_EMAIL", "").strip()
    brevo_name = os.environ.get("BREVO_FROM_NAME", "Indian Physios").strip() or "Indian Physios"

    if brevo_key and brevo_from:
        payload = json.dumps({
            "sender": {"name": brevo_name, "email": brevo_from},
            "to": [{"email": to}],
            "subject": subject,
            "textContent": body,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=payload,
            headers={
                "accept": "application/json",
                "api-key": brevo_key,
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                if response.status >= 300:
                    raise RuntimeError(f"Brevo email request failed with HTTP {response.status}.")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"Brevo email request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach Brevo: {exc.reason}") from exc
        return True

    # Keep SMTP support as a fallback for non-Render deployments.
    host=os.environ.get("SMTP_HOST"); port=int(os.environ.get("SMTP_PORT","587")); user=os.environ.get("SMTP_USERNAME"); password=os.environ.get("SMTP_PASSWORD"); sender=os.environ.get("SMTP_FROM") or user
    if all([host,user,password,sender]):
        msg=EmailMessage(); msg["From"]=sender; msg["To"]=to; msg["Subject"]=subject; msg.set_content(body)
        with smtplib.SMTP(host,port,timeout=20) as s:
            s.starttls(); s.login(user,password); s.send_message(msg)
        return True

    if IS_PRODUCTION:
        raise RuntimeError("Email is not configured. Set BREVO_API_KEY and BREVO_FROM_EMAIL.")
    print("
EMAIL (development mode)
To:",to,"
Subject:",subject,"
",body,"
")
    return False

def verification_link(token):
    base=APP_BASE_URL or url_for("verify_email", token=token, _external=True).rsplit("/verify/",1)[0]
    return f"{base}/verify/{token}"

@app.route("/")
def home():
    c=db(); counts={k:c.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] for k,t in [("physios","users"),("jobs","jobs"),("events","events"),("home_visits","home_visits")]}; c.close(); return render_template("home.html",counts=counts)

@app.route("/register",methods=["GET","POST"])
def register():
    if request.method=="POST":
        if not rate_limit("register:"+request.remote_addr,5,3600): flash("Too many registration attempts. Please try again later.","error"); return redirect(url_for("register"))
        name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower(); password=request.form.get("password",""); pincode=request.form.get("pincode","").strip()
        if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",email) or len(password)<10 or not valid_pincode(pincode): flash("Please provide a valid email, a password of at least 10 characters, and a valid 6-digit PIN code.","error"); return redirect(url_for("register"))
        c=db()
        try:
            cur=c.execute("INSERT INTO users (name,email,password_hash,phone,qualification,specialization,state,city,pincode,registration_no,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(name,email,generate_password_hash(password,method="pbkdf2:sha256:600000"),request.form.get("phone",""),request.form.get("qualification",""),request.form.get("specialization",""),request.form.get("state",""),request.form.get("city",""),pincode,request.form.get("registration_no",""),now()))
            uid=cur.lastrowid; token=make_token(); expires=(datetime.utcnow()+timedelta(hours=24)).isoformat(); c.execute("INSERT INTO verification_tokens(user_id,token,expires_at) VALUES(?,?,?)",(uid,token,expires)); c.commit()
        except (sqlite3.IntegrityError, UniqueViolation):
            c.rollback(); c.close(); flash("An account with this email already exists.","error"); return redirect(url_for("register"))
        c.close()
        link=verification_link(token)
        try: sent=send_email(email,"Verify your Indian Physios account",f"Welcome to Indian Physios, {name}!\n\nVerify your email by opening this link:\n{link}\n\nThis link expires in 24 hours.")
        except Exception as e: app.logger.exception(e); sent=False
        flash("Account created. Check your email for the verification link." if sent else "Account created. Email is not configured yet; the verification link was printed in the server log.","success")
        if not sent: print("VERIFICATION LINK:",link)
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/verify/<token>")
def verify_email(token):
    c=db(); row=c.execute("SELECT user_id,expires_at FROM verification_tokens WHERE token=?",(token,)).fetchone()
    if not row: c.close(); flash("Invalid or expired verification link.","error"); return redirect(url_for("login"))
    if datetime.fromisoformat(row["expires_at"]) < datetime.utcnow(): c.execute("DELETE FROM verification_tokens WHERE token=?",(token,)); c.commit(); c.close(); flash("This verification link has expired.","error"); return redirect(url_for("login"))
    c.execute("UPDATE users SET verified_email=1 WHERE id=?",(row["user_id"],)); c.execute("DELETE FROM verification_tokens WHERE token=?",(token,)); c.commit(); c.close(); flash("Email verified. You can now log in.","success"); return redirect(url_for("login"))

@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        if not rate_limit("login:"+request.remote_addr,10,900): flash("Too many login attempts. Please wait 15 minutes and try again.","error"); return redirect(url_for("login"))
        email=request.form.get("email","").strip().lower(); password=request.form.get("password",""); c=db(); u=c.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone(); c.close()
        if not u or not check_password_hash(u["password_hash"],password): flash("Invalid email or password.","error"); return redirect(url_for("login"))
        if not u["verified_email"]: flash("Please verify your email first.","error"); return redirect(url_for("login"))
        session.clear(); session["user_id"]=u["id"]; return redirect(url_for("profile",user_id=u["id"]))
    return render_template("login.html")

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("home"))

@app.route("/profile/edit",methods=["GET","POST"])
def edit_profile():
    u=current_user()
    if not u: return redirect(url_for("login"))
    if request.method=="POST":
        name=request.form.get("name","").strip(); pincode=request.form.get("pincode","").strip()
        if not name or not valid_pincode(pincode): flash("Name and a valid 6-digit PIN code are required.","error"); return redirect(url_for("edit_profile"))
        photo=u["photo"]; photo_data=u["photo_data"]; photo_mime=u["photo_mime"]
        uploaded=request.files.get("photo")
        if uploaded and uploaded.filename:
            if not allowed_file(uploaded.filename): flash("Profile photo must be PNG, JPG, JPEG or WEBP.","error"); return redirect(url_for("edit_profile"))
            data=uploaded.read()
            if len(data)>MAX_UPLOAD_BYTES: flash("Profile photo must be 2 MB or smaller.","error"); return redirect(url_for("edit_profile"))
            ext=uploaded.filename.rsplit(".",1)[1].lower(); photo=f"user_{u['id']}_{secrets.token_hex(8)}.{ext}"; photo_data=data; photo_mime={"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","webp":"image/webp"}[ext]
        c=db(); c.execute("UPDATE users SET name=?,phone=?,qualification=?,specialization=?,state=?,city=?,pincode=?,registration_no=?,bio=?,clinic=?,experience=?,education=?,website=?,photo=?,photo_data=?,photo_mime=? WHERE id=?",(name,request.form.get("phone","").strip(),request.form.get("qualification","").strip(),request.form.get("specialization","").strip(),request.form.get("state","").strip(),request.form.get("city","").strip(),pincode,request.form.get("registration_no","").strip(),request.form.get("bio","").strip(),request.form.get("clinic","").strip(),request.form.get("experience","").strip(),request.form.get("education","").strip(),request.form.get("website","").strip(),photo,photo_data,photo_mime,u["id"])); c.commit(); c.close(); flash("Profile updated successfully.","success"); return redirect(url_for("profile",user_id=u["id"]))
    return render_template("edit_profile.html",user=u)

@app.route("/media/profile/<int:user_id>")
def profile_media(user_id):
    c=db(); u=c.execute("SELECT photo_data,photo_mime FROM users WHERE id=?",(user_id,)).fetchone(); c.close()
    if not u or not u["photo_data"]: abort(404)
    return send_file(BytesIO(bytes(u["photo_data"])),mimetype=u["photo_mime"] or "image/jpeg",max_age=86400)

@app.route("/profile/<int:user_id>")
def profile(user_id):
    c=db(); u=c.execute("SELECT * FROM users WHERE id=?",(user_id,)).fetchone(); c.close()
    if not u: abort(404)
    return render_template("profile.html",user=u)

@app.route("/directory")
def directory():
    q=request.args.get("q","").strip(); state=request.args.get("state","").strip(); pincode=request.args.get("pincode","").strip(); specialization=request.args.get("specialization","").strip(); c=db()
    if q or state or pincode or specialization:
        like=f"%{q}%"; sl=f"%{specialization}%"; pl=f"%{pincode}%"; users=c.execute("SELECT * FROM users WHERE (name LIKE ? OR specialization LIKE ? OR qualification LIKE ? OR city LIKE ?) AND (?='' OR state=?) AND (?='' OR pincode LIKE ?) AND (?='' OR specialization LIKE ?) ORDER BY name",(like,like,like,like,state,state,pl,pl,sl,sl)).fetchall()
    else: users=c.execute("SELECT * FROM users ORDER BY name").fetchall()
    c.close(); return render_template("directory.html",users=users,q=q,state=state,pincode=pincode,specialization=specialization)

# Keep the existing connection, messaging, community, jobs, home visits, notifications, events and admin routes.
@app.route("/connect/<int:user_id>",methods=["POST"])
def connect(user_id):
    u=current_user()
    if not u: return redirect(url_for("login"))
    if user_id==u["id"]: flash("You cannot connect with yourself.","error"); return redirect(url_for("profile",user_id=user_id))
    c=db(); target=c.execute("SELECT id FROM users WHERE id=?",(user_id,)).fetchone()
    if not target: c.close(); abort(404)
    existing=c.execute("SELECT * FROM connections WHERE (requester_id=? AND receiver_id=?) OR (requester_id=? AND receiver_id=?)",(u["id"],user_id,user_id,u["id"])).fetchone()
    if existing: flash("A connection request already exists.","error")
    else: c.execute("INSERT INTO connections(requester_id,receiver_id,status,created_at) VALUES(?,?,?,?)",(u["id"],user_id,"pending",now())); c.commit(); flash("Connection request sent.","success")
    c.close(); return redirect(url_for("profile",user_id=user_id))

@app.route("/connections")
def connections():
    u=current_user()
    if not u:return redirect(url_for("login"))
    c=db(); incoming=c.execute("SELECT connections.id,users.id AS user_id,users.name,users.specialization,users.city,users.state FROM connections JOIN users ON users.id=connections.requester_id WHERE connections.receiver_id=? AND connections.status='pending' ORDER BY connections.id DESC",(u["id"],)).fetchall(); outgoing=c.execute("SELECT connections.id,users.id AS user_id,users.name,users.specialization,users.city,users.state FROM connections JOIN users ON users.id=connections.receiver_id WHERE connections.requester_id=? AND connections.status='pending' ORDER BY connections.id DESC",(u["id"],)).fetchall(); accepted=c.execute("SELECT users.id,users.name,users.specialization,users.city,users.state FROM connections JOIN users ON users.id=CASE WHEN connections.requester_id=? THEN connections.receiver_id ELSE connections.requester_id END WHERE (connections.requester_id=? OR connections.receiver_id=?) AND connections.status='accepted' ORDER BY users.name",(u["id"],u["id"],u["id"])).fetchall(); c.close(); return render_template("connections.html",incoming=incoming,outgoing=outgoing,accepted=accepted)

@app.route("/connection/<int:connection_id>/<action>",methods=["POST"])
def connection_action(connection_id,action):
    u=current_user()
    if not u:return redirect(url_for("login"))
    if action not in ("accept","decline","cancel"):abort(400)
    c=db(); conn=c.execute("SELECT * FROM connections WHERE id=?",(connection_id,)).fetchone()
    if not conn:c.close();abort(404)
    if action=="accept" and conn["receiver_id"]==u["id"] and conn["status"]=="pending":c.execute("UPDATE connections SET status='accepted' WHERE id=?",(connection_id,));flash("Connection accepted.","success")
    elif action=="decline" and conn["receiver_id"]==u["id"] and conn["status"]=="pending":c.execute("DELETE FROM connections WHERE id=?",(connection_id,));flash("Connection declined.","success")
    elif action=="cancel" and conn["requester_id"]==u["id"] and conn["status"]=="pending":c.execute("DELETE FROM connections WHERE id=?",(connection_id,));flash("Connection request cancelled.","success")
    else:flash("That connection action is not available.","error")
    c.commit();c.close();return redirect(url_for("connections"))

def are_connected(a,b):
    c=db(); row=c.execute("SELECT 1 AS ok FROM connections WHERE ((requester_id=? AND receiver_id=?) OR (requester_id=? AND receiver_id=?)) AND status='accepted' LIMIT 1",(a,b,b,a)).fetchone(); c.close(); return bool(row)

@app.route("/messages")
def messages():
    u=current_user()
    if not u:return redirect(url_for("login"))
    c=db(); rows=c.execute("SELECT x.*,users.name,users.photo,users.specialization,users.city FROM (SELECT m.*,CASE WHEN m.sender_id=? THEN m.receiver_id ELSE m.sender_id END AS other_id FROM messages m WHERE m.sender_id=? OR m.receiver_id=?) x JOIN users ON users.id=x.other_id WHERE x.id IN (SELECT MAX(m2.id) FROM messages m2 WHERE m2.sender_id=? OR m2.receiver_id=? GROUP BY CASE WHEN m2.sender_id=? THEN m2.receiver_id ELSE m2.sender_id END) ORDER BY x.id DESC",(u["id"],u["id"],u["id"],u["id"],u["id"],u["id"])).fetchall(); c.close(); return render_template("messages.html",conversations=rows)

@app.route("/messages/<int:user_id>",methods=["GET","POST"])
def message_user(user_id):
    u=current_user()
    if not u:return redirect(url_for("login"))
    if user_id==u["id"] or not are_connected(u["id"],user_id): flash("You can message a physiotherapist after you are connected.","error"); return redirect(url_for("profile",user_id=user_id))
    c=db(); target=c.execute("SELECT * FROM users WHERE id=?",(user_id,)).fetchone(); c.close()
    if not target:abort(404)
    if request.method=="POST":
        if not rate_limit(f"message:{u['id']}",60,3600): flash("Message limit reached. Please try again later.","error")
        else:
            body=request.form.get("body","").strip()
            if not body or len(body)>5000:flash("Message must be between 1 and 5,000 characters.","error")
            else:c=db();c.execute("INSERT INTO messages(sender_id,receiver_id,body,created_at) VALUES(?,?,?,?)",(u["id"],user_id,body,now()));c.commit();c.close();return redirect(url_for("message_user",user_id=user_id))
    c=db();thread=c.execute("SELECT messages.*,users.name AS sender_name FROM messages JOIN users ON users.id=messages.sender_id WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?) ORDER BY messages.id",(u["id"],user_id,user_id,u["id"])).fetchall();c.execute("UPDATE messages SET read_at=? WHERE sender_id=? AND receiver_id=? AND read_at IS NULL",(now(),user_id,u["id"]));c.commit();c.close();return render_template("message_thread.html",target=target,thread=thread)

@app.route("/community",methods=["GET","POST"])
def community():
    if request.method=="POST":
        u=current_user()
        if not u:return redirect(url_for("login"))
        if not rate_limit(f"post:{u['id']}",10,3600):flash("Posting limit reached. Please try again later.","error")
        else:
            title=request.form.get("title","").strip();body=request.form.get("body","").strip()
            if title and body and len(title)<=200 and len(body)<=5000:c=db();c.execute("INSERT INTO posts(user_id,title,body,created_at) VALUES(?,?,?,?)",(u["id"],title,body,now()));c.commit();c.close()
        return redirect(url_for("community"))
    c=db();posts=c.execute("SELECT posts.*,users.name FROM posts JOIN users ON users.id=posts.user_id ORDER BY posts.id DESC").fetchall();c.close();return render_template("community.html",posts=posts)

@app.route("/jobs",methods=["GET","POST"])
def jobs():
    if request.method=="POST":
        u=current_user()
        if not u:return redirect(url_for("login"))
        fields=[request.form.get(x,"").strip() for x in ("title","organization","city","description")]
        if all(fields) and len(fields[0])<=200 and len(fields[3])<=5000:c=db();c.execute("INSERT INTO jobs(user_id,title,organization,city,description,created_at) VALUES(?,?,?,?,?,?)",(u["id"],*fields,now()));c.commit();c.close();flash("Job posted successfully.","success")
        else:flash("Please complete all job fields.","error")
        return redirect(url_for("jobs"))
    q=request.args.get("q","").strip();c=db();like=f"%{q}%";rows=c.execute("SELECT jobs.*,users.name FROM jobs JOIN users ON users.id=jobs.user_id WHERE (?='') OR jobs.title LIKE ? OR jobs.organization LIKE ? OR jobs.city LIKE ? OR jobs.description LIKE ? ORDER BY jobs.id DESC",(q,like,like,like,like)).fetchall();c.close();return render_template("jobs.html",jobs=rows,q=q)

@app.route("/home-visits",methods=["GET","POST"])
def home_visits():
    u=current_user()
    if request.method=="POST":
        if not u:return redirect(url_for("login"))
        location=request.form.get("location","").strip();pincode=request.form.get("pincode","").strip();diagnosis=request.form.get("diagnosis","").strip();physio_need=request.form.get("physio_need","").strip();details=request.form.get("details","").strip()
        if not(location and valid_pincode(pincode) and diagnosis and physio_need and details):flash("Please complete all Home Visit fields and enter a valid 6-digit PIN code.","error");return redirect(url_for("home_visits"))
        if not rate_limit(f"homevisit:{u['id']}",10,3600):flash("Home Visit posting limit reached. Please try again later.","error");return redirect(url_for("home_visits"))
        c=db();cur=c.execute("INSERT INTO home_visits(user_id,location,diagnosis,physio_need,details,pincode,created_at) VALUES(?,?,?,?,?,?,?)",(u["id"],location,diagnosis,physio_need,details,pincode,now()));visit_id=cur.lastrowid;nearby=c.execute("SELECT id FROM users WHERE id<>? AND pincode=? AND verified_email=1",(u["id"],pincode)).fetchall()
        for person in nearby:c.execute("INSERT INTO notifications(user_id,home_visit_id,title,body,created_at) VALUES(?,?,?,?,?)",(person["id"],visit_id,"New Home Visit near you",f"A Home Visit opportunity was posted in your PIN code area. Open Home Visits to review the case.",now()))
        c.commit();c.close();flash(f"Home Visit posted successfully. {len(nearby)} physiotherapist(s) in the same PIN code area were notified.","success");return redirect(url_for("home_visits"))
    q=request.args.get("q","").strip();c=db();like=f"%{q}%";visits=c.execute("SELECT home_visits.*,users.name,users.email FROM home_visits JOIN users ON users.id=home_visits.user_id WHERE (?='') OR home_visits.location LIKE ? OR home_visits.pincode LIKE ? OR home_visits.diagnosis LIKE ? OR home_visits.physio_need LIKE ? OR home_visits.details LIKE ? ORDER BY home_visits.id DESC",(q,like,like,like,like,like)).fetchall();c.close();return render_template("home_visits.html",visits=visits,q=q)

@app.route("/notifications")
def notifications():
    u=current_user()
    if not u:return redirect(url_for("login"))
    c=db();rows=c.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC",(u["id"],)).fetchall();c.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL",(now(),u["id"]));c.commit();c.close();return render_template("notifications.html",notifications=rows)

@app.route("/events",methods=["GET","POST"])
def events():
    if request.method=="POST":
        u=current_user()
        if not u:return redirect(url_for("login"))
        fields=[request.form.get(x,"").strip() for x in ("title","location","event_date","description")]
        if all(fields):c=db();c.execute("INSERT INTO events(user_id,title,location,event_date,description,created_at) VALUES(?,?,?,?,?,?)",(u["id"],*fields,now()));c.commit();c.close();flash("Event posted successfully.","success")
        return redirect(url_for("events"))
    c=db();rows=c.execute("SELECT events.*,users.name FROM events JOIN users ON users.id=events.user_id ORDER BY event_date").fetchall();c.close();return render_template("events.html",events=rows)

@app.route("/admin")
def admin():
    u=current_user()
    if not u or u["email"].lower()!=os.environ.get("ADMIN_EMAIL","admin@indian_physios.local").lower(): abort(403)
    c=db();users=c.execute("SELECT * FROM users ORDER BY id DESC").fetchall();c.close();return render_template("admin.html",users=users)

@app.route("/admin/verify/<int:user_id>",methods=["POST"])
def admin_verify(user_id):
    u=current_user()
    if not u or u["email"].lower()!=os.environ.get("ADMIN_EMAIL","admin@indian_physios.local").lower():abort(403)
    c=db();c.execute("UPDATE users SET verified_physio=1 WHERE id=?",(user_id,));c.commit();c.close();return redirect(url_for("admin"))

@app.route("/about")
def about():return render_template("about.html")

init_db()

if __name__=="__main__":
    app.run(host="127.0.0.1",port=PORT,debug=False)
