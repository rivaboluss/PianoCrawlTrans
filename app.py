#!/usr/bin/env python3
"""Local GUI scheduler for yt-dlp + Aria-AMT / MuScriptor piano transcription."""

from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_from_directory,
)

ROOT = Path(__file__).resolve().parent
MUSICDATA = ROOT / "musicdata"
OUTPUT = ROOT / "output"
YT_EXE = ROOT / "yt-dlp_win_x86" / "yt.exe"
ARIA_ROOT = ROOT / "本地aria-amt"
ARIA_PYTHON = ARIA_ROOT / ".venv" / "Scripts" / "python.exe"
ARIA_SCRIPT = ARIA_ROOT / "transcribe_local.py"
MUSCRIPTOR_ROOT = ROOT / "本地muscriptor"
MUSCRIPTOR_PYTHON = MUSCRIPTOR_ROOT / ".venv" / "Scripts" / "python.exe"
MUSCRIPTOR_SCRIPT = MUSCRIPTOR_ROOT / "transcribe_local.py"

AUDIO_EXTS = {".wav", ".mp3", ".mp4", ".flac", ".ogg", ".m4a", ".aac", ".webm"}
MODELS = ("aria-amt", "muscriptor")
MUSCRIPTOR_SIZES = ("small", "medium", "large")

