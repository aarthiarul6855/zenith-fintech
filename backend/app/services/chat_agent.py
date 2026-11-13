"""
Ask Zenith — a lightweight tool-calling chat agent over the user's own financial data.

Uses the Groq SDK directly (OpenAI-compatible tool-calling). The LLM never invents
numbers: it calls our engines as tools, and we feed back the real computed results.
Provider-agnostic — swap GROQ_BASE_URL/MODEL to use any OpenAI-style endpoint.
"""
import json
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.config import settings
from app.models.models import User, Transaction, Loan, Goal
from app.engines import (
    salary_allocation, whatif_simulation, stress_index,
    goal_predictor, lifestyle_inflation, debt_optimizer, cash_flow_forecaster,
)

try:
    from groq import Groq
    _HAS_GROQ = True
except Exception:
    _HAS_GROQ = False


SYSTEM_PROMPT = (
    "You are Zenith, a concise personal-finance assistant. The user's money is in Indian Rupees (INR, ₹). "
    "Answer ONLY using the tools provided — never invent or estimate numbers yourself. "
    "Call the most relevant tool(s), then explain the result in 2-4 short sentences in plain language. "
    "If the user asks something unrelated to their finances, politely steer back. "
    "If a tool reports missing data (e.g. no goals or loans), tell the user what to add."
)

MAX_TOOL_ROUNDS = 5


def is_configured() -> bool:
    return bool(_HAS_GROQ and settings.GROQ_API_KEY)


# ── Data helpers ──────────────────────────────────────────────────────────────

def _avg_monthly_expenses(transactions: list) -> float:
    exp = [t for t in transactions if getattr(t.type, "value", t.type) == "expense"]
    months = len(set(t.date.strftime("%Y-%m") for t in exp if t.date)) or 1
    return sum(t.amount for t in exp) / months


# ── Tool implementations (each returns a JSON-serializable dict) ───────────────

def _financial_summary(db, user, **_):
    txns = db.query(Transaction).filter(Transaction.user_id == user.id).all()
    income = sum(t.amount for t in txns if getattr(t.type, "value", t.type) == "income")
    expenses = sum(t.amount for t in txns if getattr(t.type, "value", t.type) == "expense")
    cats: Dict[str, float] = {}
    for t in txns:
        if getattr(t.type, "value", t.type) == "expense":
            c = getattr(t.category, "value", t.category)
            cats[c] = cats.get(c, 0) + t.amount
    top = sorted(cats.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "monthly_income": user.monthly_income,
        "total_income_recorded": round(income, 2),
        "total_expenses_recorded": round(expenses, 2),
        "net": round(income - expenses, 2),
        "top_expense_categories": [{"category": k, "amount": round(v, 2)} for k, v in top],
        "transaction_count": len(txns),
    }


def _stress_index(db, user, **_):
    txns = db.query(Transaction).filter(Transaction.user_id == user.id).all()
    loans = db.query(Loan).filter(Loan.user_id == user.id).all()
    return stress_index.run(user.monthly_income, txns, sum(l.monthly_emi for l in loans))


def _cash_flow_forecast(db, user, months: int = 6, **_):
    txns = db.query(Transaction).filter(Transaction.user_id == user.id).all()
    loans = db.query(Loan).filter(Loan.user_id == user.id).all()
    if not txns:
        return {"error": "No transactions yet — add or import some to forecast."}
    return cash_flow_forecaster.run(user.monthly_income, txns, sum(l.monthly_emi for l in loans), months)


def _goal_probability(db, user, goal_name: str = "", **_):
    goals = db.query(Goal).filter(Goal.user_id == user.id).all()
    if not goals:
        return {"error": "No goals yet. Add a goal first."}
    match = next((g for g in goals if goal_name.lower() in g.name.lower()), None) if goal_name else None
    if not match:
        return {"error": "Goal not found.", "available_goals": [g.name for g in goals]}
    txns = db.query(Transaction).filter(Transaction.user_id == user.id).all()
    return goal_predictor.run(match, user.monthly_income, txns)


def _what_if(db, user, description: str = "a change", monthly_impact: float = 0.0,
             duration_months: int = 12, one_time_cost: float = 0.0, **_):
    txns = db.query(Transaction).filter(Transaction.user_id == user.id).all()
    loans = db.query(Loan).filter(Loan.user_id == user.id).all()
    income = user.monthly_income
    avg_exp = _avg_monthly_expenses(txns)
    total_emi = sum(l.monthly_emi for l in loans)
    scenarios = [{
        "description": description, "monthly_impact": monthly_impact,
        "duration_months": duration_months, "one_time_cost": one_time_cost,
    }]
    return whatif_simulation.run(income, avg_exp + total_emi, (income - avg_exp) * 3, scenarios, 12)


def _salary_allocation(db, user, **_):
    txns = db.query(Transaction).filter(Transaction.user_id == user.id).all()
    loans = db.query(Loan).filter(Loan.user_id == user.id).all()
    if not user.monthly_income:
        return {"error": "Monthly income not set on the profile."}
    return salary_allocation.run(user.monthly_income, txns, loans)


