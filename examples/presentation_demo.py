"""Measure local keyword coverage; no model request or resume fabrication."""
import json
from app.jd.parser import ParsedJD
from app.jd.keywords import score_coverage_deterministic
jd = ParsedJD(title="后端工程师", must_skills=["Python", "SQL", "Kubernetes"], nice_skills=["Redis"])
bullets = ["使用 FastAPI 开发服务，使用 PostgreSQL 保存业务数据。", "使用 Redis 缓存查询结果。"]
print(json.dumps({"jd":jd.model_dump(),"resume_bullets":bullets}, ensure_ascii=False, indent=2))
print(json.dumps(score_coverage_deterministic(bullets, jd).model_dump(), ensure_ascii=False, indent=2))
print("Scope: deterministic substring/alias coverage; no LLM rewrite or ATS evaluation.")
