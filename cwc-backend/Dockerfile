# CWC backend (Spring Boot 3.2 / Java 17)
# Build: docker compose build backend

# ── Build stage ───────────────────────────────────────────────────────────────
FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /src
# Dependencies first so code changes don't re-download the whole Maven world
COPY pom.xml .
RUN mvn -q -B dependency:go-offline || true   # best effort: package below fetches anything missed
COPY src ./src
RUN mvn -q -B clean package -DskipTests

# ── Run stage ─────────────────────────────────────────────────────────────────
FROM eclipse-temurin:17-jre
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --create-home app
WORKDIR /app
COPY --from=build /src/target/cwc-backend-*.jar /app/cwc-backend.jar
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod 755 /app/docker-entrypoint.sh

# The backend stores times in UTC (same as JAVA_TOOL_OPTIONS in the README)
ENV JAVA_OPTS="-Xms256m -Xmx1g -Duser.timezone=UTC" \
    TZ=UTC
USER app
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
  CMD curl -fsS http://localhost:8080/api/ingest/status > /dev/null || exit 1
ENTRYPOINT ["/app/docker-entrypoint.sh"]
