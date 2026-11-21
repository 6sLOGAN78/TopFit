#!/usr/bin/env bash
# Idempotent, provider-agnostic host bootstrap for Ubuntu 24.04.
# Run as root (or via `sudo bash -s -- <args>`).
# Usage: bootstrap.sh --deploy-pubkey "<ssh public key>" --hostname <PUBLIC_HOSTNAME> \
#                     --email <ACME_EMAIL> [--ghcr-owner <lowercase owner>]
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage: bootstrap.sh --deploy-pubkey "<ssh public key>" --hostname <PUBLIC_HOSTNAME> --email <ACME_EMAIL> [--ghcr-owner <owner>]
  --deploy-pubkey  public key line to authorise for the 'deploy' user (required)
  --hostname       public hostname served by Caddy (required)
  --email          ACME / Let's Encrypt contact email (required)
  --ghcr-owner     GHCR owner, lowercased automatically (default: ayushmaurya-tech)
USAGE
  exit 2
}

PUBKEY=""
HOSTNAME_ARG=""
EMAIL=""
GHCR_OWNER="ayushmaurya-tech"

while [ $# -gt 0 ]; do
  case "$1" in
    --deploy-pubkey) PUBKEY="${2:-}"; shift 2 ;;
    --hostname) HOSTNAME_ARG="${2:-}"; shift 2 ;;
    --email) EMAIL="${2:-}"; shift 2 ;;
    --ghcr-owner) GHCR_OWNER="${2:-}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

if [ -z "$PUBKEY" ] || [ -z "$HOSTNAME_ARG" ] || [ -z "$EMAIL" ]; then
  usage
fi
GHCR_OWNER="${GHCR_OWNER,,}"

if [ "$(id -u)" -ne 0 ]; then
  echo "bootstrap.sh must run as root (use sudo)." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "== timezone =="
timedatectl set-timezone UTC || true

echo "== packages =="
apt-get update -qq
apt-get install -y -qq ca-certificates curl jq ufw unattended-upgrades openssl

echo "== deploy user =="
if ! id deploy >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" deploy
fi
install -d -o deploy -g deploy -m 700 /home/deploy/.ssh
{
  [ -f /home/deploy/.ssh/authorized_keys ] && cat /home/deploy/.ssh/authorized_keys
  [ -f /root/.ssh/authorized_keys ] && cat /root/.ssh/authorized_keys
  if [ -n "${SUDO_USER:-}" ] && [ -f "/home/${SUDO_USER}/.ssh/authorized_keys" ]; then
    cat "/home/${SUDO_USER}/.ssh/authorized_keys"
  fi
  printf '%s\n' "$PUBKEY"
} | sed '/^[[:space:]]*$/d' | sort -u > /home/deploy/.ssh/authorized_keys.new
mv /home/deploy/.ssh/authorized_keys.new /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys

echo "== sshd hardening =="
# Switch PermitRootLogin to 'no' after confirming the deploy user can log in.
cat > /etc/ssh/sshd_config.d/00-ranker.conf <<'SSHD'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
SSHD
sshd -t
systemctl reload ssh
sshd -T | grep -i passwordauthentication

echo "== firewall =="
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

echo "== unattended upgrades =="
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT

echo "== docker =="
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck disable=SC1091
  . /etc/os-release
  cat > /etc/apt/sources.list.d/docker.sources <<DOCKERSRC
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
DOCKERSRC
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
# The docker group is root-equivalent; acceptable on a single-purpose VM.
usermod -aG docker deploy

desired_daemon='{"log-driver":"local","log-opts":{"max-size":"10m","max-file":"5"}}'
if [ ! -f /etc/docker/daemon.json ] || [ "$(cat /etc/docker/daemon.json)" != "$desired_daemon" ]; then
  mkdir -p /etc/docker
  printf '%s\n' "$desired_daemon" > /etc/docker/daemon.json.new
  # compare without trailing newline difference
  if [ ! -f /etc/docker/daemon.json ] || ! diff -q <(tr -d '\n' < /etc/docker/daemon.json.new) <(tr -d '\n' < /etc/docker/daemon.json) >/dev/null; then
    mv /etc/docker/daemon.json.new /etc/docker/daemon.json
    systemctl restart docker
  else
    rm -f /etc/docker/daemon.json.new
  fi
fi

echo "== swap =="
if ! swapon --show | grep -q '/swapfile'; then
  if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo 'vm.swappiness=10' > /etc/sysctl.d/99-ranker.conf
sysctl -q -p /etc/sysctl.d/99-ranker.conf

echo "== /srv/ranker =="
install -d -o deploy -g deploy -m 750 /srv/ranker
ENV_STATE="kept existing"
if [ ! -f /srv/ranker/.env ]; then
  pg_pass="$(openssl rand -hex 24)"
  session_secret="$(openssl rand -hex 32)"
  umask 077
  cat > /srv/ranker/.env <<ENVFILE
APP_ENV=production
POSTGRES_USER=ranker
POSTGRES_DB=ranker
POSTGRES_PASSWORD=${pg_pass}
DATABASE_URL=postgresql+psycopg://ranker:${pg_pass}@db:5432/ranker
SESSION_SECRET=${session_secret}
COOKIE_SECURE=true
EMBEDDER=fastembed
EMBED_THREADS=2
LOG_LEVEL=INFO
PUBLIC_HOSTNAME=${HOSTNAME_ARG}
ACME_EMAIL=${EMAIL}
GHCR_OWNER=${GHCR_OWNER}
IMAGE_TAG=main
ENVFILE
  chmod 600 /srv/ranker/.env
  chown deploy:deploy /srv/ranker/.env
  ENV_STATE="created"
fi

echo
echo "== summary =="
echo "user:   $(id deploy)"
echo "ufw:    $(ufw status | head -1)"
echo "docker: $(docker --version)"
echo "swap:   $(swapon --show --noheadings | tr -s ' ' | head -1)"
echo ".env:   ${ENV_STATE} (/srv/ranker/.env, mode 600)"
echo
echo "Provider notes (nothing provider-specific was executed):"
echo "  GCP:    also allow tcp:80,443 in the VPC firewall and reserve a static IP."
echo "  Oracle: also open 80/443 in the VCN security list and set the repo variable"
echo "          PLATFORMS to linux/amd64,linux/arm64."
