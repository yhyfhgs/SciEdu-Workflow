"""SciForge HTTP API（P2 FastAPI 网关）

create_app() 工厂注入 QGen/Solve agent，便于测试与资源管理。
注：身份鉴权 / 计费 / 多租户 / 异步批量 / 组卷导出需 storage schema 稳定后接入（P2 后续）。
"""

from sciforge.api.app import create_app

__all__ = ["create_app"]
