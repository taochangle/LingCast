# ---- build stage ----
FROM golang:1.26-alpine AS builder

WORKDIR /src

ARG SERVICE=api-admin
ARG GOPROXY=https://proxy.golang.org,direct
ENV GOPROXY=$GOPROXY

COPY go.mod go.sum ./

RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/api ./cmd/${SERVICE}

# ---- runtime stage ----
FROM alpine:latest

ARG APK_MIRROR=dl-cdn.alpinelinux.org
RUN sed -i "s/dl-cdn.alpinelinux.org/${APK_MIRROR}/g" /etc/apk/repositories \
    && apk add --timeout 30 --no-cache ca-certificates tzdata

COPY --from=builder /out/api /usr/local/bin/api

ARG GIT_REVISION=unknown
LABEL org.opencontainers.image.revision=$GIT_REVISION

EXPOSE 8081
ENTRYPOINT ["api"]
