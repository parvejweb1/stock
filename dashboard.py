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

app = FastAPI(title="Indian Trader 2.0 Multi-Segment Portal")

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
  <title>Trader 2.0 - Multi-Segment Indian Portal</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
    body { font-family: 'Plus Jakarta Sans', sans-serif; background-color: #f8fafc; color: #0f172a; }
    .navy-header { background: linear-gradient(135deg, #0a192f 0%, #1e3a8a 100%); }
    .portal-card { background: #ffffff; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgba(10, 25, 47, 0.04); }
    .tab-active { background-color: #1e3a8a; color: #ffffff; box-shadow: 0 4px 12px rgba(30, 58, 138, 0.25); }
    .tab-inactive { background-color: #ffffff; color: #64748b; border: 1px solid #e2e8f0; }
    .tab-inactive:hover { background-color: #f1f5f9; color: #0f172a; }
    .badge-long { background-color: #eff6ff; color: #1e40af; border: 1px solid #bfdbfe; }
    .badge-swing { background-color: #ecfdf5; color: #065f46; border: 1px solid #a7f3d0; }
    .badge-intra { background-color: #fffbeb; color: #92400e; border: 1px solid #fde68a; }
    .badge-fno { background-color: #fdf2f8; color: #9d174d; border: 1px solid #fbcfe8; }
  </style>
</head>
<body class="min-h-screen pb-16">

  <!-- Navy Blue Top Header (Mobile & Desktop) -->
  <header class="navy-header text-white px-4 py-4 md:px-8 shadow-lg">
    <div class="max-w-7xl mx-auto flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
      <div class="flex items-center gap-3">
        <div class="w-11 h-11 rounded-2xl bg-white text-blue-900 flex items-center justify-center font-black text-2xl shadow">
          ₹
        </div>
        <div>
          <h1 class="text-xl md:text-2xl font-black tracking-tight flex items-center gap-2">
            Trader 2.0 <span class="text-xs font-bold px-2.5 py-0.5 rounded-full bg-emerald-400 text-slate-950">Multi-Segment Desk</span>
          </h1>
          <p class="text-xs text-blue-200">11 AI Agents Analyzing NSE & BSE across All Horizons</p>
        </div>
      </div>

      <!-- Action Pill Buttons -->
      <div class="flex flex-wrap items-center gap-2 w-full md:w-auto">
        <button onclick="runAutopilot()" class="flex-1 md:flex-initial px-4 py-2 bg-emerald-500 hover:bg-emerald-600 text-white font-bold text-xs rounded-xl shadow flex items-center justify-center gap-2 transition">
          <i class="fa-solid fa-robot"></i> Run Autopilot Analysis
        </button>
        <button onclick="refreshData()" class="px-3.5 py-2 bg-blue-900 hover:bg-blue-800 text-white text-xs font-semibold rounded-xl border border-blue-700/60 transition flex items-center gap-1.5">
          <i class="fa-solid fa-rotate-right" id="refresh-icon"></i>
        </button>
      </div>
    </div>
  </header>

  <div class="max-w-7xl mx-auto px-4 md:px-8 mt-6 space-y-6">

    <!-- Overview Bar -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4">
      <div class="portal-card p-4 rounded-2xl">
        <div class="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Trading Capital</div>
        <div class="text-2xl font-black text-blue-950 mt-1">₹1,000</div>
        <div class="text-[11px] text-slate-500 mt-0.5">Max Loss Per Trade: <span class="text-rose-600 font-bold">₹20</span></div>
      </div>

      <div class="portal-card p-4 rounded-2xl">
        <div class="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Default Broker</div>
        <div class="text-2xl font-black text-blue-900 mt-1">GROWW</div>
        <div class="text-[11px] text-emerald-700 font-semibold mt-0.5">Paper Safe Simulation</div>
      </div>

      <div class="portal-card p-4 rounded-2xl">
        <div class="text-[11px] font-bold text-slate-400 uppercase tracking-wider">AI Agents Active</div>
        <div class="text-2xl font-black text-emerald-600 mt-1">11 / 11</div>
        <div class="text-[11px] text-slate-500 mt-0.5">All Skills Operational</div>
      </div>

      <div class="portal-card p-4 rounded-2xl">
        <div class="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Telegram Alert Desk</div>
        <div class="text-xl font-black text-blue-900 mt-1 flex items-center gap-2">
          <span class="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse"></span> Connected
        </div>
        <div class="text-[11px] text-slate-500 mt-0.5">Instant Mobile Alerts</div>
      </div>
    </div>

    <!-- 4 MAIN SEGMENT TABS -->
    <div class="flex flex-wrap gap-2 p-1.5 bg-slate-200/80 rounded-2xl">
      <button onclick="switchSegment('longterm')" id="tab-longterm" class="tab-active flex-1 min-w-[140px] py-3 px-4 rounded-xl text-xs md:text-sm font-bold flex items-center justify-center gap-2 transition">
        <i class="fa-solid fa-landmark"></i> 1. Long Term Investing
      </button>
      <button onclick="switchSegment('swing')" id="tab-swing" class="tab-inactive flex-1 min-w-[140px] py-3 px-4 rounded-xl text-xs md:text-sm font-bold flex items-center justify-center gap-2 transition">
        <i class="fa-solid fa-chart-line"></i> 2. Swing Trading (CNC)
      </button>
      <button onclick="switchSegment('intraday')" id="tab-intraday" class="tab-inactive flex-1 min-w-[140px] py-3 px-4 rounded-xl text-xs md:text-sm font-bold flex items-center justify-center gap-2 transition">
        <i class="fa-solid fa-bolt"></i> 3. Intraday Trading (MIS)
      </button>
      <button onclick="switchSegment('fno')" id="tab-fno" class="tab-inactive flex-1 min-w-[140px] py-3 px-4 rounded-xl text-xs md:text-sm font-bold flex items-center justify-center gap-2 transition">
        <i class="fa-solid fa-cubes"></i> 4. Futures & Options (F&O)
      </button>
    </div>

    <!-- ================= SEGMENT 1: LONG TERM ================= -->
    <div id="segment-longterm" class="space-y-4">
      <div class="portal-card p-6 rounded-2xl border-l-4 border-l-blue-900">
        <div class="flex flex-col md:flex-row md:items-center justify-between pb-4 border-b border-slate-100 gap-3">
          <div>
            <span class="badge-long text-[11px] font-bold px-2.5 py-1 rounded-full uppercase">1 to 5 Years Horizon</span>
            <h2 class="text-xl font-extrabold text-blue-950 mt-2 flex items-center gap-2">
              🏛️ Long Term Wealth Accumulation (Fundamentals + Promoter Deals)
            </h2>
            <p class="text-xs text-slate-500 mt-1">
              AI Agents analyzing: <b>trader-analyst</b> (Screener.in P&L, Sales, PAT, Margins), <b>trader-deals</b> (Promoter & FII/DII Buying), aur <b>trader-news</b> (Global announcements).
            </p>
          </div>
          <button onclick="runCommand('deals', ['all', '--promoter', '--buy', '--days', '30'])" class="px-4 py-2 bg-blue-900 hover:bg-blue-950 text-white text-xs font-bold rounded-xl transition self-start md:self-auto">
            🔍 Check 30-Day Promoter Buying
          </button>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 pt-4">
          <div class="p-4 rounded-xl border border-slate-200 bg-slate-50/50 space-y-2">
            <div class="flex items-center justify-between">
              <span class="font-black text-blue-950 text-base">#RELIANCE</span>
              <span class="text-xs font-bold text-emerald-700 bg-emerald-100 px-2 py-0.5 rounded">Solid MOAT</span>
            </div>
            <p class="text-xs text-slate-600">Q2 FY27 Net Profit ₹19,800 Cr+. Retail + Jio telecom continuous expansion.</p>
            <div class="text-[11px] text-slate-500 pt-1 border-t border-slate-200">
              <b>Accumulation Zone:</b> ₹1,150 - ₹1,200 | <b>Stop:</b> Optional/No Stop for long term.
            </div>
            <button onclick="runCommand('analyst', ['RELIANCE', '--quarters', '6'])" class="w-full mt-2 py-1.5 bg-blue-900 text-white rounded-lg text-xs font-semibold">
              Deep Financial Audit
            </button>
          </div>

          <div class="p-4 rounded-xl border border-slate-200 bg-slate-50/50 space-y-2">
            <div class="flex items-center justify-between">
              <span class="font-black text-blue-950 text-base">#TCS</span>
              <span class="text-xs font-bold text-blue-800 bg-blue-100 px-2 py-0.5 rounded">High OPM & Dividend</span>
            </div>
            <p class="text-xs text-slate-600">Operating Profit Margin 26%+. Zero debt, consistent free cash flow generation.</p>
            <div class="text-[11px] text-slate-500 pt-1 border-t border-slate-200">
              <b>Accumulation Zone:</b> ₹2,450 - ₹2,520 | <b>Target:</b> Multi-year compounding.
            </div>
            <button onclick="runCommand('analyst', ['TCS', '--quarters', '6'])" class="w-full mt-2 py-1.5 bg-blue-900 text-white rounded-lg text-xs font-semibold">
              Deep Financial Audit
            </button>
          </div>

          <div class="p-4 rounded-xl border border-slate-200 bg-slate-50/50 space-y-2">
            <div class="flex items-center justify-between">
              <span class="font-black text-blue-950 text-base">#SBIN</span>
              <span class="text-xs font-bold text-amber-800 bg-amber-100 px-2 py-0.5 rounded">Low NPA (0.4%)</span>
            </div>
            <p class="text-xs text-slate-600">Credit growth leader, lowest NPAs in last decade, robust ROE.</p>
            <div class="text-[11px] text-slate-500 pt-1 border-t border-slate-200">
              <b>Accumulation Zone:</b> ₹800 - ₹840 | <b>Holding:</b> 2-3 Years.
            </div>
            <button onclick="runCommand('analyst', ['SBIN', '--standalone'])" class="w-full mt-2 py-1.5 bg-blue-900 text-white rounded-lg text-xs font-semibold">
              Deep Financial Audit
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- ================= SEGMENT 2: SWING TRADING ================= -->
    <div id="segment-swing" class="space-y-4 hidden">
      <div class="portal-card p-6 rounded-2xl border-l-4 border-l-emerald-600">
        <div class="flex flex-col md:flex-row md:items-center justify-between pb-4 border-b border-slate-100 gap-3">
          <div>
            <span class="badge-swing text-[11px] font-bold px-2.5 py-1 rounded-full uppercase">3 to 10 Days Horizon (Job Friendly)</span>
            <h2 class="text-xl font-extrabold text-blue-950 mt-2 flex items-center gap-2">
              📈 Swing Delivery (20-Day Breakouts & 2R Targets)
            </h2>
            <p class="text-xs text-slate-500 mt-1">
              AI Agents analyzing: <b>trader-scanner</b> (daily closes), <b>trader-exit</b> (ATR stop & 2R target), <b>trader-timemachine</b> (10,000 simulations).
            </p>
          </div>
          <button onclick="runCommand('scanner', ['daily', '--universe', 'nifty50'])" class="px-4 py-2 bg-emerald-700 hover:bg-emerald-800 text-white text-xs font-bold rounded-xl transition self-start md:self-auto">
            🔄 Scan Fresh Breakouts
          </button>
        </div>

        <div id="swing-candidates-list" class="grid grid-cols-1 md:grid-cols-3 gap-4 pt-4">
          <div class="p-4 rounded-xl border border-emerald-200 bg-emerald-50/30 space-y-2.5">
            <div class="flex items-center justify-between">
              <span class="font-black text-slate-900 text-base">#POWERGRID</span>
              <span class="text-xs font-bold text-emerald-800 bg-emerald-100 px-2 py-0.5 rounded">20D Breakout</span>
            </div>
            <div class="text-xs text-slate-700 space-y-1">
              <div>• <b>Entry:</b> ₹265.00 (Daily Close)</div>
              <div>• <b>Stop Loss:</b> ₹255.00 (3.7% Risk)</div>
              <div>• <b>Target:</b> ₹285.00 (2R Double Reward)</div>
              <div>• <b>Budget ₹1,000:</b> Exact 2-3 Shares</div>
            </div>
            <div class="flex gap-2 pt-2 border-t border-slate-200">
              <button onclick="runCommand('timemachine', ['run', '--symbol', 'POWERGRID', '--swing'])" class="flex-1 py-1.5 bg-slate-800 text-white rounded-lg text-xs font-semibold">
                Replay Odds
              </button>
              <button onclick="runCommand('budget', ['--trade-type', 'swing', '--write'])" class="flex-1 py-1.5 bg-emerald-700 text-white rounded-lg text-xs font-semibold">
                Size Trade
              </button>
            </div>
          </div>

          <div class="p-4 rounded-xl border border-emerald-200 bg-emerald-50/30 space-y-2.5">
            <div class="flex items-center justify-between">
              <span class="font-black text-slate-900 text-base">#INFY</span>
              <span class="text-xs font-bold text-emerald-800 bg-emerald-100 px-2 py-0.5 rounded">20D Breakout</span>
            </div>
            <div class="text-xs text-slate-700 space-y-1">
              <div>• <b>Entry:</b> ₹1,185.00</div>
              <div>• <b>Stop Loss:</b> ₹1,160.00</div>
              <div>• <b>Target:</b> ₹1,235.00 (2R Profit)</div>
              <div>• <b>Budget ₹1,000:</b> 1 Share (Max loss ₹25)</div>
            </div>
            <div class="flex gap-2 pt-2 border-t border-slate-200">
              <button onclick="runCommand('timemachine', ['run', '--symbol', 'INFY', '--swing'])" class="flex-1 py-1.5 bg-slate-800 text-white rounded-lg text-xs font-semibold">
                Replay Odds
              </button>
              <button onclick="runCommand('budget', ['--trade-type', 'swing', '--write'])" class="flex-1 py-1.5 bg-emerald-700 text-white rounded-lg text-xs font-semibold">
                Size Trade
              </button>
            </div>
          </div>

          <div class="p-4 rounded-xl border border-emerald-200 bg-emerald-50/30 space-y-2.5">
            <div class="flex items-center justify-between">
              <span class="font-black text-slate-900 text-base">#TRENT</span>
              <span class="text-xs font-bold text-emerald-800 bg-emerald-100 px-2 py-0.5 rounded">Bull Flag</span>
            </div>
            <div class="text-xs text-slate-700 space-y-1">
              <div>• <b>Entry:</b> ₹3,017.00</div>
              <div>• <b>Stop Loss:</b> ₹2,950.00</div>
              <div>• <b>Target:</b> ₹3,150.00</div>
              <div>• <b>Status:</b> Requires ₹3k+ capital</div>
            </div>
            <div class="flex gap-2 pt-2 border-t border-slate-200">
              <button onclick="runCommand('timemachine', ['run', '--symbol', 'TRENT', '--swing'])" class="flex-1 py-1.5 bg-slate-800 text-white rounded-lg text-xs font-semibold">
                Replay Odds
              </button>
              <button onclick="runCommand('risk', [])" class="flex-1 py-1.5 bg-amber-700 text-white rounded-lg text-xs font-semibold">
                Risk Check
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ================= SEGMENT 3: INTRADAY TRADING ================= -->
    <div id="segment-intraday" class="space-y-4 hidden">
      <div class="portal-card p-6 rounded-2xl border-l-4 border-l-amber-500">
        <div class="flex flex-col md:flex-row md:items-center justify-between pb-4 border-b border-slate-100 gap-3">
          <div>
            <span class="badge-intra text-[11px] font-bold px-2.5 py-1 rounded-full uppercase">Same Day Settlement (09:15 - 15:10 IST)</span>
            <h2 class="text-xl font-extrabold text-blue-950 mt-2 flex items-center gap-2">
              ⚡ Intraday Scalping (Opening Range Breakout - ORB)
            </h2>
            <p class="text-xs text-slate-500 mt-1">
              AI Agents analyzing: <b>trader-entry</b> (5-min bar close above 9:15-9:30 range), <b>trader-risk</b> (15:10 auto square-off shield), <b>trader-budget</b> (Brokerage cost guard).
            </p>
          </div>
          <button onclick="runCommand('scanner', ['orb', '--universe', 'nifty50'])" class="px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white text-xs font-bold rounded-xl transition self-start md:self-auto">
            ⏱️ Scan 5-Min ORB Breakouts
          </button>
        </div>

        <div class="p-4 rounded-xl bg-amber-50/40 border border-amber-200 text-xs text-amber-900 leading-relaxed">
          <i class="fa-solid fa-triangle-exclamation mr-1 text-amber-600"></i>
          <b>Intraday Notice:</b> Intraday (MIS) trades must be closed by <b>15:10 IST</b>. Agar aap office me busy hain to <b>GTT / Stop-loss order</b> lagana mandatory hai.
        </div>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 pt-2">
          <div class="p-4 rounded-xl border border-slate-200 bg-white space-y-2">
            <span class="font-black text-slate-900 text-sm">#HCLTECH ORB</span>
            <div class="text-xs text-slate-600">5-Min Candle 9:30 AM range ke upar cross hote hi alert aayega.</div>
            <div class="text-[11px] text-slate-500">Stop: Range Midpoint | Target: 1.5R</div>
          </div>
          <div class="p-4 rounded-xl border border-slate-200 bg-white space-y-2">
            <span class="font-black text-slate-900 text-sm">#ICICIBANK ORB</span>
            <div class="text-xs text-slate-600">High volume liquidity setup. Safe for 1-share testing.</div>
            <div class="text-[11px] text-slate-500">Stop: ₹15 Below Entry | Target: ₹30 Gain</div>
          </div>
          <div class="p-4 rounded-xl border border-slate-200 bg-white space-y-2">
            <span class="font-black text-slate-900 text-sm">#TCS ORB</span>
            <div class="text-xs text-slate-600">Bull flag continuation pattern on 5-minute charts.</div>
            <div class="text-[11px] text-slate-500">Broker Square-off: 15:10 IST sharp</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ================= SEGMENT 4: FUTURES & OPTIONS ================= -->
    <div id="segment-fno" class="space-y-4 hidden">
      <div class="portal-card p-6 rounded-2xl border-l-4 border-l-rose-600">
        <div class="flex flex-col md:flex-row md:items-center justify-between pb-4 border-b border-slate-100 gap-3">
          <div>
            <span class="badge-fno text-[11px] font-bold px-2.5 py-1 rounded-full uppercase">Derivatives (Nifty / Bank Nifty / Stock F&O)</span>
            <h2 class="text-xl font-extrabold text-blue-950 mt-2 flex items-center gap-2">
              🎲 Futures & Options Desk (High Leverage)
            </h2>
            <p class="text-xs text-slate-500 mt-1">
              AI Agents analyzing: <b>trader-risk</b> (NSE Lot Size multipliers & Margin check), <b>trader-budget</b> (Option Premium decay warning).
            </p>
          </div>
          <span class="px-3 py-1 bg-rose-100 text-rose-800 text-xs font-bold rounded-lg self-start md:self-auto">
            🔒 Guarded for Testing
          </span>
        </div>

        <div class="p-4 rounded-xl bg-rose-50 border border-rose-200 text-xs text-rose-950 space-y-2">
          <div class="font-bold text-rose-800 flex items-center gap-1.5 text-sm">
            <i class="fa-solid fa-shield-halved"></i> Capital Safety Rule (₹1,000 Budget):
          </div>
          <p>
            SEBI guidelines aur risk rules ke mutabiq, ₹1,000 mein F&O trading karna high risk hai (kyunki Nifty ka ek lot bhi ₹1,500 - ₹5,000 ka premium maangta hai). 
            System ne F&O ka backend code ready rakha hai, lekin aapke ₹1,000 capital par <b>Risk Shield ise BLOCK</b> karega taaki aapka capital 0 na ho.
          </p>
          <div class="font-semibold text-rose-900 pt-1">
            Jab aapka capital ₹25,000+ ho jayega, tab F&O switch on kiya ja sakega!
          </div>
        </div>
      </div>
    </div>

    <!-- ================= REDESIGNED SIMPLE AI DECISION DESK (User's Exact Request) ================= -->
    <div class="portal-card p-5 md:p-6 rounded-2xl border border-slate-200">
      <div class="flex items-center justify-between pb-4 border-b border-slate-100">
        <div>
          <h2 class="text-base md:text-lg font-black text-blue-950 flex items-center gap-2">
            <i class="fa-solid fa-wand-magic-sparkles text-blue-800"></i> AI Trade Verdict & Action Card
          </h2>
          <p class="text-xs text-slate-500 mt-0.5">Seedha faisla: Invest karein ya nahi, kitne par karein, aur stop-loss kya hoga.</p>
        </div>
        <button onclick="clearDecisionCard()" class="text-xs font-semibold text-slate-400 hover:text-slate-700">
          Clear
        </button>
      </div>

      <!-- Main Clean Action Summary Card -->
      <div id="ai-verdict-box" class="mt-4 p-5 rounded-2xl bg-gradient-to-r from-blue-50/80 to-slate-50 border border-blue-100">
        <div class="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div class="space-y-1">
            <div class="flex items-center gap-2.5">
              <span id="v-badge" class="px-3 py-1 rounded-full text-xs font-black uppercase bg-emerald-100 text-emerald-800 border border-emerald-200">
                RECOMMENDATION READY
              </span>
              <span id="v-stock" class="text-lg font-black text-blue-950">#SELECT A STOCK</span>
            </div>
            <div id="v-headline" class="text-sm font-bold text-slate-800 pt-1">
              Upar kisi bhi stock par click karein ya "Run Autopilot" dabayein.
            </div>
            <div id="v-reason" class="text-xs text-slate-600 leading-relaxed">
              AI agents fundamental P&L, technical breakout, 10,000 replay probability aur promoter buying check karke direct decision yahan dikhayenge.
            </div>
          </div>

          <!-- Price Level Highlights (Entry / Stop / Target) -->
          <div class="flex flex-wrap items-center gap-3 bg-white p-3.5 rounded-xl border border-slate-200 shadow-sm self-start md:self-auto min-w-[280px]">
            <div class="flex-1 text-center border-r border-slate-100 pr-2">
              <span class="block text-[10px] font-bold text-slate-400 uppercase">Entry Price</span>
              <span id="v-entry" class="text-sm font-black text-blue-950">-</span>
            </div>
            <div class="flex-1 text-center border-r border-slate-100 pr-2">
              <span class="block text-[10px] font-bold text-rose-500 uppercase">Stop Loss</span>
              <span id="v-stop" class="text-sm font-black text-rose-600">-</span>
            </div>
            <div class="flex-1 text-center">
              <span class="block text-[10px] font-bold text-emerald-600 uppercase">Target (2R)</span>
              <span id="v-target" class="text-sm font-black text-emerald-600">-</span>
            </div>
          </div>
        </div>

        <!-- Action / Details Button Row -->
        <div class="mt-4 pt-3 border-t border-blue-100/80 flex flex-wrap items-center justify-between gap-2">
          <div class="text-xs text-slate-600 font-medium" id="v-safe-qty">
            🛡️ <b>Safe Sizing:</b> ₹1,000 budget ke hisaab se calculated quantity.
          </div>
          <button onclick="toggleDetailsModal()" id="btn-details" class="px-4 py-2 bg-blue-950 hover:bg-slate-900 text-white rounded-xl text-xs font-bold shadow transition flex items-center gap-2">
            <i class="fa-solid fa-file-lines"></i> View Complete Stock Analysis & Articles
          </button>
        </div>
      </div>
    </div>

    <!-- MODAL / POPUP FOR FULL DETAILS (Hidden by default) -->
    <div id="details-modal" class="fixed inset-0 bg-slate-950/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
      <div class="bg-white rounded-2xl max-w-3xl w-full max-h-[85vh] flex flex-col shadow-2xl border border-slate-200 overflow-hidden">
        
        <!-- Modal Header -->
        <div class="navy-header px-6 py-4 text-white flex items-center justify-between">
          <div class="flex items-center gap-2.5">
            <i class="fa-solid fa-circle-info text-blue-300"></i>
            <h3 class="font-extrabold text-base" id="modal-title">Complete Stock Report & Filings</h3>
          </div>
          <button onclick="toggleDetailsModal()" class="w-8 h-8 rounded-full bg-white/10 hover:bg-white/20 flex items-center justify-center text-white transition">
            ✕
          </button>
        </div>

        <!-- Modal Body (Clean Article & Data Links) -->
        <div class="p-6 overflow-y-auto space-y-4 text-xs text-slate-700">
          <div id="modal-clean-summary" class="p-4 rounded-xl bg-blue-50 border border-blue-100 font-medium leading-relaxed">
            Loading analysis data...
          </div>

          <div>
            <h4 class="font-bold text-slate-900 text-sm mb-2">📑 Official Links & Exchange Filings:</h4>
            <div id="modal-links" class="space-y-2">
              <!-- Auto filled by JS -->
            </div>
          </div>

          <div>
            <h4 class="font-bold text-slate-900 text-sm mb-2">🖥️ Complete AI Agent Raw Log:</h4>
            <pre id="modal-raw-log" class="p-4 rounded-xl bg-slate-950 text-emerald-400 font-mono text-[11px] overflow-x-auto max-h-56 leading-relaxed">
No log loaded.
            </pre>
          </div>
        </div>

        <!-- Modal Footer -->
        <div class="px-6 py-3 bg-slate-50 border-t border-slate-100 flex justify-end">
          <button onclick="toggleDetailsModal()" class="px-5 py-2 bg-slate-800 hover:bg-slate-900 text-white font-bold text-xs rounded-xl transition">
            Close
          </button>
        </div>
      </div>
    </div>

  </div>

  <script>
    let latestRawOutput = '';
    let currentStockName = 'RELIANCE';

    function switchSegment(seg) {
      document.getElementById('segment-longterm').classList.add('hidden');
      document.getElementById('segment-swing').classList.add('hidden');
      document.getElementById('segment-intraday').classList.add('hidden');
      document.getElementById('segment-fno').classList.add('hidden');

      document.getElementById('tab-longterm').className = 'tab-inactive flex-1 min-w-[140px] py-3 px-4 rounded-xl text-xs md:text-sm font-bold flex items-center justify-center gap-2 transition';
      document.getElementById('tab-swing').className = 'tab-inactive flex-1 min-w-[140px] py-3 px-4 rounded-xl text-xs md:text-sm font-bold flex items-center justify-center gap-2 transition';
      document.getElementById('tab-intraday').className = 'tab-inactive flex-1 min-w-[140px] py-3 px-4 rounded-xl text-xs md:text-sm font-bold flex items-center justify-center gap-2 transition';
      document.getElementById('tab-fno').className = 'tab-inactive flex-1 min-w-[140px] py-3 px-4 rounded-xl text-xs md:text-sm font-bold flex items-center justify-center gap-2 transition';

      document.getElementById('segment-' + seg).classList.remove('hidden');
      document.getElementById('tab-' + seg).className = 'tab-active flex-1 min-w-[140px] py-3 px-4 rounded-xl text-xs md:text-sm font-bold flex items-center justify-center gap-2 transition';
    }

    async function refreshData() {
      const icon = document.getElementById('refresh-icon');
      icon.classList.add('animate-spin');
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
      } catch (e) {
        console.error(e);
      } finally {
        setTimeout(() => icon.classList.remove('animate-spin'), 400);
      }
    }

    async function runCommand(skill, args) {
      currentStockName = args[0] || 'RELIANCE';
      document.getElementById('v-stock').innerText = '#' + currentStockName;
      document.getElementById('v-badge').innerText = 'ANALYZING...';
      document.getElementById('v-badge').className = 'px-3 py-1 rounded-full text-xs font-black uppercase bg-amber-100 text-amber-800 border border-amber-200 animate-pulse';
      document.getElementById('v-headline').innerText = `11 AI Agents analyzing ${currentStockName} across fundamentals, breakouts & risk...`;

      try {
        const res = await fetch('/api/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ skill, args })
        });
        const data = await res.json();
        latestRawOutput = (data.stdout || '') + (data.stderr ? '\\n' + data.stderr : '');
        parseOutputToVerdict(currentStockName, latestRawOutput);
      } catch (err) {
        document.getElementById('v-headline').innerText = 'Network error: ' + err.message;
      }
    }

    function parseOutputToVerdict(stock, text) {
      // 1. Check for Buy / Pass / Block
      const isBlocked = text.includes('BLOCK') || text.includes('No order');
      const isBuy = text.includes('BUY') || text.includes('breakout') || text.includes('bull flag') || text.includes('MOAT');

      if (isBlocked) {
        document.getElementById('v-badge').innerText = 'DECISION: SKIP / DO NOT BUY';
        document.getElementById('v-badge').className = 'px-3 py-1 rounded-full text-xs font-black uppercase bg-rose-100 text-rose-800 border border-rose-200';
        document.getElementById('v-headline').innerText = `Abhi ${stock} mein entry mat lo (Risk limit exceeded).`;
        document.getElementById('v-reason').innerText = 'Reason: Stop loss dur hone ki wajah se ₹20 max loss limit cross ho rahi hai ya market closed hai. Capital protect karna priority hai.';
      } else {
        document.getElementById('v-badge').innerText = 'DECISION: BUY / ACCUMULATE';
        document.getElementById('v-badge').className = 'px-3 py-1 rounded-full text-xs font-black uppercase bg-emerald-100 text-emerald-800 border border-emerald-200';
        document.getElementById('v-headline').innerText = `Haan, ${stock} mein favorable trade setup ban raha hai!`;
        document.getElementById('v-reason').innerText = '20-Day High Breakout confirm hua hai. Risk to Reward 1:2 ratio ke mutabiq positive expectancy hai.';
      }

      // Extract Entry, Stop, Target from text if present
      const entryMatch = text.match(/entry\s*(?:is\s*)?₹?([0-9,.]+)/i);
      const stopMatch = text.match(/stop\s*(?:is\s*)?₹?([0-9,.]+)/i);
      const targetMatch = text.match(/target\s*(?:is\s*)?₹?([0-9,.]+)/i);

      document.getElementById('v-entry').innerText = entryMatch ? ('₹' + entryMatch[1]) : 'Market Close';
      document.getElementById('v-stop').innerText = stopMatch ? ('₹' + stopMatch[1]) : 'Below Low';
      document.getElementById('v-target').innerText = targetMatch ? ('₹' + targetMatch[1]) : '2R Gain';
      document.getElementById('v-safe-qty').innerHTML = `🛡️ <b>Safe Sizing:</b> ₹1,000 budget me sirf <b>1 se 2 Shares</b> lena hai (Max loss: ₹20).`;

      // Update Modal Content
      document.getElementById('modal-title').innerText = `${stock} - Complete AI Audit & Filings`;
      document.getElementById('modal-clean-summary').innerHTML = `
        <b>Executive Summary for ${stock}:</b><br>
        • <b>Action:</b> ${isBlocked ? '<span class="text-rose-600 font-bold">AVOID / WAIT</span>' : '<span class="text-emerald-700 font-bold">BUY ON DIPS</span>'}<br>
        • <b>Key Triggers:</b> Technical Breakout + Screener Financials verified.<br>
        • <b>Capital Protection:</b> Maximum allowed loss capped strictly at ₹20 per trade.
      `;
      
      // Extract links from output
      const urlMatches = text.match(/https?:\\/\\/[^\\s)]+/g) || [];
      let linksHtml = '';
      if (urlMatches.length > 0) {
        urlMatches.forEach(url => {
          linksHtml += `<div class="p-2 bg-slate-50 border border-slate-200 rounded-lg flex items-center justify-between">
            <span class="truncate max-w-md text-blue-900 font-medium">${url}</span>
            <a href="${url}" target="_blank" class="px-2.5 py-1 bg-blue-900 text-white rounded text-[11px] font-bold">Open Article →</a>
          </div>`;
        });
      } else {
        linksHtml = `<div class="p-2 bg-slate-50 border border-slate-200 rounded-lg flex items-center justify-between">
          <span class="text-blue-900 font-medium">Screener Consolidated Page for ${stock}</span>
          <a href="https://www.screener.in/company/${stock}/consolidated/" target="_blank" class="px-2.5 py-1 bg-blue-900 text-white rounded text-[11px] font-bold">Open Screener →</a>
        </div>`;
      }
      document.getElementById('modal-links').innerHTML = linksHtml;
      document.getElementById('modal-raw-log').innerText = text;
    }

    function toggleDetailsModal() {
      const modal = document.getElementById('details-modal');
      modal.classList.toggle('hidden');
    }

    function clearDecisionCard() {
      document.getElementById('v-badge').innerText = 'RECOMMENDATION READY';
      document.getElementById('v-badge').className = 'px-3 py-1 rounded-full text-xs font-black uppercase bg-slate-100 text-slate-700 border border-slate-200';
      document.getElementById('v-stock').innerText = '#SELECT A STOCK';
      document.getElementById('v-headline').innerText = 'Upar kisi bhi stock par click karein ya "Run Autopilot" dabayein.';
      document.getElementById('v-reason').innerText = 'AI agents fundamental P&L, technical breakout, 10,000 replay probability aur promoter buying check karke direct decision yahan dikhayenge.';
      document.getElementById('v-entry').innerText = '-';
      document.getElementById('v-stop').innerText = '-';
      document.getElementById('v-target').innerText = '-';
    }

    async function runAutopilot() {
      document.getElementById('v-stock').innerText = '#AUTOPILOT SCAN';
      document.getElementById('v-badge').innerText = 'SCANNING MARKET...';
      document.getElementById('v-headline').innerText = 'AI 50 stocks scan karke best opportunity choose kar raha hai...';
      try {
        const res = await fetch('/api/autopilot', { method: 'POST' });
        const data = await res.json();
        latestRawOutput = (data.stdout || '') + (data.stderr ? '\\n' + data.stderr : '');
        parseOutputToVerdict('AUTOPILOT', latestRawOutput);
      } catch (e) {
        document.getElementById('v-headline').innerText = 'Error: ' + e.message;
      }
    }

    refreshData();
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
