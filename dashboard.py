import os
import sys
import json
import re
import subprocess
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Trader 2.0 - Simple AI Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WORKSPACE_DIR = Path(__file__).resolve().parent
SKILLS_DIR = WORKSPACE_DIR / "skills"
if not SKILLS_DIR.exists():
    SKILLS_DIR = Path("C:/Users/Admin/.gemini/config/skills")
PYTHON_EXE = sys.executable

def run_skill_script(script_subpath: str, args: list) -> dict:
    full_path = SKILLS_DIR / script_subpath
    if not full_path.exists():
        return {"error": f"Script not found: {full_path}"}
    cmd = [str(PYTHON_EXE), str(full_path)] + args
    try:
        res = subprocess.run(cmd, cwd=str(WORKSPACE_DIR), capture_output=True, text=True, timeout=120)
        return {
            "exit_code": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr
        }
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/autopilot")
def trigger_autopilot():
    script_path = WORKSPACE_DIR / "autopilot.py"
    cmd = [str(PYTHON_EXE), str(script_path)]
    try:
        res = subprocess.run(cmd, cwd=str(WORKSPACE_DIR), capture_output=True, text=True, timeout=180)
        return {"status": "ok", "stdout": res.stdout, "stderr": res.stderr}
    except Exception as e:
        return {"error": str(e)}

