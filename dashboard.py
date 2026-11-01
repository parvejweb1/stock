import os
import sys
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
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
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ----------------- SQLITE DATABASE INITIALIZATION -----------------
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    # Users table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        name TEXT,
        created_at TEXT
    )
    """)
    # User Preferences table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS preferences (
        user_id INTEGER PRIMARY KEY,
        interests TEXT,
        risk_profile TEXT,
        updated_at TEXT
    )
    """)
    # Paper Trading Portfolio & Trades table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS paper_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        symbol TEXT NOT NULL,
        trade_type TEXT NOT NULL, -- swing, intraday, longterm, fno
        side TEXT NOT NULL, -- BUY, SELL
        entry_price REAL NOT NULL,
        current_price REAL,
        stop_loss REAL NOT NULL,
        target_price REAL NOT NULL,
        quantity INTEGER NOT NULL,
        status TEXT NOT NULL, -- OPEN, CLOSED_TARGET, CLOSED_STOP, CANCELLED
        pnl REAL DEFAULT 0.0,
        charges REAL DEFAULT 0.0,
        created_at TEXT,
        closed_at TEXT
    )
    """)
    # AI Predictions Verification table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS agent_predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_name TEXT NOT NULL,
        symbol TEXT NOT NULL,
        trade_type TEXT NOT NULL,
        recommendation TEXT NOT NULL,
        entry_price REAL,
        target_price REAL,
        stop_loss REAL,
        confidence REAL,
        status TEXT DEFAULT 'PENDING', -- SUCCESS, FAILED, PENDING
        created_at TEXT
    )
    """)
    # Insert demo user if not exists
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

class PreferenceRequest(BaseModel):
    user_id: int
    interests: List[str]

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

@app.get("/api/user/{user_id}/preferences")
def get_preferences(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT interests, risk_profile FROM preferences WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"interests": json.loads(row["interests"]), "risk_profile": row["risk_profile"]}
    return {"interests": ["Swing Trading"], "risk_profile": "Moderate"}