MUSICDATA.mkdir(exist_ok=True)
OUTPUT.mkdir(exist_ok=True)


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE from .env into os.environ (do not override existing)."""
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv(ROOT / ".env")


def _hf_token_present() -> bool:
    if os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN"):
        return True
    for p in (
        Path.home() / ".cache" / "huggingface" / "token",
        Path.home() / ".huggingface" / "token",
    ):
        if p.is_file() and p.stat().st_size > 0:
            return True
    return False


def _subprocess_env() -> dict:
    env = os.environ.copy()
    ffmpeg_dir = ARIA_ROOT / "tools" / "ffmpeg"
    if ffmpeg_dir.is_dir():
        env["PATH"] = str(ffmpeg_dir) + os.pathsep + env.get("PATH", "")
    bundled_hf = ROOT / "huggingface"
    if bundled_hf.is_dir():
        env["HF_HOME"] = str(bundled_hf)
    return env


app = Flask(
    __name__,
    template_folder=str(ROOT / "web" / "templates"),
    static_folder=str(ROOT / "web" / "static"),
)


@dataclass
class Job:
    id: str
    kind: str
    label: str
    status: str = "queued"  # queued | running | done | error
    logs: list[str] = field(default_factory=list)
    result: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    _subscribers: list = field(default_factory=list, repr=False)

    def emit(self, line: str) -> None:
        self.logs.append(line)
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(line)
            except Exception:
                dead.append(q)
        for q in dead:
            self._subscribers.remove(q)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        for line in self.logs:
            q.put(line)
        self._subscribers.append(q)
        return q


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()
_run_lock = threading.Lock()  # serialize heavy GPU / download work


def _safe_name(name: str) -> str:
    name = os.path.basename(name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name.strip(" .") or "unnamed"


def _list_audio() -> list[dict]:
    items = []
    if not MUSICDATA.exists():
        return items
    for p in sorted(MUSICDATA.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            st = p.stat()
            items.append(
                {
                    "name": p.name,
                    "path": str(p),
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                }
            )
    return items


def _list_midi() -> list[dict]:
    items = []
    if not OUTPUT.exists():
        return items
    for p in sorted(OUTPUT.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file() and p.suffix.lower() in {".mid", ".midi"}:
            st = p.stat()
            items.append(
                {
                    "name": p.name,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                }
            )
    return items


def _create_job(kind: str, label: str) -> Job:
    job = Job(id=uuid.uuid4().hex[:10], kind=kind, label=label)
    with _jobs_lock:
        _jobs[job.id] = job
    return job


def _stream_process(job: Job, cmd: list[str], cwd: Path | None = None) -> int:
    job.emit(f"$ {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd or ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_subprocess_env(),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        job.emit(line.rstrip("\n\r"))
    return proc.wait()


def _run_download(job: Job, url: str) -> None:
    job.status = "running"
    try:
        if not YT_EXE.is_file():
            raise FileNotFoundError(f"找不到 yt-dlp: {YT_EXE}")

        out_tpl = str(MUSICDATA / "%(title)s.%(ext)s")
        cmd = [
            str(YT_EXE),
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "0",
            "--no-playlist",
            "--match-filter",
            "duration <= 3h",
            "-o",
            out_tpl,
            url,
        ]
        with _run_lock:
            code = _stream_process(job, cmd, cwd=ROOT)
        if code != 0:
            job.status = "error"
            job.emit(f"[error] yt-dlp 退出码 {code}")
            return
        job.status = "done"
        job.result = {"files": _list_audio()}
        job.emit("[done] 下载完成")
    except Exception as exc:
        job.status = "error"
        job.emit(f"[error] {exc}")


def _run_transcribe_aria(
    job: Job,
    audio_path: Path,
    *,
    normalize: bool = False,
    no_silence_filter: bool = False,
    silence_top_db: float = 45.0,
) -> None:
    if not ARIA_PYTHON.is_file():
        raise FileNotFoundError(f"找不到 Aria venv: {ARIA_PYTHON}")
    if not ARIA_SCRIPT.is_file():
        raise FileNotFoundError(f"找不到转写脚本: {ARIA_SCRIPT}")

    cmd = [
        str(ARIA_PYTHON),
        str(ARIA_SCRIPT),
        str(audio_path),
        "-o",
        str(OUTPUT),
        "--silence-top-db",
        str(silence_top_db),
    ]
    if normalize:
        cmd.append("--normalize")
    if no_silence_filter:
        cmd.append("--no-silence-filter")

    with _run_lock:
        code = _stream_process(job, cmd, cwd=ARIA_ROOT)
    if code != 0:
        raise RuntimeError(f"Aria-AMT 退出码 {code}")


def _run_transcribe_muscriptor(
    job: Job,
    audio_path: Path,
    *,
    model_size: str = "small",
    instruments: str = "acoustic_piano",
) -> Path:
    if not MUSCRIPTOR_PYTHON.is_file():
        raise FileNotFoundError(f"找不到 MuScriptor venv: {MUSCRIPTOR_PYTHON}")
    if not MUSCRIPTOR_SCRIPT.is_file():
        raise FileNotFoundError(f"找不到 MuScriptor 脚本: {MUSCRIPTOR_SCRIPT}")
    if not _hf_token_present():
        raise RuntimeError(
            "未检测到 HuggingFace Token。请接受 "
            "https://huggingface.co/MuScriptor/muscriptor-small 许可，"
            "创建 token 后写入项目根目录 .env（参考 .env.example）"
        )

    size = model_size if model_size in MUSCRIPTOR_SIZES else "small"
    out_path = OUTPUT / f"{audio_path.stem}.muscriptor-{size}.mid"
    cmd = [
        str(MUSCRIPTOR_PYTHON),
        str(MUSCRIPTOR_SCRIPT),
        str(audio_path),
        "-o",
        str(out_path),
        "-m",
        size,
        "--instruments",
        instruments or "acoustic_piano",
        "--device",
        "cuda",
        "--dtype",
        "float16",
    ]
    with _run_lock:
        code = _stream_process(job, cmd, cwd=MUSCRIPTOR_ROOT)
    if code != 0:
        raise RuntimeError(f"MuScriptor 退出码 {code}")
    return out_path


def _run_transcribe(job: Job, audio_path: Path, options: dict) -> None:
    job.status = "running"
    try:
        if not audio_path.is_file():
            raise FileNotFoundError(f"音频不存在: {audio_path}")

        OUTPUT.mkdir(exist_ok=True)
        model = (options.get("model") or "aria-amt").strip().lower()
        if model not in MODELS:
            raise ValueError(f"未知模型: {model}")

        job.emit(f"[info] 模型 = {model}")

        if model == "aria-amt":
            _run_transcribe_aria(
                job,
                audio_path,
                normalize=bool(options.get("normalize")),
                no_silence_filter=bool(options.get("no_silence_filter")),
                silence_top_db=float(options.get("silence_top_db") or 45),
            )
            expected = OUTPUT / f"{audio_path.stem}.mid"
        else:
            expected = _run_transcribe_muscriptor(
                job,
                audio_path,
                model_size=str(options.get("muscriptor_size") or "small"),
                instruments=str(
                    options.get("instruments") or "acoustic_piano"
                ).strip(),
            )

        job.status = "done"
        job.result = {
            "midi": expected.name if expected.is_file() else None,
            "outputs": _list_midi(),
            "model": model,
        }
        job.emit(f"[done] MIDI 已写入 {OUTPUT}")
    except Exception as exc:
        job.status = "error"
        job.emit(f"[error] {exc}")


def _start_thread(target, *args, **kwargs) -> None:
    t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    t.start()


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def api_status():
    return jsonify(
        {
            "yt_dlp": YT_EXE.is_file(),
            "aria": ARIA_PYTHON.is_file() and ARIA_SCRIPT.is_file(),
            "muscriptor": MUSCRIPTOR_PYTHON.is_file() and MUSCRIPTOR_SCRIPT.is_file(),
            "hf_token": _hf_token_present(),
            "models": list(MODELS),
            "muscriptor_sizes": list(MUSCRIPTOR_SIZES),
            "musicdata": str(MUSICDATA),
            "output": str(OUTPUT),
            # backward-compatible fields
            "aria_python": ARIA_PYTHON.is_file(),
            "aria_script": ARIA_SCRIPT.is_file(),
        }
    )


@app.get("/api/files")
def api_files():
    return jsonify({"files": _list_audio()})


@app.get("/api/outputs")
def api_outputs():
    return jsonify({"files": _list_midi()})


@app.post("/api/download")
def api_download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "请填写链接"}), 400
    job = _create_job("download", url)
    _start_thread(_run_download, job, url)
    return jsonify({"job_id": job.id})


@app.post("/api/upload")
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "未选择文件"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "空文件名"}), 400
    name = _safe_name(f.filename)
    if Path(name).suffix.lower() not in AUDIO_EXTS:
        return jsonify(
            {"error": f"不支持的格式，请上传: {', '.join(sorted(AUDIO_EXTS))}"}
        ), 400
    dest = MUSICDATA / name
    f.save(dest)
    return jsonify({"name": dest.name, "files": _list_audio()})


@app.post("/api/transcribe")
def api_transcribe():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "请选择音频文件"}), 400
    audio = MUSICDATA / _safe_name(name)
    if not audio.is_file():
        return jsonify({"error": f"文件不存在: {name}"}), 404

    model = (data.get("model") or "aria-amt").strip().lower()
    if model not in MODELS:
        return jsonify({"error": f"未知模型: {model}"}), 400

    job = _create_job("transcribe", f"{model}:{audio.name}")
    _start_thread(_run_transcribe, job, audio, data)
    return jsonify({"job_id": job.id})


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(
        {
            "id": job.id,
            "kind": job.kind,
            "label": job.label,
            "status": job.status,
            "logs": job.logs,
            "result": job.result,
        }
    )


@app.get("/api/jobs/<job_id>/stream")
def api_job_stream(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "任务不存在"}), 404

    def generate():
        q = job.subscribe()
        while True:
            try:
                line = q.get(timeout=1.0)
                yield f"data: {line}\n\n"
            except queue.Empty:
                if job.status in ("done", "error"):
                    while True:
                        try:
                            line = q.get_nowait()
                            yield f"data: {line}\n\n"
                        except queue.Empty:
                            break
                    yield f"event: status\ndata: {job.status}\n\n"
                    break
                yield ": ping\n\n"

    return app.response_class(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/outputs/<path:filename>")
def api_download_midi(filename: str):
    safe = _safe_name(filename)
    path = OUTPUT / safe
    if not path.is_file():
        return jsonify({"error": "文件不存在"}), 404
    return send_from_directory(OUTPUT, safe, as_attachment=True)


@app.delete("/api/files/<path:filename>")
def api_delete_file(filename: str):
    safe = _safe_name(filename)
    path = MUSICDATA / safe
    if not path.is_file():
        return jsonify({"error": "文件不存在"}), 404
    path.unlink()
    return jsonify({"files": _list_audio()})


if __name__ == "__main__":
    print(f"钢琴扒谱机  →  http://127.0.0.1:8765")
    print(f"音频目录     →  {MUSICDATA}")
    print(f"MIDI 输出    →  {OUTPUT}")
    print(f"MuScriptor   →  {MUSCRIPTOR_PYTHON.is_file()}")
    print(f"HF Token     →  {_hf_token_present()}")
    app.run(host="127.0.0.1", port=8765, debug=False, threaded=True)
