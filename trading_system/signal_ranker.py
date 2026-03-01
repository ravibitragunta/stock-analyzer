"""
signal_ranker.py — Scores and ranks trading signals based on quality matrix.
"""

MINIMUM_QUALITY_SCORE = 60

def score_signal(signal: dict, gate: dict) -> int:
    """
    Score a signal from 0 to 100 based on technical and macro factors.
    """
    score = 0
    sig_type = signal.get("signal_type", "LONG")

    # 1. Compression days (max 25)
    comp_days = signal.get("compression_days", 0)
    if comp_days > 10:
        score += 25
    elif 7 <= comp_days <= 10:
        score += 20
    elif 4 <= comp_days <= 6:
        score += 10

    # 2. Volume multiple (max 20)
    vol_mult = signal.get("_vol_mult", 0.0)
    if vol_mult > 3.0:
        score += 20
    elif 2.0 < vol_mult <= 3.0:
        score += 17
    elif 1.5 <= vol_mult <= 2.0:
        score += 10

    # 3. Sector above EMA (max 15)
    sector_ema = signal.get("sector_above_ema", None)
    if sector_ema is True:
        score += 15
    elif sector_ema is None:
        score += 7
    # False = 0pts

    # 4. VIX zone (max 15)
    vix = gate.get("vix_level", 15.0)
    if sig_type == "LONG":
        if 13.0 <= vix <= 16.0:
            score += 15
        elif 16.0 < vix <= 18.0:
            score += 8
    else:  # SHORT
        if 18.0 <= vix <= 22.0:
            score += 15
        else:
            score += 5

    # 5. Advance-Decline (max 15)
    ad = gate.get("advance_decline", 0.5)
    if sig_type == "LONG":
        if ad > 0.6:
            score += 15
        else:
            score += 5
    else:  # SHORT
        if ad < 0.4:
            score += 15
        else:
            score += 5

    # 6. AI score (max 10)
    ai = signal.get("ai_score", None)
    if ai is not None:
        if ai >= 0.75:
            score += 10
        elif ai >= 0.5:
            score += 6
        elif ai < 0.3:
            score -= 5

    # Clamp between 0 and 100
    return max(0, min(100, score))


def rank_signals(signals: list[dict], gate: dict) -> list[dict]:
    """
    Score each signal, append quality_score and tradeable fields, 
    and return the list sorted descending by score.
    """
    for sig in signals:
        qs = score_signal(sig, gate)
        sig["quality_score"] = qs
        sig["tradeable"] = qs >= MINIMUM_QUALITY_SCORE

    return sorted(signals, key=lambda x: x.get("quality_score", 0), reverse=True)
