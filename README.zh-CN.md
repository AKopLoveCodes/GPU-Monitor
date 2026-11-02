# GPU Monitor

<img src="docs/gpu-monitor-app-icon.png" alt="GPU Monitor icon" width="64">

在本地网页上实时监控所有可通过 SSH 访问的服务器的 NVIDIA GPU，一页总览，选卡只需几秒。

[English docs →](README.md)

![界面截图](docs/screenshot-zh-light.jpg)

无需 Docker、Go、Prometheus 或 Grafana。Python 部分仅使用标准库，网页为无依赖的原生 JavaScript。

## 功能

- **多服务器统一总览**，也可在左侧切换到单台服务器。
- 每张 GPU 显示已用 / 总显存、实际空闲显存、计算利用率、温度、功耗与计算进程数，并附短期趋势线。
- 可按**空闲、使用中、我使用的 GPU**（`own_users`）筛选，按编号、空闲显存或利用率排序。
- **进程表**包含服务器、物理 GPU 编号、用户、PID、GPU 显存、完整命令与运行时长，可按用户、PID 或命令搜索。
- 点击 GPU 卡片查看 UUID、驱动 / CUDA 版本、风扇、功率上限、趋势，并可复制 `CUDA_VISIBLE_DEVICES` 命令。
- **界面语言：简体中文或 English**，在“外观设置”中切换，按浏览器保存，默认简体中文。
- Light / Dark 主题；可选自备背景图。
- 各服务器独立采集，一台慢或失联不会阻塞其他服务器；超过过期窗口的旧数据会标记为过期，并排除出统计与推荐。
- 最近约 5 分钟的趋势保存在内存中，重启服务后重新积累。

## 环境要求

- **macOS**（已实测；Apple Silicon 通过 Rosetta 运行 exporter）。Linux 走同一套命令行路径通常可用，但本版本未实测；不支持 Windows。
- Python 3.9 或更高。Node.js 仅用于运行前端单元测试。
- 到每台被监控服务器的免密 SSH（密钥认证），例如 `~/.ssh/config` 中的别名。应用不会交互式询问密码。
- 被监控主机上 `nvidia-smi` 在 `PATH` 中，且 Linux `ps` 支持
  `-ww -o pid=,user:32=,pcpu=,pmem=,etimes=,args=`（标准 procps）。

对被监控主机只做只读查询：不安装任何东西、不开放远端端口、不启动任何 GPU 负载。

## 快速开始

```sh
git clone <your-repo-url> gpu-monitor && cd gpu-monitor
python3 scripts/manage.py init
```

`init` 会扫描 `~/.ssh/config` 中的具体别名，以只读的 `nvidia-smi` 测试它们，询问一次确认后为实际报告 NVIDIA GPU 的主机创建私有 `config.json`。显示名、SSH 配置中的用户名会自动推导；本地 exporter 端口在运行时自动选择，无需手工分配。若只想测试指定别名：

```sh
python3 scripts/manage.py init gpu-01 gpu-02
```

无人值守环境可追加 `--yes`。除非明确传入 `--force`，`init` 绝不会覆盖已有的 `config.json`。

之后需要添加服务器时，也不必编辑 JSON：停止服务后运行 `python3 scripts/manage.py init --add gpu-03`，它会探测该别名，并只把兼容的 GPU 主机合并进现有配置，其他设置保持不变。

然后双击 **`Start.command`**，或运行：

```sh
python3 scripts/manage.py start --open
```

随后打开命令打印的本地地址（通常是 <http://127.0.0.1:8765>；若该端口被占用，`init` 会选择另一个空闲端口），首次采样需要几秒钟。双击 **`Stop.command`** 或运行 `python3 scripts/manage.py stop` 停止；`status` 查看运行状态。关闭网页不会停止后台采集。当前版本不设置开机启动，重启电脑后需再次启动。

`config.json` 保存本机特有的 SSH 别名，故意不入库；`config.example.json` 是模板。

## 配置项

