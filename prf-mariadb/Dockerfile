FROM mariadb:latest

# Copy initialization script (shell script reads env vars at runtime)
COPY init.sh /docker-entrypoint-initdb.d/01-init.sh

# Ensure init script has proper permissions
RUN chmod 755 /docker-entrypoint-initdb.d/01-init.sh
