"use strict";

const GROUPS = [
  ["pii", "Contains personal data"],
  ["injection", "Hidden instructions for the AI"],
  ["safe", "Safe controls"],
];
const MODE_TITLES = {
  pii: "PII guardrail",
  injection: "Prompt-injection guardrail",
  both: "PII + prompt-injection guardrails",
};

const $ = (id) => document.getElementById(id);
const state = { sample: null, upload: null, config: { categories: {}, rails: {} }, busy: false };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

function selectedMode() {
  return document.querySelector('input[name="mode"]:checked').value;
}

function updateSend() {
  const hasImage = Boolean(state.sample || state.upload);
  $("send").disabled = !hasImage || state.busy;
  $("hint").hidden = hasImage;
}

function showError(message) {
  const box = $("error");
  box.textContent = message;
  box.hidden = !message;
}

function setPreview(src, caption) {
  $("preview").src = src;
  $("lightbox-img").src = src;
  $("zoom").hidden = false;
  $("preview-caption").textContent = `${caption} (click to enlarge)`;
}

function wireLightbox() {
  const box = $("lightbox");
  $("zoom").addEventListener("click", () => box.showModal());
  $("lightbox-close").addEventListener("click", () => box.close());
  // A click on the backdrop or the image closes it; Escape closes it natively.
  box.addEventListener("click", () => box.close());
}

function renderGallery(samples) {
  const gallery = $("gallery");
  for (const [group, title] of GROUPS) {
    gallery.append(el("h3", "group-title", title));
    const tiles = el("div", "tiles");
    for (const sample of samples.filter((s) => s.group === group)) {
      const tile = el("button", "tile");
      tile.type = "button";
      tile.setAttribute("aria-pressed", "false");
      const img = el("img");
      img.src = `/samples/${sample.file}`;
      img.alt = "";
      img.loading = "lazy";
      tile.append(img, el("strong", "", sample.title), el("span", "", sample.blurb));
      tile.addEventListener("click", () => selectSample(sample, tile));
      tiles.append(tile);
    }
    gallery.append(tiles);
  }
}

function selectSample(sample, tile) {
  for (const other of document.querySelectorAll(".tile")) other.setAttribute("aria-pressed", "false");
  tile.setAttribute("aria-pressed", "true");
  state.sample = sample.id;
  state.upload = null;
  setPreview(`/samples/${sample.file}`, sample.title);
  updateSend();
}

function describeVerdict(verdict) {
  const rail = state.config.rails[verdict.judge] || verdict.judge;
  if (verdict.verdict === "safe") return `${rail}: safe`;
  const labels = verdict.categories.map((code) => `${code} ${state.config.categories[code] || ""}`.trim());
  return `${rail}: unsafe${labels.length ? " (" + labels.join(", ") + ")" : ""}`;
}

function renderResult(container, title, result, isBaseline) {
  container.className = "card";
  container.replaceChildren(el("h3", "", title));
  const label = result.status === "allowed" && isBaseline ? "Answered" : result.status;
  const head = el("div");
  head.append(
    el("span", `badge ${result.status}`, label.charAt(0).toUpperCase() + label.slice(1)),
    el("span", "meta", `${(result.latency_ms / 1000).toFixed(1)} s`),
  );
  container.append(head);
  if (result.status === "error") {
    container.append(el("p", "answer", result.error || "Unknown error"));
  } else {
    container.append(el("p", "answer", result.answer || ""));
  }
  if (result.verdicts.length) {
    const details = el("details");
    details.append(el("summary", "", "Why"));
    const list = el("ul");
    for (const verdict of result.verdicts) list.append(el("li", "", describeVerdict(verdict)));
    details.append(list);
    container.append(details);
  }
}

function setPlaceholder(container, title, message) {
  container.className = "card empty";
  container.replaceChildren(el("h3", "", title), el("p", "muted", message));
}

async function send() {
  state.busy = true;
  updateSend();
  showError("");
  const mode = selectedMode();
  setPlaceholder($("unprotected"), "Unprotected model", "Running…");
  setPlaceholder($("protected"), `Protected: ${MODE_TITLES[mode] || "none selected"}`, mode === "none" ? "No guardrail selected." : "Running…");
  const body = { prompt: $("prompt").value, mode };
  if (state.sample) body.sample = state.sample;
  else body.image = state.upload;
  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    renderResult($("unprotected"), "Unprotected model", data.unprotected, true);
    if (data.protected) renderResult($("protected"), `Protected: ${MODE_TITLES[mode]}`, data.protected, false);
  } catch (error) {
    showError(error instanceof Error ? error.message : String(error));
    setPlaceholder($("unprotected"), "Unprotected model", "No result.");
    setPlaceholder($("protected"), "Protected by guardrails", "No result.");
  } finally {
    state.busy = false;
    updateSend();
  }
}

function wirePrompt() {
  const preset = $("preset");
  const prompt = $("prompt");
  prompt.value = preset.value;
  preset.addEventListener("change", () => {
    if (preset.value) prompt.value = preset.value;
    else prompt.focus();
  });
  prompt.addEventListener("input", () => {
    const match = [...preset.options].find((option) => option.value === prompt.value);
    preset.value = match ? match.value : "";
  });
}

function wireUpload(config) {
  if (!config.allow_upload) return;
  $("upload-wrap").hidden = false;
  $("upload").addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > config.max_upload_bytes) {
      showError(`Image is larger than ${config.max_upload_bytes} bytes.`);
      return;
    }
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      state.upload = String(reader.result);
      state.sample = null;
      for (const tile of document.querySelectorAll(".tile")) tile.setAttribute("aria-pressed", "false");
      setPreview(state.upload, `Uploaded: ${file.name}`);
      showError("");
      updateSend();
    });
    reader.readAsDataURL(file);
  });
}

async function init() {
  try {
    const [config, samples] = await Promise.all([getJSON("/api/config"), getJSON("/api/samples")]);
    state.config = config;
    $("model").textContent = config.model;
    $("prompt").maxLength = config.max_prompt_chars;
    renderGallery(samples);
    wireUpload(config);
  } catch (error) {
    showError(`Could not load the demo: ${error instanceof Error ? error.message : error}`);
  }
  wirePrompt();
  wireLightbox();
  $("send").addEventListener("click", send);
  updateSend();
}

init();
