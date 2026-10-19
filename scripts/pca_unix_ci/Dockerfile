FROM postgres:18-trixie

RUN apt-get update \
    && apt-get install --yes --no-install-recommends python3 python3-venv acl util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 pca-runtime \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin pca-runtime \
    && python3 -m venv /opt/venv

WORKDIR /opt/pca
COPY --chown=0:0 pyproject.toml uv.lock /opt/pca/
# This repository has no package build configuration. Install its declared runtime
# dependencies, not the repository as an implicitly discovered Python package.
RUN /opt/venv/bin/python -c 'import subprocess, tomllib; from pathlib import Path; dependencies = tomllib.loads(Path("pyproject.toml").read_text())["project"]["dependencies"]; subprocess.run(["/opt/venv/bin/python", "-m", "pip", "install", "--no-cache-dir", *dependencies], check=True, timeout=300)'

COPY --chown=0:0 backend /opt/pca/backend
COPY --chown=0:0 scripts /opt/pca/scripts
RUN chmod -R go-w /opt/pca /opt/venv \
    && chmod 0755 /opt /opt/pca \
    && install -d -o 10001 -g 10001 -m 0700 /opt/pca/.local-docs

# Bypass the upstream entrypoint: it must not initialize a default cluster or
# consume POSTGRES_* configuration. The root provisioner owns this cluster only.
USER 0:0
ENTRYPOINT ["/usr/bin/timeout", "--signal=TERM", "--kill-after=15s", "900s", "/opt/venv/bin/python", "-B", "/opt/pca/scripts/pca_unix_ci/provision.py"]
