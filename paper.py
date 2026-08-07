"""
PRACTICE TRADING
================
Fake money, real prices.

Fully self-contained: brings its own styling and degrades gracefully if
live.py is missing. Imported by dashboard.py.
"""

import sqlite3
from datetime import datetime

import pandas as pd
import streamlit as st

DB = "market.db"
BENCHMARK = "^NSEI"
STARTING_CASH = 1_000_000

CSS = """
<style>
  @import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,800&family=Inter:wght@400;500;600&display=swap');
  .pt-hero { background:linear-gradient(118deg,#0E8C7A 0%,#33B7A1 100%);
             border-radius:20px; padding:24px 28px; margin-bottom:16px;
             box-shadow:0 14px 34px -18px #0E8C7A; }
  .pt-hero h1 { font-family:'Bricolage Grotesque',sans-serif; font-weight:800;
                font-size:28px; color:#fff; letter-spacing:-.025em; margin:0; }
  .pt-hero p { color:#ffffffcc; font-size:13.5px; margin:5px 0 0; }
  .pt-card { background:#fff; border:1px solid #E6ECF3; border-radius:16px;
             padding:20px 24px; margin-bottom:12px;
             box-shadow:0 1px 2px #101B290A; }
  .pt-name { font-family:'Bricolage Grotesque',sans-serif; font-weight:800;
             font-size:20px; color:#101B29; letter-spacing:-.02em; }
  .pt-sub { color:#6B7C90; font-size:12.5px; margin-top:3px; }
  .pt-big { font-family:'Bricolage Grotesque',sans-serif; font-weight:800;
            font-size:24px; color:#101B29; }
  .pt-lab { font-size:10.5px; letter-spacing:.16em; text-transform:uppercase;
            font-weight:700; color:#0E8C7A; margin-bottom:7px; }
  .pt-up { color:#0B7A55; } .pt-dn { color:#BE3B32; }
  .pt-note { background:#F4F8FB; border-left:3px solid #0E8C7A; border-radius:8px;
             padding:13px 16px; font-size:13.5px; line-height:1.6; color:#38475A; }
</style>
"""


# ------------------------------------------------------------------

