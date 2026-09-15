# MuScriptor 本地环境

已通过 `uv` 创建独立 venv（CUDA 12.6），命令等价于：

```text
.venv\Scripts\python.exe -m muscriptor transcribe <audio> -o <out.mid> --model small --instruments acoustic_piano --device cuda --dtype float16
```

## 首次使用前（必需）

1. 打开并接受许可：https://huggingface.co/MuScriptor/muscriptor-small  
2. 创建 Token：https://huggingface.co/settings/tokens  
3. 复制项目根目录 `.env.example` 为 `.env`，填入：

```text
HF_TOKEN=hf_你的token
```

国内可加：

```text
HF_ENDPOINT=https://hf-mirror.com
```

4. 重启 `start.bat` / `python app.py`

GTX 1650（4GB）请优先用 **small**；medium/large 容易 OOM。
