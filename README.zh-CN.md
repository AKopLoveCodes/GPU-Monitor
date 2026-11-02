# GPU Monitor

通过 SSH，在本地网页统一查看多台服务器的 NVIDIA GPU。找空闲卡、看谁在使用、了解最近是否有计算活动，一页完成。

[English](README.md)

![GPU Monitor 界面](docs/screenshot-zh-light.jpg)

## 功能

- **多服务器总览**：空闲显存、利用率、温度、功耗。
- **近期活动**：近 30 秒利用率均值、峰值与采样覆盖率，以及近 5 分钟趋势，便于发现短时、间歇任务。
- **进程查询**：用户、PID、GPU 显存、完整命令、运行时长，支持搜索。
- **快速选卡**：按空闲、使用中、我使用的 GPU 筛选，按空闲显存排序，复制 `CUDA_VISIBLE_DEVICES` 命令。
- 中英文界面、明暗主题，可选自定义背景图。

无需配置 Docker、Prometheus 或 Grafana。监控只读，不在服务器上安装软件包或系统服务。

## 环境要求

| 位置 | 要求 |
| --- | --- |
| 本机 | Python 3.9+、OpenSSH。macOS 已实测；Apple Silicon 需要 Rosetta。Linux 尚未实测，不支持 Windows。 |
| GPU 服务器 | Linux、NVIDIA 驱动、`nvidia-smi`、标准 `ps`。历史采样还需要带有 `ctypes` 的 Python 3。 |
| 连接 | 使用 SSH 密钥免密登录，在 `~/.ssh/config` 中配置服务器别名。 |

先在终端连接每台服务器一次，核验主机指纹，并确认 `ssh gpu-01 nvidia-smi` 无需输入密码即可运行。

## 快速开始

在仓库目录运行：

```sh
python3 scripts/manage.py init
python3 scripts/manage.py start --open
```

`init` 从 SSH 配置中发现 GPU 服务器，确认后保存。首次启动会自动下载 GPU 采集工具并打开网页，地址通常为 [127.0.0.1:8765](http://127.0.0.1:8765)，等待几秒即可看到数据。

只想添加指定服务器时，使用 `python3 scripts/manage.py init gpu-01 gpu-02`。
macOS 也可双击 **Start.command** / **Stop.command** 启停。

| 操作 | 命令 |
| --- | --- |
| 启动 | `python3 scripts/manage.py start --open` |
| 停止 | `python3 scripts/manage.py stop` |
| 查看状态 | `python3 scripts/manage.py status` |
| 添加服务器 | 停止服务，运行 `python3 scripts/manage.py init --add gpu-03`，再启动。 |

关闭网页不会停止采集；重启电脑后需要重新启动应用。

## 配置

初始化生成私有的 `config.json`，不会提交到 Git。常用设置如下，完整模板见 [config.example.json](config.example.json)。修改后重启服务生效。

| 设置 | 默认值 | 用途 |
| --- | --- | --- |
| `own_users` | `[]` | 设为 `["你的用户名"]`，启用“只看我使用的 GPU”。 |
| `interval_seconds` | `3` | 网页数据采集间隔，单位秒。 |
| `history_points` | `100` | 每张卡保留的趋势点数，默认约 5 分钟。 |
| `utilization_backend` | `nvml` | 历史采样；改为 `exporter` 可只用单点采样。 |
| `port` | `8765` | 本地网页端口；初始化时会避开已占用端口。 |

主题和语言在右上角“外观设置”中切换。如需背景图，将 `background-light.jpg` 和 `background-dark.jpg` 放入 `web/assets/`。

## 如何读懂数据

- **空闲**：无计算进程，显存占用不超过 512 MiB，利用率不超过 10%。模型仍占用显存时，即使利用率为 0%，也显示为**已占用**。
- **30 秒均值 / 峰值**：最新值回到 0% 后，仍能看到近期计算活动。历史采样不可用时自动回退到单点采样。
- **覆盖率**：近 30 秒中有有效样本的时间比例。缺失时间不计为 0，也不参与均值计算。趋势在重启后重新积累。
- 离线或过期数据不参与实时统计。应用不预留 GPU、不调度任务，启动任务前仍需确认可用性。

网页仅供本机访问，包含进程命令行，请勿直接暴露到公网。

## 故障排查

```sh
python3 scripts/manage.py doctor
```

服务器无法连接时，先检查 SSH、VPN 与密钥。日志位于 `.runtime/monitor.log`、`.runtime/<name>-exporter.log` 和 `.runtime/<ssh_host>-nvml.log`。

Apple Silicon 请确认已安装 Rosetta。如需使用自己的兼容采集工具，启动前将 `GPU_MONITOR_EXPORTER` 设为其绝对路径。

## 开发

```sh
python3 -m unittest discover -s tests
node tests/test_ui_logic.cjs
python3 scripts/mock_demo.py  # 使用示例数据预览：http://127.0.0.1:8799
```

实测结果与采样开销见[测试记录](docs/TEST_REPORT.md)和 [NVML 采样测试](docs/NVML_TEST_REPORT.md)。

## 许可证

[MIT](LICENSE)。GPU 指标使用 [nvidia_gpu_exporter](https://github.com/utkuozdemir/nvidia_gpu_exporter)，第三方署名见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。
