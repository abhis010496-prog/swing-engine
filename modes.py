"""
STRATEGY MODES
==============
Four ways of screening the same universe, tuned for a 2-month hold.

Each mode changes what qualifies, how far the exit sits, and what target to
aim for. A large-cap and a small-cap need different rules — a 12% target is
ambitious for one and modest for the other.

Imported by dashboard.py.
"""

# ------------------------------------------------------------------
# The four profiles
# ------------------------------------------------------------------

PROFILES = {
    "Best overall": dict(
        blurb="Every size. Ranked purely on strength, with a floor on how "
              "much the stock moves so it can actually reach a target in "
              "two months.",
        mcap_min=1_000, mcap_max=None,
        turnover_min=8, atr_min=1.8, atr_max=8.0,
        stop_mult=2.0, target_pct=13, hold_days=60, max_positions=6,
        risk_note="Mixed. Read each one on its merits.",
    ),
    "Steady — large companies": dict(
        blurb="Big, heavily traded companies. Slower moves, smaller targets, "
              "but the business is unlikely to disappear and you can always "
              "get out.",
        mcap_min=50_000, mcap_max=None,
        turnover_min=50, atr_min=1.2, atr_max=4.0,
        stop_mult=2.2, target_pct=9, hold_days=60, max_positions=5,
        risk_note="Lower. Gaps are rare, liquidity is deep, information is "
                  "widely available.",
    ),
    "Balanced — mid-size companies": dict(
        blurb="Established but still growing. The sweet spot for a two-month "
              "hold — enough movement to reach a real target, enough "
              "liquidity to exit.",
        mcap_min=15_000, mcap_max=50_000,
        turnover_min=20, atr_min=1.6, atr_max=5.5,
        stop_mult=2.0, target_pct=13, hold_days=60, max_positions=6,
        risk_note="Moderate. Can fall hard on bad results, but rarely "
                  "untradeable.",
    ),
    "Aggressive — small & volatile": dict(
        blurb="Small companies that move fast. Bigger targets, bigger "
              "drawdowns, and the real danger is being unable to sell when "
              "everyone wants out at once.",
        mcap_min=1_000, mcap_max=15_000,
        turnover_min=10, atr_min=2.5, atr_max=9.0,
        stop_mult=1.8, target_pct=22, hold_days=60, max_positions=4,
        risk_note="High. Expect several losers for every large winner. "
                  "Position size should be smaller here, not bigger.",
    ),
}


# ------------------------------------------------------------------
# Entry quality — the checks I'd argue matter most
# ------------------------------------------------------------------

def extension_check(price, ma50):
    """
    How far above its 50-day average has the stock already run?

    This is the single most useful filter that most screens omit. A stock
    30% above its average has already made the move you're hoping to catch.
    Buying there means your exit sits a long way below, and any pause turns
    into a painful drawdown. Strong trends pull back to the average
    regularly — waiting for that is usually the better entry.
    """
    if not ma50 or ma50 <= 0:
        return None, "unknown"
    ext = (price / ma50 - 1) * 100
    if ext > 25:
        return ext, "far"        # too extended — poor entry
    if ext > 15:
        return ext, "stretched"  # workable but not ideal
    return ext, "ok"


def qualifies(s, p):
    """
    Return (bool, list_of_reasons_it_failed).
    's' is a scored stock dict, 'p' is a profile.
    """
    fails = []

    if not s.get("trend"):
        fails.append("not in an uptrend")
    if (s.get("rs") or 0) <= 0:
        fails.append("not beating the index")
    if s.get("from_high", -99) <= -15:
        fails.append("too far below its yearly high")

    mc = s.get("mcap")
    if p["mcap_min"] is not None:
        if mc is None:
            fails.append("company size unknown")
        elif mc < p["mcap_min"]:
            fails.append("smaller than this mode allows")
    if p["mcap_max"] is not None and mc is not None and mc > p["mcap_max"]:
        fails.append("larger than this mode allows")

    if s.get("turnover", 0) < p["turnover_min"]:
        fails.append(f"trades under ₹{p['turnover_min']}cr a day — hard to exit")

    atr_pct = s.get("atr_pct", 0)
    if atr_pct < p["atr_min"]:
        fails.append(f"moves too little ({atr_pct:.1f}% a day) to reach "
                     f"{p['target_pct']}% in two months")
    if atr_pct > p["atr_max"]:
        fails.append(f"moves too wildly ({atr_pct:.1f}% a day) to hold safely")

    _, ext_state = extension_check(s.get("price"), s.get("ma50"))
    if ext_state == "far":
        fails.append("already run too far above its average — poor entry here")

    return (len(fails) == 0), fails


def plan(s, p, budget):
    """Work out the trade: shares, exit level, target, expected outcomes."""
    price = s["price"]
    stop = price - p["stop_mult"] * s["atr"]
    target = price * (1 + p["target_pct"] / 100)

    stop_pct = (1 - stop / price) * 100
    shares = int(budget / price) if price > 0 else 0
    cost = shares * price
    loss = shares * (price - stop)
    gain = shares * (target - price)
    ratio = (target - price) / (price - stop) if price > stop else 0

    return dict(shares=shares, cost=cost, stop=stop, stop_pct=stop_pct,
                target=target, target_pct=p["target_pct"],
                loss=loss, gain=gain, ratio=ratio, hold_days=p["hold_days"])


def concentration_warning(picks):
    """
    Flag when the shortlist is really one bet wearing several hats.

    Five stocks from one sector is not five positions — it's one position
    with extra steps. When that sector turns, they all fall together, and
    the diversification you thought you had turns out to be imaginary.
    """
    if len(picks) < 3:
        return None
    counts = {}
    for r in picks:
        sec = r.get("sector") or "Unknown"
        counts[sec] = counts.get(sec, 0) + 1
    worst = max(counts.items(), key=lambda x: x[1])
    if worst[1] >= max(3, len(picks) * 0.5):
        return (f"{worst[1]} of your {len(picks)} picks are in {worst[0]}. "
                f"That is really one bet, not {worst[1]}. If that sector turns, "
                f"they will all fall together.")
    return None


def regime_advice(healthy, breadth_pct, profile_name):
    """How much to commit given market conditions."""
    if healthy and breadth_pct > 55:
        return ("good", "Conditions are supportive. Normal position sizes are "
                        "reasonable.")
    if healthy:
        return ("mixed", f"The index is holding up but only {breadth_pct:.0f}% of "
                         f"stocks are rising — the strength is narrow. Consider "
                         f"half-sized positions.")
    if "Aggressive" in profile_name:
        return ("bad", "The market is falling and you have the highest-risk mode "
                       "selected. Small companies fall hardest in weak markets. "
                       "This is the worst combination — consider waiting.")
    return ("bad", "The market is falling. Most stocks drop with it regardless of "
                   "how good they look. Either wait, or buy much smaller than "
                   "usual.")