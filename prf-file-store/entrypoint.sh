#!/bin/sh
# prf-file-store entrypoint — render SeaweedFS's S3 + IAM configuration from the environment, then start the
# single-process server through the image's own entrypoint (which fixes /data ownership and drops privileges).
#
# Knobs (the MINIO_* names are kept on purpose: every compose file, `pol security`, the backend and PSC already
# carry them — the store changed, the contract did not):
#   MINIO_ROOT_USER / MINIO_ROOT_PASSWORD   the root S3 identity (Admin) — REQUIRED (no default secret, ever)
#   FILE_STORE_VOLUME_MB                    volume file size limit (default 256; small nodes, small volumes)
#   FILE_STORE_OIDC_ISSUER                  the OIDC issuer (default: POLARI_KEYCLOAK_ISSUER_URI when present) — set = the
#                                           realm is trusted: STS AssumeRoleWithWebIdentity + Bearer tokens on S3
#   FILE_STORE_OIDC_CLIENT_ID               the audience the tokens must carry (default polari-file-store)
#   FILE_STORE_OIDC_ROLES_CLAIM             the top-level token claim holding realm roles (default `roles`)
#   FILE_STORE_TLS_CA                       a CA file to trust for the issuer's TLS (default /etc/seaweedfs/ca.crt if present)
#   FILE_STORE_STS_KEY                      base64 STS signing key (>= 32 bytes); default: generated once, kept in /data
# Authorization comes from the REALM ROLES in the token's top-level `roles` claim (a Keycloak realm-roles mapper):
#   polari-admin → s3:*  ·  polari-developer → list/get/put/delete + create buckets  ·  polari-user → list/get/put/delete
#   polari-viewer / default-roles-polari (every realm user) → list/get
# ONE door: STS AssumeRoleWithWebIdentity in CLAIM mode (RoleArn omitted / arn:aws:iam:::role/sts-claim-based) →
# temporary S3 keys carrying exactly the token's role policies; any S3 client (SigV4 + session token) then works.
# There are no concrete roles to assume and no Bearer-on-S3 (both would let a caller pick a role — see below).
set -eu
CONF=/etc/seaweedfs
mkdir -p "$CONF"
U="${MINIO_ROOT_USER:-${FILE_STORE_ROOT_USER:-}}"
P="${MINIO_ROOT_PASSWORD:-${FILE_STORE_ROOT_PASS:-}}"
if [ -z "$U" ] || [ -z "$P" ]; then
    echo "[prf-file-store] refusing to start: MINIO_ROOT_USER / MINIO_ROOT_PASSWORD are not set (pol security setup writes them)" >&2
    exit 1
fi
esc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
# ---- s3.json: the root identity (the basic credential file, -s3.config)
cat > "$CONF/s3.json" <<JSON
{"identities": [{"name": "$(esc "$U")", "credentials": [{"accessKey": "$(esc "$U")", "secretKey": "$(esc "$P")"}],
                 "actions": ["Admin", "Read", "Write", "List", "Tagging"]}]}
