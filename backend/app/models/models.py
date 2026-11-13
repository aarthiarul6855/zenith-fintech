from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Enum, Text, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from app.database import Base


class TransactionType(str, enum.Enum):
    income = "income"
    expense = "expense"


class TransactionCategory(str, enum.Enum):
    salary = "salary"
    freelance = "freelance"
    investment = "investment"
    rent = "rent"
    groceries = "groceries"
    utilities = "utilities"
    entertainment = "entertainment"
    healthcare = "healthcare"
    education = "education"
    transport = "transport"
    dining = "dining"
    shopping = "shopping"
    emi = "emi"
    insurance = "insurance"
    savings = "savings"
    other = "other"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    hashed_password = Column(String, nullable=False)
    monthly_income = Column(Float, default=0.0)
    # Two-factor auth (TOTP). backup_codes holds a JSON list of sha256 hashes.
    totp_secret = Column(String, nullable=True)
    totp_enabled = Column(Boolean, default=False, nullable=False)
    backup_codes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    transactions = relationship("Transaction", back_populates="user")
    loans = relationship("Loan", back_populates="user")
    goals = relationship("Goal", back_populates="user")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Float, nullable=False)
    type = Column(Enum(TransactionType), nullable=False)
    category = Column(Enum(TransactionCategory), nullable=False)
    description = Column(String, default="")
    date = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="transactions")


class Loan(Base):
    __tablename__ = "loans"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    principal = Column(Float, nullable=False)
    outstanding = Column(Float, nullable=False)
    interest_rate = Column(Float, nullable=False)  # annual %
    monthly_emi = Column(Float, nullable=False)
    tenure_months_remaining = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="loans")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String, unique=True, index=True, nullable=False)  # sha256 of the raw token
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    email = Column(String, nullable=True)       # attempted email (useful for failed logins)
    action = Column(String, nullable=False)     # e.g. login_success, login_failed, register, data_change
    detail = Column(String, default="")
    ip = Column(String, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Goal(Base):
    __tablename__ = "goals"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    target_amount = Column(Float, nullable=False)
    current_amount = Column(Float, default=0.0)
    monthly_contribution = Column(Float, default=0.0)
    deadline_months = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="goals")
