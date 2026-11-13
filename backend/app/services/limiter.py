"""Shared rate limiter (slowapi). In-memory by default — fine for a single instance."""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
