# ---- build ----
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY . .
ARG VITE_API_BASE_URL=/api
ARG VITE_DEMO_VIDEO_URL=
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL VITE_DEMO_VIDEO_URL=$VITE_DEMO_VIDEO_URL
RUN npm run build

# ---- serve ----
FROM nginx:1.27-alpine
# Where API calls go; override per environment (docker compose, Azure Container Apps).
ENV BACKEND_URL=http://backend:8000
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD wget -qO- http://127.0.0.1/healthz || exit 1
