# prf-file-store — THE SUITE'S OBJECT STORE, on SeaweedFS (D-fs-1, his call 2026-09-25, after `minio/minio` vanished
# from Docker Hub — the repository is gone, so the MinIO-based image can no longer build anywhere).
#
#   SeaweedFS 4.47   Apache-2.0   chrislusf/seaweedfs, pinned by tag; one process serves master + volume + filer +
#                                 the S3 gateway (`weed server`), sized for a node (the volume limit is a knob)
#
# The contract every consumer already has is KEPT: S3 on :9000 (path-style, SigV4), a web UI on :9001 (the filer's
# file browser), the root credentials from MINIO_ROOT_USER / MINIO_ROOT_PASSWORD (the `pol security` knobs —
# POLARI_MINIO_ROOT_USER/_PASS — write them), buckets created by the applications. New: the store trusts the suite's
# Keycloak realm as an OIDC provider (STS AssumeRoleWithWebIdentity + Bearer tokens on S3) when the issuer is set
# — see entrypoint.sh for the knobs and README.md for the role mapping.
#
#   docker build -t prf-file-store:staging .
#   docker run --rm -p 9000:9000 -p 9001:9001 -e MINIO_ROOT_USER=admin -e MINIO_ROOT_PASSWORD=secret prf-file-store:staging
FROM chrislusf/seaweedfs:4.47
COPY entrypoint.sh /polari-entrypoint.sh
RUN chmod +x /polari-entrypoint.sh && mkdir -p /etc/seaweedfs && chown seaweed:seaweed /etc/seaweedfs
VOLUME /data
EXPOSE 9000 9001
# the image's own entrypoint keeps its job (volume permissions, FIPS mode, dropping to `seaweed`); ours renders
# the S3 / IAM configuration from the environment first, then hands over
ENTRYPOINT ["/polari-entrypoint.sh"]
CMD ["server"]