JSON
chmod 600 "$CONF/s3.json"
# ---- iam.json: the realm as an OIDC provider (the advanced IAM file, -s3.iam.config) — only when an issuer is named
ISS="${FILE_STORE_OIDC_ISSUER:-${POLARI_KEYCLOAK_ISSUER_URI:-}}"
IAM_ARG=""
if [ -n "$ISS" ]; then
    CID="${FILE_STORE_OIDC_CLIENT_ID:-polari-file-store}"
    CLAIM="${FILE_STORE_OIDC_ROLES_CLAIM:-roles}"
    CA="${FILE_STORE_TLS_CA:-/etc/seaweedfs/ca.crt}"
    CA_LINE=""; [ -f "$CA" ] && CA_LINE="\"tlsCaCert\": \"$CA\","
    KEY="${FILE_STORE_STS_KEY:-}"
    if [ -z "$KEY" ]; then
        # generated once per data volume: tokens issued before a restart stay valid after it
        if [ ! -f /data/.sts-signing-key ]; then
            head -c 32 /dev/urandom | base64 | tr -d '\n' > /data/.sts-signing-key; chmod 600 /data/.sts-signing-key
        fi
        KEY="$(cat /data/.sts-signing-key)"
    fi
    # Policies are NAMED AFTER THE REALM ROLES and the token's top-level `roles` claim is the policyClaim: STS runs in
    # CLAIM mode only (RoleArn omitted or the sentinel arn:aws:iam:::role/sts-claim-based) — the token's roles ARE the
    # policies, the realm is the sole authority, there is no role to pick. No concrete roles and no roleMapping are
    # defined ON PURPOSE: a concrete role is assumable by any realm token (trust policies can only see iss/sub/aud), and
    # the Bearer-on-S3 door needs concrete roles — verified 2026-09-25: with roles present a viewer's token could
    # AssumeRole PolariAdmin; locking the roles then broke Bearer, which goes through the same gate. So: STS claim mode.
    cat > "$CONF/iam.json" <<JSON
{
  "sts": {"tokenDuration": "${FILE_STORE_STS_TOKEN_DURATION:-1h}", "maxSessionLength": "${FILE_STORE_STS_MAX_SESSION:-12h}", "issuer": "prf-file-store-sts", "signingKey": "$KEY"},
  "providers": [{
    "name": "keycloak", "type": "oidc", "enabled": true,
    "config": {
      "issuer": "$ISS", "clientId": "$CID",
      "jwksUri": "$ISS/protocol/openid-connect/certs", "userInfoUri": "$ISS/protocol/openid-connect/userinfo",
      "scopes": ["openid", "profile", "email", "roles"], $CA_LINE
      "policyClaim": "$CLAIM"
    }
  }],
  "policies": [
    {"name": "default-roles-polari", "document": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["s3:List*", "s3:Get*"], "Resource": ["*"]}]}},
    {"name": "polari-viewer", "document": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["s3:List*", "s3:Get*"], "Resource": ["*"]}]}},
    {"name": "polari-user", "document": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["s3:List*", "s3:Get*", "s3:Put*", "s3:DeleteObject", "s3:AbortMultipartUpload"], "Resource": ["*"]}]}},
    {"name": "polari-developer", "document": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["s3:List*", "s3:Get*", "s3:Put*", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:CreateBucket", "s3:DeleteBucket"], "Resource": ["*"]}]}},
    {"name": "polari-admin", "document": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["s3:*"], "Resource": ["*"]}]}}
  ]
}
JSON
    chmod 600 "$CONF/iam.json"
    IAM_ARG="-s3.iam.config=$CONF/iam.json"
    echo "[prf-file-store] OIDC provider: $ISS (audience $CID, roles claim '$CLAIM'$( [ -n "$CA_LINE" ] && echo ", CA $CA" ))"
else
    echo "[prf-file-store] no OIDC issuer named (FILE_STORE_OIDC_ISSUER / POLARI_KEYCLOAK_ISSUER_URI): access keys only"
fi
chown -R seaweed:seaweed "$CONF" 2>/dev/null || true
[ -f /data/.sts-signing-key ] && chown seaweed:seaweed /data/.sts-signing-key 2>/dev/null || true
if [ "${1:-server}" = "server" ]; then
    shift || true
    # one process: master + volume + filer + S3 (:9000) + the filer UI (:9001); everything advertises loopback because
    # it all lives here; binds on every interface so the network can reach :9000/:9001
    exec /entrypoint.sh server -dir=/data -ip=127.0.0.1 -ip.bind=0.0.0.0 \
        -master.volumeSizeLimitMB="${FILE_STORE_VOLUME_MB:-256}" -volume.max=0 \
        -filer -filer.port=9001 \
        -s3 -s3.port=9000 -s3.config="$CONF/s3.json" -s3.allowEmptyFolder=true $IAM_ARG "$@"
fi
exec /entrypoint.sh "$@"