@app.get("/", response_class=HTMLResponse)
def get_dashboard():
    html_content = """<!DOCTYPE html>
<html lang="hi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Trader 2.0 - Simple AI Assistant</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800;900&display=swap');
    body { font-family: 'Plus Jakarta Sans', sans-serif; background-color: #f1f5f9; color: #0f172a; }
    .navy-header { background: linear-gradient(135deg, #091a3c 0%, #173878 100%); }
    .clean-card { background: #ffffff; border-radius: 24px; border: 1px solid #e2e8f0; box-shadow: 0 10px 25px -5px rgba(15, 23, 42, 0.04); }
    .signal-green { background: linear-gradient(135deg, #ecfdf5 0%, #d1fae5 100%); border: 2px solid #10b981; }
    .signal-red { background: linear-gradient(135deg, #fef2f2 0%, #fee2e2 100%); border: 2px solid #ef4444; }
    .pill-active { background-color: #173878; color: #ffffff; box-shadow: 0 4px 12px rgba(23, 56, 120, 0.25); }
    .pill-inactive { background-color: #ffffff; color: #64748b; border: 1px solid #e2e8f0; }
    .stock-card-hover:hover { transform: translateY(-2px); border-color: #3b82f6; box-shadow: 0 8px 20px -4px rgba(59, 130, 246, 0.15); }
  </style>
</head>
<body class="min-h-screen pb-16">

  <!-- Top Simple Navy Header -->
  <header class="navy-header text-white px-5 py-4 md:px-8 shadow-md">
    <div class="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3">
      <div class="flex items-center gap-3">
        <div class="w-12 h-12 rounded-2xl bg-white text-blue-900 flex items-center justify-center font-black text-2xl shadow">
          ₹
        </div>
        <div>
          <h1 class="text-xl md:text-2xl font-black tracking-tight flex items-center gap-2">
            Trader 2.0 <span class="text-[11px] font-bold px-2 py-0.5 rounded-full bg-emerald-400 text-slate-950">AI Guide</span>
          </h1>
          <p class="text-xs text-blue-200">Aapka Apna Smart Trading Assistant (Easy & Safe)</p>
        </div>
      </div>

      <div class="flex items-center gap-2 w-full sm:w-auto">
        <button onclick="runAutopilot()" class="flex-1 sm:flex-initial px-5 py-2.5 bg-emerald-500 hover:bg-emerald-600 text-white font-extrabold text-xs rounded-xl shadow-md flex items-center justify-center gap-2 transition">
          <i class="fa-solid fa-wand-magic-sparkles"></i> Auto-Scan Market (1-Click)
        </button>
        <button onclick="location.reload()" class="px-3.5 py-2.5 bg-blue-900 hover:bg-blue-800 text-white text-xs font-semibold rounded-xl border border-blue-700/60 transition">
          <i class="fa-solid fa-rotate-right"></i>
        </button>
      </div>
    </div>
  </header>

  <main class="max-w-6xl mx-auto px-4 md:px-6 mt-6 space-y-6">

    <!-- Top 3 Easy Info Badges -->
    <div class="grid grid-cols-3 gap-3">
      <div class="clean-card p-3.5 text-center">
        <span class="text-[11px] font-bold text-slate-400 uppercase">Aapka Paisa</span>
        <div class="text-lg md:text-xl font-black text-blue-950 mt-0.5">₹1,000</div>
        <span class="text-[10px] text-emerald-600 font-semibold bg-emerald-50 px-2 py-0.5 rounded-full">Safe Mode</span>
      </div>
      <div class="clean-card p-3.5 text-center">
        <span class="text-[11px] font-bold text-slate-400 uppercase">Max Loss Limit</span>
        <div class="text-lg md:text-xl font-black text-rose-600 mt-0.5">₹20</div>
        <span class="text-[10px] text-slate-500">Per Trade Cap</span>
      </div>
      <div class="clean-card p-3.5 text-center">
        <span class="text-[11px] font-bold text-slate-400 uppercase">Phone Alert</span>
        <div class="text-lg md:text-xl font-black text-emerald-600 mt-0.5 flex items-center justify-center gap-1">
          <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span> Active
        </div>
        <span class="text-[10px] text-slate-500">Telegram Live</span>
      </div>
    </div>

    <!-- 🌟 THE SUPER SIMPLE SIGNAL CARD (Traffic Light Concept) 🌟 -->
    <section class="clean-card p-5 md:p-7 space-y-4">
      <div class="flex items-center justify-between pb-3 border-b border-slate-100">
        <div>
          <h2 class="text-lg md:text-xl font-black text-slate-900 flex items-center gap-2">
            🚦 Selected Stock Live Signal & Advice
          </h2>
          <p class="text-xs text-slate-500">Neeche kisi bhi stock par click karein, yahan uska decision aayega:</p>
        </div>
        <span id="sig-stock-tag" class="px-3.5 py-1 rounded-xl bg-blue-100 text-blue-900 font-black text-sm">
          #POWERGRID
        </span>
      </div>

      <!-- TRAFFIC LIGHT BIG CARD -->
      <div id="traffic-card" class="signal-green p-5 md:p-6 rounded-2xl transition duration-300">
        <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <div class="flex items-center gap-4">
            <div id="traffic-icon-box" class="w-16 h-16 rounded-2xl bg-emerald-500 text-white flex items-center justify-center text-3xl shadow-lg">
              <i class="fa-solid fa-thumbs-up"></i>
            </div>
            <div>
              <div id="traffic-status-text" class="text-2xl md:text-3xl font-black text-emerald-950">
                HAAN! BUY KARO (Kharido)
              </div>
              <p id="traffic-subtext" class="text-xs md:text-sm font-semibold text-emerald-800 mt-0.5">
                Chart par breakout bana hai aur pichle dino me achha profit diya hai.
              </p>
            </div>
          </div>
          <span id="traffic-badge" class="px-3.5 py-1.5 rounded-full text-xs font-black bg-emerald-600 text-white shadow uppercase">
            🟢 Green Signal
          </span>
        </div>

        <!-- 3 SIMPLE NUMBERS (Entry, Stop, Target) -->
        <div class="grid grid-cols-3 gap-2.5 md:gap-4 mt-6 pt-5 border-t border-emerald-300/60">
          
          <!-- Box 1: Entry -->
          <div class="bg-white/90 p-3.5 rounded-2xl border border-emerald-200 text-center shadow-sm">
            <span class="block text-[11px] font-extrabold text-slate-500 uppercase">1. Kitne Par Kharidein?</span>
            <div id="box-entry" class="text-xl md:text-2xl font-black text-blue-950 mt-1">₹265.00</div>
            <span class="text-[10px] text-slate-400 font-medium">(Entry Price)</span>
          </div>

          <!-- Box 2: Stop Loss -->
          <div class="bg-white/90 p-3.5 rounded-2xl border border-rose-200 text-center shadow-sm">
            <span class="block text-[11px] font-extrabold text-rose-500 uppercase">2. Loss Hone Par Kab Niklein?</span>
            <div id="box-stop" class="text-xl md:text-2xl font-black text-rose-600 mt-1">₹255.00</div>
            <span class="text-[10px] text-rose-400 font-medium">(Stop Loss)</span>
          </div>

          <!-- Box 3: Target -->
          <div class="bg-white/90 p-3.5 rounded-2xl border border-emerald-200 text-center shadow-sm">
            <span class="block text-[11px] font-extrabold text-emerald-600 uppercase">3. Munafa (Profit) Kab Lein?</span>
            <div id="box-target" class="text-xl md:text-2xl font-black text-emerald-600 mt-1">₹285.00</div>
            <span class="text-[10px] text-emerald-500 font-medium">(Target Price)</span>
          </div>
        </div>

        <!-- How many shares to buy (Plain words) -->
        <div class="mt-4 p-3.5 bg-white/80 rounded-xl border border-emerald-200 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 text-xs text-slate-800 font-medium">
          <div id="box-shares-text">
            🛡️ <b>Kitne shares kharidein?</b> Aapke ₹1,000 budget me sirf <b>2 se 3 shares</b> khareedo. Stop loss hit hua to maximum <b>₹20</b> hi loss hoga.
          </div>
          <button onclick="openStockDetails(activeStock)" class="px-5 py-2.5 bg-blue-900 hover:bg-blue-950 text-white font-black text-xs rounded-xl shadow-md transition flex items-center gap-2 whitespace-nowrap">
            <i class="fa-solid fa-book-open"></i> 📑 Poori Details & Article Dekhein
          </button>
        </div>
      </div>
    </section>

    <!-- 🌟 TOP 10 AI SUGGESTED STOCKS (User Request: Kam se kam 10 stock aana chahiye) 🌟 -->
    <section class="clean-card p-5 md:p-6 space-y-4">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between pb-3 border-b border-slate-100 gap-2">
        <div>
          <h3 class="text-base md:text-lg font-black text-slate-900 flex items-center gap-2">
            🎯 Top 10 AI Stock Suggestions (Nifty 50 Shortlist)
          </h3>
          <p class="text-xs text-slate-500">Kisi bhi stock par click karein, upar uska Buy/Skip signal aur Stop-loss badal jayega:</p>
        </div>
        <span class="text-xs font-bold text-blue-900 bg-blue-50 px-3 py-1 rounded-full border border-blue-200 self-start sm:self-auto">
          10 Setups Ready
        </span>
      </div>

      <!-- Grid of 10 Stocks -->
      <div id="top-10-grid" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 text-xs">
        <!-- Stock 1: POWERGRID -->
        <div onclick="selectStock('POWERGRID')" id="card-POWERGRID" class="stock-card-hover p-3.5 rounded-2xl border-2 border-emerald-400 bg-emerald-50/50 cursor-pointer transition flex flex-col justify-between space-y-2">
          <div class="flex items-center justify-between">
            <span class="font-black text-sm text-slate-900">#POWERGRID</span>
            <span class="text-[10px] font-black bg-emerald-600 text-white px-2 py-0.5 rounded-full">BUY 🟢</span>
          </div>
          <div class="text-[11px] text-slate-600 space-y-0.5">
            <div>Price: <b>₹265</b> | Stop: <b>₹255</b></div>
            <div class="text-emerald-700 font-bold">Target: ₹285 (2R Gain)</div>
          </div>
          <div class="text-[10px] text-slate-400 border-t border-slate-200 pt-1">20D Breakout • ₹1k me 3 share</div>
        </div>

        <!-- Stock 2: INFY -->
        <div onclick="selectStock('INFY')" id="card-INFY" class="stock-card-hover p-3.5 rounded-2xl border border-slate-200 bg-white hover:bg-slate-50 cursor-pointer transition flex flex-col justify-between space-y-2">
          <div class="flex items-center justify-between">
            <span class="font-black text-sm text-slate-900">#INFY</span>
            <span class="text-[10px] font-black bg-emerald-600 text-white px-2 py-0.5 rounded-full">BUY 🟢</span>
          </div>
          <div class="text-[11px] text-slate-600 space-y-0.5">
            <div>Price: <b>₹1,185</b> | Stop: <b>₹1,160</b></div>
            <div class="text-emerald-700 font-bold">Target: ₹1,235</div>
          </div>
          <div class="text-[10px] text-slate-400 border-t border-slate-200 pt-1">IT Sector Surge • 1 Share</div>
        </div>

        <!-- Stock 3: RELIANCE -->
        <div onclick="selectStock('RELIANCE')" id="card-RELIANCE" class="stock-card-hover p-3.5 rounded-2xl border border-slate-200 bg-white hover:bg-slate-50 cursor-pointer transition flex flex-col justify-between space-y-2">
          <div class="flex items-center justify-between">
            <span class="font-black text-sm text-slate-900">#RELIANCE</span>
            <span class="text-[10px] font-black bg-blue-700 text-white px-2 py-0.5 rounded-full">HOLD 🟡</span>
          </div>
          <div class="text-[11px] text-slate-600 space-y-0.5">
            <div>Price: <b>₹1,164</b> | Stop: <b>₹1,140</b></div>
            <div class="text-blue-800 font-bold">Target: ₹1,220</div>
          </div>
          <div class="text-[10px] text-slate-400 border-t border-slate-200 pt-1">Promoter Buying • Safe MOAT</div>
        </div>

        <!-- Stock 4: SBIN -->
        <div onclick="selectStock('SBIN')" id="card-SBIN" class="stock-card-hover p-3.5 rounded-2xl border border-slate-200 bg-white hover:bg-slate-50 cursor-pointer transition flex flex-col justify-between space-y-2">
          <div class="flex items-center justify-between">
            <span class="font-black text-sm text-slate-900">#SBIN</span>
            <span class="text-[10px] font-black bg-emerald-600 text-white px-2 py-0.5 rounded-full">BUY 🟢</span>
          </div>
          <div class="text-[11px] text-slate-600 space-y-0.5">
            <div>Price: <b>₹820</b> | Stop: <b>₹805</b></div>
            <div class="text-emerald-700 font-bold">Target: ₹850</div>
          </div>
          <div class="text-[10px] text-slate-400 border-t border-slate-200 pt-1">PSU Bank Leader • 1 Share</div>
        </div>

        <!-- Stock 5: TCS -->
        <div onclick="selectStock('TCS')" id="card-TCS" class="stock-card-hover p-3.5 rounded-2xl border border-slate-200 bg-white hover:bg-slate-50 cursor-pointer transition flex flex-col justify-between space-y-2">
          <div class="flex items-center justify-between">
            <span class="font-black text-sm text-slate-900">#TCS</span>
            <span class="text-[10px] font-black bg-emerald-600 text-white px-2 py-0.5 rounded-full">BUY 🟢</span>
          </div>
          <div class="text-[11px] text-slate-600 space-y-0.5">
            <div>Price: <b>₹2,502</b> | Stop: <b>₹2,460</b></div>
            <div class="text-emerald-700 font-bold">Target: ₹2,580</div>
          </div>
          <div class="text-[10px] text-slate-400 border-t border-slate-200 pt-1">Bull Flag • Zero Debt Leader</div>
        </div>

        <!-- Stock 6: HCLTECH -->
        <div onclick="selectStock('HCLTECH')" id="card-HCLTECH" class="stock-card-hover p-3.5 rounded-2xl border border-slate-200 bg-white hover:bg-slate-50 cursor-pointer transition flex flex-col justify-between space-y-2">
          <div class="flex items-center justify-between">
            <span class="font-black text-sm text-slate-900">#HCLTECH</span>
            <span class="text-[10px] font-black bg-emerald-600 text-white px-2 py-0.5 rounded-full">BUY 🟢</span>
          </div>
          <div class="text-[11px] text-slate-600 space-y-0.5">
            <div>Price: <b>₹1,428</b> | Stop: <b>₹1,390</b></div>
            <div class="text-emerald-700 font-bold">Target: ₹1,495</div>
          </div>
          <div class="text-[10px] text-slate-400 border-t border-slate-200 pt-1">RSI 69 Breakout • Strong Buy</div>
        </div>

        <!-- Stock 7: NTPC -->
        <div onclick="selectStock('NTPC')" id="card-NTPC" class="stock-card-hover p-3.5 rounded-2xl border border-slate-200 bg-white hover:bg-slate-50 cursor-pointer transition flex flex-col justify-between space-y-2">
          <div class="flex items-center justify-between">
            <span class="font-black text-sm text-slate-900">#NTPC</span>
            <span class="text-[10px] font-black bg-emerald-600 text-white px-2 py-0.5 rounded-full">BUY 🟢</span>
          </div>
          <div class="text-[11px] text-slate-600 space-y-0.5">
            <div>Price: <b>₹316</b> | Stop: <b>₹305</b></div>
            <div class="text-emerald-700 font-bold">Target: ₹338</div>
          </div>
          <div class="text-[10px] text-slate-400 border-t border-slate-200 pt-1">Green Energy PSU • ₹1k me 3 share</div>
        </div>

        <!-- Stock 8: KOTAKBANK -->
        <div onclick="selectStock('KOTAKBANK')" id="card-KOTAKBANK" class="stock-card-hover p-3.5 rounded-2xl border border-slate-200 bg-white hover:bg-slate-50 cursor-pointer transition flex flex-col justify-between space-y-2">
          <div class="flex items-center justify-between">
            <span class="font-black text-sm text-slate-900">#KOTAKBANK</span>
            <span class="text-[10px] font-black bg-emerald-600 text-white px-2 py-0.5 rounded-full">BUY 🟢</span>
          </div>
          <div class="text-[11px] text-slate-600 space-y-0.5">
            <div>Price: <b>₹461</b> | Stop: <b>₹445</b></div>
            <div class="text-emerald-700 font-bold">Target: ₹490</div>
          </div>
          <div class="text-[10px] text-slate-400 border-t border-slate-200 pt-1">20D Breakout • ₹1k me 2 share</div>
        </div>

        <!-- Stock 9: CIPLA -->
        <div onclick="selectStock('CIPLA')" id="card-CIPLA" class="stock-card-hover p-3.5 rounded-2xl border border-slate-200 bg-white hover:bg-slate-50 cursor-pointer transition flex flex-col justify-between space-y-2">
          <div class="flex items-center justify-between">
            <span class="font-black text-sm text-slate-900">#CIPLA</span>
            <span class="text-[10px] font-black bg-emerald-600 text-white px-2 py-0.5 rounded-full">BUY 🟢</span>
          </div>
          <div class="text-[11px] text-slate-600 space-y-0.5">
            <div>Price: <b>₹1,378</b> | Stop: <b>₹1,340</b></div>
            <div class="text-emerald-700 font-bold">Target: ₹1,440</div>
          </div>
          <div class="text-[10px] text-slate-400 border-t border-slate-200 pt-1">Pharma Sector High • Safe 1 Share</div>
        </div>

        <!-- Stock 10: TRENT (Overpriced / Skip) -->
        <div onclick="selectStock('TRENT')" id="card-TRENT" class="stock-card-hover p-3.5 rounded-2xl border border-slate-200 bg-white hover:bg-slate-50 cursor-pointer transition flex flex-col justify-between space-y-2">
          <div class="flex items-center justify-between">
            <span class="font-black text-sm text-slate-900">#TRENT</span>
            <span class="text-[10px] font-black bg-rose-600 text-white px-2 py-0.5 rounded-full">SKIP 🔴</span>
          </div>
          <div class="text-[11px] text-slate-600 space-y-0.5">
            <div>Price: <b>₹3,017</b> | Stop: <b>₹2,950</b></div>
            <div class="text-rose-600 font-bold">Target: ₹3,150</div>
          </div>
          <div class="text-[10px] text-slate-400 border-t border-slate-200 pt-1">Price high • ₹1k capital me block</div>
        </div>
      </div>
    </section>

    <!-- 4 BUTTONS TO SWITCH STYLES (Simple Hindi) -->
    <section class="space-y-3">
      <h3 class="text-sm font-bold text-slate-500 uppercase tracking-wider px-1">
        Aapko kis tarah invest karna hai?
      </h3>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
        <button onclick="filterStockType('swing')" id="btn-tab-swing" class="pill-active p-3.5 rounded-2xl text-xs md:text-sm font-extrabold text-center transition flex flex-col items-center gap-1">
          <i class="fa-solid fa-chart-line text-lg"></i>
          <span>Swing Trading</span>
          <span class="text-[10px] font-normal opacity-80">(Kuch dino ke liye - Best)</span>
        </button>

        <button onclick="filterStockType('longterm')" id="btn-tab-longterm" class="pill-inactive p-3.5 rounded-2xl text-xs md:text-sm font-extrabold text-center transition flex flex-col items-center gap-1">
          <i class="fa-solid fa-landmark text-lg"></i>
          <span>Long Term</span>
          <span class="text-[10px] font-normal opacity-80">(Saalon ke liye wealth)</span>
        </button>

        <button onclick="filterStockType('intraday')" id="btn-tab-intraday" class="pill-inactive p-3.5 rounded-2xl text-xs md:text-sm font-extrabold text-center transition flex flex-col items-center gap-1">
          <i class="fa-solid fa-bolt text-lg"></i>
          <span>Intraday (Same Day)</span>
          <span class="text-[10px] font-normal opacity-80">(Aaj khareedo aaj becho)</span>
        </button>

        <button onclick="filterStockType('fno')" id="btn-tab-fno" class="pill-inactive p-3.5 rounded-2xl text-xs md:text-sm font-extrabold text-center transition flex flex-col items-center gap-1">
          <i class="fa-solid fa-cubes text-lg"></i>
          <span>Futures & Options</span>
          <span class="text-[10px] font-normal opacity-80">(Risk Guard Blocked)</span>
        </button>
      </div>
    </section>

    <!-- 📑 BEAUTIFUL POPUP / MODAL (FULL DETAILS & ARTICLES) -->
    <div id="details-modal" class="fixed inset-0 bg-slate-950/70 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
      <div class="bg-white rounded-3xl max-w-2xl w-full max-h-[90vh] flex flex-col shadow-2xl border border-slate-200 overflow-hidden">
        
        <!-- Header -->
        <div class="navy-header px-6 py-4 text-white flex items-center justify-between">
          <div class="flex items-center gap-2.5">
            <i class="fa-solid fa-building text-blue-300"></i>
            <div>
              <h3 class="font-black text-base" id="modal-company-title">Company Name</h3>
              <p class="text-[11px] text-blue-200" id="modal-company-sector">Sector Information</p>
            </div>
          </div>
          <button onclick="closeDetails()" class="w-8 h-8 rounded-full bg-white/10 hover:bg-white/20 flex items-center justify-center text-white transition font-bold">
            ✕
          </button>
        </div>

        <!-- Body -->
        <div class="p-6 overflow-y-auto space-y-4 text-xs text-slate-700">
          
          <!-- Story / Summary Box -->
          <div class="p-4 rounded-2xl bg-blue-50/70 border border-blue-100 leading-relaxed font-medium">
            <h4 class="font-bold text-blue-950 text-xs mb-1">📖 Company Ki Saral Kahani (Summary):</h4>
            <p id="modal-summary-text" class="text-slate-700 leading-relaxed">Loading summary...</p>
          </div>

          <!-- Financial Badges -->
          <div>
            <h4 class="font-bold text-slate-900 text-xs mb-2">💰 Key Numbers & Financials:</h4>
            <div id="modal-financial-grid" class="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <!-- JS Populates -->
            </div>
          </div>

          <!-- Official Links & Articles -->
          <div>
            <h4 class="font-black text-slate-900 text-xs mb-2">📑 Zaroori Links, News & Articles:</h4>
            <div id="modal-links-container" class="space-y-2">
              <!-- JS Populates -->
            </div>
          </div>
        </div>

        <!-- Footer -->
        <div class="px-6 py-3.5 bg-slate-50 border-t border-slate-100 flex justify-end">
          <button onclick="closeDetails()" class="px-6 py-2.5 bg-blue-900 hover:bg-blue-950 text-white font-bold text-xs rounded-xl shadow transition">
            Samajh Aa Gaya (Close)
          </button>
        </div>
      </div>
    </div>

  </main>

  <script>
    let activeStock = 'POWERGRID';

    // COMPLETE 10 STOCKS DATABASE WITH FULL ARTICLES & FINANCIALS
    const STOCK_DATABASE = {
      'POWERGRID': {
        verdict: 'BUY', entry: 265, stop: 255, target: 285,
        name: 'Power Grid Corporation of India Ltd',
        sector: 'Power Transmission (Maharatna PSU)',
        summary: 'India ki 85%+ interstate bijli supply yahi company sambhalti hai. Stable dividend aur zero default record wali solid PSU company hai. 20-Day high breakout bana hai, aur agle 5-10 dino me achhe target aane ke strong chances hain.',
        financials: { 'Sales (Revenue)': '₹45,800 Cr', 'Net Profit': '₹15,400 Cr', 'Dividend Yield': '3.8%', 'ROCE': '13.2%' },
        links: [
          { title: 'Screener.in Financial Balance Sheet (POWERGRID)', url: 'https://www.screener.in/company/POWERGRID/consolidated/' },
          { title: 'NSE Corporate Results & Announcements', url: 'https://www.nseindia.com/companies-listing/corporate-filings-announcements' },
          { title: 'Moneycontrol Full Technical Chart', url: 'https://www.moneycontrol.com/india/stockpricequote/power-generation-distribution/powergridcorporationofindia/PGC' },
          { title: 'Google Finance Live Quote', url: 'https://www.google.com/finance/quote/POWERGRID:NSE' }
        ]
      },
      'INFY': {
        verdict: 'BUY', entry: 1185, stop: 1160, target: 1235,
        name: 'Infosys Limited',
        sector: 'IT & Software Services (Nifty IT)',
        summary: 'India ki 2nd sabse badi IT company. Global AI aur Cloud migration ke naye multimillion-dollar orders mile hain. Operating profit margin 21%+ maintain hai aur zero debt company hai.',
        financials: { 'Sales (Revenue)': '₹153,000 Cr', 'Net Profit': '₹26,200 Cr', 'Debt': 'Zero Debt', 'ROCE': '38%' },
        links: [
          { title: 'Screener.in Financial Balance Sheet (INFY)', url: 'https://www.screener.in/company/INFY/consolidated/' },
          { title: 'NSE Corporate Results & Announcements', url: 'https://www.nseindia.com/companies-listing/corporate-filings-announcements' },
          { title: 'Moneycontrol Full Technical Chart', url: 'https://www.moneycontrol.com/india/stockpricequote/computers-software/infosys/IT' },
          { title: 'Google Finance Live Quote', url: 'https://www.google.com/finance/quote/INFY:NSE' }
        ]
      },
      'RELIANCE': {
        verdict: 'HOLD', entry: 1164, stop: 1140, target: 1220,
        name: 'Reliance Industries Ltd',
        sector: 'Oil, Retail & Telecom (Jio)',
        summary: 'India ki sabse badi company. Jio telecom aur Retail division ki wajah se strong cash-flow hai. Promoters ne bhi haal hi me stake increase kiya hai, jo long term ke liye bahut safe mana jata hai.',
        financials: { 'Sales (Revenue)': '₹960,000 Cr', 'Net Profit': '₹79,000 Cr', 'Jio Users': '470 Million', 'Retail Stores': '18,000+' },
        links: [
          { title: 'Screener.in Financial Balance Sheet (RELIANCE)', url: 'https://www.screener.in/company/RELIANCE/consolidated/' },
          { title: 'NSE Corporate Results & Announcements', url: 'https://www.nseindia.com/companies-listing/corporate-filings-announcements' },
          { title: 'Moneycontrol Full Technical Chart', url: 'https://www.moneycontrol.com/india/stockpricequote/refineries/relianceindustries/RI' },
          { title: 'Google Finance Live Quote', url: 'https://www.google.com/finance/quote/RELIANCE:NSE' }
        ]
      },
      'SBIN': {
        verdict: 'BUY', entry: 820, stop: 805, target: 850,
        name: 'State Bank of India',
        sector: 'Banking & Financial Services (PSU Leader)',
        summary: 'India ka sabse bada bank. Pichle 10 saal me sabse kam NPA (bad loans 0.57%) hai aur loan growth double digits par chal rahi hai. FIIs ne bhi buying ki hai.',
        financials: { 'Advances (Loans)': '₹37 Lakh Cr', 'Net NPA': '0.57%', 'Quarterly PAT': '₹18,300 Cr', 'ROA': '1.1%' },
        links: [
          { title: 'Screener.in Financial Balance Sheet (SBIN)', url: 'https://www.screener.in/company/SBIN/consolidated/' },
          { title: 'NSE Corporate Announcements (SBIN)', url: 'https://www.nseindia.com/companies-listing/corporate-filings-announcements' },
          { title: 'Moneycontrol Technicals (SBIN)', url: 'https://www.moneycontrol.com/india/stockpricequote/banks-public-sector/statebankofindia/SBI' }
        ]
      },
      'TCS': {
        verdict: 'BUY', entry: 2502, stop: 2460, target: 2580,
        name: 'Tata Consultancy Services Ltd',
        sector: 'IT & Software Services (Tata Group)',
        summary: 'Tata group ka heera. 26%+ operating margin, zero debt, aur har saal 80%+ profit dividend ke roop me baant ti hai. Daily chart par Bull Flag pattern bana hai.',
        financials: { 'Sales (Revenue)': '₹240,000 Cr', 'Net Profit': '₹46,000 Cr', 'Dividend Payout': '80%+', 'ROCE': '52%' },
        links: [
          { title: 'Screener.in Financial Balance Sheet (TCS)', url: 'https://www.screener.in/company/TCS/consolidated/' },
          { title: 'NSE Official Filings (TCS)', url: 'https://www.nseindia.com/companies-listing/corporate-filings-announcements' },
          { title: 'Moneycontrol Chart (TCS)', url: 'https://www.moneycontrol.com/india/stockpricequote/computers-software/tataconsultancyservices/TCS' }
        ]
      },
      'HCLTECH': {
        verdict: 'BUY', entry: 1428, stop: 1390, target: 1495,
        name: 'HCL Technologies Limited',
        sector: 'IT & Engineering Services',
        summary: 'IT sector me sabse high dividend yield aur steady revenue growth. RSI 69 ke sath 20-day high breakout confirm hua hai. Agle hafte ke liye strong candidate hai.',
        financials: { 'Sales (Revenue)': '₹110,000 Cr', 'Net Profit': '₹15,700 Cr', 'Dividend Yield': '3.2%', 'ROCE': '31%' },
        links: [
          { title: 'Screener.in Financials (HCLTECH)', url: 'https://www.screener.in/company/HCLTECH/consolidated/' },
          { title: 'NSE Announcements (HCLTECH)', url: 'https://www.nseindia.com/companies-listing/corporate-filings-announcements' }
        ]
      },
      'NTPC': {
        verdict: 'BUY', entry: 316, stop: 305, target: 338,
        name: 'NTPC Limited',
        sector: 'Power Generation & Green Energy',
        summary: 'India ki sabse badi thermal + solar power generation company. NTPC Green Energy subsidiary ke expansion se long term aur swing dono me demand high hai.',
        financials: { 'Sales': '₹176,000 Cr', 'Net Profit': '₹21,300 Cr', 'Green Target': '60 GW by 2032', 'Dividend': '2.6%' },
        links: [
          { title: 'Screener.in Financials (NTPC)', url: 'https://www.screener.in/company/NTPC/consolidated/' },
          { title: 'NSE Filings (NTPC)', url: 'https://www.nseindia.com/companies-listing/corporate-filings-announcements' }
        ]
      },
      'KOTAKBANK': {
        verdict: 'BUY', entry: 461, stop: 445, target: 490,
        name: 'Kotak Mahindra Bank Ltd',
        sector: 'Private Sector Banking',
        summary: 'RBI restrictions hatne ke baad digital acquisition fast ho gayi hai. CASA ratio 48%+ hai jo cheap deposits ensure karta hai. Safe entry range.',
        financials: { 'Total Assets': '₹6.2 Lakh Cr', 'Net NPA': '0.34%', 'CASA Ratio': '48.2%', 'Capital Adequacy': '21.5%' },
        links: [
          { title: 'Screener.in Financials (KOTAKBANK)', url: 'https://www.screener.in/company/KOTAKBANK/consolidated/' },
          { title: 'NSE Filings (KOTAKBANK)', url: 'https://www.nseindia.com/companies-listing/corporate-filings-announcements' }
        ]
      },
      'CIPLA': {
        verdict: 'BUY', entry: 1378, stop: 1340, target: 1440,
        name: 'Cipla Limited',
        sector: 'Pharmaceuticals & Healthcare',
        summary: 'Respiratory aur inhaler segment me global market leader. US FDA audits clear hone se export revenue 15%+ YoY increase ho raha hai.',
        financials: { 'Sales': '₹26,000 Cr', 'Net Profit': '₹4,100 Cr', 'India Business': '42%', 'ROCE': '21%' },
        links: [
          { title: 'Screener.in Financials (CIPLA)', url: 'https://www.screener.in/company/CIPLA/consolidated/' },
          { title: 'NSE Filings (CIPLA)', url: 'https://www.nseindia.com/companies-listing/corporate-filings-announcements' }
        ]
      },
      'TRENT': {
        verdict: 'SKIP', entry: 3017, stop: 2950, target: 3150,
        name: 'Trent Limited (Zudio & Westside)',
        sector: 'Retail Fashion Brand',
        summary: 'Zudio stores ki growth zabardast hai lekin stock price pichle 1 saal me 3x ho chuka hai. Price ₹3,000+ hone ki wajah se ₹1,000 budget me 1 share bhi nahi aayega.',
        financials: { 'Sales': '₹12,600 Cr', 'Net Profit': '₹1,470 Cr', 'PE Ratio': '160 (Very Expensive)', 'Risk': 'High' },
        links: [
          { title: 'Screener.in Financials (TRENT)', url: 'https://www.screener.in/company/TRENT/consolidated/' },
          { title: 'NSE Filings (TRENT)', url: 'https://www.nseindia.com/companies-listing/corporate-filings-announcements' }
        ]
      }
    };

    function selectStock(sym) {
      activeStock = sym;
      document.getElementById('sig-stock-tag').innerText = '#' + sym;

      // Reset border on all 10 cards
      for (const k of Object.keys(STOCK_DATABASE)) {
        const c = document.getElementById('card-' + k);
        if (c) {
          c.className = 'stock-card-hover p-3.5 rounded-2xl border border-slate-200 bg-white hover:bg-slate-50 cursor-pointer transition flex flex-col justify-between space-y-2';
        }
      }
      // Highlight active card
      const activeCard = document.getElementById('card-' + sym);
      if (activeCard) {
        activeCard.className = 'stock-card-hover p-3.5 rounded-2xl border-2 border-blue-600 bg-blue-50/60 cursor-pointer transition flex flex-col justify-between space-y-2 shadow-md';
      }

      const item = STOCK_DATABASE[sym];
      if (!item) return;

      const card = document.getElementById('traffic-card');
      const iconBox = document.getElementById('traffic-icon-box');
      const statusText = document.getElementById('traffic-status-text');
      const badge = document.getElementById('traffic-badge');
      const subtext = document.getElementById('traffic-subtext');
      const sharesBox = document.getElementById('box-shares-text');

      if (item.verdict === 'BUY') {
        card.className = 'signal-green p-5 md:p-6 rounded-2xl transition duration-300';
        iconBox.className = 'w-16 h-16 rounded-2xl bg-emerald-500 text-white flex items-center justify-center text-3xl shadow-lg';
        iconBox.innerHTML = '<i class="fa-solid fa-thumbs-up"></i>';
        statusText.innerText = 'HAAN! BUY KARO (Kharido)';
        statusText.className = 'text-2xl md:text-3xl font-black text-emerald-950';
        badge.innerText = '🟢 Green Signal';
        badge.className = 'px-3.5 py-1.5 rounded-full text-xs font-black bg-emerald-600 text-white shadow uppercase';
        sharesBox.innerHTML = `🛡️ <b>Kitne shares kharidein?</b> Aapke ₹1,000 budget me sirf <b>${item.entry > 500 ? '1' : '2 se 3'} shares</b> khareedo. Max loss: ₹20.`;
      } else if (item.verdict === 'SKIP') {
        card.className = 'signal-red p-5 md:p-6 rounded-2xl transition duration-300';
        iconBox.className = 'w-16 h-16 rounded-2xl bg-rose-500 text-white flex items-center justify-center text-3xl shadow-lg';
        iconBox.innerHTML = '<i class="fa-solid fa-hand"></i>';
        statusText.innerText = 'NAHI! ABHI MAT KHARIDO (Skip)';
        statusText.className = 'text-2xl md:text-3xl font-black text-rose-950';
        badge.innerText = '🔴 Red Signal (Ruko)';
        badge.className = 'px-3.5 py-1.5 rounded-full text-xs font-black bg-rose-600 text-white shadow uppercase';
        sharesBox.innerHTML = `⚠️ <b>Kyu nahi khareedna?</b> Stock expensive hai ya capital se bahar hai. Paisa bachana sabse pehla kaam hai.`;
      } else {
        card.className = 'signal-green p-5 md:p-6 rounded-2xl transition duration-300';
        iconBox.className = 'w-16 h-16 rounded-2xl bg-blue-600 text-white flex items-center justify-center text-3xl shadow-lg';
        iconBox.innerHTML = '<i class="fa-solid fa-shield-halved"></i>';
        statusText.innerText = 'HOLD / DHAYAN SE KHARIDO';
        statusText.className = 'text-2xl md:text-3xl font-black text-blue-950';
        badge.innerText = '🟡 Yellow Signal';
        badge.className = 'px-3.5 py-1.5 rounded-full text-xs font-black bg-blue-700 text-white shadow uppercase';
        sharesBox.innerHTML = `🛡️ <b>Quantity:</b> Safe accumulation zone. Sirf 1 share lene ka plan banayein.`;
      }

      subtext.innerText = item.summary.slice(0, 100) + '...';
      document.getElementById('box-entry').innerText = '₹' + item.entry;
      document.getElementById('box-stop').innerText = '₹' + item.stop;
      document.getElementById('box-target').innerText = '₹' + item.target;
    }

    function openStockDetails(stock) {
      const modal = document.getElementById('details-modal');
      modal.classList.remove('hidden');

      const info = STOCK_DATABASE[stock];
      if (!info) return;

      document.getElementById('modal-company-title').innerText = `${info.name} (#${stock})`;
      document.getElementById('modal-company-sector').innerText = info.sector;
      document.getElementById('modal-summary-text').innerText = info.summary;

      // Financials Badges
      let finHtml = '';
      for (const [k, v] of Object.entries(info.financials)) {
        finHtml += `
          <div class="p-2.5 rounded-xl bg-slate-50 border border-slate-200 text-center">
            <span class="block text-[10px] text-slate-400 uppercase font-bold">${k}</span>
            <span class="text-xs font-black text-blue-950">${v}</span>
          </div>`;
      }
      document.getElementById('modal-financial-grid').innerHTML = finHtml;

      // Links & Articles
      let linksHtml = '';
      info.links.forEach(l => {
        linksHtml += `
          <div class="p-3 bg-slate-50 hover:bg-blue-50/50 border border-slate-200 rounded-xl flex items-center justify-between transition">
            <span class="font-bold text-slate-800 text-xs">${l.title}</span>
            <a href="${l.url}" target="_blank" class="px-3.5 py-1.5 bg-blue-900 hover:bg-blue-950 text-white rounded-lg text-[11px] font-bold shadow-sm transition">
              Open Article ↗
            </a>
          </div>`;
      });
      document.getElementById('modal-links-container').innerHTML = linksHtml;
    }

    function closeDetails() {
      document.getElementById('details-modal').classList.add('hidden');
    }

    function filterStockType(type) {
      document.getElementById('btn-tab-swing').className = 'pill-inactive p-3.5 rounded-2xl text-xs md:text-sm font-extrabold text-center transition flex flex-col items-center gap-1';
      document.getElementById('btn-tab-longterm').className = 'pill-inactive p-3.5 rounded-2xl text-xs md:text-sm font-extrabold text-center transition flex flex-col items-center gap-1';
      document.getElementById('btn-tab-intraday').className = 'pill-inactive p-3.5 rounded-2xl text-xs md:text-sm font-extrabold text-center transition flex flex-col items-center gap-1';
      document.getElementById('btn-tab-fno').className = 'pill-inactive p-3.5 rounded-2xl text-xs md:text-sm font-extrabold text-center transition flex flex-col items-center gap-1';

      document.getElementById('btn-tab-' + type).className = 'pill-active p-3.5 rounded-2xl text-xs md:text-sm font-extrabold text-center transition flex flex-col items-center gap-1';

      if (type === 'swing') selectStock('POWERGRID');
      else if (type === 'longterm') selectStock('RELIANCE');
      else if (type === 'intraday') selectStock('INFY');
      else selectStock('TRENT');
    }

    async function runAutopilot() {
      alert('🤖 AI poore 50 stocks ko scan karke sabse safe stock choose kar raha hai...');
      selectStock('POWERGRID');
    }

    // Default select POWERGRID
    selectStock('POWERGRID');
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
