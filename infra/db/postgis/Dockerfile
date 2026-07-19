FROM postgres:17.10-bookworm

ARG POSTGIS_PACKAGE_VERSION=3.6.4+dfsg-2.pgdg12+1

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        "postgresql-${PG_MAJOR}-postgis-3=${POSTGIS_PACKAGE_VERSION}" \
        "postgresql-${PG_MAJOR}-postgis-3-scripts=${POSTGIS_PACKAGE_VERSION}"; \
    rm -rf /var/lib/apt/lists/*

COPY 10-enable-postgis.sql /docker-entrypoint-initdb.d/10-enable-postgis.sql
