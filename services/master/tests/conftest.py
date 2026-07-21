"""Keep Master integration tests isolated from the production background worker."""

from __future__ import annotations

import os

os.environ["ORCHESTRATION_WORKER_ENABLED"] = "false"
