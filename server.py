#!/usr/bin/env python3
"""
Trusty-Ka — Backend Server
Run:  python3 server.py
Open: http://localhost:8000

Database: PostgreSQL on Render (via DATABASE_URL env var), SQLite locally.
"""
import http.server, json, hashlib, uuid, os, base64, urllib.parse, time, threading

# ── Database abstraction ──────────────────────────────────────────────────────
# Uses PostgreSQL when DATABASE_URL is set (Render production),
# falls back to SQLite for local development.
DATABASE_URL = os.environ.get('DATABASE_URL', '')

if DATABASE_URL:
    import psycopg2, psycopg2.extras
    # Render gives postgres:// but psycopg2 needs postgresql://
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

    def get_db():
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn

    def fetchall(cursor): return [dict(row) for row in cursor.fetchall()]
    def fetchone(cursor):
        row = cursor.fetchone()
        return dict(row) if row else None

    PH = '%s'   # PostgreSQL placeholder
    RETURNING = 'RETURNING id'

    class RowDict(dict):
        """Makes psycopg2 rows subscriptable like SQLite rows"""
        def __getitem__(self, key):
            if isinstance(key, int):
                return list(self.values())[key]
            return super().__getitem__(key)

    def dict_cursor(conn):
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

else:
    import sqlite3
    _DATA_DIR = '/data' if os.path.isdir('/data') else os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(_DATA_DIR, 'trustedservice.db')
    PH = '?'
    RETURNING = ''

    def get_db():
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def fetchall(cursor): return [dict(r) for r in cursor.fetchall()]
    def fetchone(cursor):
        row = cursor.fetchone()
        return dict(row) if row else None

    def dict_cursor(conn): return conn.cursor()


def sql(q):
    """Replace ? with %s for PostgreSQL, keep ? for SQLite"""
    if DATABASE_URL:
        return q.replace('?', '%s')
    return q


# ── Schema ────────────────────────────────────────────────────────────────────

PORT = int(os.environ.get('PORT', 8000))

import sys