| 键 | 默认值 | 含义 |
|---|---|---|
| `listen` | `127.0.0.1` | 必须保持回环地址，见“安全模型”。 |
| `port` | `8765` | 网页端口。 |
| `interval_seconds` | `3` | 每台服务器的采集间隔（≥ 1）。 |
| `collection_timeout_seconds` | `10` | 单次采集超时（1–30）。 |
| `history_points` | `100` | 每张 GPU 的内存样本数（2–1200）。 |
| `utilization_backend` | `nvml` | 通过 SSH 获取历史利用率样本；`exporter` 恢复原来的单点采集。 |
| `own_users` | `[]` | 视为“我的”Unix 用户名，驱动“只看我使用的 GPU”筛选。 |
| `servers[].name` | — | 唯一显示名，`[A-Za-z0-9_-]+`。 |
| `servers[].ssh_host` | — | SSH 配置别名或 `user@host`。 |
| `servers[].exporter_port` | 自动 | 可选的旧版覆盖项；新配置无需填写。 |

高级手工配置仍可参考 `config.example.json`。仅当自动发现的结果需要调整时才编辑 `config.json`，改后重启服务生效。

## 采集方式

```text
本地浏览器 127.0.0.1:8765
        ↓
Python 本地服务（快照、短期趋势、进程详情）
        ├─ NVML 历史流 → ssh GPU 主机 → Python ctypes → 驱动采样缓冲区
        ├─ 官方 exporter 127.0.0.1:<自动端口> → ssh gpu-01 → nvidia-smi
        └─ 官方 exporter 127.0.0.1:<自动端口> → ssh gpu-02 → nvidia-smi
```

GPU 利用率默认使用 **NVML 历史样本**：通过一条常驻 SSH 通道，在 Linux GPU 主机上运行仅依赖 Python 标准库的辅助进程，每秒按时间戳增量读取 `nvmlDeviceGetSamples()`，取回两次读取之间的驱动样本。远端需要 Python 3、`ctypes` 和 `libnvidia-ml.so.1`，不安装文件、软件包或系统服务；SSH 通道关闭后进程退出。不支持或数据过期时自动回退到 exporter 单点值，并在页面提示。配置 `"utilization_backend": "exporter"` 可以完全停用此辅助进程。

卡片同时展示最新值、**近 30 秒时间加权均值、峰值和覆盖率**。覆盖率表示整个窗口内具有有效驱动样本区间的时间比例；启动初期和丢失区间为未知，不补 0，均值仅针对有覆盖的时间计算。超过 1.5 秒的驱动样本间隔视为缺口。NVML 可用时，趋势采用每个采集间隔的平均利用率。缓存仅驻留内存，精度仍受驱动原生采样分辨率限制。

