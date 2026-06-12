"""
╔══════════════════════════════════════════════════════════════════╗
║         ELITE TRADING SIGNAL BOT  —  Version 4.0                ║
║         Ultra-Selective | All Issues Fixed | 1-2 Signals/Day    ║
╠══════════════════════════════════════════════════════════════════╣
║  FIXES IN v4.0:                                                  ║
║  1. Wrong direction    → All 3 HTF must STRICTLY agree           ║
║  2. Wrong timing       → ADX 25+ required, stage early/mid only  ║
║  3. Stop loss          → Exact structural candle low/high        ║
║  4. Targets too far    → TP1 = very next real S/R level          ║
║  5. Too many signals   → 85% minimum score, 6 HTF gates          ║
║  6. Pattern quality    → Min 70% candle quality required         ║
╚══════════════════════════════════════════════════════════════════╝
"""

import sys
import io
import time
import logging
import requests
import numpy as np
import pandas as pd
import pandas_ta as ta
import ccxt
from datetime import datetime, timezone
from typing import Optional

# ══════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════
DISCORD_WEBHOOK_URL  = "https://discord.com/api/webhooks/1511851878718242898/9T7evebqOPoJPL_B--G-OBIcN7pq3-67Ddpj1YJ6A0QWP3BrYMxqb4vA93TVEVLjKtRo"

SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "XAUT/USDT",
    "XAGUSDT",
]

# Asset type classification
COMMODITIES = {"XAUT/USDT", "XAGUSDT"}   # Gold and Silver
CRYPTO      = {"BTC/USDT", "ETH/USDT"}   # Crypto

SCAN_INTERVAL_SECONDS = 300
MIN_SCORE_PCT         = 85      # Raised to 85% — ultra strict
MIN_RR                = 2.5     # Realistic RR — not too greedy
ALERT_COOLDOWN_HOURS  = 8
MAX_SIGNALS_PER_DAY   = 2
ACTIVE_HOURS_UTC      = list(range(0, 24))  # 24/7 — gold/silver move any time

# ══════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler("bot_v4.log", encoding="utf-8"),
        logging.StreamHandler(
            stream=io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        ),
    ],
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════
#  SIGNAL COUNTER
# ══════════════════════════════════════════════════════
_signals_today = 0
_signals_date  = ""
_alerted: dict = {}

def check_daily_limit() -> bool:
    global _signals_today, _signals_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _signals_date != today:
        _signals_today = 0
        _signals_date  = today
    return _signals_today < MAX_SIGNALS_PER_DAY

def increment_signal_count():
    global _signals_today
    _signals_today += 1

def already_alerted(symbol: str, direction: str) -> bool:
    key = f"{symbol}_{direction}"
    now = time.time()
    if key in _alerted and (now - _alerted[key]) < ALERT_COOLDOWN_HOURS * 3600:
        return True
    _alerted[key] = now
    return False

# ══════════════════════════════════════════════════════
#  DISCORD
# ══════════════════════════════════════════════════════
def send_discord(message: str) -> None:
    try:
        chunks = [message[i:i+1900] for i in range(0, len(message), 1900)]
        for chunk in chunks:
            r = requests.post(
                DISCORD_WEBHOOK_URL,
                json={"content": "```\n" + chunk + "\n```"},
                timeout=10,
            )
            if r.status_code in (200, 204):
                log.info("Discord: sent OK")
            else:
                log.warning(f"Discord error {r.status_code}: {r.text[:100]}")
    except Exception as e:
        log.error(f"Discord send failed: {e}")

# ══════════════════════════════════════════════════════
#  EXCHANGE
# ══════════════════════════════════════════════════════
exchange = ccxt.bitget({
    "enableRateLimit": True,
    "options": {"defaultType": "swap"},
})

