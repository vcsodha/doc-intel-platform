FROM golang:1.21-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN go mod tidy
RUN CGO_ENABLED=0 GOOS=linux go build -o main ./cmd/server/main.go

FROM alpine:latest
WORKDIR /app
COPY --from=builder /app/main .
CMD ["./main"]
