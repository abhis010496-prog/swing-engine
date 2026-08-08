"""
position_engine.py — analysis for a stock you already own.

No Streamlit here on purpose, so this can be run and tested on its own.
holdings.py provides the interface.

Everything is computed from the price table directly. This file does not
import rank.py or modes.py, so it keeps working if those change.
"""

import sqlite3
import datetime as dt
import pandas as pd
import numpy as np


# ---------------------------------------------------------------- settings

HOLD_MONTHS = 2.0          # the horizon the whole system is built around
TARGET_PCT = 13.0          # "Best overall" target
STOP_ATR_MULT = 2.0        # "Best overall" stop
BENCHMARK_CANDIDATES = ["^NSEI", "NIFTY", "NIFTY50", "^CRSLDX", "NIFTY_50",
                        "^NSEBANK", "NSEI"]


# ------------------------------------------------------- schema discovery
# market.db was built up over months, so column names are not assumed.

def _tables(conn):
    q = "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    return [r[0] for r in conn.execute(q).fetchall()]


def _columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info('{table}')").fetchall()]


def _match(columns, *wanted):
    """Find a column by any of several likely names, ignoring case."""
    lower = {c.lower(): c for c in columns}
    for w in wanted:
        if w.lower() in lower:
            return lower[w.lower()]
    return None


def find_price_table(conn):
    """Locate the daily price table and map its columns."""
    best = None
    for t in _tables(conn):
        cols = _columns(conn, t)
        m = {
            "table":  t,
            "symbol": _match(cols, "symbol", "ticker", "stock", "tradingsymbol"),
            "date":   _match(cols, "date", "dt", "trade_date", "timestamp"),
            "close":  _match(cols, "close", "close_price", "adj_close", "last"),
            "high":   _match(cols, "high", "high_price"),
            "low":    _match(cols, "low", "low_price"),
            "open":   _match(cols, "open", "open_price"),
            "volume": _match(cols, "volume", "vol", "qty", "traded_qty"),
        }
        if m["symbol"] and m["date"] and m["close"]:
            # prefer the table that looks most like a full OHLCV history
            score = sum(1 for k in ("high", "low", "open", "volume") if m[k])
            if best is None or score > best[0]:
                best = (score, m)
    if best is None:
        raise LookupError(
            "No price table found in this database. Expected a table with "
            "symbol, date and close columns."
        )
    return best[1]


def list_symbols(conn, pmap):
    q = f"SELECT DISTINCT {pmap['symbol']} FROM {pmap['table']} ORDER BY 1"
    return [r[0] for r in conn.execute(q).fetchall()]


def resolve_symbol(conn, pmap, typed):
    """Turn what the user typed into a symbol that exists in the database."""
    typed = (typed or "").strip().upper()
    if not typed:
        return None, []
    col, tbl = pmap["symbol"], pmap["table"]

    exact = conn.execute(
        f"SELECT DISTINCT {col} FROM {tbl} WHERE UPPER({col}) = ?", (typed,)
    ).fetchall()
    if exact:
        return exact[0][0], []

    # Yahoo tickers carry a .NS suffix
    for variant in (typed + ".NS", typed.replace(".NS", "")):
        got = conn.execute(
            f"SELECT DISTINCT {col} FROM {tbl} WHERE UPPER({col}) = ?", (variant,)
        ).fetchall()
        if got:
            return got[0][0], []

    near = conn.execute(
        f"SELECT DISTINCT {col} FROM {tbl} WHERE UPPER({col}) LIKE ? "
        f"ORDER BY LENGTH({col}) LIMIT 8", (typed + "%",)
    ).fetchall()
    return None, [r[0] for r in near]


# -------------------------------------------------------------- price data

def load_prices(conn, pmap, symbol, days=420):
    cols = [pmap["date"], pmap["close"]]
    for k in ("high", "low", "open", "volume"):
        if pmap[k]:
            cols.append(pmap[k])
    sql = (f"SELECT {', '.join(cols)} FROM {pmap['table']} "
           f"WHERE {pmap['symbol']} = ? ORDER BY {pmap['date']} DESC LIMIT ?")
    df = pd.read_sql_query(sql, conn, params=(symbol, days))
    if df.empty:
        return df

    rename = {pmap["date"]: "date", pmap["close"]: "close"}
    for k in ("high", "low", "open", "volume"):
        if pmap[k]:
            rename[pmap[k]] = k
    df = df.rename(columns=rename)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    # if the file lacks high/low, stand in with close so ATR still works
    if "high" not in df:
        df["high"] = df["close"]
    if "low" not in df:
        df["low"] = df["close"]
    if "volume" not in df:
        df["volume"] = np.nan
    return df


