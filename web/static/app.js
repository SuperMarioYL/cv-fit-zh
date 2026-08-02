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
// Free tier default = watermarked. The ¥9.9/投递 unwatermark flow (v0.2) creates
// a paid order via /api/payment and re-exports with its order_token.
let watermark = true;
let paidOrderToken = null;

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
  const willWatermark = watermark && !paidOrderToken;
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
        ...(paidOrderToken ? { order_token: paidOrderToken } : {}),
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
    setStatus(`已下载 ${format.toUpperCase()}${willWatermark ? "（带水印）" : "（无水印）"}。`);
  } catch (e) {
    setStatus("导出失败：" + e.message, true);
  }
}

els.generate.addEventListener("click", generate);
els.dlPdf.addEventListener("click", () => downloadExport("pdf"));
els.dlDocx.addEventListener("click", () => downloadExport("docx"));

// v0.2 — ¥9.9/投递 unwatermark flow: create a paid order via /api/payment,
// poll until paid (dev/free path returns paid immediately), then mark the
// session unwatermarked so the next /api/export carries the order_token.
els.unwatermark.addEventListener("click", async () => {
  setStatus("创建去水印订单…");
  try {
    const create = await fetch("/api/payment", { method: "POST" });
    if (!create.ok) {
      const err = await create.json().catch(() => ({ detail: create.statusText }));
      throw new Error(err.detail || `HTTP ${create.status}`);
    }
    const order = await create.json();
    if (order.code_url) {
      // Real WeChat path: show the QR for the user to scan + pay.
      els.stubDetail.hidden = false;
      els.stubDetail.innerHTML =
        `<p>微信扫码支付 ¥${order.amount_yuan}：扫下方二维码完成支付后，点"下载 PDF"即出无水印版。</p>` +
        `<img src="https://api.qrserver.com/v1/create-qr-code/?size=240x240&data=${encodeURIComponent(order.code_url)}" alt="WeChat Pay QR" width="240" height="240" />`;
      setStatus("微信扫码支付后，点下载即出无水印。轮询支付状态…");
    } else {
      // Dev/free self-host path: auto-paid.
      setStatus(order.dev_mode ? "自托管免费模式：已免水印。" : "订单已支付，可下载无水印版。");
    }
    const token = order.order_token;
    // Poll order status until paid (real path); dev path is already paid.
    for (let i = 0; i < 60; i++) {
      const s = await fetch(`/api/payment/${token}`);
      if (s.ok) {
        const st = await s.json();
        if (st.paid) {
          paidOrderToken = token;
          setStatus("已解锁无水印，点「下载 PDF/docx」即可。");
          return;
        }
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
    setStatus("支付确认超时，请重试。", true);
  } catch (e) {
    setStatus("去水印失败：" + e.message, true);
  }
});
