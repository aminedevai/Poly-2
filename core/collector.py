"""core/collector.py — Snapshots BTC 5-min markets at key timestamps."""
import asyncio, csv, os, time
from datetime import datetime, timezone
from typing import Dict, Optional
from core.api import fetch_market
from utils.config import COLLECT, PATHS
from utils import logger

log = logger.get("collector")

# Shared status — read by dashboard
live_status: Dict[str, dict] = {}


def _ensure_csv():
    os.makedirs(PATHS['logs'], exist_ok=True)
    if not os.path.exists(PATHS['collector_csv']):
        with open(PATHS['collector_csv'], 'w', newline='', encoding='utf-8') as f:
            csv.DictWriter(f, fieldnames=[
                'recorded_at', 'slug', 'end_ts', 'seconds_before_close',
                'up_price', 'volume', 'outcome'
            ]).writeheader()


def _append_row(row: dict):
    with open(PATHS['collector_csv'], 'a', newline='', encoding='utf-8') as f:
        csv.DictWriter(f, fieldnames=list(row.keys())).writerow(row)


def _backfill_outcome(slug: str, outcome: str):
    path = PATHS['collector_csv']
    if not os.path.exists(path): return
    try:
        with open(path, 'r', newline='', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        if not rows: return
        fields = list(rows[0].keys())
        changed = False
        for r in rows:
            if r['slug'] == slug and not r.get('outcome'):
                r['outcome'] = outcome
                changed = True
        if changed:
            with open(path, 'w', newline='', encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader(); w.writerows(rows)
    except Exception as e:
        log.error(f"Backfill {slug}: {e}")


def row_count() -> int:
    path = PATHS['collector_csv']
    if not os.path.exists(path): return 0
    with open(path) as f:
        return sum(1 for _ in f) - 1


class MarketTracker:
    SNAPS = COLLECT['snapshots']  # [240, 180, 120, 60, 30, 10]

    def __init__(self, slug: str):
        self.slug        = slug
        self.open_ts     = int(slug.split('-')[-1])          # open timestamp
        self.end_ts      = self.open_ts + 300                # close timestamp
        self.recorded:   set           = set()
        self.outcome:    Optional[str] = None
        self.last_price: Optional[float] = None
        self.last_vol:   float           = 0.0

    def secs_left(self) -> int:
        return max(0, self.end_ts - int(time.time()))

    def fully_expired(self) -> bool:
        return time.time() > self.end_ts + 300

    def _update_status(self, phase: str):
        live_status[self.slug] = {
            'slug':       self.slug,
            'end_ts':     self.end_ts,
            'secs_left':  self.secs_left(),
            'up_price':   self.last_price,
            'volume':     self.last_vol,
            'snaps_done': sorted(self.recorded, reverse=True),
            'outcome':    self.outcome,
            'phase':      phase,
            'url':        f"https://polymarket.com/event/{self.slug}",
        }

    def tick(self, sniper, mr) -> Optional[str]:
        secs  = self.secs_left()
        phase = ('live' if secs > 60 else 'closing' if secs > 0
                 else 'resolving' if not self.outcome else 'done')

        # Find snapshot window: ±14s around each checkpoint, counting DOWN
        target = next(
            (s for s in self.SNAPS
             if s not in self.recorded and (s - 14) <= secs <= (s + 14)),
            None
        )

        # Catchup: if 30s or 10s was missed by small margin, fire now
        if target is None:
            target = next(
                (s for s in [30, 10]
                 if s not in self.recorded and 0 <= secs < (s - 14)),
                None
            )

        # After close: poll for outcome + catchup 10s snap
        if secs == 0 and not self.outcome:
            up_price, outcome, volume = fetch_market(self.slug)
            if up_price is not None:
                self.last_price = up_price
                self.last_vol   = volume
                if 10 not in self.recorded:
                    self._record(10, up_price, volume, outcome, sniper, mr)
            if outcome:
                self.outcome = outcome
                _backfill_outcome(self.slug, outcome)
                sniper.on_outcome(self.slug, outcome)
                mr.on_outcome(self.slug, outcome)
                self._update_status('done')
                log.info(f"RESOLVED {self.slug[-10:]} → {outcome}")
                return f"RESOLVED {self.slug[-10:]} → {outcome}"
            self._update_status(phase)
            return None

        if target is None:
            self._update_status(phase)
            return None

        # Take snapshot
        up_price, outcome, volume = fetch_market(self.slug)
        if up_price is not None:
            self.last_price = up_price
            self.last_vol   = volume
        if up_price is not None or outcome is not None:
            self._record(target, up_price, volume, outcome, sniper, mr)

        if outcome and not self.outcome:
            self.outcome = outcome
            _backfill_outcome(self.slug, outcome)
            sniper.on_outcome(self.slug, outcome)
            mr.on_outcome(self.slug, outcome)
            self._update_status('done')
            return f"RESOLVED {self.slug[-10:]} → {outcome}"

        self._update_status(phase)
        return None

    def _record(self, target, up_price, volume, outcome, sniper, mr):
        _append_row({
            'recorded_at':          datetime.now(timezone.utc).isoformat(),
            'slug':                 self.slug,
            'end_ts':               self.end_ts,
            'seconds_before_close': target,
            'up_price':             round(up_price, 4) if up_price else '',
            'volume':               round(volume, 2),
            'outcome':              outcome or '',
        })
        self.recorded.add(target)
        log.info(f"SNAP {self.slug[-10:]} @{target}s UP={up_price} vol={volume:.0f}")
        if up_price:
            sniper.on_snapshot(self.slug, up_price, volume, target)
            mr.on_snapshot(self.slug, up_price, volume, target)


async def run(sniper, mr):
    _ensure_csv()
    trackers: Dict[str, MarketTracker] = {}
    interval = COLLECT['poll_interval']

    while True:
        try:
            now = int(time.time())
            cur = (now // 300) * 300   # open_ts of current candle

            for i in range(3):         # current + next 2
                open_ts = cur + i * 300
                slug    = f"btc-updown-5m-{open_ts}"
                if slug not in trackers:
                    trackers[slug] = MarketTracker(slug)
                    log.info(
                        f"TRACKING {slug}  "
                        f"{datetime.fromtimestamp(open_ts, tz=timezone.utc).strftime('%H:%M')}"
                        f"→{datetime.fromtimestamp(open_ts+300, tz=timezone.utc).strftime('%H:%M')} UTC"
                        f"  closes in {open_ts+300-now}s"
                    )

            for slug, tr in list(trackers.items()):
                tr.tick(sniper, mr)
                if tr.fully_expired():
                    live_status.pop(slug, None)
                    del trackers[slug]

        except Exception as e:
            log.error(f"Collector loop: {e}")

        await asyncio.sleep(interval)
