"""
patch_datasets.py
=================
Patches old dataset files (missing p30) by adding synthetic p30 estimates.
Run once after updating to v6+:
    python -m backtest.patch_datasets
"""
import json, os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest.fetch_data import DATA_DIR, synthetic_p30

def patch_all():
    files = [f for f in os.listdir(DATA_DIR) if f.endswith(".json")]
    if not files:
        print("No dataset files found.")
        return

    for fname in sorted(files):
        path = os.path.join(DATA_DIR, fname)
        with open(path, encoding="utf-8") as f:
            markets = json.load(f)

        needs_patch = any(m.get("p30") is None for m in markets)
        if not needs_patch:
            print(f"  OK (already has p30): {fname}")
            continue

        patched = 0
        for m in markets:
            if m.get("p30") is None and m.get("outcome"):
                m["p30"]          = synthetic_p30(m["outcome"], m.get("volume", 0), seed=m.get("open_ts", 0))
                m["has_real_p30"] = False
                m["n_price_pts"]  = m.get("n_price_pts", 0)
                patched += 1

        with open(path, "w", encoding="utf-8") as f:
            json.dump(markets, f, indent=2)
        print(f"  Patched {patched}/{len(markets)} markets in {fname}")

if __name__ == "__main__":
    patch_all()
    print("\nDone — all datasets have p30. Run backtest now.")
