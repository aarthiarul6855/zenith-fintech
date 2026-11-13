# Use the OS trust store for outbound TLS (e.g. the Groq API) so development behind a
# corporate/antivirus TLS proxy works. Safe no-op in a standard production environment.
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.database import Base, engine
from app.routers import auth, transactions, ai_engines
from app.routers.data import loans_router, goals_router
from app.services.limiter import limiter


# Create all tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Zenith Fintech API",
    description="AI-powered Smart Financial Wellness Platform",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Rate limiting (slowapi): register the shared limiter and its 429 handler.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — allow React dev server AND your live Vercel site
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", 
        "http://localhost:5173",
        "https://zenith-fintech.vercel.app"  # <--- ADD THIS EXACT LINK
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router)
app.include_router(transactions.router)
app.include_router(loans_router)
app.include_router(goals_router)
app.include_router(ai_engines.router)


@app.get("/", tags=["Health"])
def health_check():
    return {
        "status": "ok",
        "app": "Zenith Fintech",
        "version": "1.0.0",
        "engines": [
            "salary-allocation",
            "what-if-simulation",
            "stress-index",
            "goal-probability",
            "lifestyle-inflation",
            "debt-optimization",
            "cash-flow-forecast",
        ],
    }
