#!/usr/bin/env bash
set -euo pipefail

PB_USER="${PROGRAMBENCH_USER:-programbench}"
DOWNLOAD_TARGETS="${PROGRAMBENCH_DOWNLOAD_TARGETS:-0}"
RUN_SMOKE="${PROGRAMBENCH_RUN_SMOKE:-0}"
PB_HOME="/home/${PB_USER}"
RESEARCH_ROOT="${PB_HOME}/research"
UV_VERSION="0.11.28"
PYTHON_VERSION="3.12.13"
GO_VERSION="1.26.5"
GO_SHA256="5c2c3b16caefa1d968a94c1daca04a7ca301a496d9b086e17ad77bb81393f053"
RUST_VERSION="1.92.0"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this bootstrap as root." >&2
  exit 2
fi
if ! id "$PB_USER" >/dev/null 2>&1; then
  echo "Linux user does not exist: $PB_USER" >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  apt-transport-https build-essential ca-certificates cmake curl git git-lfs gnupg \
  jq lsb-release make pkg-config python3 python3-pip python3-venv ripgrep rsync shellcheck sqlite3 \
  sudo tar tmux unzip xz-utils zip

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
usermod -aG docker "$PB_USER"
systemctl enable --now docker

go_archive="/tmp/go${GO_VERSION}.linux-amd64.tar.gz"
curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" -o "$go_archive"
echo "${GO_SHA256}  ${go_archive}" | sha256sum --check --strict
rm -rf /usr/local/go
tar -C /usr/local -xzf "$go_archive"
rm -f "$go_archive"

runuser -u "$PB_USER" -- env HOME="$PB_HOME" bash -lc "
  set -euo pipefail
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain ${RUST_VERSION}
  curl -LsSf https://astral.sh/uv/${UV_VERSION}/install.sh | env UV_NO_MODIFY_PATH=1 sh
  \"\$HOME/.local/bin/uv\" python install ${PYTHON_VERSION}
"

install -d -o "$PB_USER" -g "$PB_USER" "$RESEARCH_ROOT" "$PB_HOME/.config/programbench"
docker_cpus="$(nproc)"
if (( docker_cpus > 10 )); then docker_cpus=10; fi
cat > "$PB_HOME/.config/programbench/env.sh" <<EOF
export PATH="/usr/local/go/bin:\$HOME/.cargo/bin:\$HOME/.local/bin:\$PATH"
export GOTOOLCHAIN=auto
export TZ=UTC
export PROGRAMBENCH_DOCKER_CPUS=${docker_cpus}
export PROGRAMBENCH_DOCKER_RUN_TIMEOUT=900
export PROGRAMBENCH_DOCKER_CP_TIMEOUT=600
export HF_HOME="\$HOME/.cache/huggingface"
EOF
chown "$PB_USER:$PB_USER" "$PB_HOME/.config/programbench/env.sh"
if ! grep -Fq '.config/programbench/env.sh' "$PB_HOME/.bashrc"; then
  echo 'source "$HOME/.config/programbench/env.sh"' >> "$PB_HOME/.bashrc"
fi
chown "$PB_USER:$PB_USER" "$PB_HOME/.bashrc"

runuser -u "$PB_USER" -- env HOME="$PB_HOME" bash -lc '
  set -euo pipefail
  source "$HOME/.config/programbench/env.sh"
  git config --global init.defaultBranch main
  git config --global core.autocrlf input
  git lfs install --skip-repo

  if [[ ! -d "$HOME/research/programbench/.git" ]]; then
    git clone https://github.com/facebookresearch/programbench.git "$HOME/research/programbench"
  else
    git -C "$HOME/research/programbench" pull --ff-only
  fi
  if [[ ! -d "$HOME/research/programbench-scaffold/.git" ]]; then
    git clone --branch codex-programbench-gym https://github.com/HarminChee/programbench-scaffold.git "$HOME/research/programbench-scaffold"
  else
    git -C "$HOME/research/programbench-scaffold" pull --ff-only
  fi

  cd "$HOME/research/programbench"
  uv sync --dev --python 3.12.13
  cd "$HOME/research/programbench-scaffold"
  if [[ ! -d .venv ]]; then
    uv venv --python 3.12.13 .venv
  fi
  uv pip install --python .venv/bin/python pytest pytest-timeout pytest-xdist pyyaml
'

validation="$RESEARCH_ROOT/setup-validation.txt"
{
  echo "ProgramBench environment validation"
  date --iso-8601=seconds
  uname -a
  lsb_release -ds
  docker version
  docker compose version
  /usr/local/go/bin/go version
  runuser -u "$PB_USER" -- env HOME="$PB_HOME" bash -lc 'source "$HOME/.config/programbench/env.sh"; python3 --version; uv --version; rustc --version; cargo --version'
} > "$validation" 2>&1

docker run --rm hello-world >> "$validation" 2>&1
runuser -u "$PB_USER" -- env HOME="$PB_HOME" bash -lc '
  set -euo pipefail
  source "$HOME/.config/programbench/env.sh"
  cd "$HOME/research/programbench"
  uv run pytest -q
  cd "$HOME/research/programbench-scaffold"
  .venv/bin/python -m unittest discover -s tests -v 2>/dev/null || true
' >> "$validation" 2>&1

TARGETS=(
  "sclevine__yj.8016400"
  "multiprocessio__dsq.c3ae0ba"
  "rs__jplot.2a54bcc"
  "psampaz__go-mod-outdated.bb79367"
)

if [[ "$DOWNLOAD_TARGETS" == "1" ]]; then
  for instance_id in "${TARGETS[@]}"; do
    image_id="${instance_id/__/_1776_}"
    docker pull "programbench/${image_id}:task_cleanroom_v6"
    runuser -u "$PB_USER" -- env HOME="$PB_HOME" INSTANCE_ID="$instance_id" bash -lc '
      set -euo pipefail
      source "$HOME/.config/programbench/env.sh"
      cd "$HOME/research/programbench"
      uv run programbench blob sync "$INSTANCE_ID"
    '
  done
fi

if [[ "$RUN_SMOKE" == "1" ]]; then
  runuser -u "$PB_USER" -- env HOME="$PB_HOME" bash -lc '
    set -euo pipefail
    source "$HOME/.config/programbench/env.sh"
    cd "$HOME/research/programbench-scaffold"
    .venv/bin/python tools/programbench_run_pb_style_go_oracle_pipeline.py \
      sclevine__yj.8016400 \
      --suite-label fresh_machine_smoke_v1 \
      --max-cases 40 \
      --xdist 1 \
      --overwrite
  '
fi

chown -R "$PB_USER:$PB_USER" "$RESEARCH_ROOT"
echo "Bootstrap complete. Validation: $validation"