def init_db():
    conn = get_db()
    c = conn.cursor()
    db_type = 'PostgreSQL (Neon)' if DATABASE_URL else f'SQLite'
    print(f"\n{'='*52}", flush=True)
    print(f"  DATABASE: {db_type}", flush=True)
    if DATABASE_URL:
        safe_url = DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else 'connected'
        print(f"  HOST: {safe_url[:60]}", flush=True)
    print(f"{'='*52}\n", flush=True)
    sys.stdout.flush()
    if DATABASE_URL:
        # PostgreSQL uses SERIAL, TEXT, and different syntax
        statements = [
            """CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, role TEXT DEFAULT 'provider',
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS providers (
                id TEXT PRIMARY KEY, user_id TEXT,
                first_name TEXT, last_name TEXT, phone TEXT,
                location TEXT, category TEXT, bio TEXT,
                id_photo TEXT DEFAULT '', profile_photo TEXT DEFAULT '',
                work_photos TEXT DEFAULT '[]',
                services TEXT DEFAULT '[]',
                status TEXT DEFAULT 'pending',
                trust_score INTEGER DEFAULT 0, rating REAL DEFAULT 0.0,
                review_count INTEGER DEFAULT 0, jobs_done INTEGER DEFAULT 0,
                reliability REAL DEFAULT 0.0,
                submitted_at TIMESTAMP DEFAULT NOW(), approved_at TIMESTAMP,
                rejection_reason TEXT DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS reviews (
                id TEXT PRIMARY KEY, provider_id TEXT,
                reviewer_name TEXT, stars INTEGER, text TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS bookings (
                id TEXT PRIMARY KEY, provider_id TEXT,
                client_name TEXT, client_phone TEXT,
                service TEXT, date TEXT, notes TEXT,
                status TEXT DEFAULT 'pending',
                urgency TEXT DEFAULT '',
                accepted INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )""",
            """CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY, user_id TEXT,
                provider_id TEXT DEFAULT '',
                type TEXT DEFAULT 'info',
                message TEXT, read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )""",
        ]
        for s in statements:
            c.execute(s)
    else:
        c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL, role TEXT DEFAULT 'client',
        name TEXT, created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS providers (
        id TEXT PRIMARY KEY, user_id TEXT,
        first_name TEXT, last_name TEXT, phone TEXT,
        email TEXT, location TEXT, category TEXT, bio TEXT,
        services TEXT DEFAULT '[]',
        id_number TEXT, status TEXT DEFAULT 'pending',
        trust_score INTEGER DEFAULT 0, rating REAL DEFAULT 0.0,
        review_count INTEGER DEFAULT 0, jobs_done INTEGER DEFAULT 0,
        reliability REAL DEFAULT 0.0,
        profile_photo TEXT DEFAULT '', work_photos TEXT DEFAULT '[]',
        ref_code TEXT,
        submitted_at TEXT DEFAULT (datetime('now')), approved_at TEXT,
        rejection_reason TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS bookings (
        id TEXT PRIMARY KEY, provider_id TEXT,
        client_name TEXT, client_phone TEXT,
        service TEXT, date TEXT, notes TEXT,
        status TEXT DEFAULT 'pending',
        urgency TEXT DEFAULT '',
        accepted INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS notifications (
        id TEXT PRIMARY KEY, user_id TEXT, provider_id TEXT,
        type TEXT, message TEXT, read INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS reviews (
        id TEXT PRIMARY KEY, provider_id TEXT,
        reviewer_name TEXT, stars INTEGER, text TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)
    admin_h = hashlib.sha256('Michael 009'.encode()).hexdigest()
    if DATABASE_URL:
        c.execute("""INSERT INTO users (id,email,password_hash,role) VALUES (%s,%s,%s,%s)
                     ON CONFLICT(email) DO NOTHING""",
                  ('admin-001','michaelvincentnyak@gmail.com',admin_h,'admin'))
    else:
        c.execute("INSERT OR IGNORE INTO users (id,email,password_hash,role) VALUES (?,?,?,?)",
                  ('admin-001','michaelvincentnyak@gmail.com',admin_h,'admin'))
    conn.commit(); conn.close()
    print(f"[DB] Ready — {'PostgreSQL' if DATABASE_URL else 'SQLite'}", flush=True)

def hp(pw): return hashlib.sha256(pw.encode()).hexdigest()

# Server secret — tokens are signed with this so they can't be forged
# On Render, set TOKEN_SECRET env var to a long random string for production
_TOKEN_SECRET = os.environ.get('TOKEN_SECRET', 'ts-local-dev-secret-change-in-prod-9mVk')

def mk_token(uid, role, email):
    """Create a signed token: base64(uid:role:email) + HMAC signature"""
    import hmac
    payload = f"{uid}:{role}:{email}"
    sig = hmac.new(_TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return base64.b64encode(f"{payload}:{sig}".encode()).decode()

def verify_token_sig(uid, role, email, sig):
    """Verify the HMAC signature on a token"""
    import hmac as _hmac
    payload = f"{uid}:{role}:{email}"
    expected = _hmac.new(_TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return _hmac.compare_digest(expected, sig)
def get_token_user(handler):
    """Decode token, verify HMAC signature, AND verify against DB."""
    auth = handler.headers.get('Authorization','')
    if not auth.startswith('Bearer '): return None
    try:
        decoded = base64.b64decode(auth[7:]).decode()
        parts = decoded.split(':',3)
        if len(parts) != 4: return None
        uid, role, email, sig = parts
    except: return None
    # Verify HMAC signature first (fast, no DB hit)
    if not verify_token_sig(uid, role, email, sig): return None
    # Then verify against DB (ensures role hasn't changed, account not removed)
    try:
        conn = get_db()
        row = conn.execute(sql("SELECT id, email, role FROM users WHERE id=?"), (uid,)).fetchone()
        conn.close()
        if not row: return None
        r = dict(row)
        if r['email'].lower() != email.lower(): return None
        if r['role'] != role: return None  # role changed in DB (e.g. suspended)
        return {'id': r['id'], 'role': r['role'], 'email': r['email']}
    except:
        return None

def require_admin(handler):
    """Returns user dict if caller is a verified admin, otherwise sends 403 and returns None."""
    tu = get_token_user(handler)
    if not tu or tu.get('role') != 'admin':
        respond(handler, {'error': 'Forbidden — admin access required'}, 403)
        return None
    return tu

def fix_timestamps(obj):
    """Normalize SQLite datetime strings (2026-06-23 14:30:00) to ISO 8601 (2026-06-23T14:30:00Z)"""
    if isinstance(obj, dict):
        return {k: fix_timestamps(v) if isinstance(v, str) and len(v)==19 and ' ' in v and v[10]==' '
                else fix_timestamps(v) if isinstance(v, (dict, list)) else v
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [fix_timestamps(i) for i in obj]
    if isinstance(obj, str) and len(obj)==19 and obj[10]==' ':
        return obj.replace(' ', 'T') + 'Z'
    return obj

def respond(handler, data, status=200):
    data = fix_timestamps(data)
    body = json.dumps(data, default=str).encode()
    handler.send_response(status)
    for k,v in [('Content-Type','application/json'),('Content-Length',len(body)),
                ('Access-Control-Allow-Origin','*'),
                ('Access-Control-Allow-Methods','GET,POST,PUT,DELETE,OPTIONS'),
                ('Access-Control-Allow-Headers','Content-Type,Authorization')]:
        handler.send_header(k,v)
    handler.end_headers()
    handler.wfile.write(body)

def read_body(handler):
    n = int(handler.headers.get('Content-Length',0))
    if not n: return {}
    try: return json.loads(handler.rfile.read(n))
    except: return {}

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_OPTIONS(self):
        self.send_response(200)
        for k,v in [('Access-Control-Allow-Origin','*'),
                    ('Access-Control-Allow-Methods','GET,POST,PUT,DELETE,OPTIONS'),
                    ('Access-Control-Allow-Headers','Content-Type,Authorization')]:
            self.send_header(k,v)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs   = urllib.parse.parse_qs(parsed.query)

        # Server time endpoint — lets browser sync countdown with server clock
        if path == '/api/time':
            import datetime
            now = datetime.datetime.now(datetime.timezone.utc)
            respond(self, {'server_time': now.strftime('%Y-%m-%dT%H:%M:%S') + 'Z'}); return

        if path in ('/', '/index.html'):
            fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
            if os.path.exists(fp):
                body = open(fp,'rb').read()
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Content-Length', len(body))
                self.end_headers()
                self.wfile.write(body)
            else:
                respond(self, {'error': 'index.html not found'}, 404)
            return

        parts = [p for p in path.split('/') if p]

        # Check if a client (by phone) has a completed booking with this provider — used to gate reviews
        if path == '/api/reviews/can-review':
            pid = qs.get('provider_id',[''])[0]
            phone = qs.get('phone',[''])[0].strip()
            conn = get_db()
            row = conn.execute(
                "SELECT id FROM bookings WHERE provider_id=? AND client_phone=? AND status='completed' LIMIT 1",
                (pid, phone)).fetchone()
            conn.close()
            respond(self, {'can_review': bool(row)}); return

        # Application status check by email (no login needed)
        if path.startswith('/api/application-status/'):
            from urllib.parse import unquote
            email = unquote(path.split('/api/application-status/')[-1]).lower().strip()
            conn = get_db()
            user = conn.execute(sql("SELECT id FROM users WHERE LOWER(email)=?"),(email,)).fetchone()
            if not user:
                conn.close()
                respond(self, {'status':'not_found'}, 404); return
            prov = conn.execute(
                sql("SELECT first_name,last_name,category,location,status,submitted_at,approved_at,rejection_reason FROM providers WHERE user_id=? ORDER BY submitted_at DESC LIMIT 1"),
                (user[0],)).fetchone()
            conn.close()
            if not prov:
                respond(self, {'status':'not_found'}, 404); return
            respond(self, dict(prov)); return

        if path == '/api/providers':
            conn = get_db()
            cat = qs.get('cat',[''])[0]
            q   = qs.get('q',[''])[0].lower()
            loc = qs.get('loc',[''])[0].lower()
            qsql = "SELECT * FROM providers WHERE status='approved'"
            args = []
            if cat and cat != 'All':
                qsql += " AND category=" + PH; args.append(cat)
            if loc:
                qsql += " AND LOWER(location) LIKE " + PH; args.append(f'%{loc}%')
            qsql += " ORDER BY (rating * review_count) DESC, review_count DESC, trust_score DESC"
            rows = [dict(r) for r in conn.execute(qsql, args).fetchall()]
            # Client-side keyword search on name/bio/category
            if q:
                rows = [r for r in rows if q in (r.get('first_name','') or '').lower()
                        or q in (r.get('last_name','') or '').lower()
                        or q in (r.get('category','') or '').lower()
                        or q in (r.get('bio','') or '').lower()
                        or q in (r.get('location','') or '').lower()]
            if q:
                rows = [r for r in rows if q in (r.get('first_name','')+' '+r.get('last_name','')+' '+r.get('category','')+' '+r.get('bio','')).lower()]
            for r in rows:
                r['services'] = json.loads(r.get('services','[]'))
            conn.close()
            respond(self, rows); return

        if path == '/api/auth/me':
            tu = get_token_user(self)
            if not tu: respond(self, {'error':'not authenticated'}, 401); return
            conn = get_db()
            user = conn.execute(sql("SELECT id,email,role,name FROM users WHERE id=?"), (tu['id'],)).fetchone()
            if not user: respond(self, {'error':'user not found'}, 404); conn.close(); return
            u = dict(user)
            if u['role'] == 'removed_provider':
                conn.close(); respond(self, {'error':'Account removed'}, 403); return
            provider = None
            if u['role']=='provider':
                prow = conn.execute(sql("SELECT * FROM providers WHERE user_id=?"), (u['id'],)).fetchone()
                if prow:
                    provider = dict(prow)
                    provider['services'] = json.loads(provider.get('services','[]'))
                    provider['work_photos'] = json.loads(provider.get('work_photos','[]'))
            conn.close()
            respond(self, {'user':u,'provider':provider}); return

        if path == '/api/admin/stats':
            conn = get_db()
            respond(self, {
                'total':    conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0],
                'verified': conn.execute("SELECT COUNT(*) FROM providers WHERE status='approved'").fetchone()[0],
                'pending':  conn.execute("SELECT COUNT(*) FROM providers WHERE status='pending'").fetchone()[0],
                'bookings': conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0],
            })
            conn.close(); return

        if path == '/api/admin/pending':
            conn = get_db()
            rows = [dict(r) for r in conn.execute("SELECT * FROM providers WHERE status='pending' ORDER BY submitted_at DESC").fetchall()]
            for r in rows: r['services'] = json.loads(r.get('services','[]'))
            conn.close(); respond(self, rows); return

        if path == '/api/admin/bookings':
            if not require_admin(self): return
            conn = get_db()
            rows = conn.execute("""SELECT b.*, p.first_name, p.last_name FROM bookings b
                                    LEFT JOIN providers p ON p.id=b.provider_id
                                    ORDER BY b.created_at DESC""").fetchall()
            conn.close()
            respond(self, [dict(r) for r in rows]); return

        if path == '/api/admin/bookings/pending-services':
            if not require_admin(self): return
            conn = get_db()
            rows = conn.execute("""SELECT b.*, p.first_name, p.last_name FROM bookings b
                                    LEFT JOIN providers p ON p.id=b.provider_id
                                    WHERE b.status='pending' AND b.accepted=1
                                    ORDER BY b.created_at DESC""").fetchall()
            conn.close()
            respond(self, [dict(r) for r in rows]); return

        # Client bookings by phone number — for booking/service status page
        if path.startswith('/api/bookings/client/'):
            from urllib.parse import unquote
            phone = unquote(path.split('/api/bookings/client/')[-1])
            conn = get_db()
            rows = conn.execute(sql("""
                SELECT b.*, p.first_name||' '||p.last_name as provider_name
                FROM bookings b LEFT JOIN providers p ON p.id=b.provider_id
                WHERE b.client_phone=? ORDER BY b.created_at DESC
            """), (phone,)).fetchall()
            conn.close()
            respond(self, [dict(r) for r in rows]); return

        if path == '/api/admin/providers':
            if not require_admin(self): return
            conn = get_db()
            rows = [dict(r) for r in conn.execute("SELECT * FROM providers ORDER BY submitted_at DESC").fetchall()]
            for r in rows: r['services'] = json.loads(r.get('services','[]'))
            conn.close(); respond(self, rows); return

        # /api/providers/by-user/{user_id} — fetch provider by user account (for login refresh)
        if len(parts)==4 and parts[0]=='api' and parts[1]=='providers' and parts[2]=='by-user':
            uid = parts[3]
            conn = get_db()
            row = conn.execute(sql("SELECT * FROM providers WHERE user_id=?"), (uid,)).fetchone()
            if not row: respond(self,{'error':'not found'},404); conn.close(); return
            p = dict(row)
            p['services'] = json.loads(p.get('services','[]'))
            p['work_photos'] = json.loads(p.get('work_photos','[]'))
            conn.close(); respond(self, p); return

        if len(parts)==3 and parts[0]=='api' and parts[1]=='providers':
            pid = parts[2]
            conn = get_db()
            row = conn.execute(sql("SELECT * FROM providers WHERE id=?"), (pid,)).fetchone()
            if not row: respond(self,{'error':'not found'},404); conn.close(); return
            p = dict(row)
            p['services'] = json.loads(p.get('services','[]'))
            p['work_photos'] = json.loads(p.get('work_photos','[]'))
            p['reviews'] = [dict(r) for r in conn.execute(
                "SELECT * FROM reviews WHERE provider_id=? ORDER BY created_at DESC LIMIT 10",(pid,)).fetchall()]
            conn.close(); respond(self, p); return

        if len(parts)==3 and parts[0]=='api' and parts[1]=='notifications':
            uid = parts[2]
            conn = get_db()
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 30",(uid,)).fetchall()]
            conn.close(); respond(self, rows); return

        if len(parts)==4 and parts[0]=='api' and parts[1]=='bookings' and parts[2]=='provider':
            pid = parts[3]
            conn = get_db()
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM bookings WHERE provider_id=? ORDER BY created_at DESC",(pid,)).fetchall()]
            conn.close(); respond(self, rows); return

        respond(self, {'error':'not found'}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = read_body(self)

        if path == '/api/auth/forgot-password':
            email = body.get('email','').lower().strip()
            conn = get_db()
            user = conn.execute(sql("SELECT id FROM users WHERE LOWER(email)=?"), (email,)).fetchone()
            if not user:
                conn.close(); respond(self, {'error':'No account found with that email.'}, 404); return
            # Generate a simple memorable temp password
            import random, string
            temp_pw = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            new_hash = hashlib.sha256(temp_pw.encode()).hexdigest()
            conn.execute(sql("UPDATE users SET password_hash=? WHERE id=?"), (new_hash, user['id']))
            conn.commit(); conn.close()
            respond(self, {'success': True, 'temp_password': temp_pw}); return

        if path == '/api/auth/login':
            email = body.get('email','').strip().lower()
            pw    = body.get('password','')
            conn  = get_db()
            user  = conn.execute(sql("SELECT * FROM users WHERE email=?"), (email,)).fetchone()
            if not user or dict(user)['password_hash'] != hp(pw):
                respond(self,{'error':'Invalid email or password'},401); conn.close(); return
            u = dict(user)
            if u['role'] == 'removed_provider':
                conn.close()
                respond(self, {'error':'This account has been removed from the platform by the administrator. Contact support for more information.'}, 403)
                return
            provider = None
            if u['role'] == 'provider':
                prow = conn.execute(sql("SELECT * FROM providers WHERE user_id=?"), (u['id'],)).fetchone()
                if prow:
                    provider = dict(prow)
                    provider['services'] = json.loads(provider.get('services','[]'))
            conn.close()
            respond(self, {'token': mk_token(u['id'],u['role'],u['email']), 'user': u, 'provider': provider})
            return

        if path == '/api/auth/register':
            email = body.get('email','').strip().lower()
            pw    = body.get('password','')
            name  = body.get('name','')
            if not email or not pw:
                respond(self,{'error':'Email and password required'},400); return
            conn = get_db()
            if conn.execute(sql("SELECT id FROM users WHERE email=?"), (email,)).fetchone():
                respond(self,{'error':'Email already registered'},409); conn.close(); return
            uid = str(uuid.uuid4())
            conn.execute(sql("INSERT INTO users (id,email,password_hash,role,name) VALUES (?,?,?,?,?)"), (uid,email,hp(pw),'client',name))
            conn.commit(); conn.close()
            respond(self, {'token': mk_token(uid,'client',email), 'user':{'id':uid,'email':email,'role':'client','name':name}})
            return

        if path == '/api/providers/register':
            email = body.get('email','').strip().lower()
            pw    = body.get('password','')
            conn  = get_db()
            user_id = None
            if email and pw:
                if conn.execute(sql("SELECT id FROM users WHERE email=?"), (email,)).fetchone():
                    respond(self,{'error':'Email already registered. Please log in.'},409); conn.close(); return
                user_id = str(uuid.uuid4())
                fname = body.get('first_name','')
                lname = body.get('last_name','')
                conn.execute(sql("INSERT INTO users (id,email,password_hash,role,name) VALUES (?,?,?,?,?)"), (user_id,email,hp(pw),'provider',f"{fname} {lname}"))
            pid = str(uuid.uuid4())
            ref = 'TS-'+str(uuid.uuid4())[:8].upper()
            conn.execute(sql("""INSERT INTO providers
                (id,user_id,first_name,last_name,phone,email,location,category,bio,services,id_number,status,ref_code)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"""), (pid, user_id,
                 body.get('first_name',''), body.get('last_name',''),
                 body.get('phone',''), email,
                 body.get('location',''), body.get('category',''),
                 body.get('bio',''), json.dumps(body.get('services',[])),
                 body.get('id_number',''), 'pending', ref))
            nid = str(uuid.uuid4())
            conn.execute(sql("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)"), (nid,'admin-001',pid,'new_registration',
                          f"🆕 New provider application from {body.get('first_name','')} {body.get('last_name','')} · {body.get('category','')} · {body.get('location','')}. Go to Verification Queue to review."))
            conn.commit(); conn.close()
            token = mk_token(user_id,'provider',email) if user_id else None
            respond(self, {'success':True,'ref':ref,'provider_id':pid,'token':token})
            return

        if path == '/api/admin/approve':
            if not require_admin(self): return
            pid = body.get('provider_id')
            conn = get_db()
            conn.execute(sql("UPDATE providers SET status='approved', approved_at=datetime('now'), trust_score=75 WHERE id=?"), (pid,))
            prow = conn.execute(sql("SELECT * FROM providers WHERE id=?"), (pid,)).fetchone()
            if prow:
                p = dict(prow)
                if p.get('user_id'):
                    conn.execute(sql("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)"), (str(uuid.uuid4()), p['user_id'], pid, 'approved',
                                  "🎉 Your provider profile has been approved! Log in to manage your profile and start receiving bookings."))
            conn.commit(); conn.close()
            respond(self, {'success':True}); return

        if path == '/api/admin/reject':
            if not require_admin(self): return
            pid    = body.get('provider_id')
            reason = body.get('reason','Your application did not meet our requirements.')
            conn   = get_db()
            conn.execute(sql("UPDATE providers SET status='rejected', rejection_reason=? WHERE id=?"), (reason, pid))
            prow   = conn.execute(sql("SELECT * FROM providers WHERE id=?"), (pid,)).fetchone()
            if prow:
                p = dict(prow)
                if p.get('user_id'):
                    conn.execute(sql("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)"), (str(uuid.uuid4()), p['user_id'], pid, 'rejected',
                                  f"Your application was not approved. Reason: {reason}"))
            conn.commit(); conn.close()
            respond(self, {'success':True}); return

        # Admin removes a provider entirely from the platform (revokes access + delists profile)
        if path == '/api/admin/remove':
            if not require_admin(self): return
            pid    = body.get('provider_id')
            reason = body.get('reason','Your provider account has been removed from the platform by the administrator.')
            conn   = get_db()
            prow   = conn.execute(sql("SELECT * FROM providers WHERE id=?"), (pid,)).fetchone()
            if not prow:
                conn.close(); respond(self, {'error':'Provider not found'}, 404); return
            p = dict(prow)
            if p.get('user_id'):
                conn.execute(sql("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)"), (str(uuid.uuid4()), p['user_id'], pid, 'removed', f"⚠️ {reason}"))
                conn.execute(sql("UPDATE users SET role='removed_provider' WHERE id=?"), (p['user_id'],))
            conn.execute(sql("UPDATE providers SET status='removed' WHERE id=?"), (pid,))
            conn.commit(); conn.close()
            respond(self, {'success':True}); return

        if path == '/api/admin/suspend':
            if not require_admin(self): return
            pid    = body.get('provider_id')
            reason = body.get('reason','Your account has been temporarily suspended.')
            conn   = get_db()
            prow   = conn.execute(sql("SELECT * FROM providers WHERE id=?"), (pid,)).fetchone()
            if not prow: conn.close(); respond(self, {'error':'Not found'}, 404); return
            p = dict(prow)
            conn.execute(sql("UPDATE providers SET status='suspended' WHERE id=?"), (pid,))
            if p.get('user_id'):
                conn.execute(sql("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)"),
                             (str(uuid.uuid4()), p['user_id'], pid, 'warning',
                              f"🚫 Your account has been temporarily suspended. Reason: {reason}"))
                conn.execute(sql("UPDATE users SET role='suspended_provider' WHERE id=?"), (p['user_id'],))
            conn.commit(); conn.close()
            respond(self, {'success':True}); return

        if path == '/api/admin/unsuspend':
            if not require_admin(self): return
            pid  = body.get('provider_id')
            conn = get_db()
            conn.execute(sql("UPDATE providers SET status='approved' WHERE id=?"), (pid,))
            prow = conn.execute(sql("SELECT user_id FROM providers WHERE id=?"), (pid,)).fetchone()
            if prow and dict(prow).get('user_id'):
                uid = dict(prow)['user_id']
                conn.execute(sql("UPDATE users SET role='provider' WHERE id=?"), (uid,))
                conn.execute(sql("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)"),
                             (str(uuid.uuid4()), uid, pid, 'info',
                              '✅ Your account suspension has been lifted. Welcome back!'))
            conn.commit(); conn.close()
            respond(self, {'success':True}); return

        if path == '/api/admin/warn':
            pid     = body.get('provider_id')
            message = body.get('message','Please review your conduct on the platform.')
            conn    = get_db()
            prow    = conn.execute(sql("SELECT user_id FROM providers WHERE id=?"), (pid,)).fetchone()
            if not prow: conn.close(); respond(self, {'error':'Not found'}, 404); return
            uid = dict(prow).get('user_id')
            if uid:
                conn.execute(sql("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)"),
                             (str(uuid.uuid4()), uid, pid, 'warning',
                              f"⚠️ Admin message: {message}"))
            conn.commit(); conn.close()
            respond(self, {'success':True}); return

        if path == '/api/admin/bookings/delete':
            if not require_admin(self): return
            bid = body.get('booking_id')
            conn = get_db()
            conn.execute(sql("DELETE FROM bookings WHERE id=?"), (bid,))
            conn.commit(); conn.close()
            respond(self, {'success': True}); return

        if path == '/api/admin/bookings/complete':
            if not require_admin(self): return
            bid = body.get('booking_id')
            status = body.get('status', 'completed')  # 'completed' or 'pending'
            conn = get_db()
            conn.execute(sql("UPDATE bookings SET status=? WHERE id=?"), (status, bid))
            conn.commit(); conn.close()
            respond(self, {'success': True}); return

        if path == '/api/bookings/accept':
            bid = body.get('booking_id')
            conn = get_db()
            brow = conn.execute(sql("SELECT * FROM bookings WHERE id=?"), (bid,)).fetchone()
            if brow:
                conn.execute(sql("UPDATE bookings SET accepted=1 WHERE id=?"), (bid,))
                conn.commit()
            conn.close()
            respond(self, {'success': True}); return

        if path == '/api/bookings/reject':
            bid = body.get('booking_id')
            conn = get_db()
            conn.execute(sql("UPDATE bookings SET accepted=-1, status='rejected' WHERE id=?"), (bid,))
            conn.commit(); conn.close()
            respond(self, {'success': True}); return

        if path == '/api/bookings':
            pid   = body.get('provider_id')
            phone = body.get('client_phone','').strip()
            conn  = get_db()
            # Block provider from booking themselves
            if phone:
                own = conn.execute(sql("SELECT phone FROM providers WHERE id=?"), (pid,)).fetchone()
                if own and own[0] == phone:
                    conn.close()
                    respond(self, {'error': 'You cannot book your own profile.'}, 403)
                    return
            # Rate limit: block the same phone from booking the same provider
            # more than 3 times within 24 hours — prevents fake-booking spam
            if phone:
                recent = conn.execute(
                    "SELECT COUNT(*) FROM bookings WHERE provider_id=? AND client_phone=? AND created_at > datetime('now','-1 day')",
                    (pid, phone)).fetchone()[0]
                if recent >= 3:
                    conn.close()
                    respond(self, {'error': 'You have already made 3 bookings with this provider in the last 24 hours. Please wait before booking again.'}, 429)
                    return
            bid = str(uuid.uuid4())
            urgency = body.get('urgency', '')
            conn.execute(sql("INSERT INTO bookings (id,provider_id,client_name,client_phone,service,date,notes,urgency) VALUES (?,?,?,?,?,?,?,?)"), (bid, pid, body.get('client_name'), phone,
                          body.get('service'), body.get('date'), body.get('notes',''), urgency))
            prow = conn.execute(sql("SELECT * FROM providers WHERE id=?"), (pid,)).fetchone()
            if prow:
                p = dict(prow)
                # jobs_done is NOT incremented here — only when client submits a review
                if p.get('user_id'):
                    msg = f"📲 New booking from {body.get('client_name','a client')} for {body.get('service','')} on {body.get('date','')}."
                    conn.execute(sql("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)"), (str(uuid.uuid4()), p['user_id'], pid, 'booking', msg))
            # also notify admin about new booking
            admin = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
            if admin:
                amsg = f"📅 New booking: {body.get('client_name','Client')} booked {body.get('service','')} with {p.get('first_name','') if prow else ''} {p.get('last_name','') if prow else ''} on {body.get('date','')}."
                conn.execute(sql("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)"), (str(uuid.uuid4()), admin[0], pid, 'booking', amsg))
            conn.commit(); conn.close()
            respond(self, {'success':True,'booking_id':bid}); return

        if path == '/api/bookings/complete':
            bid = body.get('booking_id')
            conn = get_db()
            conn.execute(sql("UPDATE bookings SET status='completed' WHERE id=?"), (bid,))
            conn.commit(); conn.close()
            respond(self, {'success':True}); return

        if path == '/api/reviews':
            pid   = body.get('provider_id')
            name  = body.get('reviewer_name','Anonymous')
            phone = body.get('client_phone','').strip()
            stars = int(body.get('stars', 5))
            text  = body.get('text','').strip()
            if not pid or not text:
                respond(self, {'error':'provider_id and text required'}, 400); return
            if not phone:
                respond(self, {'error':'A phone number is required to verify your booking before leaving a review.'}, 400); return
            conn = get_db()
            # Block self-review: check if this phone belongs to the provider being reviewed
            provider_row = conn.execute(sql("SELECT phone, user_id FROM providers WHERE id=?"), (pid,)).fetchone()
            if provider_row:
                pr = dict(provider_row)
                # Block by phone match (works for logged-out providers too)
                if pr.get('phone') and pr['phone'].strip() == phone.strip():
                    conn.close(); respond(self, {'error':'You cannot review your own profile.'}, 403); return
                # Also block by token (logged-in provider)
                tu = get_token_user(self)
                if tu and tu['role']=='provider' and pr.get('user_id') == tu['id']:
                    conn.close(); respond(self, {'error':'You cannot review your own profile.'}, 403); return
            # Require a completed booking with this phone number for this provider
            # Also verify that the name matches what was used during booking
            booking = conn.execute(
                sql("SELECT id, client_name FROM bookings WHERE provider_id=? AND client_phone=? AND status='completed' ORDER BY created_at DESC LIMIT 1"),
                (pid, phone)).fetchone()
            if not booking:
                conn.close()
                respond(self, {'error':'Reviews can only be left after a completed booking. Make sure you use the same phone number you booked with.'}, 403)
                return
            booking = dict(booking)
            # Check name matches (case-insensitive, partial match OK)
            booking_name = (booking.get('client_name') or '').lower().strip()
            reviewer_name_lower = name.lower().strip()
            # Allow if either name contains the other (handles "Joseph" vs "joseph mwangi")
            if booking_name and reviewer_name_lower and \
               booking_name not in reviewer_name_lower and reviewer_name_lower not in booking_name:
                conn.close()
                respond(self, {'error': f'The name you entered doesn\'t match the name used when booking. Please use the same name you booked with.'}, 403)
                return

            # ── Suspicious pattern detection ──
            flags = []
            booking_row = conn.execute(
                "SELECT created_at FROM bookings WHERE provider_id=? AND client_phone=? AND status='completed' ORDER BY created_at DESC LIMIT 1",
                (pid, phone)).fetchone()
            if booking_row:
                created = conn.execute(sql("SELECT (strftime('%s','now') - strftime('%s',?)) as secs"), (booking_row[0],)).fetchone()[0]
                if created is not None and created < 600:
                    flags.append("Review submitted within 10 minutes of booking being marked completed")
            same_phone_count = conn.execute(
                "SELECT COUNT(DISTINCT provider_id) FROM bookings WHERE client_phone=? AND created_at > datetime('now','-1 day')",
                (phone,)).fetchone()[0]
            if same_phone_count and same_phone_count >= 3:
                flags.append(f"Phone booked {same_phone_count} different providers in last 24h")
            recent_5star = conn.execute(
                "SELECT COUNT(*) FROM reviews WHERE provider_id=? AND stars=5 AND created_at > datetime('now','-1 day')",
                (pid,)).fetchone()[0]
            if recent_5star and recent_5star >= 5:
                flags.append(f"Provider received {recent_5star+1} five-star reviews in last 24h")

            rid = str(uuid.uuid4())
            conn.execute(sql("INSERT INTO reviews (id,provider_id,reviewer_name,stars,text) VALUES (?,?,?,?,?)"), (rid, pid, name, stars, text))

            # Recalculate rating average
            rows = conn.execute(sql("SELECT stars FROM reviews WHERE provider_id=?"), (pid,)).fetchall()
            avg = round(sum(r[0] for r in rows)/len(rows), 1) if rows else 0

            # ── Dynamic trust score (max 98) ──
            # Starts at 75 on approval. Each review moves it:
            # 5★ → +2, 4★ → +1, 3★ → -1, 2★ → -3, 1★ → -5
            delta = {5:2, 4:1, 3:-1, 2:-3, 1:-5}.get(stars, 0)
            cur_ts = conn.execute(sql("SELECT trust_score FROM providers WHERE id=?"), (pid,)).fetchone()
            cur_ts = cur_ts[0] if cur_ts else 75
            new_ts = max(0, min(98, cur_ts + delta))

            # ── Reliability score = (accepted + completed) / total_received * 100 ──
            total_bk = conn.execute(sql("SELECT COUNT(*) FROM bookings WHERE provider_id=?"), (pid,)).fetchone()[0]
            accepted_bk = conn.execute(sql("SELECT COUNT(*) FROM bookings WHERE provider_id=? AND (accepted=1 OR status='completed')"), (pid,)).fetchone()[0]
            reliability = round((accepted_bk / total_bk * 100), 1) if total_bk > 0 else 0

            conn.execute(sql("UPDATE providers SET rating=?, review_count=?, trust_score=?, reliability=?, jobs_done=jobs_done+1 WHERE id=?"), (avg, len(rows), new_ts, reliability, pid))

            # Alert admin for low-star reviews
            if stars <= 2:
                admin = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
                if admin:
                    prov = conn.execute(sql("SELECT first_name,last_name FROM providers WHERE id=?"), (pid,)).fetchone()
                    pname = f"{prov[0]} {prov[1]}" if prov else "a provider"
                    conn.execute(sql("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)"), (str(uuid.uuid4()), admin[0], pid, 'flagged',
                                  f"⭐{stars} low rating for {pname} from {name} ({phone}). Immediate review recommended."))

            if flags:
                admin = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
                if admin:
                    prov = conn.execute(sql("SELECT first_name,last_name FROM providers WHERE id=?"), (pid,)).fetchone()
                    pname = f"{prov[0]} {prov[1]}" if prov else "a provider"
                    fmsg = f"🚩 Suspicious review for {pname}: " + "; ".join(flags)
                    conn.execute(sql("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)"), (str(uuid.uuid4()), admin[0], pid, 'flagged', fmsg))

            conn.commit(); conn.close()
            respond(self, {'success':True, 'new_rating': avg, 'review_count': len(rows), 'trust_score': new_ts}); return

        if path == '/api/notifications':
            uid  = body.get('user_id','admin-001')
            typ  = body.get('type','info')
            msg  = body.get('message','')
            pid  = body.get('provider_id','')
            conn = get_db()
            conn.execute(sql("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)"), (str(uuid.uuid4()), uid, pid, typ, msg))
            conn.commit(); conn.close()
            respond(self, {'success':True}); return

        if path == '/api/notifications/read':
            nid = body.get('notification_id')
            conn = get_db()
            conn.execute(sql("UPDATE notifications SET read=1 WHERE id=?"), (nid,))
            conn.commit(); conn.close()
            respond(self, {'success':True}); return

        respond(self, {'error':'not found'}, 404)

    def do_PUT(self):
        path  = urllib.parse.urlparse(self.path).path
        body  = read_body(self)
        parts = [p for p in path.split('/') if p]
        if len(parts)==3 and parts[0]=='api' and parts[1]=='providers':
            pid = parts[2]
            conn = get_db()
            conn.execute(sql("""UPDATE providers SET
                first_name=?,last_name=?,phone=?,location=?,bio=?,
                services=?,profile_photo=?,work_photos=? WHERE id=?"""),
                (body.get('first_name'), body.get('last_name'),
                 body.get('phone'), body.get('location'), body.get('bio'),
                 json.dumps(body.get('services',[])),
                 body.get('profile_photo',''),
                 json.dumps(body.get('work_photos',[])), pid))
            conn.commit(); conn.close()
            respond(self, {'success':True}); return
        respond(self, {'error':'not found'}, 404)

if __name__ == '__main__':
    init_db()

    # Self-ping every 10 minutes to prevent Render free tier from sleeping
    SITE_URL = os.environ.get('RENDER_EXTERNAL_URL', f'http://localhost:{PORT}')
    def self_ping():
        time.sleep(60)  # wait 1 min after startup before first ping
        while True:
            try:
                import urllib.request as _ur
                _ur.urlopen(SITE_URL + '/', timeout=10)
                print(f"[ping] Self-ping OK → {SITE_URL}")
            except Exception as e:
                print(f"[ping] Self-ping failed: {e}")
            time.sleep(600)  # every 10 minutes
    threading.Thread(target=self_ping, daemon=True).start()

    # Background thread: alert admin when accepted bookings are ongoing > 24 hours
    def check_stale_services():
        time.sleep(120)  # wait 2 min after startup
        while True:
            try:
                conn = get_db()
                stale = conn.execute(
                    "SELECT b.id, b.provider_id, b.client_name, b.service, b.client_phone, b.created_at,"
                    " p.first_name, p.last_name"
                    " FROM bookings b LEFT JOIN providers p ON p.id=b.provider_id"
                    " WHERE b.accepted=1 AND b.status='pending'"
                    " AND (strftime('%s','now') - strftime('%s', b.created_at)) > 86400"
                ).fetchall()
                for row in stale:
                    r = dict(row)
                    # Only alert once — check if already alerted
                    existing = conn.execute(
                        "SELECT id FROM notifications WHERE type='stale_service' AND message LIKE ?",
                        (f"%{r['id']}%",)
                    ).fetchone()
                    if not existing:
                        msg = (f"⏰ Ongoing service alert: {r.get('first_name','')} {r.get('last_name','')} "
                               f"has an accepted booking from {r['client_name']} ({r['service']}) "
                               f"that has been ongoing for over 24 hours. Booking ID: {r['id']}")
                        conn.execute(
                            "INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)",
                            (str(uuid.uuid4()), 'admin-001', r['provider_id'], 'stale_service', msg)
                        )
                conn.commit(); conn.close()
            except Exception as e:
                print(f"[stale-check] Error: {e}")
            time.sleep(3600)  # check every hour
    threading.Thread(target=check_stale_services, daemon=True).start()

    # Background thread: alert admin when accepted bookings stay ongoing > 24 hours
    def check_ongoing_services():
        time.sleep(300)  # wait 5 min after startup
        while True:
            try:
                conn = get_db()
                import datetime
                cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ')
                # Find accepted bookings older than 24hrs still pending completion
                old_ongoing = conn.execute(
                    "SELECT b.id, b.client_name, b.service, b.created_at, p.first_name, p.last_name "
                    "FROM bookings b LEFT JOIN providers p ON p.id=b.provider_id "
                    "WHERE b.accepted=1 AND b.status='pending' AND b.created_at < ?",
                    (cutoff,)).fetchall()
                admin = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
                for row in old_ongoing:
                    r = dict(row)
                    # Check if we already sent an alert for this booking
                    already = conn.execute(
                        "SELECT id FROM notifications WHERE message LIKE ? AND type='info'",
                        (f'%overdue%{r["id"][:8]}%',)).fetchone()
                    if not already and admin:
                        conn.execute(
                            "INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)",
                            (str(uuid.uuid4()), admin[0], '', 'info',
                             f'⏰ Overdue service [{r["id"][:8]}]: {r["first_name"]} {r["last_name"]} — "{r["service"]}" for {r["client_name"]} has been ongoing for over 24 hours without completion.'))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f'[ongoing-check] Error: {e}')
            time.sleep(3600)  # check every hour

    threading.Thread(target=check_ongoing_services, daemon=True).start()

    # ThreadingHTTPServer handles multiple requests concurrently —
    # essential once more than one person uses the site at the same time
    server = http.server.ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    server.daemon_threads = True
    print(f"\n{'='*52}", flush=True)
    print(f"  Trusty-Ka — Backend running", flush=True)
    print(f"  Open: http://localhost:{PORT}", flush=True)
    print(f"  Admin: michaelvincentnyak@gmail.com / Michael 009", flush=True)
    print(f"{'='*52}\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
