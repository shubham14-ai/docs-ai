# syntax=docker/dockerfile:1.7
# ====================================================================
# Document Insights API — multi-stage image (api · worker · tests)
# ====================================================================
# Stages:
#   base      -> python + non-root user                       [shared]
#   deps      -> + runtime requirements                       [cached]
#   dev-deps  -> + test requirements                          [cached]
#   dev       -> + source, uvicorn --reload   (api/worker/tests in dev)
#   prod      -> deps + source, no reload     (DEFAULT target)
#
# Rebuild cost: a source edit never re-runs pip, because requirements are
# copied and installed before the source is. In dev the source is not even
# baked in -- compose bind-mounts ./app, so a code change reloads in place
# and nothing is rebuilt at all.
# ====================================================================
ARG PYTHON_VERSION=3.12

# --------------------------------------------------------------------
# STAGE: base
# --------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Do not run as root. Created here so every stage inherits the same uid.
RUN useradd --create-home --uid 10001 appuser

# --------------------------------------------------------------------
# STAGE: deps — runtime dependencies (re-runs only when requirements change)
# --------------------------------------------------------------------
FROM base AS deps

COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# --------------------------------------------------------------------
# STAGE: dev-deps — + pytest and friends
# --------------------------------------------------------------------
FROM deps AS dev-deps

COPY requirements-dev.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements-dev.txt

# --------------------------------------------------------------------
# STAGE: dev — live reload; source is overlaid by a bind mount in compose
# --------------------------------------------------------------------
FROM dev-deps AS dev

COPY pytest.ini conftest.py ./
COPY app ./app
COPY tests ./tests

# Reports land here; compose bind-mounts the host directory over it.
RUN mkdir -p /srv/test-results

RUN chown -R appuser /srv
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# --------------------------------------------------------------------
# STAGE: prod — default target: no test deps, no reloader, source baked in
# --------------------------------------------------------------------
FROM deps AS prod

COPY app ./app

RUN chown -R appuser /srv
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
