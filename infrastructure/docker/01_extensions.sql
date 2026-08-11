-- =============================================================================
-- PostgreSQL initialization script
-- Runs once on first container boot
-- =============================================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enable pgcrypto for server-side encryption helpers
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Enable pg_trgm for fast ILIKE / full-text search on text fields
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Enable btree_gin for composite GIN indexes
CREATE EXTENSION IF NOT EXISTS "btree_gin";