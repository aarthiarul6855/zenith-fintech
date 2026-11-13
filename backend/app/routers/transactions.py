import io
import math
from datetime import datetime

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models.models import User, Transaction, TransactionType, TransactionCategory
from app.schemas.schemas import TransactionCreate, TransactionOut
from app.services.auth_service import get_current_user
from app.engines import transaction_categorizer  # ← add this import

router = APIRouter(prefix="/transactions", tags=["Transactions"])

# Accepted header names (lower-cased) for each logical field in an uploaded CSV.
_DATE_COLS = ["date", "txn_date", "transaction date", "value date", "posting date"]
_DESC_COLS = ["description", "narration", "particulars", "details", "remarks", "memo"]
_AMOUNT_COLS = ["amount", "amt", "transaction amount"]
_DEBIT_COLS = ["debit", "withdrawal", "withdrawal amt", "dr", "debit amount"]
_CREDIT_COLS = ["credit", "deposit", "deposit amt", "cr", "credit amount"]
_TYPE_COLS = ["type", "txn type", "transaction type"]


def _pick(columns, candidates):
    """Return the first dataframe column matching any candidate header (case-insensitive)."""
    lut = {str(c).strip().lower(): c for c in columns}
    for cand in candidates:
        if cand in lut:
            return lut[cand]
    return None


def _to_float(val):
    if val is None:
        return None
    s = str(val).strip().replace(",", "").replace("₹", "").replace("$", "")
    if s == "" or s.lower() in ("nan", "none"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _row_key(date_str: str, amount: float, description: str) -> str:
    return f"{date_str}|{round(amount, 2)}|{description.strip().lower()}"


@router.post("/", response_model=TransactionOut, status_code=201)
def create_transaction(
    payload: TransactionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = payload.model_dump()

    # Auto-categorize from description
    if data.get("description"):
        result = transaction_categorizer.classify(data["description"])
        data["category"] = result["category"]

    t = Transaction(user_id=user.id, **data)
    db.add(t)

    # ← NEW: update monthly income if salary transaction
    if data.get("category") == "salary" and data.get("type") == "income":
        user.monthly_income = data["amount"]
        db.add(user)

    db.commit()
    db.refresh(t)
    return t


@router.get("/", response_model=List[TransactionOut])
def list_transactions(
    limit: int = 100,
    offset: int = 0,
    type: Optional[str] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(Transaction).filter(Transaction.user_id == user.id)
    if type:
        q = q.filter(Transaction.type == type)
    return q.order_by(Transaction.date.desc()).offset(offset).limit(limit).all()


@router.put("/{transaction_id}", response_model=TransactionOut)
def update_transaction(
    transaction_id: int,
    payload: TransactionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = db.query(Transaction).filter(
        Transaction.id == transaction_id, Transaction.user_id == user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    data = payload.model_dump()
    # Re-categorize from the (possibly updated) description.
    if data.get("description"):
        data["category"] = transaction_categorizer.classify(data["description"])["category"]

    for key, value in data.items():
        setattr(t, key, value)
    db.commit()
    db.refresh(t)
    return t


@router.post("/import-csv")
def import_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Bulk-import transactions from an uploaded bank-statement CSV.

    Flexibly maps common bank column names, infers income/expense, auto-categorizes
    each row from its description, and skips rows that duplicate existing transactions
    (same date + amount + description).
    """
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file.")

    raw = file.file.read()
    try:
        df = pd.read_csv(io.BytesIO(raw))
    except Exception:
        raise HTTPException(status_code=400, detail="Could not parse the CSV file.")

    if df.empty:
        raise HTTPException(status_code=400, detail="The CSV file has no rows.")

    cols = list(df.columns)
    date_col = _pick(cols, _DATE_COLS)
    desc_col = _pick(cols, _DESC_COLS)
    amount_col = _pick(cols, _AMOUNT_COLS)
    debit_col = _pick(cols, _DEBIT_COLS)
    credit_col = _pick(cols, _CREDIT_COLS)
    type_col = _pick(cols, _TYPE_COLS)

    if desc_col is None:
        raise HTTPException(status_code=400, detail="No description column found (expected one of: description, narration, particulars, details).")
    if amount_col is None and debit_col is None and credit_col is None:
        raise HTTPException(status_code=400, detail="No amount column found (expected 'amount', or 'debit'/'credit').")

    # Existing rows for this user → dedupe set.
    existing = db.query(Transaction).filter(Transaction.user_id == user.id).all()
    seen = set()
    for t in existing:
        d = t.date.strftime("%Y-%m-%d") if t.date else ""
        seen.add(_row_key(d, t.amount or 0.0, t.description or ""))

    imported = 0
    skipped_duplicates = 0
    errors = 0
    to_add = []

    for _, row in df.iterrows():
        try:
            description = str(row[desc_col]).strip()
            if not description or description.lower() == "nan":
                errors += 1
                continue

            # Determine amount + type.
            txn_type = None
            amount = None
            if amount_col is not None:
                amount = _to_float(row[amount_col])
                if type_col is not None and not pd.isna(row.get(type_col)):
                    tv = str(row[type_col]).strip().lower()
                    if tv in ("income", "credit", "cr", "deposit"):
                        txn_type = TransactionType.income
                    elif tv in ("expense", "debit", "dr", "withdrawal"):
                        txn_type = TransactionType.expense
                if amount is not None and txn_type is None:
                    # No explicit type → infer from sign (negative = expense).
                    txn_type = TransactionType.expense if amount < 0 else TransactionType.income
                if amount is not None:
                    amount = abs(amount)
            else:
                debit = _to_float(row[debit_col]) if debit_col is not None else None
                credit = _to_float(row[credit_col]) if credit_col is not None else None
                if credit and credit > 0:
                    amount, txn_type = credit, TransactionType.income
                elif debit and debit > 0:
                    amount, txn_type = debit, TransactionType.expense

            if not amount or amount <= 0 or math.isnan(amount):
                errors += 1
                continue

            # Parse date (default: today if missing/unparseable).
            dt = datetime.utcnow()
            if date_col is not None:
                parsed = pd.to_datetime(row[date_col], errors="coerce", dayfirst=True)
                if not pd.isna(parsed):
                    dt = parsed.to_pydatetime()

            # Auto-categorize, falling back to 'other' if the label isn't a known enum.
            cat_label = transaction_categorizer.classify(description).get("category", "other")
            try:
                category = TransactionCategory(cat_label)
            except ValueError:
                category = TransactionCategory.other

            key = _row_key(dt.strftime("%Y-%m-%d"), amount, description)
            if key in seen:
                skipped_duplicates += 1
                continue
            seen.add(key)

            to_add.append(Transaction(
                user_id=user.id, amount=amount, type=txn_type,
                category=category, description=description, date=dt,
            ))
            imported += 1
        except Exception:
            errors += 1
            continue

    if to_add:
        db.add_all(to_add)
        db.commit()

    return {
        "imported": imported,
        "skipped_duplicates": skipped_duplicates,
        "errors": errors,
        "total_rows": int(len(df)),
    }


@router.delete("/{transaction_id}", status_code=204)
def delete_transaction(
    transaction_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = db.query(Transaction).filter(
        Transaction.id == transaction_id, Transaction.user_id == user.id
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    db.delete(t)
    db.commit()
