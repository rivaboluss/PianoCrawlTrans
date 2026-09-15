(() => {
  const $ = (sel) => document.querySelector(sel);

  const urlInput = $("#url-input");
  const downloadForm = $("#download-form");
  const downloadBtn = $("#download-btn");
  const fileInput = $("#file-input");
  const dropzone = $("#dropzone");
  const fileList = $("#file-list");
  const outputList = $("#output-list");
  const refreshFiles = $("#refresh-files");
  const refreshOutputs = $("#refresh-outputs");
  const transcribeBtn = $("#transcribe-btn");
  const logView = $("#log-view");
  const jobStatus = $("#job-status");
  const envStatus = $("#env-status");
  const optNormalize = $("#opt-normalize");
  const optNoSilence = $("#opt-no-silence");
  const optSilenceDb = $("#opt-silence-db");
  const ariaOptions = $("#aria-options");
  const muscriptorOptions = $("#muscriptor-options");
  const optMuscriptorSize = $("#opt-muscriptor-size");
  const optInstruments = $("#opt-instruments");

  let selectedName = null;
  let eventSource = null;
  let busy = false;

  function selectedModel() {
    const el = document.querySelector('input[name="model"]:checked');
    return el ? el.value : "aria-amt";
  }

  function syncModelOptions() {
    const m = selectedModel();
    ariaOptions.classList.toggle("hidden", m !== "aria-amt");
    muscriptorOptions.classList.toggle("hidden", m !== "muscriptor");
  }

  document.querySelectorAll('input[name="model"]').forEach((el) => {
    el.addEventListener("change", syncModelOptions);
  });
  syncModelOptions();

  function setBusy(on) {
    busy = on;
    downloadBtn.disabled = on;
    transcribeBtn.disabled = on || !selectedName;
    fileInput.disabled = on;
  }

  function setStatus(text, cls = "") {
    jobStatus.textContent = text;
    jobStatus.className = "status-pill" + (cls ? ` ${cls}` : "");
  }

  function appendLog(line) {
    logView.textContent += (logView.textContent ? "\n" : "") + line;
    logView.scrollTop = logView.scrollHeight;
  }

  function clearLog() {
    logView.textContent = "";
  }

  function formatSize(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }

  function formatTime(ts) {
    try {
      return new Date(ts * 1000).toLocaleString();
    } catch {
      return "";
    }
  }

  async function api(path, opts) {
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  function renderFiles(files) {
    fileList.innerHTML = "";
    for (const f of files) {
      const li = document.createElement("li");
      li.className = "file-item" + (selectedName === f.name ? " selected" : "");
      li.innerHTML = `
        <input type="radio" name="audio" value="${escapeAttr(f.name)}" ${
        selectedName === f.name ? "checked" : ""
      } />
        <div class="meta">
          <span class="name" title="${escapeAttr(f.name)}">${escapeHtml(f.name)}</span>
          <span class="sub">${formatSize(f.size)} · ${formatTime(f.mtime)}</span>
        </div>
        <button type="button" class="btn danger-ghost" data-del="${escapeAttr(f.name)}">删除</button>
      `;
      li.addEventListener("click", (e) => {
        if (e.target.closest("[data-del]")) return;
        selectedName = f.name;
        renderFiles(files);
        transcribeBtn.disabled = busy || !selectedName;
      });
      const del = li.querySelector("[data-del]");
      del.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`删除 ${f.name}？`)) return;
        try {
          const data = await api(`/api/files/${encodeURIComponent(f.name)}`, {
            method: "DELETE",
          });
          if (selectedName === f.name) selectedName = null;
          renderFiles(data.files);
          transcribeBtn.disabled = busy || !selectedName;
        } catch (err) {
          alert(err.message);
        }
      });
      fileList.appendChild(li);
    }
    transcribeBtn.disabled = busy || !selectedName;
  }

  function renderOutputs(files) {
    outputList.innerHTML = "";
    for (const f of files) {
      const li = document.createElement("li");
      li.className = "file-item";
      li.style.cursor = "default";
      li.innerHTML = `
        <span></span>
        <div class="meta">
          <span class="name" title="${escapeAttr(f.name)}">${escapeHtml(f.name)}</span>
          <span class="sub">${formatSize(f.size)} · ${formatTime(f.mtime)}</span>
        </div>
        <a href="/api/outputs/${encodeURIComponent(f.name)}">下载</a>
      `;
      outputList.appendChild(li);
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  async function refreshAll() {
    const [files, outputs] = await Promise.all([
      api("/api/files"),
      api("/api/outputs"),
    ]);
    renderFiles(files.files);
    renderOutputs(outputs.files);
  }

  function watchJob(jobId) {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    clearLog();
    setStatus("运行中", "running");
    setBusy(true);

    eventSource = new EventSource(`/api/jobs/${jobId}/stream`);
    eventSource.onmessage = (ev) => {
      appendLog(ev.data);
    };
    eventSource.addEventListener("status", async (ev) => {
      const st = ev.data;
      if (st === "done") setStatus("完成", "done");
      else if (st === "error") setStatus("失败", "error");
      else setStatus(st);
      eventSource.close();
      eventSource = null;
      setBusy(false);
      await refreshAll();
    });
    eventSource.onerror = async () => {
      // fallback poll if SSE drops
      try {
        const job = await api(`/api/jobs/${jobId}`);
        if (job.logs && !logView.textContent) {
          logView.textContent = job.logs.join("\n");
        }
        if (job.status === "done" || job.status === "error") {
          setStatus(job.status === "done" ? "完成" : "失败", job.status);
          if (eventSource) eventSource.close();
          eventSource = null;
          setBusy(false);
          await refreshAll();
        }
      } catch {
        /* ignore transient */
      }
    };
  }

  downloadForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = urlInput.value.trim();
    if (!url || busy) return;
    try {
      const data = await api("/api/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      watchJob(data.job_id);
    } catch (err) {
      alert(err.message);
    }
  });

  async function uploadFile(file) {
    if (!file || busy) return;
    const fd = new FormData();
    fd.append("file", file);
    setStatus("上传中", "running");
    try {
      const res = await fetch("/api/upload", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "上传失败");
      selectedName = data.name;
      renderFiles(data.files);
      setStatus("已上传", "done");
      appendLog(`[upload] ${data.name}`);
    } catch (err) {
      setStatus("失败", "error");
      alert(err.message);
    }
  }

  fileInput.addEventListener("change", () => {
    const f = fileInput.files && fileInput.files[0];
    uploadFile(f);
    fileInput.value = "";
  });

  ["dragenter", "dragover"].forEach((ev) => {
    dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((ev) => {
    dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
    });
  });
  dropzone.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    uploadFile(f);
  });

  transcribeBtn.addEventListener("click", async () => {
    if (!selectedName || busy) return;
    try {
      const model = selectedModel();
      const payload = {
        name: selectedName,
        model,
      };
      if (model === "aria-amt") {
        payload.normalize = optNormalize.checked;
        payload.no_silence_filter = optNoSilence.checked;
        payload.silence_top_db = Number(optSilenceDb.value) || 45;
      } else {
        payload.muscriptor_size = optMuscriptorSize.value || "small";
        payload.instruments = (optInstruments.value || "acoustic_piano").trim();
      }
      const data = await api("/api/transcribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      watchJob(data.job_id);
    } catch (err) {
      alert(err.message);
    }
  });

  refreshFiles.addEventListener("click", () => refreshAll().catch(alert));
  refreshOutputs.addEventListener("click", () => refreshAll().catch(alert));

  api("/api/status")
    .then((s) => {
      const parts = [];
      parts.push(s.yt_dlp ? "yt-dlp✓" : "yt-dlp✗");
      parts.push(s.aria ? "Aria✓" : "Aria✗");
      parts.push(s.muscriptor ? "MuScriptor✓" : "MuScriptor✗");
      parts.push(s.hf_token ? "HF Token✓" : "HF Token✗（MuScriptor 需写入 .env）");
      envStatus.textContent = `${parts.join(" · ")} · MIDI → output/`;
    })
    .catch(() => {
      envStatus.textContent = "无法连接后端";
    });

  refreshAll().catch((err) => appendLog(`[error] ${err.message}`));
})();
