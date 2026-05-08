╔══════════════════════════════════════════════════════════╗
║          TRUSTEDSERVICE KENYA — COMPLETE APP            ║
╚══════════════════════════════════════════════════════════╝

FILES
─────
  server.py      → Python backend (API + serves website)
  index.html     → Full frontend (auto-served by backend)
  README.txt     → This file

HOW TO RUN
──────────
1. Make sure Python 3 is installed (check: python3 --version)
2. Open a terminal in this folder
3. Run:   python3 server.py
4. Open your browser at:   http://localhost:8000

That's it! The website opens automatically.

ADMIN LOGIN
───────────
  Email:    michaelvincentnyak@gmail.com
  Password: Michael 009

HOW IT WORKS (end-to-end)
──────────────────────────
  1. A provider fills "Join as Provider" form
     → Stored in SQLite database (auto-created)
     → Admin gets a notification instantly

  2. Admin logs in → goes to Verification Queue
     → Clicks Approve or Reject
     → Provider gets a notification on their dashboard

  3. Approved provider logs in (email + password they set)
     → Can edit bio, photos, services & pricing
     → Sees booking requests and notifications

  4. Client finds a provider → clicks Book Now
     → Fills in name, phone, date, service
     → Provider gets a live booking notification

BACK BUTTON
───────────
  The browser back/forward buttons work fully throughout
  the app — navigate naturally on any device.

FREE PUBLIC LINK (no card needed)
──────────────────────────────────
  Option 1 — Netlify Drop (easiest, 10 seconds):
    a. Go to https://app.netlify.com/drop
    b. Drag the FOLDER onto the page
    c. Get URL like https://trusted-service.netlify.app

  Option 2 — Railway (for full backend, free):
    a. Create account at https://railway.app
    b. Upload this folder
    c. Set start command: python3 server.py
    d. Get URL like https://trustedservice.up.railway.app

DATABASE
────────
  trustedservice.db is created automatically on first run.
  All providers, bookings, users & notifications are stored here.
  Delete it to start fresh.

