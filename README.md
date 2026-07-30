<div align="right"><sub>[English](./README.en.md)&nbsp;&nbsp;⇄&nbsp;&nbsp;<b>简体中文</b></sub></div>

<p align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./assets/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="./assets/hero-light.svg">
  <img src="./assets/hero-light.svg" width="880" alt="cv-fit-zh — 旧简历 + JD → ATS 友好的中文简历 PDF">
</picture>
</p>

<p align="center"><sub>面向中文求职者：把旧简历按岗位 JD 一键改成 ATS 友好的中文简历 PDF，关键词覆盖可视化，按投递计费。</sub></p>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/github/license/SuperMarioYL/cv-fit-zh?color=0071E3&label=license" alt="license"></a>
  <a href="https://github.com/SuperMarioYL/cv-fit-zh/releases"><img src="https://img.shields.io/github/v/release/SuperMarioYL/cv-fit-zh?color=0071E3&label=release" alt="release"></a>
  <a href="https://github.com/SuperMarioYL/cv-fit-zh/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/SuperMarioYL/cv-fit-zh/ci.yml?branch=main&label=ci" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.12-0071E3" alt="python">
</p>

**粘贴旧简历 + 岗位 JD，30 秒生成 ATS 友好、关键词覆盖可视化的中文简历 PDF —— 改写、评分、排版一次交易。**

<h2><img src="https://api.iconify.design/tabler:topology-star-3.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 架构</h2>

<p align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./assets/atlas-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="./assets/atlas-light.svg">
  <img src="./assets/atlas-light.svg" width="880" alt="架构：旧简历+JD → STAR 改写+关键词覆盖 → 中文简历 PDF">
</picture>
</p>

一个 Python 进程，三层：

- **Web 层**（FastAPI + Jinja2）：单页两文本框 + 生成按钮，POST `/api/rewrite` 再 `/api/export`。
- **LLM 工作流层**：DeepSeek-V3 主、Qwen2.5 备（OpenAI 兼容）。JD 解析 → STAR 改写 → 关键词覆盖评分，单次改写约 3 次调用。
- **格式资产层**：中文 ATS 模板（SimSun/思源宋体、2cm 页边距、单栏无表格，不破坏国内 ATS 解析）+ weasyprint PDF / python-docx 兜底。这是护城河。

核心原语是 **JD 对齐简历文档**（`JDResume`）：每条经历 bullet 与其 STAR 改写 + 覆盖的 JD 关键词配对。

## 目录

