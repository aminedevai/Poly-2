"""utils/logger.py — Logging setup."""
import logging, os
from utils.config import PATHS

def setup():
    os.makedirs(PATHS['logs'], exist_ok=True)
    fh = logging.FileHandler(PATHS['log_file'], mode='a', encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        if not isinstance(h, logging.FileHandler):
            root.removeHandler(h)
    root.addHandler(fh)

def get(name: str) -> logging.Logger:
    return logging.getLogger(name)
