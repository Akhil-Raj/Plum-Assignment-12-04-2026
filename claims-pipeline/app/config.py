"""Typed config loaded from config.yaml.

Every tunable that is ours (model names, thresholds, timeouts, retries, deduction
sizes, file caps) lives here. Policy rules stay in policy_terms.json — nothing in
this file may encode policy.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

ROOT_DIR = Path(__file__).resolve().parent.parent


class LLMModels(BaseModel):
    classifier: str
    reader: str
    consistency: str
    prep: str
    fraud_assessor: str


class LLMMaxTokens(BaseModel):
    classifier: int = 1500
    reader: int = 8000
    consistency: int = 4000
    prep: int = 5000
    fraud_assessor: int = 2000


class LLMConfig(BaseModel):
    api_key_env: str = "ANTHROPIC_API_KEY"
    timeout_seconds: float = 90.0
    sdk_retries: int = 1
    bad_output_retries: int = 1
    models: LLMModels
    max_tokens: LLMMaxTokens = LLMMaxTokens()


class Thresholds(BaseModel):
    classification_confidence_warn: float = 0.6
    extraction_confidence_warn: float = 0.6
    name_mismatch_stop_confidence: float = 0.6
    prep_mapping_confidence_warn: float = 0.6


class ConfidenceConfig(BaseModel):
    warn_deduction: float = 0.05
    read_failed_deduction: float = 0.15
    skipped_component_deduction: float = 0.25
    fraud_signal_deduction: float = 0.05
    floor: float = 0.05


class FilesConfig(BaseModel):
    max_file_mb: float = 10
    allowed_extensions: list[str] = ["jpg", "jpeg", "png", "pdf"]
    upload_dir: str = "data/uploads"


class StorageConfig(BaseModel):
    db_path: str = "data/claims.db"


class PipelineConfig(BaseModel):
    simulated_failure_stage: str = "consistency_checks"


class PolicyConfig(BaseModel):
    policy_file: str = "policy_terms.json"


class AppConfig(BaseModel):
    llm: LLMConfig
    thresholds: Thresholds = Thresholds()
    confidence: ConfidenceConfig = ConfidenceConfig()
    files: FilesConfig = FilesConfig()
    storage: StorageConfig = StorageConfig()
    pipeline: PipelineConfig = PipelineConfig()
    policy: PolicyConfig = PolicyConfig()

    def resolve(self, path_str: str) -> Path:
        """Resolve a config path relative to the project root."""
        path = Path(path_str)
        return path if path.is_absolute() else ROOT_DIR / path


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or ROOT_DIR / "config.yaml"
    with open(config_path) as f:
        raw = yaml.safe_load(f)
    return AppConfig.model_validate(raw)
