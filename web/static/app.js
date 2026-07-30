// cv-fit-zh front-end: paste resume + JD → STAR rewrite → PDF/docx download.
// Vanilla JS, no build step. Talks to the FastAPI backend on the same origin.

"use strict";

const els = {
  resume: document.getElementById("resume"),
  jd: document.getElementById("jd"),
  generate: document.getElementById("generate"),
  status: document.getElementById("status"),
  outputCol: document.getElementById("output-col"),
  coverageScore: document.getElementById("coverage-score"),
  matchedKw: document.getElementById("matched-kw"),
  missingKw: document.getElementById("missing-kw"),
  rewrittenList: document.getElementById("rewritten-list"),
  dlPdf: document.getElementById("dl-pdf"),
  dlDocx: document.getElementById("dl-docx"),
  unwatermark: document.getElementById("unwatermark"),
  stubDetail: document.getElementById("stub-detail"),
};

// The last JDResume produced by /api/rewrite; reused by /api/export.
let lastResult = null;
// Free tier default = watermarked. The ¥9.9 unwatermark is a stub (GTM §8).
let watermark = true;

function setStatus(msg, isError) {
  els.status.textContent = msg || "";
  els.status.classList.toggle("error", !!isError);
}

function escapeHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function renderCoverage(coverage) {
  const matched = coverage.matched || [];
  const missing = coverage.missing || [];
  const total = matched.length + missing.length;
  els.coverageScore.textContent = total
    ? `${matched.length}/${total}`
    : "—";
  els.matchedKw.textContent = matched.length ? matched.join("、") : "—";
  els.missingKw.textContent = missing.length ? missing.join("、") : "无";
}

function renderRewritten(rewritten) {
  els.rewrittenList.innerHTML = "";
  if (!rewritten || !rewritten.length) {
    const li = document.createElement("li");
    li.textContent = "未检测到经历条目。";
    els.rewrittenList.appendChild(li);
    return;
  }
  rewritten.forEach((b) => {
    const li = document.createElement("li");
    const text = b.star || b.orig;
    li.textContent = text;
    if (b.covers_keywords && b.covers_keywords.length) {
      const span = document.createElement("span");
      span.className = "covers";
      span.textContent = "覆盖：" + b.covers_keywords.join("、");
      li.appendChild(span);
    }
    els.rewrittenList.appendChild(li);
  });
}

async function generate() {
  const resumeText = els.resume.value.trim();
  const jdText = els.jd.value.trim();
  if (!resumeText || !jdText) {
    setStatus("请同时填写旧简历和 JD 文本。", true);
    return;
  }
  els.generate.disabled = true;
  els.dlPdf.disabled = true;
  els.dlDocx.disabled = true;
  setStatus("改写中… 约 30 秒（解析 JD + STAR 改写 + 关键词覆盖）…");
  const t0 = performance.now();
  try {
    const resp = await fetch("/api/rewrite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resume_text: resumeText, jd_text: jdText }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    lastResult = await resp.json();
    renderCoverage(lastResult.coverage);
    renderRewritten(lastResult.rewritten);
    els.outputCol.hidden = false;
    els.dlPdf.disabled = false;
    els.dlDocx.disabled = false;
    const secs = ((performance.now() - t0) / 1000).toFixed(1);
    setStatus(`完成（${secs}s）· 关键词覆盖 ${lastResult.coverage.matched.length}/${(lastResult.coverage.matched.length + lastResult.coverage.missing.length) || 0}`);
  } catch (e) {
    setStatus("失败：" + e.message, true);
  } finally {
    els.generate.disabled = false;
  }
}

async function downloadExport(format) {
  if (!lastResult) {
    setStatus("请先生成简历。", true);
    return;
  }
  setStatus(`导出 ${format.toUpperCase()} 中…`);
  try {
    const resp = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jd: lastResult.jd,
        resume: lastResult.resume,
        rewritten: lastResult.rewritten,
        coverage: lastResult.coverage,
        format: format,
        watermark: watermark,
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = format === "pdf" ? "resume.pdf" : "resume.docx";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setStatus(`已下载 ${format.toUpperCase()}${watermark ? "（带水印）" : ""}。`);
  } catch (e) {
    setStatus("导出失败：" + e.message, true);
  }
}

els.generate.addEventListener("click", generate);
els.dlPdf.addEventListener("click", () => downloadExport("pdf"));
els.dlDocx.addEventListener("click", () => downloadExport("docx"));

// 去水印 ¥9.9 stub: explain the manual payment path (GTM §8). Does NOT flip
// watermark off automatically — the dev confirms payment then ships the
// unwatermarked file. Real payment lands in v0.2.
els.unwatermark.addEventListener("click", () => {
  els.stubDetail.hidden = !els.stubDetail.hidden;
});
