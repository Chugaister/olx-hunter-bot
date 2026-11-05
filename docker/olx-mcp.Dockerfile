# olx-mcp served over Streamable HTTP.
#
# The source comes from the named build context `olx-mcp-src` (see docker-compose.yml):
# by default a pinned commit of the fork's `deploy` branch on GitHub, or a local checkout
# for development (OLX_MCP_SOURCE=../olx-mcp). Only the files copied below are transferred.
#
# The base image tag MUST match the "playwright" version in olx-mcp's package-lock.json:
# each Playwright release expects one specific browser build, which this image already contains.
FROM mcr.microsoft.com/playwright:v1.55.0-noble

WORKDIR /app

# Chromium + system libraries are already in the base image: skip the postinstall
# `playwright install --with-deps` (and husky's `prepare`) with --ignore-scripts.
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
COPY --from=olx-mcp-src package.json package-lock.json ./
RUN npm ci --ignore-scripts

COPY --from=olx-mcp-src tsconfig.json ./
COPY --from=olx-mcp-src src ./src
RUN npm run build \
    && npm prune --omit=dev --ignore-scripts \
    && npm cache clean --force \
    && rm -rf src tsconfig.json

# Non-root user that ships with the Playwright image.
USER pwuser

ENV NODE_ENV=production \
    OLX_MCP_TRANSPORT=http \
    OLX_MCP_HOST=0.0.0.0 \
    OLX_MCP_PORT=3000
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "dist/index.js"]
