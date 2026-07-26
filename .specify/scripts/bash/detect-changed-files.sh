#!/usr/bin/env bash
# Shim: the review extension's commands reference this path, but the extension
# ships the script under .specify/extensions/review/scripts/bash/. Delegate
# rather than copy, so the extension's file stays the single source of truth.
# Remove once the review extension resolves its own path.
#
# Invoked through `bash` because the extension ships its copy non-executable.
exec bash "$(dirname "${BASH_SOURCE[0]}")/../../extensions/review/scripts/bash/detect-changed-files.sh" "$@"
