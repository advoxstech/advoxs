FROM node:20-alpine AS base
RUN corepack enable
WORKDIR /repo

FROM base AS deps
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml* ./
COPY apps/web/package.json apps/web/package.json
COPY packages/ui/package.json packages/ui/package.json
COPY packages/types/package.json packages/types/package.json
COPY packages/config/package.json packages/config/package.json
RUN pnpm install --frozen-lockfile

FROM deps AS build
# Next.js embute variáveis NEXT_PUBLIC_* no bundle no momento do build, não
# em tempo de execução — precisa chegar como build-arg (ver
# .github/workflows/build-images.yml), nunca só no .env do host.
ARG NEXT_PUBLIC_STRIPE_CONNECT_PUBLISHABLE_KEY
ENV NEXT_PUBLIC_STRIPE_CONNECT_PUBLISHABLE_KEY=$NEXT_PUBLIC_STRIPE_CONNECT_PUBLISHABLE_KEY
COPY . .
RUN pnpm turbo run build --filter=@advoxs/web

FROM base AS runner
ENV NODE_ENV=production
WORKDIR /repo
COPY --from=build /repo/apps/web/public ./apps/web/public
COPY --from=build /repo/apps/web/.next/standalone ./
COPY --from=build /repo/apps/web/.next/static ./apps/web/.next/static
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