def compute_metrics(df):
    """Everything the gates and the verdict need, from prices alone."""
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    v = df["volume"].astype(float)

    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()

    m = {
        "last_date":  df["date"].iloc[-1],
        "close":      float(c.iloc[-1]),
        "bars":       len(df),
        "dma50":      float(c.rolling(50).mean().iloc[-1]) if len(c) >= 50 else np.nan,
        "dma200":     float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else np.nan,
        "high_52w":   float(c.tail(252).max()),
        "atr_rs":     float(atr14.iloc[-1]) if len(c) >= 15 else np.nan,
    }
    m["atr_pct"] = (m["atr_rs"] / m["close"] * 100) if m["close"] else np.nan
    m["from_high_pct"] = (m["close"] / m["high_52w"] - 1) * 100 if m["high_52w"] else np.nan
    m["ext_50dma_pct"] = ((m["close"] / m["dma50"] - 1) * 100
                          if m["dma50"] and not np.isnan(m["dma50"]) else np.nan)

    # turnover in rupees crore, 20-day average
    turn = (c * v).rolling(20).mean().iloc[-1]
    m["turnover_cr"] = float(turn) / 1e7 if pd.notna(turn) else np.nan

    for label, back in (("ret_3m_pct", 63), ("ret_6m_pct", 126), ("ret_1m_pct", 21)):
        m[label] = ((c.iloc[-1] / c.iloc[-1 - back] - 1) * 100
                    if len(c) > back else np.nan)

    m["stale_days"] = (dt.datetime.now() - m["last_date"].to_pydatetime()).days
    return m


def benchmark_return(conn, pmap, days=63):
    """3-month Nifty return, for the relative-strength gate."""
    for cand in BENCHMARK_CANDIDATES:
        sym, _ = resolve_symbol(conn, pmap, cand)
        if not sym:
            continue
        df = load_prices(conn, pmap, sym, days=days + 20)
        if len(df) > days:
            c = df["close"].astype(float)
            return float((c.iloc[-1] / c.iloc[-1 - days] - 1) * 100), sym
    return None, None


# ------------------------------------------------------------------ gates
# Section 2.2 of the documentation, recomputed here.

def check_gates(m, bench_3m):
    g = []

    def add(name, passed, detail):
        if passed is not None:
            passed = bool(passed)
        g.append({"name": name, "pass": passed, "detail": detail})

    if np.isnan(m["dma50"]) or np.isnan(m["dma200"]):
        add("Uptrend", None, "Not enough history for a 200-day average")
    else:
        add("Uptrend", m["close"] > m["dma50"] > m["dma200"],
            f"Close ₹{m['close']:.0f} · 50DMA ₹{m['dma50']:.0f} · 200DMA ₹{m['dma200']:.0f}")

    if bench_3m is None or np.isnan(m["ret_3m_pct"]):
        add("Beating the market", None, "No index data to compare against")
    else:
        excess = m["ret_3m_pct"] - bench_3m
        add("Beating the market", excess > 5,
            f"3-month {m['ret_3m_pct']:+.1f}% vs index {bench_3m:+.1f}% "
            f"({excess:+.1f} points)")

    add("Near its 52-week high", m["from_high_pct"] > -10,
        f"{m['from_high_pct']:+.1f}% from the high of ₹{m['high_52w']:.0f}")

    if np.isnan(m["turnover_cr"]):
        add("Liquidity", None, "No volume data")
    else:
        add("Liquidity", m["turnover_cr"] > 15,
            f"₹{m['turnover_cr']:.0f}cr traded per day")

    if np.isnan(m["atr_pct"]):
        add("Volatility in range", None, "Not enough history")
    else:
        add("Volatility in range", 1.8 <= m["atr_pct"] <= 7.0,
            f"Moves {m['atr_pct']:.1f}% a day (needs 1.8–7%)")

    if np.isnan(m["ext_50dma_pct"]):
        add("Not overextended", None, "No 50-day average yet")
    else:
        add("Not overextended", m["ext_50dma_pct"] < 20,
            f"{m['ext_50dma_pct']:+.1f}% above its 50-day average")

    return g


