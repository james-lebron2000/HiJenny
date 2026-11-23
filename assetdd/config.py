"""Runtime configuration (frozen contract, Lead-owned).

All paths are relative to the repository root unless overridden by env vars.
No external services are required for the default offline mode.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("ASSETDD_DATA_DIR", REPO_ROOT / "data"))
DB_PATH = Path(os.getenv("ASSETDD_DB_PATH", DATA_DIR / "assetdd.db"))
WEB_DIR = REPO_ROOT / "web"
RULES_DIR = Path(os.getenv("ASSETDD_RULES_DIR", REPO_ROOT / "assetdd" / "rules"))
SAMPLE_PACKAGE_DIR = REPO_ROOT / "data" / "sample_package"

# ---------------------------------------------------------------------------
# LLM adapter.  Empty key => deterministic rule-based extraction (offline mode).
# Works with any OpenAI-compatible endpoint (vLLM, Azure, DeepSeek, ...).
# ---------------------------------------------------------------------------
LLM_BASE_URL = os.getenv("ASSETDD_LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("ASSETDD_LLM_API_KEY", "")
LLM_MODEL = os.getenv("ASSETDD_LLM_MODEL", "gpt-4o-mini")
LLM_TIMEOUT_S = float(os.getenv("ASSETDD_LLM_TIMEOUT", "120"))
LLM_MAX_PAGES_PER_CALL = int(os.getenv("ASSETDD_LLM_MAX_PAGES", "6"))
LLM_MAX_RETRIES = int(os.getenv("ASSETDD_LLM_RETRIES", "2"))

# "rules" (default, offline) | "hybrid" (rules first, LLM fills gaps) | "llm"
EXTRACT_MODE = os.getenv("ASSETDD_EXTRACT_MODE", "rules").lower()

# Capabilities / limits
MAX_UPLOAD_MB = int(os.getenv("ASSETDD_MAX_UPLOAD_MB", "200"))
MAX_PAGES_PER_DOC = int(os.getenv("ASSETDD_MAX_PAGES", "2500"))
PARSER_VERSION = "parser-1.0.0"
SCHEMA_VERSION = "schema-1.0.0"

# Export / report output
EXPORT_SUBDIR = "exports"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def llm_available() -> bool:
    return bool(LLM_API_KEY) and EXTRACT_MODE in {"hybrid", "llm"}
