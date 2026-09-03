FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Install Node.js 20 for the WhatsApp bridge
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg git && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get purge -y gnupg && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The project depends on the vendored LeRobot tree, so copy the complete source
# before installation. Installing from a metadata-only layer would leave the
# local path dependency unresolved in a clean Docker build.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY roborsi/ roborsi/
COPY bridge/ bridge/
COPY frontend/web/ frontend/web/
RUN uv pip install --system --no-cache ".[web,libero]"

# Build the WhatsApp bridge
WORKDIR /app/bridge
RUN npm install && npm run build

# Build the Manager session cockpit.
WORKDIR /app/frontend/web
RUN npm install && npm run build
WORKDIR /app

# Create config directory
RUN mkdir -p /root/.roborsi

# Evolution dashboard and Manager session cockpit.
EXPOSE 8787 8795

ENTRYPOINT ["roborsi"]
CMD ["status"]
