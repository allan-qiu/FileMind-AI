#!/usr/bin/env bash
# Read-only Ubuntu diagnostics. No sudo, installs, reloads, deletions or configuration writes.
set -u
section() { printf '\n### %s\n' "$1"; }
check() { if command -v "$1" >/dev/null 2>&1; then "$@"; else printf 'Unavailable: %s\n' "$1"; fi; }
section 'Time / kernel / CPU / load'
date -Is
cat /etc/os-release
check uname -a
check nproc
check uptime
section 'Memory (MiB)'
check free -m
section 'Disk space and filesystems'
check df -hT
section 'Listening sockets (read only; process details may require permission)'
check ss -lntup
section 'Top resident memory users (no command arguments or environment secrets)'
ps -eo pid,user,comm,pcpu,pmem,rss --sort=-rss | head -n 25
section 'Service status only; no restart/reload'
if command -v systemctl >/dev/null 2>&1; then
  systemctl list-units --type=service --state=running --no-pager --plain
  for service in nginx docker postgresql mysql mariadb; do
    printf '%s: ' "$service"
    systemctl is-active "$service" 2>/dev/null || true
  done
fi
section 'Docker running container metadata only'
if command -v docker >/dev/null 2>&1; then
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>&1 || true
else
  printf 'Docker command unavailable. No attempt to install it.\n'
fi
section 'Nginx configuration layout only (not private config contents)'
if [ -d /etc/nginx ]; then
  find /etc/nginx -maxdepth 2 -type f -name '*.conf' -print 2>/dev/null || true
else
  printf '/etc/nginx is unavailable.\n'
fi
section 'FileMind candidate port 8765 (no binding or firewall modification)'
if command -v ss >/dev/null 2>&1; then ss -lnt 'sport = :8765'; fi
printf '\nRead-only diagnostics finished. Actual capacity and deployment still require review.\n'
