#!/usr/bin/env bash
# Run SPECTRA over one scanner's JSON output and always leave a Markdown
# report behind, so the CI report job has something to post either way.
#
#   scripts/spectra_ci.sh <scan.json> <finding-count> <title> <out-base>
#
# SPECTRA runs only when there is something to analyze and an API key is set.
# A clean scan, a missing key (e.g. fork PRs), or a SPECTRA failure each write
# a short note instead. The script never fails the job: SPECTRA advises, it
# doesn't gate.
#
# Env: ANTHROPIC_API_KEY (optional), SPECTRA_MODEL (optional --model override).
set -uo pipefail

scan="$1"
count="$2"
title="$3"
out="$4"

mkdir -p "$(dirname "$out")"

note() {
  printf '### %s\n\n%s\n' "$title" "$1" >"$out.md"
}

if [ ! -s "$scan" ]; then
  note "⚠️ The scanner produced no output, so SPECTRA had nothing to analyze. Check the scan step log."
  exit 0
fi

if [ "$count" -eq 0 ]; then
  note "✅ Clean scan: no findings, so SPECTRA was skipped (no API call made)."
  exit 0
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  note "ℹ️ The scanner reported **$count** finding(s), but \`ANTHROPIC_API_KEY\` isn't available to this run (not configured, or this is a fork PR), so SPECTRA analysis was skipped. The raw scan is in the workflow artifacts."
  exit 0
fi

args=(analyze "$scan" --format both --output "$out-spectra" --usage)
if [ -n "${SPECTRA_MODEL:-}" ]; then
  args+=(--model "$SPECTRA_MODEL")
fi

if spectra "${args[@]}"; then
  { printf '### %s\n\n' "$title"; cat "$out-spectra.md"; } >"$out.md"
else
  note "❌ SPECTRA failed while analyzing **$count** finding(s). See the job log. The raw scan is in the workflow artifacts."
fi
exit 0
