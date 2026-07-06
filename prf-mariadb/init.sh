#!/bin/bash
# ==============================================================================
# POLARI RESEARCH FRAMEWORK - MARIADB INITIALIZATION
# ==============================================================================
# Standalone DB for the PRF-only deployment. Currently provisions:
#   - keycloak database + `kc` user (used by prf-keycloak)
#
# Future additions (per the user's plan to consolidate MariaDB + Redis):
#   - polari application database for object-store consolidation
#
# Environment variables (set via env_file or docker-compose `environment:`):
#   MARIADB_ROOT_PASSWORD - Root password (REQUIRED by the mariadb image itself)
#   KC_DB_PASSWORD        - Keycloak DB user password (default: kcpassword)
# ==============================================================================

set -e

KC_PASS="${KC_DB_PASSWORD:-kcpassword}"
POLARI_PASS="${POLARI_DB_PASSWORD:-polaripassword}"

echo "[prf-mariadb init.sh] Initializing PRF databases and users..."

mariadb -u root -p"${MARIADB_ROOT_PASSWORD}" <<-EOSQL

-- ==============================================================================
-- KEYCLOAK DATABASE AND USER
-- ==============================================================================
CREATE DATABASE IF NOT EXISTS keycloak
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'kc'@'%' IDENTIFIED BY '${KC_PASS}';
GRANT ALL PRIVILEGES ON keycloak.* TO 'kc'@'%';

-- ==============================================================================
-- POLARI OBJECT DATABASE AND USER (database.type: mariadb backends)
-- The polari user may create per-instance schemas (polari_objects,
-- polari_objects_b, ...) — hence the polari_objects% grant pattern.
-- ==============================================================================
CREATE DATABASE IF NOT EXISTS polari_objects
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'polari'@'%' IDENTIFIED BY '${POLARI_PASS}';
GRANT ALL PRIVILEGES ON \`polari_objects%\`.* TO 'polari'@'%';

-- ==============================================================================
-- APPLY PRIVILEGES
-- ==============================================================================
FLUSH PRIVILEGES;

EOSQL

echo "[prf-mariadb init.sh] Database initialization complete."
