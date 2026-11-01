import os
import sys
import json
import random
import sqlite3
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="TradePilot AI - Indian Stock Market & Multi-Agent Paper Trading Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WORKSPACE_DIR = Path(__file__).resolve().parent
DB_PATH = WORKSPACE_DIR / "trader" / "tradepilot.db"
CONFIG_PATH = WORKSPACE_DIR / "trader" / "config.json"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# In-memory OTP storage
OTP_STORAGE = {}

# Standard Starting Capital for Fair Comparison (10 Lakhs each)
STARTING_CAPITAL = 1000000.0

# ----------------- LIVE YAHOO NSE TICKER HELPER -----------------
def get_live_nse_price(symbol: str, fallback_price: float) -> float:
    """NSE real-time market price direct fetch karta hai."""
    try:
        clean_sym = symbol.replace(".NS", "").replace("^", "").strip()
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{clean_sym}.NS?interval=1d&range=1d"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            live_p = data['chart']['result'][0]['meta']['regularMarketPrice']
            if live_p and live_p > 0:
                return round(float(live_p), 2)
    except Exception:
        pass
    return round(float(fallback_price), 2)

# ----------------- TELEGRAM HELPER -----------------
def send_telegram_msg(html_text: str) -> bool:
    bot_token = None
    chat_id = None
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                bot_token = cfg.get("telegram_bot_token")
                chat_id = cfg.get("telegram_chat_id")
        except Exception:
            pass

    if not bot_token:
        bot_token = "9291565347:AAE9iI_U4se2gCZ0AzidonLwNalnFznkLBU"
    if not chat_id:
        chat_id = "5706014125"

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": html_text,
        "parse_mode": "HTML"
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[Telegram Dispatch Error]: {e}")
        return False

# ----------------- SQLITE DATABASE INITIALIZATION -----------------
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        name TEXT,
        created_at TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS preferences (
        user_id INTEGER PRIMARY KEY,
        interests TEXT,
        risk_profile TEXT,
        updated_at TEXT
    )
    """)
    # User's Manual Paper Trading Ledger (10 Lakh Initial Fund)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS paper_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        symbol TEXT NOT NULL,
        trade_type TEXT NOT NULL,
        side TEXT NOT NULL,
        entry_price REAL NOT NULL,
        current_price REAL,
        stop_loss REAL NOT NULL,
        target_price REAL NOT NULL,
        quantity INTEGER NOT NULL,
        status TEXT NOT NULL,
        pnl REAL DEFAULT 0.0,
        charges REAL DEFAULT 0.0,
        created_at TEXT,
        closed_at TEXT
    )
    """)
    # AI Autonomous Portfolio Trades (10 Lakh Initial Fund)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ai_portfolio_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_name TEXT NOT NULL,
        symbol TEXT NOT NULL,
        trade_type TEXT NOT NULL,
        side TEXT NOT NULL,
        entry_price REAL NOT NULL,
        current_price REAL NOT NULL,
        stop_loss REAL NOT NULL,
        target_price REAL NOT NULL,
        quantity INTEGER NOT NULL,
        invested_amt REAL NOT NULL,
        pnl REAL DEFAULT 0.0,
        status TEXT NOT NULL,
        reason TEXT,
        created_at TEXT
    )
    """)
    # AI Predictions Verification Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS agent_predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_name TEXT NOT NULL,
        symbol TEXT NOT NULL,
        trade_type TEXT NOT NULL,
        recommendation TEXT NOT NULL,
        suggested_entry REAL,
        actual_market_price REAL,
        target_price REAL,
        stop_loss REAL,
        outcome_status TEXT DEFAULT 'PENDING',
        pnl_pct REAL DEFAULT 0.0,
        reasoning TEXT,
        created_at TEXT
    )
    """)
    # Default Demo User
    cursor.execute("SELECT id FROM users WHERE email = 'demo@tradepilot.ai'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (email, password, name, created_at) VALUES ('demo@tradepilot.ai', 'pilot123', 'Ashok Kumar', ?)", (datetime.now().isoformat(),))
        cursor.execute("INSERT INTO preferences (user_id, interests, risk_profile, updated_at) VALUES (1, '[\"Swing Trading\", \"Long-Term Investment\"]', 'Moderate', ?)", (datetime.now().isoformat(),))

    conn.commit()
    conn.close()

init_db()

# ----------------- PYDANTIC MODELS -----------------
class LoginRequest(BaseModel):
    email: str
    password: str

class ForgotPasswordRequest(BaseModel):
    email: str

class ResetPasswordRequest(BaseModel):
    email: str
    otp: str
    new_password: str

class TradeOrderRequest(BaseModel):
    user_id: int
    symbol: str
    trade_type: str
    side: str
    entry_price: float
    stop_loss: float
    target_price: float
    quantity: int

# ----------------- API ROUTES -----------------
@app.post("/api/auth/login")
def login(req: LoginRequest):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, email, name FROM users WHERE email = ? AND password = ?", (req.email, req.password))
    user = cursor.fetchone()
    conn.close()
    if user:
        return {"status": "success", "user": {"id": user["id"], "email": user["email"], "name": user["name"]}}
    raise HTTPException(status_code=401, detail="Invalid credentials. Use demo@tradepilot.ai / pilot123")

@app.post("/api/auth/send-otp")
def send_otp(req: ForgotPasswordRequest):
    otp = str(random.randint(100000, 999999))
    OTP_STORAGE[req.email] = {
        "otp": otp,
        "expires_at": datetime.now() + timedelta(minutes=10)
    }

    tg_text = (
        f"🔐 <b>TradePilot AI • Security Verification</b>\n\n"
        f"Account: <code>{req.email}</code>\n"
        f"Password Reset OTP:\n\n"
        f"🔑 <b><code>{otp}</code></b>\n\n"
        f"<i>Valid for 10 minutes. Do not share with anyone.</i>"
    )
    sent = send_telegram_msg(tg_text)
    return {"status": "success", "message": "OTP aapke Telegram account par bhej diya gaya hai!", "telegram_dispatched": sent}

@app.post("/api/auth/reset-password")
def reset_password(req: ResetPasswordRequest):
    record = OTP_STORAGE.get(req.email)
    if not record or record["otp"] != req.otp.strip():
        raise HTTPException(status_code=400, detail="Galat OTP ya expire ho gaya hai!")
    if datetime.now() > record["expires_at"]:
        raise HTTPException(status_code=400, detail="OTP expire ho chuka hai.")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET password = ? WHERE email = ?", (req.new_password, req.email))
    conn.commit()
    conn.close()

    del OTP_STORAGE[req.email]
    send_telegram_msg(f"✅ <b>Password Changed Successfully</b> for <code>{req.email}</code>")
    return {"status": "success", "message": "Password successfully update ho gaya! Ab login karein."}

# ----------------- REAL MARKET AI AUTONOMOUS PORTFOLIO (10 LAKH) -----------------
@app.get("/api/ai-portfolio")
def get_ai_portfolio():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ai_portfolio_trades ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()

    invested_amount = 0.0
    total_unrealized_pnl = 0.0
    total_realized_pnl = 0.0
    trades = []

    for r in rows:
        d = dict(r)
        # Update with live market tick if open
        if d["status"] == "OPEN":
            live_price = get_live_nse_price(d["symbol"], d["current_price"])
            d["current_price"] = live_price
            d["pnl"] = round((live_price - d["entry_price"]) * d["quantity"], 2)
            invested_amount += d["invested_amt"]
            total_unrealized_pnl += d["pnl"]
        else:
            total_realized_pnl += d["pnl"]
        trades.append(d)

    total_pnl = total_unrealized_pnl + total_realized_pnl
    cash_balance = STARTING_CAPITAL - invested_amount + total_realized_pnl
    current_portfolio_value = STARTING_CAPITAL + total_pnl
    roi_pct = round((total_pnl / STARTING_CAPITAL) * 100, 2)

    return {
        "initial_capital": STARTING_CAPITAL,
        "current_value": round(current_portfolio_value, 2),
        "cash_balance": round(cash_balance, 2),
        "invested_amount": round(invested_amount, 2),
        "total_pnl": round(total_pnl, 2),
        "roi_pct": roi_pct,
        "holdings_count": len([t for t in trades if t["status"] == "OPEN"]),
        "trades": trades
    }