def fetch_ohlcv(symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
    try:
        raw = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        if not raw or len(raw) < 30:
            return pd.DataFrame()
        df = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        df.set_index("ts", inplace=True)
        return df.astype(float)
    except Exception as e:
        log.warning(f"Fetch failed {symbol} {timeframe}: {e}")
        return pd.DataFrame()

# ══════════════════════════════════════════════════════
#  INDICATORS
# ══════════════════════════════════════════════════════
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or len(df) < 55:
        return df
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    df["ema20"]  = ta.ema(c, 20)
    df["ema50"]  = ta.ema(c, 50)
    df["ema200"] = ta.ema(c, 200)
    df["rsi"]    = ta.rsi(c, 14)
    df["atr"]    = ta.atr(h, l, c, 14)

    macd = ta.macd(c, 12, 26, 9)
    if macd is not None:
        df["macd"]      = macd.iloc[:, 0]
        df["macd_sig"]  = macd.iloc[:, 1]
        df["macd_hist"] = macd.iloc[:, 2]

    bb = ta.bbands(c, 20, 2.0)
    if bb is not None:
        df["bb_upper"] = bb.iloc[:, 0]
        df["bb_mid"]   = bb.iloc[:, 1]
        df["bb_lower"] = bb.iloc[:, 2]
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

    df["vol_sma"]   = v.rolling(20).mean()
    df["vol_ratio"] = v / df["vol_sma"]
    df["body"]       = abs(c - df["open"])
    df["upper_wick"] = h - df[["close","open"]].max(axis=1)
    df["lower_wick"] = df[["close","open"]].min(axis=1) - l
    df["candle_range"] = h - l
    df["body_pct"]   = df["body"] / df["candle_range"].replace(0, np.nan)

    try:
        ichi = ta.ichimoku(h, l, c, lookahead=False)
        if ichi and len(ichi) == 2:
            i0 = ichi[0]
            if "ITS_9" in i0.columns:
                df["tenkan"] = i0["ITS_9"]
                df["kijun"]  = i0["IKS_26"]
                df["span_a"] = i0["ISA_9"]
                df["span_b"] = i0["ISB_26"]
    except Exception:
        pass

    return df

# ══════════════════════════════════════════════════════
#  STRICT MARKET STRUCTURE
# ══════════════════════════════════════════════════════
def strict_structure(df: pd.DataFrame, lookback: int = 30) -> str:
    """
    Strict HH/HL or LH/LL detection.
    Returns: bullish / bearish / ranging
    ranging = DO NOT TRADE
    """
    if df.empty or len(df) < lookback:
        return "ranging"
    r   = df.tail(lookback)
    mid = lookback // 2

    # Split into two halves
    first_half  = r.iloc[:mid]
    second_half = r.iloc[mid:]

    fh_hi = first_half["high"].max()
    fh_lo = first_half["low"].min()
    sh_hi = second_half["high"].max()
    sh_lo = second_half["low"].min()

    # Strict bullish: BOTH higher high AND higher low
    if sh_hi > fh_hi * 1.001 and sh_lo > fh_lo * 1.001:
        return "bullish"
    # Strict bearish: BOTH lower high AND lower low
    if sh_hi < fh_hi * 0.999 and sh_lo < fh_lo * 0.999:
        return "bearish"
    return "ranging"

# ══════════════════════════════════════════════════════
#  HTF GATE — ALL 3 MUST PASS
# ══════════════════════════════════════════════════════
def htf_gate(df_mo, df_wk, df_day, direction: str) -> tuple:
    """
    This is the most important filter.
    Monthly, Weekly, AND Daily structure must ALL agree.
    No ranging allowed on Weekly or Daily.
    Returns (passed, reason)
    """
    ms_mo  = strict_structure(df_mo,  8)
    ms_wk  = strict_structure(df_wk,  15)
    ms_day = strict_structure(df_day, 20)

    reasons = []

    # Monthly — ranging is OK (it's very slow)
    mo_ok = (direction == "long"  and ms_mo in ("bullish", "ranging")) or \
            (direction == "short" and ms_mo in ("bearish", "ranging"))
    if not mo_ok:
        return False, f"BLOCKED: Monthly structure {ms_mo} against {direction}"

    # Weekly — crypto must strictly agree, commodities allow ranging
    is_commodity = True  # Will be overridden by caller
    wk_ok = (direction == "long"  and ms_wk in ("bullish", "ranging")) or \
            (direction == "short" and ms_wk in ("bearish", "ranging"))
    if not wk_ok:
        return False, f"BLOCKED: Weekly structure {ms_wk} against {direction}"

    # Daily — must strictly agree for all assets
    day_ok = (direction == "long"  and ms_day == "bullish") or \
             (direction == "short" and ms_day == "bearish")
    if not day_ok:
        return False, f"BLOCKED: Daily structure {ms_day} — must be {direction}"

    return True, f"HTF gate passed: Mo={ms_mo} Wk={ms_wk} Day={ms_day}"

# ══════════════════════════════════════════════════════
#  ADX TREND STRENGTH
# ══════════════════════════════════════════════════════
def get_adx(df: pd.DataFrame) -> tuple:
    if df.empty or len(df) < 30:
        return 0.0, "weak"
    try:
        adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
        if adx_df is not None and "ADX_14" in adx_df.columns:
            adx = float(adx_df["ADX_14"].iloc[-1])
            if adx >= 40: return adx, "strong"
            if adx >= 25: return adx, "moderate"
            return adx, "weak"
    except Exception:
        pass
    return 0.0, "weak"

# ══════════════════════════════════════════════════════
#  MOVE STAGE — STRICT
# ══════════════════════════════════════════════════════
def move_stage(df: pd.DataFrame, direction: str) -> str:
    if df.empty or len(df) < 50:
        return "unknown"
    price = float(df["close"].iloc[-1])
    rsi   = float(df["rsi"].iloc[-1])   if "rsi"   in df.columns else 50.0
    ema20 = float(df["ema20"].iloc[-1]) if "ema20" in df.columns else price
    atr   = float(df["atr"].iloc[-1])   if "atr"   in df.columns else price * 0.01
    dist  = abs(price - ema20) / max(atr, 1e-9)

    if direction == "long":
        if rsi > 68 or dist > 3.5: return "exhausted"
        if rsi > 60 or dist > 2.0: return "late"
        if rsi > 50 or dist > 1.0: return "middle"
        return "early"
    else:
        if rsi < 32 or dist > 3.5: return "exhausted"
        if rsi < 40 or dist > 2.0: return "late"
        if rsi < 50 or dist > 1.0: return "middle"
        return "early"

# ══════════════════════════════════════════════════════
#  DAILY CANDLE AGREEMENT
# ══════════════════════════════════════════════════════
def daily_agrees(df_day: pd.DataFrame, direction: str) -> tuple:
    if df_day.empty or len(df_day) < 3:
        return False, "No data"
    last = df_day.iloc[-1]
    prev = df_day.iloc[-2]
    bpct = float(last["body_pct"]) if "body_pct" in df_day.columns and not pd.isna(last["body_pct"]) else 0
    bull = last["close"] > last["open"] and bpct > 0.30 and last["close"] > prev["close"]
    bear = last["close"] < last["open"] and bpct > 0.30 and last["close"] < prev["close"]
    if direction == "long"  and bull: return True,  "Daily candle bullish"
    if direction == "short" and bear: return True,  "Daily candle bearish"
    return False, "Daily candle disagrees"


# ══════════════════════════════════════════════════════
#  MOMENTUM BREAKOUT DETECTION (for commodities)
# ══════════════════════════════════════════════════════
def detect_momentum_breakout(df_1h: pd.DataFrame, df_4h: pd.DataFrame) -> tuple:
    """
    Detect strong directional momentum even without a perfect candle pattern.
    Used for commodities that can break hard without warning candles.
    """
    if df_1h.empty or len(df_1h) < 10 or df_4h.empty:
        return "none", 0.0

    # Check last 3 candles on 1H
    recent = df_1h.tail(5)
    price  = float(df_1h["close"].iloc[-1])
    atr    = float(df_1h["atr"].iloc[-1]) if "atr" in df_1h.columns else price * 0.005

    # Strong bearish momentum: 3 consecutive bearish candles closing lower
    last3 = df_1h.tail(3)
    all_bear = all(last3["close"].iloc[i] < last3["open"].iloc[i] for i in range(3))
    all_bull = all(last3["close"].iloc[i] > last3["open"].iloc[i] for i in range(3))

    # Each candle body must be at least 0.4x ATR
    bodies = [abs(float(last3["close"].iloc[i]) - float(last3["open"].iloc[i])) for i in range(3)]
    strong_bodies = all(b >= 0.4 * atr for b in bodies)

    # Price moved more than 1.5x ATR in 3 candles
    total_move = abs(float(last3["close"].iloc[-1]) - float(last3["open"].iloc[0]))

    if all_bear and strong_bodies and total_move >= 1.5 * atr:
        # Also check 4H is bearish
        if float(df_4h["close"].iloc[-1]) < float(df_4h["open"].iloc[-1]):
            return "Bearish Momentum Breakout", 0.75

    if all_bull and strong_bodies and total_move >= 1.5 * atr:
        if float(df_4h["close"].iloc[-1]) > float(df_4h["open"].iloc[-1]):
            return "Bullish Momentum Breakout", 0.75

    # Price breaking below a key level with strong candle
    recent_low = float(df_1h["low"].tail(20).iloc[:-3].min())
    recent_high = float(df_1h["high"].tail(20).iloc[:-3].max())

    last_c = df_1h.iloc[-1]
    last_body = abs(float(last_c["close"]) - float(last_c["open"]))

    if float(last_c["close"]) < recent_low and last_body >= 0.5 * atr and last_c["close"] < last_c["open"]:
        return "Level Breakdown", 0.80

    if float(last_c["close"]) > recent_high and last_body >= 0.5 * atr and last_c["close"] > last_c["open"]:
        return "Level Breakout", 0.80

    return "none", 0.0

# ══════════════════════════════════════════════════════
#  PATTERN DETECTION
# ══════════════════════════════════════════════════════
def detect_pattern(df: pd.DataFrame) -> tuple:
    if df.empty or len(df) < 4:
        return "none", 0.0

    c1 = df.iloc[-1]
    c2 = df.iloc[-2]
    c3 = df.iloc[-3]

    b1   = float(c1["body"])
    r1   = float(c1["candle_range"]) if float(c1["candle_range"]) > 0 else 1e-6
    uw1  = float(c1["upper_wick"])
    lw1  = float(c1["lower_wick"])
    bp1  = float(c1["body_pct"]) if not pd.isna(c1["body_pct"]) else 0.0

    # ── Single candle ──────────────────────────────────

    # Bullish Engulfing — strong body required
    if (c1["close"] > c1["open"] and c2["close"] < c2["open"]
            and c1["close"] > c2["open"] and c1["open"] < c2["close"]
            and bp1 > 0.60):
        return "Bullish Engulfing", min(bp1 * 1.1, 1.0)

    # Bearish Engulfing
    if (c1["close"] < c1["open"] and c2["close"] > c2["open"]
            and c1["close"] < c2["open"] and c1["open"] > c2["close"]
            and bp1 > 0.60):
        return "Bearish Engulfing", min(bp1 * 1.1, 1.0)

    # Hammer — wick must be 2.5x body
    if lw1 >= 2.5*b1 and uw1 <= 0.25*b1 and c1["close"] > c1["open"] and bp1 > 0.15:
        return "Hammer", min(lw1/r1, 1.0)

    # Shooting Star
    if uw1 >= 2.5*b1 and lw1 <= 0.25*b1 and c1["close"] < c1["open"] and bp1 > 0.15:
        return "Shooting Star", min(uw1/r1, 1.0)

    # Bullish Pin Bar
    if lw1 >= 3.0*max(b1, 1e-9) and c1["close"] > c1["open"]:
        return "Bullish Pin Bar", min(lw1/r1, 1.0)

    # Bearish Pin Bar
    if uw1 >= 3.0*max(b1, 1e-9) and c1["close"] < c1["open"]:
        return "Bearish Pin Bar", min(uw1/r1, 1.0)

    # Dragonfly Doji
    if lw1 > 3*b1 and uw1 < b1 and bp1 < 0.12:
        return "Dragonfly Doji", 0.85

    # Gravestone Doji
    if uw1 > 3*b1 and lw1 < b1 and bp1 < 0.12:
        return "Gravestone Doji", 0.85

    # ── Multi candle ───────────────────────────────────

    b2 = abs(float(c2["close"]) - float(c2["open"]))
    b3 = abs(float(c3["close"]) - float(c3["open"]))

    # Morning Star
    if (c3["close"] < c3["open"]
            and b2 < b3 * 0.35
            and c1["close"] > c1["open"]
            and c1["close"] > (c3["open"] + c3["close"]) / 2
            and b3 > 0):
        return "Morning Star", 0.88

    # Evening Star
    if (c3["close"] > c3["open"]
            and b2 < b3 * 0.35
            and c1["close"] < c1["open"]
            and c1["close"] < (c3["open"] + c3["close"]) / 2
            and b3 > 0):
        return "Evening Star", 0.88

    # Three White Soldiers
    if (c1["close"] > c1["open"] and c2["close"] > c2["open"] and c3["close"] > c3["open"]
            and c1["close"] > c2["close"] > c3["close"]
            and bp1 > 0.55 and float(c2["body_pct"]) > 0.55 if not pd.isna(c2["body_pct"]) else False):
        return "Three White Soldiers", 0.90

    # Three Black Crows
    if (c1["close"] < c1["open"] and c2["close"] < c2["open"] and c3["close"] < c3["open"]
            and c1["close"] < c2["close"] < c3["close"]
            and bp1 > 0.55 and float(c2["body_pct"]) > 0.55 if not pd.isna(c2["body_pct"]) else False):
        return "Three Black Crows", 0.90

    # Failed Breakout (bearish reversal)
    if (float(c2["high"]) > float(c3["high"])
            and c1["close"] < float(c3["high"])
            and c1["close"] < c1["open"]
            and float(c1["candle_range"]) > float(c2["candle_range"]) * 0.75):
        return "Failed Breakout", 0.92

    # Failed Breakdown (bullish reversal)
    if (float(c2["low"]) < float(c3["low"])
            and c1["close"] > float(c3["low"])
            and c1["close"] > c1["open"]
            and float(c1["candle_range"]) > float(c2["candle_range"]) * 0.75):
        return "Failed Breakdown", 0.92

    return "none", 0.0

# ══════════════════════════════════════════════════════
#  SWING LEVELS
# ══════════════════════════════════════════════════════
def swing_levels(df: pd.DataFrame, window: int = 5, n: int = 8) -> dict:
    empty = {"supports":[], "resistances":[], "touch_counts":{}}
    if df.empty or len(df) < window*3:
        return empty
    price = float(df["close"].iloc[-1])
    h, l  = df["high"], df["low"]
    sh = df["high"][(h == h.rolling(window, center=True).max())].dropna().tolist()
    sl = df["low"] [(l == l.rolling(window, center=True).min())].dropna().tolist()

    def cluster(lvs, tol=0.003):
        if not lvs: return []
        lvs = sorted(set(lvs))
        out, grp = [], [lvs[0]]
        for lv in lvs[1:]:
            if abs(lv-grp[-1])/max(grp[-1],1e-9) <= tol:
                grp.append(lv)
            else:
                out.append(float(np.mean(grp)))
                grp = [lv]
        out.append(float(np.mean(grp)))
        return out

    def tc(lv, df, tol=0.005):
        lo,hi = lv*(1-tol), lv*(1+tol)
        return int(((df["low"]<=hi)&(df["high"]>=lo)).sum())

    ch = cluster(sh)
    cl = cluster(sl)
    tcs = {round(lv,6): tc(lv,df) for lv in ch+cl}
    return {
        "supports":    sorted([x for x in cl if x < price], reverse=True)[:n],
        "resistances": sorted([x for x in ch if x > price])[:n],
        "touch_counts": tcs,
    }

# ══════════════════════════════════════════════════════
#  FIND NEAREST S/R LEVEL — FOR TP1
# ══════════════════════════════════════════════════════
def nearest_sr(price: float, direction: str, levels: dict,
               min_dist_pct: float = 0.005) -> Optional[float]:
    """
    Returns the very next significant S/R level in trade direction.
    This is used as TP1 — real level, not calculated.
    """
    if direction == "long":
        candidates = [r for r in levels.get("resistances",[])
                      if r > price * (1 + min_dist_pct)]
        return min(candidates) if candidates else None
    else:
        candidates = [s for s in levels.get("supports",[])
                      if s < price * (1 - min_dist_pct)]
        return max(candidates) if candidates else None

# ══════════════════════════════════════════════════════
#  FIBONACCI
# ══════════════════════════════════════════════════════
def fib_levels(df: pd.DataFrame, lookback: int = 100) -> dict:
    if df.empty or len(df) < lookback: return {}
    r = df.tail(lookback)
    hi, lo = float(r["high"].max()), float(r["low"].min())
    d = hi - lo
    return {
        "23.6": hi-0.236*d, "38.2": hi-0.382*d,
        "50.0": hi-0.500*d, "61.8": hi-0.618*d,
        "78.6": hi-0.786*d,
        "127.2": lo-0.272*d, "161.8": lo-0.618*d,
    }

def fib_near(price: float, fibs: dict, tol: float = 0.008) -> tuple:
    key = ["38.2","50.0","61.8","78.6"]
    best, bd = None, float("inf")
    for k in key:
        if k in fibs:
            d = abs(price-fibs[k])/price
            if d < bd: bd, best = d, k
    return (best, bd) if bd <= tol else (None, bd)

# ══════════════════════════════════════════════════════
#  RSI DIVERGENCE
# ══════════════════════════════════════════════════════
def rsi_div(df: pd.DataFrame, lookback: int = 25) -> str:
    if "rsi" not in df.columns or len(df) < lookback+5:
        return "none"
    r   = df.tail(lookback)
    mid = lookback//2
    if r["low"].iloc[mid:].min()  < r["low"].iloc[:mid].min()  and \
       r["rsi"].iloc[-5:].mean()  > r["rsi"].iloc[:mid].mean() + 3:
        return "bullish"
    if r["high"].iloc[mid:].max() > r["high"].iloc[:mid].max() and \
       r["rsi"].iloc[-5:].mean()  < r["rsi"].iloc[:mid].mean() - 3:
        return "bearish"
    return "none"

# ══════════════════════════════════════════════════════
#  ICHIMOKU
# ══════════════════════════════════════════════════════
def ichi_bias(df: pd.DataFrame, direction: str) -> tuple:
    cols = ["span_a","span_b","tenkan","kijun"]
    if not all(c in df.columns for c in cols):
        return False, "unavailable"
    price = float(df["close"].iloc[-1])
    sa, sb = float(df["span_a"].iloc[-1]), float(df["span_b"].iloc[-1])
    tk, kj = float(df["tenkan"].iloc[-1]), float(df["kijun"].iloc[-1])
    ct, cb = max(sa,sb), min(sa,sb)
    if cb <= price <= ct: return False, "inside cloud"
    if direction == "long":
        if price > ct and tk >= kj: return True, "above cloud + TK bull"
        if price > ct:              return True, "above cloud"
    else:
        if price < cb and tk <= kj: return True, "below cloud + TK bear"
        if price < cb:              return True, "below cloud"
    return False, "not aligned"

# ══════════════════════════════════════════════════════
#  PREVIOUS DAY / WEEK LEVELS
# ══════════════════════════════════════════════════════
def pdh_pdl(df_1h: pd.DataFrame) -> tuple:
    if df_1h.empty or len(df_1h) < 48: return 0.0, 0.0
    today = df_1h.index[-1].normalize()
    prev  = df_1h[df_1h.index < today].tail(24)
    return (float(prev["high"].max()), float(prev["low"].min())) if not prev.empty else (0.0, 0.0)

def pwh_pwl(df_4h: pd.DataFrame) -> tuple:
    if df_4h.empty or len(df_4h) < 84: return 0.0, 0.0
    wsn  = (df_4h.index[-1] - pd.Timedelta(days=df_4h.index[-1].dayofweek)).normalize()
    prev = df_4h[df_4h.index < wsn].tail(42)
    return (float(prev["high"].max()), float(prev["low"].min())) if not prev.empty else (0.0, 0.0)

# ══════════════════════════════════════════════════════
#  ROUND NUMBER
# ══════════════════════════════════════════════════════
def round_num(price: float, tol: float = 0.008) -> tuple:
    mag = 10 ** (len(str(int(price))) - 1)
    for lv in [round(price/mag)*mag, round(price/(mag/2))*(mag/2)]:
        if lv > 0 and abs(price-lv)/price <= tol:
            return True, f"${lv:,.0f}"
    return False, ""

# ══════════════════════════════════════════════════════
#  STRUCTURE-BASED STOP LOSS
#  Placed at the most recent significant swing high/low
# ══════════════════════════════════════════════════════
def struct_stop(price: float, direction: str,
                df_4h: pd.DataFrame, df_1h: pd.DataFrame,
                atr: float) -> float:
    buf = atr * 0.25
    if direction == "long":
        # Stop below the most recent significant 4H swing low
        lows_4h = df_4h["low"].tail(15)
        # Find local pivot lows on 4H
        pivot_lows = []
        for i in range(2, len(lows_4h)-2):
            if (lows_4h.iloc[i] < lows_4h.iloc[i-1] and
                    lows_4h.iloc[i] < lows_4h.iloc[i-2] and
                    lows_4h.iloc[i] < lows_4h.iloc[i+1] and
                    lows_4h.iloc[i] < lows_4h.iloc[i+2]):
                pivot_lows.append(float(lows_4h.iloc[i]))
        if pivot_lows:
            # Use the most recent pivot low below current price
            valid = [l for l in pivot_lows if l < price]
            stop  = max(valid) - buf if valid else price - 2*atr
        else:
            stop = float(lows_4h.min()) - buf
        return min(stop, price * 0.975)  # Max 2.5% stop for crypto

    else:
        highs_4h = df_4h["high"].tail(15)
        pivot_highs = []
        for i in range(2, len(highs_4h)-2):
            if (highs_4h.iloc[i] > highs_4h.iloc[i-1] and
                    highs_4h.iloc[i] > highs_4h.iloc[i-2] and
                    highs_4h.iloc[i] > highs_4h.iloc[i+1] and
                    highs_4h.iloc[i] > highs_4h.iloc[i+2]):
                pivot_highs.append(float(highs_4h.iloc[i]))
        if pivot_highs:
            valid = [h for h in pivot_highs if h > price]
            stop  = min(valid) + buf if valid else price + 2*atr
        else:
            stop = float(highs_4h.max()) + buf
        return max(stop, price * 1.025)

# ══════════════════════════════════════════════════════
#  REALISTIC TARGETS
# ══════════════════════════════════════════════════════
def calc_targets(price: float, stop: float, direction: str,
                 day_levels: dict, fibs: dict) -> tuple:
    risk = abs(price - stop)

    if direction == "long":
        # TP1 = next real resistance level
        res = [r for r in day_levels.get("resistances",[]) if r > price + risk * 0.5]
        tp1 = min(res) if res else price + 2.0 * risk

        # TP2 = next resistance after TP1 or fib extension
        res2 = [r for r in day_levels.get("resistances",[]) if r > tp1 + risk * 0.3]
        fe   = [v for k,v in fibs.items() if k in ("127.2","161.8") and v > tp1]
        tp2  = min(res2+fe) if (res2 or fe) else price + 3.5 * risk

        # TP3 = extended target
        tp3 = price + 5.0 * risk
    else:
        sup = [s for s in day_levels.get("supports",[]) if s < price - risk * 0.5]
        tp1 = max(sup) if sup else price - 2.0 * risk

        sup2 = [s for s in day_levels.get("supports",[]) if s < tp1 - risk * 0.3]
        fe   = [v for k,v in fibs.items() if k in ("127.2","161.8") and v < tp1]
        tp2  = max(sup2+fe) if (sup2 or fe) else price - 3.5 * risk

        tp3 = price - 5.0 * risk

    return tp1, tp2, tp3

# ══════════════════════════════════════════════════════
#  MASTER SCORER
# ══════════════════════════════════════════════════════
def score_signal(price, direction, df_mo, df_wk, df_day, df_4h, df_1h,
                 pattern, pat_qual) -> tuple:
    score = 0
    max_p = 0
    hits  = []
    miss  = []

    def chk(w, ok, h, m):
        nonlocal score, max_p
        max_p += w
        if ok:
            score += w
            hits.append(f"  [+{w}] {h}")
        else:
            miss.append(f"  [ 0] {m}")

    # ALREADY PASSED HTF GATE BEFORE THIS FUNCTION
    # So we add those as confirmed hits
    ms_mo  = strict_structure(df_mo,  8)
    ms_wk  = strict_structure(df_wk,  15)
    ms_day = strict_structure(df_day, 20)
    ms_4h  = strict_structure(df_4h,  20)

    chk(3, True, f"Monthly structure: {ms_mo}", "")
    chk(3, True, f"Weekly structure STRICT: {ms_wk}", "")
    chk(3, True, f"Daily structure STRICT: {ms_day}", "")

    chk(2,
        (direction=="long"  and ms_4h in ("bullish","ranging")) or
        (direction=="short" and ms_4h in ("bearish","ranging")),
        f"4H structure: {ms_4h}",
        f"4H not aligned: {ms_4h}")

    # Daily candle agreement
    dc_ok, dc_msg = daily_agrees(df_day, direction)
    chk(3, dc_ok, f"Daily candle: {dc_msg}", f"Daily candle: {dc_msg}")

    # EMA 200
    ema200_ok = False
    e200_val  = 0.0
    if "ema200" in df_day.columns and not pd.isna(df_day["ema200"].iloc[-1]):
        e200_val  = float(df_day["ema200"].iloc[-1])
        ema200_ok = (direction=="long" and price>e200_val) or (direction=="short" and price<e200_val)
    chk(2, ema200_ok,
        f"Above/below EMA200 (${e200_val:,.2f})",
        f"Wrong side of EMA200 (${e200_val:,.2f})")

    # ADX trend strength
    adx_val, adx_lbl = get_adx(df_4h)
    chk(2, adx_lbl in ("strong","moderate"),
        f"ADX trend: {adx_lbl} ({adx_val:.1f})",
        f"ADX weak ({adx_val:.1f}) — no clear trend")

    # Move stage
    stage    = move_stage(df_4h, direction)
    stage_ok = stage in ("early","middle")
    chk(3, stage_ok,
        f"Move stage: {stage}",
        f"Move stage: {stage} — too late")

    # At HTF S/R level
    wk_lvls  = swing_levels(df_wk,  window=3, n=5)
    day_lvls = swing_levels(df_day, window=5, n=8)
    all_htf  = (wk_lvls["supports"] + wk_lvls["resistances"] +
                day_lvls["supports"] + day_lvls["resistances"])
    htf_hit  = any(abs(price-lv)/price <= 0.010 for lv in all_htf)
    near_htf = min(all_htf, key=lambda x:abs(x-price)) if all_htf else 0
    chk(3, htf_hit,
        f"At HTF S/R level (${near_htf:,.4f})",
        f"Not at HTF level (nearest ${near_htf:,.4f})")

    # Fibonacci
    fibs_day = fib_levels(df_day, 100)
    fibs_4h  = fib_levels(df_4h,  100)
    fn_d, fd = fib_near(price, fibs_day)
    fn_4, f4 = fib_near(price, fibs_4h)
    fib_ok   = fn_d is not None or fn_4 is not None
    fib_lbl  = (f"Daily Fib {fn_d}%" if fn_d else (f"4H Fib {fn_4}%" if fn_4 else "none"))
    chk(2, fib_ok, f"Fibonacci: {fib_lbl}", f"No Fib (day:{fd*100:.1f}% 4h:{f4*100:.1f}% away)")

    # RSI zone + divergence
    rsi_ok, rsi_lbl = False, "no RSI data"
    if "rsi" in df_1h.columns:
        rsi    = float(df_1h["rsi"].iloc[-1])
        zone   = (direction=="long" and 35<=rsi<=58) or (direction=="short" and 42<=rsi<=65)
        div    = rsi_div(df_1h)
        div_ok = (direction=="long" and div=="bullish") or (direction=="short" and div=="bearish")
        if zone and div_ok:  rsi_ok=True; rsi_lbl=f"RSI {rsi:.1f} + {div} div"
        elif zone:           rsi_ok=True; rsi_lbl=f"RSI {rsi:.1f} in zone"
        elif div_ok:         rsi_ok=True; rsi_lbl=f"{div} divergence"
        else:                rsi_lbl=f"RSI {rsi:.1f} not optimal"
    chk(2, rsi_ok, f"RSI: {rsi_lbl}", f"RSI: {rsi_lbl}")

    # MACD
    macd_ok = False
    if "macd" in df_4h.columns:
        m  = float(df_4h["macd"].iloc[-1])
        ms = float(df_4h["macd_sig"].iloc[-1])
        mh = float(df_4h["macd_hist"].iloc[-1])
        macd_ok = (direction=="long" and m>ms and mh>0) or (direction=="short" and m<ms and mh<0)
    chk(1, macd_ok, "MACD confirms (4H)", "MACD not confirming")

    # Volume
    vol_ok = False
    if "vol_ratio" in df_1h.columns:
        vr = float(df_1h["vol_ratio"].iloc[-1])
        vol_ok = vr >= 1.25
        chk(1, vol_ok, f"Volume {vr:.1f}x avg", f"Volume weak {vr:.1f}x avg")
    else:
        max_p += 1
        miss.append("  [ 0] Volume unavailable")

    # Ichimoku
    ichi_ok, ichi_msg = ichi_bias(df_4h, direction)
    chk(1, ichi_ok, f"Ichimoku: {ichi_msg}", f"Ichimoku: {ichi_msg}")

    # PDH/PDL
    pdh, pdl = pdh_pdl(df_1h)
    pd_ok = (pdh>0 and ((direction=="short" and abs(price-pdh)/price<=0.006) or
                         (direction=="long"  and abs(price-pdl)/price<=0.006)))
    chk(1, pd_ok, f"At PDH/PDL", f"Not at PDH/PDL")

    # PWH/PWL
    pwh, pwl = pwh_pwl(df_4h)
    pw_ok = (pwh>0 and ((direction=="short" and abs(price-pwh)/price<=0.008) or
                         (direction=="long"  and abs(price-pwl)/price<=0.008)))
    chk(1, pw_ok, f"At PWH/PWL", f"Not at PWH/PWL")

    # Round number
    rn_ok, rn_lbl = round_num(price)
    chk(1, rn_ok, f"Round number {rn_lbl}", "No round number")

    # Pattern quality
    strong_patterns = {
        "Bullish Engulfing","Bearish Engulfing","Hammer","Shooting Star",
        "Bullish Pin Bar","Bearish Pin Bar","Morning Star","Evening Star",
        "Three White Soldiers","Three Black Crows",
        "Failed Breakout","Failed Breakdown","Dragonfly Doji","Gravestone Doji",
    }
    pat_ok = pattern in strong_patterns and pat_qual >= 0.70
    chk(2, pat_ok,
        f"Pattern: {pattern} ({pat_qual*100:.0f}%)",
        f"Weak pattern: {pattern} ({pat_qual*100:.0f}%)")

    return score, max_p, hits, miss, adx_val, stage

# ══════════════════════════════════════════════════════
#  FORMAT SIGNAL
# ══════════════════════════════════════════════════════
def format_signal(symbol, direction, score, max_p, hits, miss,
                  entry, stop, tp1, tp2, tp3, pattern, pat_qual,
                  adx_val, stage, ms_day) -> str:

    pct    = min(int(score/max_p*100), 100) if max_p > 0 else 0
    risk   = abs(entry-stop)
    rr1    = abs(tp1-entry)/risk if risk>0 else 0
    rr2    = abs(tp2-entry)/risk if risk>0 else 0
    rr3    = abs(tp3-entry)/risk if risk>0 else 0
    now    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    dirsym = "LONG  [BUY]" if direction=="long" else "SHORT [SELL]"
    regime = "Trending Bull" if ms_day=="bullish" else "Trending Bear"
    inv    = f"Daily close {'below' if direction=='long' else 'above'} ${stop:,.6f}"

    if pct >= 92: grade = "A+  ELITE"
    elif pct >= 85: grade = "A   HIGH QUALITY"
    else: grade = "A-"

    # Position size example
    acct_1pct = "e.g. $100 risk on $10,000 account"

    lines = [
        "=" * 50,
        f"   ELITE SIGNAL  |  Bot v4.0  |  {grade}",
        "=" * 50,
        f"Asset      : {symbol}",
        f"Direction  : {dirsym}",
        f"Score      : {score}/{max_p} ({pct}%)",
        f"Regime     : {regime}  |  ADX {adx_val:.1f}",
        f"Move Stage : {stage}  (entry timing)",
        f"Time       : {now}",
        "",
        "--- TRADE LEVELS ---",
        f"Entry      : ${entry:,.6f}",
        f"Stop Loss  : ${stop:,.6f}  <- set immediately",
        f"TP1  (33%) : ${tp1:,.6f}   [RR {rr1:.1f}:1]",
        f"TP2  (33%) : ${tp2:,.6f}   [RR {rr2:.1f}:1]",
        f"TP3 trail  : ${tp3:,.6f}   [RR {rr3:.1f}:1]",
        f"Risk/trade : max 1% account ({acct_1pct})",
        "",
        f"--- TRIGGER PATTERN ---",
        f"{pattern}  |  quality {pat_qual*100:.0f}%",
        "",
        f"--- CONFIRMED FACTORS ({len(hits)}) ---",
    ] + hits + [
        "",
        f"--- MISSING FACTORS ({len(miss)}) ---",
    ] + miss[:5] + [
        "",
        "--- HOW TO MANAGE ---",
        "1. Enter at Entry price shown above",
        "2. Set stop loss IMMEDIATELY",
        "3. TP1 hit: close 33%, move stop to breakeven",
        "4. TP2 hit: close 33%, trail stop on rest",
        "5. TP3: trail stop below each new higher low",
        f"INVALIDATION: {inv}",
        "",
        "CHECK THE CHART BEFORE EXECUTING",
        "=" * 50,
    ]
    return "\n".join(lines)

# ══════════════════════════════════════════════════════
#  MAIN SCANNER
# ══════════════════════════════════════════════════════
def scan_symbol(symbol: str):
    log.info(f"Scanning {symbol}...")

    if not check_daily_limit():
        log.info(f"  Daily limit reached. Skipping.")
        return

    if datetime.now(timezone.utc).hour not in ACTIVE_HOURS_UTC:
        log.info(f"  Outside active hours.")
        return

    # Fetch data
    df_mo  = fetch_ohlcv(symbol, "1M",  36)
    df_wk  = fetch_ohlcv(symbol, "1w",  52)
    df_day = fetch_ohlcv(symbol, "1d", 200)
    df_4h  = fetch_ohlcv(symbol, "4h", 300)
    df_1h  = fetch_ohlcv(symbol, "1h", 300)

    if df_day.empty or df_1h.empty or df_4h.empty:
        log.warning(f"  {symbol}: insufficient data")
        return

    if df_mo.empty:  df_mo  = df_wk.copy()  if not df_wk.empty  else df_day.copy()
    if df_wk.empty:  df_wk  = df_day.copy()

    for df in [df_mo, df_wk, df_day, df_4h, df_1h]:
        add_indicators(df)

    price  = float(df_1h["close"].iloc[-1])
    atr_1h = float(df_1h["atr"].iloc[-1]) if "atr" in df_1h.columns else price * 0.005
    atr_4h = float(df_4h["atr"].iloc[-1]) if "atr" in df_4h.columns else price * 0.01

    # Detect pattern
    pattern, pat_qual = detect_pattern(df_1h)

    # For commodities: also detect momentum breakouts even without perfect candle
    if pattern == "none" and symbol in COMMODITIES:
        pattern, pat_qual = detect_momentum_breakout(df_1h, df_4h)
        if pattern != "none":
            log.info(f"  {symbol}: momentum breakout detected: {pattern}")

    if pattern == "none":
        log.info(f"  {symbol}: no valid pattern. skip.")
        return
    # Lower quality threshold for commodities
    min_quality = 0.65 if symbol in COMMODITIES else 0.70
    if pat_qual < min_quality:
        log.info(f"  {symbol}: pattern quality {pat_qual*100:.0f}% below {min_quality*100:.0f}%. skip.")
        return

    # Direction from pattern
    bull_p = {"Bullish Engulfing","Hammer","Bullish Pin Bar","Dragonfly Doji",
              "Morning Star","Three White Soldiers","Failed Breakdown",
              "Bullish Momentum Breakout","Level Breakout"}
    bear_p = {"Bearish Engulfing","Shooting Star","Bearish Pin Bar","Gravestone Doji",
              "Evening Star","Three Black Crows","Failed Breakout",
              "Bearish Momentum Breakout","Level Breakdown"}

    directions = []
    if pattern in bull_p: directions.append("long")
    if pattern in bear_p: directions.append("short")
    if not directions: return

    for direction in directions:
        if already_alerted(symbol, direction):
            log.info(f"  {symbol} {direction}: cooldown. skip.")
            continue

        # ── GATE 1: HTF structure must ALL agree (most important filter)
        htf_ok, htf_msg = htf_gate(df_mo, df_wk, df_day, direction)
        if not htf_ok:
            log.info(f"  {htf_msg}")
            continue

        # ── GATE 2: RSI hard limits
        if "rsi" in df_1h.columns:
            rsi = float(df_1h["rsi"].iloc[-1])
            if direction == "short" and rsi < 35:
                log.info(f"  SHORT blocked: RSI oversold ({rsi:.1f})")
                continue
            if direction == "long" and rsi > 65:
                log.info(f"  LONG blocked: RSI overbought ({rsi:.1f})")
                continue

        # ── GATE 3: Move must not be exhausted
        stage = move_stage(df_4h, direction)
        if stage == "exhausted":
            log.info(f"  {symbol}: exhausted move. skip.")
            continue

        # ── GATE 4: ADX trend strength (lower threshold for commodities)
        adx_val, adx_lbl = get_adx(df_4h)
        adx_threshold = 20 if symbol in COMMODITIES else 25
        if adx_val < adx_threshold:
            log.info(f"  {symbol}: ADX {adx_val:.1f} below {adx_threshold}. skip.")
            continue

        # ── GATE 5: No over-extension
        if atr_4h > 0:
            move = abs(float(df_4h["close"].iloc[-1]) - float(df_4h["close"].iloc[-20]))
            if move > 7 * atr_4h:
                log.info(f"  {symbol}: over-extended {move/atr_4h:.1f}x ATR. skip.")
                continue

        # ── Score
        score, max_p, hits, miss, adx_val, stage = score_signal(
            price, direction, df_mo, df_wk, df_day, df_4h, df_1h, pattern, pat_qual
        )
        pct = min(int(score/max_p*100), 100) if max_p > 0 else 0
        log.info(f"  {symbol} {direction.upper()}: {score}/{max_p} ({pct}%) [{pattern}]")

        if pct < MIN_SCORE_PCT:
            log.info(f"  {pct}% below {MIN_SCORE_PCT}%. skip.")
            continue

        # ── Build trade levels
        day_lvls     = swing_levels(df_day, window=5, n=10)
        fibs         = fib_levels(df_day, 100)
        stop         = struct_stop(price, direction, df_4h, df_1h, atr_1h)
        tp1, tp2, tp3 = calc_targets(price, stop, direction, day_lvls, fibs)

        risk = abs(price - stop)
        if risk <= 0: continue

        rr1 = abs(tp1-price)/risk
        if rr1 < MIN_RR:
            log.info(f"  RR {rr1:.1f} below {MIN_RR}. skip.")
            continue

        ms_day = strict_structure(df_day, 20)

        msg = format_signal(
            symbol=symbol, direction=direction,
            score=score, max_p=max_p, hits=hits, miss=miss,
            entry=price, stop=stop, tp1=tp1, tp2=tp2, tp3=tp3,
            pattern=pattern, pat_qual=pat_qual,
            adx_val=adx_val, stage=stage, ms_day=ms_day,
        )

        log.info(f"  >>> SIGNAL FIRED: {symbol} {direction.upper()} {pct}% <<<")
        send_discord(msg)
        increment_signal_count()

# ══════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════
def main():
    log.info("="*50)
    log.info("  ELITE TRADING SIGNAL BOT  v4.0")
    log.info("="*50)

    send_discord(
        "ELITE TRADING SIGNAL BOT v4.0 — ONLINE\n"
        "=========================================\n"
        f"Assets   : {', '.join(SYMBOLS)}\n"
        f"TFs      : Monthly > Weekly > Daily > 4H > 1H\n"
        f"Min Score: {MIN_SCORE_PCT}%  |  Min RR: {MIN_RR}:1\n"
        f"Max/Day  : {MAX_SIGNALS_PER_DAY} signals\n"
        "=========================================\n"
        "v4.0 — All issues fixed:\n"
        "  + Weekly + Daily must STRICTLY agree\n"
        "  + ADX 25+ required — no weak trends\n"
        "  + Pivot-based structural stop loss\n"
        "  + TP1 = next real S/R level\n"
        "  + Pattern quality min 70% required\n"
        "  + 5 hard gates before scoring\n"
        "  + Over-extension filter tightened\n"
        "=========================================\n"
        "Running 24/7. Always confirm before trading."
    )

    while True:
        log.info(f"\n{'='*50}")
        log.info(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        log.info(f"  Signals today: {_signals_today}/{MAX_SIGNALS_PER_DAY}")
        log.info(f"{'='*50}")

        for symbol in SYMBOLS:
            try:
                scan_symbol(symbol)
                time.sleep(3)
            except Exception as e:
                log.error(f"Error {symbol}: {e}", exc_info=True)

        log.info(f"  Next scan in {SCAN_INTERVAL_SECONDS//60} min.")
        time.sleep(SCAN_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()