#!/bin/bash
# Anyone Stick — Privacy Mode (PREWARM -> FAST FLIP, LEAK-FREE)
# Key: keep client online until we KNOW anon+circuits are ready, then flip iptables instantly.

set -euo pipefail

IN_IF="usb0"
OUT_IF="wlan0"
PI_IP="192.168.7.1"

TRANS_PORT="9040"
DNS_PORT="9053"
SOCKS_PORT="9050"
CTRL_HOST="127.0.0.1"
CTRL_PORT="9051"
CTRL_COOKIE="/var/lib/anon/control_auth_cookie"

CM_BASE="${CM_BASE:-http://127.0.0.1:8787}"
CM_SVC="${CM_SVC:-anyone-stick-circuit-manager.service}"

STATE_DIR="/var/lib/anyone-stick"
PRIV_OK="${STATE_DIR}/privacy_verified"
NORM="/usr/local/bin/mode_normal.sh"

log(){ echo "[privacy] $*"; }

mkdir -p "$STATE_DIR" >/dev/null 2>&1 || true
rm -f "$PRIV_OK" >/dev/null 2>&1 || true

rollback(){
  log "ERROR -> rollback to NORMAL"
  rm -f "$PRIV_OK" >/dev/null 2>&1 || true
  [ -x "$NORM" ] && "$NORM" >/dev/null 2>&1 || true
}
trap rollback ERR

is_listening_tcp() {
  local ip="$1" port="$2"
  ss -lnt 2>/dev/null | awk -v ip="$ip" -v port="$port" '($4==ip ":" port){f=1} END{exit(f?0:1)}'
}

is_listening_udp() {
  local ip="$1" port="$2"
  ss -lun 2>/dev/null | awk -v ip="$ip" -v port="$port" '($4==ip ":" port){f=1} END{exit(f?0:1)}'
}

read_cookie_hex() {
  [ -r "$CTRL_COOKIE" ] || return 1
  python3 - <<'PYC'
import binascii
with open("/var/lib/anon/control_auth_cookie","rb") as f:
    print(binascii.hexlify(f.read()).decode(), end="")
PYC
}

bootstrap_progress() {
  local hex line prog req
  hex="$(read_cookie_hex 2>/dev/null || true)"
  [ -n "$hex" ] || { echo ""; return 0; }
  req=$'AUTHENTICATE '"$hex"$'\r\nGETINFO status/bootstrap-phase\r\nQUIT\r\n'
  line="$(printf "%s" "$req" | nc -w 2 "$CTRL_HOST" "$CTRL_PORT" 2>/dev/null | sed -n 's/^250-status\/bootstrap-phase=//p' | tail -n 1 || true)"
  prog="$(echo "$line" | sed -n 's/.*PROGRESS=\([0-9][0-9]*\).*/\1/p')"
  echo "${prog:-}"
}

wait_bootstrap_100() {
  local max="${1:-120}" t=0
  while [ "$t" -lt "$max" ]; do
    if is_listening_tcp 127.0.0.1 "$CTRL_PORT" && [ -r "$CTRL_COOKIE" ]; then
      local prog
      prog="$(bootstrap_progress 2>/dev/null || true)"
      if [ -n "$prog" ]; then
        log "bootstrap progress=${prog}%"
        [ "$prog" -ge 100 ] && return 0
      fi
    fi
    sleep 1; t=$((t+1))
  done
  return 1
}

socks_verified() {
  local url body
  for url in \
    "http://ip-api.com/line/?fields=query" \
    "https://check.en.anyone.tech/" \
    "http://example.com/"
  do
    body="$(curl -sS --max-time 8 --connect-timeout 4 \
      --socks5-hostname "127.0.0.1:${SOCKS_PORT}" \
      "$url" 2>/dev/null || true)"
    if [ -n "$body" ]; then
      log "socks_verified via $url"
      return 0
    fi
  done
  return 1
}

wait_socks_verified() {
  local max="${1:-40}" t=0
  while [ "$t" -lt "$max" ]; do
    socks_verified && return 0
    sleep 1; t=$((t+1))
  done
  return 1
}

cm_ready() {
  curl -fsS --max-time 1 "${CM_BASE}/health" 2>/dev/null | grep -q '"ready":true'
}

cm_hops_len() {
  python3 -c '
import json, urllib.request, os
try:
    base = os.environ.get("CM_BASE","http://127.0.0.1:8787").rstrip("/")
    with urllib.request.urlopen(base + "/circuit", timeout=2) as r:
        d = json.loads(r.read().decode("utf-8","replace"))
    hops = d.get("hops") or []
    print(len(hops) if isinstance(hops, list) else 0, end="")
except Exception:
    print(0, end="")
' 2>/dev/null || echo 0
}

