# Sandbox for the retrospective forks: Claude Code 2.1.280 and Codex 0.156.1, no host files.
FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates git procps && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code@2.1.280 @openai/codex@0.156.1 && npm cache clean --force
COPY --chmod=0755 oracle_tool.py /opt/retro/oracle_tool.py
RUN useradd -m -u 1001 agent && mkdir -p /workspace && chown agent /workspace
USER agent
WORKDIR /workspace
