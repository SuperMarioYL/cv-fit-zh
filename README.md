[English](./README.en.md) · [Website](https://cv-fit-zh.lei6393.com) · [GitHub](https://github.com/SuperMarioYL/cv-fit-zh)

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="./assets/presentation/hero-mobile-dark.svg">
  <source media="(max-width: 600px)" srcset="./assets/presentation/hero-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="./assets/presentation/hero-dark.svg">
  <img src="./assets/presentation/hero-light.svg" width="960" alt="Hero diagram">
</picture>

# cv-fit-zh

**看清你的经历与岗位要求如何对应**

cv-fit-zh 将中文简历与目标 JD 配对，整理输入，可选用模型改写经历条目，并导出带关键词覆盖信息的文档。

## 为什么需要它

调整简历时，容易只改措辞，却没有检查岗位要求是否得到体现。统一的 JDResume 对象同时保存原始经历、改写条目和覆盖结果，便于审阅和导出。

- **保留原始经历** — 每条改写内容保留原文，方便逐项比较。
- **解释覆盖情况** — 分数同时列出已匹配和缺失关键词。
- **导出同一份结果** — PDF 与 DOCX 复用结构化数据，无需再次改写。

## 架构

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="./assets/presentation/architecture-mobile-dark.svg">
  <source media="(max-width: 600px)" srcset="./assets/presentation/architecture-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="./assets/presentation/architecture-dark.svg">
  <img src="./assets/presentation/architecture-light.svg" width="960" alt="Architecture diagram">
</picture>

JD 和简历解析器创建 Pydantic 模型。改写阶段调用已配置的 OpenAI 兼容客户端生成 STAR 条目，不可用时保留原文。当前改写流程使用同一个确定性覆盖匹配器生成分数和高亮。导出器通过 Jinja2、WeasyPrint 或 python-docx 渲染组装后的 JDResume。

| 组件 | 职责 |
| --- | --- |
| `Resume + JD` | text inputs |
| `Parsers` | structured Pydantic models |
| `STAR rewrite` | model or original bullets |
| `Coverage` | substring and alias matching |
| `Document export` | HTML / PDF / DOCX |

## 安装与快速上手

需要 Python 3.12+ 和 uv。PDF 导出还需 WeasyPrint 原生依赖和中文字体；覆盖示例不调用 PDF 渲染。

```bash
git clone https://github.com/SuperMarioYL/cv-fit-zh.git
cd cv-fit-zh
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e .
```

随仓 Python 示例给出全部四个 JD 关键词及两条合成经历，运行 score_coverage_deterministic，不使用凭据或外部模型调用。

```bash
.venv/bin/python examples/presentation_demo.py
```

## 实际运行示例

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="./assets/presentation/process-mobile-dark.svg">
  <source media="(max-width: 600px)" srcset="./assets/presentation/process-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="./assets/presentation/process-dark.svg">
  <img src="./assets/presentation/process-light.svg" width="960" alt="Process diagram">
</picture>

The deterministic example matches 3 of 4 keywords; 0.75 is text coverage, not an ATS score.

```text
{
  "jd": {
    "title": "后端工程师",
    "must_skills": [
      "Python",
      "SQL",
      "Kubernetes"
    ],
    "nice_skills": [
      "Redis"
    ],
    "responsibilities": []
  },
  "resume_bullets": [
    "使用 FastAPI 开发服务，使用 PostgreSQL 保存业务数据。",
    "使用 Redis 缓存查询结果。"
  ]
}
{
  "matched": [
    "Python",
    "SQL",
    "Redis"
  ],
  "missing": [
    "Kubernetes"
  ],
  "score": 0.75
}
Scope: deterministic substring/alias coverage; no LLM rewrite or ATS evaluation.
```

完整命令与输出保存在 [docs/demo-results.json](./docs/demo-results.json). 输入和复现代码均随仓提供。

![已有终端录制](./assets/demo.gif)

保留已有录制供参考；上方文字示例给出当前可复现的操作。

## 用法

打开 http://127.0.0.1:8000 使用源码目录中的界面。POST /api/rewrite 接受 resume_text 和 jd_text，/api/export 将返回的结构化对象导出为 PDF 或 DOCX。解析器和 STAR 模块也提供处理文本文件的 python -m 入口。使用文档前应逐项审阅改写中的事实。

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 配置

DEEPSEEK_API_KEY（或 OPENAI_API_KEY）配置主模型，DASHSCOPE_API_KEY 配置备用模型。CVFIT_PRIMARY_BASE_URL/MODEL 与 CVFIT_FALLBACK_BASE_URL/MODEL 选择端点和模型名。CVFIT_TIMEOUT、CVFIT_TEMPERATURE、CVFIT_MAX_TOKENS 调整调用参数。CVFIT_FREE_QUOTA 控制进程内按 IP 统计的每日改写尝试额度，默认 1。CVFIT_PAYMENT_DISABLED 开启自托管免费导出路径；真实支付需要另行配置微信商户信息和可选 SDK。

## 集成与职责分工

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="./assets/presentation/integrations-mobile-dark.svg">
  <source media="(max-width: 600px)" srcset="./assets/presentation/integrations-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="./assets/presentation/integrations-dark.svg">
  <img src="./assets/presentation/integrations-light.svg" width="960" alt="Integrations diagram">
</picture>

模型用于建议措辞，原始简历仍是事实依据。模板采用简单文档布局，覆盖分数衡量的是工具自身的关键词规则，不能代表招聘系统认证或录用效果提升。

| 路径 | 已实现职责 |
| --- | --- |
| Web / FastAPI | rewrite and export endpoints |
| OpenAI-compatible | optional configured LLM |
| WeasyPrint | PDF from HTML/CSS |
| python-docx | editable DOCX output |
| JSON | shared JDResume data |

## 限制与后续方向

- 模型不可用时使用启发式解析，改写保留原条目；此回退并不是模型生成的 STAR 文案。
- 覆盖是子串和别名匹配，不是语义证明、ATS 厂商评分或面试预测。
- 额度和订单状态保存在进程内。本示例未验证支付、PDF 渲染或生产部署。

已实现解析、可选模型改写、确定性覆盖、文档导出器及本地 API、支付接口。后续方向包括强化原始事实核对和导出可移植性。在线改写质量、ATS 兼容性和支付结算需单独评估。

## 许可与贡献

许可见 [LICENSE](./LICENSE). 反馈问题时请提供最小输入、执行命令和实际输出。
