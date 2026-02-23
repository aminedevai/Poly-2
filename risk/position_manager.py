"""
risk/position_manager.py — Kelly sizing and drawdown kill switch.

Currently used for reference/future live trading.
Paper mode uses fixed bet sizes from config.yaml.
"""

def kelly_fraction(win_rate: float, avg_win: float,
                   avg_loss: float) -> float:
    """
    Full Kelly criterion.
    f* = (p × b - q) / b
    where b = avg_win / avg_loss, p = win_rate, q = 1 - win_rate
    Returns fraction of capital to bet (clamped to [0, 0.25]).
    """
    if avg_loss == 0: return 0.0
    b  = avg_win / avg_loss
    q  = 1.0 - win_rate
    f  = (win_rate * b - q) / b
    return max(0.0, min(0.25, f))   # never bet more than 25% of capital

def half_kelly(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Half Kelly — more conservative, recommended for live trading."""
    return kelly_fraction(win_rate, avg_win, avg_loss) * 0.5

class DrawdownGuard:
    """
    Kill switch: halt trading if capital drops by more than max_dd_pct
    from the starting value.
    """
    def __init__(self, starting_capital: float, max_dd_pct: float = 0.20):
        self.peak        = starting_capital
        self.max_dd_pct  = max_dd_pct
        self.triggered   = False

    def update(self, current_capital: float) -> bool:
        """Returns True if trading should continue, False if kill switch triggered."""
        if current_capital > self.peak:
            self.peak = current_capital
        drawdown = (self.peak - current_capital) / self.peak
        if drawdown >= self.max_dd_pct:
            self.triggered = True
        return not self.triggered
