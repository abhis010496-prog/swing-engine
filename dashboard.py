"""
MY STOCK SHORTLIST
==================
Run with:   python -m streamlit run dashboard.py

Needs rank.py to have been run at least once.

The interface is rendered as a single custom component rather than with
Streamlit's stock widgets, so it behaves and looks like a real application:
instant switching, no page reloads, proper charts.
"""

import json
import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import modes

import os as _os
DB = "market.db" if _os.path.exists("market.db") else "deploy.db"
BENCHMARK = "^NSEI"
PER_CATEGORY = 5
SERIES_DAYS = 130

st.set_page_config(page_title="My Stock Shortlist", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@500;600&display=swap');
  header[data-testid="stHeader"] { height:0; background:transparent; }
  .block-container { padding:0.5rem 1.2rem 0 !important; max-width:100% !important; }
  .stApp { background:#FAFBFD; }
  section[data-testid="stSidebar"] { display:none; }

  div[data-testid="stSelectbox"]:first-of-type { max-width:280px; margin-bottom:10px; }
  div[data-baseweb="select"] > div { background:#fff; border-color:#D6DEE7;
      border-radius:10px; font-size:15px; font-weight:600; min-height:46px; }

  /* ---------- mobile only: nothing above this line is modified ---------- */
  @media (max-width: 900px) {
    .block-container { padding:0.5rem 0.6rem 0 !important; }
    div[data-testid="stSelectbox"]:first-of-type { max-width:100% !important; }

    /* Streamlit columns: wrap instead of squeezing into slivers */
    div[data-testid="stHorizontalBlock"] {
        flex-wrap:wrap !important; gap:0.55rem !important; }
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
        flex:1 1 100% !important; width:100% !important; min-width:0 !important; }

    /* ...except metric strips, which read better two-up */
    div[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"])
        > div[data-testid="column"] {
        flex:1 1 calc(50% - 0.55rem) !important; width:auto !important; }
    div[data-testid="stMetricValue"] { font-size:1.3rem !important; }
    div[data-testid="stMetricLabel"] p { font-size:0.78rem !important; }

    /* page banners defined further down the file */
    .whhero, .phero, .whero, .mhero {
        padding:18px 16px !important; border-radius:14px !important; }
    .whhero h2, .phero h2, .whero h2, .mhero h2 { font-size:20px !important; }
    .whhero p, .phero p, .whero p, .mhero p { font-size:12.5px !important; }
    .wcard, .mcard { padding:14px 15px !important; }
    .wname { font-size:17px !important; }
    .wbig, .freq { font-size:19px !important; }
    .elevels { gap:14px !important; }
    .vbox, .mout, .entry, .wsince, .wgone {
        padding:12px 14px !important; font-size:13px !important; }
  }
</style>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------
# Data
# ------------------------------------------------------------------

@st.cache_data(ttl=300)
def read_scores():
    conn = sqlite3.connect(DB)
    try:
        df = pd.read_sql("SELECT * FROM scores", conn)
        as_of = conn.execute("SELECT MAX(date) FROM prices_daily").fetchone()[0]
    except Exception:
        conn.close()
        return None, None
    conn.close()
    return df, as_of


@st.cache_data(ttl=60, show_spinner=False)
def live_map(tickers):
    """
    Current (roughly 15-min delayed) prices for a list of tickers.

    Display only. Every score, ranking and signal is still computed from
    settled closing prices — a stock that qualifies at 11am and fails by
    3pm would have you making and unmaking decisions all day.
    """
    if not tickers:
        return {}
    try:
        import live
        return live.get_live(tuple(sorted(set(tickers)))) or {}
    except Exception:
        return {}


def apply_live(rows_list, quotes):
    """Overlay live prices onto scored rows, recomputing today's move."""
    stamp = None
    for r in rows_list:
        q = quotes.get(r["ticker"])
        if not q:
            continue
        prev_close = r["price"]
        r["close_price"] = prev_close
        r["price"] = q["price"]
        if prev_close:
            r["day"] = (q["price"] / prev_close - 1) * 100
        r["is_live"] = True
        stamp = q.get("time", stamp)
    return stamp


def _wl_conn():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS watchlist (
        ticker   TEXT PRIMARY KEY,
        added_on TEXT,
        price_at REAL,
        note     TEXT)""")
    conn.commit()
    return conn


def watchlist_tickers():
    conn = _wl_conn()
    rows = conn.execute(
        "SELECT ticker, added_on, price_at, note FROM watchlist "
        "ORDER BY added_on DESC").fetchall()
    conn.close()
    return rows


def watchlist_add(ticker, price, note=""):
    conn = _wl_conn()
    conn.execute("INSERT OR IGNORE INTO watchlist VALUES (?,?,?,?)",
                 (ticker, datetime.now().strftime("%Y-%m-%d"), price, note))
    conn.commit()
    conn.close()


def watchlist_remove(ticker):
    conn = _wl_conn()
    conn.execute("DELETE FROM watchlist WHERE ticker=?", (ticker,))
    conn.commit()
    conn.close()


@st.cache_data(ttl=300)
def read_history_dates():
    conn = sqlite3.connect(DB)
    try:
        d = [r[0] for r in conn.execute(
            "SELECT DISTINCT as_of FROM shortlist_history ORDER BY as_of DESC")]
    except Exception:
        d = []
    conn.close()
    return d


@st.cache_data(ttl=300)
def read_picks_on(day, use_live=True):
    conn = sqlite3.connect(DB)
    try:
        df = pd.read_sql("""
            SELECT h.ticker, h.score, i.name, i.sector, i.cap_bucket,
                   p.close AS price_then
            FROM shortlist_history h
            LEFT JOIN instruments i ON i.ticker = h.ticker
            LEFT JOIN prices_daily p ON p.ticker = h.ticker AND p.date = h.as_of
            WHERE h.as_of = ? ORDER BY h.score DESC""",
            conn, params=(day,))
        latest = pd.read_sql("""
            SELECT ticker, close AS price_now FROM prices_daily
            WHERE date = (SELECT MAX(date) FROM prices_daily)""", conn)
        df = df.merge(latest, on="ticker", how="left")
        df["since_pct"] = (df["price_now"] / df["price_then"] - 1) * 100
        if use_live:
            q = {}
            try:
                import live as _lv
                q = _lv.get_live(tuple(df["ticker"].tolist()[:80])) or {}
            except Exception:
                q = {}
            if q:
                df["price_now"] = df.apply(
                    lambda r: q[r["ticker"]]["price"] if r["ticker"] in q
                    else r["price_now"], axis=1)
                df["since_pct"] = (df["price_now"] / df["price_then"] - 1) * 100
    except Exception:
        df = None
    conn.close()
    return df


@st.cache_data(ttl=300)
def read_tracker(window):
    conn = sqlite3.connect(DB)
    try:
        perf = pd.read_sql("SELECT * FROM pick_performance WHERE window_days=? "
                           "ORDER BY ret_pct DESC", conn, params=(window,))
        summ = pd.read_sql("SELECT * FROM tracker_summary WHERE window_days=?",
                           conn, params=(window,))
    except Exception:
        conn.close()
        return None, None
    conn.close()
    return perf, (summ.iloc[0].to_dict() if not summ.empty else None)


@st.cache_data(ttl=300)
def read_monthly(period="monthly"):
    conn = sqlite3.connect(DB)
    try:
        df = pd.read_sql("SELECT * FROM monthly_shortlist WHERE period=? "
                         "ORDER BY consistency DESC, ret_1m DESC",
                         conn, params=(period,))
        if df.empty:      # older table without a period column
            df = pd.read_sql("SELECT * FROM monthly_shortlist "
                             "ORDER BY consistency DESC", conn)
    except Exception:
        df = None
    conn.close()
    return df


@st.cache_data(ttl=300)
def series_for(tickers):
    if not tickers:
        return {}
    conn = sqlite3.connect(DB)
    marks = ",".join("?" * len(tickers))
    df = pd.read_sql(
        f"SELECT ticker, date, close FROM prices_daily "
        f"WHERE ticker IN ({marks}) ORDER BY ticker, date",
        conn, params=list(tickers), parse_dates=["date"])
    conn.close()
    out = {}
    for t, g in df.groupby("ticker"):
        g = g.tail(SERIES_DAYS)
        out[t] = {
            "c": [round(float(v), 2) for v in g["close"]],
            "d": [d.strftime("%d %b") for d in g["date"]],
        }
    return out


@st.cache_data(ttl=300)
def news_for(tickers):
    if not tickers:
        return {}
    conn = sqlite3.connect(DB)
    out = {}
    try:
        cut = (datetime.now() - timedelta(days=21)).strftime("%Y-%m-%d")
        for t in tickers:
            out[t] = [
                {"h": h, "s": s, "p": p, "u": u or ""}
                for p, h, s, u in conn.execute(
                    "SELECT published, headline, source, url FROM news "
                    "WHERE ticker=? AND published>=? ORDER BY published DESC "
                    "LIMIT 3", (t, cut))]
    except Exception:
        pass
    conn.close()
    return out


scores_df, as_of = read_scores()
if scores_df is None or scores_df.empty:
    st.error("No scores yet. Run this in your terminal:\n\n    python rank.py")
    st.stop()

rows = [{
    "ticker": r["ticker"], "short": r["ticker"].replace(".NS", ""),
    "name": r["name"], "sector": r["sector"] or "—", "cap": r["cap_bucket"],
    "mcap": r["mcap"], "price": r["price"], "day": r["day_chg"],
    "ma50": r["ma50"], "atr": r["atr"], "atr_pct": r["atr_pct"],
    "from_high": r["from_high"], "rs": r["rs"], "turnover": r["turnover"],
    "trend": bool(r["trend"]), "score": int(r["score"]),
    "days": int(r["days_on_list"]) if pd.notna(r.get("days_on_list")) else 0,
    "isnew": bool(r["is_new"]) if pd.notna(r.get("is_new")) else False,
    "good": json.loads(r["good_json"]), "bad": json.loads(r["bad_json"]),
} for r in scores_df.to_dict("records")]
rows.sort(key=lambda x: -x["score"])

# ---- section switch: a plain selectbox, no state juggling, no reruns ----
nav_a, nav_b, nav_c = st.columns([1.3, 1.7, 1.5])
view = nav_a.selectbox("Section", ["Find stocks", "Practice trading"], index=0)
listing = nav_b.selectbox(
    "Which list",
    ["Daily shortlist", "Weekly shortlist", "Monthly shortlist",
     "My watchlist", "Past daily picks", "Weekly performance"],
    index=0,
    help="Daily = passes today. Weekly = consistent over 7 days. "
         "Monthly = consistent over 30 days. "
         "Past picks = what was picked on any earlier date. "
         "Weekly performance = did the picks actually work.")
st.session_state["use_live"] = nav_c.toggle(
    "Live prices", value=st.session_state.get("use_live", True),
    help="Show current prices (about 15 minutes delayed) instead of the last "
         "close. Scoring always uses closes either way.")
use_live_prices = st.session_state["use_live"]

if view == "Practice trading":
    import paper
    for r in rows:
        r["strong"] = bool(r["trend"]) and (r["rs"] or 0) > 0
    paper.render(rows)
    st.stop()

# ---- overlay live prices on everything that will be displayed ----
if use_live_prices:
    _wl = {t for t, *_ in watchlist_tickers()}
    _need = {r["ticker"] for r in rows if r.get("qualifies") or r["ticker"] in _wl}
    if not _need:
        _need = {r["ticker"] for r in rows[:80]}
    _need |= _wl
    live_stamp = apply_live(rows, live_map(list(_need)[:120]))
    if live_stamp:
        st.caption(f"Live prices as of {live_stamp} · about 15 minutes delayed · "
                   f"scores and rankings still use closing prices")
    else:
        st.caption("Live prices unavailable right now — showing last close.")

# ================= MY WATCHLIST =================
if listing == "My watchlist":
    st.markdown("""<style>
      .whhero{background:linear-gradient(118deg,#B8860B 0%,#E0A93C 100%);
              border-radius:18px;padding:22px 26px;margin-bottom:16px;
              box-shadow:0 12px 30px -18px #B8860B}
      .whhero h2{font-family:'Bricolage Grotesque',sans-serif;color:#fff;
                 font-size:25px;margin:0;letter-spacing:-.02em}
      .whhero p{color:#ffffffdd;font-size:13.5px;margin:5px 0 0}
      .wcard{background:#fff;border:1px solid #E6ECF3;border-radius:14px;
             padding:18px 22px;margin-bottom:12px}
      .wname{font-family:'Bricolage Grotesque',sans-serif;font-weight:800;
             font-size:19px;color:#101B29}
      .wsub{color:#6B7C90;font-size:12.5px;margin-top:3px}
      .wbig{font-family:'Bricolage Grotesque',sans-serif;font-weight:800;
            font-size:22px;color:#101B29}
      .wlab{font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;
            font-weight:700;color:#6B7C90;margin:12px 0 6px}
      .wpt{font-size:13.5px;line-height:1.85;display:flex;gap:8px}
      .wsince{background:#FBF6E9;border-left:3px solid #B8860B;border-radius:8px;
              padding:11px 14px;font-size:13.5px;margin-top:10px}
      .wgone{background:#F4F6F8;border-left:3px solid #99A6B4;border-radius:8px;
             padding:11px 14px;font-size:13px;color:#5A6B80;margin-top:10px}
    </style>""", unsafe_allow_html=True)

    saved = watchlist_tickers()
    st.markdown(f"""<div class='whhero'><h2>My watchlist</h2>
      <p>{len(saved)} stocks you picked yourself · stays here until you remove
      it, whether or not it makes today's shortlist</p></div>""",
      unsafe_allow_html=True)

    if not saved:
        st.info("Nothing saved yet. Go to **Daily shortlist** and use "
                "**Add to watchlist** under the list.")
        st.stop()

    lookup = {r["ticker"]: r for r in rows}
    live_ok = [t for t, *_ in saved if t in lookup]

    if live_ok:
        chg = [lookup[t]["price"] / p * 100 - 100
               for t, _, p, _ in saved if t in lookup and p]
        c1, c2, c3 = st.columns(3)
        c1.metric("On the watchlist", len(saved))
        if chg:
            c2.metric("Average since added", f"{sum(chg)/len(chg):+.2f}%")
            c3.metric("Up since added",
                      f"{sum(1 for x in chg if x > 0)}/{len(chg)}")

    still = {r["ticker"] for r in rows if r.get("fits")}

    for tk, added, price_at, note in saved:
        r = lookup.get(tk)
        st.markdown("<div class='wcard'>", unsafe_allow_html=True)
        a, b, c, d = st.columns([2.4, 1.1, 1.1, 0.8])

        if r is None:
            a.markdown(f"<div class='wname'>{tk.replace('.NS','')}</div>"
                       f"<div class='wsub'>added {added}</div>",
                       unsafe_allow_html=True)
            b.markdown("<div class='wsub'>no current data</div>",
                       unsafe_allow_html=True)
        else:
            since = ((r["price"] / price_at - 1) * 100) if price_at else None
            a.markdown(f"<div class='wname'>{r['name']}</div>"
                       f"<div class='wsub'>{r['short']} · {r['sector']} · "
                       f"added {added}</div>", unsafe_allow_html=True)
            b.markdown(f"<div class='wbig'>₹{r['price']:,.0f}</div>"
                       f"<div class='wsub' style='color:"
                       f"{'#0B7A55' if r['day'] >= 0 else '#BE3B32'}'>"
                       f"{r['day']:+.2f}% today</div>", unsafe_allow_html=True)
            if since is not None:
                c.markdown(f"<div class='wbig' style='color:"
                           f"{'#0B7A55' if since >= 0 else '#BE3B32'}'>"
                           f"{since:+.1f}%</div>"
                           f"<div class='wsub'>since you added<br>"
                           f"(₹{price_at:,.0f})</div>", unsafe_allow_html=True)
            else:
                c.markdown(f"<div class='wsub'>score {r['score']}</div>",
                           unsafe_allow_html=True)

        if d.button("Remove", key=f"rm_{tk}", use_container_width=True):
            watchlist_remove(tk)
            st.rerun()

        if r is not None:
            if tk in still:
                st.markdown("<div class='wsince'><b>Still on today's "
                            "shortlist.</b> It continues to pass every test."
                            "</div>", unsafe_allow_html=True)
            else:
                why = "; ".join(r.get("why_not", [])) or "no longer qualifying"
                st.markdown(f"<div class='wgone'><b>Not on today's shortlist</b> "
                            f"— {why}. That is not a sell signal on its own; it "
                            f"means it is no longer a fresh buy candidate.</div>",
                            unsafe_allow_html=True)

            with st.expander("Why it was picked, and where it stands"):
                g, bcol = st.columns(2)
                with g:
                    st.markdown("<div class='wlab' style='color:#0B7A55'>"
                                "Looks good</div>", unsafe_allow_html=True)
                    for x in r["good"][:4]:
                        st.markdown(f"<div class='wpt'><span style='color:#0B7A55'>"
                                    f"✓</span><span>{x}</span></div>",
                                    unsafe_allow_html=True)
                with bcol:
                    st.markdown("<div class='wlab' style='color:#BE3B32'>"
                                "Watch out for</div>", unsafe_allow_html=True)
                    for x in (r["bad"][:4] or ["Nothing flagged"]):
                        st.markdown(f"<div class='wpt'><span style='color:#BE3B32'>"
                                    f"•</span><span>{x}</span></div>",
                                    unsafe_allow_html=True)
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Score", r["score"])
                m2.metric("vs index", f"{r['rs']:+.0f}%"
                          if r["rs"] is not None else "—")
                m3.metric("From high", f"{r['from_high']:+.1f}%")
                m4.metric("Daily move", f"{r['atr_pct']:.1f}%")
        st.markdown("</div>", unsafe_allow_html=True)

    st.caption("Your own list. Independent of paper trading, and it does not "
               "place any orders.")
    st.stop()


# ================= PAST DAILY PICKS =================
if listing == "Past daily picks":
    st.markdown("""<style>
      .phero{background:linear-gradient(118deg,#5B4BC4 0%,#7C6BE0 100%);
             border-radius:18px;padding:22px 26px;margin-bottom:16px;
             box-shadow:0 12px 30px -18px #5B4BC4}
      .phero h2{font-family:'Bricolage Grotesque',sans-serif;color:#fff;
                font-size:25px;margin:0;letter-spacing:-.02em}
      .phero p{color:#ffffffcc;font-size:13.5px;margin:5px 0 0}
    </style>""", unsafe_allow_html=True)

    days = read_history_dates()
    if not days:
        st.warning("No pick history yet. Run:\n\n    python backfill_history.py")
        st.stop()

    st.markdown(f"""<div class='phero'><h2>Past daily picks</h2>
      <p>What the system picked on any earlier date, and what has happened
      since · {len(days)} days recorded</p></div>""", unsafe_allow_html=True)

    d1, d2 = st.columns([1.4, 2])
    day = d1.selectbox("Pick a date", days, index=0)
    picks = read_picks_on(day, use_live_prices)

    if picks is None or picks.empty:
        st.info("Nothing was picked on that date.")
        st.stop()

    valid = picks[picks["since_pct"].notna()]
    if not valid.empty:
        up = int((valid["since_pct"] > 0).sum())
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Picked that day", len(picks))
        c2.metric("Up since", f"{up}/{len(valid)}")
        c3.metric("Average since", f"{valid['since_pct'].mean():+.2f}%")
        c4.metric("Best since", f"{valid['since_pct'].max():+.1f}%")
        st.caption(f"Measured from the closing price on {day} to the latest "
                   f"close. This is what the picks did, not what you would "
                   f"have made — see Weekly performance for that.")

    show = picks.copy()
    show["Company"] = show["name"].fillna(show["ticker"])
    show["Stock"] = show["ticker"].str.replace(".NS", "", regex=False)
    out = show[["Stock", "Company", "sector", "cap_bucket", "score",
                "price_then", "price_now", "since_pct"]].rename(columns={
        "sector": "Sector", "cap_bucket": "Size", "score": "Score",
        "price_then": f"Price on {day}", "price_now": "Price now",
        "since_pct": "Change %"})
    st.dataframe(out.round(2), use_container_width=True, hide_index=True,
                 height=520)
    st.stop()


# ================= WEEKLY PERFORMANCE =================
if listing == "Weekly performance":
    st.markdown("""<style>
      .whero{background:linear-gradient(118deg,#0E8C7A 0%,#33B7A1 100%);
             border-radius:18px;padding:22px 26px;margin-bottom:16px;
             box-shadow:0 12px 30px -18px #0E8C7A}
      .whero h2{font-family:'Bricolage Grotesque',sans-serif;color:#fff;
                font-size:25px;margin:0;letter-spacing:-.02em}
      .whero p{color:#ffffffcc;font-size:13.5px;margin:5px 0 0}
      .vbox{border-radius:10px;padding:15px 18px;margin:14px 0;
            font-size:14px;line-height:1.65}
    </style>""", unsafe_allow_html=True)

    st.markdown("""<div class='whero'><h2>Did the picks actually work?</h2>
      <p>If you had bought every stock the system picked, at the next day's
      open, and held to now</p></div>""", unsafe_allow_html=True)

    w1, w2 = st.columns([1.2, 3])
    window = w1.selectbox("Window", [7, 30],
                          format_func=lambda x: f"Last {x} days")
    perf, summ = read_tracker(window)

    if perf is None or perf.empty or summ is None:
        st.warning("No performance data yet. Run:\n\n    python tracker.py")
        st.stop()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Stocks picked", int(summ["picks"]))
    c2.metric("Win rate", f"{summ['win_rate']:.0f}%",
              f"{int(summ['winners'])} up, {int(summ['losers'])} down")
    c3.metric("Portfolio return", f"{summ['portfolio_ret']:+.2f}%",
              f"₹{summ['total_pnl']:,.0f}")
    c4.metric("Versus Nifty",
              f"{summ['excess']:+.2f} pts" if summ["excess"] is not None else "—",
              f"index {summ['nifty_ret']:+.2f}%"
              if summ["nifty_ret"] is not None else None)

    ex = summ["excess"]
    vcol = ("#0E8C7A", "#ECF8F5") if (ex or 0) > 2 else \
           ("#C2560E", "#FDF0EC") if (ex or 0) < -1 else ("#C99012", "#FDF6E9")
    st.markdown(f"<div class='vbox' style='background:{vcol[1]};"
                f"border-left:3px solid {vcol[0]}'><b>Verdict.</b><br>"
                f"{summ['verdict']}</div>", unsafe_allow_html=True)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Average return", f"{summ['avg_ret']:+.2f}%")
    m2.metric("Median return", f"{summ['median_ret']:+.2f}%")
    m3.metric("Avg best moment", f"{summ['avg_max_gain']:+.1f}%",
              help="How far the average pick rose at its peak")
    m4.metric("Avg worst moment", f"{summ['avg_max_dd']:+.1f}%",
              help="How far the average pick fell at its trough — what you "
                   "would have had to sit through")

    b1, b2 = st.columns(2)
    b1.success(f"**Best:** {summ['best_ticker'].replace('.NS','')} "
               f"{summ['best_ret']:+.1f}%")
    b2.error(f"**Worst:** {summ['worst_ticker'].replace('.NS','')} "
             f"{summ['worst_ret']:+.1f}%")

    st.markdown("#### Every pick")
    t = perf.copy()
    t["Stock"] = t["ticker"].str.replace(".NS", "", regex=False)
    cols = {"name": "Company", "sector": "Sector", "first_picked": "First picked",
            "entry_price": "Entry", "latest_price": "Now", "ret_pct": "Return %",
            "max_gain_pct": "Best %", "max_dd_pct": "Worst %",
            "pnl": "P&L ₹", "excess_pct": "vs Nifty", "times_picked": "Times"}
    st.dataframe(t[["Stock"] + list(cols)].rename(columns=cols).round(2),
                 use_container_width=True, hide_index=True, height=480)

    st.caption("Entry is the next day's open after a pick — you cannot buy a "
               "price that has already printed. Brokerage and slippage are not "
               "deducted, so real results would be slightly worse. One window "
               "is not evidence; check several before concluding anything.")
    st.stop()


# ================= MONTHLY VIEW =================
if listing in ("Monthly shortlist", "Weekly shortlist"):
    _period = "weekly" if listing.startswith("Weekly") else "monthly"
    mdf = read_monthly(_period)
    if mdf is None or mdf.empty:
        st.warning("No monthly data yet. Run these once in your terminal:\n\n"
                   "    python backfill_history.py\n    python monthly.py")
        st.stop()

    STAGE_COLOR = {"Established": ("#0E8C7A", "#ECF8F5"),
                   "Early": ("#1F6FB2", "#EDF5FB"),
                   "Extended": ("#C2560E", "#FDF2E9"),
                   "Fading": ("#8A3A34", "#FBEEEE")}

    st.markdown("""<style>
      .mhero{background:linear-gradient(118deg,#1F6FB2 0%,#4A9BD8 100%);
             border-radius:18px;padding:22px 26px;margin-bottom:16px;
             box-shadow:0 12px 30px -18px #1F6FB2;}
      .mhero h2{font-family:'Bricolage Grotesque',sans-serif;color:#fff;
                font-size:25px;margin:0;letter-spacing:-.02em}
      .mhero p{color:#ffffffcc;font-size:13.5px;margin:5px 0 0}
      .mcard{background:#fff;border:1px solid #E6ECF3;border-radius:14px;
             padding:16px 20px;margin-bottom:10px}
      .mname{font-family:'Bricolage Grotesque',sans-serif;font-weight:800;
             font-size:18px;color:#101B29}
      .msub{color:#6B7C90;font-size:12.5px;margin-top:2px}
      .badge{display:inline-block;font-size:10px;font-weight:700;
             letter-spacing:.09em;text-transform:uppercase;padding:3px 9px;
             border-radius:20px;margin-right:6px}
      .freq{font-family:'Bricolage Grotesque',sans-serif;font-weight:800;
            font-size:21px;color:#101B29}
      .mlab{font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;
            font-weight:700;color:#6B7C90;margin:14px 0 6px}
      .mpt{font-size:13.5px;line-height:1.75;display:flex;gap:8px;
           margin-bottom:3px}
      .mout{background:#F4F8FB;border-left:3px solid #1F6FB2;border-radius:8px;
            padding:12px 15px;font-size:13.5px;line-height:1.6;margin-top:12px}
      .entry{border-radius:10px;padding:14px 17px;margin-top:12px;
             font-size:13.5px;line-height:1.65}
      .e-go{background:#ECF8F5;border-left:3px solid #0E8C7A}
      .e-wait{background:#FDF6E9;border-left:3px solid #C99012}
      .e-no{background:#FDF0EC;border-left:3px solid #C2560E}
      .ehead{font-family:'Bricolage Grotesque',sans-serif;font-weight:800;
             font-size:16px;margin-bottom:5px}
      .elevels{display:flex;gap:22px;margin-top:10px;flex-wrap:wrap}
      .elevels div{font-size:12px;color:#6B7C90}
      .elevels b{display:block;font-size:16px;color:#101B29;
                 font-family:'Bricolage Grotesque',sans-serif;font-weight:800}
    </style>""", unsafe_allow_html=True)

    st.markdown(f"""<div class='mhero'><h2>{"Weekly" if _period == "weekly" else "Monthly"} shortlist</h2>
      <p>{len(mdf)} stocks that kept qualifying across
      {int(mdf['days_30'].iloc[0])} trading days, not just today ·
      as of {mdf['as_of'].iloc[0]}</p></div>""", unsafe_allow_html=True)

    # Show how many sit in each bucket, so an unchanged count is never a mystery
    stage_counts = mdf["stage"].value_counts().to_dict()
    cap_counts = mdf["cap_bucket"].value_counts().to_dict()

    f1, f2, f3 = st.columns([1.5, 1.5, 1.4])
    stage_opts = [f"All stages ({len(mdf)})"] + [
        f"{s_} ({stage_counts[s_]})"
        for s_ in ["Established", "Early", "Extended", "Fading"]
        if s_ in stage_counts]
    pick_stage = f1.selectbox("Stage", stage_opts).split(" (")[0]

    cap_opts = [f"All sizes ({len(mdf)})"] + [
        f"{c} ({cap_counts[c]})" for c in sorted(cap_counts) if c]
    pick_cap = f2.selectbox("Company size", cap_opts).split(" (")[0]

    min_cons = f3.slider("Minimum consistency (%)", 40, 100, 50, 5)

    g0, g1, g2 = st.columns([1.2, 1.6, 1.6])
    top_n = g0.selectbox("How many to show", ["Top 10", "Top 20", "Top 30",
                                              "All"], index=0)
    entry_opts = ["Any timing", "Favourable to bid now",
                  "Better to wait", "Over-extended"]
    pick_entry = g1.selectbox("Entry timing", entry_opts)
    sort_by = g2.selectbox("Sort by", ["Most favourable to bid",
                                       "Most likely to continue",
                                       "Most consistent",
                                       "Biggest monthly gain"])

    view_df = mdf.copy()
    if pick_stage != "All stages":
        view_df = view_df[view_df["stage"] == pick_stage]
    if pick_cap != "All sizes":
        view_df = view_df[view_df["cap_bucket"] == pick_cap]
    view_df = view_df[view_df["consistency"] >= min_cons]

    if "entry_verdict" in view_df.columns:
        if pick_entry == "Favourable to bid now":
            view_df = view_df[view_df["entry_verdict"].isin(
                ["Buy now", "Buy now or wait"])]
        elif pick_entry == "Better to wait":
            view_df = view_df[view_df["entry_verdict"] == "Wait for pullback"]
        elif pick_entry == "Over-extended":
            view_df = view_df[view_df["entry_verdict"] == "Do not chase"]

    limit = {"Top 10": 10, "Top 20": 20, "Top 30": 30, "All": None}[top_n]

    sort_col = {"Most favourable to bid": "fav_score",
                "Most likely to continue": "cont_score",
                "Most consistent": "consistency",
                "Biggest monthly gain": "ret_1m"}[sort_by]
    if sort_col in view_df.columns:
        view_df = view_df.sort_values(sort_col, ascending=False)
    if limit:
        view_df = view_df.head(limit)

    if use_live_prices:
        _q = live_map(view_df["ticker"].tolist()[:60])
        if _q:
            view_df = view_df.copy()
            view_df["close_price"] = view_df["price"]
            view_df["price"] = view_df.apply(
                lambda r: _q[r["ticker"]]["price"] if r["ticker"] in _q
                else r["price"], axis=1)

    st.caption(f"Showing {len(view_df)} of {len(mdf)}. "
               f"Consistency is how often a stock passed the daily test this "
               f"month — the core measure here.")

    if view_df.empty:
        st.info("Nothing matches. Loosen the filters.")
        st.stop()

    MAX_CARDS = 30
    if len(view_df) > MAX_CARDS:
        st.info(f"Showing the {MAX_CARDS} most consistent of {len(view_df)}. "
                f"Narrow the filters to see others.")
    for r in view_df.head(MAX_CARDS).to_dict("records"):
        col, tint = STAGE_COLOR.get(r["stage"], ("#6B7C90", "#F2F5F8"))
        with st.container():
            st.markdown("<div class='mcard'>", unsafe_allow_html=True)
            a, b, c = st.columns([2.6, 1.1, 1.1])
            a.markdown(
                f"<span class='badge' style='background:{tint};color:{col}'>"
                f"{r['stage']}</span>"
                + (f"<span class='badge' style='background:#EEF1F6;color:#5A6B80'>"
                   f"new high</span>" if r["new_high"] else "")
                + (f"<span class='badge' style='background:"
                   f"{'#ECF8F5' if r.get('entry_verdict')=='Buy now' else '#FDF0EC' if r.get('entry_verdict')=='Do not chase' else '#FDF6E9'};"
                   f"color:{'#0E8C7A' if r.get('entry_verdict')=='Buy now' else '#C2560E' if r.get('entry_verdict')=='Do not chase' else '#C99012'}'>"
                   f"{r['entry_verdict']}</span>" if r.get("entry_verdict") else "")
                + f"<div class='mname' style='margin-top:6px'>{r['name']}</div>"
                f"<div class='msub'>{r['ticker'].replace('.NS','')} · "
                f"{r['sector']} · {r['cap_bucket'] or '?'} cap</div>",
                unsafe_allow_html=True)
            b.markdown(f"<div class='freq'>{r['hits_30']}/{r['days_30']}</div>"
                       f"<div class='msub'>days qualified<br>"
                       f"({r['consistency']:.0f}%)</div>",
                       unsafe_allow_html=True)
            c.markdown(f"<div class='freq' style='color:"
                       f"{'#0B7A55' if r['ret_1m'] >= 0 else '#BE3B32'}'>"
                       f"{r['ret_1m']:+.1f}%</div>"
                       f"<div class='msub'>this month · ₹{r['price']:,.0f}<br>"
                       + (f"bid appeal {r['fav_score']}/100"
                          if r.get("fav_score") is not None else "")
                       + "</div>", unsafe_allow_html=True)

            with st.expander("Why is this on the monthly list?"):
                st.markdown("<div class='mlab'>Technical reasons</div>",
                            unsafe_allow_html=True)
                for t in json.loads(r["tech_json"]):
                    st.markdown(f"<div class='mpt'><span style='color:#1F6FB2'>▸"
                                f"</span><span>{t}</span></div>",
                                unsafe_allow_html=True)
                st.markdown("<div class='mlab'>Sentiment &amp; fundamentals</div>",
                            unsafe_allow_html=True)
                for t in json.loads(r["senti_json"]):
                    st.markdown(f"<div class='mpt'><span style='color:#0E8C7A'>▸"
                                f"</span><span>{t}</span></div>",
                                unsafe_allow_html=True)
                st.markdown(f"<div class='mout'><b>Where it stands now.</b><br>"
                            f"{r['outlook']}</div>", unsafe_allow_html=True)

                v = r.get("entry_verdict") or ""
                cls = ("e-go" if v == "Buy now" else
                       "e-no" if v == "Do not chase" else "e-wait")
                if v:
                    st.markdown(f"""<div class='entry {cls}'>
                      <div class='ehead'>{v}</div>
                      {r['entry_note']}
                      <div class='elevels'>
                        <div>Price now<b>₹{r['price']:,.0f}</b></div>
                        <div>Better entry<b>₹{r['entry_ideal']:,.0f}</b></div>
                        <div>Don't pay above<b>₹{r['entry_ceiling']:,.0f}</b></div>
                        <div>Stop<b>₹{r['stop_price']:,.0f}</b></div>
                        <div>Target<b>₹{r['target_price']:,.0f}</b></div>
                        <div>Payoff<b>{r['rr']:.1f} : 1</b></div>
                      </div></div>""", unsafe_allow_html=True)
                if r.get("forecast"):
                    band = r.get("cont_band", "")
                    bcol = {"High": ("#0E8C7A", "#ECF8F5"),
                            "Moderate": ("#C99012", "#FDF6E9"),
                            "Low": ("#C2560E", "#FDF0EC")}.get(
                                band, ("#6B7C90", "#F2F5F8"))
                    st.markdown(f"""<div class='entry'
                        style='background:{bcol[1]};border-left:3px solid {bcol[0]}'>
                      <div class='ehead'>Next month — continuation {band.lower()}
                        ({r['cont_score']}/100)</div>
                      {r['forecast']}
                      <div class='elevels'>
                        <div>Range low<b>₹{r['fc_low']:,.0f}</b></div>
                        <div>If trend holds<b>₹{r['fc_base']:,.0f}</b></div>
                        <div>Range high<b>₹{r['fc_high']:,.0f}</b></div>
                        <div>Entry appeal<b>{r['fav_score']}/100</b></div>
                      </div></div>""", unsafe_allow_html=True)

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("This week", f"{r['hits_7']}/{r['days_7']}")
                m2.metric("Current streak", f"{r['streak']}d")
                m3.metric("vs sector", f"{r['excess_vs_sector']:+.1f}%"
                          if r["excess_vs_sector"] is not None else "—")
                m4.metric("Daily move", f"{r['atr_pct']:.1f}%")
            st.markdown("</div>", unsafe_allow_html=True)

    st.caption("Consistency measures how often a stock passed the same daily "
               "test, not whether it will keep rising. Verify the reasons "
               "before acting. Not investment advice.")
    st.stop()

# ---- work out what fits each category ----
CATS = list(modes.PROFILES.keys())
by_cat, needed = {}, set()
for cat in CATS:
    P = modes.PROFILES[cat]
    fits = [r for r in rows if modes.qualifies(r, P)[0]][:PER_CATEGORY]
    by_cat[cat] = [r["ticker"] for r in fits]
    needed.update(by_cat[cat])

series = series_for(sorted(needed))
news = news_for(sorted(needed))

payload = {
    "asOf": as_of,
    "total": len(rows),
    "cats": [{
        "name": c,
        "blurb": modes.PROFILES[c]["blurb"],
        "risk": modes.PROFILES[c]["risk_note"],
        "stopMult": modes.PROFILES[c]["stop_mult"],
        "maxPos": modes.PROFILES[c]["max_positions"],
        "tickers": by_cat[c],
    } for c in CATS],
    "stocks": {r["ticker"]: {
        "short": r["short"], "name": r["name"], "sector": r["sector"],
        "days": r["days"], "isnew": r["isnew"],
        "mcap": r["mcap"], "price": r["price"], "day": r["day"],
        "atr": r["atr"], "good": r["good"][:4], "bad": r["bad"][:4],
        "series": series.get(r["ticker"], {"c": [], "d": []}),
        "news": news.get(r["ticker"], []),
    } for r in rows if r["ticker"] in needed},
}

HTML = """
<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --ink:#101B29; --body:#38475A; --soft:#6B7C90; --line:#E6ECF3;
  --card:#FFFFFF; --bg:#FAFBFD;
  --a1:#5B4BC4; --a2:#7C6BE0; --tint:#F3F1FD;
  --good:#0B7A55; --bad:#BE3B32;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--body);
     font-size:14px;-webkit-font-smoothing:antialiased}
.dsp{font-family:'Bricolage Grotesque',system-ui,sans-serif;color:var(--ink);
     letter-spacing:-.022em;font-weight:800}
.wrap{max-width:100%;margin:0 auto;padding:0 2px 40px}

/* ---------- header ---------- */
.hero{background:linear-gradient(118deg,var(--a1) 0%,var(--a2) 100%);
      border-radius:20px;padding:26px 30px;color:#fff;margin-bottom:18px;
      box-shadow:0 14px 34px -18px var(--a1);transition:background .5s ease}
.hero h1{font-family:'Bricolage Grotesque',sans-serif;font-weight:800;
         font-size:30px;letter-spacing:-.025em;color:#fff}
.hero p{color:#ffffffcc;margin-top:5px;font-size:13.5px}

/* ---------- category tabs ---------- */
.lab{font-size:10.5px;letter-spacing:.17em;text-transform:uppercase;
     font-weight:700;color:var(--a1);margin:0 0 9px 2px;transition:color .4s}
.tabs{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:22px}
.tab{background:var(--card);border:1.5px solid var(--line);border-radius:14px;
     padding:14px 15px;cursor:pointer;transition:all .22s cubic-bezier(.4,0,.2,1);
     text-align:left}
.tab:hover{transform:translateY(-2px);box-shadow:0 8px 20px -12px #101B2955}
.tab .t{font-family:'Bricolage Grotesque',sans-serif;font-weight:600;
        font-size:14.5px;color:var(--ink);line-height:1.25}
.tab .n{font-size:11.5px;color:var(--soft);margin-top:5px}
.tab.on{border-color:transparent;color:#fff;
        box-shadow:0 10px 26px -14px currentColor}
.tab.on .t,.tab.on .n{color:#fff}
.tab.on .n{opacity:.85}

/* ---------- layout ---------- */
.grid{display:grid;grid-template-columns:320px 1fr;gap:18px;align-items:start}
.left{position:sticky;top:8px}
.stack{display:flex;flex-direction:column;gap:14px}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;
      box-shadow:0 1px 2px #101B290A}

/* ---------- list ---------- */
.listhead{padding:14px 16px;border-bottom:1px solid var(--line)}
.pill{display:inline-block;background:var(--a1);color:#fff;font-weight:600;
      font-size:12px;padding:5px 12px;border-radius:99px;transition:background .4s}
.list{max-height:560px;overflow-y:auto}
.list::-webkit-scrollbar{width:7px}
.list::-webkit-scrollbar-thumb{background:#DCE3EB;border-radius:9px}
.item{padding:12px 16px;border-bottom:1px solid var(--line);cursor:pointer;
      border-left:3px solid transparent;transition:background .16s}
.item:hover{background:var(--tint)}
.item.on{background:var(--tint);border-left-color:var(--a1)}
.item .nm{font-weight:600;color:var(--ink);font-size:13.5px;line-height:1.3}
.item .sub{font-size:11.5px;color:var(--soft);margin-top:3px}
.tag{display:inline-block;font-size:9.5px;font-weight:700;letter-spacing:.06em;
     padding:1px 6px;border-radius:20px;margin-left:5px;text-transform:uppercase}
.tag.new{background:#FFF1E4;color:#C2560E}
.tag.held{background:#E9F6F1;color:#0B7A55}
.item .px{font-size:13.5px;font-weight:600;color:var(--ink);text-align:right}
.row{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
.up{color:var(--good)} .down{color:var(--bad)}

/* ---------- detail ---------- */
.pad{padding:21px 26px}
.co{font-family:'Bricolage Grotesque',sans-serif;font-weight:800;font-size:25px;
    color:var(--ink);letter-spacing:-.024em;line-height:1.15}
.meta{font-size:12.5px;color:var(--soft);margin-top:4px}
.big{font-family:'Bricolage Grotesque',sans-serif;font-weight:800;font-size:33px;
     color:var(--ink);line-height:1;letter-spacing:-.03em}

.seg{display:inline-flex;background:#F1F4F8;border-radius:9px;padding:3px;gap:2px;
     margin:18px 0 10px}
.seg button{border:0;background:transparent;font-family:inherit;font-size:12px;
            color:var(--soft);padding:6px 14px;border-radius:7px;cursor:pointer;
            transition:all .18s;font-weight:500}
.seg button.on{background:#fff;color:var(--ink);font-weight:600;
               box-shadow:0 1px 3px #101B2914}

svg .ln{fill:none;stroke:var(--a1);stroke-width:2.2;stroke-linejoin:round;
        transition:stroke .4s}
svg .avg{fill:none;stroke:#C9D3DE;stroke-width:1.4;stroke-dasharray:4 4}
svg .ar{fill:url(#g)}
.tip{position:absolute;background:var(--ink);color:#fff;font-size:11.5px;
     padding:6px 10px;border-radius:7px;pointer-events:none;opacity:0;
     transition:opacity .12s;white-space:nowrap;font-weight:500}

.cols{display:grid;grid-template-columns:1fr 1fr;gap:40px}
@media(max-width:700px){.cols{grid-template-columns:1fr}}
.pt{font-size:13.5px;line-height:1.95;display:flex;gap:8px}
.pt i{font-style:normal;flex-shrink:0}

.buy{background:linear-gradient(120deg,var(--tint) 0%,#fff 90%);
     border-left:3px solid var(--a1);transition:border-color .4s}
.buybig{font-family:'Bricolage Grotesque',sans-serif;font-weight:800;font-size:21px;
        color:var(--a1);letter-spacing:-.02em;transition:color .4s}
input[type=range]{width:100%;accent-color:var(--a1);margin:10px 0 4px;height:4px}
.amt{display:flex;justify-content:space-between;font-size:12px;color:var(--soft)}

.nw{border-left:2px solid var(--line);padding:7px 0 7px 13px;margin-bottom:9px;
    font-size:13.5px;line-height:1.5;color:var(--body)}
.nw a{color:var(--a1);text-decoration:none;font-size:12px;font-weight:500}
.nw span{font-size:11.5px;color:var(--soft)}
.foot{font-size:12px;color:var(--soft);margin-top:24px;line-height:1.6}
.empty{padding:50px 26px;text-align:center;color:var(--soft);font-size:14px}
.fade{animation:f .3s ease}
@keyframes f{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}

/* ================================================================
   MOBILE ONLY — every rule above this point is unchanged.
   These only apply at <=900px, so the desktop render is identical.
   ================================================================ */
@media(max-width:900px){
  .wrap{padding:0 0 24px}

  .hero{padding:20px 18px;border-radius:16px;margin-bottom:14px}
  .hero h1{font-size:23px}
  .hero p{font-size:12.5px;margin-top:4px}

  .lab{margin-bottom:7px}
  .tabs{grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:16px}
  .tab{padding:12px 13px;border-radius:12px}
  .tab .t{font-size:13.5px}
  .tab .n{font-size:11px;margin-top:3px}
  .tab:hover{transform:none;box-shadow:none}

  /* .grid already collapses to one column; the sticky column must not
     stay pinned once it is stacked above the detail panel */
  .grid{gap:12px}
  .left{position:static;top:auto}
  .card{border-radius:14px}

  /* cap the list so the detail panel is reachable without endless scrolling */
  .list{max-height:320px;-webkit-overflow-scrolling:touch}
  .item{padding:11px 14px}
  #list .row > div:first-child{min-width:0}
  #list .nm{overflow-wrap:anywhere}
  #list .px{white-space:nowrap}

  .pad{padding:16px 16px}
  .co{font-size:20px}
  .meta{font-size:12px}
  .big{font-size:26px}

  /* detail header: price block drops below the company name */
  #top .row{flex-wrap:wrap;gap:0}
  #top .row > div:first-child{flex:1 1 100%}
  #top .row > div:last-child{flex:1 1 100%;margin-top:10px;
      text-align:left !important;display:flex;align-items:baseline;gap:10px}

  .seg{display:flex;width:100%;margin:14px 0 8px}
  .seg button{flex:1;text-align:center;padding:8px 6px}

  /* the SVG keeps its 700x250 ratio, so a fixed 250px box letterboxes
     into dead space once the width drops - match the height to the ratio */
  #cwrap svg{height:170px !important}
  .tip{font-size:11px;padding:5px 8px}

  .cols{gap:16px}
  .pt{font-size:13px;line-height:1.8}

  .buybig{font-size:18px}
  input[type=range]{height:20px;margin:12px 0 4px}

  .nw{font-size:13px}
  .foot{font-size:11.5px;margin-top:18px}
  .empty{padding:34px 18px;font-size:13.5px}
}

@media(max-width:420px){
  .tabs{grid-template-columns:1fr}
  .hero h1{font-size:21px}
  .co{font-size:18px}
  .big{font-size:23px}
  .pad{padding:14px 13px}
  #cwrap svg{height:145px !important}
}
</style></head><body>
<div class="wrap">
  <div class="hero" id="hero">
    <h1>My stock shortlist</h1>
    <p id="heroP"></p>
  </div>

  <div class="lab">Step 1 — choose your category</div>
  <div class="tabs" id="tabs"></div>

  <div class="lab" id="lab2">Step 2 — choose a company</div>
  <div class="grid">
    <div class="card left">
      <div class="listhead"><span class="pill" id="pill"></span></div>
      <div class="list" id="list"></div>
    </div>
    <div id="top"></div>
  </div>
  <div class="stack" id="rest" style="margin-top:16px"></div>
</div>

<script>
const D = __DATA__;
const THEME = [
  ["#5B4BC4","#7C6BE0","#F3F1FD"],
  ["#1F6FB2","#4A9BD8","#EDF5FB"],
  ["#0E8C7A","#33B7A1","#ECF8F5"],
  ["#C2560E","#EE8A3C","#FDF2E9"],
];
let ci = 0, sel = null, days = 130, budget = 50000;

const inr = n => "₹" + Math.round(n).toLocaleString("en-IN");
const $ = id => document.getElementById(id);

function theme(){
  const [a1,a2,t] = THEME[ci], r = document.documentElement.style;
  r.setProperty("--a1",a1); r.setProperty("--a2",a2); r.setProperty("--tint",t);
  $("hero").style.background = `linear-gradient(118deg,${a1} 0%,${a2} 100%)`;
  $("hero").style.boxShadow = `0 14px 34px -18px ${a1}`;
}

function tabs(){
  $("tabs").innerHTML = D.cats.map((c,i)=>{
    const [a1,a2] = THEME[i];
    const on = i===ci;
    const bg = on ? `background:linear-gradient(120deg,${a1},${a2});color:${a1}` : "";
    return `<div class="tab ${on?'on':''}" style="${bg}" onclick="pick(${i})">
      <div class="t">${c.name.split("—")[0].trim()}</div>
      <div class="n">${c.tickers.length} fit${c.tickers.length===1?'':'s'}</div></div>`;
  }).join("");
}

function list(){
  const c = D.cats[ci];
  $("pill").textContent = `${c.tickers.length} companies fit`;
  if(!c.tickers.length){ $("list").innerHTML =
    `<div class="empty">Nothing fits today.<br>Try another category.</div>`; return; }
  $("list").innerHTML = c.tickers.map(t=>{
    const s = D.stocks[t], on = t===sel;
    return `<div class="item ${on?'on':''}" onclick="choose('${t}')"><div class="row">
      <div><div class="nm">${s.name}</div><div class="sub">${s.sector}
        ${s.isnew ? `<span class="tag new">new</span>`
                  : s.days>=10 ? `<span class="tag held">${s.days}d</span>` : ""}
      </div></div>
      <div><div class="px">${inr(s.price)}</div>
      <div class="sub ${s.day>=0?'up':'down'}" style="text-align:right">
        ${s.day>=0?'+':''}${s.day.toFixed(2)}%</div></div></div></div>`;
  }).join("");
}

function chart(s){
  const c = s.series.c.slice(-days), d = s.series.d.slice(-days);
  if(c.length<3) return "<div class='empty'>No chart data</div>";
  const W=700,H=250,P=8;
  const lo=Math.min(...c)*0.99, hi=Math.max(...c)*1.01;
  const X=i=>P+i/(c.length-1)*(W-2*P), Y=v=>P+(1-(v-lo)/(hi-lo))*(H-2*P);
  const line = c.map((v,i)=>`${i?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("");
  const area = `${line}L${X(c.length-1)},${H}L${X(0)},${H}Z`;
  const avg = c.map((_,i)=>{ if(i<20) return null;
      return c.slice(i-19,i+1).reduce((a,b)=>a+b,0)/20; });
  const aline = avg.map((v,i)=>v==null?null:
      `${avg[i-1]==null?'M':'L'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`)
      .filter(Boolean).join("");
  const [a1] = THEME[ci];
  return `<div style="position:relative" id="cwrap">
   <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:250px;display:block"
        onmousemove="hover(event,${lo},${hi},${c.length})" onmouseleave="hide()">
    <defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="${a1}" stop-opacity=".16"/>
      <stop offset="100%" stop-color="${a1}" stop-opacity="0"/></linearGradient></defs>
    <path class="ar" d="${area}"/><path class="avg" d="${aline}"/>
    <path class="ln" d="${line}"/>
    <circle id="dot" r="4" fill="${a1}" stroke="#fff" stroke-width="2" opacity="0"/>
   </svg><div class="tip" id="tip"></div></div>`;
}

let CUR=null;
function hover(e,lo,hi,n){
  const svg=e.currentTarget, r=svg.getBoundingClientRect();
  const i=Math.round((e.clientX-r.left)/r.width*(n-1));
  if(i<0||i>=n||!CUR) return;
  const c=CUR.series.c.slice(-days), d=CUR.series.d.slice(-days);
  const x=(i/(n-1))*r.width, y=(1-(c[i]-lo)/(hi-lo))*(r.height-16)+8;
  const dot=$("dot");
  dot.setAttribute("cx",8+i/(n-1)*684); dot.setAttribute("cy",
    8+(1-(c[i]-lo)/(hi-lo))*234); dot.setAttribute("opacity","1");
  const t=$("tip"); t.style.opacity="1"; t.textContent=`${d[i]}  ${inr(c[i])}`;
  t.style.left=Math.min(Math.max(x-42,0),r.width-100)+"px";
  t.style.top=(y-34)+"px";
}
function hide(){ const d=$("dot"); if(d) d.setAttribute("opacity","0");
  const t=$("tip"); if(t) t.style.opacity="0"; }

function detail(){
  if(!sel){ $("top").innerHTML =
      `<div class="card"><div class="empty">Pick a company from the list.</div></div>`;
    $("rest").innerHTML=""; return; }
  const s = D.stocks[sel]; CUR = s;
  const c = D.cats[ci];
  const stop = s.price - c.stopMult*s.atr;
  const sh = Math.floor(budget/s.price), cost = sh*s.price, loss = sh*(s.price-stop);

  $("top").innerHTML = `<div class="card pad fade">
    <div class="row">
      <div><div class="co">${s.name}</div><div class="meta">${s.short} · ${s.sector}${
        s.mcap? " · " + inr(s.mcap) + " cr company":""}</div>
        <div class="meta" style="margin-top:5px">${
          s.isnew ? `<b style="color:#C2560E">Appeared on the list today.</b>
                     Newly qualifying stocks are less proven — worth waiting a
                     few days to see if it holds.`
          : s.days>=20 ? `<b style="color:#0B7A55">Has held its place for
                     ${s.days} days.</b> A trend this persistent is more
                     trustworthy than a fresh signal.`
          : s.days>1 ? `On the list for ${s.days} days running.`
          : ``}</div></div>
      <div style="text-align:right"><div class="big">${inr(s.price)}</div>
        <div class="${s.day>=0?'up':'down'}" style="font-size:14px;font-weight:600">
          ${s.day>=0?'+':''}${s.day.toFixed(2)}% today</div></div>
    </div>
    <div class="seg">
      ${[["3 months",63],["6 months",130]].map(([l,v])=>
        `<button class="${days===v?'on':''}" onclick="setDays(${v})">${l}</button>`
      ).join("")}
    </div>
    ${chart(s)}
  </div>`;

  $("rest").innerHTML = `
    <div class="card pad fade">
      <div class="cols">
        <div><div class="lab" style="color:var(--good)">Looks good</div>
          ${s.good.map(g=>`<div class="pt"><i style="color:var(--good)">✓</i>
            <span>${g}</span></div>`).join("") || "<div class='pt'>—</div>"}</div>
        <div><div class="lab" style="color:var(--bad)">Watch out for</div>
          ${(s.bad.length?s.bad:["Nothing flagged — but nobody has read the accounts"])
            .map(b=>`<div class="pt"><i style="color:var(--bad)">•</i>
            <span>${b}</span></div>`).join("")}</div>
      </div>
    </div>

    <div class="card buy pad">
      <div class="amt"><span>If you buy</span><span>${inr(budget)}</span></div>
      <input type="range" min="10000" max="300000" step="5000" value="${budget}"
             oninput="setBudget(this.value)">
      <div class="buybig">${sh.toLocaleString("en-IN")} shares · ${inr(cost)}</div>
      <div style="margin-top:7px;font-size:14px">Sell if it falls to
        <b>${inr(stop)}</b> — that caps your loss at about <b>${inr(loss)}</b>.</div>
    </div>

    ${s.news.length? `<div class="card pad"><div class="lab">Recent news</div>` +
      s.news.map(n=>`<div class="nw">${n.h}<br><span>${n.p} · ${n.s}</span>${
        n.u? ` &nbsp;<a href="${n.u}" target="_blank">Open</a>`:""}</div>`).join("")
      + `</div>` : ""}

    <div class="foot" style="margin-top:0;padding:0 4px">Built from price movement and
      headlines only. Nobody has read this company's accounts. Check the news before
      you act. Not investment advice.</div>`;
}

function setDays(v){ days=v; detail(); }
function setBudget(v){ budget=+v;
  const s=D.stocks[sel], c=D.cats[ci];
  const stop=s.price-c.stopMult*s.atr, sh=Math.floor(budget/s.price);
  document.querySelector("#rest .buybig").textContent =
    `${sh.toLocaleString("en-IN")} shares · ${inr(sh*s.price)}`;
  document.querySelector("#rest .amt").lastElementChild.textContent = inr(budget);
  document.querySelector("#rest .buy > div:last-child").innerHTML =
    `Sell if it falls to <b>${inr(stop)}</b> — that caps your loss at about
     <b>${inr(sh*(s.price-stop))}</b>.`;
}
function pick(i){ ci=i; sel=D.cats[i].tickers[0]||null;
  theme(); tabs(); list(); detail(); }
function choose(t){ sel=t; list(); detail(); }

$("heroP").textContent = `${D.total.toLocaleString("en-IN")} companies checked · prices as of ${D.asOf}`;
pick(0);

/* ---- mobile only: fit the host iframe to the stacked content ----
   Above 900px this returns immediately, so the desktop height=1320
   set in Python is left exactly as it was. If the host blocks access
   to the frame element this silently does nothing and the iframe
   falls back to its own scrollbar (scrolling=True). */
function syncHeight(){
  if(window.innerWidth > 900) return;
  try{
    const f = window.frameElement;
    if(!f) return;
    const h = Math.ceil(document.documentElement.scrollHeight) + 8;
    const cur = parseInt(f.style.height, 10) || f.clientHeight || 0;
    if(Math.abs(cur - h) > 4){
      f.style.height = h + "px";
      f.setAttribute("height", h);
    }
  }catch(e){}
}
window.addEventListener("resize", syncHeight);
if(window.ResizeObserver){
  new ResizeObserver(syncHeight).observe(document.documentElement);
}
syncHeight();
</script></body></html>
"""

# ---- add to watchlist (native control: the component cannot write back) ----
saved_now = {t for t, *_ in watchlist_tickers()}
addable = [r for r in rows if r["ticker"] in needed
           and r["ticker"] not in saved_now]

with st.expander(f"Add to watchlist  ·  {len(saved_now)} saved", expanded=False):
    if not addable:
        st.caption("Everything on today's shortlist is already saved.")
    else:
        labels = {r["ticker"]: f"{r['name']} · ₹{r['price']:,.0f} · "
                               f"score {r['score']}" for r in addable}
        chosen = st.multiselect(
            "Pick from today's shortlist", list(labels.keys()),
            format_func=lambda k: labels[k],
            help="Saved stocks stay on your watchlist until you remove them, "
                 "whether or not they qualify again.")
        if st.button("Save to watchlist", type="primary", disabled=not chosen):
            for tk in chosen:
                pr = next(r["price"] for r in rows if r["ticker"] == tk)
                watchlist_add(tk, pr)
            st.success(f"Added {len(chosen)} to your watchlist.")
            st.rerun()

    if saved_now:
        st.caption("Currently saved: "
                   + ", ".join(sorted(t.replace(".NS", "") for t in saved_now)))

components.html(HTML.replace("__DATA__", json.dumps(payload)),
                height=1320, scrolling=True)