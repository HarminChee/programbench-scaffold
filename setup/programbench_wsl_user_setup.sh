#!/usr/bin/env bash
set -euo pipefail

user_name="${1:?Linux user name is required}"
if ! id "$user_name" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$user_name"
fi

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq sudo
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$user_name" > "/etc/sudoers.d/$user_name"
chmod 0440 "/etc/sudoers.d/$user_name"
cat > /etc/wsl.conf <<EOF
[boot]
systemd=true

[user]
default=$user_name
EOF
