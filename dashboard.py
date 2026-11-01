import os
import sys
import json
import subprocess
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Indian Day Trader 2.0 Web UI")

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
    portfolio_path = WORKSPACE_DIR / "trader" / "portfolio.json"
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
        "portfolio": read_json(portfolio_path),
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

class ConfigUpdateRequest(BaseModel):
    budget: float
    max_loss_per_trade_inr: float
    max_daily_loss_inr: float
    broker: str
    trade_type: str

@app.post("/api/config")
def update_config(req: ConfigUpdateRequest):
    config_path = WORKSPACE_DIR / "trader" / "config.json"
    data = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    data["budget"] = req.budget
    data["max_loss_per_trade_inr"] = req.max_loss_per_trade_inr
    data["max_daily_loss_inr"] = req.max_daily_loss_inr
    data["broker"] = req.broker
    data["trade_type"] = req.trade_type
    data["product"] = "MIS" if req.trade_type == "intraday" else "CNC"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return {"status": "ok", "config": data}

@app.get("/", response_class=HTMLResponse)
def get_dashboard():
    html_content = """<!DOCTYPE html>
<html lang="hi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Trader 2.0 - India Desk</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    body { font-family: 'Inter', sans-serif; background-color: #f8fafc; color: #0f172a; }
    .navy-header { background: linear-gradient(135deg, #0a192f 0%, #1e3a8a 100%); }
    .navy-card { background-color: #ffffff; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgba(10, 25, 47, 0.05); }
    .navy-btn { background-color: #1e3a8a; color: #ffffff; }
    .navy-btn:hover { background-color: #0a192f; }
    .stock-badge { background-color: #eff6ff; color: #1e3a8a; border: 1px solid #bfdbfe; }
    .card-hover:hover { transform: translateY(-2px); box-shadow: 0 10px 15px -3px rgba(30, 58, 138, 0.1); border-color: #93c5fd; }
  </style>
</head>
<body class="min-h-screen pb-12">

  <!-- Navy Blue Top Header (Mobile Optimized) -->
  <header class="navy-header text-white px-4 py-4 md:px-8 shadow-md">
    <div class="max-w-7xl mx-auto flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-white text-blue-900 flex items-center justify-center font-black text-xl shadow">
          ₹
        </div>
        <div>
          <h1 class="text-xl md:text-2xl font-extrabold tracking-tight flex items-center gap-2">
            Trader 2.0 <span class="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-emerald-400 text-slate-950 font-bold">NSE / BSE</span>
          </h1>
          <p class="text-xs text-blue-200">AI Stock Suggestion & Automated Trading Desk</p>
        </div>
      </div>

      <!-- Action Pill Buttons -->
      <div class="flex flex-wrap items-center gap-2 w-full md:w-auto">
        <button onclick="runAutopilot()" class="flex-1 md:flex-initial px-4 py-2 bg-emerald-500 hover:bg-emerald-600 text-white font-bold text-xs rounded-xl shadow flex items-center justify-center gap-2 transition">
          <i class="fa-solid fa-robot"></i> Run Autopilot (Suggest & Plan)
        </button>
        <button onclick="refreshData()" class="px-3.5 py-2 bg-blue-900 hover:bg-blue-800 text-white text-xs font-semibold rounded-xl border border-blue-700/60 transition flex items-center gap-1.5">
          <i class="fa-solid fa-rotate-right" id="refresh-icon"></i>
        </button>
      </div>
    </div>
  </header>

  <div class="max-w-7xl mx-auto px-4 md:px-8 mt-6 space-y-6">

    <!-- Overview Bar (Clean White Cards) -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4">
      <div class="navy-card p-4 rounded-2xl">
        <div class="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Aapka Budget</div>
        <div id="card-budget" class="text-2xl font-black text-blue-950 mt-1">₹1,000</div>
        <div class="text-[11px] text-slate-500 mt-0.5">Max loss: <span id="card-max-loss" class="text-rose-600 font-bold">₹20</span></div>
      </div>

      <div class="navy-card p-4 rounded-2xl">
        <div class="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Active Broker</div>
        <div id="card-broker" class="text-2xl font-black text-blue-900 mt-1">GROWW</div>
        <div class="text-[11px] text-emerald-700 font-medium mt-0.5">Paper Safe Mode Active</div>
      </div>

      <div class="navy-card p-4 rounded-2xl">
        <div class="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Trading Style</div>
        <div id="card-trade-type" class="text-xl font-black text-slate-800 mt-1">Swing (CNC)</div>
        <div class="text-[11px] text-blue-600 mt-0.5">Job Friendly Mode</div>
      </div>

      <div class="navy-card p-4 rounded-2xl">
        <div class="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Phone Alerts</div>
        <div class="text-xl font-black text-emerald-600 mt-1 flex items-center gap-1.5">
          <span class="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse"></span> Connected
        </div>
        <div class="text-[11px] text-slate-500 mt-0.5">Telegram Alerts Live</div>
      </div>
    </div>

    <!-- AI Stock Recommendations Box (The Magic Section) -->
    <div class="navy-card p-5 rounded-2xl border-l-4 border-l-blue-900">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between pb-3 border-b border-slate-100 gap-2">
        <div>
          <h2 class="text-base font-extrabold text-blue-950 flex items-center gap-2">
            <i class="fa-solid fa-bullseye text-blue-700"></i> AI Suggested Stocks For You
          </h2>
          <p class="text-xs text-slate-500 mt-0.5">Nifty 50 scan se filtered best technical breakout setups (Click to test):</p>
        </div>
        <button onclick="runCommand('scanner', ['daily', '--universe', 'nifty50'])" class="text-xs font-bold text-blue-700 hover:text-blue-900 bg-blue-50 hover:bg-blue-100 px-3 py-1.5 rounded-lg border border-blue-200 transition self-start sm:self-auto">
          🔄 Scan Fresh Stocks
        </button>
      </div>

      <div id="suggestions-container" class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3 pt-3">
        <div class="text-xs text-slate-400 py-3">Scanning setups...</div>
      </div>
    </div>

    <!-- Action Grid (White & Navy Style) -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
      
      <!-- 1. Scan -->
      <div class="navy-card card-hover p-5 rounded-2xl cursor-pointer transition" onclick="runCommand('scanner', ['daily', '--universe', 'nifty50'])">
        <div class="w-10 h-10 rounded-xl bg-blue-100 text-blue-900 flex items-center justify-center font-bold text-lg mb-3">
          <i class="fa-solid fa-magnifying-glass-chart"></i>
        </div>
        <h3 class="font-bold text-slate-900 text-base">1. Market Scan</h3>
        <p class="text-xs text-slate-500 mt-1">20-day high breakouts aur bull flag stocks dhoondhein.</p>
        <span class="inline-block mt-3 text-xs font-bold text-blue-800">Scan Nifty 50 →</span>
      </div>

      <!-- 2. Smart Money -->
      <div class="navy-card card-hover p-5 rounded-2xl cursor-pointer transition" onclick="runCommand('deals', ['all', '--promoter', '--buy', '--days', '7'])">
        <div class="w-10 h-10 rounded-xl bg-emerald-100 text-emerald-800 flex items-center justify-center font-bold text-lg mb-3">
          <i class="fa-solid fa-user-tie"></i>
        </div>
        <h3 class="font-bold text-slate-900 text-base">2. Promoter Buying</h3>
        <p class="text-xs text-slate-500 mt-1">SEBI filings se dekhein promoters ne kitna maal khareeda.</p>
        <span class="inline-block mt-3 text-xs font-bold text-emerald-700">Check Smart Money →</span>
      </div>

      <!-- 3. News & Impact -->
      <div class="navy-card card-hover p-5 rounded-2xl cursor-pointer transition" onclick="runCommand('news', ['--tickers', 'RELIANCE', 'TCS', 'HDFCBANK', 'SBIN', '--hours', '48'])">
        <div class="w-10 h-10 rounded-xl bg-amber-100 text-amber-900 flex items-center justify-center font-bold text-lg mb-3">
          <i class="fa-solid fa-newspaper"></i>
        </div>
        <h3 class="font-bold text-slate-900 text-base">3. News & Announcements</h3>
        <p class="text-xs text-slate-500 mt-1">Earnings results aur exchange announcements padhein.</p>
        <span class="inline-block mt-3 text-xs font-bold text-amber-800">Read News →</span>
      </div>

      <!-- 4. Earnings Analyst -->
      <div class="navy-card card-hover p-5 rounded-2xl cursor-pointer transition" onclick="quickAnalyze()">
        <div class="w-10 h-10 rounded-xl bg-purple-100 text-purple-900 flex items-center justify-center font-bold text-lg mb-3">
          <i class="fa-solid fa-chart-pie"></i>
        </div>
        <h3 class="font-bold text-slate-900 text-base">4. Company Financials</h3>
        <p class="text-xs text-slate-500 mt-1">Screener se quarterly profit, sales aur EPS margin check karein.</p>
        <span class="inline-block mt-3 text-xs font-bold text-purple-800">Analyze Stock →</span>
      </div>

      <!-- 5. Time Machine -->
      <div class="navy-card card-hover p-5 rounded-2xl cursor-pointer transition" onclick="quickTimeMachine()">
        <div class="w-10 h-10 rounded-xl bg-rose-100 text-rose-900 flex items-center justify-center font-bold text-lg mb-3">
          <i class="fa-solid fa-clock-rotate-left"></i>
        </div>
        <h3 class="font-bold text-slate-900 text-base">5. 10,000 Replays (TimeMachine)</h3>
        <p class="text-xs text-slate-500 mt-1">Trade lene se pehle 10,000 simulations se probability janein.</p>
        <span class="inline-block mt-3 text-xs font-bold text-rose-800">Replay Odds →</span>
      </div>

      <!-- 6. Order Simulator -->
      <div class="navy-card card-hover p-5 rounded-2xl cursor-pointer transition" onclick="runCommand('broker', ['show'])">
        <div class="w-10 h-10 rounded-xl bg-slate-100 text-slate-800 flex items-center justify-center font-bold text-lg mb-3">
          <i class="fa-solid fa-receipt"></i>
        </div>
        <h3 class="font-bold text-slate-900 text-base">6. Order Ticket</h3>
        <p class="text-xs text-slate-500 mt-1">Groww / Zerodha ke simulated ticket aur charges dekhein.</p>
        <span class="inline-block mt-3 text-xs font-bold text-slate-700">View Ticket →</span>
      </div>
    </div>

    <!-- Live AI Output Console (Clean High-Contrast Terminal) -->
    <div class="navy-card rounded-2xl overflow-hidden">
      <div class="bg-slate-100 px-4 py-3 border-b border-slate-200 flex items-center justify-between">
        <div class="flex items-center gap-2 text-xs font-bold text-slate-700">
          <i class="fa-solid fa-terminal text-blue-900"></i> AI Output Terminal & Logs
        </div>
        <button onclick="clearConsole()" class="text-xs text-slate-500 hover:text-slate-800 font-semibold">
          Clear Screen
        </button>
      </div>
      <div class="p-4 bg-slate-950">
        <pre id="console-output" class="text-xs font-mono text-emerald-400 overflow-x-auto min-h-[200px] max-h-[360px] leading-relaxed">Ready. Click any action button above or run Autopilot.</pre>
      </div>
    </div>

  </div>

  <script>
    async function refreshData() {
      const icon = document.getElementById('refresh-icon');
      icon.classList.add('animate-spin');
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        
        if (data.config) {
          document.getElementById('card-budget').innerText = '₹' + (data.config.budget || 1000).toLocaleString('en-IN');
          document.getElementById('card-max-loss').innerText = '₹' + (data.config.max_loss_per_trade_inr || 20);
          document.getElementById('card-broker').innerText = (data.config.broker || 'groww').toUpperCase();
          document.getElementById('card-trade-type').innerText = (data.config.trade_type === 'swing' ? 'Swing (CNC)' : 'Intraday (MIS)');
        }

        if (data.watchlist && data.watchlist.setups) {
          let html = '';
          for (const [sym, setups] of Object.entries(data.watchlist.setups)) {
            html += `
              <div class="p-3 rounded-xl border border-blue-100 bg-blue-50/50 hover:bg-blue-50 transition flex items-center justify-between">
                <div>
                  <div class="font-extrabold text-blue-950 text-sm">#${sym}</div>
                  <div class="text-[11px] text-blue-700 mt-0.5">${setups[0] || 'Breakout'}</div>
                </div>
                <button onclick="planStock('${sym}')" class="px-3 py-1.5 bg-blue-900 hover:bg-blue-950 text-white rounded-lg text-xs font-bold shadow-sm transition">
                  Plan Trade
                </button>
              </div>`;
          }
          document.getElementById('suggestions-container').innerHTML = html || '<div class="text-xs text-slate-400 py-2">No stocks found right now.</div>';
        }
      } catch (e) {
        console.error(e);
      } finally {
        setTimeout(() => icon.classList.remove('animate-spin'), 400);
      }
    }

    async function runCommand(skill, args) {
      const out = document.getElementById('console-output');
      out.innerText = `⏳ Executing: /trader-${skill} ${args.join(' ')}\nPlease wait...`;
      try {
        const res = await fetch('/api/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ skill, args })
        });
        const data = await res.json();
        if (data.error) {
          out.innerText = '❌ Error: ' + data.error;
        } else {
          out.innerText = (data.stdout || '') + (data.stderr ? '\\n' + data.stderr : '');
        }
        refreshData();
      } catch (err) {
        out.innerText = 'Network error: ' + err.message;
      }
    }

    async function runAutopilot() {
      const out = document.getElementById('console-output');
      out.innerText = '🤖 RUNNING AUTOPILOT: Scanning setups, checking probabilities, and sending alerts to your Telegram...';
      try {
        const res = await fetch('/api/autopilot', { method: 'POST' });
        const data = await res.json();
        out.innerText = (data.stdout || '') + (data.stderr ? '\\n' + data.stderr : '');
        refreshData();
      } catch (e) {
        out.innerText = 'Error: ' + e.message;
      }
    }

    async function planStock(sym) {
      const out = document.getElementById('console-output');
      out.innerText = `⏳ Calculating exit & budget sizing for ${sym}...`;
      await runCommand('exit', ['plan', sym, '--swing', '--write']);
      await runCommand('budget', ['--write']);
      await runCommand('risk', []);
    }

    function quickAnalyze() {
      const sym = prompt('Kaunsa stock analyze karna hai? (e.g. RELIANCE, TCS, INFY):', 'INFY');
      if (sym) runCommand('analyst', [sym.trim().toUpperCase(), '--quarters', '4']);
    }

    function quickTimeMachine() {
      const sym = prompt('Kaunse stock par 10,000 replays run karein? (e.g. INFY, SBIN):', 'INFY');
      if (sym) runCommand('timemachine', [sym.trim().toUpperCase()]);
    }

    function clearConsole() {
      document.getElementById('console-output').innerText = 'Ready.';
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
