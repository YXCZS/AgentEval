FROM node:22-alpine AS dependencies
WORKDIR /app
# 国内构建可通过 build arg 切换 npm 镜像源（compose 由 .env.production 注入）。
ARG NPM_REGISTRY=https://registry.npmjs.org
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --registry="$NPM_REGISTRY"

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ARG NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
ARG NEXT_PUBLIC_PROJECT_ID=default-project
ARG NEXT_PUBLIC_WORKSPACE_SESSION=
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_PROJECT_ID=$NEXT_PUBLIC_PROJECT_ID
ENV NEXT_PUBLIC_WORKSPACE_SESSION=$NEXT_PUBLIC_WORKSPACE_SESSION
COPY --from=dependencies /app/node_modules ./node_modules
COPY apps/web ./
RUN npm run build
EXPOSE 3000
CMD ["npm", "run", "start"]
