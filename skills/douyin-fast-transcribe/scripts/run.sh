#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$script_dir/../../.." && pwd)
if [ -n "${DOUYIN_TRANSCRIBE_PYTHON:-}" ]; then
  task_python=$DOUYIN_TRANSCRIBE_PYTHON
elif [ -x "$project_dir/tools/.audio-runtime/venv/bin/python" ]; then
  task_python="$project_dir/tools/.audio-runtime/venv/bin/python"
else
  task_python=python3
fi
exec "$task_python" "$script_dir/transcribe.py" "$@"
