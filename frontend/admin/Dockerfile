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
ARG GIT_REVISION=unknown
LABEL org.opencontainers.image.revision=$GIT_REVISION

# 设置时区（可选，用于 Nginx 日志时间准确）
ENV TZ=Asia/Shanghai
RUN apk add --no-cache tzdata

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