显存、温度、功耗及计算进程列表仍来自官方 [nvidia_gpu_exporter](https://github.com/utkuozdemir/nvidia_gpu_exporter) v1.16.0，使用其[远程采集方式](https://github.com/utkuozdemir/nvidia_gpu_exporter/blob/v1.16.0/docs/CONFIGURE.md#remote-scraping)：`scripts/ssh_smi.py` 转发远端命令，并启用 `--collect.compute-apps`。Python 服务另外执行 `ps` 补充用户、CPU 均值、完整命令与运行时长。这是混合采集：保留原有 `nvidia-smi` 查询，额外增加轻量常驻进程。exporter 与 `ps` 共用本应用 `.runtime/` 中的 SSH 控制套接字；NVML 流使用独立常驻连接，避免其卡住时连带阻塞回退采集。密钥继续由 OpenSSH 管理。

实测开销与限制见 [NVML 测试报告](docs/NVML_TEST_REPORT.md)。

exporter 压缩包会在首次启动时由 `scripts/bootstrap.py` 从官方 GitHub Release 下载并做 SHA-256 校验，放入私有且不入库的 `.tools/` 目录。之后启动时会复核压缩包和解出的二进制；下载和替换均为原子操作，中断下载不会成为可执行文件。详见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

## “空闲”的定义

仅当计算进程采集成功、计算进程数为 0、GPU 显存占用不超过 512 MiB 且利用率不超过 10% 时，才记为空闲。**利用率为 0% 但显存被 vLLM 等任务占用的卡仍显示为已占用。** 实际空闲显存直接取自驱动的 `memory.free`，不用“总量 − 已用”代替，因为还有驱动预留。提及已有任务的推荐表示“可考虑共享”，不是资源预留。

## 安全模型

- 网页与所有 exporter 仅监听 `127.0.0.1`；服务拒绝其他 `listen` 取值，并拒绝 `Host` 头不是回环地址的请求，因为页面会展示进程命令行。
- 接口只读：不提供远端命令执行、杀进程或提交任务。
- `.runtime/`（日志、PID 文件、SSH 控制套接字）以 `0700` 权限创建。
- 这是个人 / 课题组工具，请放在自己的机器后面使用，不适合不受信任的网络。

## 外观

- 主题（Light / Dark）与语言（简体中文 / English）在右上角“外观设置”中，按浏览器保存。URL 追加 `?lang=en` 或 `?theme=dark` 可强制指定，截屏时也便于控制。
- 背景图可选且永不入库：把自己的 `background-light.jpg` 与 `background-dark.jpg` 放入 `web/assets/`，不透明度滑块即会出现；没有图片时使用纯色背景。

## 故障排查

先运行完整的本地与远端环境检查：

```sh
python3 scripts/manage.py doctor
```

它会检查本机 SSH 客户端、exporter 平台支持、网页端口可用性，以及每台已配置主机的免密 SSH、`nvidia-smi` 与 `ps` 能力。该命令不会修改配置，也不会下载或安装任何内容。

```sh
ssh -o BatchMode=yes -o ConnectTimeout=6 <ssh_host> nvidia-smi
```

失败时先解决网络 / VPN / 密钥解锁 / SSH 配置问题；应用不会交互式询问密码。新增服务器前，先在终端正常连接一次并核验主机指纹。

日志位于 `.runtime/monitor.log` 与 `.runtime/<name>-exporter.log`。启动脚本能识别本应用已运行的实例，不会重复启动，也不会停止恰好占用端口的其他程序。本地指标端点无响应时，每个采集器只会重启自己启动的 exporter，绝不会停止远端进程；仓库路径过长时会自动使用私有的短 SSH 控制套接字目录，避免 OpenSSH 的路径长度限制。

没有 Rosetta 的 Apple Silicon 无法运行 x86_64 版 exporter；请安装 Rosetta 或自行提供兼容构建。启动前把其绝对路径设为 `GPU_MONITOR_EXPORTER`（例如 `GPU_MONITOR_EXPORTER=/opt/bin/nvidia_gpu_exporter python3 scripts/manage.py start`）。该二进制会以 `--version` 检查，但本项目不会下载或替换它。

## 开发与测试

```sh
python3 -m unittest discover -s tests      # 离线单元测试
node tests/test_ui_logic.cjs               # 离线前端逻辑 + 中英词典一致性测试
python3 scripts/mock_demo.py               # 合成数据预览 :8799，无需 SSH 与 GPU
python3 tests/manual/check_live.py         # 需要已启动的服务与真实 SSH 主机
python3 tests/manual/check_recovery.py     # 暂停一个本地 exporter 验证恢复，始终会恢复
```

`mock_demo.py` 用生成的数据运行真实界面，`docs/` 中的截图即由此产生。

## 限制

SSH / exec 后端无法提供每个进程的瞬时 SM 利用率，也不采集图形进程；CPU 列为 `ps` 给出的进程整个生命周期平均使用率。进程身份与命令受服务器权限限制，查不到时明确显示未知。采集与 `ps` 不是同一时刻的原子快照，进程恰好在两者之间退出时可能短暂缺少详情。长期历史、告警、资源锁定、任务排队与自动调度不属于此版本：多人仍可能同时看到同一张空闲 GPU，启动任务前请以最新状态为准。

## 许可证

MIT — 见 [LICENSE](LICENSE)。第三方组件与署名见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。