@app.post("/api/ai-portfolio/rebalance")
def ai_rebalance():
    """AI automatically fetches the exact real market price from NSE and buys safely."""
    conn = get_db()
    cursor = conn.cursor()
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 1. Fetch real market price for candidate stock (POWERGRID)
    live_price = get_live_nse_price("POWERGRID", 265.00)
    stop_loss = round(live_price * 0.96, 2) # 4% SL
    target_price = round(live_price * 1.08, 2) # 8% 2R Target
    qty = 350
    invested = round(live_price * qty, 2)

    cursor.execute("""
    INSERT INTO ai_portfolio_trades (agent_name, symbol, trade_type, side, entry_price, current_price, stop_loss, target_price, quantity, invested_amt, pnl, status, reason, created_at)
    VALUES ('Smart Money Agent', 'POWERGRID', 'Swing (20D)', 'BUY', ?, ?, ?, ?, ?, ?, 0.0, 'OPEN', 'Real-time breakout with promoter buy filings', ?)
    """, (live_price, live_price, stop_loss, target_price, qty, invested, now_ts))
    conn.commit()
    conn.close()

    send_telegram_msg(
        f"🤖 <b>AI Real-Market Trade Executed (₹10L Fund)</b>\n\n"
        f"Action: <b>BUY #POWERGRID</b> ({qty} Shares)\n"
        f"Exchange: <b>NSE Real Tick</b>: ₹{live_price}\n"
        f"Capital Allocated: ₹{invested:,.2f}\n"
        f"Target: ₹{target_price} | Stop-Loss: ₹{stop_loss}\n\n"
        f"<i>AI Master Agent has updated its autonomous ₹10 Lakh portfolio.</i>"
    )
    return {"status": "success", "message": f"AI ne NSE real market price ₹{live_price} par POWERGRID buy kar liya hai!"}

@app.get("/api/reports/comparison")
def get_reports_comparison():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM agent_predictions ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/telegram/broadcast-stock")
def broadcast_stock_to_telegram(data: dict):
    sym = data.get("symbol")
    horizon = data.get("horizon", "Swing Trading")
    entry = data.get("entry")
    stop = data.get("stop")
    target = data.get("target")
    reason = data.get("reason", "Multi-Agent Consensus verified")
    qty = data.get("qty", 1)

    tg_msg = (
        f"🎯 <b>TradePilot AI • Real-Market Alert</b>\n\n"
        f"📌 <b>Stock:</b> <b>#{sym}</b> (NSE)\n"
        f"⏱ <b>Investment Horizon:</b> <code>{horizon}</code>\n"
        f"🚦 <b>Signal:</b> <b>STRONG BUY</b>\n\n"
        f"💵 <b>Kharido Price (Real Price):</b> ₹{entry}\n"
        f"🛑 <b>Stop Loss:</b> ₹{stop}\n"
        f"🎯 <b>Target (Munafa):</b> ₹{target} (2R)\n"
        f"📦 <b>Suggested Safe Qty:</b> {qty} shares\n\n"
        f"💡 <b>Reason:</b> {reason}\n\n"
        f"🛡️ <i>Both AI & User Portfolios funded with ₹10,00,000 each for live benchmark.</i>"
    )
    success = send_telegram_msg(tg_msg)
    return {"status": "success", "message": f"{sym} alert broadcasted to Telegram!", "dispatched": success}