def ensure_schema(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS paper_account (
        id INTEGER PRIMARY KEY CHECK (id=1),
        starting_cash REAL, cash REAL, created TEXT);
    CREATE TABLE IF NOT EXISTS paper_positions (
        id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, name TEXT,
        shares INTEGER NOT NULL, buy_price REAL NOT NULL, buy_date TEXT NOT NULL,
        stop_price REAL, plan_days INTEGER, reason TEXT NOT NULL,
        invalidation TEXT NOT NULL, nifty_at_buy REAL,
        status TEXT DEFAULT 'open', sell_price REAL, sell_date TEXT,
        sell_reason TEXT);
    """)
    if conn.execute("SELECT COUNT(*) FROM paper_account").fetchone()[0] == 0:
        conn.execute("INSERT INTO paper_account VALUES (1,?,?,?)",
                     (STARTING_CASH, STARTING_CASH,
                      datetime.now().strftime("%Y-%m-%d")))
    conn.commit()


def last_close(conn, ticker):
    r = conn.execute("SELECT close, date FROM prices_daily WHERE ticker=? "
                     "ORDER BY date DESC LIMIT 1", (ticker,)).fetchone()
    return (float(r[0]), r[1]) if r else (None, None)


def days_between(a, b):
    """Never negative — price data can lag the calendar by a day."""
    d = (datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")).days
    return max(0, d)


def get_live_safe(tickers):
    """Live prices if live.py exists and works. Never raises."""
    try:
        import live
        return live.get_live(tickers) or {}
    except Exception:
        return {}


# ------------------------------------------------------------------

def render(scored_rows):
    st.markdown(CSS, unsafe_allow_html=True)

    try:
        conn = sqlite3.connect(DB)
        ensure_schema(conn)
    except Exception as e:
        st.error(f"Couldn't open the practice account: {e}")
        return

    cash = float(conn.execute("SELECT cash FROM paper_account WHERE id=1")
                 .fetchone()[0])
    opens = conn.execute(
        "SELECT id,ticker,name,shares,buy_price,buy_date,stop_price,plan_days,"
        "reason,invalidation,nifty_at_buy FROM paper_positions "
        "WHERE status='open' ORDER BY buy_date DESC").fetchall()
    nifty_now, price_date = last_close(conn, BENCHMARK)

    st.markdown(f"""<div class='pt-hero'>
      <h1>Practice trading</h1>
      <p>Fake money, real prices — the only way to find out whether the
         shortlist actually works</p></div>""", unsafe_allow_html=True)

    use_live = False
    quotes = {}
    if opens:
        use_live = st.toggle("Use live prices (delayed ~15 min)", value=True)
        if use_live:
            quotes = get_live_safe([o[1] for o in opens])
            if not quotes:
                st.caption("Live prices unavailable — using the last close.")

    # ---- value the portfolio ----
    holdings, live_rows = 0.0, []
    for (pid, tk, nm, sh, bp, bd, stop, pdays, reason, inval, nifty_buy) in opens:
        cp, _ = last_close(conn, tk)
        if tk in quotes:
            cp = quotes[tk]["price"]
        if cp is None:
            cp = bp
        val = sh * cp
        holdings += val
        held = days_between(bd, price_date or datetime.now().strftime("%Y-%m-%d"))
        nif = ((nifty_now / nifty_buy - 1) * 100
               if nifty_buy and nifty_now else None)
        live_rows.append(dict(id=pid, ticker=tk, name=nm, shares=sh, buy=bp,
                              now=cp, value=val, pnl=val - sh * bp,
                              pnl_pct=(cp / bp - 1) * 100, held=held, stop=stop,
                              plan_days=pdays, reason=reason, inval=inval,
                              nifty_pct=nif, buy_date=bd))

    total = cash + holdings
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Account value", f"₹{total:,.0f}",
              f"{(total/STARTING_CASH-1)*100:+.2f}%")
    c2.metric("Cash left", f"₹{cash:,.0f}")
    c3.metric("Invested", f"₹{holdings:,.0f}",
              f"{len(opens)} position{'' if len(opens)==1 else 's'}")
    c4.metric("Profit / loss", f"₹{total-STARTING_CASH:,.0f}")
    st.caption(f"Valued at {'live prices' if quotes else f'closes from {price_date}'}. "
               f"Started with ₹{STARTING_CASH:,.0f}.")

    t1, t2, t3 = st.tabs(["Your positions", "Buy something", "Results"])

    # ================= POSITIONS =================
    with t1:
        if not live_rows:
            st.info("No positions yet. Open the **Buy something** tab to start.")
        for r in live_rows:
            st.markdown("<div class='pt-card'>", unsafe_allow_html=True)
            a, b, c = st.columns([2.3, 1.2, 1.2])
            a.markdown(f"<div class='pt-name'>{r['name']}</div>"
                       f"<div class='pt-sub'>{r['shares']:,} shares · bought "
                       f"₹{r['buy']:,.1f} on {r['buy_date']} · {r['held']} days ago"
                       f"</div>", unsafe_allow_html=True)
            b.markdown(f"<div class='pt-big'>₹{r['now']:,.1f}</div>"
                       f"<div class='pt-sub'>worth ₹{r['value']:,.0f}</div>",
                       unsafe_allow_html=True)
            cls = "pt-up" if r["pnl"] >= 0 else "pt-dn"
            c.markdown(f"<div class='pt-big {cls}'>{r['pnl']:+,.0f}</div>"
                       f"<div class='{cls}'>{r['pnl_pct']:+.2f}%</div>",
                       unsafe_allow_html=True)

            if r["nifty_pct"] is not None:
                d = r["pnl_pct"] - r["nifty_pct"]
                st.markdown(
                    f"<div class='pt-note'>The Nifty moved {r['nifty_pct']:+.2f}% "
                    f"over the same period, so you are "
                    f"<b>{'ahead of' if d>0 else 'behind'} the index by "
                    f"{abs(d):.2f}%</b>. The same money in an index fund would be "
                    f"₹{r['shares']*r['buy']*(1+r['nifty_pct']/100):,.0f}.</div>",
                    unsafe_allow_html=True)

            if r["stop"] and r["now"] <= r["stop"]:
                st.warning("Below your exit level. You said you'd sell here — "
                           "do it, or write down why you changed your mind.")
            if r["plan_days"] and r["held"] >= r["plan_days"]:
                st.warning(f"{r['held']} days held, past your {r['plan_days']}-day "
                           f"plan. Time to decide.")

            with st.expander("Why you bought it"):
                st.write(f"**Your reason:** {r['reason']}")
                st.write(f"**You'd be wrong if:** {r['inval']}")
                if r["stop"]:
                    st.write(f"**Exit level:** ₹{r['stop']:,.1f}")

            s1, s2 = st.columns([3, 1])
            why = s1.selectbox("If selling, why?",
                               ["Hit my exit level", "Reached my target",
                                "The reason I bought is no longer true",
                                "Ran out of time", "Changed my mind"],
                               key=f"w{r['id']}")
            if s2.button("Sell", key=f"s{r['id']}", use_container_width=True):
                conn.execute("UPDATE paper_positions SET status='closed', "
                             "sell_price=?, sell_date=?, sell_reason=? WHERE id=?",
                             (r["now"], datetime.now().strftime("%Y-%m-%d"),
                              why, r["id"]))
                conn.execute("UPDATE paper_account SET cash=cash+? WHERE id=1",
                             (r["shares"] * r["now"],))
                conn.commit()
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

    # ================= BUY =================
    with t2:
        held = {r["ticker"] for r in live_rows}

        # Same four categories as the shortlist, so practice matches what you'd
        # actually be choosing from.
        try:
            import modes
            cat_names = ["Any category"] + list(modes.PROFILES.keys())
        except Exception:
            modes, cat_names = None, ["Any category"]

        chosen = st.selectbox("Which category do you want to buy from?", cat_names)

        pool = [r for r in scored_rows if r["ticker"] not in held]
        if modes is not None and chosen != "Any category":
            P = modes.PROFILES[chosen]
            opts = [r for r in pool if modes.qualifies(r, P)[0]]
            st.caption(f"{len(opts)} companies fit **{chosen}** · "
                       f"exit set at {P['stop_mult']}x the stock's normal daily move")
        else:
            opts = [r for r in pool if r.get("strong")] or pool[:40]
            st.caption(f"{len(opts)} companies available")

        if not opts:
            st.info("Nothing fits that category right now. Try another one.")
        else:
            labels = {r["short"]: f"{r['name']} · ₹{r['price']:,.0f}" for r in opts}
            pick = st.selectbox("Which company?", list(labels.keys()),
                                format_func=lambda k: labels[k])
            stock = next(r for r in opts if r["short"] == pick)

            top = int(min(cash, 300000))
            amount = st.slider("How much to put in? (₹)", 10000,
                               max(top, 20000), min(50000, max(top, 10000)), 5000)
            plan_days = st.slider("Plan to hold for (days)", 10, 90, 60, 5)

            # Use the same price source the portfolio is valued at. Buying
            # at a stale score-table price and valuing at a fresh close (or a
            # live quote) invents a profit the moment you buy.
            live_now = get_live_safe([stock["ticker"]])
            close_now, _cd = last_close(conn, stock["ticker"])
            buy_price = (live_now[stock["ticker"]]["price"]
                         if stock["ticker"] in live_now
                         else (close_now or stock["price"]))

            if abs(buy_price - stock["price"]) / max(stock["price"], 1) > 0.005:
                st.caption(f"Buying at ₹{buy_price:,.1f} (current) rather than "
                           f"₹{stock['price']:,.1f} (last scoring run).")

            shares = int(amount / buy_price)
            cost = shares * buy_price
            mult = (modes.PROFILES[chosen]["stop_mult"]
                    if (modes is not None and chosen != "Any category")
                    else 2.5)
            stop = buy_price - mult * stock["atr"]
            risk = shares * (buy_price - stop)

            st.markdown(
                f"<div class='pt-note'><b>{shares:,} shares at "
                f"₹{buy_price:,.1f} = ₹{cost:,.0f}</b><br>"
                f"Exit level ₹{stop:,.1f} "
                f"({(1-stop/buy_price)*100:.1f}% below). "
                f"If it goes wrong you lose about ₹{risk:,.0f}.</div>",
                unsafe_allow_html=True)

            st.markdown("**Write these down before you buy.** In two months "
                        "you'll want to know what you were thinking.")
            reason = st.text_area("Why are you buying this?", height=80,
                                  placeholder="e.g. Won a large order and broke "
                                              "to a new high on heavy volume.")
            inval = st.text_area("What would prove you wrong?", height=80,
                                 placeholder="e.g. If it closes below ₹X, or the "
                                             "next results show no revenue impact.")

            if st.button("Place practice order", type="primary"):
                if len(reason.strip()) < 15:
                    st.error("Write a real reason — 'looks good' won't help you later.")
                elif len(inval.strip()) < 15:
                    st.error("Write what would prove you wrong. If you can't name "
                             "it, you don't have a thesis yet.")
                elif shares < 1:
                    st.error("That amount buys less than one share.")
                elif cost > cash:
                    st.error("Not enough cash in the practice account.")
                else:
                    conn.execute(
                        "INSERT INTO paper_positions (ticker,name,shares,buy_price,"
                        "buy_date,stop_price,plan_days,reason,invalidation,"
                        "nifty_at_buy) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (stock["ticker"], stock["name"], shares, buy_price,
                         datetime.now().strftime("%Y-%m-%d"), stop, plan_days,
                         (f"[{chosen}] " if chosen != "Any category" else "")
                         + reason.strip(), inval.strip(), nifty_now))
                    conn.execute("UPDATE paper_account SET cash=cash-? WHERE id=1",
                                 (cost,))
                    conn.commit()
                    st.success(f"Bought {shares:,} shares of {stock['name']}.")
                    st.rerun()

    # ================= RESULTS =================
    with t3:
        closed = conn.execute(
            "SELECT name,shares,buy_price,buy_date,sell_price,sell_date,"
            "sell_reason,nifty_at_buy FROM paper_positions WHERE status='closed' "
            "ORDER BY sell_date DESC").fetchall()

        if not closed:
            st.info("No completed trades yet. Results appear once you sell something.")
        else:
            recs = []
            for nm, sh, bp, bd, sp, sd, why, nb in closed:
                pct = (sp / bp - 1) * 100
                nif = (nifty_now / nb - 1) * 100 if nb and nifty_now else None
                recs.append(dict(name=nm, days=days_between(bd, sd),
                                 pnl=sh * (sp - bp), pct=pct, why=why,
                                 bought=bd, sold=sd, buy=bp, sell=sp,
                                 vs=(pct - nif) if nif is not None else None))

            wins = [r for r in recs if r["pnl"] > 0]
            beat = [r for r in recs if r["vs"] is not None and r["vs"] > 0]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Trades", len(recs))
            m2.metric("Win rate", f"{len(wins)/len(recs)*100:.0f}%")
            m3.metric("Total profit", f"₹{sum(r['pnl'] for r in recs):,.0f}")
            m4.metric("Beat the index", f"{len(beat)}/{len(recs)}")

            losses = [r for r in recs if r["pnl"] <= 0]
            aw = sum(r["pct"] for r in wins) / len(wins) if wins else 0
            al = sum(r["pct"] for r in losses) / len(losses) if losses else 0
            verdict = ("You need about 30 trades before this means anything. "
                       "Below that it is mostly luck."
                       if len(recs) < 30 else
                       "You have enough trades for this to carry some weight. "
                       "If most lose to the index, the honest conclusion is that "
                       "the screen isn't adding value yet.")
            st.markdown(
                f"<div class='pt-note'><b>What your record says.</b><br>"
                f"Winners averaged {aw:.1f}%, losers cost {abs(al):.1f}%. "
                f"You held {sum(r['days'] for r in recs)/len(recs):.0f} days on "
                f"average. {len(beat)} of {len(recs)} beat simply buying the "
                f"Nifty.<br><br>{verdict}</div>", unsafe_allow_html=True)

            st.dataframe(pd.DataFrame([{
                "Company": r["name"], "Bought": r["bought"], "Sold": r["sold"],
                "Days": r["days"], "Buy ₹": round(r["buy"], 1),
                "Sell ₹": round(r["sell"], 1), "Profit ₹": round(r["pnl"]),
                "Return %": round(r["pct"], 2),
                "vs Nifty %": round(r["vs"], 2) if r["vs"] is not None else None,
                "Why sold": r["why"],
            } for r in recs]), use_container_width=True, hide_index=True)

        with st.expander("Reset the practice account"):
            st.caption("Deletes every practice trade. Price data is untouched.")
            if st.button("Delete all practice trades"):
                conn.execute("DELETE FROM paper_positions")
                conn.execute("UPDATE paper_account SET cash=? WHERE id=1",
                             (STARTING_CASH,))
                conn.commit()
                st.rerun()

    conn.close()