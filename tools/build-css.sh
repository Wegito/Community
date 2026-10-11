#!/usr/bin/env bash
# Build community/static/app.css with the Tailwind CSS standalone CLI (no Node needed).
#
#   tools/build-css.sh            build once (minified); commit the result with the template change
#   tools/build-css.sh --watch    rebuild whenever a template changes (development)
#
# The CLI is downloaded once from GitHub into tools/bin/ (git-ignored) and checked against the
# SHA-256 pinned below. The built CSS is committed, so the server needs no build step; CI rebuilds
# it and fails when the committed file differs (job "css").
#
# Updating Tailwind: set TAILWIND_VERSION and copy the hashes from the release's sha256sums.txt
# (https://github.com/tailwindlabs/tailwindcss/releases/download/v<version>/sha256sums.txt).
set -euo pipefail

TAILWIND_VERSION=4.3.3

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$ROOT/tools/bin"
INPUT="community/static/src/app.css"
OUTPUT="community/static/app.css"

# SHA-256 of each release asset of v4.3.3.
expected_sha256() {
  case "$1" in
    tailwindcss-linux-x64) echo dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a ;;
    tailwindcss-linux-x64-musl) echo a04d34ceacc8f52cbe8920ad846cdeb61d3d0021dba32db0d1f77c9d9fad7a6c ;;
    tailwindcss-linux-arm64) echo 55fd0b241214eff3de1e8ee4f22796662f2d2e7a49bcfca7477cfd0bac398195 ;;
    tailwindcss-linux-arm64-musl) echo 71ea4be79c9de9827545682df3e040053fb535d37c71ed2cfdedf9385a0868e0 ;;
    tailwindcss-macos-x64) echo 7922e0953f2110c05976e3bf58f14e643d90427575e766b7d433f5f80cbee7e1 ;;
    tailwindcss-macos-arm64) echo cdf646702987a743464dff4d9c60fd4480d1c1e73dd819a9a67f1078815dce9d ;;
    tailwindcss-windows-x64.exe) echo e0e260ce048014e9268f6237ff18f8ccf02cef521cbd0ae04e82c2cdf7aa3955 ;;
    *) return 1 ;;
  esac
}

# Release asset name for this machine, e.g. tailwindcss-linux-x64.
asset_name() {
  local os arch suffix=''
  case "$(uname -s)" in
    Linux) os=linux ;;
    Darwin) os=macos ;;
    MINGW* | MSYS* | CYGWIN*) os=windows suffix=.exe ;;
    *) echo "build-css: unsupported system $(uname -s)" >&2; return 1 ;;
  esac
  case "$(uname -m)" in
    x86_64 | amd64) arch=x64 ;;
    aarch64 | arm64) arch=arm64 ;;
    *) echo "build-css: unsupported CPU $(uname -m)" >&2; return 1 ;;
  esac
  # Alpine and other musl-based Linux systems need their own build.
  if [ "$os" = linux ] && ldd --version 2>&1 | grep -qi musl; then
    suffix=-musl
  fi
  echo "tailwindcss-$os-$arch$suffix"
}

sha256_of() {
  if command -v sha256sum > /dev/null; then
    sha256sum "$1" | cut -d ' ' -f 1
  else
    shasum -a 256 "$1" | cut -d ' ' -f 1
  fi
}

asset="$(asset_name)"
if ! expected="$(expected_sha256 "$asset")"; then
  echo "build-css: no Tailwind CLI $TAILWIND_VERSION for this machine ($asset)" >&2
  exit 1
fi
cli="$BIN_DIR/tailwindcss-v$TAILWIND_VERSION-${asset#tailwindcss-}"

if [ ! -x "$cli" ]; then
  url="https://github.com/tailwindlabs/tailwindcss/releases/download/v$TAILWIND_VERSION/$asset"
  echo "build-css: downloading $url"
  mkdir -p "$BIN_DIR"
  partial="$cli.part"
  trap 'rm -f "$partial"' EXIT
  curl --fail --location --silent --show-error --retry 3 --retry-all-errors --output "$partial" "$url"
  actual="$(sha256_of "$partial")"
  if [ "$actual" != "$expected" ]; then
    echo "build-css: checksum mismatch for $asset (expected $expected, got $actual)" >&2
    exit 1
  fi
  chmod +x "$partial"
  mv "$partial" "$cli"
fi

cd "$ROOT"
"$cli" --input "$INPUT" --output "$OUTPUT" --minify "$@"
