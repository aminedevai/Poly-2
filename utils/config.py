"""utils/config.py — Load config.yaml once. All modules import from here."""
import yaml, os

def _load() -> dict:
    # Walk up from this file to find config.yaml at project root
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    path = os.path.join(root, "config.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

CFG = _load()

# Shortcuts used across the project
COPY    = CFG["copy_trader"]
MR      = CFG["mean_reversion"]
SNIPER  = CFG["sniper"]
COLLECT = CFG["collector"]
APIS    = CFG["apis"]
PATHS   = CFG["paths"]
