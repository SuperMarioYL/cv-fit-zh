"""Shared test fixtures.

Two key contracts:
  * By default the LLM is treated as UNAVAILABLE so every module runs on its
    deterministic/heuristic fallback path — tests are network-free and stable.
  * The ``mock_llm`` fixture swaps in a fake client with canned JSON responses
    keyed on the prompt, so the LLM-path logic is tested without a real key.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import app.llm.client as llm_client_mod
from app.llm.client import LLMUnavailableError

# --- sample inputs ---------------------------------------------------------

SAMPLE_JD = """招聘岗位：后端开发工程师（Python）

岗位职责：
1. 负责公司核心业务系统的设计与开发
2. 参与高并发场景下的服务架构优化
3. 与前端团队协作完成接口设计与联调

任职要求：
1. 熟悉 Python，至少 3 年后端开发经验
2. 熟悉 SQL 与至少一种关系型数据库（MySQL/PostgreSQL）
3. 熟悉分布式系统设计与微服务架构
4. 熟悉 Redis、消息队列等中间件
5. 具备良好的沟通能力与团队协作精神

加分项：
1. 有大数据处理经验
2. 熟悉 Kubernetes
"""

SAMPLE_RESUME = """张三
13800138000 | zhangsan@example.com

个人简介
3 年后端开发经验，熟悉 Python 与分布式系统。

工作经历
某科技公司 | 后端开发工程师 | 2021.07-至今
- 负责订单系统的开发与维护，日均处理 50w 订单
- 使用 Python + Flask 搭建服务，将接口响应从 800ms 优化到 200ms
- 参与 MySQL 数据库表设计与索引优化

某创业公司 | 初级开发 | 2019.07-2021.06
- 开发内部管理后台，使用 Django
- 编写 SQL 脚本进行数据统计

教育背景
某某大学 | 计算机科学·本科 | 2015-2019
"""


@pytest.fixture
def sample_jd() -> str:
    return SAMPLE_JD


@pytest.fixture
def sample_resume() -> str:
    return SAMPLE_RESUME


# --- offline (default): LLM unavailable → fallback paths ------------------

class _OfflineClient:
    """Stands in for an unconfigured LLMClient: available=False, never called."""

    available = False

    def require_available(self) -> None:
        raise LLMUnavailableError("offline test client — no key")

    def complete_json(self, system: str, user: str) -> dict[str, Any]:  # pragma: no cover
        raise LLMUnavailableError("offline test client — no key")


@pytest.fixture(autouse=True)
def offline_llm():
    """Force every module onto its offline fallback path by default."""
    prev = llm_client_mod._client
    llm_client_mod._client = _OfflineClient()
    try:
        yield
    finally:
        llm_client_mod._client = prev


# --- mock LLM: canned responses keyed on the prompt ------------------------

class _FakeLLMClient:
    """A scripted LLM client: ``complete_json`` returns canned JSON per prompt."""

    available = True

    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str]] = []

    def require_available(self) -> None:
        return None

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        self.calls.append((system, user))
        for marker, resp in self._responses.items():
            if marker in system:
                return resp
        raise AssertionError(f"no canned response for system prompt: {system[:80]!r}")


CANNED_JD = {
    "title": "后端开发工程师（Python）",
    "must_skills": ["Python", "SQL", "分布式", "Redis", "沟通能力"],
    "nice_skills": ["大数据", "Kubernetes"],
    "responsibilities": ["业务系统设计开发", "高并发架构优化", "接口设计联调"],
}

CANNED_RESUME = {
    "basics": {
        "name": "张三",
        "phone": "13800138000",
        "email": "zhangsan@example.com",
        "summary": "3 年后端开发经验，熟悉 Python 与分布式系统。",
    },
    "experience": [
        {
            "title": "后端开发工程师",
            "company": "某科技公司",
            "dates": "2021.07-至今",
            "bullets": [
                "负责订单系统的开发与维护，日均处理 50w 订单",
                "使用 Python + Flask 搭建服务，将接口响应从 800ms 优化到 200ms",
                "参与 MySQL 数据库表设计与索引优化",
            ],
        },
        {
            "title": "初级开发",
            "company": "某创业公司",
            "dates": "2019.07-2021.06",
            "bullets": [
                "开发内部管理后台，使用 Django",
                "编写 SQL 脚本进行数据统计",
            ],
        },
    ],
    "education": [
        {"school": "某某大学", "degree": "计算机科学·本科", "dates": "2015-2019"},
    ],
}

CANNED_STAR = {
    "rewritten": [
        {
            "orig": "负责订单系统的开发与维护，日均处理 50w 订单",
            "star": "主导订单系统设计与开发，支撑日均 50w 订单稳定运行，可用性 99.95%。",
            "covers_keywords": ["分布式", "Redis"],
        },
        {
            "orig": "使用 Python + Flask 搭建服务，将接口响应从 800ms 优化到 200ms",
            "star": "用 Python + Flask 重构核心服务，将 P99 接口响应从 800ms 降至 200ms，吞吐提升 3 倍。",
            "covers_keywords": ["Python", "高并发"],
        },
        {
            "orig": "参与 MySQL 数据库表设计与索引优化",
            "star": "主导 MySQL 表结构与索引优化，慢查询减少 70%，支撑订单库 2 亿行。",
            "covers_keywords": ["SQL"],
        },
        {
            "orig": "开发内部管理后台，使用 Django",
            "star": "独立开发 Django 管理后台，覆盖 30+ 业务表，交付周期缩短 40%。",
            "covers_keywords": [],
        },
        {
            "orig": "编写 SQL 脚本进行数据统计",
            "star": "编写 SQL 统计脚本，日产出 10+ 报表，支撑运营决策。",
            "covers_keywords": ["SQL"],
        },
    ]
}

CANNED_COVERAGE = {
    "covered": {"Python": [1], "SQL": [2, 4], "分布式": [0]},
    "matched": ["Python", "SQL", "分布式"],
    "missing": ["Redis", "沟通能力", "大数据", "Kubernetes"],
    "score": 0.43,
}


def _canned_responses() -> dict[str, dict[str, Any]]:
    return {
        "JD 结构化解析器": CANNED_JD,
        "中文简历结构化解析器": CANNED_RESUME,
        "STAR（情境-任务-行动-结果）": CANNED_STAR,
        "JD 关键词覆盖核对器": CANNED_COVERAGE,
    }


@pytest.fixture
def mock_llm():
    """Install a scripted LLM client; returns the client so tests can assert calls."""
    client = _FakeLLMClient(_canned_responses())
    prev = llm_client_mod._client
    llm_client_mod._client = client
    try:
        yield client
    finally:
        llm_client_mod._client = prev


# --- weasyprint availability (native libs may be absent outside CI) --------

def _weasyprint_ok() -> bool:
    try:
        from weasyprint import HTML

        HTML(string="<p>x</p>").write_pdf()
        return True
    except Exception:  # noqa: BLE001 — any failure means skip, not a test error
        return False


WEASYPRINT_OK = _weasyprint_ok()
skip_no_weasyprint = pytest.mark.skipif(
    not WEASYPRINT_OK, reason="weasyprint native libs (pango/cairo/gobject) unavailable"
)
