# Polari Research Framework - Environment Configuration Guide

This document describes the tiered environment variable system used across the PRF project.

## Overview

The PRF uses a **three-tier configuration system** that provides flexibility for different deployment scenarios:

| Tier | Name | When Applied | How to Change |
|------|------|--------------|---------------|
| **Tier 1** | Build-time | During Docker image build | Rebuild image |
| **Tier 2** | Startup-time | At container startup | Edit `.env` or pass env vars |
| **Tier 3** | Runtime | While app is running | Via UI or API |

**Priority**: Tier 3 > Tier 2 > Tier 1 (higher tiers override lower tiers)

## Configuration Files

### Root Level Files

| File | Tier | Purpose |
|------|------|---------|
| `.env.defaults` | 1 | Base defaults baked into images |
| `.env` | 2 | Startup-time overrides |
| `docker-compose.yml` | 2 | Container orchestration with env var support |

### Frontend (Angular)

| File | Tier | Purpose |
|------|------|---------|
| `src/environments/environment.ts` | 1 | Bare metal/local development |
| `src/environments/environment-dev.ts` | 1 | Docker development build |
| `src/environments/environment.prod.ts` | 1 | Production build |
| `src/assets/runtime-config.json` | 2 | Startup-time overrides |
| `RuntimeConfigService` | 3 | Runtime configuration service |
| `PolariService` | 3 | Runtime connection settings |

### Backend (Python)

| File | Tier | Purpose |
|------|------|---------|
| `config.yaml` | 1 | Base YAML configuration |
| Environment variables | 2 | Startup-time overrides |
| `config_loader.py` | 2/3 | Configuration loader with runtime support |

## Environment Variables Reference

### Backend Configuration

| Variable | Tier | Default | Description |
|----------|------|---------|-------------|
| `BACKEND_URL` | 2 | `localhost` | Backend server hostname |
| `BACKEND_HTTP_PORT` | 2 | `3000` | HTTP port |
| `BACKEND_HTTPS_PORT` | 2 | `2096` | HTTPS port (Cloudflare-compatible) |
| `DEPLOY_ENV` | 2 | `development` | Environment name |
| `LOG_LEVEL` | 2/3 | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `CORS_ENABLED` | 2/3 | `true` | Enable CORS |

### Frontend Configuration

| Variable | Tier | Default | Description |
|----------|------|---------|-------------|
| `FRONTEND_URL` | 2 | `localhost` | Frontend server hostname |
| `FRONTEND_HTTP_PORT` | 2 | `4200` | HTTP port |
| `FRONTEND_HTTPS_PORT` | 2 | `2087` | HTTPS port (Cloudflare-compatible) |
| `BACKEND_PREFER_HTTPS` | 2/3 | `false` | Use HTTPS for backend connections |

### SSL Configuration

| Variable | Tier | Default | Description |
|----------|------|---------|-------------|
| `SSL_ENABLED` | 2 | `true` | Enable SSL/HTTPS |
| `SSL_CERT_PATH` | 2 | `/app/certs/prf-proxy.crt` | SSL certificate path |
| `SSL_KEY_PATH` | 2 | `/app/certs/prf-proxy.key` | SSL private key path |

### Connection Settings (Runtime Configurable)

| Variable | Tier | Default | Description |
|----------|------|---------|-------------|
| `CONNECTION_RETRY_INTERVAL` | 2/3 | `3000` | Retry interval in milliseconds |
| `CONNECTION_MAX_RETRY_TIME` | 2/3 | `60000` | Max retry time in milliseconds |
| `CONNECTION_TIMEOUT` | 2/3 | `30000` | Request timeout in milliseconds |

## Usage Examples

### 1. Default Docker Development

```bash
# Uses defaults from .env.defaults and .env
docker-compose up
```

### 2. Override at Startup (Tier 2)

```bash
# Override backend URL
BACKEND_URL=192.168.1.100 docker-compose up

# Override multiple settings
BACKEND_URL=api.example.com BACKEND_HTTPS_PORT=443 docker-compose up
```

### 3. Edit .env File (Tier 2)

```bash
# Edit .env to change defaults
vim .env

# Then start containers
docker-compose up
```

### 4. Runtime Configuration via Frontend UI (Tier 3)

The Polari Configuration component allows changing:
- Backend URL and port
- Protocol (HTTP/HTTPS)
- Connection retry settings

Changes take effect immediately without restart.

### 5. Runtime Configuration via Python API (Tier 3)

```python
from config_loader import config

# Change configuration at runtime
config.set_runtime('backend.port', 3001)
config.set_runtime('logging.level', 'DEBUG')

# Check which tier a value comes from
print(config.get_config_tier('backend.port'))  # 'tier3_runtime'

# View all tier values
print(config.get_all_tiers('backend.port'))
```

## Port Reference

### Cloudflare-Compatible HTTPS Ports

These ports are supported by Cloudflare's proxy:
- 443, 2053, 2083, 2087, 2096, 8443

### PRF Port Allocation

| Service | HTTP | HTTPS |
|---------|------|-------|
| Frontend | 4200 | 2087 |
| Backend | 3000 | 2096 |

### PSC Port Allocation (for reference)

| Service | HTTP | HTTPS |
|---------|------|-------|
| Frontend | 4200 | 2053 |
| Backend | 8080 | 2083 |

## Best Practices

1. **Never commit secrets** - Use environment variables for passwords/keys
2. **Use Tier 2 for deployment-specific settings** - Different URLs per environment
3. **Use Tier 3 for user preferences** - Connection settings that users may want to change
4. **Keep Tier 1 as sensible defaults** - Should work out of the box for development

## Troubleshooting

### Check Effective Configuration

**Frontend (Browser Console):**
```javascript
// In Angular app
runtimeConfigService.getCurrentConfig()
```

**Backend (Python):**
```python
from config_loader import config
print(config.get_all_tiers('backend.port'))
```

### Configuration Not Taking Effect

1. Check tier priority (Tier 3 > Tier 2 > Tier 1)
2. For Tier 2, restart the container
3. For Tier 1, rebuild the image: `docker-compose build --no-cache`

### SSL Certificate Issues

```bash
# Generate certificates
./generate-prf-certs.sh dev

# Verify certificates exist
ls -la prf-proxy/certs/
```
