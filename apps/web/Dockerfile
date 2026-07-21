FROM node:24.18.0-bookworm-slim

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:${PATH}

RUN corepack enable \
    && corepack prepare pnpm@11.9.0 --activate \
    && mkdir -p /app /pnpm/store \
    && chown -R node:node /app /pnpm

WORKDIR /app

USER node

COPY --chown=node:node package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,id=hennongxi-pnpm,target=/pnpm/store,uid=1000,gid=1000 \
    pnpm install --frozen-lockfile

COPY --chown=node:node . .

EXPOSE 3000

CMD ["pnpm", "exec", "vite", "--host", "0.0.0.0", "--port", "3000"]
