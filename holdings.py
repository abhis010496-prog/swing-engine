"""
holdings.py — type any stock you own and see where you stand.

Run it:
    python -m streamlit run holdings.py

It reads market.db but never writes to it.
"""

import os
import datetime as dt
import streamlit as st

from position_engine import analyse, TARGET_PCT, HOLD_MONTHS, STOP_ATR_MULT

DB = "market.db" if os.path.exists("market.db") else "deploy.db"

st.set_page_config(page_title="My holdings", page_icon="•", layout="centered")

st.markdown("""
<style>
  .block-container {max-width: 760px; padding-top: 2.2rem;}
  .verdict {border-radius: 10px; padding: 1.1rem 1.3rem; margin: .4rem 0 1.2rem;}
  .v-good {background:#0e3b2e; border-left:5px solid #26a17b;}
  .v-warn {background:#3d3218; border-left:5px solid #d99a2b;}
  .v-bad  {background:#3d1f1f; border-left:5px solid #d95757;}
  .verdict h2 {margin:0; font-size:1.55rem; color:#fff; letter-spacing:-.01em;}
  .verdict p  {margin:.35rem 0 0; color:#cfd4d8; font-size:.93rem;}
  .row {display:flex; justify-content:space-between; padding:.5rem 0;
        border-bottom:1px solid rgba(255,255,255,.08); font-size:.94rem;}
  .row .lbl {color:#9aa3ab;}
  .row .val {font-variant-numeric:tabular-nums; font-weight:600;}
  .tick {color:#26a17b; font-weight:700;}
  .cross{color:#d95757; font-weight:700;}
  .dash {color:#7b848c; font-weight:700;}
  .note {color:#8b939b; font-size:.83rem; line-height:1.5;}
</style>
""", unsafe_allow_html=True)

st.title("My holdings")
st.caption("Type a stock you own. This reads the same checks the daily screen "
           "uses, and tells you whether the reasons to own it still hold.")

# ------------------------------------------------------------------- input

typed = st.text_input("Stock", placeholder="DABUR", key="sym").strip()

c1, c2, c3 = st.columns(3)
entry_price = c1.number_input("Your buy price (₹)", min_value=0.0,
                              value=0.0, step=1.0, format="%.2f")
qty = c2.number_input("Shares held", min_value=0, value=0, step=1)
entry_date = c3.date_input("Bought on", value=dt.date.today() - dt.timedelta(days=30),
                           max_value=dt.date.today())

if not typed:
    st.info("Enter a stock above to see how your position stands.")
    st.stop()

try:
    r = analyse(DB, typed, entry_price or None, qty or None, entry_date)
except LookupError as e:
    st.error(str(e))
    st.stop()
except Exception as e:
    st.error(f"Could not read {DB}: {e}")
    st.stop()

if not r["found"]:
    if r.get("suggestions"):
        st.warning(f"No stock called **{typed.upper()}**. Did you mean: "
                   + ", ".join(f"`{s}`" for s in r["suggestions"]) + "?")
    else:
        st.warning(r.get("error", f"**{typed.upper()}** is not in the database."))
    st.stop()

m, v, pos = r["metrics"], r["verdict"], r["verdict"]["position"]

# ----------------------------------------------------------------- verdict

st.markdown(f"""
<div class="verdict v-{v['tone']}">
  <h2>{v['call']} — {r['symbol']}</h2>
  <p>Priced at ₹{m['close']:,.2f} on {m['last_date']:%d %b %Y}</p>
</div>
""", unsafe_allow_html=True)

if m["stale_days"] > 5:
    st.warning(f"Prices are {m['stale_days']} days old. Run `build_universe.py` "
               "before trusting this.")


def rows(items):
    html = ""
    for lbl, val in items:
        html += f'<div class="row"><span class="lbl">{lbl}</span>' \
                f'<span class="val">{val}</span></div>'
    st.markdown(html, unsafe_allow_html=True)