# ---------------------------------------------------------------- vetoes
# Section 2.1. Only runs if a fundamentals table exists.

def check_vetoes(conn, symbol, m):
    out = []
    fund_table = None
    for t in _tables(conn):
        if "fundamental" in t.lower():
            fund_table = t
            break

    if fund_table:
        cols = _columns(conn, fund_table)
        symcol = _match(cols, "symbol", "ticker", "stock")
        if symcol:
            row = conn.execute(
                f"SELECT * FROM {fund_table} WHERE UPPER({symcol}) = ? LIMIT 1",
                (symbol.upper(),)
            ).fetchone()
            if row:
                d = dict(zip(cols, row))

                def num(*names):
                    col = _match(cols, *names)
                    if col is None:
                        return None
                    try:
                        return float(d[col])
                    except (TypeError, ValueError):
                        return None

                margin = num("profit_margin", "profit_margins", "net_margin", "margin")
                if margin is not None:
                    val = margin * 100 if abs(margin) <= 1 else margin
                    if val < 0:
                        out.append(f"Loss-making — net margin {val:.1f}%")

                de = num("debt_to_equity", "debt_equity", "de", "debtToEquity")
                if de is not None:
                    val = de / 100 if de > 25 else de   # some feeds report percent
                    if val > 2:
                        out.append(f"Heavy debt — debt is {val:.1f}× equity")

                rev = num("revenue_growth", "rev_growth", "revenueGrowth", "sales_growth")
                if rev is not None:
                    val = rev * 100 if abs(rev) <= 1 else rev
                    if val < -15:
                        out.append(f"Sales falling — revenue {val:.1f}%")

    if not np.isnan(m["turnover_cr"]) and m["turnover_cr"] < 8:
        out.append(f"Too thinly traded — only ₹{m['turnover_cr']:.1f}cr a day")

    return out, (fund_table is not None)


# ------------------------------------------------------------ persistence

def persistence(conn, symbol):
    """How many of the last 7 and 30 days this stock passed the screen."""
    hist = None
    for t in _tables(conn):
        if "shortlist" in t.lower() or "qualif" in t.lower():
            hist = t
            break
    if hist is None:
        return None

    cols = _columns(conn, hist)
    symcol = _match(cols, "symbol", "ticker", "stock")
    datecol = _match(cols, "date", "dt", "trade_date", "as_of")
    if not (symcol and datecol):
        return None

    def count(window):
        q = (f"SELECT COUNT(DISTINCT {datecol}) FROM {hist} "
             f"WHERE UPPER({symcol}) = ? AND {datecol} >= date('now', ?)")
        try:
            return conn.execute(q, (symbol.upper(), f"-{window} days")).fetchone()[0]
        except sqlite3.Error:
            return None

    def total(window):
        q = (f"SELECT COUNT(DISTINCT {datecol}) FROM {hist} "
             f"WHERE {datecol} >= date('now', ?)")
        try:
            return conn.execute(q, (f"-{window} days",)).fetchone()[0]
        except sqlite3.Error:
            return None

    return {"d7": count(7), "d7_of": total(7),
            "d30": count(30), "d30_of": total(30)}


# ----------------------------------------------------------------- verdict

