# ── build stage ──────────────────────────────────────────────────────────
FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY go.sum ./
RUN go mod download
COPY . .
# lib/pq is pure Go, so we can build a fully static binary.
RUN CGO_ENABLED=0 GOOS=linux go build -o /gateway .

# ── runtime stage ────────────────────────────────────────────────────────
FROM alpine:3.20
RUN adduser -D -u 10001 app
COPY --from=build /gateway /usr/local/bin/gateway
USER app
EXPOSE 8080
ENTRYPOINT ["gateway"]