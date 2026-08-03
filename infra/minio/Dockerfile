FROM golang:1.24-bookworm AS builder

ARG MINIO_TAG=RELEASE.2025-10-15T17-29-55Z

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git clone \
    --depth 1 \
    --branch "${MINIO_TAG}" \
    https://github.com/minio/minio.git \
    /src/minio

WORKDIR /src/minio

RUN CGO_ENABLED=0 \
    GOOS=linux \
    GOARCH=amd64 \
    go build \
    -tags kqueue \
    -trimpath \
    --ldflags "$(go run buildscripts/gen-ldflags.go)" \
    -o /out/minio \
    .

FROM debian:bookworm-slim

ARG MINIO_TAG=RELEASE.2025-10-15T17-29-55Z

LABEL org.opencontainers.image.title="Local MinIO Community"
LABEL org.opencontainers.image.source="https://github.com/minio/minio"
LABEL org.opencontainers.image.version="${MINIO_TAG}"
LABEL org.opencontainers.image.licenses="AGPL-3.0"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /out/minio /usr/local/bin/minio

EXPOSE 9000 9001

VOLUME ["/data"]

ENTRYPOINT ["/usr/local/bin/minio"]

CMD ["server", "/data", "--console-address", ":9001"]
