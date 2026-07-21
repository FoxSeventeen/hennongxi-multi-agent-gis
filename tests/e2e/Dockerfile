FROM mcr.microsoft.com/playwright:v1.61.1-noble

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:${PATH}

RUN corepack enable \
    && corepack prepare pnpm@11.9.0 --activate

WORKDIR /work

COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=hennongxi-e2e-pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile

COPY playwright.config.ts tsconfig.json ./
COPY tests ./tests

CMD ["pnpm", "test"]
