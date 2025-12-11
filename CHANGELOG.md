# Changelog

All notable changes to this project will be documented in this file.

## 2025-12-11 (Update 2)

### Changed
- **Replaced 1m window with latest_price**: The RuneLite API doesn't have a `/1m` endpoint
- **Database Migration**: Expanded `window` column from VARCHAR(2) to VARCHAR(20)
- **Valid Windows**: Now `'latest_price'` and `'5m'` (was `'1m'` and `'5m'`)

### Technical Details
- `latest_price` uses `/latest` endpoint which returns instant trade prices (`high`/`low`)
- Volume data is NULL for `latest_price` (endpoint doesn't provide volume)
- Files modified: models, runelite_api.py, price_service.py, scheduler, API routes

## 2025-12-11

### Added
- **Phase 6 - API Routes**:
  - Custom exception hierarchy (`app/core/exceptions.py`) with `OSRSArbitrageError` base class
  - Item service with CRUD operations
  - Price service for snapshot management
  - Arbitrage service for profit calculations with GE tax formula
  - API dependencies with timing-safe API key verification
  - Items routes: list, add (admin), delete (admin), price history
  - Arbitrage routes: top opportunities, history
  - Admin routes: manual price refresh trigger
  - Consistent error response format: `{"detail": "message", "error_code": "CODE"}`

- **Phase 7 - Background Scheduler**:
  - APScheduler with AsyncIOScheduler for background price polling
  - Runs every 5 minutes (configurable via `POLL_INTERVAL_MINUTES`)
  - Initial price fetch on startup
  - Stores price snapshots even when values are null
  - Consecutive failure tracking (5 failures triggers CRITICAL log)
  - Scheduler status endpoint for health monitoring

- **Phase 8 - FastAPI Application**:
  - `main.py` with modern lifespan context manager pattern
  - CORS middleware configured from settings
  - Global exception handler for custom exceptions
  - Health endpoint with scheduler status (healthy/degraded/critical)
  - Swagger UI at `/docs`, ReDoc at `/redoc`

## 2025-12-10

### Added
- **Phase 1-5 Complete**: Full service layer implemented
- Complete project structure with Docker configuration
- SQLAlchemy models: TrackedItem, PriceSnapshot, ArbitrageSnapshot
- Alembic configured for async database migrations
- Pydantic schemas for API validation
- RuneLite API client for price fetching
- Seed data utility for initial 5 items (Coal, Iron ore, Iron bar, Mithril ore, Mithril bar)

### Fixed
- CORS_ORIGINS env var format for pydantic list parsing (`["*"]` instead of `*`)
- PostgreSQL port conflict (stopped local instance for Docker)
- SQLAlchemy relationship resolution in seed_data.py

### Added
- Production deployment checklist with security reminders

## 2025-12-09

### Added
- Initial project setup
- Project documentation (README.md and CLAUDE.md)
- Defined project scope and goals
