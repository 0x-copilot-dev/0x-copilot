FROM node:22-alpine AS build

WORKDIR /repo

COPY package.json package-lock.json ./
COPY tsconfig.base.json ./
COPY packages/api-types/package.json ./packages/api-types/package.json
COPY packages/api-types/tsconfig.json ./packages/api-types/tsconfig.json
COPY packages/api-types/src ./packages/api-types/src
COPY packages/design-system/package.json ./packages/design-system/package.json
COPY packages/design-system/tsconfig.json ./packages/design-system/tsconfig.json
COPY packages/design-system/src ./packages/design-system/src
COPY apps/frontend/package.json ./apps/frontend/package.json
COPY apps/frontend/tsconfig.json ./apps/frontend/tsconfig.json
COPY apps/frontend/vite.config.ts ./apps/frontend/vite.config.ts
COPY apps/frontend/index.html ./apps/frontend/index.html
COPY apps/frontend/src ./apps/frontend/src

RUN npm ci
RUN npm run build --workspace @enterprise-search/frontend

FROM nginx:1.29-alpine

COPY apps/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/apps/frontend/dist /usr/share/nginx/html

EXPOSE 8080