def build_verdict(m, gates, vetoes, persist, entry_price, qty, entry_date):
    """
    Hold, trim or exit — based on whether the reasons to own it still hold.
    Deliberately not a prediction.
    """
    pos = {}
    if entry_price and entry_price > 0:
        pos["entry"] = entry_price
        pos["value_now"] = m["close"] * (qty or 0)
        pos["cost"] = entry_price * (qty or 0)
        pos["pnl_rs"] = pos["value_now"] - pos["cost"]
        pos["pnl_pct"] = (m["close"] / entry_price - 1) * 100
        pos["target"] = entry_price * (1 + TARGET_PCT / 100)
        pos["progress"] = (pos["pnl_pct"] / TARGET_PCT * 100)
        if not np.isnan(m["atr_rs"]):
            pos["stop"] = entry_price - STOP_ATR_MULT * m["atr_rs"]
            pos["stop_dist_pct"] = (m["close"] / pos["stop"] - 1) * 100
    if entry_date:
        held = (dt.date.today() - entry_date).days
        pos["days_held"] = held
        pos["months_held"] = held / 30.4
        pos["window_left_days"] = int(HOLD_MONTHS * 30.4) - held

    exit_reasons, watch_reasons, keep_reasons = [], [], []

    if vetoes:
        exit_reasons += [f"{v} — this would now be filtered out entirely"
                         for v in vetoes]

    if not np.isnan(m["dma200"]) and m["close"] < m["dma200"]:
        exit_reasons.append("Price has fallen below its 200-day average — "
                            "the long-term trend has broken")

    if "stop" in pos and m["close"] <= pos["stop"]:
        exit_reasons.append(
            f"Price ₹{m['close']:.0f} is at or below your stop of ₹{pos['stop']:.0f}")

    if not np.isnan(m["dma50"]) and m["close"] < m["dma50"]:
        watch_reasons.append("Price has slipped below its 50-day average")
    if (not np.isnan(m["dma50"]) and not np.isnan(m["dma200"])
            and m["dma50"] < m["dma200"]):
        watch_reasons.append("The 50-day average has crossed below the 200-day")
    if m["from_high_pct"] < -15:
        watch_reasons.append(
            f"{abs(m['from_high_pct']):.0f}% below its 52-week high — momentum has faded")
    if persist and persist.get("d30") is not None and persist.get("d30_of"):
        if persist["d30"] == 0:
            watch_reasons.append("Has not passed the screen once in the last 30 days")
        elif persist["d30"] < persist["d30_of"] * 0.3:
            watch_reasons.append(
                f"Passed the screen only {persist['d30']} of the last "
                f"{persist['d30_of']} days")

    if pos.get("pnl_pct", 0) >= TARGET_PCT:
        watch_reasons.append(
            f"Target of {TARGET_PCT:.0f}% reached — the original plan is complete")
    if pos.get("window_left_days") is not None and pos["window_left_days"] < 0:
        watch_reasons.append(
            f"Held {pos['months_held']:.1f} months, past the {HOLD_MONTHS:.0f}-month plan")

    failed = [g["name"] for g in gates if g["pass"] is False]
    passed = [g["name"] for g in gates if g["pass"] is True]
    if not failed:
        keep_reasons.append("Still passes every check the screen applies")
    elif len(failed) <= 2:
        keep_reasons.append(f"Still passes {len(passed)} of "
                            f"{len(passed) + len(failed)} checks")

    if exit_reasons:
        call, tone = "Exit", "bad"
    elif len(watch_reasons) >= 2:
        call, tone = "Trim or review", "warn"
    elif watch_reasons:
        call, tone = "Watch closely", "warn"
    else:
        call, tone = "Hold", "good"

    return {
        "call": call, "tone": tone, "position": pos,
        "exit_reasons": exit_reasons,
        "watch_reasons": watch_reasons,
        "keep_reasons": keep_reasons,
        "failed_gates": failed,
    }


# ------------------------------------------------------------------ facade

def analyse(db_path, typed_symbol, entry_price=None, qty=None, entry_date=None):
    conn = sqlite3.connect(db_path)
    try:
        pmap = find_price_table(conn)
        symbol, suggestions = resolve_symbol(conn, pmap, typed_symbol)
        if symbol is None:
            return {"found": False, "suggestions": suggestions}

        df = load_prices(conn, pmap, symbol)
        if df.empty or len(df) < 30:
            return {"found": False, "suggestions": [],
                    "error": f"Only {len(df)} days of price history for {symbol}."}

        m = compute_metrics(df)
        bench_3m, bench_sym = benchmark_return(conn, pmap)
        gates = check_gates(m, bench_3m)
        vetoes, has_fund = check_vetoes(conn, symbol, m)
        persist = persistence(conn, symbol)
        verdict = build_verdict(m, gates, vetoes, persist,
                                entry_price, qty, entry_date)

        return {"found": True, "symbol": symbol, "prices": df, "metrics": m,
                "gates": gates, "vetoes": vetoes, "has_fundamentals": has_fund,
                "persistence": persist, "verdict": verdict,
                "benchmark": bench_sym, "benchmark_3m": bench_3m}
    finally:
        conn.close()