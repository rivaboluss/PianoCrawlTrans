# 钢琴扒谱机

本地图形界面：用 **yt-dlp** 下载音频，再用 **Aria-AMT** 或 **MuScriptor** 扒成 MIDI。

## 别人怎么用（发布包）

1. 解压 `钢琴扒谱机-v*.zip`
2. 双击 **`安装环境.bat`**（首次，需联网，时间较长）
3. 编辑 **`.env`**，填入 HuggingFace Token（仅 MuScriptor 需要）
4. 双击 **`启动.bat`**，浏览器打开 http://127.0.0.1:8765

详细说明见 [使用说明.md](使用说明.md)。

## 开发者：打发布包

```bat
powershell -ExecutionPolicy Bypass -File scripts\make_release.ps1
```

产物在 `dist\钢琴扒谱机-v版本号.zip`（**不含** `.env` / 虚拟环境 / 你的音频）。

## 系统要求

- Windows 10/11 x64
- NVIDIA 显卡 + 较新驱动（建议 4GB+ 显存；MuScriptor large 通常不够）
- 首次安装需联网下载 PyTorch 等依赖
