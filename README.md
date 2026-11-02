# GPU Monitor

A local dashboard for NVIDIA GPUs across your SSH servers. Find a free GPU, see
who is using it, and check recent activity—all on one page.

[简体中文](README.zh-CN.md)

![GPU Monitor dashboard](docs/screenshot-en-light.jpg)

## Features

- **Multi-server overview** with free memory, utilization, temperature and power.
- **Recent activity** with 30-second utilization mean, peak and sample coverage,
  plus five-minute trends to help spot short or intermittent workloads.
- **Searchable process list** with owner, PID, GPU memory, command and uptime.
- **Quick GPU selection**: filter by idle, in use or your processes; sort by free
  memory; copy a `CUDA_VISIBLE_DEVICES` command.
- Chinese / English interface, light / dark themes and optional wallpapers.

No Docker, Prometheus or Grafana setup required. Monitoring is read-only and
installs no packages or services on your servers.

## Requirements

| Where | Requirements |
| --- | --- |
| Your computer | Python 3.9+ and OpenSSH. Tested on macOS; Apple Silicon requires Rosetta. Linux is untested; Windows is not supported. |
| GPU servers | Linux, NVIDIA driver, `nvidia-smi` and standard `ps`. Python 3 with `ctypes` is needed for history sampling. |
| Connection | SSH key authentication, with server aliases in `~/.ssh/config`. |

Connect to each server once in a terminal to verify its host key and make sure
`ssh gpu-01 nvidia-smi` works without a password prompt.

## Quick start

From the repository directory:

```sh
python3 scripts/manage.py init
python3 scripts/manage.py start --open
```

`init` discovers GPU servers from your SSH config and asks for confirmation before saving.
The first start downloads the GPU exporter automatically and opens the dashboard,
usually at [127.0.0.1:8765](http://127.0.0.1:8765). Allow a few seconds for data to appear.

To select servers explicitly, use `python3 scripts/manage.py init gpu-01 gpu-02`.
On macOS, you can also double-click **Start.command** / **Stop.command**.

| Action | Command |
| --- | --- |
| Start | `python3 scripts/manage.py start --open` |
| Stop | `python3 scripts/manage.py stop` |
| Check status | `python3 scripts/manage.py status` |
| Add a server | Stop, run `python3 scripts/manage.py init --add gpu-03`, then start again. |

Closing the browser does not stop monitoring. Start the app again after rebooting.

## Configuration

Setup creates a private `config.json`, which is excluded from Git. Most users only
need the following settings; see [config.example.json](config.example.json) for the full template.
Restart the service after editing.

| Setting | Default | Purpose |
| --- | --- | --- |
| `own_users` | `[]` | Set to `["your-username"]` to enable the “mine” filter. |
| `interval_seconds` | `3` | Dashboard collection interval in seconds. |
| `history_points` | `100` | Trend points per GPU; about five minutes at the default interval. |
| `utilization_backend` | `nvml` | History sampling. Set to `exporter` to use only point samples. |
| `port` | `8765` | Local dashboard port; setup selects a free one if needed. |

Theme and language are in the **Appearance** menu. For optional wallpapers, add
`background-light.jpg` and `background-dark.jpg` to `web/assets/`.

## Reading the dashboard

- **Idle** means no compute processes, at most 512 MiB used memory and at most 10%
  utilization. A model holding GPU memory at 0% is still **occupied**.
- **30-second mean / peak** retain recent activity after the latest value returns
  to zero. History sampling falls back to point samples when unavailable.
- **Coverage** shows how much of the 30-second window has valid samples. Missing
  time is excluded from the mean, not counted as zero. Trends reset on restart.
- Offline or stale GPUs are excluded from live statistics. The app does not reserve
  GPUs or schedule jobs; check availability before starting your workload.

The dashboard is for local access only and shows process command lines. Do not
expose it directly to the public internet.

## Troubleshooting

```sh
python3 scripts/manage.py doctor
```

If a server is unreachable, check SSH access, VPN and keys first. Logs are in
`.runtime/monitor.log`, `.runtime/<name>-exporter.log` and `.runtime/<ssh_host>-nvml.log`.

On Apple Silicon, ensure Rosetta is installed. To use your own compatible exporter,
set `GPU_MONITOR_EXPORTER` to its absolute path before starting.

## Development

```sh
python3 -m unittest discover -s tests
node tests/test_ui_logic.cjs
python3 scripts/mock_demo.py  # preview with sample data at http://127.0.0.1:8799
```

See the [live-test record](docs/TEST_REPORT.md) and
[NVML sampling results](docs/NVML_TEST_REPORT.md) for validation and measured overhead.

## License

[MIT](LICENSE). GPU metrics use [nvidia_gpu_exporter](https://github.com/utkuozdemir/nvidia_gpu_exporter);
see [third-party notices](THIRD-PARTY-NOTICES.md).
