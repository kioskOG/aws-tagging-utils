#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
#  aws-tagging-utils — EC2 Bootstrap Script
#
#  Run this on a fresh EC2 instance (Amazon Linux 2023 or Ubuntu 22.04)
#  to install Docker, Docker Compose plugin, and start the application.
#
#  Usage:
#    chmod +x scripts/ec2_bootstrap.sh
#    ./scripts/ec2_bootstrap.sh
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

OS_ID="$(. /etc/os-release && echo "$ID")"

install_docker_amazon_linux() {
    echo "==> Detected Amazon Linux. Installing Docker..."
    sudo dnf update -y
    sudo dnf install -y docker git
    sudo systemctl enable --now docker
    sudo usermod -aG docker "$USER"

    echo "==> Installing Docker Compose plugin..."
    ARCH="$(uname -m)"
    COMPOSE_VERSION="v2.27.0"
    sudo mkdir -p /usr/local/lib/docker/cli-plugins
    sudo curl -SL \
      "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${ARCH}" \
      -o /usr/local/lib/docker/cli-plugins/docker-compose
    sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
}

install_docker_ubuntu() {
    echo "==> Detected Ubuntu. Installing Docker..."
    sudo apt-get update -y
    sudo apt-get install -y ca-certificates curl gnupg git lsb-release
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
      | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
      | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -y
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    sudo systemctl enable --now docker
    sudo usermod -aG docker "$USER"
}

case "$OS_ID" in
    amzn) install_docker_amazon_linux ;;
    ubuntu) install_docker_ubuntu ;;
    *)
        echo "ERROR: Unsupported OS: $OS_ID. Install Docker manually."
        exit 1
        ;;
esac

echo "==> Docker version:"
docker --version
echo "==> Docker Compose version:"
docker compose version

echo ""
echo "✅ Bootstrap complete."
echo ""
echo "NOTE: You may need to log out and back in for group membership to take effect."
echo "      Or run:  newgrp docker"