# ---------------------------------------------------------------- position

if pos.get("entry"):
    st.subheader("Your position")
    sign = "+" if pos["pnl_pct"] >= 0 else ""
    items = [
        ("Bought at", f"₹{pos['entry']:,.2f}"),
        ("Now", f"₹{m['close']:,.2f}"),
        ("Profit / loss", f"{sign}{pos['pnl_pct']:.1f}%"),
    ]
    if qty:
        items.append(("In rupees", f"{sign}₹{pos['pnl_rs']:,.0f} "
                                   f"on {qty} shares"))
    if "stop" in pos:
        items.append((f"Stop ({STOP_ATR_MULT:.0f}× daily range below entry)",
                      f"₹{pos['stop']:,.2f} · {pos['stop_dist_pct']:+.1f}% away"))
    items.append((f"Target ({TARGET_PCT:.0f}%)",
                  f"₹{pos['target']:,.2f} · {pos['progress']:.0f}% of the way"))
    if "days_held" in pos:
        left = pos["window_left_days"]
        items.append(("Held for", f"{pos['days_held']} days"
                      + (f" · {left} left in the {HOLD_MONTHS:.0f}-month plan"
                         if left >= 0 else " · past the planned window")))
    rows(items)

# ------------------------------------------------------------- the reasons

st.subheader("Why")

if v["exit_reasons"]:
    for x in v["exit_reasons"]:
        st.markdown(f"**·** {x}")
if v["watch_reasons"]:
    for x in v["watch_reasons"]:
        st.markdown(f"**·** {x}")
if v["keep_reasons"] and not v["exit_reasons"]:
    for x in v["keep_reasons"]:
        st.markdown(f"**·** {x}")
if not (v["exit_reasons"] or v["watch_reasons"] or v["keep_reasons"]):
    st.markdown("Nothing has changed for better or worse.")

# ------------------------------------------------------------------ checks

st.subheader("The six checks")
st.caption("The same tests a stock must pass to reach the daily list.")

html = ""
for g in r["gates"]:
    mark = ('<span class="tick">✓</span>' if g["pass"] is True
            else '<span class="cross">✗</span>' if g["pass"] is False
            else '<span class="dash">–</span>')
    html += (f'<div class="row"><span class="lbl">{mark} &nbsp;{g["name"]}</span>'
             f'<span class="val" style="font-weight:400;color:#b6bdc4;">'
             f'{g["detail"]}</span></div>')
st.markdown(html, unsafe_allow_html=True)

if r["vetoes"]:
    st.error("**Disqualified outright:** " + " · ".join(r["vetoes"]))
elif not r["has_fundamentals"]:
    st.caption("No fundamentals table found, so the loss-making, debt and "
               "falling-sales checks were skipped.")

# ------------------------------------------------------------- persistence

p = r["persistence"]
if p and p.get("d30_of"):
    st.subheader("How often it passes")
    rows([("Last 7 days", f"{p['d7']} of {p['d7_of']} days"),
          ("Last 30 days", f"{p['d30']} of {p['d30_of']} days")])
    st.caption("This is what the weekly and monthly lists rank on.")

# ------------------------------------------------------------------- chart

st.subheader("Price")
df = r["prices"].tail(260).copy()
df["50-day average"] = r["prices"]["close"].rolling(50).mean().tail(260)
df["200-day average"] = r["prices"]["close"].rolling(200).mean().tail(260)
st.line_chart(df.set_index("date")[["close", "50-day average", "200-day average"]],
              height=280)

# -------------------------------------------------------------------- note

st.markdown("---")
st.markdown(
    '<p class="note">This reads the present, not the future. It cannot tell '
    'you where the price is going — the holdout test in section 3.1 found no '
    'evidence of that. What it does is flag when the reasons you bought have '
    'quietly stopped being true. Costs of 0.3–0.6% per round trip are not '
    'included anywhere above.</p>',
    unsafe_allow_html=True)