"""`sciforge serve` —— 启动 FastAPI 网关 + 内置 SaaS 控制台

把生成内核（QGenAgent / SolveAgent）+ 题库 storage + SaaS 元数据（SaasStore）
组装成统一后端，经 uvicorn 提供服务：

- ``/``         → 重定向到内置控制台 ``/app/``（注册/登录/出题/组卷/推荐/用量）
- ``/v1/*``     → REST API（B2B 用 X-API-Key，C 端用自定义 HMAC 签名 bearer token）
- ``/docs``     → OpenAPI 文档

鉴权/计费默认开启（SaasStore）；``--open`` 切到无鉴权开放模式（仅本地调试）。
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console

from sciforge.cli.display import error
from sciforge.core.models import Discipline

serve_app = typer.Typer(name="serve", help="启动 Web 服务（API + SaaS 控制台）")
console = Console()


@serve_app.callback(invoke_without_command=True)
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址（0.0.0.0 对外）"),
    port: int = typer.Option(8000, "--port", "-p", help="监听端口"),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db", help="题库 SQLite 路径"),
    saas_db: Path = typer.Option(Path("data/saas.db"), "--saas-db", help="SaaS 元数据 DB 路径"),
    conversation_db: Path = typer.Option(
        Path("data/conversations.db"), "--conversation-db", help="Chatbox 会话 DB 路径"
    ),
    runtime_db: Path = typer.Option(
        Path("data/qgen_runtime.db"), "--runtime-db", help="可恢复 Agent run DB 路径"
    ),
    open_mode: bool = typer.Option(False, "--open", help="开放模式：无鉴权/计费（仅本地调试）"),
    llm_config: Path | None = typer.Option(None, "--llm-config", help="LLM 配置文件"),
    cors_origin: list[str] = typer.Option(
        [], "--cors-origin", help="额外允许的 CORS 来源（可多次）；默认 *"
    ),
) -> None:
    """启动统一后端服务。"""
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        error("缺少 uvicorn，请安装 server extra：uv sync --extra server")
        raise typer.Exit(1) from exc

    from sciforge.agents import QGenAgent, SolveAgent
    from sciforge.agents.runtime import SQLiteRunStore
    from sciforge.api.app import create_app
    from sciforge.api.conversations import ConversationStore
    from sciforge.cli.generate import _resolve_llm_config
    from sciforge.config.settings import AppConfig
    from sciforge.llm.client import LLMClient
    from sciforge.llm.config import LLMConfig
    from sciforge.storage.saas import SaasStore
    from sciforge.storage.sqlite_backend import SQLiteStorageBackend

    llm_config = _resolve_llm_config(llm_config)
    app_cfg = AppConfig(llm_config_file=llm_config)

    def task_config(task: str, *, fallback: str) -> LLMConfig:
        effective = task if task in app_cfg.task_llm else fallback
        return LLMConfig.from_app_config(app_cfg, effective)

    # Parse once, instantiate per Agent run.  LLMClient owns mutable usage
    # counters, so sharing one instance across requests would cross-charge
    # concurrent durable runs.
    qgen_configs = {
        "generate": task_config("generate", fallback="generate"),
        "solve": task_config("solve", fallback="generate"),
        "review": task_config("review", fallback="generate"),
        "tag": task_config("tag", fallback="generate"),
        "improve": task_config("improve", fallback="generate"),
    }
    solve_config = task_config("solve", fallback="generate")

    def new_client(config: LLMConfig) -> LLMClient:
        return LLMClient(config.model_copy(deep=True))

    db.parent.mkdir(parents=True, exist_ok=True)
    # 长生命周期检索库（QGen/Solve 读用，不随请求关闭）；WAL 支持并发读
    retrieval_storage = SQLiteStorageBackend(db)

    def qgen_factory(discipline: Discipline) -> QGenAgent:
        return QGenAgent.from_config(
            llm=new_client(qgen_configs["generate"]),
            storage=retrieval_storage,
            discipline=discipline,
            config=app_cfg,
            solve_llm=new_client(qgen_configs["solve"]),
            review_llm=new_client(qgen_configs["review"]),
            tag_llm=new_client(qgen_configs["tag"]),
            evolve_llm=new_client(qgen_configs["improve"]),
        )

    def solve_factory(discipline: Discipline) -> SolveAgent:
        return SolveAgent(
            llm=new_client(solve_config),
            storage=retrieval_storage,
            discipline=discipline,
        )

    # 组卷/推荐端点用完即关，须每次取新连接
    def storage_factory() -> SQLiteStorageBackend:
        return SQLiteStorageBackend(db)

    saas_store = None if open_mode else SaasStore(saas_db)
    conversation_store = ConversationStore(conversation_db)
    run_store = SQLiteRunStore(runtime_db)
    token_secret = os.environ.get(
        "SCIFORGE_TOKEN_SECRET",
        os.environ.get("SCIFORGE_JWT_SECRET", "dev-secret-change-me"),
    )
    if not open_mode and token_secret == "dev-secret-change-me":
        console.print(
            "[yellow]⚠ 使用默认 token 签名密钥（仅限本地）。生产请设置 SCIFORGE_TOKEN_SECRET。[/]"
        )

    app = create_app(
        qgen_factory,
        solve_factory,
        saas_store=saas_store,
        storage_factory=storage_factory,
        conversation_store=conversation_store,
        run_store=run_store,
        jwt_secret=token_secret,
        cors_origins=cors_origin or None,
    )

    def close_service_resources() -> None:
        retrieval_storage.close()
        if saas_store is not None:
            saas_store.close()

    app.router.add_event_handler("shutdown", close_service_resources)

    mode = "开放（无鉴权）" if open_mode else "鉴权+计费"
    console.print(
        f"[bold green]SciForge 服务启动[/] · 模式={mode}\n"
        f"  控制台:  http://{host}:{port}/app/\n"
        f"  API 文档: http://{host}:{port}/docs"
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
