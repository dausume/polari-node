FROM nginx:latest

# Create certificate directories
RUN mkdir -p /etc/nginx/certs/ca

# CA certificate (may not exist in all environments - use wildcard copy)
COPY certs/ca/prf-ca.crt /etc/nginx/certs/ca/prf-ca.crt

# Proxy certificate and key
COPY certs/prf-proxy.crt /etc/nginx/certs/prf-proxy.crt
COPY certs/prf-proxy.key /etc/nginx/certs/prf-proxy.key

# Nginx configuration - use ARG to select dev or prod
# For staging/prod, this is overridden at runtime via volume mount
ARG NGINX_CONF=nginx.prod.conf
COPY ${NGINX_CONF} /etc/nginx/nginx.conf

# Expose ports:
#   80   - HTTP redirect to HTTPS
#   443  - HTTPS (subdomain routing for frontend, API, MinIO)
EXPOSE 80
EXPOSE 443

CMD ["nginx", "-g", "daemon off;"]
