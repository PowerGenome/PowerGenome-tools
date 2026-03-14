#!/usr/bin/env bash
set -uo pipefail

payload="$(cat)"

py_files=()
while IFS= read -r file_path; do
    if [ -n "$file_path" ]; then
        py_files+=("$file_path")
    fi
done < <(
    HOOK_PAYLOAD="$payload" python3 - <<'PY'
import json
import os
import sys
from pathlib import Path


def wants_path(key):
    if not key:
        return False
    lowered = key.lower()
    return "path" in lowered or "file" in lowered


def collect_paths(value, key_hint, out):
    if isinstance(value, dict):
        for k, v in value.items():
            collect_paths(v, k, out)
        return

    if isinstance(value, list):
        for item in value:
            collect_paths(item, key_hint, out)
        return

    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return
        if candidate.endswith(".py") and (wants_path(key_hint) or os.path.sep in candidate or candidate.startswith(".")):
            out.add(candidate)


payload = os.environ.get("HOOK_PAYLOAD", "")
if not payload:
    sys.exit(0)

try:
    data = json.loads(payload)
except Exception:
    sys.exit(0)

tool_input = data.get("tool_input")
if not tool_input:
    sys.exit(0)

paths = set()
collect_paths(tool_input, "", paths)

if not paths:
    sys.exit(0)

cwd = data.get("cwd") or os.getcwd()
resolved = []
for raw_path in sorted(paths):
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(cwd) / path
    path = path.resolve()
    if path.suffix == ".py" and path.is_file():
        resolved.append(str(path))

for file_path in resolved:
    print(file_path)
PY
)

if [ "${#py_files[@]}" -eq 0 ]; then
  exit 0
fi

if command -v uv >/dev/null 2>&1; then
  uv run black "${py_files[@]}"
  exit $?
fi

if python3 -m black --version >/dev/null 2>&1; then
  python3 -m black "${py_files[@]}"
  exit $?
fi

if command -v black >/dev/null 2>&1; then
  black "${py_files[@]}"
  exit $?
fi

echo "Black is not installed. Skipping Python formatting hook." >&2
exit 0