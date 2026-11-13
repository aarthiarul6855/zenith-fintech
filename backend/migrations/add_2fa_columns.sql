-- Migration: add 2FA columns to the users table.
-- Idempotent — safe to run more than once. Run once per environment (local + production).
--   psql "<YOUR_DATABASE_URL>" -f backend/migrations/add_2fa_columns.sql

ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_secret VARCHAR;
ALTER TABLE users ADD COLUMN IF NOT EXISTS totp_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS backup_codes TEXT;
