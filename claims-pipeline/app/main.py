"""App factory. PolicyStore loads (and validates) at startup — a broken policy file
fails the boot, never a claim."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.agents import AgentSet
from app.agents.classifier import DocumentClassifierAgent
from app.agents.consistency import ConsistencyCheckerAgent
from app.agents.prep import DecisionPrepAgent
from app.agents.reader import DocumentReaderAgent
from app.api import router
from app.config import ROOT_DIR, AppConfig, load_config
from app.llm import LLMClient
from app.pipeline import build_pipeline
from app.policy_store import PolicyStore
from app.service import ClaimService
from app.storage import ClaimRepository


def build_agents(config: AppConfig) -> AgentSet:
    """Real LLM-backed agents. The underlying client is lazy, so the app boots
    without an API key; calls then fail per stage design and degrade gracefully."""
    llm = LLMClient(config.llm)
    return AgentSet(
        classifier=DocumentClassifierAgent(llm, config),
        reader=DocumentReaderAgent(llm, config),
        consistency=ConsistencyCheckerAgent(llm, config),
        prep=DecisionPrepAgent(llm, config),
    )


def create_app(config: AppConfig | None = None) -> FastAPI:
    config = config or load_config()
    policy = PolicyStore(config.resolve(config.policy.policy_file))
    repo = ClaimRepository(config.resolve(config.storage.db_path))
    runner = build_pipeline(policy, config, build_agents(config))

    app = FastAPI(title="Claims Pipeline", version="0.1")
    app.state.service = ClaimService(config=config, policy=policy, repo=repo, runner=runner)
    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def malformed_request(request, exc: RequestValidationError):
        errors = [
            {
                "error_code": "MALFORMED_REQUEST",
                "message": f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}",
                "what_to_do_next": "Fix the field and resubmit.",
            }
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"status": "REJECTED_AT_INTAKE", "errors": errors})

    ui_dir = ROOT_DIR / "ui"
    if (ui_dir / "index.html").exists():
        app.mount("/ui", StaticFiles(directory=str(ui_dir), html=True), name="ui")

        @app.get("/", include_in_schema=False)
        def root():
            return RedirectResponse("/ui/")

    return app


app = create_app()
