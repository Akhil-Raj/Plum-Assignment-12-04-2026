"""System configuration — the single source of truth for every tunable that is ours:
model names, thresholds, timeouts, retries, confidence deductions, file caps.

No magic numbers may live in logic code; they live here, typed and in one place.
Policy rules are different: they live in policy_terms.json (read via PolicyStore)
and are never duplicated here.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Minimal .env support, no dependency: KEY=VALUE lines from a gitignored
    .env at the project root. Real environment variables always win."""
    env_file = ROOT_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()


class LLMModels(BaseModel):
    # vision classification is fast + cheap and gates the expensive calls
    classifier: str = "claude-sonnet-4-6"
    # full document read: messy handwriting / stamps / regional language need depth
    reader: str = "claude-opus-4-8"
    consistency: str = "claude-opus-4-8"
    prep: str = "claude-opus-4-8"
    fraud_assessor: str = "claude-opus-4-8"


class LLMMaxTokens(BaseModel):
    """Per-response output ceilings (the Messages API requires max_tokens)."""

    classifier: int = 3000
    reader: int = 24000
    consistency: int = 8000
    prep: int = 10000
    fraud_assessor: int = 4000


class LLMConfig(BaseModel):
    api_key_env: str = "ANTHROPIC_API_KEY"
    timeout_seconds: float = 90.0
    sdk_retries: int = 1          # SDK-level retries for connection errors / 429 / 5xx
    bad_output_retries: int = 1   # our retries when a response fails schema validation
    models: LLMModels = LLMModels()
    max_tokens: LLMMaxTokens = LLMMaxTokens()


class Thresholds(BaseModel):
    # below these, a result gets a WARN trace event + confidence deduction
    classification_confidence_warn: float = 0.6
    extraction_confidence_warn: float = 0.6
    prep_mapping_confidence_warn: float = 0.6
    # a patient-mismatch FAIL below this confidence routes to manual review
    # instead of stopping the claim (a blurry name must not bounce a real claim)
    name_mismatch_stop_confidence: float = 0.6


class ConfidenceConfig(BaseModel):
    warn_deduction: float = 0.05               # each WARN event
    read_failed_deduction: float = 0.15        # a document that could not be read at all
    skipped_component_deduction: float = 0.25  # an entire component failed and was skipped
    fraud_signal_deduction: float = 0.05       # sub-threshold fraud signals present...
    fraud_signal_dip_min_score: float = 0.3    # ...but only from this score up — benign
    floor: float = 0.05                        # notes (score near 0) don't dent confidence


class FilesConfig(BaseModel):
    max_file_mb: float = 10
    allowed_extensions: list[str] = ["jpg", "jpeg", "png", "pdf"]
    upload_dir: str = "data/uploads"


class StorageConfig(BaseModel):
    db_path: str = "data/claims.db"


class PipelineConfig(BaseModel):
    # the stage that `simulate_component_failure` breaks (TC011); consistency is the
    # natural target because the claim can still reach a correct decision without it
    simulated_failure_stage: str = "consistency_checks"


class PolicyConfig(BaseModel):
    policy_file: str = "policy_terms.json"


class AppConfig(BaseModel):
    llm: LLMConfig = LLMConfig()
    thresholds: Thresholds = Thresholds()
    confidence: ConfidenceConfig = ConfidenceConfig()
    files: FilesConfig = FilesConfig()
    storage: StorageConfig = StorageConfig()
    pipeline: PipelineConfig = PipelineConfig()
    policy: PolicyConfig = PolicyConfig()

    def resolve(self, path_str: str) -> Path:
        """Resolve a configured path relative to the project root."""
        path = Path(path_str)
        return path if path.is_absolute() else ROOT_DIR / path


def load_config() -> AppConfig:
    return AppConfig()
