# syntax=docker/dockerfile:1
# Reuse the reviewed reference's exact Linux CPython image, not a moving tag.
FROM python:3.13-slim-bookworm@sha256:70bda1e795ded2a684ef9815955e05829b14ee5bb5c3eb2a940648e88f10fce9

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHON_DOTENV_DISABLED=1 POSTGRES_DB="" \
    DJANGO_SETTINGS_MODULE=config.test_settings UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1 UV_OFFLINE=1 REQUIRE_NETWORK_ISOLATION=1 \
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/app \
    GIT_OPTIONAL_LOCKS=0

# Preparation phase only. No package manager runs during isolated execution.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git acl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.13.6 \
    && groupadd --gid 10001 polypbase_cca_eob \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin polypbase_cca_eob \
    && mkdir /app /qa /private

# These contain only authenticated TARGET blobs, never the QA checkout or its .env.
ADD target.tar /app/
COPY target.pack source-integrity.json /qa/
WORKDIR /app
RUN git init --quiet \
    && git index-pack --stdin < /qa/target.pack \
    && git update-ref refs/heads/qa-target 5461e7a5c07cf8893c2db78953316df78c9243a8 \
    && git symbolic-ref HEAD refs/heads/qa-target \
    && git read-tree 5461e7a5c07cf8893c2db78953316df78c9243a8 \
    && test ! -e /app/.venv \
    && uv venv /app/.venv --python /usr/local/bin/python3.13 \
    && UV_OFFLINE=0 uv sync --locked --no-install-project --no-default-groups --python /usr/local/bin/python3.13 \
    && python -I -S -B -c "import hashlib,json,pathlib; p=pathlib.Path('/qa/install-receipt.json'); p.write_text(json.dumps({'target':'5461e7a5c07cf8893c2db78953316df78c9243a8','uv_version':'0.13.6','uv_lock_sha256':hashlib.sha256(pathlib.Path('/app/uv.lock').read_bytes()).hexdigest(),'fresh_venv':True,'command':['uv','sync','--locked','--no-install-project','--no-default-groups','--python','/usr/local/bin/python3.13']},sort_keys=True)+'\n')" \
    && printf 'SYNTHETIC QA ONLY - NOT A SECRET\n' > /app/backend/.env \
    && printf 'SYNTHETIC QA ONLY - NOT A SECRET\n' > /private/probe \
    && chmod 0600 /app/backend/.env /private/probe \
    && chmod 0755 /app /qa /private

COPY run_tests.py prove_installation.py /qa/
USER 10001:10001
ENTRYPOINT ["/app/.venv/bin/python", "-B", "/qa/run_tests.py"]