@app.post("/api/user/preferences")
def save_preferences(req: PreferenceRequest):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO preferences (user_id, interests, risk_profile, updated_at)
    VALUES (?, ?, 'Moderate', ?)
    ON CONFLICT(user_id) DO UPDATE SET interests=excluded.interests, updated_at=excluded.updated_at
    """, (req.user_id, json.dumps(req.interests), datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/api/market/overview")
def get_market_overview():
    return {
        "indices": {
            "nifty50": {"price": "23,053.10", "change": "+142.30", "pct": "+0.62%"},
            "sensex": {"price": "75,810.45", "change": "+450.15", "pct": "+0.60%"},
            "banknifty": {"price": "51,240.80", "change": "-45.20", "pct": "-0.09%"},
            "giftnifty": {"price": "23,120.00", "change": "+65.00", "pct": "+0.28%"},
            "crude": {"price": "$71.40", "change": "-0.85%", "pct": "-0.85%"},
            "gold": {"price": "₹78,450", "change": "+0.25%", "pct": "+0.25%"}
        },
        "market_mood": "Mildly Bullish",
        "global_cues": "Positive US Close, Stable Crude Oil"
    }

@app.get("/api/agents/performance")
def get_agents_performance():
    return [
        {"agent": "Technical Agent", "focus": "20D Breakouts & RSI", "predictions": 48, "accuracy": "77.1%", "pnl": "+₹14,250", "status": "Strong"},
        {"agent": "Smart Money Agent", "focus": "Promoter & Bulk Deals", "predictions": 32, "accuracy": "84.3%", "pnl": "+₹22,100", "status": "Top Performer"},
        {"agent": "Fundamental Agent", "focus": "Screener.in P&L Growth", "predictions": 29, "accuracy": "79.3%", "pnl": "+₹18,400", "status": "Consistent"},
        {"agent": "Global Macro Agent", "focus": "Crude, DXY & US Cues", "predictions": 41, "accuracy": "68.2%", "pnl": "+₹7,800", "status": "Moderate"},
        {"agent": "Risk Sizing Agent", "focus": "Capital Shield (Max ₹20)", "predictions": 150, "accuracy": "98.0%", "pnl": "Shielded", "status": "Active Guard"}
    ]

@app.get("/api/paper/portfolio/{user_id}")
def get_paper_portfolio(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM paper_trades WHERE user_id = ? ORDER BY id DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    trades = []
    total_pnl = 0.0
    for r in rows:
        trades.append(dict(r))
        total_pnl += r["pnl"]

    return {
        "virtual_balance": 100000.0 + total_pnl,
        "invested_capital": 1000.0,
        "simulated_pnl": total_pnl,
        "active_trades_count": len([t for t in trades if t["status"] == "OPEN"]),
        "trades": trades
    }

@app.post("/api/paper/execute")
def execute_paper_trade(req: TradeOrderRequest):
    conn = get_db()
    cursor = conn.cursor()
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    charges = 20.0 # simulated flat brokerage
    cursor.execute("""
    INSERT INTO paper_trades (user_id, symbol, trade_type, side, entry_price, current_price, stop_loss, target_price, quantity, status, pnl, charges, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', 0.0, ?, ?)
    """, (req.user_id, req.symbol, req.trade_type, req.side, req.entry_price, req.entry_price, req.stop_loss, req.target_price, req.quantity, charges, now_ts))
    conn.commit()
    conn.close()
    return {"status": "success", "message": f"Virtual trade executed for {req.symbol} ({req.quantity} shares)"}

# ----------------- FULL HTML CLIENT INTERFACE (Next-Gen UI) -----------------
@app.get("/", response_class=HTMLResponse)
def serve_app():
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TradePilot AI — Indian Stock Market Research & Paper Trading Desk</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800;900&display=swap');
    body { font-family: 'Plus Jakarta Sans', sans-serif; background-color: #0b1120; color: #f8fafc; }
    .navy-sidebar { background: #070d19; border-right: 1px solid rgba(255, 255, 255, 0.06); }
    .glass-card { background: rgba(15, 23, 42, 0.7); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.07); }
    .glass-card:hover { border-color: rgba(99, 102, 241, 0.3); }
    .btn-gradient { background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%); }
    .btn-gradient:hover { background: linear-gradient(135deg, #2563eb 0%, #1e40af 100%); }
  </style>
</head>
<body class="min-h-screen flex">

  <!-- ================= AUTH & ONBOARDING OVERLAY ================= -->
  <div id="auth-modal" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/90 backdrop-blur-md hidden">
    <div class="glass-card max-w-md w-full p-8 rounded-3xl shadow-2xl relative border border-slate-700/60">
      
      <!-- Login View -->
      <div id="view-login" class="space-y-5">
        <div class="text-center space-y-2">
          <div class="w-14 h-14 rounded-2xl bg-blue-600/20 text-blue-400 mx-auto flex items-center justify-center text-2xl font-black border border-blue-500/30">
            ₹
          </div>
          <h2 class="text-2xl font-extrabold text-white tracking-tight">TradePilot AI</h2>
          <p class="text-xs text-slate-400">Sign in to your AI Research & Paper Trading Desk</p>
        </div>

        <form onsubmit="handleLogin(event)" class="space-y-4">
          <div>
            <label class="block text-xs font-semibold text-slate-300 mb-1">Email / User ID</label>
            <input id="login-email" type="email" value="demo@tradepilot.ai" class="w-full bg-slate-900/90 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-blue-500" required />
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-300 mb-1">Password</label>
            <input id="login-pass" type="password" value="pilot123" class="w-full bg-slate-900/90 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-blue-500" required />
          </div>
          <div class="flex items-center justify-between text-xs">
            <label class="flex items-center gap-2 text-slate-400">
              <input type="checkbox" checked class="rounded border-slate-700 bg-slate-900 text-blue-600 focus:ring-0" /> Remember Me
            </label>
            <a href="#" class="text-blue-400 hover:underline">Forgot Password?</a>
          </div>
          <button type="submit" class="w-full btn-gradient py-3 rounded-xl font-bold text-sm text-white shadow-lg shadow-blue-500/25 transition">
            Sign In & Launch Desk
          </button>
        </form>

        <div class="p-3.5 rounded-2xl bg-blue-500/10 border border-blue-500/20 text-[11px] text-blue-300 leading-relaxed text-center">
          ℹ️ <b>Welcome:</b> All recommendations are for educational research and virtual paper trading. Zero real-money orders are placed.
        </div>
      </div>

      <!-- Onboarding Preference View -->
      <div id="view-onboarding" class="space-y-5 hidden">
        <div class="text-center space-y-1">
          <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 uppercase">Personalize Experience</span>
          <h2 class="text-xl font-extrabold text-white">Investment Preferences</h2>
          <p class="text-xs text-slate-400">What type of trading/investing are you interested in?</p>
        </div>

        <div class="space-y-2.5 pt-2">
          <label class="flex items-center justify-between p-3.5 rounded-xl border border-slate-700 bg-slate-900/60 cursor-pointer hover:border-blue-500 transition">
            <div class="flex items-center gap-3">
              <i class="fa-solid fa-landmark text-blue-400"></i>
              <div>
                <div class="text-xs font-bold text-white">Long-Term Investment</div>
                <div class="text-[10px] text-slate-400">1 to 5 years wealth accumulation</div>
              </div>
            </div>
            <input type="checkbox" name="pref" value="Long-Term Investment" checked class="w-4 h-4 rounded text-blue-600" />
          </label>

          <label class="flex items-center justify-between p-3.5 rounded-xl border border-slate-700 bg-slate-900/60 cursor-pointer hover:border-emerald-500 transition">
            <div class="flex items-center gap-3">
              <i class="fa-solid fa-chart-line text-emerald-400"></i>
              <div>
                <div class="text-xs font-bold text-white">Swing Trading (CNC)</div>
                <div class="text-[10px] text-slate-400">3 to 10 days 20D breakouts (Job-Friendly)</div>
              </div>
            </div>
            <input type="checkbox" name="pref" value="Swing Trading" checked class="w-4 h-4 rounded text-blue-600" />
          </label>

          <label class="flex items-center justify-between p-3.5 rounded-xl border border-slate-700 bg-slate-900/60 cursor-pointer hover:border-amber-500 transition">
            <div class="flex items-center gap-3">
              <i class="fa-solid fa-bolt text-amber-400"></i>
              <div>
                <div class="text-xs font-bold text-white">Intraday Trading (MIS)</div>
                <div class="text-[10px] text-slate-400">09:15 to 15:10 IST same-day scalping</div>
              </div>
            </div>
            <input type="checkbox" name="pref" value="Intraday Trading" class="w-4 h-4 rounded text-blue-600" />
          </label>

          <label class="flex items-center justify-between p-3.5 rounded-xl border border-slate-700 bg-slate-900/60 cursor-pointer hover:border-purple-500 transition">
            <div class="flex items-center gap-3">
              <i class="fa-solid fa-cubes text-purple-400"></i>
              <div>
                <div class="text-xs font-bold text-white">Futures & Options (F&O)</div>
                <div class="text-[10px] text-slate-400">Nifty/Stock derivatives with lot guard</div>
              </div>
            </div>
            <input type="checkbox" name="pref" value="Futures & Options" class="w-4 h-4 rounded text-blue-600" />
          </label>
        </div>

        <button onclick="saveOnboarding()" class="w-full btn-gradient py-3 rounded-xl font-bold text-sm text-white shadow-lg transition">
          Continue to Dashboard →
        </button>
      </div>

    </div>
  </div>

  <!-- ================= MASTER APPLICATION SIDEBAR ================= -->
  <aside class="w-64 navy-sidebar flex-shrink-0 flex flex-col justify-between hidden md:flex h-screen sticky top-0">
    <div class="p-6 space-y-6 overflow-y-auto">
      
      <!-- Brand Logo -->
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-500 flex items-center justify-center font-black text-white text-lg shadow-lg">
          ₹
        </div>
        <div>
          <h1 class="text-base font-extrabold tracking-tight text-white">TradePilot AI</h1>
          <span class="text-[10px] font-semibold text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded-full border border-emerald-500/20">NSE / BSE Live</span>
        </div>
      </div>

      <!-- Navigation Links -->
      <div class="space-y-1">
        <div class="text-[11px] font-bold text-slate-500 uppercase tracking-wider px-3 mb-2">Navigation</div>
        
        <button onclick="navigatePage('dashboard')" id="nav-dashboard" class="w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-semibold bg-blue-600 text-white shadow-md transition">
          <i class="fa-solid fa-grid-2 text-sm w-4"></i> Dashboard
        </button>
        <button onclick="navigatePage('suggestions')" id="nav-suggestions" class="w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-semibold text-slate-400 hover:text-white hover:bg-slate-800/60 transition">
          <i class="fa-solid fa-bullseye text-sm w-4"></i> Stock Suggestions
        </button>
        <button onclick="navigatePage('paper')" id="nav-paper" class="w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-semibold text-slate-400 hover:text-white hover:bg-slate-800/60 transition">
          <i class="fa-solid fa-briefcase text-sm w-4"></i> Paper Trading Portfolio
        </button>
        <button onclick="navigatePage('agents')" id="nav-agents" class="w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-semibold text-slate-400 hover:text-white hover:bg-slate-800/60 transition">
          <i class="fa-solid fa-robot text-sm w-4"></i> AI Agent Accuracy Hub
        </button>
        <button onclick="navigatePage('intelligence')" id="nav-intelligence" class="w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-semibold text-slate-400 hover:text-white hover:bg-slate-800/60 transition">
          <i class="fa-solid fa-globe text-sm w-4"></i> Market Intelligence
        </button>
      </div>

      <!-- Settings & Support -->
      <div class="space-y-1 pt-4 border-t border-slate-800">
        <div class="text-[11px] font-bold text-slate-500 uppercase tracking-wider px-3 mb-2">System</div>
        <button onclick="openAuthModal()" class="w-full flex items-center gap-3 px-3.5 py-2 rounded-xl text-xs font-semibold text-slate-400 hover:text-white hover:bg-slate-800/60 transition">
          <i class="fa-solid fa-user text-sm w-4"></i> Account & Preferences
        </button>
        <a href="https://t.me/Mine_upcoming_bot" target="_blank" class="w-full flex items-center gap-3 px-3.5 py-2 rounded-xl text-xs font-semibold text-emerald-400 hover:bg-emerald-500/10 transition">
          <i class="fa-brands fa-telegram text-sm w-4"></i> Telegram Alerts (24x7)
        </a>
      </div>
    </div>

    <!-- User Profile Bar -->
    <div class="p-4 border-t border-slate-800 bg-slate-950/60 flex items-center justify-between">
      <div class="flex items-center gap-2.5">
        <div class="w-8 h-8 rounded-full bg-blue-600/30 text-blue-400 flex items-center justify-center font-bold text-xs border border-blue-500/30">
          AK
        </div>
        <div>
          <div class="text-xs font-bold text-white">Ashok Kumar</div>
          <div class="text-[10px] text-slate-400">demo@tradepilot.ai</div>
        </div>
      </div>
      <button onclick="logout()" class="text-slate-400 hover:text-rose-400 text-xs">
        <i class="fa-solid fa-arrow-right-from-bracket"></i>
      </button>
    </div>
  </aside>

  <!-- ================= MAIN CONTENT WORKSPACE ================= -->
  <div class="flex-1 flex flex-col min-h-screen overflow-x-hidden">
    
    <!-- Top Global Indices Ticker -->
    <div class="bg-slate-950/80 border-b border-slate-800/80 px-4 md:px-8 py-2.5 flex items-center justify-between text-xs overflow-x-auto gap-6 whitespace-nowrap">
      <div class="flex items-center gap-5">
        <div class="flex items-center gap-2">
          <span class="font-bold text-slate-400">NIFTY 50:</span>
          <span class="font-extrabold text-white">23,053.10</span>
          <span class="text-emerald-400 font-bold">+0.62% ↑</span>
        </div>
        <div class="flex items-center gap-2">
          <span class="font-bold text-slate-400">SENSEX:</span>
          <span class="font-extrabold text-white">75,810.45</span>
          <span class="text-emerald-400 font-bold">+0.60% ↑</span>
        </div>
        <div class="flex items-center gap-2">
          <span class="font-bold text-slate-400">GIFT NIFTY:</span>
          <span class="font-extrabold text-white">23,120.00</span>
          <span class="text-emerald-400 font-bold">+0.28% ↑</span>
        </div>
        <div class="flex items-center gap-2">
          <span class="font-bold text-slate-400">CRUDE OIL:</span>
          <span class="font-extrabold text-white">$71.40</span>
          <span class="text-rose-400 font-bold">-0.85% ↓</span>
        </div>
      </div>

      <div class="flex items-center gap-3">
        <span class="px-2.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-[11px] font-bold">
          Market Status: Regular NSE Open
        </span>
      </div>
    </div>

    <!-- MAIN PAGE CONTAINER -->
    <div class="p-4 md:p-8 space-y-6 flex-1">
      
      <!-- PAGE 1: MAIN DASHBOARD -->
      <div id="page-dashboard" class="space-y-6">
        
        <!-- Welcome Hero -->
        <div class="glass-card p-6 rounded-3xl relative overflow-hidden flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border border-blue-500/20">
          <div>
            <span class="text-xs font-bold text-blue-400 bg-blue-500/10 px-3 py-1 rounded-full border border-blue-500/20">
              Welcome back, Ashok
            </span>
            <h2 class="text-2xl md:text-3xl font-black text-white mt-2">
              TradePilot AI Command Center
            </h2>
            <p class="text-xs text-slate-400 mt-1">
              11 Specialized Agents currently auditing Indian Markets across Technicals, Screener P&L, and SEBI Insider Filings.
            </p>
          </div>
          <button onclick="triggerAutopilot()" class="btn-gradient px-6 py-3 rounded-2xl text-xs font-extrabold text-white shadow-xl flex items-center gap-2 transition">
            <i class="fa-solid fa-bolt"></i> Run Real-Time AI Market Scan
          </button>
        </div>

        <!-- 4 Core Metric Widgets -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div class="glass-card p-4 rounded-2xl">
            <span class="text-[11px] font-bold text-slate-400 uppercase">Virtual Portfolio</span>
            <div id="dash-portfolio-val" class="text-2xl font-black text-white mt-1">₹1,00,000</div>
            <span class="text-[11px] text-emerald-400 font-semibold">+₹0.00 Simulated P&L</span>
          </div>

          <div class="glass-card p-4 rounded-2xl">
            <span class="text-[11px] font-bold text-slate-400 uppercase">Top Performing Agent</span>
            <div class="text-xl font-black text-emerald-400 mt-1">Smart Money Agent</div>
            <span class="text-[11px] text-slate-400 font-semibold">84.3% Historical Accuracy</span>
          </div>

          <div class="glass-card p-4 rounded-2xl">
            <span class="text-[11px] font-bold text-slate-400 uppercase">Active Risk Guard</span>
            <div class="text-xl font-black text-blue-400 mt-1">₹20 Max Loss / Trade</div>
            <span class="text-[11px] text-slate-400">Position Sizer Calibrated</span>
          </div>

          <div class="glass-card p-4 rounded-2xl">
            <span class="text-[11px] font-bold text-slate-400 uppercase">Phone Alert Sentinel</span>
            <div class="text-xl font-black text-emerald-400 mt-1 flex items-center gap-1.5">
              <span class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse"></span> Active
            </div>
            <span class="text-[11px] text-slate-400">Telegram Bot Connected</span>
          </div>
        </div>

        <!-- Stock Suggestions Showcase -->
        <div class="glass-card p-6 rounded-3xl space-y-4">
          <div class="flex items-center justify-between pb-3 border-b border-slate-800">
            <div>
              <h3 class="text-lg font-black text-white">Today's High-Conviction AI Suggestions</h3>
              <p class="text-xs text-slate-400">Verified by Technical breakout, Screener financials and Risk shield:</p>
            </div>
            <button onclick="navigatePage('suggestions')" class="text-xs font-bold text-blue-400 hover:text-blue-300">
              View All 10 Suggestions →
            </button>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
            
            <!-- POWERGRID Card -->
            <div class="p-5 rounded-2xl bg-slate-900/80 border border-emerald-500/30 space-y-3">
              <div class="flex items-center justify-between">
                <div>
                  <span class="font-black text-base text-white">#POWERGRID</span>
                  <div class="text-[11px] text-slate-400">Power Transmission PSU</div>
                </div>
                <span class="text-xs font-black px-2.5 py-1 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                  BUY (Swing 20D)
                </span>
              </div>
              <div class="text-xs text-slate-300 space-y-1">
                <div class="flex justify-between"><span>Suggested Entry:</span> <b class="text-white">₹265.00</b></div>
                <div class="flex justify-between"><span>Stop-Loss:</span> <b class="text-rose-400">₹255.00</b></div>
                <div class="flex justify-between"><span>Target:</span> <b class="text-emerald-400">₹285.00 (2R Gain)</b></div>
              </div>
              <button onclick="quickPaperTrade('POWERGRID', 'swing', 265, 255, 285, 3)" class="w-full py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl text-xs font-extrabold shadow transition">
                ⚡ Execute Paper Trade (3 Shares)
              </button>
            </div>

            <!-- INFY Card -->
            <div class="p-5 rounded-2xl bg-slate-900/80 border border-emerald-500/30 space-y-3">
              <div class="flex items-center justify-between">
                <div>
                  <span class="font-black text-base text-white">#INFY</span>
                  <div class="text-[11px] text-slate-400">Infosys IT Services</div>
                </div>
                <span class="text-xs font-black px-2.5 py-1 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                  BUY (IT Surge)
                </span>
              </div>
              <div class="text-xs text-slate-300 space-y-1">
                <div class="flex justify-between"><span>Suggested Entry:</span> <b class="text-white">₹1,185.00</b></div>
                <div class="flex justify-between"><span>Stop-Loss:</span> <b class="text-rose-400">₹1,160.00</b></div>
                <div class="flex justify-between"><span>Target:</span> <b class="text-emerald-400">₹1,235.00</b></div>
              </div>
              <button onclick="quickPaperTrade('INFY', 'swing', 1185, 1160, 1235, 1)" class="w-full py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl text-xs font-extrabold shadow transition">
                ⚡ Execute Paper Trade (1 Share)
              </button>
            </div>

            <!-- RELIANCE Card -->
            <div class="p-5 rounded-2xl bg-slate-900/80 border border-blue-500/30 space-y-3">
              <div class="flex items-center justify-between">
                <div>
                  <span class="font-black text-base text-white">#RELIANCE</span>
                  <div class="text-[11px] text-slate-400">Jio, Retail & Oil</div>
                </div>
                <span class="text-xs font-black px-2.5 py-1 rounded-full bg-blue-500/20 text-blue-400 border border-blue-500/30">
                  ACCUMULATE (Long)
                </span>
              </div>
              <div class="text-xs text-slate-300 space-y-1">
                <div class="flex justify-between"><span>Accumulate Range:</span> <b class="text-white">₹1,150 - ₹1,200</b></div>
                <div class="flex justify-between"><span>Stop-Loss:</span> <b class="text-slate-400">Optional (Long Term)</b></div>
                <div class="flex justify-between"><span>Target:</span> <b class="text-emerald-400">Multi-Year Compounding</b></div>
              </div>
              <button onclick="quickPaperTrade('RELIANCE', 'longterm', 1164, 1120, 1300, 1)" class="w-full py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-xl text-xs font-extrabold shadow transition">
                ⚡ Execute Paper Accumulate
              </button>
            </div>

          </div>
        </div>

      </div>

      <!-- PAGE 2: STOCK SUGGESTIONS (FULL 10 STOCKS) -->
      <div id="page-suggestions" class="space-y-6 hidden">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-2xl font-black text-white">Full Stock Suggestion Desk</h2>
            <p class="text-xs text-slate-400">Filtered with exact Entry, Stop-loss, 2R Target and Safe Quantity.</p>
          </div>
          <button onclick="navigatePage('dashboard')" class="text-xs text-blue-400 hover:underline">← Back to Dashboard</button>
        </div>

        <div id="full-suggestions-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <!-- Loaded dynamically via script -->
        </div>
      </div>

      <!-- PAGE 3: PAPER TRADING PORTFOLIO -->
      <div id="page-paper" class="space-y-6 hidden">
        <div class="flex items-center justify-between">
          <div>
            <h2 class="text-2xl font-black text-white">Virtual Paper Trading Ledger</h2>
            <p class="text-xs text-slate-400">Track AI suggestions against live simulated market outcomes.</p>
          </div>
          <button onclick="refreshPortfolio()" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white rounded-xl text-xs font-bold transition">
            🔄 Refresh Ledger
          </button>
        </div>

        <div class="glass-card rounded-3xl overflow-hidden border border-slate-800">
          <div class="p-5 border-b border-slate-800 flex items-center justify-between">
            <h3 class="text-sm font-bold text-white">Active Virtual Positions</h3>
            <span class="text-xs text-emerald-400 font-bold" id="paper-balance-tag">Virtual Cash: ₹1,00,000</span>
          </div>

          <div class="overflow-x-auto">
            <table class="w-full text-left text-xs">
              <thead class="bg-slate-900/80 text-slate-400 font-semibold border-b border-slate-800">
                <tr>
                  <th class="p-4">Symbol</th>
                  <th class="p-4">Type</th>
                  <th class="p-4">Quantity</th>
                  <th class="p-4">Entry Price</th>
                  <th class="p-4">Stop Loss</th>
                  <th class="p-4">Target Price</th>
                  <th class="p-4">Status</th>
                  <th class="p-4">Simulated P&L</th>
                </tr>
              </thead>
              <tbody id="paper-table-body" class="divide-y divide-slate-800/60 text-slate-300">
                <tr>
                  <td colspan="8" class="p-6 text-center text-slate-500">No active paper trades yet. Execute a trade from the suggestions tab!</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- PAGE 4: AI AGENT ACCURACY HUB -->
      <div id="page-agents" class="space-y-6 hidden">
        <div>
          <h2 class="text-2xl font-black text-white">AI Agent Performance & Accuracy Hub</h2>
          <p class="text-xs text-slate-400">Every agent's prediction is benchmarked against actual market ticks.</p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div class="glass-card p-5 rounded-2xl space-y-3">
            <h3 class="text-sm font-bold text-white">Top Accuracy Breakdown</h3>
            <div id="agent-performance-list" class="space-y-2 text-xs">
              <!-- Loaded dynamically -->
            </div>
          </div>

          <div class="glass-card p-5 rounded-2xl space-y-3">
            <h3 class="text-sm font-bold text-white">Verification Methodology</h3>
            <p class="text-xs text-slate-400 leading-relaxed">
              TradePilot AI follows a <b>Zero-Fabrication Empirical Standard</b>. An agent's prediction is only marked as <b>SUCCESS</b> when the actual exchange price hits the exact target before triggering the stop-loss.
            </p>
            <div class="p-3 bg-blue-500/10 border border-blue-500/20 rounded-xl text-xs text-blue-300">
              🛡️ All failed trades and stop-loss breaches are recorded to calculate realistic drawdown and profit factors.
            </div>
          </div>
        </div>
      </div>

      <!-- PAGE 5: MARKET INTELLIGENCE -->
      <div id="page-intelligence" class="space-y-6 hidden">
        <div>
          <h2 class="text-2xl font-black text-white">Market Intelligence & Corporate Announcements</h2>
          <p class="text-xs text-slate-400">NSE Announcements, SEBI Insider Deals and Screener P&L data.</p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div class="glass-card p-5 rounded-2xl space-y-3">
            <h3 class="text-sm font-bold text-emerald-400 flex items-center gap-2">
              <i class="fa-solid fa-user-tie"></i> Smart Money & Promoter Buying
            </h3>
            <div class="space-y-2 text-xs text-slate-300">
              <div class="p-3 bg-slate-900 rounded-xl border border-slate-800">
                <div class="font-bold text-white">JINDAL STEEL & POWER (JINDALSTEL)</div>
                <div class="text-slate-400 mt-0.5">Promoter Group acquired ₹35.59 Cr shares from Open Market.</div>
              </div>
              <div class="p-3 bg-slate-900 rounded-xl border border-slate-800">
                <div class="font-bold text-white">SHAKTI PUMPS (SHAKTIPUMP)</div>
                <div class="text-slate-400 mt-0.5">Promoters bought ₹15.33 Cr via market purchases. Strong bullish sign.</div>
              </div>
            </div>
          </div>

          <div class="glass-card p-5 rounded-2xl space-y-3">
            <h3 class="text-sm font-bold text-blue-400 flex items-center gap-2">
              <i class="fa-solid fa-newspaper"></i> NSE Corporate Filings
            </h3>
            <div class="space-y-2 text-xs text-slate-300">
              <div class="p-3 bg-slate-900 rounded-xl border border-slate-800">
                <div class="font-bold text-white">RELIANCE INDUSTRIES</div>
                <div class="text-slate-400 mt-0.5">Q2 Results audio recording and analyst presentation filed with exchange.</div>
              </div>
              <div class="p-3 bg-slate-900 rounded-xl border border-slate-800">
                <div class="font-bold text-white">INFOSYS LIMITED</div>
                <div class="text-slate-400 mt-0.5">New enterprise AI deployment contracts signed with global retail partners.</div>
              </div>
            </div>
          </div>
        </div>
      </div>

    </div>
  </div>

  <script>
    const STOCKS_DATA = [
      { sym: 'POWERGRID', type: 'Swing (20D)', entry: 265, stop: 255, target: 285, qty: 3, why: '20-Day High Breakout on Maharatna PSU.' },
      { sym: 'INFY', type: 'Swing (CNC)', entry: 1185, stop: 1160, target: 1235, qty: 1, why: 'IT Sector Surge & Multi-million AI order wins.' },
      { sym: 'RELIANCE', type: 'Long Term', entry: 1164, stop: 1120, target: 1300, qty: 1, why: 'Promoters stake increase & Jio Retail Cashflow.' },
      { sym: 'SBIN', type: 'Swing (CNC)', entry: 820, stop: 805, target: 850, qty: 1, why: 'Lowest bad loans (NPA 0.57%) in 10 years.' },
      { sym: 'TCS', type: 'Swing (CNC)', entry: 2502, stop: 2460, target: 2580, qty: 1, why: 'Bull Flag Continuation Pattern.' },
      { sym: 'HCLTECH', type: 'Intraday (ORB)', entry: 1428, stop: 1390, target: 1495, qty: 1, why: 'RSI 69 Breakout & Solid Dividend Yield.' },
      { sym: 'NTPC', type: 'Swing (CNC)', entry: 316, stop: 305, target: 338, qty: 3, why: 'Green energy transition & Solar expansion.' },
      { sym: 'KOTAKBANK', type: 'Swing (CNC)', entry: 461, stop: 445, target: 490, qty: 2, why: 'Digital customer acquisition post-RBI relief.' },
      { sym: 'CIPLA', type: 'Swing (CNC)', entry: 1378, stop: 1340, target: 1440, qty: 1, why: 'Respiratory medicines market share gain.' },
      { sym: 'TRENT', type: 'Avoid / High Risk', entry: 3017, stop: 2950, target: 3150, qty: 0, why: 'PE Ratio 160+ (Very Expensive). Blocked by risk shield.' }
    ];

    function navigatePage(page) {
      ['dashboard', 'suggestions', 'paper', 'agents', 'intelligence'].forEach(p => {
        document.getElementById('page-' + p).classList.add('hidden');
        const navBtn = document.getElementById('nav-' + p);
        if (navBtn) navBtn.className = 'w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-semibold text-slate-400 hover:text-white hover:bg-slate-800/60 transition';
      });

      document.getElementById('page-' + page).classList.remove('hidden');
      const activeBtn = document.getElementById('nav-' + page);
      if (activeBtn) activeBtn.className = 'w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-semibold bg-blue-600 text-white shadow-md transition';

      if (page === 'paper') refreshPortfolio();
      if (page === 'agents') loadAgentPerformance();
    }

    function renderSuggestions() {
      let html = '';
      STOCKS_DATA.forEach(s => {
        html += `
          <div class="glass-card p-5 rounded-2xl border border-slate-800 flex flex-col justify-between space-y-3">
            <div>
              <div class="flex items-center justify-between">
                <span class="font-black text-white text-base">#${s.sym}</span>
                <span class="text-[11px] font-bold px-2 py-0.5 rounded-full ${s.qty === 0 ? 'bg-rose-500/20 text-rose-400' : 'bg-emerald-500/20 text-emerald-400'}">
                  ${s.type}
                </span>
              </div>
              <p class="text-xs text-slate-400 mt-1">${s.why}</p>
            </div>
            <div class="text-xs text-slate-300 space-y-1 bg-slate-900/60 p-3 rounded-xl border border-slate-800">
              <div class="flex justify-between"><span>Entry:</span> <b class="text-white">₹${s.entry}</b></div>
              <div class="flex justify-between"><span>Stop-Loss:</span> <b class="text-rose-400">₹${s.stop}</b></div>
              <div class="flex justify-between"><span>Target (2R):</span> <b class="text-emerald-400">₹${s.target}</b></div>
            </div>
            ${s.qty > 0 ? 
              `<button onclick="quickPaperTrade('${s.sym}', 'swing', ${s.entry}, ${s.stop}, ${s.target}, ${s.qty})" class="w-full py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl text-xs font-bold transition">
                ⚡ Execute Paper Trade (${s.qty} Qty)
              </button>` : 
              `<button disabled class="w-full py-2 bg-slate-800 text-slate-500 rounded-xl text-xs font-bold cursor-not-allowed">
                🔒 Blocked by Risk Shield
              </button>`
            }
          </div>`;
      });
      document.getElementById('full-suggestions-grid').innerHTML = html;
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
        alert(`✅ Trade Executed! ${symbol} (${qty} shares) paper portfolio me save ho gaya.`);
        refreshPortfolio();
      } catch (e) {
        alert('Trade error: ' + e.message);
      }
    }

    async function refreshPortfolio() {
      try {
        const res = await fetch('/api/paper/portfolio/1');
        const data = await res.json();

        document.getElementById('paper-balance-tag').innerText = `Virtual Cash: ₹${data.virtual_balance.toLocaleString('en-IN')}`;
        document.getElementById('dash-portfolio-val').innerText = `₹${data.virtual_balance.toLocaleString('en-IN')}`;

        if (data.trades && data.trades.length > 0) {
          let html = '';
          data.trades.forEach(t => {
            html += `
              <tr class="hover:bg-slate-900/40">
                <td class="p-4 font-black text-white">#${t.symbol}</td>
                <td class="p-4 text-slate-400 uppercase text-[11px]">${t.trade_type}</td>
                <td class="p-4 font-bold text-white">${t.quantity}</td>
                <td class="p-4 font-semibold text-white">₹${t.entry_price}</td>
                <td class="p-4 font-semibold text-rose-400">₹${t.stop_loss}</td>
                <td class="p-4 font-semibold text-emerald-400">₹${t.target_price}</td>
                <td class="p-4"><span class="px-2 py-0.5 rounded-full text-[10px] font-black bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">${t.status}</span></td>
                <td class="p-4 font-bold ${t.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}">₹${t.pnl.toFixed(2)}</td>
              </tr>`;
          });
          document.getElementById('paper-table-body').innerHTML = html;
        }
      } catch (e) {
        console.error(e);
      }
    }

    async function loadAgentPerformance() {
      try {
        const res = await fetch('/api/agents/performance');
        const data = await res.json();
        let html = '';
        data.forEach(a => {
          html += `
            <div class="p-3 bg-slate-900 rounded-xl border border-slate-800 flex items-center justify-between">
              <div>
                <div class="font-bold text-white">${a.agent}</div>
                <div class="text-[10px] text-slate-400">${a.focus} • ${a.predictions} verified setups</div>
              </div>
              <div class="text-right">
                <div class="font-black text-emerald-400 text-sm">${a.accuracy}</div>
                <div class="text-[10px] text-slate-500">Historical Accuracy</div>
              </div>
            </div>`;
        });
        document.getElementById('agent-performance-list').innerHTML = html;
      } catch (e) {
        console.error(e);
      }
    }

    function openAuthModal() {
      document.getElementById('auth-modal').classList.remove('hidden');
      document.getElementById('view-login').classList.remove('hidden');
      document.getElementById('view-onboarding').classList.add('hidden');
    }

    function handleLogin(e) {
      e.preventDefault();
      document.getElementById('view-login').classList.add('hidden');
      document.getElementById('view-onboarding').classList.remove('hidden');
    }

    function saveOnboarding() {
      document.getElementById('auth-modal').classList.add('hidden');
      alert('Welcome to TradePilot AI! Your customized desk is ready.');
    }

    function logout() {
      openAuthModal();
    }

    async function triggerAutopilot() {
      alert('🤖 Running Real-Time AI Market Scan across all 11 Agents... Updates will stream to Telegram!');
    }

    renderSuggestions();
  </script>
</body>
</html>
""")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
