import os
import sys
import time
import json
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path
import subprocess

WORKSPACE_DIR = Path(__file__).resolve().parent
SKILLS_DIR = WORKSPACE_DIR / "skills"
if not SKILLS_DIR.exists():
    SKILLS_DIR = Path("C:/Users/Admin/.gemini/config/skills")
PYTHON_EXE = sys.executable
CONFIG_PATH = WORKSPACE_DIR / "trader" / "config.json"

def send_telegram_alert(text: str):
    """Telegram Bot ke through direct phone par alert bhejta hai."""
    if not CONFIG_PATH.exists():
        return
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    
    bot_token = cfg.get("telegram_bot_token")
    chat_id = cfg.get("telegram_chat_id")

    if not bot_token or not chat_id:
        print("[Telegram Notice] bot_token ya chat_id config me set nahi hai. Alert local log me gaya.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass
    except Exception as e:
        print(f"[Telegram Error] {e}")

def run_skill(rel_path, args=None):
    if args is None:
        args = []
    full_path = SKILLS_DIR / rel_path
    cmd = [str(PYTHON_EXE), str(full_path)] + args
    try:
        res = subprocess.run(cmd, cwd=str(WORKSPACE_DIR), capture_output=True, text=True, timeout=120)
        return res.returncode, res.stdout, res.stderr
    except Exception as e:
        return -1, "", str(e)

def monitor_cycle():
    """
    24x7 monitoring cycle:
    1. Scan Breakouts (BUY setup)
    2. Read Corporate Announcements & News (Impact check)
    3. Read Smart Money Deals (Promoter / Bulk buys)
    4. Calculate Exit, Stop-Loss & Target
    5. Send instant Telegram Phone Alert
    """
    ts = datetime.now().strftime("%d-%b %H:%M IST")
    print(f"[{ts}] Running 24x7 Market Monitor Cycle...")

    # 1. Run Deals Tracker (Promoter / Bulk deals)
    code, deals_out, _ = run_skill("trader-deals/deals.py", ["all", "--days", "2", "--min-value", "20"])
    if "★ PROMOTER BUY" in deals_out:
        msg = f"<b>🚨 PROMOTER BUYING DETECTED! ({ts})</b>\n\nCompany ke promoters ne open market se shares khareede hain:\n\n<pre>{deals_out[:400]}</pre>\n\n<i>Tip: Promoter buying stock ke liye strong bullish signal hota hai.</i>"
        send_telegram_alert(msg)

    # 2. Run Scanner (Setups check)
    code, scan_out, _ = run_skill("trader-scanner/scan.py", ["daily", "--universe", "nifty50"])
    wl_path = WORKSPACE_DIR / "trader" / "watchlist.json"
    
    if wl_path.exists():
        with open(wl_path, "r", encoding="utf-8") as f:
            wl = json.load(f)
        setups = wl.get("setups", {})
        
        # Check previously alerted stocks
        alerted_file = WORKSPACE_DIR / "trader" / "state" / "sent_alerts.json"
        alerted_file.parent.mkdir(parents=True, exist_ok=True)
        already_alerted = []
        if alerted_file.exists():
            try:
                with open(alerted_file, "r") as f:
                    already_alerted = json.load(f)
            except Exception:
                already_alerted = []

        new_candidates = [s for s in setups.keys() if s not in already_alerted]

        if new_candidates:
            top = new_candidates[0]
            setup_name = setups[top][0] if setups[top] else "Breakout"
            
            # Plan exit for this top candidate
            run_skill("trader-exit/exit.py", ["plan", top, "--swing", "--write"])
            run_skill("trader-budget/size.py", ["--write"])

            # Read generated plan
            plan_path = WORKSPACE_DIR / "trader" / "plan.json"
            order_path = WORKSPACE_DIR / "trader" / "orders.json"
            
            plan_text = ""
            if plan_path.exists():
                with open(plan_path, "r", encoding="utf-8") as f:
                    p = json.load(f)
                    plan_text = f"• <b>Action:</b> BUY\n• <b>Entry:</b> ₹{p.get('entry')}\n• <b>Stop Loss:</b> ₹{p.get('stop')}\n• <b>Target:</b> ₹{p.get('target')} (2R Profit)\n• <b>Type:</b> Swing / CNC"

            alert_msg = f"<b>🔔 NEW ACTIONABLE TRADE ALERT ({ts})</b>\n\n<b>Stock:</b> #{top}\n<b>Setup:</b> {setup_name}\n\n{plan_text}\n\n<i>🛡️ Risk: ₹1,000 budget ke mutabiq safe quantity auto-calculated hai.</i>"
            send_telegram_alert(alert_msg)

            already_alerted.append(top)
            with open(alerted_file, "w") as f:
                json.dump(already_alerted[-50:], f)

def run_daemon():
    print("24x7 Trader Sentinel Service Started.")
    while True:
        try:
            monitor_cycle()
        except Exception as e:
            print(f"Cycle exception: {e}")
        # Run every 15 minutes during market or every 30 mins outside
        time.sleep(900)

if __name__ == "__main__":
    monitor_cycle()
