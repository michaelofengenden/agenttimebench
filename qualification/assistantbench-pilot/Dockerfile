FROM python@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
COPY requirements.lock /opt/pins/requirements.lock
RUN python -m pip install --no-cache-dir --requirement /opt/pins/requirements.lock \
    && python -m playwright install --with-deps chromium \
    && python -m pip freeze > /opt/pins/installed.txt \
    && dpkg-query -W > /opt/pins/os-packages.txt \
    && useradd --create-home --uid 10001 agenttime \
    && chmod -R a+rX /opt/playwright
WORKDIR /opt/qualification
USER 10001:10001
ENTRYPOINT ["/bin/sleep"]
CMD ["infinity"]