wait_cm_ready_and_circuit() {
  local max="${1:-25}" t=0
  while [ "$t" -lt "$max" ]; do
    if cm_ready; then
      local n
      n="$(cm_hops_len || echo 0)"
      if [ "${n:-0}" -ge 2 ]; then
        log "circuit-manager ready; hops=${n}"
        return 0
      fi
    fi
    sleep 1; t=$((t+1))
  done
  return 1
}

apply_leakfree_firewall() {
  log "Applying leak-free firewall (flip routing now)..."
  # Ensure Anon DNSPort is live after any config/restart
  pkill -HUP -f "/usr/local/bin/anon" 2>/dev/null; sleep 1

  sysctl -w net.ipv4.ip_forward=1 >/dev/null
  sysctl -w net.ipv4.conf."$IN_IF".route_localnet=1 >/dev/null 2>&1 || true
  sysctl -w net.ipv4.conf.all.route_localnet=1 >/dev/null 2>&1 || true

  iptables -F
  iptables -t nat -F
  iptables -t mangle -F

  # Disable IPv6 on stick (best-effort)
  sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null 2>&1 || true
  sysctl -w net.ipv6.conf.default.disable_ipv6=1 >/dev/null 2>&1 || true
  sysctl -w net.ipv6.conf.lo.disable_ipv6=1 >/dev/null 2>&1 || true

  iptables -P INPUT ACCEPT
  iptables -P OUTPUT ACCEPT
  iptables -P FORWARD DROP

  # DNS MUST come first — client uses Pi IP as DNS, so RETURN would swallow DNS queries!
  iptables -t nat -A PREROUTING -i "$IN_IF" -p udp --dport 53 -j DNAT --to-destination 127.0.0.1:"$DNS_PORT"

  # Keep portal reachable (AFTER DNS rule so DNS gets DNAT'd through Anon)
  iptables -t nat -A PREROUTING -i "$IN_IF" -d "$PI_IP" -j RETURN

  # All other TCP -> TransPort
  iptables -t nat -A PREROUTING -i "$IN_IF" -p tcp -j DNAT --to-destination 127.0.0.1:"$TRANS_PORT"

  iptables -A INPUT -i "$IN_IF" -p tcp --dport "$TRANS_PORT" -j ACCEPT
  iptables -A INPUT -i "$IN_IF" -p udp --dport "$DNS_PORT" -j ACCEPT
  iptables -A INPUT -i "$IN_IF" -p udp -j DROP

  # NAT out
  iptables -A OUTPUT -o "$OUT_IF" -j ACCEPT
  iptables -t nat -A POSTROUTING -o "$OUT_IF" -j MASQUERADE

  # TCP MSS clamp (avoid MTU blackholes)
  iptables -t mangle -A OUTPUT -o "$OUT_IF" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu || true
  iptables -t mangle -A POSTROUTING -o "$OUT_IF" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu || true

  # IPv6: drop client IPv6 on usb0 to avoid AAAA blackhole
  ip6tables -P FORWARD DROP >/dev/null 2>&1 || true
  ip6tables -F >/dev/null 2>&1 || true
  ip6tables -t nat -F >/dev/null 2>&1 || true
  ip6tables -A INPUT  -i "$IN_IF" -j DROP >/dev/null 2>&1 || true
  ip6tables -A FORWARD -i "$IN_IF" -j DROP >/dev/null 2>&1 || true
  ip6tables -A OUTPUT -o "$IN_IF" -j DROP >/dev/null 2>&1 || true

  log "Leak-free firewall active."
}

# ──────────────────────────────────────────────
# FAST-PATH: if already warm, flip immediately
# ──────────────────────────────────────────────
if cm_ready; then
  n="$(cm_hops_len || echo 0)"
  if [ "${n:-0}" -ge 2 ] && socks_verified; then
    log "Fast-path: warm (hops=${n}) + SOCKS ok -> flip"
    apply_leakfree_firewall
    date > "$PRIV_OK" 2>/dev/null || true
    log "OK: Privacy VERIFIED + routing active (fast-path)."
    trap - ERR
    exit 0
  fi
fi

# ──────────────────────────────────────────────
# PREWARM / VERIFY BEFORE FLIP (keeps client online while waiting)
# ──────────────────────────────────────────────
log "Prewarm: verifying anon + circuit-manager BEFORE flipping..."
is_listening_tcp 127.0.0.1 "$TRANS_PORT"
is_listening_tcp 127.0.0.1 "$SOCKS_PORT"
is_listening_tcp 127.0.0.1 "$CTRL_PORT"
is_listening_udp 127.0.0.1 "$DNS_PORT"

wait_bootstrap_100 120
wait_socks_verified 40
wait_cm_ready_and_circuit 25

# Now flip instantly
apply_leakfree_firewall
date > "$PRIV_OK" 2>/dev/null || true
log "OK: Privacy VERIFIED + routing active."

trap - ERR
exit 0
