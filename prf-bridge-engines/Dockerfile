# prf-bridge-engines - the HOSTED BRIDGE worker (sim-2, AI-Notes/plans/HARDWARE_BENCH_CONVERGENCE_PLAN.md s3): builds the generated Polari Hardware
# Bridge (the Java app grpcbridge generates) and runs it as a detached process beside a twin session (source=twin-tcp), so the LightState row on a
# Polari server follows a simulated board and `light-toggle` reaches it - the web page alone is a complete test. From Debian 13 (trixie) packages on
# the SAME digest-pinned base the board / formal / pcb engines use.
#
#   openjdk-21-jdk-headless   GPL-2.0 with the Classpath Exception   javac + java: the JDK, not the JRE, because the worker BUILDS the jar (mvn needs javac);
#                                                                    the generated pom targets release 17, so JDK 21 compiles it
#   maven                     Apache-2.0                              the build (Debian's maven 3.9.x)
#   the warmed repository     Apache-2.0 / BSD-3 / MIT / EDL ...      ~/.m2 of the `worker` user, filled at image build by building the bridge's OWN pom
#                                                                    (warm/, written by warm_project.py from grpcbridge's template) once, so a jar build at
#                                                                    run time is `mvn -o` (no network): grpc-java 1.64.0, protobuf 3.25.3, the shade/compiler/
#                                                                    protobuf plugins, protoc + protoc-gen-grpc-java executables for linux-x86_64
#   python3-falcon + gunicorn Apache-2.0 / MIT                        the /capability + /bridges worker
#
# Build (ON isle-core only - the image carries a JDK and a Maven repository):
#   ./build.sh                 # writes warm/ from the framework's pom template, ships this directory to isle-core, `docker build -t prf-bridge-engines:trixie`
#   docker run -d --restart unless-stopped --name prf-bridge-engines -p 9870:9870 --memory 768m prf-bridge-engines:trixie
# Versions are printed at build time (the last RUN) and served by GET /capability.
ARG BASE=debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a
FROM ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-21-jdk-headless maven python3-falcon gunicorn ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && dpkg-query -W -f '${Package} ${Version}\n' openjdk-21-jdk-headless maven python3-falcon gunicorn
RUN useradd -m -u 1000 worker && mkdir -p /srv/bridges /srv/jars /srv/builds /warm && chown worker /srv/bridges /srv/jars /srv/builds /warm
USER worker
COPY --chown=worker warm/ /warm/
RUN cd /warm && mvn -B -q package -DskipTests && rm -rf /warm/target && du -sh /home/worker/.m2/repository
COPY --chown=worker bridge_engines_service.py bridge_hosted.py cost.json /srv/
WORKDIR /srv
EXPOSE 9870
CMD ["gunicorn", "-b", "0.0.0.0:9870", "-w", "1", "--threads", "4", "-t", "600", "--chdir", "/srv", "bridge_engines_service:app"]
