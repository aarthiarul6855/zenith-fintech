"""
Engine 8: Cash-Flow Forecaster

Forward-looking projection of monthly cash flow and running balance, built entirely
from the user's transaction history — no additional user input required.

Method (adapts to available history):
  - >= 4 months of data: Holt-Winters exponential smoothing (statsmodels) on the
    monthly expense series to capture trend.
  - < 4 months: a robust trend-adjusted moving-average fallback (seasonal/trend
    models need more data, so we degrade gracefully instead of failing).

Loan EMIs are added as fixed monthly obligations on top of the forecast. Confidence
bands (P10 / expected / P90) are derived from historical volatility, widening over the
horizon like a random walk.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List

import numpy as np

try:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    _HAS_SM = True
except Exception:  # pragma: no cover - statsmodels is a declared dependency
    _HAS_SM = False

# z-score for an ~80% band (P10–P90).
_Z = 1.2816


def _monthly_series(transactions: list):
    """Return (sorted_months, income_by_month, expense_by_month_excl_emi)."""
    income = defaultdict(float)
    expense = defaultdict(float)  # excludes 'emi' category to avoid double-counting loans
    for t in transactions:
        if not t.date:
            continue
        key = t.date.strftime("%Y-%m")
        ttype = getattr(t.type, "value", t.type)
        cat = getattr(t.category, "value", t.category)
        if ttype == "income":
            income[key] += t.amount
        elif ttype == "expense":
            if cat != "emi":
                expense[key] += t.amount
    months = sorted(set(list(income.keys()) + list(expense.keys())))
    return months, income, expense


def _future_labels(n: int) -> List[str]:
    """Generate the next n calendar-month labels like 'Jan 27' starting next month."""
    now = datetime.utcnow()
    y, m = now.year, now.month
    labels = []
    for _ in range(n):
        m += 1
        if m > 12:
            m = 1
            y += 1
        labels.append(datetime(y, m, 1).strftime("%b %y"))
    return labels


def _forecast_expenses(series: List[float], horizon: int):
    """Forecast future monthly base expenses. Returns (values, method_label)."""
    arr = np.array(series, dtype=float)
    # Holt-Winters when we have enough history.
    if _HAS_SM and len(arr) >= 4:
        try:
            model = ExponentialSmoothing(
                arr, trend="add", seasonal=None, initialization_method="estimated"
            ).fit()
            fc = np.asarray(model.forecast(horizon), dtype=float)
            fc = np.clip(fc, 0, None)
            if np.all(np.isfinite(fc)):
                return fc.tolist(), "Holt-Winters exponential smoothing"
        except Exception:
            pass

    # Fallback: recent-weighted average nudged by a gentle linear trend.
    if len(arr) == 0:
        return [0.0] * horizon, "No expense history"
    recent = arr[-3:] if len(arr) >= 3 else arr
    base = float(np.average(recent, weights=np.arange(1, len(recent) + 1)))
    if len(arr) >= 2:
        slope = float((arr[-1] - arr[0]) / (len(arr) - 1))
    else:
        slope = 0.0
    slope = max(min(slope, base * 0.15), -base * 0.15)  # cap drift at ±15%/mo
    vals = [max(0.0, base + slope * (i + 1)) for i in range(horizon)]
    label = "Trend-adjusted moving average (limited history)" if len(arr) < 4 else "Trend-adjusted moving average"
    return vals, label


def run(monthly_income: float, transactions: list, total_emi: float, months: int = 6) -> Dict[str, Any]:
    months = max(1, min(int(months or 6), 12))
    hist_months, income_m, expense_m = _monthly_series(transactions)
    n_hist = len(hist_months)

    # Income forecast: steady baseline from profile income or the recent income average.
    income_values = [income_m[k] for k in hist_months if income_m.get(k)]
    avg_income = float(np.mean(income_values)) if income_values else 0.0
    proj_income = float(monthly_income) if monthly_income else avg_income
    if proj_income <= 0:
        proj_income = avg_income

    # Expense forecast from the (emi-excluded) monthly series.
    expense_series = [expense_m.get(k, 0.0) for k in hist_months]
    exp_forecast, method = _forecast_expenses(expense_series, months)

    # Starting balance estimate = cumulative recorded income − expenses (clearly an estimate).
    total_income_all = sum(income_m.values())
    total_expense_all = sum(expense_m.values())
    emi_history = 0.0
    for t in transactions:
        cat = getattr(t.category, "value", t.category)
        ttype = getattr(t.type, "value", t.type)
        if ttype == "expense" and cat == "emi":
            emi_history += t.amount
    start_balance = total_income_all - total_expense_all - emi_history

    # Historical volatility of monthly net for confidence bands.
    nets = [income_m.get(k, 0.0) - expense_m.get(k, 0.0) for k in hist_months]
    if len(nets) >= 2:
        volatility = float(np.std(nets))
    else:
        volatility = abs(proj_income) * 0.15  # rough prior when history is thin

    labels = _future_labels(months)
    projected = []
    balance = start_balance
    negative_months = []
    nets_future = []
    for i in range(months):
        exp = round(exp_forecast[i] + float(total_emi), 2)
        net = round(proj_income - exp, 2)
        balance = round(balance + net, 2)
        band = float(_Z * volatility * np.sqrt(i + 1))
        lower = round(balance - band, 2)
        upper = round(balance + band, 2)
        projected.append({
            "month": labels[i],
            "income": round(proj_income, 2),
            "expenses": exp,
            "net": net,
            "balance": balance,
            "lower": lower,
            "upper": upper,
        })
        nets_future.append(net)
        if balance < 0:
            negative_months.append(labels[i])

    lowest = min((p["balance"] for p in projected), default=start_balance)
    avg_surplus = round(float(np.mean(nets_future)), 2) if nets_future else 0.0

    # Verdict + recommendations.
    if negative_months:
        rating = "at_risk"
        summary = f"Your balance is projected to go negative in {len(negative_months)} of the next {months} months. Act before {negative_months[0]}."
    elif avg_surplus <= 0:
        rating = "tight"
        summary = f"You're projected to roughly break even (avg {avg_surplus:,.0f}/mo). Little buffer for surprises."
    elif lowest < proj_income:
        rating = "stable"
        summary = f"Cash flow looks stable with an average surplus of {avg_surplus:,.0f}/mo, though your buffer stays modest."
    else:
        rating = "healthy"
        summary = f"Healthy outlook — averaging {avg_surplus:,.0f}/mo surplus and a growing buffer over the next {months} months."

    recommendations = []
    if negative_months:
        recommendations.append(f"Build a buffer before {negative_months[0]} — trim discretionary spend or defer large outflows.")
    if total_emi > 0 and proj_income > 0 and (total_emi / proj_income) > 0.4:
        recommendations.append(f"EMIs are {total_emi / proj_income * 100:.0f}% of monthly income — consider the Debt Optimizer to reduce interest.")
    if avg_surplus > 0:
        recommendations.append(f"You could redirect ~{avg_surplus * 0.5:,.0f}/mo of surplus into savings or investments.")
    if n_hist < 4:
        recommendations.append("Add more transaction history for a sharper forecast — this projection uses limited data.")

    return {
        "method": method,
        "months_of_history": n_hist,
        "horizon_months": months,
        "starting_balance_estimate": round(start_balance, 2),
        "projected_income_monthly": round(proj_income, 2),
        "total_emi_monthly": round(float(total_emi), 2),
        "avg_monthly_surplus": avg_surplus,
        "lowest_projected_balance": round(lowest, 2),
        "negative_months": negative_months,
        "projected_months": projected,
        "verdict": {"rating": rating, "summary": summary},
        "recommendations": recommendations,
        "limited_history": n_hist < 4,
    }
