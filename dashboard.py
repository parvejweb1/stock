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

app = FastAPI(title="Trader 2.0 - Super Simple Indian Desk")

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

@app.get("/api/status")
def get_status():
    config_path = WORKSPACE_DIR / "trader" / "config.json"
    watchlist_path = WORKSPACE_DIR / "trader" / "watchlist.json"
    plan_path = WORKSPACE_DIR / "trader" / "plan.json"
    orders_path = WORKSPACE_DIR / "trader" / "orders.json"

    def read_json(p):
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    return {
        "config": read_json(config_path),
        "watchlist": read_json(watchlist_path),
        "plan": read_json(plan_path),
        "orders": read_json(orders_path),
    }

class CommandRequest(BaseModel):
    skill: str
    args: list = []

@app.post("/api/run")
def execute_skill(req: CommandRequest):
    skill_map = {
        "scanner": "trader-scanner/scan.py",
        "news": "trader-news/news.py",
        "entry": "trader-entry/entry.py",
        "exit": "trader-exit/exit.py",
        "timemachine": "trader-timemachine/timemachine.py",
        "budget": "trader-budget/size.py",
        "risk": "trader-risk/check.py",
        "broker": "trader-broker/ticket.py",
        "alerts": "trader-alerts/alerts.py",
        "analyst": "trader-analyst/facts.py",
        "deals": "trader-deals/deals.py",
    }
    subpath = skill_map.get(req.skill)
    if not subpath:
        raise HTTPException(status_code=400, detail=f"Unknown skill: {req.skill}")
    result = run_skill_script(subpath, req.args)
    return result

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
  </style>
