import time
import os
import json
import logging
from datetime import datetime
from pathlib import Path
import subprocess

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

WORKSPACE_DIR = Path(__file__).resolve().parent
SKILLS_DIR = WORKSPACE_DIR / "skills"
if not SKILLS_DIR.exists():
    SKILLS_DIR = Path("C:/Users/Admin/.gemini/config/skills")
PYTHON_EXE = sys.executable

def run_script(rel_path, args=None):
    if args is None:
        args = []
    full_path = SKILLS_DIR / rel_path
    cmd = [str(PYTHON_EXE), str(full_path)] + args
    try:
        res = subprocess.run(cmd, cwd=str(WORKSPACE_DIR), capture_output=True, text=True, timeout=120)
        return res.returncode, res.stdout, res.stderr
    except Exception as e:
        return -1, "", str(e)

def log_auto_activity(msg):
    log_file = WORKSPACE_DIR / "trader" / "auto_bot.log"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    print(line.strip())
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line)

def run_autopilot_cycle():
    """
    Ek complete autonomous loop:
    1. Scan Nifty 50 setups
    2. Read watchlist
    3. Check entry breakout
    4. Plan exit (stop/target)
    5. Check TimeMachine probability
    6. Size budget to ₹20 max loss
    7. Risk check Pass/Block
    8. Paper trade simulation record
    """
    log_auto_activity("--- Starting Autopilot Market Cycle ---")

    # Step 1: Scan
    log_auto_activity("Step 1: Scanning Nifty 50 for breakouts...")
    code, out, err = run_script("trader-scanner/scan.py", ["daily", "--universe", "nifty50"])
    
    # Step 2: Read watchlist
    wl_path = WORKSPACE_DIR / "trader" / "watchlist.json"
    if not wl_path.exists():
        log_auto_activity("Watchlist not found, ending cycle.")
        return
    
    with open(wl_path, "r", encoding="utf-8") as f:
        wl_data = json.load(f)
    
    setups = wl_data.get("setups", {})
    if not setups:
        log_auto_activity("No valid breakouts found matching criteria right now.")
        return
    
    candidates = list(setups.keys())
    log_auto_activity(f"Step 2: Found {len(candidates)} candidate stocks: {', '.join(candidates[:5])}")

    # Step 3: Pick best candidate & evaluate
    best_stock = candidates[0]
    log_auto_activity(f"Step 3: Evaluating best candidate: {best_stock}")

    # Check News & Deals
    run_script("trader-news/news.py", ["--tickers", best_stock, "--hours", "24"])
    run_script("trader-deals/deals.py", ["all", "--symbol", best_stock, "--days", "7"])

    # Step 4: Plan Entry & Exit
    # Example using exit planner
    log_auto_activity(f"Step 4: Planning Entry, Stop-Loss & Target for {best_stock}...")
    run_script("trader-exit/exit.py", ["plan", best_stock, "--write"])

    # Step 5: Time Machine verification
    log_auto_activity(f"Step 5: Simulating 10,000 replays on {best_stock} with Time Machine...")
    code, tm_out, _ = run_script("trader-timemachine/timemachine.py", [best_stock])
    
    # Step 6: Size budget
    log_auto_activity("Step 6: Sizing trade according to ₹1,000 budget & ₹20 max loss...")
    run_script("trader-budget/size.py", ["--write"])

    # Step 7: Risk guard check
    log_auto_activity("Step 7: Enforcing Indian market rules & risk check...")
    code, risk_out, _ = run_script("trader-risk/check.py")
    
    if "BLOCK" in risk_out:
        log_auto_activity(f"Risk Shield Decision: BLOCKED (Safe - Capital Protected). Details in log.")
    else:
        log_auto_activity(f"Risk Shield Decision: PASSED. Executing Paper Trade on {best_stock}.")
        run_script("trader-broker/ticket.py", ["paper", "--confirm"])

    log_auto_activity("--- Autopilot Cycle Finished ---")

if __name__ == "__main__":
    log_auto_activity("AI Trading Autopilot Bot Initialized. Running first cycle...")
    run_autopilot_cycle()