- [为什么做这个](#为什么做这个)
- [安装与快速开始](#安装与快速开始)
- [用法](#用法)
- [Demo](#demo)
- [配置](#配置)
- [付费与定价](#付费与定价)
- [路线图](#路线图)
- [License](#license)

## 为什么做这个

中文求职者投递前改简历，今天要靠两个工具：把旧简历贴进豆包/ChatGPT 对话改写，拿到一堆散文 bullet —— 没有关键词覆盖分、没有 ATS 友好的中文排版、还要自己拿 Word 或超级简历重新排版。痛点不是"写不出来"，而是"产不出一份 JD 对齐、HR 能读、排版正确的中文简历交付物"。cv-fit-zh 把**改写 + 评分 + 排版**收进一次交易：一份 JD、一份旧简历，30 秒出可投递的 PDF。

> 2026 年 LLM 一次性结构化改写过了可靠门槛（解析 JD + STAR 改写 sub-元/份），career-ops（62k stars）让"JD → 简历改写"成为可被复刻的工作单元，国内 ATS 关键词过滤随投递量收紧——三件事一起到位，这块才成立。

| 维度 | 豆包/ChatGPT 对话 | 超级简历模板站 | career-ops（dev CLI） | **cv-fit-zh** |
|---|---|---|---|---|
| JD 驱动 STAR 语义改写 | ✓ 散文 bullet | 弱 AI 改写 | ✓（英文） | ✓ |
| JD 关键词覆盖可视化 | — | — | — | ✓ |
| 中文 ATS 排版/字体/页边距 | — | ✓ | — | ✓ |
| 即装即用（非开发者） | ✓ | ✓ | — | ✓ |
| 单次出可投递 PDF | — | 需手动填模板 | — | ✓ |

## 安装与快速开始

```bash
git clone https://github.com/SuperMarioYL/cv-fit-zh && cd cv-fit-zh
pip install -e .
# 起服务（配置 DeepSeek/Qwen key 见下；不配也能离线跑解析+覆盖评分）
DEEPSEEK_API_KEY=sk-xxx uvicorn app.main:app
```

打开 `http://localhost:8000`，粘贴旧简历 + JD → 生成 → 下载 PDF。

<details><summary>或用 uv / 虚拟环境</summary>

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[dev]"
uvicorn app.main:app
```

weasyprint 需要系统库 pango/cairo（macOS: `brew install pango cairo`；Ubuntu: `apt install libpango-1.0-0 libcairo2`）。
</details>

## 用法

<h2><img src="https://api.iconify.design/tabler:terminal-2.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 命令行与 API</h2>

**1. CLI 解析 JD（无需 key，离线即可）**

```bash
python -m app.jd.parser --jd examples/sample.jd.txt
# → {"title": "后端开发工程师（Python）", "must_skills": ["Python","SQL","分布式",...]}
```

**2. CLI 全流程：旧简历 + JD → STAR 改写 + 覆盖分**

```bash
python -m app.resume.star --resume examples/sample.resume.txt --jd examples/sample.jd.txt
# → JDResume JSON，含 rewritten[].star 与 coverage.score
```

**3. Web API**（FastAPI，单页 UI 在 `/`）

```bash
# 改写
curl -s localhost:8000/api/rewrite \
  -H 'Content-Type: application/json' \
  -d '{"resume_text":"...","jd_text":"..."}' | jq '.coverage'

# 导出 PDF
curl -s localhost:8000/api/export \
  -H 'Content-Type: application/json' \
  -d '{"jd":...,"resume":...,"rewritten":...,"coverage":...,"format":"pdf","watermark":true}' \
  -o resume.pdf
```

更多示例见 [`examples/`](./examples)。

<h2><img src="https://api.iconify.design/tabler:photo.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Demo</h2>

![demo](assets/demo.gif)

30 秒：粘贴旧简历 + JD → STAR 改写 → 关键词覆盖分 → 下载中文简历 PDF。录屏走 CLI 离线路径（无需 API key），Web UI 同流程。

## 配置

通过环境变量配置，无配置文件即可启动（仅改写需 LLM key，解析/覆盖评分离线可用）：

| 环境变量 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | str | 空 | DeepSeek-V3 主模型 key（或 `OPENAI_API_KEY`） |
| `DASHSCOPE_API_KEY` | str | 空 | Qwen2.5-72b 备用模型 key |
| `CVFIT_PRIMARY_MODEL` | str | `deepseek-chat` | 主模型名 |
| `CVFIT_FALLBACK_MODEL` | str | `qwen-plus` | 备用模型名 |
| `CVFIT_HOST` / `CVFIT_PORT` | str/int | `127.0.0.1` / `8000` | 服务监听 |
| `CVFIT_FREE_QUOTA` | int | `1` | 免费带水印档/天（无 DB，进程内计数） |
| `CVFIT_WATERMARK` | str | `cv-fit-zh 生成` | 免费档水印文字 |
| `CVFIT_TEMPERATURE` | float | `0.3` | LLM 温度 |

## 付费与定价

> 商业版产品，按投递计费。开源核心，自托管完全免费。

- **自托管免费**：本地跑全部功能，无水印，无限制。这是"开源核心"的边界——工具本身免费。
- **¥9.9 / 投递（去水印）**：v0.1 为 stub——本地跑免费 1 次/天带 "cv-fit-zh 生成" 水印；付 ¥9.9 去水印。v0.1 暂走微信转账手动跑通第一笔（证伪付费意愿），真实在线支付在 v0.2 接入。
- **单份经济模型**：单次改写约 3 次 DeepSeek-V3 调用、每次 ~2k tokens，成本 sub-元，与 ¥9.9/投递 之间留 ~10x 毛利。
- **v0.2 路线**：评估 ¥39/月 秋招季月卡（不限次）；真实支付（微信支付扫码）+ 对象存储存去水印 PDF 临时下载链接。

为什么开源核心还收费？收的是"去水印的便利 + 托管交付"，不是功能锁——自托管始终完全免费。

## 路线图

- [x] **m1 周0 需求探针**（V2EX/少数派/小红书 证伪闸门，已通过 → 进入实现）
- [x] **m2 JD + 旧简历结构化解析**（标题/技能/任职要求/经历条目）
- [x] **m3 经历 STAR 改写 + 关键词覆盖评分**（matched/missing/score）
- [x] **m4 中文 ATS 排版 + PDF/docx 导出 + Web UI 串联**（首发版本）
- [ ] v0.2 真实支付（微信支付）+ 去水印自动交付
- [ ] v0.2 JD 自动抓取评估（BOSS/智联/猎聘，反爬/风控待评估）
- [ ] v0.2 多中文排版模板 + 在线轻量编辑
- [ ] v0.3 云托管形态（自托管免费 + 托管付费，非多租户 SaaS）

## License

[MIT](./LICENSE)。欢迎提 [Issue](https://github.com/SuperMarioYL/cv-fit-zh/issues) 或 PR。

<p align="center"><sub><a href="./LICENSE">MIT</a> © 2026 SuperMarioYL</sub></p>
