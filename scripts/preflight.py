#!/usr/bin/env python3
"""Checkout-local entry point for the publication preflight.

Exit 0 means the artifact is publishable. Any nonzero exit means it is not:
1 for a rejected artifact, 2 for rejected or malformed input.
"""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

main = import_module("agent_signage.preflight").main


if __name__ == "__main__":
    raise SystemExit(main())
