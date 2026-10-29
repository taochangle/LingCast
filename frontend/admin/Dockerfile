# ---- build stage ----
FROM node:22-alpine AS builder

WORKDIR /app

RUN corepack enable

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

# ---- runtime stage ----
FROM nginx:alpine AS runner

# 设置时区（可选，用于 Nginx 日志时间准确）
ENV TZ=Asia/Shanghai
RUN apk info -e tzdata >/dev/null || apk add --no-cache tzdata

# Our complete nginx.conf does not need the stock IPv6 patch, whose apk
# manifest lookup can block startup when package mirrors are unavailable.
RUN rm /docker-entrypoint.d/10-listen-on-ipv6-by-default.sh
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

ARG GIT_REVISION=unknown
LABEL org.opencontainers.image.revision=$GIT_REVISION

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
