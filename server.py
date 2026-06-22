#!/usr/bin/env python3
"""
TrustedService Kenya — Backend Server
Run:  python3 server.py
Open: http://localhost:8000
"""
import http.server, json, sqlite3, hashlib, uuid, os, base64, urllib.parse

# Use Render's persistent disk if mounted at /data, otherwise fall back to local folder
# (so this still works for local testing on your own machine)
_DATA_DIR = '/data' if os.path.isdir('/data') else os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_DATA_DIR, 'trustedservice.db')
PORT = int(os.environ.get('PORT', 8000))

def get_db():
    # timeout lets SQLite wait briefly for a lock instead of immediately failing
    # under concurrent requests from multiple threads
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # allows concurrent reads while writing
    return conn

def init_db():
    conn = get_db(); c = conn.cursor()
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
        services_json TEXT DEFAULT '[]',
        id_number TEXT, status TEXT DEFAULT 'pending',
        trust_score INTEGER DEFAULT 0, rating REAL DEFAULT 0.0,
        review_count INTEGER DEFAULT 0, jobs_done INTEGER DEFAULT 0,
        profile_photo TEXT DEFAULT '', work_photos_json TEXT DEFAULT '[]',
        ref_code TEXT,
        submitted_at TEXT DEFAULT (datetime('now')), approved_at TEXT
    );
    CREATE TABLE IF NOT EXISTS bookings (
        id TEXT PRIMARY KEY, provider_id TEXT,
        client_name TEXT, client_phone TEXT,
        service TEXT, date TEXT, notes TEXT,
        status TEXT DEFAULT 'pending',
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
    c.execute("INSERT OR IGNORE INTO users (id,email,password_hash,role,name) VALUES (?,?,?,?,?)",
              ('admin-001','michaelvincentnyak@gmail.com',admin_h,'admin','Michael Vincent'))
    conn.commit(); conn.close()
    print(f"[DB] Ready: {DB_PATH}")

def hp(pw): return hashlib.sha256(pw.encode()).hexdigest()
def mk_token(uid,role,email): return base64.b64encode(f"{uid}:{role}:{email}".encode()).decode()
def get_token_user(handler):
    auth = handler.headers.get('Authorization','')
    if not auth.startswith('Bearer '): return None
    try:
        p = base64.b64decode(auth[7:]).decode().split(':',2)
        return {'id':p[0],'role':p[1],'email':p[2]}
    except: return None

def respond(handler, data, status=200):
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

        if path == '/api/providers':
            conn = get_db()
            cat = qs.get('cat',[''])[0]
            q   = qs.get('q',[''])[0].lower()
            sql = "SELECT * FROM providers WHERE status='approved'"
            args = []
            if cat and cat != 'All':
                sql += " AND category=?"; args.append(cat)
            # Rank by actual client satisfaction first (rating x review_count),
            # falling back to trust_score only as a tiebreaker for providers
            # who have not yet received any reviews.
            sql += " ORDER BY (rating * review_count) DESC, review_count DESC, trust_score DESC"
            rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
            if q:
                rows = [r for r in rows if q in (r.get('first_name','')+' '+r.get('last_name','')+' '+r.get('category','')+' '+r.get('bio','')).lower()]
            for r in rows:
                r['services'] = json.loads(r.get('services_json','[]'))
            conn.close()
            respond(self, rows); return

        if path == '/api/auth/me':
            tu = get_token_user(self)
            if not tu: respond(self, {'error':'not authenticated'}, 401); return
            conn = get_db()
            user = conn.execute("SELECT id,email,role,name FROM users WHERE id=?",(tu['id'],)).fetchone()
            if not user: respond(self, {'error':'user not found'}, 404); conn.close(); return
            u = dict(user)
            if u['role'] == 'removed_provider':
                conn.close(); respond(self, {'error':'Account removed'}, 403); return
            provider = None
            if u['role']=='provider':
                prow = conn.execute("SELECT * FROM providers WHERE user_id=?",(u['id'],)).fetchone()
                if prow:
                    provider = dict(prow)
                    provider['services'] = json.loads(provider.get('services_json','[]'))
                    provider['work_photos'] = json.loads(provider.get('work_photos_json','[]'))
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
            for r in rows: r['services'] = json.loads(r.get('services_json','[]'))
            conn.close(); respond(self, rows); return

        if path == '/api/admin/bookings':
            conn = get_db()
            rows = conn.execute("""SELECT b.*, p.first_name, p.last_name FROM bookings b
                                    LEFT JOIN providers p ON p.id=b.provider_id
                                    ORDER BY b.created_at DESC""").fetchall()
            conn.close()
            respond(self, [dict(r) for r in rows]); return

        if path == '/api/admin/providers':
            conn = get_db()
            rows = [dict(r) for r in conn.execute("SELECT * FROM providers ORDER BY submitted_at DESC").fetchall()]
            for r in rows: r['services'] = json.loads(r.get('services_json','[]'))
            conn.close(); respond(self, rows); return

        # /api/providers/by-user/{user_id} — fetch provider by user account (for login refresh)
        if len(parts)==4 and parts[0]=='api' and parts[1]=='providers' and parts[2]=='by-user':
            uid = parts[3]
            conn = get_db()
            row = conn.execute("SELECT * FROM providers WHERE user_id=?", (uid,)).fetchone()
            if not row: respond(self,{'error':'not found'},404); conn.close(); return
            p = dict(row)
            p['services'] = json.loads(p.get('services_json','[]'))
            p['work_photos'] = json.loads(p.get('work_photos_json','[]'))
            conn.close(); respond(self, p); return

        if len(parts)==3 and parts[0]=='api' and parts[1]=='providers':
            pid = parts[2]
            conn = get_db()
            row = conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
            if not row: respond(self,{'error':'not found'},404); conn.close(); return
            p = dict(row)
            p['services'] = json.loads(p.get('services_json','[]'))
            p['work_photos'] = json.loads(p.get('work_photos_json','[]'))
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

        if path == '/api/auth/login':
            email = body.get('email','').strip().lower()
            pw    = body.get('password','')
            conn  = get_db()
            user  = conn.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
            if not user or dict(user)['password_hash'] != hp(pw):
                respond(self,{'error':'Invalid email or password'},401); conn.close(); return
            u = dict(user)
            if u['role'] == 'removed_provider':
                conn.close()
                respond(self, {'error':'This account has been removed from the platform by the administrator. Contact support for more information.'}, 403)
                return
            provider = None
            if u['role'] == 'provider':
                prow = conn.execute("SELECT * FROM providers WHERE user_id=?",(u['id'],)).fetchone()
                if prow:
                    provider = dict(prow)
                    provider['services'] = json.loads(provider.get('services_json','[]'))
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
            if conn.execute("SELECT id FROM users WHERE email=?",(email,)).fetchone():
                respond(self,{'error':'Email already registered'},409); conn.close(); return
            uid = str(uuid.uuid4())
            conn.execute("INSERT INTO users (id,email,password_hash,role,name) VALUES (?,?,?,?,?)",
                         (uid,email,hp(pw),'client',name))
            conn.commit(); conn.close()
            respond(self, {'token': mk_token(uid,'client',email), 'user':{'id':uid,'email':email,'role':'client','name':name}})
            return

        if path == '/api/providers/register':
            email = body.get('email','').strip().lower()
            pw    = body.get('password','')
            conn  = get_db()
            user_id = None
            if email and pw:
                if conn.execute("SELECT id FROM users WHERE email=?",(email,)).fetchone():
                    respond(self,{'error':'Email already registered. Please log in.'},409); conn.close(); return
                user_id = str(uuid.uuid4())
                fname = body.get('first_name','')
                lname = body.get('last_name','')
                conn.execute("INSERT INTO users (id,email,password_hash,role,name) VALUES (?,?,?,?,?)",
                             (user_id,email,hp(pw),'provider',f"{fname} {lname}"))
            pid = str(uuid.uuid4())
            ref = 'TS-'+str(uuid.uuid4())[:8].upper()
            conn.execute("""INSERT INTO providers
                (id,user_id,first_name,last_name,phone,email,location,category,bio,services_json,id_number,status,ref_code)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, user_id,
                 body.get('first_name',''), body.get('last_name',''),
                 body.get('phone',''), email,
                 body.get('location',''), body.get('category',''),
                 body.get('bio',''), json.dumps(body.get('services',[])),
                 body.get('id_number',''), 'pending', ref))
            nid = str(uuid.uuid4())
            conn.execute("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)",
                         (nid,'admin-001',pid,'new_registration',
                          f"🆕 New provider application from {body.get('first_name','')} {body.get('last_name','')} · {body.get('category','')} · {body.get('location','')}. Go to Verification Queue to review."))
            conn.commit(); conn.close()
            token = mk_token(user_id,'provider',email) if user_id else None
            respond(self, {'success':True,'ref':ref,'provider_id':pid,'token':token})
            return

        if path == '/api/admin/approve':
            pid = body.get('provider_id')
            conn = get_db()
            conn.execute("UPDATE providers SET status='approved', approved_at=datetime('now'), trust_score=75 WHERE id=?",(pid,))
            prow = conn.execute("SELECT * FROM providers WHERE id=?",(pid,)).fetchone()
            if prow:
                p = dict(prow)
                if p.get('user_id'):
                    conn.execute("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)",
                                 (str(uuid.uuid4()), p['user_id'], pid, 'approved',
                                  "🎉 Your provider profile has been approved! Log in to manage your profile and start receiving bookings."))
            conn.commit(); conn.close()
            respond(self, {'success':True}); return

        if path == '/api/admin/reject':
            pid    = body.get('provider_id')
            reason = body.get('reason','Your application did not meet our requirements.')
            conn   = get_db()
            conn.execute("UPDATE providers SET status='rejected' WHERE id=?",(pid,))
            prow   = conn.execute("SELECT * FROM providers WHERE id=?",(pid,)).fetchone()
            if prow:
                p = dict(prow)
                if p.get('user_id'):
                    conn.execute("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)",
                                 (str(uuid.uuid4()), p['user_id'], pid, 'rejected',
                                  f"Your application was not approved. Reason: {reason}"))
            conn.commit(); conn.close()
            respond(self, {'success':True}); return

        # Admin removes a provider entirely from the platform (revokes access + delists profile)
        if path == '/api/admin/remove':
            pid    = body.get('provider_id')
            reason = body.get('reason','Your provider account has been removed from the platform by the administrator.')
            conn   = get_db()
            prow   = conn.execute("SELECT * FROM providers WHERE id=?",(pid,)).fetchone()
            if not prow:
                conn.close(); respond(self, {'error':'Provider not found'}, 404); return
            p = dict(prow)
            # Notify the provider's user account before removing access
            if p.get('user_id'):
                conn.execute("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)",
                             (str(uuid.uuid4()), p['user_id'], pid, 'removed', f"⚠️ {reason}"))
                # Revoke login by switching role so they can no longer access the provider dashboard
                conn.execute("UPDATE users SET role='removed_provider' WHERE id=?", (p['user_id'],))
            # Delist the profile and mark status
            conn.execute("UPDATE providers SET status='removed' WHERE id=?", (pid,))
            conn.commit(); conn.close()
            respond(self, {'success':True}); return

        if path == '/api/bookings':
            pid = body.get('provider_id')
            conn = get_db()
            bid = str(uuid.uuid4())
            conn.execute("INSERT INTO bookings (id,provider_id,client_name,client_phone,service,date,notes) VALUES (?,?,?,?,?,?,?)",
                         (bid, pid, body.get('client_name'), body.get('client_phone'),
                          body.get('service'), body.get('date'), body.get('notes','')))
            prow = conn.execute("SELECT * FROM providers WHERE id=?",(pid,)).fetchone()
            if prow:
                p = dict(prow)
                conn.execute("UPDATE providers SET jobs_done=jobs_done+1 WHERE id=?",(pid,))
                if p.get('user_id'):
                    msg = f"📲 New booking from {body.get('client_name','a client')} for {body.get('service','')} on {body.get('date','')}."
                    conn.execute("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)",
                                 (str(uuid.uuid4()), p['user_id'], pid, 'booking', msg))
            # also notify admin about new booking
            admin = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
            if admin:
                amsg = f"📅 New booking: {body.get('client_name','Client')} booked {body.get('service','')} with {p.get('first_name','') if prow else ''} {p.get('last_name','') if prow else ''} on {body.get('date','')}."
                conn.execute("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)",
                             (str(uuid.uuid4()), admin[0], pid, 'booking', amsg))
            conn.commit(); conn.close()
            respond(self, {'success':True,'booking_id':bid}); return

        if path == '/api/bookings/complete':
            bid = body.get('booking_id')
            conn = get_db()
            conn.execute("UPDATE bookings SET status='completed' WHERE id=?",(bid,))
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
            # Block providers from reviewing their own profile
            tu = get_token_user(self)
            if tu and tu['role']=='provider':
                own = conn.execute("SELECT id FROM providers WHERE id=? AND user_id=?",(pid, tu['id'])).fetchone()
                if own:
                    conn.close(); respond(self, {'error':'You cannot review your own profile.'}, 403); return
            # Require a completed booking with this phone number for this provider
            booking = conn.execute(
                "SELECT id FROM bookings WHERE provider_id=? AND client_phone=? AND status='completed' LIMIT 1",
                (pid, phone)).fetchone()
            if not booking:
                conn.close()
                respond(self, {'error':'Reviews can only be left after a completed booking with this provider. If your service is done, ask the provider to mark your booking as completed.'}, 403)
                return

            # ── Suspicious pattern detection — flag for admin review, never block the user ──
            flags = []
            booking_row = conn.execute(
                "SELECT created_at FROM bookings WHERE provider_id=? AND client_phone=? AND status='completed' ORDER BY created_at DESC LIMIT 1",
                (pid, phone)).fetchone()
            if booking_row:
                created = conn.execute("SELECT (strftime('%s','now') - strftime('%s',?)) as secs", (booking_row[0],)).fetchone()[0]
                if created is not None and created < 600:  # booking marked completed less than 10 min ago
                    flags.append("Review submitted within 10 minutes of booking being marked completed")
            same_phone_count = conn.execute(
                "SELECT COUNT(DISTINCT provider_id) FROM bookings WHERE client_phone=? AND created_at > datetime('now','-1 day')",
                (phone,)).fetchone()[0]
            if same_phone_count and same_phone_count >= 3:
                flags.append(f"This phone number has booked {same_phone_count} different providers in the last 24 hours")
            recent_5star = conn.execute(
                "SELECT COUNT(*) FROM reviews WHERE provider_id=? AND stars=5 AND created_at > datetime('now','-1 day')",
                (pid,)).fetchone()[0]
            if recent_5star and recent_5star >= 5:
                flags.append(f"This provider has received {recent_5star+1} five-star reviews in the last 24 hours")

            rid = str(uuid.uuid4())
            conn.execute("INSERT INTO reviews (id,provider_id,reviewer_name,stars,text) VALUES (?,?,?,?,?)",
                         (rid, pid, name, stars, text))
            # recalculate rating average
            rows = conn.execute("SELECT stars FROM reviews WHERE provider_id=?",(pid,)).fetchall()
            avg = round(sum(r[0] for r in rows)/len(rows), 1) if rows else 0
            conn.execute("UPDATE providers SET rating=?, review_count=? WHERE id=?",(avg, len(rows), pid))

            if flags:
                admin = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
                if admin:
                    prov = conn.execute("SELECT first_name,last_name FROM providers WHERE id=?",(pid,)).fetchone()
                    pname = f"{prov[0]} {prov[1]}" if prov else "a provider"
                    fmsg = f"🚩 Suspicious review activity for {pname}: " + "; ".join(flags) + ". Please review manually."
                    conn.execute("INSERT INTO notifications (id,user_id,provider_id,type,message) VALUES (?,?,?,?,?)",
                                 (str(uuid.uuid4()), admin[0], pid, 'flagged', fmsg))

            conn.commit(); conn.close()
            respond(self, {'success':True, 'new_rating': avg, 'review_count': len(rows)}); return

        if path == '/api/notifications/read':
            nid = body.get('notification_id')
            conn = get_db()
            conn.execute("UPDATE notifications SET read=1 WHERE id=?",(nid,))
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
            conn.execute("""UPDATE providers SET
                first_name=?,last_name=?,phone=?,location=?,bio=?,
                services_json=?,profile_photo=?,work_photos_json=? WHERE id=?""",
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
    # ThreadingHTTPServer handles multiple requests concurrently —
    # essential once more than one person uses the site at the same time
    server = http.server.ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    server.daemon_threads = True
    print(f"\n{'='*52}")
    print(f"  TrustedService Kenya — Backend running")
    print(f"  Open: http://localhost:{PORT}")
    print(f"  Admin: michaelvincentnyak@gmail.com / Michael 009")
    print(f"{'='*52}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
