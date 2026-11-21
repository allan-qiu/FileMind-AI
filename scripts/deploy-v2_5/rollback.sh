#!/usr/bin/env bash
# LOCAL TEMPLATE. Explicit activation required; stops ONLY filemind.service.
set -euo pipefail
if [ "${1:-}" != "--approved-stop-filemind-only" ]; then
  printf '%s\n' 'No changes made. Approval required. See docs/V2_5_ROLLBACK_PLAN.md.'
  exit 2
fi
if [ "$(id -u)" -ne 0 ]; then printf '%s\n' 'Run as an authorized operator.'; exit 2; fi
systemctl stop filemind.service
systemctl is-active filemind.service || true
printf '%s\n' 'FileMind stopped. No Nginx, firewall, Docker, question-bank or database changes.'
printf '%s\n' 'Preserve code, credentials, DB+WAL and storage. Restore only from a verified backup.'
