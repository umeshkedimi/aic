# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in AIC, please report it responsibly:

1. **Do NOT** open a public GitHub issue for security vulnerabilities
2. Email the maintainers directly with details
3. Allow reasonable time for a fix before public disclosure

## Security Best Practices

### Environment Variables

**Never commit secrets to the repository.** All sensitive configuration must be provided via environment variables:

```bash
# Required secrets (set in your environment, NOT in files)
OPENAI_API_KEY=sk-...
AIC_DATABASE_URL=postgresql+asyncpg://user:password@host:5432/db
AIC_REDIS_URL=redis://:password@host:6379/0
```

### Local Development

The `docker-compose.yml` contains **development-only** placeholder passwords:
- `aic_dev_password` — PostgreSQL dev password
- `admin` — Grafana dev password

These are **NOT** for production use. In production, always:
- Use strong, unique passwords
- Use secrets management (Vault, AWS Secrets Manager, etc.)
- Enable TLS/SSL for all connections

### Production Deployment Checklist

- [ ] All secrets injected via environment variables or secrets manager
- [ ] TLS enabled for API, database, and cache connections
- [ ] API authentication enabled (`AIC_API_KEYS` configured)
- [ ] CORS origins restricted (`AIC_CORS_ORIGINS`)
- [ ] Debug mode disabled (`AIC_DEBUG=false`)
- [ ] Log level set appropriately (`AIC_LOG_LEVEL=INFO` or `WARNING`)
- [ ] Database connection pooling configured for load
- [ ] Rate limiting enabled (via reverse proxy)

### What's Safe in This Repository

- `.env.example` — Template with placeholder values only
- `docker-compose.yml` — Development passwords (intentional for local dev)
- Configuration code — References env vars, no hardcoded secrets

### What Should NEVER Be Committed

- `.env` files (except `.env.example`)
- API keys, tokens, or credentials
- Private keys or certificates
- Database dumps with real data
- Log files containing sensitive information
