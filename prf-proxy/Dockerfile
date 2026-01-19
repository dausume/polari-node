FROM nginx:latest

# Create certificate directories
RUN mkdir -p /etc/nginx/certs/ca

# CA certificate
COPY certs/ca/prf-ca.crt /etc/nginx/certs/ca/prf-ca.crt

# Proxy certificate and key
COPY certs/prf-proxy.crt /etc/nginx/certs/prf-proxy.crt
COPY certs/prf-proxy.key /etc/nginx/certs/prf-proxy.key

# Nginx configuration - use ARG to select dev or prod
ARG NGINX_CONF=nginx.prod.conf
COPY ${NGINX_CONF} /etc/nginx/nginx.conf

# Expose Cloudflare-compatible HTTPS ports
EXPOSE 80
EXPOSE 443
EXPOSE 2087
EXPOSE 2096

CMD ["nginx", "-g", "daemon off;"]
