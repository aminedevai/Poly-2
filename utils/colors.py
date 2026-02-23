"""utils/colors.py — Terminal color helpers."""
import re

class C:
    R  = "\033[0m";  B  = "\033[1m"
    CY = "\033[96m"; MG = "\033[95m"
    GR = "\033[92m"; RE = "\033[91m"
    YL = "\033[93m"; BL = "\033[94m"
    WH = "\033[97m"; GY = "\033[90m"
    OR = "\033[38;5;208m"

def _c(t, c):    return f"{c}{t}{C.R}"
def green(t):    return _c(t, C.GR)
def red(t):      return _c(t, C.RE)
def yel(t):      return _c(t, C.YL)
def cyan(t):     return _c(t, C.CY)
def gray(t):     return _c(t, C.GY)
def bold(t):     return _c(t, C.B)
def blue(t):     return _c(t, C.BL)
def orange(t):   return _c(t, C.OR)
def mg(t):       return _c(t, C.MG)
def pnlc(v, t):  return green(t) if v >= 0 else red(t)
def trunc(t, n): return t[:n - 2] + ".." if len(t) > n else t

def strip_ansi(t):
    return re.sub(r"\033\[[0-9;]*m", "", t)

def pad(t, n, align="<"):
    raw   = strip_ansi(t)
    extra = len(t) - len(raw)
    w     = n + extra
    if align == ">": return t.rjust(w)
    if align == "^": return t.center(w)
    return t.ljust(w)
