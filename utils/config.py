"""utils/config.py — Load config.yaml once, share everywhere."""
import yaml, os

def load() -> dict:
    path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
    with open(path) as f:
        return yaml.safe_load(f)

CFG = load()

# Shortcuts
COPY    = CFG['copy_trader']
MR      = CFG['mean_reversion']
SNIPER  = CFG['sniper']
COLLECT = CFG['collector']
APIS    = CFG['apis']
PATHS   = CFG['paths']
