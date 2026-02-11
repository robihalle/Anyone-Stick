#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"

CHAIN="ANYONE_KS"
V4="iptables"
V6="ip6tables"

have_if() { ip link show "$1" >/dev/null 2>&1; }

ensure_chain_v4() {
  $V4 -t filter -N "$CHAIN" 2>/dev/null || true
  $V4 -t filter -F "$CHAIN"

  # allow loopback + established
  $V4 -A "$CHAIN" -o lo -j RETURN
  $V4 -A "$CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  $V4 -A "$CHAIN" -d 127.0.0.0/8 -j RETURN

  # allow local management networks on common mgmt ifaces
  for IF in usb0 wlan0 eth0; do
    if have_if "$IF"; then
      $V4 -A "$CHAIN" -o "$IF" -d 10.0.0.0/8 -j RETURN
      $V4 -A "$CHAIN" -o "$IF" -d 172.16.0.0/12 -j RETURN
      $V4 -A "$CHAIN" -o "$IF" -d 192.168.0.0/16 -j RETURN
      $V4 -A "$CHAIN" -o "$IF" -d 169.254.0.0/16 -j RETURN
    fi
  done

  # drop everything else
  $V4 -A "$CHAIN" -j DROP

  # hook chain early into OUTPUT + FORWARD
  $V4 -C OUTPUT  -j "$CHAIN" 2>/dev/null || $V4 -I OUTPUT  1 -j "$CHAIN"
  $V4 -C FORWARD -j "$CHAIN" 2>/dev/null || $V4 -I FORWARD 1 -j "$CHAIN"
}

ensure_chain_v6() {
  $V6 -t filter -N "$CHAIN" 2>/dev/null || true
  $V6 -t filter -F "$CHAIN"

  $V6 -A "$CHAIN" -o lo -j RETURN
  $V6 -A "$CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
  $V6 -A "$CHAIN" -d ::1/128 -j RETURN

  for IF in usb0 wlan0 eth0; do
    if have_if "$IF"; then
      $V6 -A "$CHAIN" -o "$IF" -d fc00::/7  -j RETURN   # ULA
      $V6 -A "$CHAIN" -o "$IF" -d fe80::/10 -j RETURN   # link-local
    fi
  done

  $V6 -A "$CHAIN" -j DROP

  $V6 -C OUTPUT  -j "$CHAIN" 2>/dev/null || $V6 -I OUTPUT  1 -j "$CHAIN"
  $V6 -C FORWARD -j "$CHAIN" 2>/dev/null || $V6 -I FORWARD 1 -j "$CHAIN"
}

disable_v4() {
  $V4 -D OUTPUT  -j "$CHAIN" 2>/dev/null || true
  $V4 -D FORWARD -j "$CHAIN" 2>/dev/null || true
  $V4 -t filter -F "$CHAIN" 2>/dev/null || true
  $V4 -t filter -X "$CHAIN" 2>/dev/null || true
}

disable_v6() {
  $V6 -D OUTPUT  -j "$CHAIN" 2>/dev/null || true
  $V6 -D FORWARD -j "$CHAIN" 2>/dev/null || true
  $V6 -t filter -F "$CHAIN" 2>/dev/null || true
  $V6 -t filter -X "$CHAIN" 2>/dev/null || true
}

status() {
  if $V4 -C OUTPUT -j "$CHAIN" >/dev/null 2>&1; then
    echo "ON"
  else
    echo "OFF"
  fi
}

case "$ACTION" in
  on|enable)
    ensure_chain_v4
    ensure_chain_v6
    status
    ;;
  off|disable)
    disable_v4
    disable_v6
    status
    ;;
  status)
    status
    ;;
  *)
    echo "usage: $0 {on|off|status}" >&2
    exit 2
    ;;
esac
