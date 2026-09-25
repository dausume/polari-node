# prf-file-store — the suite's object store, on SeaweedFS

**D-fs-1 (his call, 2026-09-25): SeaweedFS replaces MinIO.** `minio/minio` disappeared from Docker Hub (the whole
repository), so the MinIO-based image can no longer build anywhere. SeaweedFS is Apache-2.0, released about weekly
for twelve years, and carries an OIDC/STS door natively. This directory is the ONE definition; the node stack
(`docker-compose.staging-nip.yml`), the suite (`docker-compose.yml`, `docker-compose.prod.yml`), the standalone PoC
(`pol-file-store/`) and PSC's test stack all build it from here.

## The contract is kept

| | before (MinIO) | now (SeaweedFS `weed server`) |
|---|---|---|
| S3 API | `:9000`, path-style, SigV4 | `:9000`, path-style, SigV4 — the backend's `minio-py` client, presigned URLs, PSC's Java client all unchanged |
| web UI | `:9001` MinIO console (had a login) | `:9001` the filer's file browser — **no login of its own and not Keycloak-capable, so it is NOT published**: internal to the docker network only (the proxy has no `files.` host, the LAN sees no :9001); the store is reached only through S3 |
| root credentials | `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | the same knobs (`pol security` writes `POLARI_MINIO_ROOT_USER/_PASS`) — rendered into `/etc/seaweedfs/s3.json` as the Admin identity at start; **no default secret: the store refuses to start without them** |
| buckets | made by the applications | the same; a directory under the filer's `/buckets/` IS a bucket (that is how `pol shell publish` uploads: `POST http://prf-file-store:9001/buckets/<bucket>/<key>`) |
| data | `/data` volume | `/data` volume (`FILE_STORE_VOLUME_MB`, default 256, sizes the volume files — a node knob) |

## The Keycloak door (new)

When `POLARI_KEYCLOAK_ISSUER_URI` (or `FILE_STORE_OIDC_ISSUER`) is in the environment the realm is registered as an
OIDC provider (`/etc/seaweedfs/iam.json`, rendered by `entrypoint.sh`; the suite CA at `/etc/seaweedfs/ca.crt` lets the
store fetch the realm's JWKS over TLS). **One door, STS in claim mode:**

    POST http://prf-file-store:9000/  Action=AssumeRoleWithWebIdentity  WebIdentityToken=<realm access token>
        (RoleArn omitted, or the sentinel arn:aws:iam:::role/sts-claim-based)
    → temporary AccessKeyId / SecretAccessKey / SessionToken whose policies ARE the token's realm roles

Policies are named after the realm roles and the token's top-level `roles` claim is the `policyClaim`:

| realm role (token `roles`) | S3 |
|---|---|
| `polari-admin` | `s3:*` |
| `polari-developer` | list, get, put, delete objects, create/delete buckets |
| `polari-user` | list, get, put, delete objects |
| `polari-viewer`, `default-roles-polari` (every realm user) | list, get |

The realm side (both realm imports, `pol-keycloak/realm-imports` and `prf-keycloak/realm-imports`): the clients
`polari-frontend`, `polari-shell`, `polari-backend` carry two mappers — realm roles into a top-level `roles` claim, and
the audience `polari-file-store` — and a bearer-only client `polari-file-store` documents that audience.

**Why claim mode only** (verified while building, 2026-09-25): SeaweedFS trust policies can only see `oidc:iss/sub/aud`,
so with concrete roles defined ANY realm token could `AssumeRole PolariAdmin`; locking those roles then broke
Bearer-on-S3, which goes through the same trust gate. So there are no concrete roles and no Bearer door: a caller can
never pick a role it does not hold — the realm is the sole authority. Proven against the live realm with the backend's
service-account token (roles `polari-developer`): claim-mode STS → create bucket / put / list / delete OK; a concrete
`RoleArn` → `role not found`; Bearer → 403.

## Knobs

`MINIO_ROOT_USER` `MINIO_ROOT_PASSWORD` (required) · `FILE_STORE_VOLUME_MB` (256) · `FILE_STORE_OIDC_ISSUER` (default
`POLARI_KEYCLOAK_ISSUER_URI`) · `FILE_STORE_OIDC_CLIENT_ID` (`polari-file-store`, the audience) ·
`FILE_STORE_OIDC_ROLES_CLAIM` (`roles`) · `FILE_STORE_TLS_CA` (`/etc/seaweedfs/ca.crt` when present) ·
`FILE_STORE_STS_KEY` (base64, ≥ 32 bytes; default generated once into `/data/.sts-signing-key`) ·
`FILE_STORE_STS_TOKEN_DURATION` (1h) · `FILE_STORE_STS_MAX_SESSION` (12h).

    docker build -t prf-file-store:staging .
    docker run --rm -p 9000:9000 -p 9001:9001 -e MINIO_ROOT_USER=admin -e MINIO_ROOT_PASSWORD=secret prf-file-store:staging
    # healthcheck: curl -s -o /dev/null http://localhost:9000/ && curl -sf http://localhost:9333/cluster/status

Licence: SeaweedFS Apache-2.0 (`AI-Notes/evaluations/FILE_STORE_LICENSE_GATE.md`); everything here is a separate
process the suite talks S3 to. Pin bumps: change the `FROM` tag, rebuild, re-run the three proofs above.