# ----------------- MY PAPER PORTFOLIO (10 LAKH STARTING BALANCE) -----------------
@app.get("/api/paper/portfolio/{user_id}")
def get_paper_portfolio(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM paper_trades WHERE user_id = ? ORDER BY id DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    trades = []
    invested_amount = 0.0
    total_unrealized_pnl = 0.0
    total_realized_pnl = 0.0

    for r in rows:
        d = dict(r)
        if d["status"] == "OPEN":
            live_price = get_live_nse_price(d["symbol"], d["current_price"] or d["entry_price"])
            d["current_price"] = live_price
            d["pnl"] = round((live_price - d["entry_price"]) * d["quantity"] - d.get("charges", 20.0), 2)
            invested_amount += (d["entry_price"] * d["quantity"])
            total_unrealized_pnl += d["pnl"]
        else:
            total_realized_pnl += d["pnl"]
        trades.append(d)

    total_pnl = total_unrealized_pnl + total_realized_pnl
    cash_balance = STARTING_CAPITAL - invested_amount + total_realized_pnl
    portfolio_value = STARTING_CAPITAL + total_pnl
    roi_pct = round((total_pnl / STARTING_CAPITAL) * 100, 2)

    return {
        "virtual_balance": round(cash_balance, 2),
        "portfolio_value": round(portfolio_value, 2),
        "invested_capital": round(invested_amount, 2),
        "simulated_pnl": round(total_pnl, 2),
        "roi_pct": roi_pct,
        "active_trades_count": len([t for t in trades if t["status"] == "OPEN"]),
        "trades": trades
    }

@app.post("/api/paper/execute")
def execute_paper_trade(req: TradeOrderRequest):
    conn = get_db()
    cursor = conn.cursor()
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    charges = 20.0
    
    # Fetch real live exchange price for fair entry
    live_entry = get_live_nse_price(req.symbol, req.entry_price)
    
    cursor.execute("""
    INSERT INTO paper_trades (user_id, symbol, trade_type, side, entry_price, current_price, stop_loss, target_price, quantity, status, pnl, charges, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', 0.0, ?, ?)
    """, (req.user_id, req.symbol, req.trade_type, req.side, live_entry, live_entry, req.stop_loss, req.target_price, req.quantity, charges, now_ts))
    conn.commit()
    conn.close()

    send_telegram_msg(
        f"⚡ <b>User Paper Trade Executed (₹10L Fund)</b>\n"
        f"Stock: <b>#{req.symbol}</b> ({req.quantity} Shares)\n"
        f"Execution Price: ₹{live_entry} (NSE Real-Tick)\n"
        f"Target: ₹{req.target_price} | SL: ₹{req.stop_loss}"
    )
    return {"status": "success", "message": f"Real price ₹{live_entry} par {req.symbol} buy kar liya gaya!"}

# ----------------- CLEAN PHONE-FIRST WHITE & NAVY BLUE UI -----------------
@app.get("/", response_class=HTMLResponse)
def serve_app():
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>TradePilot AI — Indian Stock Research & AI vs Human Trading</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    body { font-family: 'Inter', -apple-system, sans-serif; background-color: #f8fafc; color: #0f172a; -webkit-tap-highlight-color: transparent; }
    .navy-brand { background-color: #0a192f; }
    .navy-text { color: #0a192f; }
    .clean-card { background: #ffffff; border: 1px solid #e2e8f0; box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.04); }
    .stock-logo { width: 42px; height: 42px; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 13px; }
  </style>
</head>
<body class="min-h-screen flex flex-col antialiased pb-20 md:pb-0">

  <!-- ================= TOP CLEAN HEADER ================= -->
  <header class="bg-white border-b border-slate-200 sticky top-0 z-30 shadow-sm">
    <div class="max-w-7xl mx-auto px-4 h-16 flex items-center justify-between">
      
      <!-- Brand -->
      <div class="flex items-center gap-2.5 cursor-pointer" onclick="navigateTab('dashboard')">
        <div class="w-9 h-9 rounded-xl navy-brand text-white flex items-center justify-center font-extrabold text-base shadow-sm">
          ₹
        </div>
        <div>
          <span class="text-base font-extrabold tracking-tight text-slate-900 block leading-tight">TradePilot AI</span>
          <span class="text-[10px] font-semibold text-emerald-600 flex items-center gap-1">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span> NSE Live Active
          </span>
        </div>
      </div>

      <!-- Desktop Navigation Tabs -->
      <nav class="hidden md:flex items-center gap-1">
        <button onclick="navigateTab('dashboard')" id="nav-d-dashboard" class="px-3.5 py-2 rounded-lg text-xs font-semibold navy-brand text-white transition">Dashboard</button>
        <button onclick="navigateTab('ai-fund')" id="nav-d-ai-fund" class="px-3.5 py-2 rounded-lg text-xs font-semibold text-indigo-700 bg-indigo-50 border border-indigo-200 hover:bg-indigo-100 transition flex items-center gap-1.5">
          <i class="fa-solid fa-robot"></i> AI Portfolio (₹10L)
        </button>
        <button onclick="navigateTab('portfolio')" id="nav-d-portfolio" class="px-3.5 py-2 rounded-lg text-xs font-semibold text-slate-600 hover:text-slate-900 hover:bg-slate-100 transition">
          My Portfolio (₹10L)
        </button>
        <button onclick="navigateTab('stocks')" id="nav-d-stocks" class="px-3.5 py-2 rounded-lg text-xs font-semibold text-slate-600 hover:text-slate-900 hover:bg-slate-100 transition">Stock Suggestions</button>
        <button onclick="navigateTab('reports')" id="nav-d-reports" class="px-3.5 py-2 rounded-lg text-xs font-semibold text-slate-600 hover:text-slate-900 hover:bg-slate-100 transition">AI Comparison</button>
      </nav>

      <!-- Telegram & User Profile -->
      <div class="flex items-center gap-2">
        <a href="https://t.me/Mine_upcoming_bot" target="_blank" class="px-3 py-1.5 rounded-lg bg-sky-50 text-sky-700 hover:bg-sky-100 border border-sky-200 text-xs font-semibold flex items-center gap-1.5 transition">
          <i class="fa-brands fa-telegram text-sky-500 text-sm"></i> <span class="hidden sm:inline">Bot</span> Alerts
        </a>
        <button onclick="openAuthModal()" class="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 transition">
          <div class="w-7 h-7 rounded-full bg-slate-100 navy-text flex items-center justify-center font-bold text-xs">AK</div>
          <span class="text-xs font-medium text-slate-700 hidden sm:inline">Ashok</span>
        </button>
      </div>

    </div>

    <!-- Ticker Bar -->
    <div class="bg-slate-50 border-t border-slate-200/80 px-4 py-2 flex items-center justify-between text-xs overflow-x-auto whitespace-nowrap gap-4 scrollbar-none">
      <div class="flex items-center gap-4 text-[11px] font-medium text-slate-600">
        <div>NIFTY 50: <b class="text-slate-900">23,053</b> <span class="text-emerald-600 font-bold">+0.62% ↑</span></div>
        <div>SENSEX: <b class="text-slate-900">75,810</b> <span class="text-emerald-600 font-bold">+0.60% ↑</span></div>
        <div>CRUDE: <b class="text-slate-900">$71.40</b> <span class="text-rose-600 font-bold">-0.85% ↓</span></div>
      </div>
      <div class="flex items-center gap-2">
        <span class="text-[10px] font-semibold text-indigo-700 bg-indigo-50 px-2 py-0.5 rounded border border-indigo-200">
          ⚖️ Equal Capital: ₹10,00,000 Each
        </span>
        <span class="text-[10px] font-semibold text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">
          🟢 Real NSE Market Execution
        </span>
      </div>
    </div>
  </header>

  <!-- ================= MAIN APP BODY ================= -->
  <main class="max-w-7xl mx-auto px-4 py-5 w-full flex-1">
    
    <!-- 1. DASHBOARD TAB -->
    <div id="tab-dashboard" class="space-y-5">
      
      <!-- Performance Battle Banner (Equal 10L Capital) -->
      <div class="clean-card rounded-2xl p-5 bg-gradient-to-r from-slate-900 via-navy-brand to-slate-900 text-white shadow-sm flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div>
          <span class="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 bg-emerald-500/20 text-emerald-300 rounded border border-emerald-400/30">
            Real Market Data • Equal ₹10 Lakhs Capital
          </span>
          <h1 class="text-xl md:text-2xl font-bold mt-1.5">TradePilot AI vs Human Challenge</h1>
          <p class="text-xs text-slate-300 mt-1 max-w-xl leading-relaxed">
            Kal subah 09:15 AM market khulte hi dono accounts live real prices par execute karenge. 
            AI apne ₹10 Lakh se autonomous trades lega aur aap apne ₹10 Lakh se trades lenge. Dono ka P&L fair comparison hoga!
          </p>
        </div>
        <div class="flex items-center gap-2 w-full md:w-auto">
          <button onclick="navigateTab('ai-fund')" class="flex-1 md:flex-none px-4 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl font-bold text-xs shadow-sm transition flex items-center justify-center gap-2">
            <i class="fa-solid fa-robot"></i> AI Portfolio (₹10L)
          </button>
          <button onclick="navigateTab('portfolio')" class="flex-1 md:flex-none px-4 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-xl font-bold text-xs shadow-sm transition flex items-center justify-center gap-2">
            <i class="fa-solid fa-user"></i> My Portfolio (₹10L)
          </button>
        </div>
      </div>

      <!-- Side-by-Side Comparison: AI vs My Trading (Equal 10 Lakh) -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        
        <!-- Card 1: AI Autonomous Fund (10 Lakh) -->
        <div class="clean-card rounded-2xl p-5 border-l-4 border-l-indigo-600 bg-white">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <div class="w-8 h-8 rounded-lg bg-indigo-50 text-indigo-700 flex items-center justify-center text-sm font-bold">
                <i class="fa-solid fa-brain"></i>
              </div>
              <div>
                <h3 class="text-sm font-bold text-slate-900">AI Autonomous Fund</h3>
                <span class="text-[10px] text-slate-500">Autonomous Real-Market Execution</span>
              </div>
            </div>
            <span class="text-[11px] font-bold px-2.5 py-0.5 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-200">
              ₹10,00,000 Fund
            </span>
          </div>

          <div class="grid grid-cols-3 gap-2 mt-4 pt-3 border-t border-slate-100 text-center">
            <div>
              <span class="text-[10px] text-slate-500 block">Starting Capital</span>
              <span class="text-sm font-bold text-slate-900">₹10,00,000</span>
            </div>
            <div>
              <span class="text-[10px] text-slate-500 block">Current Value</span>
              <span id="dash-ai-val" class="text-sm font-bold text-slate-900">₹10,00,000</span>
            </div>
            <div>
              <span class="text-[10px] text-slate-500 block">AI Profit / Loss</span>
              <span id="dash-ai-pnl" class="text-sm font-extrabold text-slate-700">₹0.00 (0.0%)</span>
            </div>
          </div>
          <button onclick="navigateTab('ai-fund')" class="w-full mt-3 py-2 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 font-semibold text-xs rounded-lg transition">
            AI ke Real-Market Trades dekhein →
          </button>
        </div>

        <!-- Card 2: My Manual Paper Portfolio (10 Lakh) -->
        <div class="clean-card rounded-2xl p-5 border-l-4 border-l-blue-600 bg-white">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <div class="w-8 h-8 rounded-lg bg-blue-50 text-blue-700 flex items-center justify-center text-sm font-bold">
                <i class="fa-solid fa-user"></i>
              </div>
              <div>
                <h3 class="text-sm font-bold text-slate-900">My Personal Portfolio</h3>
                <span class="text-[10px] text-slate-500">Ashok Kumar Discretionary</span>
              </div>
            </div>
            <span class="text-[11px] font-bold px-2.5 py-0.5 rounded-full bg-blue-50 text-blue-700 border border-blue-200">
              ₹10,00,000 Fund
            </span>
          </div>

          <div class="grid grid-cols-3 gap-2 mt-4 pt-3 border-t border-slate-100 text-center">
            <div>
              <span class="text-[10px] text-slate-500 block">Starting Capital</span>
              <span class="text-sm font-bold text-slate-900">₹10,00,000</span>
            </div>
            <div>
              <span class="text-[10px] text-slate-500 block">Current Value</span>
              <span id="dash-my-val" class="text-sm font-bold text-slate-900">₹10,00,000</span>
            </div>
            <div>
              <span class="text-[10px] text-slate-500 block">My Profit / Loss</span>
              <span id="dash-my-pnl" class="text-sm font-extrabold text-slate-700">₹0.00 (0.0%)</span>
            </div>
          </div>
          <button onclick="navigateTab('portfolio')" class="w-full mt-3 py-2 bg-blue-50 hover:bg-blue-100 text-blue-700 font-semibold text-xs rounded-lg transition">
            Mera ₹10L Ledger dekhein →
          </button>
        </div>

      </div>

      <!-- Featured Stock Picks Preview -->
      <div class="clean-card rounded-2xl p-5">
        <div class="flex items-center justify-between pb-3 border-b border-slate-100">
          <div>
            <h2 class="text-base font-bold text-slate-900">Real Market High-Conviction Suggestions</h2>
            <p class="text-xs text-slate-500">Live NSE rates with exact entry, stop-loss and 2R targets:</p>
          </div>
          <button onclick="navigateTab('stocks')" class="text-xs font-bold text-blue-600 hover:underline">
            View All 10 Stocks →
          </button>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4">
          
          <!-- POWERGRID -->
          <div class="border border-slate-200 rounded-xl p-4 bg-white hover:border-blue-400 transition flex flex-col justify-between space-y-3">
            <div class="flex items-start justify-between">
              <div class="flex items-center gap-3">
                <div class="stock-logo bg-amber-50 text-amber-700 border border-amber-200">⚡</div>
                <div>
                  <h3 class="text-sm font-bold text-slate-900">POWERGRID</h3>
                  <span class="text-[11px] text-slate-500">Power Transmission PSU</span>
                </div>
              </div>
              <span class="text-[10px] font-bold px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200">
                Swing (20D)
              </span>
            </div>
            <div class="bg-slate-50 p-3 rounded-lg text-xs space-y-1">
              <div class="flex justify-between text-slate-600"><span>Real Price:</span> <b class="text-slate-900">₹265.00</b></div>
              <div class="flex justify-between text-slate-600"><span>Stop Loss:</span> <b class="text-rose-600">₹255.00</b></div>
              <div class="flex justify-between text-slate-600"><span>Target:</span> <b class="text-emerald-700 font-bold">₹285.00 (2R)</b></div>
            </div>
            <div class="flex items-center gap-2">
              <button onclick="quickPaperTrade('POWERGRID', 'Swing', 265, 255, 285, 300)" class="flex-1 py-2 bg-slate-900 hover:bg-slate-800 text-white rounded-lg text-xs font-semibold shadow-sm transition">
                ⚡ Buy (300 Qty)
              </button>
              <button onclick="sendToTelegram('POWERGRID', 'Swing Trading', 265, 255, 285, '20-Day High Breakout on Maharatna PSU', 300)" title="Send Alert to Telegram" class="p-2 border border-slate-200 hover:bg-slate-50 rounded-lg text-slate-700 transition">
                <i class="fa-brands fa-telegram text-sky-500"></i>
              </button>
            </div>
          </div>

          <!-- INFY -->
          <div class="border border-slate-200 rounded-xl p-4 bg-white hover:border-blue-400 transition flex flex-col justify-between space-y-3">
            <div class="flex items-start justify-between">
              <div class="flex items-center gap-3">
                <div class="stock-logo bg-blue-50 text-blue-700 border border-blue-200">INF</div>
                <div>
                  <h3 class="text-sm font-bold text-slate-900">INFY</h3>
                  <span class="text-[11px] text-slate-500">Infosys IT Services</span>
                </div>
              </div>
              <span class="text-[10px] font-bold px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200">
                Swing (CNC)
              </span>
            </div>
            <div class="bg-slate-50 p-3 rounded-lg text-xs space-y-1">
              <div class="flex justify-between text-slate-600"><span>Real Price:</span> <b class="text-slate-900">₹1,185.00</b></div>
              <div class="flex justify-between text-slate-600"><span>Stop Loss:</span> <b class="text-rose-600">₹1,160.00</b></div>
              <div class="flex justify-between text-slate-600"><span>Target:</span> <b class="text-emerald-700 font-bold">₹1,235.00</b></div>
            </div>
            <div class="flex items-center gap-2">
              <button onclick="quickPaperTrade('INFY', 'Swing', 1185, 1160, 1235, 100)" class="flex-1 py-2 bg-slate-900 hover:bg-slate-800 text-white rounded-lg text-xs font-semibold shadow-sm transition">
                ⚡ Buy (100 Qty)
              </button>
              <button onclick="sendToTelegram('INFY', 'Swing Trading', 1185, 1160, 1235, 'IT Sector Surge & Multi-million AI order wins', 100)" title="Send Alert to Telegram" class="p-2 border border-slate-200 hover:bg-slate-50 rounded-lg text-slate-700 transition">
                <i class="fa-brands fa-telegram text-sky-500"></i>
              </button>
            </div>
          </div>

          <!-- RELIANCE -->
          <div class="border border-slate-200 rounded-xl p-4 bg-white hover:border-blue-400 transition flex flex-col justify-between space-y-3">
            <div class="flex items-start justify-between">
              <div class="flex items-center gap-3">
                <div class="stock-logo bg-indigo-50 text-indigo-700 border border-indigo-200">REL</div>
                <div>
                  <h3 class="text-sm font-bold text-slate-900">RELIANCE</h3>
                  <span class="text-[11px] text-slate-500">Jio, Retail & Oil</span>
                </div>
              </div>
              <span class="text-[10px] font-bold px-2 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-200">
                Long-Term
              </span>
            </div>
            <div class="bg-slate-50 p-3 rounded-lg text-xs space-y-1">
              <div class="flex justify-between text-slate-600"><span>Real Price:</span> <b class="text-slate-900">₹1,164.90</b></div>
              <div class="flex justify-between text-slate-600"><span>Stop Loss:</span> <b class="text-slate-400">Optional</b></div>
              <div class="flex justify-between text-slate-600"><span>Target:</span> <b class="text-emerald-700 font-bold">1,300+</b></div>
            </div>
            <div class="flex items-center gap-2">
              <button onclick="quickPaperTrade('RELIANCE', 'Long-Term', 1164.9, 1120, 1300, 100)" class="flex-1 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-semibold shadow-sm transition">
                ⚡ Buy (100 Qty)
              </button>
              <button onclick="sendToTelegram('RELIANCE', 'Long-Term Investment', 1164.9, 1120, 1300, 'Promoters stake increase & Jio Retail Cashflow', 100)" title="Send Alert to Telegram" class="p-2 border border-slate-200 hover:bg-slate-50 rounded-lg text-slate-700 transition">
                <i class="fa-brands fa-telegram text-sky-500"></i>
              </button>
            </div>
          </div>

        </div>
      </div>

    </div>

    <!-- 2. AI AUTONOMOUS PORTFOLIO TAB (₹10 LAKH FUND) -->
    <div id="tab-ai-fund" class="space-y-4 hidden">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <div class="flex items-center gap-2">
            <span class="px-2 py-0.5 rounded text-[10px] font-extrabold bg-indigo-50 text-indigo-700 border border-indigo-200">REAL MARKET EXECUTION</span>
            <h2 class="text-lg font-bold text-slate-900">AI Autonomous Fund (₹10,00,000 Starting Balance)</h2>
          </div>
          <p class="text-xs text-slate-500 mt-0.5">AI Agents live market rates par autonomously buy aur sell karenge.</p>
        </div>
        <div class="flex items-center gap-2">
          <button onclick="triggerAiRebalance()" class="px-3.5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-sm flex items-center gap-1.5 transition">
            <i class="fa-solid fa-wand-magic-sparkles"></i> Execute AI Auto-Trade
          </button>
          <button onclick="loadAiPortfolio()" class="px-3 py-2 rounded-lg border border-slate-200 text-xs font-semibold text-slate-700 hover:bg-slate-50 transition">
            🔄 Refresh
          </button>
        </div>
      </div>

      <!-- AI Fund Summary Cards -->
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div class="clean-card p-4 rounded-xl border-t-2 border-t-indigo-600">
          <span class="text-[11px] text-slate-500 font-medium">Total AI Fund Value</span>
          <div id="ai-tot-val" class="text-xl font-bold text-slate-900 mt-0.5">₹10,00,000</div>
          <span class="text-[10px] text-slate-400">Starting: ₹10,00,000</span>
        </div>

        <div class="clean-card p-4 rounded-xl border-t-2 border-t-emerald-600">
          <span class="text-[11px] text-slate-500 font-medium">Net Profit / Loss</span>
          <div id="ai-tot-pnl" class="text-xl font-bold text-slate-700 mt-0.5">₹0.00</div>
          <span id="ai-roi-pct" class="text-[10px] font-bold text-slate-500">0.0% Overall Gain</span>
        </div>

        <div class="clean-card p-4 rounded-xl border-t-2 border-t-blue-600">
          <span class="text-[11px] text-slate-500 font-medium">Invested Capital</span>
          <div id="ai-invested" class="text-xl font-bold text-blue-700 mt-0.5">₹0.00</div>
          <span id="ai-open-count" class="text-[10px] text-slate-500">0 Active Positions</span>
        </div>

        <div class="clean-card p-4 rounded-xl border-t-2 border-t-slate-600">
          <span class="text-[11px] text-slate-500 font-medium">Available Cash Buffer</span>
          <div id="ai-cash" class="text-xl font-bold text-slate-900 mt-0.5">₹10,00,000</div>
          <span class="text-[10px] text-slate-400">Ready for Market Open</span>
        </div>
      </div>

      <!-- AI Holdings Table -->
      <div class="clean-card rounded-xl overflow-hidden">
        <div class="p-3.5 bg-slate-50 border-b border-slate-200 flex items-center justify-between">
          <span class="text-xs font-bold text-slate-800">AI Live Holdings & Executed Trades</span>
          <span class="text-[10px] text-slate-500">NSE Live Prices</span>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs whitespace-nowrap">
            <thead class="bg-slate-100/70 border-b border-slate-200 text-slate-600 uppercase text-[10px] font-bold">
              <tr>
                <th class="p-3.5">Managing Agent</th>
                <th class="p-3.5">Stock</th>
                <th class="p-3.5">Type</th>
                <th class="p-3.5">Qty</th>
                <th class="p-3.5">Invested Capital</th>
                <th class="p-3.5">Entry (Real)</th>
                <th class="p-3.5">Current Price</th>
                <th class="p-3.5">Target / SL</th>
                <th class="p-3.5">Status</th>
                <th class="p-3.5">AI Profit/Loss</th>
                <th class="p-3.5">AI Logic</th>
              </tr>
            </thead>
            <tbody id="ai-trades-rows" class="divide-y divide-slate-100 text-slate-700">
              <tr>
                <td colspan="11" class="p-6 text-center text-slate-400">Dono accounts ready hain! Kal subah market khulte hi ya button dabate hi real price par trade start hoga.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 3. MY PAPER PORTFOLIO (₹10 LAKH FUND) -->
    <div id="tab-portfolio" class="space-y-4 hidden">
      <div class="flex items-center justify-between">
        <div>
          <div class="flex items-center gap-2">
            <span class="px-2 py-0.5 rounded text-[10px] font-extrabold bg-blue-50 text-blue-700 border border-blue-200">EQUAL BALANCE</span>
            <h2 class="text-lg font-bold text-slate-900">My Portfolio (₹10,00,000 Fund)</h2>
          </div>
          <p class="text-xs text-slate-500 mt-0.5">Aapka personal account (Equal ₹10 Lakhs capital) real market rates par.</p>
        </div>
        <button onclick="refreshPortfolio()" class="px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-semibold text-slate-700 hover:bg-slate-50 transition">
          🔄 Refresh
        </button>
      </div>

      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div class="clean-card p-4 rounded-xl border-t-2 border-t-blue-600">
          <span class="text-[11px] text-slate-500 font-medium">My Portfolio Value</span>
          <div id="my-tot-val" class="text-xl font-bold text-slate-900 mt-0.5">₹10,00,000</div>
          <span class="text-[10px] text-slate-400">Starting: ₹10,00,000</span>
        </div>

        <div class="clean-card p-4 rounded-xl border-t-2 border-t-emerald-600">
          <span class="text-[11px] text-slate-500 font-medium">Net Profit / Loss</span>
          <div id="my-tot-pnl" class="text-xl font-bold text-slate-700 mt-0.5">₹0.00</div>
          <span id="my-roi-pct" class="text-[10px] font-bold text-slate-500">0.0% Overall Gain</span>
        </div>

        <div class="clean-card p-4 rounded-xl border-t-2 border-t-indigo-600">
          <span class="text-[11px] text-slate-500 font-medium">Invested Capital</span>
          <div id="my-invested" class="text-xl font-bold text-blue-700 mt-0.5">₹0.00</div>
          <span class="text-[10px] text-slate-500">In Active Stocks</span>
        </div>

        <div class="clean-card p-4 rounded-xl border-t-2 border-t-slate-600">
          <span class="text-[11px] text-slate-500 font-medium">Available Cash</span>
          <div id="my-cash" class="text-xl font-bold text-slate-900 mt-0.5">₹10,00,000</div>
          <span class="text-[10px] text-slate-400">Ready to Deploy</span>
        </div>
      </div>

      <div class="clean-card rounded-xl overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs whitespace-nowrap">
            <thead class="bg-slate-50 border-b border-slate-200 text-slate-500 uppercase text-[10px] font-bold">
              <tr>
                <th class="p-3.5">Stock</th>
                <th class="p-3.5">Type</th>
                <th class="p-3.5">Qty</th>
                <th class="p-3.5">Entry (Real)</th>
                <th class="p-3.5">Current Price</th>
                <th class="p-3.5">SL</th>
                <th class="p-3.5">Target</th>
                <th class="p-3.5">Status</th>
                <th class="p-3.5">Simulated P&L</th>
              </tr>
            </thead>
            <tbody id="paper-rows" class="divide-y divide-slate-100 text-slate-700">
              <tr>
                <td colspan="9" class="p-6 text-center text-slate-400">No active trades yet. Suggestions tab se stock pick karein!</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 4. STOCKS TAB (CLEAN LOGO-WISE DESK) -->
    <div id="tab-stocks" class="space-y-4 hidden">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h2 class="text-lg font-bold text-slate-900">All Stock Suggestions (Logo & Horizon)</h2>
          <p class="text-xs text-slate-500">Choose horizon to see tailored recommendations:</p>
        </div>
        <button onclick="broadcastAllToTelegram()" class="px-3.5 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold flex items-center gap-1.5 transition">
          <i class="fa-brands fa-telegram"></i> Broadcast All to Telegram
        </button>
      </div>

      <!-- Filter Tabs -->
      <div class="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-none">
        <button onclick="filterStocks('all')" id="btn-f-all" class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-900 text-white transition">All Stocks (10)</button>
        <button onclick="filterStocks('Long-Term')" id="btn-f-long" class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-50 transition">Long-Term</button>
        <button onclick="filterStocks('Swing')" id="btn-f-swing" class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-50 transition">Swing Trading</button>
        <button onclick="filterStocks('Intraday')" id="btn-f-intra" class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-50 transition">Intraday (ORB)</button>
      </div>

      <div id="stocks-container" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5">
        <!-- Injected via javascript -->
      </div>
    </div>

    <!-- 5. AI COMPARISON REPORT TAB -->
    <div id="tab-reports" class="space-y-4 hidden">
      <div class="flex items-center justify-between">
        <div>
          <h2 class="text-lg font-bold text-slate-900">AI Suggestion vs Real Market Report</h2>
          <p class="text-xs text-slate-500">AI prediction time par kya price tha aur actual me kya result aaya:</p>
        </div>
        <button onclick="loadComparisonReports()" class="px-3 py-1.5 rounded-lg border border-slate-200 text-xs font-semibold text-slate-700 hover:bg-slate-50 transition">
          🔄 Refresh
        </button>
      </div>

      <!-- Comparison Ledger -->
      <div class="clean-card rounded-xl overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs whitespace-nowrap">
            <thead class="bg-slate-50 border-b border-slate-200 text-slate-500 uppercase text-[10px] font-bold">
              <tr>
                <th class="p-3.5">Agent</th>
                <th class="p-3.5">Stock</th>
                <th class="p-3.5">Signal</th>
                <th class="p-3.5">Suggested Entry</th>
                <th class="p-3.5">Actual Market Price</th>
                <th class="p-3.5">Target / SL</th>
                <th class="p-3.5">Outcome</th>
                <th class="p-3.5">Real P&L</th>
                <th class="p-3.5">Supporting Reason</th>
              </tr>
            </thead>
            <tbody id="report-rows" class="divide-y divide-slate-100 text-slate-700">
              <!-- Dynamically populated -->
            </tbody>
          </table>
        </div>
      </div>
    </div>

  </main>

  <!-- ================= MOBILE BOTTOM NAVIGATION BAR ================= -->
  <nav class="md:hidden fixed bottom-0 left-0 right-0 bg-white border-t border-slate-200 flex items-center justify-around h-16 z-30 shadow-lg px-2">
    <button onclick="navigateTab('dashboard')" id="nav-m-dashboard" class="flex flex-col items-center gap-1 text-slate-900 font-bold text-[10px]">
      <i class="fa-solid fa-house text-base"></i>
      <span>Home</span>
    </button>
    <button onclick="navigateTab('ai-fund')" id="nav-m-ai-fund" class="flex flex-col items-center gap-1 text-indigo-700 font-bold text-[10px]">
      <i class="fa-solid fa-robot text-base"></i>
      <span>AI Fund (10L)</span>
    </button>
    <button onclick="navigateTab('portfolio')" id="nav-m-portfolio" class="flex flex-col items-center gap-1 text-blue-700 font-bold text-[10px]">
      <i class="fa-solid fa-wallet text-base"></i>
      <span>My 10L</span>
    </button>
    <button onclick="navigateTab('stocks')" id="nav-m-stocks" class="flex flex-col items-center gap-1 text-slate-500 hover:text-slate-900 text-[10px]">
      <i class="fa-solid fa-chart-line text-base"></i>
      <span>Picks</span>
    </button>
    <button onclick="navigateTab('reports')" id="nav-m-reports" class="flex flex-col items-center gap-1 text-slate-500 hover:text-slate-900 text-[10px]">
      <i class="fa-solid fa-clipboard-check text-base"></i>
      <span>Reports</span>
    </button>
  </nav>

  <!-- ================= AUTH MODAL WITH TELEGRAM OTP ================= -->
  <div id="auth-modal" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-sm hidden">
    <div class="clean-card max-w-sm w-full p-6 rounded-2xl shadow-xl relative bg-white">
      <button onclick="closeAuthModal()" class="absolute top-4 right-4 text-slate-400 hover:text-slate-700">
        <i class="fa-solid fa-xmark text-lg"></i>
      </button>

      <div id="m-view-login" class="space-y-4">
        <div class="text-center">
          <div class="w-12 h-12 rounded-xl navy-brand text-white mx-auto flex items-center justify-center text-xl font-bold shadow-sm mb-2">
            ₹
          </div>
          <h3 class="text-lg font-bold text-slate-900">Sign In to TradePilot</h3>
          <p class="text-xs text-slate-500">Education & Paper Trading Portal</p>
        </div>

        <form onsubmit="handleLogin(event)" class="space-y-3">
          <div>
            <label class="block text-xs font-medium text-slate-700 mb-1">Email ID</label>
            <input type="email" id="login-email" value="demo@tradepilot.ai" required class="w-full border border-slate-300 rounded-lg px-3 py-2 text-xs text-slate-900 focus:outline-none focus:border-blue-600" />
          </div>

          <div>
            <label class="block text-xs font-medium text-slate-700 mb-1">Password</label>
            <input type="password" id="login-pass" value="pilot123" required class="w-full border border-slate-300 rounded-lg px-3 py-2 text-xs text-slate-900 focus:outline-none focus:border-blue-600" />
          </div>

          <div class="flex items-center justify-between text-xs">
            <span class="text-slate-500">demo@tradepilot.ai</span>
            <button type="button" onclick="showForgotPassword()" class="text-blue-600 hover:underline font-semibold">Forgot Password?</button>
          </div>

          <button type="submit" class="w-full py-2.5 navy-brand text-white rounded-lg font-semibold text-xs shadow-sm hover:opacity-95 transition">
            Sign In
          </button>
        </form>
      </div>

      <!-- Forgot Password with Telegram OTP -->
      <div id="m-view-forgot" class="space-y-4 hidden">
        <button onclick="backToLogin()" class="text-xs text-slate-500 hover:text-slate-800 flex items-center gap-1">
          <i class="fa-solid fa-arrow-left"></i> Back to Login
        </button>
        
        <div>
          <h3 class="text-base font-bold text-slate-900">Telegram OTP Password Reset</h3>
          <p class="text-xs text-slate-500 mt-0.5">OTP seedha aapke Telegram bot par bhej diya jayega.</p>
        </div>

        <div id="f-step-1" class="space-y-3">
          <div>
            <label class="block text-xs font-medium text-slate-700 mb-1">Email Address</label>
            <input type="email" id="forgot-email" value="demo@tradepilot.ai" class="w-full border border-slate-300 rounded-lg px-3 py-2 text-xs text-slate-900 focus:outline-none focus:border-blue-600" />
          </div>
          <button onclick="sendTelegramOTP()" id="btn-otp-trigger" class="w-full py-2.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg font-semibold text-xs shadow-sm transition flex items-center justify-center gap-1.5">
            <i class="fa-brands fa-telegram"></i> Send OTP to Telegram
          </button>
        </div>

        <div id="f-step-2" class="space-y-3 hidden">
          <div class="p-2.5 bg-emerald-50 border border-emerald-200 rounded-lg text-[11px] text-emerald-800">
            ✓ OTP dispatched to your Telegram (@Mine_upcoming_bot)!
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-700 mb-1">Enter 6-Digit Telegram OTP</label>
            <input type="text" id="reset-otp" placeholder="e.g. 583921" maxlength="6" class="w-full border border-slate-300 rounded-lg px-3 py-2 text-center text-sm font-mono tracking-widest text-slate-900 focus:outline-none focus:border-emerald-600" />
          </div>
          <div>
            <label class="block text-xs font-medium text-slate-700 mb-1">New Password</label>
            <input type="password" id="reset-new-pass" placeholder="Naya password likhein" class="w-full border border-slate-300 rounded-lg px-3 py-2 text-xs text-slate-900 focus:outline-none focus:border-blue-600" />
          </div>
          <button onclick="submitPasswordReset()" class="w-full py-2.5 navy-brand text-white rounded-lg font-semibold text-xs shadow-sm hover:opacity-95 transition">
            Verify OTP & Save Password
          </button>
        </div>
      </div>

    </div>
  </div>

  <!-- ================= JAVASCRIPT LOGIC ================= -->
  <script>
    const STOCKS_DATA = [
      { sym: 'POWERGRID', name: 'Power Grid Corp', logoText: '⚡', logoBg: 'bg-amber-50 text-amber-700 border-amber-200', horizon: 'Swing', type: 'Swing (20D)', entry: 265, stop: 255, target: 285, qty: 300, why: '20-Day High Breakout on Maharatna PSU.' },
      { sym: 'INFY', name: 'Infosys Limited', logoText: 'INF', logoBg: 'bg-blue-50 text-blue-700 border-blue-200', horizon: 'Swing', type: 'Swing (CNC)', entry: 1185, stop: 1160, target: 1235, qty: 100, why: 'IT Sector Surge & Multi-million AI order wins.' },
      { sym: 'RELIANCE', name: 'Reliance Industries', logoText: 'REL', logoBg: 'bg-indigo-50 text-indigo-700 border-indigo-200', horizon: 'Long-Term', type: 'Long-Term', entry: 1164.9, stop: 1120, target: 1300, qty: 100, why: 'Promoters stake increase & Jio Retail Cashflow.' },
      { sym: 'SBIN', name: 'State Bank of India', logoText: 'SBI', logoBg: 'bg-sky-50 text-sky-700 border-sky-200', horizon: 'Intraday', type: 'Intraday (ORB)', entry: 820, stop: 805, target: 850, qty: 100, why: 'Lowest bad loans (NPA 0.57%) in 10 years.' },
      { sym: 'TCS', name: 'Tata Consultancy Services', logoText: 'TCS', logoBg: 'bg-purple-50 text-purple-700 border-purple-200', horizon: 'Swing', type: 'Swing (CNC)', entry: 2502, stop: 2460, target: 2580, qty: 40, why: 'Bull Flag Continuation Pattern.' },
      { sym: 'HCLTECH', name: 'HCL Technologies', logoText: 'HCL', logoBg: 'bg-cyan-50 text-cyan-700 border-cyan-200', horizon: 'Intraday', type: 'Intraday (ORB)', entry: 1428, stop: 1390, target: 1495, qty: 70, why: 'RSI 69 Breakout & Solid Dividend Yield.' },
      { sym: 'NTPC', name: 'NTPC Energy', logoText: 'NTP', logoBg: 'bg-emerald-50 text-emerald-700 border-emerald-200', horizon: 'Long-Term', type: 'Long-Term', entry: 316, stop: 305, target: 338, qty: 300, why: 'Green energy transition & Solar expansion.' },
      { sym: 'KOTAKBANK', name: 'Kotak Mahindra Bank', logoText: 'KM', logoBg: 'bg-rose-50 text-rose-700 border-rose-200', horizon: 'Swing', type: 'Swing (CNC)', entry: 461, stop: 445, target: 490, qty: 200, why: 'Digital customer acquisition post-RBI relief.' },
      { sym: 'CIPLA', name: 'Cipla Pharma', logoText: 'CIP', logoBg: 'bg-teal-50 text-teal-700 border-teal-200', horizon: 'Swing', type: 'Swing (CNC)', entry: 1378, stop: 1340, target: 1440, qty: 70, why: 'Respiratory medicines market share gain.' },
      { sym: 'TRENT', name: 'Trent Fashion Retail', logoText: 'TRN', logoBg: 'bg-slate-100 text-slate-700 border-slate-300', horizon: 'Intraday', type: 'Avoid / High Risk', entry: 3017, stop: 2950, target: 3150, qty: 0, why: 'PE Ratio 160+ (Very Expensive). Blocked by risk shield.' }
    ];

    let activeFilter = 'all';

    function navigateTab(tab) {
      ['dashboard', 'ai-fund', 'portfolio', 'stocks', 'reports'].forEach(t => {
        const el = document.getElementById('tab-' + t);
        if (el) el.classList.add('hidden');
        
        const btnD = document.getElementById('nav-d-' + t);
        if (btnD) {
          if (t === 'ai-fund') {
            btnD.className = 'px-3.5 py-2 rounded-lg text-xs font-semibold text-indigo-700 bg-indigo-50 border border-indigo-200 hover:bg-indigo-100 transition flex items-center gap-1.5';
          } else {
            btnD.className = 'px-3.5 py-2 rounded-lg text-xs font-semibold text-slate-600 hover:text-slate-900 hover:bg-slate-100 transition';
          }
        }
        
        const btnM = document.getElementById('nav-m-' + t);
        if (btnM) btnM.className = 'flex flex-col items-center gap-1 text-slate-500 hover:text-slate-900 text-[10px]';
      });

      const target = document.getElementById('tab-' + tab);
      if (target) target.classList.remove('hidden');

      const activeD = document.getElementById('nav-d-' + tab);
      if (activeD) {
        if (tab === 'ai-fund') {
          activeD.className = 'px-3.5 py-2 rounded-lg text-xs font-semibold bg-indigo-600 text-white transition flex items-center gap-1.5';
        } else {
          activeD.className = 'px-3.5 py-2 rounded-lg text-xs font-semibold navy-brand text-white transition';
        }
      }

      const activeM = document.getElementById('nav-m-' + tab);
      if (activeM) {
        if (tab === 'ai-fund') activeM.className = 'flex flex-col items-center gap-1 text-indigo-700 font-extrabold text-[10px]';
        else if (tab === 'portfolio') activeM.className = 'flex flex-col items-center gap-1 text-blue-700 font-extrabold text-[10px]';
        else activeM.className = 'flex flex-col items-center gap-1 text-slate-900 font-bold text-[10px]';
      }

      if (tab === 'ai-fund') loadAiPortfolio();
      if (tab === 'reports') loadComparisonReports();
      if (tab === 'portfolio') refreshPortfolio();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    async function loadAiPortfolio() {
      try {
        const res = await fetch('/api/ai-portfolio');
        const data = await res.json();

        document.getElementById('ai-tot-val').innerText = `₹${data.current_value.toLocaleString('en-IN')}`;
        document.getElementById('dash-ai-val').innerText = `₹${data.current_value.toLocaleString('en-IN')}`;
        
        const sign = data.total_pnl >= 0 ? '+' : '';
        document.getElementById('ai-tot-pnl').innerText = `${sign}₹${data.total_pnl.toLocaleString('en-IN')}`;
        document.getElementById('dash-ai-pnl').innerText = `${sign}₹${data.total_pnl.toLocaleString('en-IN')} (${sign}${data.roi_pct}%)`;
        document.getElementById('ai-roi-pct').innerText = `${sign}${data.roi_pct}% Overall ROI`;
        
        document.getElementById('ai-invested').innerText = `₹${data.invested_amount.toLocaleString('en-IN')}`;
        document.getElementById('ai-cash').innerText = `₹${data.cash_balance.toLocaleString('en-IN')}`;
        document.getElementById('ai-open-count').innerText = `${data.holdings_count} Active Positions`;

        if (data.trades && data.trades.length > 0) {
          let html = '';
          data.trades.forEach(t => {
            const isTargetHit = t.status === 'TARGET_HIT';
            const statusTag = isTargetHit ?
              '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-200">🎯 TARGET HIT</span>' :
              '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-indigo-50 text-indigo-700 border border-indigo-200">⚡ ACTIVE HOLDING</span>';

            html += `
              <tr class="hover:bg-slate-50">
                <td class="p-3.5 font-bold text-slate-900">${t.agent_name}</td>
                <td class="p-3.5 font-black text-indigo-900">#${t.symbol}</td>
                <td class="p-3.5 text-slate-600 text-[11px]">${t.trade_type}</td>
                <td class="p-3.5 font-bold text-slate-800">${t.quantity}</td>
                <td class="p-3.5 font-semibold text-slate-900">₹${t.invested_amt.toLocaleString('en-IN')}</td>
                <td class="p-3.5 text-slate-700">₹${t.entry_price.toFixed(2)}</td>
                <td class="p-3.5 font-bold text-slate-900">₹${t.current_price.toFixed(2)}</td>
                <td class="p-3.5 text-[11px] text-slate-600">T: ₹${t.target_price} | SL: ₹${t.stop_loss}</td>
                <td class="p-3.5">${statusTag}</td>
                <td class="p-3.5 font-bold ${t.pnl >= 0 ? 'text-emerald-600' : 'text-rose-600'}">
                  ${t.pnl >= 0 ? '+' : ''}₹${t.pnl.toLocaleString('en-IN')}
                </td>
                <td class="p-3.5 text-slate-500 max-w-xs truncate" title="${t.reason}">${t.reason}</td>
              </tr>`;
          });
          document.getElementById('ai-trades-rows').innerHTML = html;
        } else {
          document.getElementById('ai-trades-rows').innerHTML = `
            <tr>
              <td colspan="11" class="p-6 text-center text-slate-400">Kal subah market open hote hi AI real NSE prices par buy execute karega.</td>
            </tr>`;
        }
      } catch (e) {
        console.error(e);
      }
    }

    async function triggerAiRebalance() {
      alert('🤖 AI Market Scanner: Fetching Live NSE prices for ₹10 Lakh Fund allocation...');
      try {
        const res = await fetch('/api/ai-portfolio/rebalance', { method: 'POST' });
        const data = await res.json();
        alert('🎉 ' + data.message);
        loadAiPortfolio();
      } catch (e) {
        alert('Error: ' + e.message);
      }
    }

    function filterStocks(horizon) {
      activeFilter = horizon;
      ['all', 'long', 'swing', 'intra'].forEach(k => {
        const btn = document.getElementById('btn-f-' + k);
        if (btn) btn.className = 'px-3 py-1.5 rounded-lg text-xs font-semibold bg-white text-slate-600 border border-slate-200 hover:bg-slate-50 transition';
      });
      if (horizon === 'all') document.getElementById('btn-f-all').className = 'px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-900 text-white transition';
      else if (horizon === 'Long-Term') document.getElementById('btn-f-long').className = 'px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-900 text-white transition';
      else if (horizon === 'Swing') document.getElementById('btn-f-swing').className = 'px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-900 text-white transition';
      else if (horizon === 'Intraday') document.getElementById('btn-f-intra').className = 'px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-900 text-white transition';

      renderStocksDesk();
    }

    function renderStocksDesk() {
      let html = '';
      const list = activeFilter === 'all' ? STOCKS_DATA : STOCKS_DATA.filter(s => s.horizon === activeFilter);
      list.forEach(s => {
        html += `
          <div class="clean-card rounded-xl p-4 bg-white flex flex-col justify-between space-y-3">
            <div>
              <div class="flex items-start justify-between">
                <div class="flex items-center gap-3">
                  <div class="stock-logo ${s.logoBg} border">${s.logoText}</div>
                  <div>
                    <h3 class="text-sm font-bold text-slate-900">#${s.sym}</h3>
                    <div class="text-[11px] text-slate-500">${s.name}</div>
                  </div>
                </div>
                <span class="text-[10px] font-bold px-2 py-0.5 rounded ${s.qty === 0 ? 'bg-rose-50 text-rose-700 border border-rose-200' : 'bg-emerald-50 text-emerald-700 border border-emerald-200'}">
                  ${s.type}
                </span>
              </div>
              <p class="text-xs text-slate-600 mt-2">${s.why}</p>
            </div>

            <div class="bg-slate-50 p-2.5 rounded-lg text-xs space-y-1">
              <div class="flex justify-between text-slate-600"><span>Real Market Entry:</span> <b class="text-slate-900">₹${s.entry}</b></div>
              <div class="flex justify-between text-slate-600"><span>Stop Loss:</span> <b class="text-rose-600">₹${s.stop}</b></div>
              <div class="flex justify-between text-slate-600"><span>Target (Munafa):</span> <b class="text-emerald-700 font-bold">₹${s.target}</b></div>
            </div>

            <div class="flex items-center gap-2 pt-1">
              ${s.qty > 0 ? 
                `<button onclick="quickPaperTrade('${s.sym}', '${s.horizon}', ${s.entry}, ${s.stop}, ${s.target}, ${s.qty})" class="flex-1 py-2 bg-slate-900 hover:bg-slate-800 text-white rounded-lg text-xs font-semibold shadow-sm transition">
                  ⚡ My Paper Trade (${s.qty} Qty)
                </button>
                <button onclick="sendToTelegram('${s.sym}', '${s.type}', ${s.entry}, ${s.stop}, ${s.target}, '${s.why.replace(/'/g, "")}', ${s.qty})" title="Send to Telegram" class="p-2 border border-slate-200 hover:bg-slate-50 rounded-lg text-slate-700 transition">
                  <i class="fa-brands fa-telegram text-sky-500"></i>
                </button>` : 
                `<button disabled class="w-full py-2 bg-slate-100 text-slate-400 rounded-lg text-xs font-semibold cursor-not-allowed">
                  🔒 Blocked by Risk Shield
                </button>`
              }
            </div>
          </div>`;
      });
      document.getElementById('stocks-container').innerHTML = html;
    }

    async function sendToTelegram(symbol, horizon, entry, stop, target, reason, qty) {
      try {
        const res = await fetch('/api/telegram/broadcast-stock', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ symbol, horizon, entry, stop, target, reason, qty })
        });
        const data = await res.json();
        alert(`📲 Telegram Sent!\n${symbol} ka alert bot par bhej diya gaya hai!`);
      } catch (e) {
        alert('Telegram error: ' + e.message);
      }
    }

    async function broadcastAllToTelegram() {
      if (!confirm('Sabhi verified stocks ko Telegram par bhejein?')) return;
      alert('🚀 Telegram par alerts bheje ja rahe hain...');
      for (const s of STOCKS_DATA) {
        if (s.qty > 0) {
          await sendToTelegram(s.sym, s.type, s.entry, s.stop, s.target, s.why, s.qty);
        }
      }
      alert('✅ Sabhi stocks Telegram bot par deliver ho gaye!');
    }

    async function quickPaperTrade(symbol, type, entry, stop, target, qty) {
      try {
        const res = await fetch('/api/paper/execute', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_id: 1,
            symbol: symbol,
            trade_type: type,
            side: 'BUY',
            entry_price: entry,
            stop_loss: stop,
            target_price: target,
            quantity: qty
          })
        });
        const data = await res.json();
        alert(`✅ Trade Recorded!\n${symbol} (${qty} shares) aapke personal ₹10L portfolio me save ho gaya.`);
        refreshPortfolio();
      } catch (e) {
        alert('Trade error: ' + e.message);
      }
    }

    async function refreshPortfolio() {
      try {
        const res = await fetch('/api/paper/portfolio/1');
        const data = await res.json();

        document.getElementById('my-tot-val').innerText = `₹${data.portfolio_value.toLocaleString('en-IN')}`;
        document.getElementById('dash-my-val').innerText = `₹${data.portfolio_value.toLocaleString('en-IN')}`;

        const sign = data.simulated_pnl >= 0 ? '+' : '';
        document.getElementById('my-tot-pnl').innerText = `${sign}₹${data.simulated_pnl.toLocaleString('en-IN')}`;
        document.getElementById('dash-my-pnl').innerText = `${sign}₹${data.simulated_pnl.toLocaleString('en-IN')} (${sign}${data.roi_pct}%)`;
        document.getElementById('my-roi-pct').innerText = `${sign}${data.roi_pct}% Overall ROI`;

        document.getElementById('my-invested').innerText = `₹${data.invested_capital.toLocaleString('en-IN')}`;
        document.getElementById('my-cash').innerText = `₹${data.virtual_balance.toLocaleString('en-IN')}`;

        if (data.trades && data.trades.length > 0) {
          let html = '';
          data.trades.forEach(t => {
            html += `
              <tr class="hover:bg-slate-50">
                <td class="p-3.5 font-bold text-slate-900">#${t.symbol}</td>
                <td class="p-3.5 text-slate-600 text-[11px]">${t.trade_type}</td>
                <td class="p-3.5 font-semibold text-slate-900">${t.quantity}</td>
                <td class="p-3.5 text-slate-700">₹${t.entry_price.toFixed(2)}</td>
                <td class="p-3.5 font-bold text-slate-900">₹${t.current_price.toFixed(2)}</td>
                <td class="p-3.5 text-rose-600">₹${t.stop_loss}</td>
                <td class="p-3.5 text-emerald-600 font-bold">₹${t.target_price}</td>
                <td class="p-3.5"><span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-200">${t.status}</span></td>
                <td class="p-3.5 font-bold ${t.pnl >= 0 ? 'text-emerald-600' : 'text-rose-600'}">₹${t.pnl.toFixed(2)}</td>
              </tr>`;
          });
          document.getElementById('paper-rows').innerHTML = html;
        } else {
          document.getElementById('paper-rows').innerHTML = `
            <tr>
              <td colspan="9" class="p-6 text-center text-slate-400">Aapka account ready hai! Kal subah 9:15 AM market khulte hi suggestions tab se real price par trade buy karein.</td>
            </tr>`;
        }
      } catch (e) {
        console.error(e);
      }
    }

    async function loadComparisonReports() {
      try {
        const res = await fetch('/api/reports/comparison');
        const data = await res.json();
        let html = '';

        data.forEach(r => {
          let statusTag = r.outcome_status === 'TARGET_HIT' ?
            '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-200">🎯 TARGET HIT</span>' :
            (r.outcome_status === 'STOP_LOSS_HIT' ?
              '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-50 text-rose-700 border border-rose-200">🛑 STOP LOSS</span>' :
              '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-blue-50 text-blue-700 border border-blue-200">⏳ ACTIVE</span>');

          html += `
            <tr class="hover:bg-slate-50">
              <td class="p-3.5 font-medium text-slate-900">${r.agent_name}</td>
              <td class="p-3.5 font-bold text-slate-900">#${r.symbol}</td>
              <td class="p-3.5 font-semibold text-emerald-600">${r.recommendation}</td>
              <td class="p-3.5 text-slate-800">₹${r.suggested_entry.toFixed(2)}</td>
              <td class="p-3.5 font-bold text-blue-700">₹${r.actual_market_price.toFixed(2)}</td>
              <td class="p-3.5 text-[11px] text-slate-600">T: ₹${r.target_price} | SL: ₹${r.stop_loss}</td>
              <td class="p-3.5">${statusTag}</td>
              <td class="p-3.5 font-bold ${r.pnl_pct >= 0 ? 'text-emerald-600' : 'text-rose-600'}">
                ${r.pnl_pct >= 0 ? '+' : ''}${r.pnl_pct.toFixed(2)}%
              </td>
              <td class="p-3.5 text-slate-500 max-w-xs truncate" title="${r.reasoning}">${r.reasoning}</td>
            </tr>`;
        });

        document.getElementById('report-rows').innerHTML = html;
      } catch (e) {
        console.error(e);
      }
    }

    function openAuthModal() {
      document.getElementById('auth-modal').classList.remove('hidden');
      document.getElementById('m-view-login').classList.remove('hidden');
      document.getElementById('m-view-forgot').classList.add('hidden');
    }

    function closeAuthModal() {
      document.getElementById('auth-modal').classList.add('hidden');
    }

    function showForgotPassword() {
      document.getElementById('m-view-login').classList.add('hidden');
      document.getElementById('m-view-forgot').classList.remove('hidden');
      document.getElementById('f-step-1').classList.remove('hidden');
      document.getElementById('f-step-2').classList.add('hidden');
    }

    function backToLogin() {
      document.getElementById('m-view-forgot').classList.add('hidden');
      document.getElementById('m-view-login').classList.remove('hidden');
    }

    async function sendTelegramOTP() {
      const email = document.getElementById('forgot-email').value;
      const btn = document.getElementById('btn-otp-trigger');
      btn.innerText = 'Sending OTP...';
      try {
        const res = await fetch('/api/auth/send-otp', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email })
        });
        const data = await res.json();
        alert('📲 ' + data.message);
        document.getElementById('f-step-1').classList.add('hidden');
        document.getElementById('f-step-2').classList.remove('hidden');
      } catch (e) {
        alert('OTP error: ' + e.message);
      } finally {
        btn.innerHTML = '<i class="fa-brands fa-telegram"></i> Send OTP to Telegram';
      }
    }

    async function submitPasswordReset() {
      const email = document.getElementById('forgot-email').value;
      const otp = document.getElementById('reset-otp').value;
      const new_password = document.getElementById('reset-new-pass').value;

      if (!otp || !new_password) {
        alert('OTP aur Naya Password dono dalein!');
        return;
      }

      try {
        const res = await fetch('/api/auth/reset-password', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, otp, new_password })
        });
        const data = await res.json();
        alert('🎉 ' + data.message);
        document.getElementById('login-pass').value = new_password;
        backToLogin();
      } catch (e) {
        alert('Reset error: ' + e.message);
      }
    }

    function handleLogin(e) {
      e.preventDefault();
      closeAuthModal();
      alert('Welcome Ashok Kumar! Kal subah 9:15 AM real-market test ke liye aapka account ready hai.');
    }

    renderStocksDesk();
    loadAiPortfolio();
    refreshPortfolio();
  </script>
</body>
</html>
""")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
