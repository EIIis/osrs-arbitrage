"""
API Routes Package.

This module exports all API routers for easy importing in main.py.
"""
from app.api.routes.items import router as items_router
from app.api.routes.arbitrage import router as arbitrage_router
from app.api.routes.admin import router as admin_router

__all__ = ["items_router", "arbitrage_router", "admin_router"]