def _debt_optimization(db, user, strategy: str = "avalanche", **_):
    txns = db.query(Transaction).filter(Transaction.user_id == user.id).all()
    loans = db.query(Loan).filter(Loan.user_id == user.id).all()
    if not loans:
        return {"error": "No loans recorded. Add loans first."}
    surplus = user.monthly_income - _avg_monthly_expenses(txns)
    return debt_optimizer.run(loans, surplus, strategy if strategy in ("avalanche", "snowball") else "avalanche")


def _lifestyle_inflation(db, user, **_):
    txns = db.query(Transaction).filter(Transaction.user_id == user.id).all()
    return lifestyle_inflation.run(user.monthly_income, 0.01, txns)


def _list_goals(db, user, **_):
    goals = db.query(Goal).filter(Goal.user_id == user.id).all()
    return {"goals": [{"name": g.name, "target": g.target_amount, "current": g.current_amount,
                       "monthly_contribution": g.monthly_contribution, "deadline_months": g.deadline_months} for g in goals]}


def _list_loans(db, user, **_):
    loans = db.query(Loan).filter(Loan.user_id == user.id).all()
    return {"loans": [{"name": l.name, "outstanding": l.outstanding, "interest_rate": l.interest_rate,
                       "monthly_emi": l.monthly_emi, "months_remaining": l.tenure_months_remaining} for l in loans]}


# ── Tool registry: name → (python fn, JSON schema for the model) ──────────────

TOOLS = {
    "financial_summary": (_financial_summary, {"type": "object", "properties": {}}),
    "stress_index": (_stress_index, {"type": "object", "properties": {}}),
    "cash_flow_forecast": (_cash_flow_forecast, {"type": "object", "properties": {
        "months": {"type": "integer", "description": "Months to project (1-12)."}}}),
    "goal_probability": (_goal_probability, {"type": "object", "properties": {
        "goal_name": {"type": "string", "description": "Name (or part) of the goal."}}, }),
    "what_if": (_what_if, {"type": "object", "properties": {
        "description": {"type": "string"},
        "monthly_impact": {"type": "number", "description": "Extra monthly outflow (negative = saving)."},
        "duration_months": {"type": "integer"},
        "one_time_cost": {"type": "number"}}, }),
    "salary_allocation": (_salary_allocation, {"type": "object", "properties": {}}),
    "debt_optimization": (_debt_optimization, {"type": "object", "properties": {
        "strategy": {"type": "string", "enum": ["avalanche", "snowball"]}}}),
    "lifestyle_inflation": (_lifestyle_inflation, {"type": "object", "properties": {}}),
    "list_goals": (_list_goals, {"type": "object", "properties": {}}),
    "list_loans": (_list_loans, {"type": "object", "properties": {}}),
}

_TOOL_DESCRIPTIONS = {
    "financial_summary": "Income, expenses, net, and top spending categories.",
    "stress_index": "The user's financial stress score (0-100) with signals.",
    "cash_flow_forecast": "Projected balance for the next N months with risk alerts.",
    "goal_probability": "Monte Carlo probability of hitting a named savings goal.",
    "what_if": "Simulate the balance impact of a hypothetical financial decision.",
    "salary_allocation": "Recommended split of monthly income across buckets.",
    "debt_optimization": "Avalanche vs snowball loan payoff plan.",
    "lifestyle_inflation": "Detects spending growing faster than income.",
    "list_goals": "List the user's savings goals.",
    "list_loans": "List the user's loans and EMIs.",
}


def _tool_specs() -> List[dict]:
    return [{
        "type": "function",
        "function": {"name": name, "description": _TOOL_DESCRIPTIONS[name], "parameters": schema},
    } for name, (_, schema) in TOOLS.items()]


def run_chat(question: str, db: Session, user: User) -> Dict[str, Any]:
    """Run one question through the agent loop. Returns {answer, tools_used}."""
    if not is_configured():
        return {
            "answer": "The AI assistant isn't configured yet. Add a free GROQ_API_KEY to the backend .env to enable it.",
            "tools_used": [], "configured": False,
        }

    client = Groq(api_key=settings.GROQ_API_KEY)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    tools_used: List[str] = []

    for _ in range(MAX_TOOL_ROUNDS):
        resp = client.chat.completions.create(
            model=settings.GROQ_MODEL, messages=messages, tools=_tool_specs(),
            tool_choice="auto", temperature=0.3, max_tokens=700,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return {"answer": msg.content or "I couldn't find an answer.", "tools_used": tools_used, "configured": True}

        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]})
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            fn = TOOLS.get(name, (None, None))[0]
            if fn is None:
                result = {"error": f"Unknown tool {name}"}
            else:
                try:
                    result = fn(db, user, **args)
                except Exception as e:
                    result = {"error": str(e)}
            tools_used.append(name)
            messages.append({"role": "tool", "tool_call_id": tc.id, "name": name, "content": json.dumps(result, default=str)})

    return {"answer": "That needed too many steps — try asking something more specific.", "tools_used": tools_used, "configured": True}