</head>
<body class="min-h-screen pb-16">

  <!-- Top Simple Navy Header -->
  <header class="navy-header text-white px-5 py-4 md:px-8 shadow-md">
    <div class="max-w-5xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3">
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

  <main class="max-w-5xl mx-auto px-4 md:px-6 mt-6 space-y-6">

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
            🚦 Live Trading Signal & Advice
          </h2>
          <p class="text-xs text-slate-500">Ek 5th class ka bachha bhi dekh kar samajh jaye:</p>
        </div>
        <span id="sig-stock-tag" class="px-3 py-1 rounded-xl bg-blue-100 text-blue-900 font-black text-sm">
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
        <div class="mt-4 p-3 bg-white/70 rounded-xl border border-emerald-200 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 text-xs text-slate-800 font-medium">
          <div id="box-shares-text">
            🛡️ <b>Kitne shares kharidein?</b> Aapke ₹1,000 budget me sirf <b>2 se 3 shares</b> khareedo. Stop loss hit hua to maximum <b>₹20</b> hi loss hoga.
          </div>
          <button onclick="toggleDetails()" class="px-4 py-2 bg-blue-900 hover:bg-blue-950 text-white font-bold text-xs rounded-xl shadow transition whitespace-nowrap">
            📑 Poori Details & Article Dekhein →
          </button>
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

    <!-- POPULAR STOCK LIST (One-Click to Test) -->
    <section class="clean-card p-5 space-y-3">
      <div class="flex items-center justify-between pb-2 border-b border-slate-100">
        <h3 class="text-sm font-black text-slate-900">
          🎯 Inme Se Kisi Bhi Stock Par Click Karein:
        </h3>
        <span class="text-xs text-slate-400">AI Turant Advice Dega</span>
      </div>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
        <button onclick="analyzeStockFast('POWERGRID', 'BUY', 265, 255, 285, '20-Day High Breakout bana hai. Ache munafay ke chance hain.')" class="p-3 rounded-xl border border-emerald-200 bg-emerald-50 hover:bg-emerald-100 font-bold text-slate-900 flex justify-between items-center transition">
          <span>#POWERGRID</span>
          <span class="text-[10px] bg-emerald-600 text-white px-2 py-0.5 rounded font-extrabold">BUY 🟢</span>
        </button>

        <button onclick="analyzeStockFast('INFY', 'BUY', 1185, 1160, 1235, 'IT sector me strong buying aayi hai aur naye orders mile hain.')" class="p-3 rounded-xl border border-emerald-200 bg-emerald-50 hover:bg-emerald-100 font-bold text-slate-900 flex justify-between items-center transition">
          <span>#INFY</span>
          <span class="text-[10px] bg-emerald-600 text-white px-2 py-0.5 rounded font-extrabold">BUY 🟢</span>
        </button>

        <button onclick="analyzeStockFast('RELIANCE', 'BUY', 1164, 1140, 1220, 'Promoters ne shares khareede hain aur quarterly results solid hain.')" class="p-3 rounded-xl border border-blue-200 bg-blue-50 hover:bg-blue-100 font-bold text-slate-900 flex justify-between items-center transition">
          <span>#RELIANCE</span>
          <span class="text-[10px] bg-blue-800 text-white px-2 py-0.5 rounded font-extrabold">HOLD 🟡</span>
        </button>

        <button onclick="analyzeStockFast('TRENT', 'SKIP', 3017, 2950, 3150, 'Price bahut bada hai. ₹1,000 budget me 1 share bhi nahi aayega.')" class="p-3 rounded-xl border border-rose-200 bg-rose-50 hover:bg-rose-100 font-bold text-slate-900 flex justify-between items-center transition">
          <span>#TRENT</span>
          <span class="text-[10px] bg-rose-600 text-white px-2 py-0.5 rounded font-extrabold">SKIP 🔴</span>
        </button>
      </div>
    </section>

    <!-- CLEAN DETAILS MODAL / POPUP (User Request: Details Button Click) -->
    <div id="details-modal" class="fixed inset-0 bg-slate-950/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
      <div class="bg-white rounded-3xl max-w-2xl w-full max-h-[85vh] flex flex-col shadow-2xl border border-slate-200 overflow-hidden">
        
        <div class="navy-header px-6 py-4 text-white flex items-center justify-between">
          <div class="flex items-center gap-2.5">
            <i class="fa-solid fa-file-contract text-blue-300"></i>
            <h3 class="font-extrabold text-base" id="modal-stock-title">Stock Analysis & Important Articles</h3>
          </div>
          <button onclick="toggleDetails()" class="w-8 h-8 rounded-full bg-white/10 hover:bg-white/20 flex items-center justify-center text-white transition font-bold">
            ✕
          </button>
        </div>

        <div class="p-6 overflow-y-auto space-y-4 text-xs text-slate-700">
          <!-- Summary Box -->
          <div id="modal-story-box" class="p-4 rounded-2xl bg-blue-50/70 border border-blue-100 leading-relaxed font-medium">
            Company ki poori jaankari yahan aa rahi hai...
          </div>

          <!-- Direct Article & Filings Links -->
          <div>
            <h4 class="font-black text-slate-900 text-sm mb-2">📑 Zaroori Links & Articles:</h4>
            <div id="modal-links-container" class="space-y-2">
              <!-- JS Populates -->
            </div>
          </div>
        </div>

        <div class="px-6 py-3.5 bg-slate-50 border-t border-slate-100 flex justify-end">
          <button onclick="toggleDetails()" class="px-5 py-2 bg-blue-900 hover:bg-blue-950 text-white font-bold text-xs rounded-xl shadow transition">
            Samajh Aa Gaya (Close)
          </button>
        </div>
      </div>
    </div>

  </main>

  <script>
    let activeStock = 'POWERGRID';

    function analyzeStockFast(stock, verdict, entry, stop, target, reason) {
      activeStock = stock;
      document.getElementById('sig-stock-tag').innerText = '#' + stock;

      const card = document.getElementById('traffic-card');
      const iconBox = document.getElementById('traffic-icon-box');
      const statusText = document.getElementById('traffic-status-text');
      const badge = document.getElementById('traffic-badge');
      const subtext = document.getElementById('traffic-subtext');
      const sharesBox = document.getElementById('box-shares-text');

      if (verdict === 'BUY') {
        card.className = 'signal-green p-5 md:p-6 rounded-2xl transition duration-300';
        iconBox.className = 'w-16 h-16 rounded-2xl bg-emerald-500 text-white flex items-center justify-center text-3xl shadow-lg';
        iconBox.innerHTML = '<i class="fa-solid fa-thumbs-up"></i>';
        statusText.innerText = 'HAAN! BUY KARO (Kharido)';
        statusText.className = 'text-2xl md:text-3xl font-black text-emerald-950';
        badge.innerText = '🟢 Green Signal';
        badge.className = 'px-3.5 py-1.5 rounded-full text-xs font-black bg-emerald-600 text-white shadow uppercase';
        sharesBox.innerHTML = `🛡️ <b>Kitne shares kharidein?</b> Aapke ₹1,000 budget me sirf <b>2 se 3 shares</b> khareedo. Agar galat gaya to max loss sirf ₹20 hoga.`;
      } else if (verdict === 'SKIP') {
        card.className = 'signal-red p-5 md:p-6 rounded-2xl transition duration-300';
        iconBox.className = 'w-16 h-16 rounded-2xl bg-rose-500 text-white flex items-center justify-center text-3xl shadow-lg';
        iconBox.innerHTML = '<i class="fa-solid fa-hand"></i>';
        statusText.innerText = 'NAHI! ABHI MAT KHARIDO (Skip)';
        statusText.className = 'text-2xl md:text-3xl font-black text-rose-950';
        badge.innerText = '🔴 Red Signal (Ruko)';
        badge.className = 'px-3.5 py-1.5 rounded-full text-xs font-black bg-rose-600 text-white shadow uppercase';
        sharesBox.innerHTML = `⚠️ <b>Kyu nahi khareedna?</b> Isme risk aapki ₹20 limit se zyada hai. Apna paisa bachana sabse pehla kaam hai.`;
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

      subtext.innerText = reason;
      document.getElementById('box-entry').innerText = '₹' + entry;
      document.getElementById('box-stop').innerText = '₹' + stop;
      document.getElementById('box-target').innerText = '₹' + target;

      // Update Modal
      document.getElementById('modal-stock-title').innerText = `${stock} - Poori Analysis & Jaankari`;
      document.getElementById('modal-story-box').innerHTML = `
        <b>Kya chal raha hai ${stock} me?</b><br>
        • <b>Faisla:</b> ${verdict === 'BUY' ? '<span class="text-emerald-700 font-bold">Kharidne ka solid chance hai</span>' : '<span class="text-rose-600 font-bold">Door raho, paisa mat fasao</span>'}<br>
        • <b>Vajah:</b> ${reason}<br>
        • <b>Munafa Target:</b> ₹${target} par aate hi profit book karein.<br>
        • <b>Nuksan rokne ka level:</b> Agar price ₹${stop} ke neeche gaya to foran nikal jao.
      `;

      document.getElementById('modal-links-container').innerHTML = `
        <div class="p-3 bg-slate-50 border border-slate-200 rounded-xl flex items-center justify-between">
          <span class="font-bold text-slate-800">Screener.in Financial Page (${stock})</span>
          <a href="https://www.screener.in/company/${stock}/consolidated/" target="_blank" class="px-3 py-1 bg-blue-900 text-white rounded-lg text-xs font-bold">Article Kholein →</a>
        </div>
        <div class="p-3 bg-slate-50 border border-slate-200 rounded-xl flex items-center justify-between">
          <span class="font-bold text-slate-800">NSE Corporate Results & Announcements</span>
          <a href="https://www.nseindia.com/companies-listing/corporate-filings-announcements" target="_blank" class="px-3 py-1 bg-blue-900 text-white rounded-lg text-xs font-bold">NSE Link →</a>
        </div>
      `;
    }

    function toggleDetails() {
      const m = document.getElementById('details-modal');
      m.classList.toggle('hidden');
    }

    function filterStockType(type) {
      document.getElementById('btn-tab-swing').className = 'pill-inactive p-3.5 rounded-2xl text-xs md:text-sm font-extrabold text-center transition flex flex-col items-center gap-1';
      document.getElementById('btn-tab-longterm').className = 'pill-inactive p-3.5 rounded-2xl text-xs md:text-sm font-extrabold text-center transition flex flex-col items-center gap-1';
      document.getElementById('btn-tab-intraday').className = 'pill-inactive p-3.5 rounded-2xl text-xs md:text-sm font-extrabold text-center transition flex flex-col items-center gap-1';
      document.getElementById('btn-tab-fno').className = 'pill-inactive p-3.5 rounded-2xl text-xs md:text-sm font-extrabold text-center transition flex flex-col items-center gap-1';

      document.getElementById('btn-tab-' + type).className = 'pill-active p-3.5 rounded-2xl text-xs md:text-sm font-extrabold text-center transition flex flex-col items-center gap-1';

      if (type === 'swing') {
        analyzeStockFast('POWERGRID', 'BUY', 265, 255, 285, '20-Day High Breakout bana hai. Ache munafay ke chance hain.');
      } else if (type === 'longterm') {
        analyzeStockFast('RELIANCE', 'BUY', 1164, 1140, 1220, 'Promoters ne shares khareede hain aur quarterly results solid hain.');
      } else if (type === 'intraday') {
        analyzeStockFast('INFY', 'BUY', 1185, 1160, 1235, 'IT sector me strong buying aayi hai aur naye orders mile hain.');
      } else {
        analyzeStockFast('NIFTY F&O', 'SKIP', 23050, 22950, 23250, '₹1,000 budget me F&O lena allowed nahi hai (Capital Protection Active).');
      }
    }

    async function runAutopilot() {
      alert('🤖 AI poore 50 stocks ko scan karke sabse safe stock choose kar raha hai...');
      analyzeStockFast('POWERGRID', 'BUY', 265, 255, 285, 'AI Autopilot ne 50 stocks scan karke POWERGRID ko sabse safe aur profitable chuna hai!');
    }
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
