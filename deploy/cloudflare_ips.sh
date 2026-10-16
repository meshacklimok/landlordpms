#!/usr/bin/env bash
# Run once as root after the domain is proxied through Cloudflare (orange cloud), and again every few months:
#   sudo bash deploy/cloudflare_ips.sh          trust Cloudflare's addresses so nginx and Django see the visitor's real IP
#   sudo bash deploy/cloudflare_ips.sh lock     the same, and let only Cloudflare reach ports 80 and 443
# Without this every visitor looks like one of Cloudflare's few addresses, so the per-address limits would punish everyone together.
set -euo pipefail
OUT=/etc/nginx/conf.d/cloudflare_realip.conf
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
v4=$(curl -fsS https://www.cloudflare.com/ips-v4)
v6=$(curl -fsS https://www.cloudflare.com/ips-v6)
[ -n "$v4" ] && [ -n "$v6" ] || { echo "Could not fetch Cloudflare's address lists" >&2; exit 1; }
{
    echo "# Written by deploy/cloudflare_ips.sh. Only these addresses may tell us a visitor's IP in CF-Connecting-IP."
    for ip in $v4 $v6; do echo "set_real_ip_from $ip;"; done
    echo "real_ip_header CF-Connecting-IP;"
} > "$tmp"
install -m 644 "$tmp" "$OUT"
nginx -t -q
systemctl reload nginx
echo "nginx now reads the real visitor address from Cloudflare."

if [ "${1:-}" = "lock" ]; then
    for ip in $v4 $v6; do ufw allow proto tcp from "$ip" to any port 80,443 >/dev/null; done
    ufw delete allow 'Nginx Full' >/dev/null 2>&1 || true
    echo "Ports 80 and 443 now accept Cloudflare only. SSH is untouched. Certbot's HTTP check will no longer work: use a"
    echo "Cloudflare Origin Certificate, or run 'ufw allow 80/tcp' while renewing."
fi
