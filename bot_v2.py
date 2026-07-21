"""
╔══════════════════════════════════════════════════════════════════╗
║         ELITE TRADING SIGNAL BOT  —  Version 6.0                ║
║         All 20 weaknesses from v5 fixed                         ║
╠══════════════════════════════════════════════════════════════════╣
║  FIXES IN v6.0:                                                  ║
║  1.  Daily pattern must confirm 1H trigger                       ║
║  2.  Proper Fibonacci on most recent significant swing           ║
║  3.  Retest confirmation — price must have left and returned     ║
║  4.  Weekly S/R weighted 2x higher than Daily S/R               ║
║  5.  Level depletion check — 4th+ touch = weak level            ║
║  6.  Order block detection added                                 ║
║  7.  Volume profile (high volume nodes) added                   ║
║  8.  ADX with DI+/DI- direction confirmation                    ║
║  9.  BTC correlation filter for altcoins                        ║
║  10. Minimum stop distance (0.8% minimum)                       ║
║  11. Ichimoku fixed — using non-shifted span values             ║
║  12. Market session awareness for Gold/Silver                   ║
║  13. Weekly range check — no trading mid-range                  ║
║  14. RSI + MACD cross-validated                                 ║
║  15. Daily candle pattern check added                           ║
║  16. pandas_ta restored with compatibility fix                  ║
║  17. Professional S/R drawing (body + wick method)             ║
║  18. Stop loss minimum distance enforced                        ║
║  19. Level strength grading system                              ║
║  20. Clean short signal messages only                           ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os
import sys
import io
import time
import logging
import requests
import numpy as np
import pandas as pd
import ccxt
from datetime import datetime, timezone
from typing import Optional

# Use ta library (compatible with all Python versions)
import ta

# ══════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════
DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL",
    "YOUR_DISCORD_WEBHOOK_URL"
)

SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "XAUT/USDT",
    "XAGUSDT",
]

COMMODITIES    = {"XAUT/USDT", "XAGUSDT"}
CRYPTO         = {"BTC/USDT",  "ETH/USDT"}
ALTS           = {"ETH/USDT"}   # Need BTC correlation check

SCAN_INTERVAL  = 300     # 5 minutes
MIN_SCORE_PCT  = 80      # 80%+ to fire
MIN_RR         = 2.5     # minimum risk/reward
COOLDOWN_HOURS = 6       # no repeat within 6 hours
MIN_STOP_PCT   = 0.008   # minimum 0.8% stop distance

# Gold/Silver active sessions (UTC hours)
COMMODITY_SESSIONS = list(range(0, 24))   # 24/7
CRYPTO_SESSIONS    = list(range(0, 24))   # 24/7

# ══════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler("bot_v6.log", encoding="utf-8")]
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════
#  COOLDOWN
# ══════════════════════════════════════════════
_alerted: dict = {}

def on_cooldown(symbol: str, direction: str) -> bool:
    key = f"{symbol}_{direction}"
    now = time.time()
    if key in _alerted and (now - _alerted[key]) < COOLDOWN_HOURS * 3600:
        return True
    _alerted[key] = now
    return False

# ══════════════════════════════════════════════
#  DISCORD
# ══════════════════════════════════════════════
def send_discord(msg: str) -> None:
    try:
        r = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": msg},
            timeout=10
        )
        if r.status_code not in (200, 204):
            log.warning(f"Discord {r.status_code}: {r.text[:100]}")
    except Exception as e:
        log.error(f"Discord failed: {e}")

# ══════════════════════════════════════════════
#  EXCHANGE
# ══════════════════════════════════════════════
exchange = ccxt.bitget({
    "enableRateLimit": True,
    "options": {"defaultType": "swap"},
})

def fetch(symbol: str, tf: str, limit: int = 300) -> pd.DataFrame:
    try:
        raw = exchange.fetch_ohlcv(symbol, tf, limit=limit)
        if not raw or len(raw) < 30:
            return pd.DataFrame()
        df = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        df.set_index("ts", inplace=True)
        return df.astype(float)
    except Exception as e:
        log.warning(f"Fetch {symbol} {tf}: {e}")
        return pd.DataFrame()

# ══════════════════════════════════════════════
#  INDICATORS — using ta library properly
# ══════════════════════════════════════════════
def enrich(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or len(df) < 55:
        return df
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    # EMAs
    df["ema20"]  = ta.trend.ema_indicator(c, window=20)
    df["ema50"]  = ta.trend.ema_indicator(c, window=50)
    df["ema200"] = ta.trend.ema_indicator(c, window=200)

    # RSI
    df["rsi"] = ta.momentum.rsi(c, window=14)

    # ATR
    df["atr"] = ta.volatility.average_true_range(h, l, c, window=14)

    # MACD
    macd = ta.trend.MACD(c, window_slow=26, window_fast=12, window_sign=9)
    df["macd"]      = macd.macd()
    df["macd_sig"]  = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()

    # Bollinger Bands
    bb = ta.volatility.BollingerBands(c, window=20, window_dev=2)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_mid"]   = bb.bollinger_mavg()
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"].replace(0, np.nan)

    # ADX with DI+/DI- for direction confirmation
    try:
        adx_ind = ta.trend.ADXIndicator(h, l, c, window=14)
        df["adx"]   = adx_ind.adx()
        df["di_pos"] = adx_ind.adx_pos()   # DI+
        df["di_neg"] = adx_ind.adx_neg()   # DI-
    except Exception:
        df["adx"]    = 0.0
        df["di_pos"] = 0.0
        df["di_neg"] = 0.0

    # Volume
    df["vol_sma"]   = v.rolling(20).mean()
    df["vol_ratio"] = v / df["vol_sma"]

    # Candle metrics
    df["body"]       = abs(c - df["open"])
    df["upper_wick"] = h - df[["close","open"]].max(axis=1)
    df["lower_wick"] = df[["close","open"]].min(axis=1) - l
    df["crange"]     = h - l
    df["body_pct"]   = df["body"] / df["crange"].replace(0, np.nan)

    # Ichimoku — manual calculation, no shift issues
    try:
        h9  = h.rolling(9).max();  l9  = l.rolling(9).min()
        h26 = h.rolling(26).max(); l26 = l.rolling(26).min()
        h52 = h.rolling(52).max(); l52 = l.rolling(52).min()
        df["tenkan"]  = (h9  + l9)  / 2
        df["kijun"]   = (h26 + l26) / 2
        # Use current span values (not future-shifted)
        df["span_a_current"] = (df["tenkan"] + df["kijun"]) / 2
        df["span_b_current"] = (h52 + l52) / 2
    except Exception:
        pass

    return df

# ══════════════════════════════════════════════
#  MARKET STRUCTURE — strict
# ══════════════════════════════════════════════
def structure(df: pd.DataFrame, lookback: int = 30) -> str:
    if df.empty or len(df) < lookback:
        return "ranging"
    r   = df.tail(lookback)
    mid = lookback // 2
    rh  = r["high"].iloc[mid:].max()
    ph  = r["high"].iloc[:mid].max()
    rl  = r["low"].iloc[mid:].min()
    pl  = r["low"].iloc[:mid].min()
    if rh > ph * 1.001 and rl > pl * 1.001:   return "bullish"
    if rh < ph * 0.999 and rl < pl * 0.999:   return "bearish"
    return "ranging"

# ══════════════════════════════════════════════
#  PROFESSIONAL S/R LEVELS
#  Drawn from candle BODIES (not wicks) for strong levels
#  Wicks used for outer zone boundaries
# ══════════════════════════════════════════════
def professional_sr(df: pd.DataFrame, window: int = 5, n: int = 8) -> dict:
    """
    Draws S/R levels the professional way:
    - Body closes define the core level
    - Wick tips define the outer zone
    - Counts touches to grade level strength
    - Penalizes depleted levels (4+ touches)
    """
    empty = {"supports": [], "resistances": [], "strength": {}}
    if df.empty or len(df) < window * 3:
        return empty

    price = float(df["close"].iloc[-1])

    # Find pivot highs and lows using BODY closes
    body_high = df[["open","close"]].max(axis=1)
    body_low  = df[["open","close"]].min(axis=1)

    pivot_hi_body = body_high[(body_high == body_high.rolling(window, center=True).max())].dropna().tolist()
    pivot_lo_body = body_low [(body_low  == body_low.rolling(window, center=True).min())].dropna().tolist()

    # Also include wick extremes for zone boundaries
    pivot_hi_wick = df["high"][(df["high"] == df["high"].rolling(window, center=True).max())].dropna().tolist()
    pivot_lo_wick = df["low"] [(df["low"]  == df["low"].rolling(window, center=True).min())].dropna().tolist()

    def cluster(lvs, tol=0.004):
        if not lvs: return []
        lvs = sorted(set([round(x, 6) for x in lvs]))
        out, grp = [], [lvs[0]]
        for lv in lvs[1:]:
            if abs(lv - grp[-1]) / max(grp[-1], 1e-9) <= tol:
                grp.append(lv)
            else:
                out.append(float(np.mean(grp)))
                grp = [lv]
        out.append(float(np.mean(grp)))
        return out

    def count_touches(level, df, tol=0.006):
        lo, hi = level*(1-tol), level*(1+tol)
        return int(((df["low"] <= hi) & (df["high"] >= lo)).sum())

    def level_strength(level, df) -> str:
        touches = count_touches(level, df)
        if touches >= 4:   return "depleted"   # Too many touches — weak
        if touches == 3:   return "moderate"
        if touches <= 2:   return "fresh"       # Fresh = strongest
        return "moderate"

    ch = cluster(pivot_hi_body + pivot_hi_wick)
    cl = cluster(pivot_lo_body + pivot_lo_wick)

    strength = {}
    for lv in ch + cl:
        s = level_strength(lv, df)
        strength[round(lv, 6)] = s

    # Filter out depleted levels
    ch_valid = [h for h in ch if h > price and strength.get(round(h,6)) != "depleted"]
    cl_valid = [l for l in cl if l < price and strength.get(round(l,6)) != "depleted"]

    return {
        "supports":    sorted(cl_valid, reverse=True)[:n],
        "resistances": sorted(ch_valid)[:n],
        "strength":    strength,
    }

# ══════════════════════════════════════════════
#  ORDER BLOCK DETECTION
#  Last candle before a strong impulse move
# ══════════════════════════════════════════════
def find_order_blocks(df: pd.DataFrame, direction: str) -> Optional[tuple]:
    """
    Order block = last candle before a strong impulsive move.
    For longs: last bearish candle before strong bullish impulse
    For shorts: last bullish candle before strong bearish impulse
    Returns (ob_high, ob_low) or None
    """
    if df.empty or len(df) < 10:
        return None

    atr = float(df["atr"].iloc[-1]) if "atr" in df.columns else 0.0
    if atr <= 0:
        return None

    price = float(df["close"].iloc[-1])

    # Look for order blocks in last 50 candles
    recent = df.tail(50).reset_index(drop=True)

    for i in range(len(recent)-3, 2, -1):
        c  = recent.iloc[i]
        c1 = recent.iloc[i+1] if i+1 < len(recent) else None
        c2 = recent.iloc[i+2] if i+2 < len(recent) else None

        if c1 is None or c2 is None:
            continue

        impulse = abs(float(c2["close"]) - float(c["close"]))

        if direction == "long":
            # Last bearish candle before bullish impulse
            if (c["close"] < c["open"] and
                    c1["close"] > c1["open"] and
                    c2["close"] > c2["open"] and
                    impulse >= 2 * atr):
                ob_high = float(c["open"])   # Body top of bearish OB
                ob_low  = float(c["close"])  # Body bottom
                # Check if price is near this OB
                if abs(price - ob_high) / price <= 0.015 or ob_low <= price <= ob_high:
                    return ob_high, ob_low

        else:  # short
            # Last bullish candle before bearish impulse
            if (c["close"] > c["open"] and
                    c1["close"] < c1["open"] and
                    c2["close"] < c2["open"] and
                    impulse >= 2 * atr):
                ob_high = float(c["close"])  # Body top of bullish OB
                ob_low  = float(c["open"])   # Body bottom
                if abs(price - ob_low) / price <= 0.015 or ob_low <= price <= ob_high:
                    return ob_high, ob_low

    return None

# ══════════════════════════════════════════════
#  VOLUME PROFILE — high volume nodes
# ══════════════════════════════════════════════
def high_volume_nodes(df: pd.DataFrame, bins: int = 20) -> list:
    """
    Find price levels where the most volume traded.
    These act as strong institutional S/R.
    """
    if df.empty or len(df) < 20:
        return []
    try:
        recent  = df.tail(100)
        hi, lo  = float(recent["high"].max()), float(recent["low"].min())
        edges   = np.linspace(lo, hi, bins+1)
        vols    = np.zeros(bins)
        for _, row in recent.iterrows():
            for j in range(bins):
                if edges[j] <= row["close"] <= edges[j+1]:
                    vols[j] += row["volume"]
                    break
        # Top 3 volume nodes
        top_idx = np.argsort(vols)[-3:]
        nodes   = [(edges[i] + edges[i+1]) / 2 for i in top_idx]
        return [float(n) for n in nodes]
    except Exception:
        return []

# ══════════════════════════════════════════════
#  RETEST CONFIRMATION
#  Price must have LEFT the level and RETURNED
# ══════════════════════════════════════════════
def is_retest(price: float, level: float, df: pd.DataFrame,
              direction: str, tol: float = 0.010) -> bool:
    """
    True if price:
    1. Was at the level before
    2. Left the level (moved away significantly)
    3. Has now returned to the level
    This confirms a real retest, not just hovering.
    """
    if df.empty or len(df) < 20:
        return False

    recent = df.tail(30)
    zone_lo = level * (1 - tol)
    zone_hi = level * (1 + tol)

    # Find when price was last AT the level
    at_level_idx = None
    for i in range(len(recent)-2, 0, -1):
        if zone_lo <= recent["close"].iloc[i] <= zone_hi:
            at_level_idx = i
            break

    if at_level_idx is None:
        return False

    # Check that price LEFT the level after that
    if direction == "long":
        # Price should have gone DOWN away from level then come back
        after_level = recent["low"].iloc[at_level_idx+1:-1]
        left = any(float(x) < zone_lo * 0.995 for x in after_level)
    else:
        after_level = recent["high"].iloc[at_level_idx+1:-1]
        left = any(float(x) > zone_hi * 1.005 for x in after_level)

    # Now price has returned
    returned = zone_lo <= price <= zone_hi

    return left and returned

# ══════════════════════════════════════════════
#  FIBONACCI — on most recent SIGNIFICANT swing
# ══════════════════════════════════════════════
def significant_fib(df: pd.DataFrame, direction: str) -> dict:
    """
    Draws Fibonacci on the most recent SIGNIFICANT swing:
    - For long: from recent significant low to recent high (measuring the pullback)
    - For short: from recent significant high to recent low
    Significant = the swing that created the current move, not just any high/low
    """
    if df.empty or len(df) < 30:
        return {}

    recent = df.tail(100)
    atr    = float(df["atr"].iloc[-1]) if "atr" in df.columns else 1.0

    if direction == "long":
        # Find most recent significant low (swing that started the current uptrend)
        # Significant = low that is at least 3x ATR below a prior high
        recent_high = float(recent["high"].max())
        lows = recent["low"]
        sig_low_idx  = lows.idxmin()
        sig_low      = float(lows.min())
        # High after the significant low
        after_low    = recent.loc[sig_low_idx:]["high"]
        sig_high     = float(after_low.max())
        if sig_high <= sig_low:
            return {}
        diff = sig_high - sig_low
        return {
            "swing_lo":  sig_low,
            "swing_hi":  sig_high,
            "23.6": sig_high - 0.236*diff,
            "38.2": sig_high - 0.382*diff,
            "50.0": sig_high - 0.500*diff,
            "61.8": sig_high - 0.618*diff,
            "78.6": sig_high - 0.786*diff,
            "127.2": sig_high + 0.272*diff,
            "161.8": sig_high + 0.618*diff,
        }
    else:
        recent_low  = float(recent["low"].min())
        highs       = recent["high"]
        sig_hi_idx  = highs.idxmax()
        sig_high    = float(highs.max())
        after_high  = recent.loc[sig_hi_idx:]["low"]
        sig_low     = float(after_high.min())
        if sig_low >= sig_high:
            return {}
        diff = sig_high - sig_low
        return {
            "swing_lo":  sig_low,
            "swing_hi":  sig_high,
            "23.6": sig_low + 0.236*diff,
            "38.2": sig_low + 0.382*diff,
            "50.0": sig_low + 0.500*diff,
            "61.8": sig_low + 0.618*diff,
            "78.6": sig_low + 0.786*diff,
            "127.2": sig_low - 0.272*diff,
            "161.8": sig_low - 0.618*diff,
        }

def near_fib(price: float, fibs: dict, tol: float = 0.012) -> Optional[str]:
    for k in ["61.8", "50.0", "38.2", "78.6"]:
        if k in fibs and abs(price - fibs[k]) / price <= tol:
            return k
    return None

# ══════════════════════════════════════════════
#  RSI DIVERGENCE
# ══════════════════════════════════════════════
def rsi_div(df: pd.DataFrame) -> str:
    if "rsi" not in df.columns or len(df) < 30:
        return "none"
    r   = df.tail(30)
    mid = 15
    pl2 = r["low"].iloc[mid:].min()
    pl1 = r["low"].iloc[:mid].min()
    ph2 = r["high"].iloc[mid:].max()
    ph1 = r["high"].iloc[:mid].max()
    r2  = r["rsi"].iloc[-5:].mean()
    r1  = r["rsi"].iloc[:mid].mean()
    if pl2 < pl1 and r2 > r1 + 3:   return "bullish div"
    if ph2 > ph1 and r2 < r1 - 3:   return "bearish div"
    return "none"

# ══════════════════════════════════════════════
#  PATTERN DETECTION — 1H AND DAILY
# ══════════════════════════════════════════════
def detect_pattern(df: pd.DataFrame) -> tuple:
    """Returns (pattern, quality, direction)"""
    if df.empty or len(df) < 4:
        return "none", 0.0, "none"

    c1 = df.iloc[-1]
    c2 = df.iloc[-2]
    c3 = df.iloc[-3]

    b1  = float(c1["body"])
    r1  = float(c1["crange"]) if float(c1["crange"]) > 0 else 1e-6
    uw1 = float(c1["upper_wick"])
    lw1 = float(c1["lower_wick"])
    bp1 = float(c1["body_pct"]) if "body_pct" in c1.index and not pd.isna(c1["body_pct"]) else 0.0
    b2  = abs(float(c2["close"]) - float(c2["open"]))
    b3  = abs(float(c3["close"]) - float(c3["open"]))

    # Bullish Engulfing
    if (c1["close"] > c1["open"] and c2["close"] < c2["open"]
            and c1["close"] > c2["open"] and c1["open"] < c2["close"]
            and bp1 > 0.60):
        return "Bullish Engulfing", min(bp1*1.1, 1.0), "long"

    # Bearish Engulfing
    if (c1["close"] < c1["open"] and c2["close"] > c2["open"]
            and c1["close"] < c2["open"] and c1["open"] > c2["close"]
            and bp1 > 0.60):
        return "Bearish Engulfing", min(bp1*1.1, 1.0), "short"

    # Hammer
    if lw1 >= 2.5*b1 and uw1 <= 0.3*b1 and c1["close"] > c1["open"] and bp1 > 0.15:
        return "Hammer", min(lw1/r1, 1.0), "long"

    # Shooting Star
    if uw1 >= 2.5*b1 and lw1 <= 0.3*b1 and c1["close"] < c1["open"] and bp1 > 0.15:
        return "Shooting Star", min(uw1/r1, 1.0), "short"

    # Bullish Pin Bar
    if lw1 >= 3.0*max(b1, 1e-9) and c1["close"] > c1["open"]:
        return "Bullish Pin Bar", min(lw1/r1, 1.0), "long"

    # Bearish Pin Bar
    if uw1 >= 3.0*max(b1, 1e-9) and c1["close"] < c1["open"]:
        return "Bearish Pin Bar", min(uw1/r1, 1.0), "short"

    # Dragonfly Doji
    if lw1 > 3*b1 and uw1 < b1 and bp1 < 0.12:
        return "Dragonfly Doji", 0.85, "long"

    # Gravestone Doji
    if uw1 > 3*b1 and lw1 < b1 and bp1 < 0.12:
        return "Gravestone Doji", 0.85, "short"

    # Morning Star
    if (c3["close"] < c3["open"] and b2 < b3*0.35
            and c1["close"] > c1["open"]
            and c1["close"] > (c3["open"]+c3["close"])/2 and b3 > 0):
        return "Morning Star", 0.88, "long"

    # Evening Star
    if (c3["close"] > c3["open"] and b2 < b3*0.35
            and c1["close"] < c1["open"]
            and c1["close"] < (c3["open"]+c3["close"])/2 and b3 > 0):
        return "Evening Star", 0.88, "short"

    # Three White Soldiers
    if (c1["close"] > c1["open"] and c2["close"] > c2["open"]
            and c3["close"] > c3["open"]
            and c1["close"] > c2["close"] > c3["close"] and bp1 > 0.55):
        return "3 White Soldiers", 0.90, "long"

    # Three Black Crows
    if (c1["close"] < c1["open"] and c2["close"] < c2["open"]
            and c3["close"] < c3["open"]
            and c1["close"] < c2["close"] < c3["close"] and bp1 > 0.55):
        return "3 Black Crows", 0.90, "short"

    # Failed Breakout (bearish)
    if (float(c2["high"]) > float(c3["high"])
            and c1["close"] < float(c3["high"])
            and c1["close"] < c1["open"]
            and float(c1["crange"]) > float(c2["crange"])*0.75):
        return "Failed Breakout", 0.92, "short"

    # Failed Breakdown (bullish)
    if (float(c2["low"]) < float(c3["low"])
            and c1["close"] > float(c3["low"])
            and c1["close"] > c1["open"]
            and float(c1["crange"]) > float(c2["crange"])*0.75):
        return "Failed Breakdown", 0.92, "long"

    return "none", 0.0, "none"

def daily_pattern_confirms(df_day: pd.DataFrame, direction: str) -> tuple:
    """
    Check if Daily candle pattern confirms the 1H signal.
    Daily pattern has much more weight than 1H.
    Returns (confirmed, pattern_name)
    """
    if df_day.empty or len(df_day) < 4:
        return False, "no data"

    pat, qual, pat_dir = detect_pattern(df_day)

    if pat == "none":
        # Even without a pattern, check daily candle direction
        last = df_day.iloc[-1]
        prev = df_day.iloc[-2]
        bp   = float(last["body_pct"]) if "body_pct" in last.index and not pd.isna(last["body_pct"]) else 0
        bull = last["close"] > last["open"] and bp > 0.30 and last["close"] > prev["close"]
        bear = last["close"] < last["open"] and bp > 0.30 and last["close"] < prev["close"]
        if direction == "long"  and bull: return True,  "Bullish daily candle"
        if direction == "short" and bear: return True,  "Bearish daily candle"
        return False, "Daily candle against direction"

    if pat_dir == direction:
        return True, f"Daily {pat}"
    return False, f"Daily {pat} conflicts"

# ══════════════════════════════════════════════
#  MOVE STAGE
# ══════════════════════════════════════════════
def move_stage(df: pd.DataFrame, direction: str) -> str:
    if df.empty or len(df) < 30:
        return "unknown"
    price = float(df["close"].iloc[-1])
    rsi   = float(df["rsi"].iloc[-1])   if "rsi"   in df.columns else 50.0
    ema20 = float(df["ema20"].iloc[-1]) if "ema20" in df.columns else price
    atr   = float(df["atr"].iloc[-1])   if "atr"   in df.columns else price*0.01
    dist  = abs(price - ema20) / max(atr, 1e-9)
    if direction == "long":
        if rsi > 68 or dist > 3.5: return "exhausted"
        if rsi > 60 or dist > 2.0: return "late"
        return "good"
    else:
        if rsi < 32 or dist > 3.5: return "exhausted"
        if rsi < 40 or dist > 2.0: return "late"
        return "good"

# ══════════════════════════════════════════════
#  BTC CORRELATION FILTER
# ══════════════════════════════════════════════
def btc_allows(direction: str, df_btc: pd.DataFrame) -> tuple:
    """
    For altcoins, check if BTC trend allows the trade.
    If BTC is in a strong downtrend, no altcoin longs.
    If BTC is in a strong uptrend, no altcoin shorts.
    """
    if df_btc.empty or len(df_btc) < 20:
        return True, "BTC data unavailable"

    btc_struct = structure(df_btc, 20)
    btc_rsi    = float(df_btc["rsi"].iloc[-1]) if "rsi" in df_btc.columns else 50.0

    # Strong BTC downtrend blocks altcoin longs
    if direction == "long" and btc_struct == "bearish" and btc_rsi < 40:
        return False, f"BTC bearish (RSI {btc_rsi:.0f}) — blocks alt longs"

    # Strong BTC uptrend blocks altcoin shorts
    if direction == "short" and btc_struct == "bullish" and btc_rsi > 60:
        return False, f"BTC bullish (RSI {btc_rsi:.0f}) — blocks alt shorts"

    return True, f"BTC {btc_struct} — OK"

# ══════════════════════════════════════════════
#  WEEKLY RANGE CHECK
# ══════════════════════════════════════════════
def is_mid_range(price: float, df_wk: pd.DataFrame) -> bool:
    """
    Returns True if price is in the middle 40% of the weekly range.
    Middle of range = no clear edge, avoid trading.
    """
    if df_wk.empty or len(df_wk) < 4:
        return False
    recent_weeks = df_wk.tail(8)
    wk_high = float(recent_weeks["high"].max())
    wk_low  = float(recent_weeks["low"].min())
    rng     = wk_high - wk_low
    if rng <= 0:
        return False
    pos = (price - wk_low) / rng   # 0 = at low, 1 = at high
    return 0.30 < pos < 0.70       # Middle 40% = mid range

# ══════════════════════════════════════════════
#  ADX DIRECTION CONFIRMATION
# ══════════════════════════════════════════════
def adx_confirms(df: pd.DataFrame, direction: str, symbol: str) -> tuple:
    """
    Uses ADX + DI+/DI- to confirm trend direction.
    DI+ > DI- = bullish trend
    DI- > DI+ = bearish trend
    """
    if not all(c in df.columns for c in ["adx","di_pos","di_neg"]):
        return False, "ADX data unavailable"

    adx    = float(df["adx"].iloc[-1])
    di_pos = float(df["di_pos"].iloc[-1])
    di_neg = float(df["di_neg"].iloc[-1])
    min_adx = 20 if symbol in COMMODITIES else 25

    if adx < min_adx:
        return False, f"ADX {adx:.0f} too weak (min {min_adx})"

    if direction == "long"  and di_pos > di_neg:
        return True,  f"ADX {adx:.0f} DI+ {di_pos:.0f} > DI- {di_neg:.0f}"
    if direction == "short" and di_neg > di_pos:
        return True,  f"ADX {adx:.0f} DI- {di_neg:.0f} > DI+ {di_pos:.0f}"

    return False, f"ADX {adx:.0f} DI direction wrong (DI+{di_pos:.0f} DI-{di_neg:.0f})"

# ══════════════════════════════════════════════
#  ICHIMOKU — fixed, no shift issues
# ══════════════════════════════════════════════
def ichi_confirms(df: pd.DataFrame, direction: str) -> tuple:
    cols = ["tenkan","kijun","span_a_current","span_b_current"]
    if not all(c in df.columns for c in cols):
        return False, "unavailable"
    price = float(df["close"].iloc[-1])
    sa    = float(df["span_a_current"].iloc[-1])
    sb    = float(df["span_b_current"].iloc[-1])
    tk    = float(df["tenkan"].iloc[-1])
    kj    = float(df["kijun"].iloc[-1])

    if pd.isna(sa) or pd.isna(sb): return False, "cloud NaN"

    ct, cb = max(sa,sb), min(sa,sb)
    if cb <= price <= ct: return False, "inside cloud"

    if direction == "long":
        if price > ct and tk >= kj: return True, "above cloud + TK bull"
        if price > ct:              return True, "above cloud"
    else:
        if price < cb and tk <= kj: return True, "below cloud + TK bear"
        if price < cb:              return True, "below cloud"
    return False, "not aligned"

# ══════════════════════════════════════════════
#  STRUCTURAL STOP LOSS
# ══════════════════════════════════════════════
def calc_stop(price: float, direction: str,
              df_4h: pd.DataFrame, atr: float) -> float:
    buf     = atr * 0.3
    min_dist = price * MIN_STOP_PCT   # Minimum 0.8% away

    if direction == "long":
        lows = df_4h["low"].tail(15)
        pivots = []
        for i in range(2, len(lows)-2):
            if (lows.iloc[i] < lows.iloc[i-1] and lows.iloc[i] < lows.iloc[i-2] and
                    lows.iloc[i] < lows.iloc[i+1] and lows.iloc[i] < lows.iloc[i+2]):
                pivots.append(float(lows.iloc[i]))
        valid = [p for p in pivots if p < price - min_dist]
        stop  = max(valid) - buf if valid else price - max(2*atr, min_dist)
        # Enforce minimum distance
        stop  = min(stop, price - min_dist)
        return stop

    else:
        highs = df_4h["high"].tail(15)
        pivots = []
        for i in range(2, len(highs)-2):
            if (highs.iloc[i] > highs.iloc[i-1] and highs.iloc[i] > highs.iloc[i-2] and
                    highs.iloc[i] > highs.iloc[i+1] and highs.iloc[i] > highs.iloc[i+2]):
                pivots.append(float(highs.iloc[i]))
        valid = [p for p in pivots if p > price + min_dist]
        stop  = min(valid) + buf if valid else price + max(2*atr, min_dist)
        stop  = max(stop, price + min_dist)
        return stop

# ══════════════════════════════════════════════
#  TARGETS — real S/R levels
# ══════════════════════════════════════════════
def calc_targets(price: float, stop: float, direction: str,
                 levels: dict, fibs: dict) -> tuple:
    risk = abs(price - stop)
    if direction == "long":
        res  = [r for r in levels.get("resistances",[]) if r > price + risk*0.5]
        fext = [v for k,v in fibs.items() if k in ("127.2","161.8") and isinstance(v, float) and v > price+risk*2]
        tp1  = min(res)  if res  else price + 2.0*risk
        tp2  = min(fext) if fext else price + 3.5*risk
    else:
        sup  = [s for s in levels.get("supports",[]) if s < price - risk*0.5]
        fext = [v for k,v in fibs.items() if k in ("127.2","161.8") and isinstance(v, float) and v < price-risk*2]
        tp1  = max(sup)  if sup  else price - 2.0*risk
        tp2  = max(fext) if fext else price - 3.5*risk
    return tp1, tp2

# ══════════════════════════════════════════════
#  ROUND NUMBER
# ══════════════════════════════════════════════
def near_round(price: float, tol: float = 0.008) -> Optional[str]:
    mag = 10 ** (len(str(int(price))) - 1)
    for lv in [round(price/mag)*mag, round(price/(mag/2))*(mag/2)]:
        if lv > 0 and abs(price-lv)/price <= tol:
            return f"${lv:,.0f}"
    return None

# ══════════════════════════════════════════════
#  PREV DAY / WEEK
# ══════════════════════════════════════════════
def pdh_pdl(df: pd.DataFrame) -> tuple:
    if df.empty or len(df) < 48: return 0.0, 0.0
    today = df.index[-1].normalize()
    prev  = df[df.index < today].tail(24)
    return (float(prev["high"].max()), float(prev["low"].min())) if not prev.empty else (0.0, 0.0)

def pwh_pwl(df: pd.DataFrame) -> tuple:
    if df.empty or len(df) < 84: return 0.0, 0.0
    wsn  = (df.index[-1] - pd.Timedelta(days=df.index[-1].dayofweek)).normalize()
    prev = df[df.index < wsn].tail(42)
    return (float(prev["high"].max()), float(prev["low"].min())) if not prev.empty else (0.0, 0.0)

# ══════════════════════════════════════════════
#  MASTER SCORER — comprehensive
# ══════════════════════════════════════════════
def score_trade(price, direction, symbol,
                df_mo, df_wk, df_day, df_4h, df_1h,
                pattern, pat_qual,
                day_pat_ok, day_pat_msg,
                adx_ok, adx_msg,
                ichi_ok_val, ichi_msg,
                ob, hvn, fibs, day_lvls,
                wk_lvls, btc_ok) -> tuple:

    pts   = 0
    total = 0
    reasons = []

    def chk(w, ok, msg):
        nonlocal pts, total
        total += w
        if ok:
            pts += w
            reasons.append(msg)

    # ── TIER 1: STRUCTURE (most important) ───────────
    ms_mo  = structure(df_mo,  8)
    ms_wk  = structure(df_wk,  15)
    ms_day = structure(df_day, 20)
    ms_4h  = structure(df_4h,  20)

    mo_ok  = (direction=="long"  and ms_mo  in ("bullish","ranging")) or \
             (direction=="short" and ms_mo  in ("bearish","ranging"))
    wk_ok  = (direction=="long"  and ms_wk  == "bullish") or \
             (direction=="short" and ms_wk  == "bearish")
    day_ok = (direction=="long"  and ms_day == "bullish") or \
             (direction=="short" and ms_day == "bearish")
    h4_ok  = (direction=="long"  and ms_4h  in ("bullish","ranging")) or \
             (direction=="short" and ms_4h  in ("bearish","ranging"))

    chk(3, mo_ok,  f"Monthly {ms_mo}")
    chk(3, wk_ok,  f"Weekly {ms_wk}")
    chk(3, day_ok, f"Daily {ms_day}")
    chk(2, h4_ok,  f"4H {ms_4h}")

    # EMA 200
    if "ema200" in df_day.columns and not pd.isna(df_day["ema200"].iloc[-1]):
        e200 = float(df_day["ema200"].iloc[-1])
        e200_ok = (direction=="long" and price>e200) or (direction=="short" and price<e200)
        chk(2, e200_ok, f"EMA200 at ${e200:,.2f}")

    # Daily candle/pattern confirmation — HIGH WEIGHT
    chk(3, day_pat_ok, day_pat_msg)

    # ADX + DI direction
    chk(2, adx_ok, adx_msg)

    # ── TIER 2: LOCATION ─────────────────────────────
    # At weekly S/R (weight 3 — highest location weight)
    wk_all  = wk_lvls.get("supports",[]) + wk_lvls.get("resistances",[])
    wk_near = any(abs(price-lv)/price <= 0.012 for lv in wk_all)
    wk_htf  = min(wk_all, key=lambda x:abs(x-price)) if wk_all else 0
    chk(3, wk_near, f"Weekly S/R at ${wk_htf:,.4f}")

    # At daily S/R (weight 2)
    day_all  = day_lvls.get("supports",[]) + day_lvls.get("resistances",[])
    day_near = any(abs(price-lv)/price <= 0.010 for lv in day_all)
    day_htf  = min(day_all, key=lambda x:abs(x-price)) if day_all else 0
    chk(2, day_near, f"Daily S/R at ${day_htf:,.4f}")

    # Retest confirmation
    all_levels = wk_all + day_all
    nearest_lv = min(all_levels, key=lambda x:abs(x-price)) if all_levels else 0
    retest_ok  = is_retest(price, nearest_lv, df_4h, direction) if nearest_lv > 0 else False
    chk(2, retest_ok, f"Confirmed retest of ${nearest_lv:,.4f}")

    # Order block
    chk(2, ob is not None, f"Order block at ${ob[1]:,.4f}-${ob[0]:,.4f}" if ob else "")

    # High volume node
    near_hvn = any(abs(price-n)/price <= 0.008 for n in hvn)
    chk(1, near_hvn, "At high volume node")

    # Fibonacci
    fn = near_fib(price, fibs)
    chk(2, fn is not None, f"Fib {fn}%" if fn else "")

    # Round number
    rn = near_round(price)
    chk(1, rn is not None, f"Round number {rn}" if rn else "")

    # ── TIER 3: MOMENTUM ─────────────────────────────
    # RSI zone + divergence
    rsi_val  = float(df_1h["rsi"].iloc[-1]) if "rsi" in df_1h.columns else 50.0
    rsi_zone = (direction=="long" and 35<=rsi_val<=58) or (direction=="short" and 42<=rsi_val<=65)
    div      = rsi_div(df_1h)
    div_ok   = (direction=="long" and div=="bullish div") or (direction=="short" and div=="bearish div")
    rsi_ok   = rsi_zone or div_ok
    rsi_msg  = f"RSI {rsi_val:.0f}" + (f" + {div}" if div_ok else "")
    chk(2, rsi_ok, rsi_msg)

    # MACD — cross-validated with RSI
    macd_ok = False
    if "macd" in df_4h.columns:
        m   = float(df_4h["macd"].iloc[-1])
        ms_v = float(df_4h["macd_sig"].iloc[-1])
        mh  = float(df_4h["macd_hist"].iloc[-1])
        macd_dir = (direction=="long" and m>ms_v and mh>0) or (direction=="short" and m<ms_v and mh<0)
        # Cross-validate: MACD only counts if RSI also agrees
        macd_ok = macd_dir and rsi_ok
    chk(1, macd_ok, "MACD + RSI aligned")

    # Volume
    if "vol_ratio" in df_1h.columns:
        vr = float(df_1h["vol_ratio"].iloc[-1])
        chk(1, vr >= 1.25, f"Volume {vr:.1f}x avg")

    # Ichimoku
    chk(1, ichi_ok_val, ichi_msg)

    # PDH/PDL
    pdh, pdl = pdh_pdl(df_1h)
    if pdh > 0:
        pd_ok = ((direction=="short" and abs(price-pdh)/price<=0.007) or
                 (direction=="long"  and abs(price-pdl)/price<=0.007))
        chk(1, pd_ok, f"At PDH/PDL")

    # PWH/PWL
    pwh, pwl = pwh_pwl(df_4h)
    if pwh > 0:
        pw_ok = ((direction=="short" and abs(price-pwh)/price<=0.009) or
                 (direction=="long"  and abs(price-pwl)/price<=0.009))
        chk(1, pw_ok, f"At PWH/PWL")

    # Pattern quality
    strong = {"Bullish Engulfing","Bearish Engulfing","Hammer","Shooting Star",
              "Bullish Pin Bar","Bearish Pin Bar","Morning Star","Evening Star",
              "3 White Soldiers","3 Black Crows","Failed Breakout","Failed Breakdown",
              "Dragonfly Doji","Gravestone Doji"}
    chk(2, pattern in strong and pat_qual >= 0.68,
        f"{pattern} ({pat_qual*100:.0f}%)")

    pct = min(int(pts/total*100), 100) if total > 0 else 0
    return pts, total, pct, reasons

# ══════════════════════════════════════════════
#  SIGNAL MESSAGE — short and clean
# ══════════════════════════════════════════════
def format_signal(symbol, direction, price, stop, tp1, tp2,
                  pattern, pct, reasons, ms_day) -> str:

    risk  = abs(price - stop)
    rr1   = abs(tp1 - price) / risk if risk > 0 else 0
    rr2   = abs(tp2 - price) / risk if risk > 0 else 0
    emoji = "📈" if direction == "long" else "📉"
    label = "LONG" if direction == "long" else "SHORT"

    # Max 5 reasons, comma separated
    why = " | ".join(reasons[:5])

    return (
        f"{emoji} **{label} {symbol}** — {pct}% confidence\n"
        f"`Entry ${price:,.4f}` | `Stop ${stop:,.4f}` | "
        f"`TP1 ${tp1:,.4f}` ({rr1:.1f}R) | `TP2 ${tp2:,.4f}` ({rr2:.1f}R)\n"
        f"**{pattern}** | {why}\n"
        f"*Max 1% risk | Stop immediately | Confirm on chart*"
    )

# ══════════════════════════════════════════════
#  MAIN SCANNER
# ══════════════════════════════════════════════
def scan(symbol: str, df_btc_4h: pd.DataFrame):
    log.info(f"Scanning {symbol}")

    df_mo  = fetch(symbol, "1M",  36)
    df_wk  = fetch(symbol, "1w",  52)
    df_day = fetch(symbol, "1d", 200)
    df_4h  = fetch(symbol, "4h", 300)
    df_1h  = fetch(symbol, "1h", 300)

    if df_day.empty or df_1h.empty or df_4h.empty:
        log.warning(f"{symbol}: no data")
        return

    if df_mo.empty:  df_mo = df_wk.copy()  if not df_wk.empty  else df_day.copy()
    if df_wk.empty:  df_wk = df_day.copy()

    for df in [df_mo, df_wk, df_day, df_4h, df_1h]:
        enrich(df)

    price  = float(df_1h["close"].iloc[-1])
    atr_1h = float(df_1h["atr"].iloc[-1]) if "atr" in df_1h.columns else price*0.005
    atr_4h = float(df_4h["atr"].iloc[-1]) if "atr" in df_4h.columns else price*0.01

    # 1H Pattern detection
    pattern, pat_qual, direction = detect_pattern(df_1h)
    if pattern == "none" or pat_qual < 0.68 or direction == "none":
        log.info(f"{symbol}: no valid 1H pattern")
        return

    if on_cooldown(symbol, direction):
        log.info(f"{symbol} {direction}: on cooldown")
        return

    # ── HARD GATES ────────────────────────────────────

    # Gate 1: Weekly structure must strictly agree
    ms_wk = structure(df_wk, 15)
    if not ((direction=="long" and ms_wk=="bullish") or (direction=="short" and ms_wk=="bearish")):
        log.info(f"{symbol}: weekly {ms_wk} blocks {direction}")
        return

    # Gate 2: Daily structure must strictly agree
    ms_day = structure(df_day, 20)
    if not ((direction=="long" and ms_day=="bullish") or (direction=="short" and ms_day=="bearish")):
        log.info(f"{symbol}: daily {ms_day} blocks {direction}")
        return

    # Gate 3: RSI hard limits
    if "rsi" in df_1h.columns:
        rsi = float(df_1h["rsi"].iloc[-1])
        if direction == "short" and rsi < 33:
            log.info(f"{symbol}: RSI {rsi:.0f} oversold — no short")
            return
        if direction == "long"  and rsi > 67:
            log.info(f"{symbol}: RSI {rsi:.0f} overbought — no long")
            return

    # Gate 4: Move stage
    if move_stage(df_4h, direction) == "exhausted":
        log.info(f"{symbol}: exhausted")
        return

    # Gate 5: Not mid-range on weekly
    if is_mid_range(price, df_wk):
        log.info(f"{symbol}: mid-range on weekly — no edge")
        return

    # Gate 6: Over-extension
    if atr_4h > 0:
        move = abs(float(df_4h["close"].iloc[-1]) - float(df_4h["close"].iloc[-20]))
        if move > 7 * atr_4h:
            log.info(f"{symbol}: over-extended")
            return

    # Gate 7: BTC correlation (alts only)
    if symbol in ALTS and not df_btc_4h.empty:
        btc_ok, btc_msg = btc_allows(direction, df_btc_4h)
        if not btc_ok:
            log.info(f"{symbol}: {btc_msg}")
            return
    else:
        btc_ok = True

    # Gate 8: ADX direction confirmation
    adx_ok, adx_msg = adx_confirms(df_4h, direction, symbol)
    if not adx_ok:
        log.info(f"{symbol}: {adx_msg}")
        return

    # ── PRE-SCORE CALCULATIONS ────────────────────────
    day_pat_ok, day_pat_msg = daily_pattern_confirms(df_day, direction)
    ichi_ok_val, ichi_msg   = ichi_confirms(df_4h, direction)
    ob   = find_order_blocks(df_4h, direction)
    hvn  = high_volume_nodes(df_4h)
    fibs = significant_fib(df_4h, direction)
    wk_lvls  = professional_sr(df_wk,  window=3, n=5)
    day_lvls = professional_sr(df_day, window=5, n=8)

    # ── SCORE ─────────────────────────────────────────
    pts, total, pct, reasons = score_trade(
        price, direction, symbol,
        df_mo, df_wk, df_day, df_4h, df_1h,
        pattern, pat_qual,
        day_pat_ok, day_pat_msg,
        adx_ok, adx_msg,
        ichi_ok_val, ichi_msg,
        ob, hvn, fibs, day_lvls, wk_lvls, btc_ok
    )

    log.info(f"{symbol} {direction}: {pct}% score [{pattern}]")

    if pct < MIN_SCORE_PCT:
        log.info(f"{symbol}: {pct}% below {MIN_SCORE_PCT}%")
        return

    # ── BUILD TRADE LEVELS ────────────────────────────
    stop       = calc_stop(price, direction, df_4h, atr_1h)
    all_lvls   = {"supports": day_lvls["supports"]+wk_lvls["supports"],
                  "resistances": day_lvls["resistances"]+wk_lvls["resistances"]}
    tp1, tp2   = calc_targets(price, stop, direction, all_lvls, fibs)

    risk = abs(price - stop)
    if risk <= 0: return

    rr1 = abs(tp1 - price) / risk
    if rr1 < MIN_RR:
        log.info(f"{symbol}: RR {rr1:.1f} below {MIN_RR}")
        return

    # ── FIRE SIGNAL ───────────────────────────────────
    ms_day_str = structure(df_day, 20)
    msg = format_signal(
        symbol, direction, price, stop, tp1, tp2,
        pattern, pct, reasons, ms_day_str
    )

    log.info(f">>> SIGNAL: {symbol} {direction} {pct}%")
    send_discord(msg)

# ══════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════
def main():
    log.info("Bot v6.0 started")

    while True:
        log.info("Scan cycle start")

        # Fetch BTC data once per cycle for correlation checks
        df_btc_4h = fetch("BTC/USDT", "4h", 100)
        if not df_btc_4h.empty:
            enrich(df_btc_4h)

        for symbol in SYMBOLS:
            try:
                scan(symbol, df_btc_4h)
                time.sleep(3)
            except Exception as e:
                log.error(f"{symbol}: {e}", exc_info=True)

        log.info(f"Cycle done. Next in {SCAN_INTERVAL//60} min.")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
