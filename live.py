"""
LIVE PRICES
===========
Current (roughly 15-minute delayed) prices from Yahoo.

Method chosen from measurement, not assumption. On a real machine:

    period=1d, interval=1d   5/5 stocks in 0.9s   <- used here
    period=2d, interval=5m   5/5 stocks in 3.0s   <- fallback
    fast_info per stock      5/5 stocks in 2.5s   <- 23s for 60, too slow

Daily bars during market hours return today's PARTIAL candle, whose close
is the latest traded price. That is exactly what we want, and it is the
lightest request of the three, so it holds up when asking for 100+ stocks.

Display only. Every score and signal still uses settled closing prices.
"""

import streamlit as st

CHUNK = 50


def _extract(data, tickers):
    """Pull the last close for each ticker out of a yfinance frame."""
    import pandas as pd

    out = {}
    if data is None or len(data) == 0:
        return out

    multi = isinstance(data.columns, pd.MultiIndex)
    for t in tickers:
        try:
            if multi:
                if t not in data.columns.get_level_values(1):
                    continue
                close = data["Close"][t].dropna()
                opens = data["Open"][t].dropna() if "Open" in data else None
            else:
                close = data["Close"].dropna()
                opens = data["Open"].dropna() if "Open" in data else None
            if close.empty:
                continue
            out[t] = {
                "price": float(close.iloc[-1]),
                "open": float(opens.iloc[-1]) if opens is not None
                        and not opens.empty else None,
                "time": close.index[-1].strftime("%d %b, %H:%M"),
            }
        except Exception:
            continue
    return out


@st.cache_data(ttl=60, show_spinner=False)
def get_live(tickers):
    """
    Current prices for a list of tickers.
    Returns {ticker: {"price": float, "open": float|None, "time": str}}.
    Never raises — a missing quote must never break the page.
    """
    tickers = [t for t in dict.fromkeys(tickers) if t]
    if not tickers:
        return {}

    try:
        import yfinance as yf
    except ImportError:
        return {}

    out = {}

    # ---- primary: today's daily bar, in chunks ----
    for i in range(0, len(tickers), CHUNK):
        batch = tickers[i:i + CHUNK]
        try:
            d = yf.download(batch, period="1d", interval="1d",
                            progress=False, threads=False, auto_adjust=False)
            out.update(_extract(d, batch))
        except Exception:
            pass

    # ---- fallback for anything still missing: 5-minute bars ----
    missing = [t for t in tickers if t not in out]
    if missing:
        for i in range(0, len(missing), CHUNK):
            batch = missing[i:i + CHUNK]
            try:
                d = yf.download(batch, period="2d", interval="5m",
                                progress=False, threads=False,
                                auto_adjust=False)
                out.update(_extract(d, batch))
            except Exception:
                pass

    # ---- last resort, only for a handful ----
    still = [t for t in tickers if t not in out]
    if still and len(still) <= 10:
        try:
            import yfinance as yf
            for t in still:
                try:
                    p = yf.Ticker(t).fast_info.last_price
                    if p:
                        out[t] = {"price": float(p), "open": None,
                                  "time": "now"}
                except Exception:
                    continue
        except Exception:
            pass

    return out


def price_line(entry, close_price, label="last close"):
    """Small HTML block comparing the live price to the stored close."""
    if not entry:
        return (f"<div style='font-size:12px;color:#6B7C90'>"
                f"Live price unavailable — showing {label} "
                f"₹{close_price:,.1f}</div>")
    live = entry["price"]
    diff = (live / close_price - 1) * 100 if close_price else 0
    cls = "up" if diff >= 0 else "down"
    return (f"<div style='font-size:12px;color:#6B7C90;margin-top:2px'>"
            f"Live ₹{live:,.1f} <span class='{cls}'>({diff:+.2f}% vs "
            f"{label})</span> · {entry['time']} · delayed ~15 min</div>")