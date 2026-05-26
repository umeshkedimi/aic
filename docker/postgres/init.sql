-- PostgreSQL initialization script
-- This runs on first container start only

-- Enable useful extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Create application user if not exists (handled by POSTGRES_USER env var)
-- Grant necessary permissions
GRANT ALL PRIVILEGES ON DATABASE aic TO aic;

-- Log successful initialization
DO $$
BEGIN
    RAISE NOTICE 'AIC database initialized successfully';
END $$;
