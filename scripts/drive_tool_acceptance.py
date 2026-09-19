"""Drive the real Tool Agent end-to-end acceptance against the live API.

Sets platform + provider environment variables from .env, then invokes
tests.live.run_tool_acceptance.main() and prints the result.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

# Platform (self-hosted API) config.
os.environ.setdefault("AGENT_EVAL_BASE_URL", "http://localhost:18080")
os.environ.setdefault("AGENT_EVAL_PROJECT_ID", "default-project")
os.environ.setdefault(
    "AGENT_EVAL_API_KEY",
    "aek_default-project_zHQJZzQMMTZxDA17YJhX9hYfDgXfDi4DS9aGjUSsGxI",
)

# Provider (real DeepSeek) config already in .env:
#   LIVE_ACCEPTANCE_BASE_URL / LIVE_ACCEPTANCE_API_KEY / LIVE_ACCEPTANCE_CHAT_MODEL

from tests.live.run_tool_acceptance import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
