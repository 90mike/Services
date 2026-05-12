#!/usr/bin/env python3
"""
TrustedService Kenya — Backend Server
Run:  python3 server.py
Open: http://localhost:8000
"""
import http.server, json, sqlite3, hashlib, uuid, os, base64, urllib.parse

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trustedservice.db')
PORT = int(os.environ.get("PORT", 8000))

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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

        if path == '/api/providers':
            conn = get_db()
            cat = qs.get('cat',[''])[0]
            q   = qs.get('q',[''])[0].lower()
            sql = "SELECT * FROM providers WHERE status='approved'"
            args = []
            if cat and cat != 'All':
                sql += " AND category=?"; args.append(cat)
            sql += " ORDER BY trust_score DESC"
            rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
            if q:
                rows = [r for r in rows if q in (r.get('first_name','')+' '+r.get('last_name','')+' '+r.get('category','')+' '+r.get('bio','')).lower()]
            for r in rows:
                r['services'] = json.loads(r.get('services_json','[]'))
            conn.close()
            respond(self, rows); return

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

        if path == '/api/admin/providers':
            conn = get_db()
            rows = [dict(r) for r in conn.execute("SELECT * FROM providers ORDER BY submitted_at DESC").fetchall()]
            for r in rows: r['services'] = json.loads(r.get('services_json','[]'))
            conn.close(); respond(self, rows); return

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
                          f"New application from {body.get('first_name','')} {body.get('last_name','')} — {body.get('category','')} in {body.get('location','')}"))
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
            conn.commit(); conn.close()
            respond(self, {'success':True,'booking_id':bid}); return

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
    server = http.server.HTTPServer(('0.0.0.0', PORT), Handler)
    print(f"\n{'='*52}")
    print(f"  TrustedService Kenya — Backend running")
    print(f"  Open: http://localhost:{PORT}")
    print(f"  Admin: michaelvincentnyak@gmail.com / Michael 009")
    print(f"{'='*52}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
