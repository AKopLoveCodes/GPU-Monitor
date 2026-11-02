# GPU Monitor

<img src="docs/gpu-monitor-app-icon.png" alt="GPU Monitor icon" width="64">

A local, read-only web dashboard that shows the NVIDIA GPUs of all your SSH-reachable
servers on one page, in real time, so picking a device for the next job takes seconds.

[简体中文文档 →](README.zh-CN.md)

![Dashboard screenshot](docs/screenshot-en-light.jpg)

No Docker, Go, Prometheus or Grafana. The Python side uses only the standard library and
the web UI is dependency-free vanilla JavaScript.

## Features

- **One overview for every server**, or drill into a single host from the sidebar.
- Per GPU: used / total memory, actual free memory, compute utilization, temperature,
  power draw and compute process count, plus a short trend line.
- **Filter** by idle, in use, or GPUs you own (via `own_users`), and sort by index, free
  memory or utilization.
- **Process table** with server, physical GPU index, owner, PID, GPU memory, full command
  line and uptime; searchable by user, PID or command.
- Click a GPU for UUID, driver / CUDA version, fan, power limit, trend and a copyable
  `CUDA_VISIBLE_DEVICES` snippet.
- **Interface language: Simplified Chinese or English**, switched from the Appearance
  menu and remembered per browser. Defaults to Simplified Chinese.
- Light / Dark themes. Optional wallpaper images you supply yourself.
- Each server is collected independently; a slow or unreachable host never blocks the
  others. Data older than the staleness window is marked stale and excluded from
  statistics and recommendations.
- Trends for roughly the last five minutes are kept in memory and rebuilt after a restart.

## Requirements

- **macOS** (tested; Apple Silicon runs the exporter through Rosetta). Linux generally
  works through the same command-line path but is untested in this release. Windows is
  not supported.
- Python 3.9 or newer. Node.js is only needed to run the UI unit test.
- Passwordless SSH (key authentication) to every monitored host, e.g. aliases in your
  `~/.ssh/config`. The app never prompts for passwords.
- `nvidia-smi` available in `PATH` on each monitored host, and a Linux `ps` that supports
  `-ww -o pid=,user:32=,pcpu=,pmem=,etimes=,args=` (standard procps).

Monitored hosts are only ever queried read-only: nothing is installed on them, no remote
port is opened, and no GPU workload is started.

## Quick start

```sh
git clone <your-repo-url> gpu-monitor && cd gpu-monitor
python3 scripts/manage.py init
```

`init` discovers concrete aliases in `~/.ssh/config`, tests them with read-only
`nvidia-smi`, asks once for confirmation, and creates a private `config.json` for the
hosts that actually report NVIDIA GPUs. It derives display names and your configured SSH
user automatically; local exporter ports are selected at runtime, so there is nothing to
allocate manually. To test only named aliases, use:

```sh
python3 scripts/manage.py init gpu-01 gpu-02
```

For unattended setup, add `--yes`. `init` never overwrites an existing `config.json`
unless you explicitly supply `--force`.

To add a server later without editing JSON, stop the service and run
`python3 scripts/manage.py init --add gpu-03`; it probes the alias and merges a compatible
GPU host while preserving the rest of the configuration.

Then either double-click **`Start.command`**, or run:

```sh
python3 scripts/manage.py start --open
```

which opens the local URL it prints (normally <http://127.0.0.1:8765>; another free port
is chosen during `init` if that is occupied). The first sample takes a few seconds. Stop with
**`Stop.command`** or `python3 scripts/manage.py stop`; `status` reports the running
instance. Closing the browser does not stop collection. The service is not started at
login; run it again after a reboot.

`config.json` holds machine-specific SSH aliases and is intentionally not committed;
`config.example.json` is the template.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `listen` | `127.0.0.1` | Must stay loopback; see “Security model”. |
| `port` | `8765` | Web UI port. |
| `interval_seconds` | `3` | Collection interval per server (≥ 1). |
| `collection_timeout_seconds` | `10` | Per-collection timeout (1–30). |
| `history_points` | `100` | In-memory samples per GPU (2–1200). |
| `own_users` | `[]` | Unix user names highlighted as yours; powers the “mine” filter. |
| `servers[].name` | — | Unique display name, `[A-Za-z0-9_-]+`. |
| `servers[].ssh_host` | — | SSH config alias or `user@host`. |
| `servers[].exporter_port` | auto | Optional legacy override; new configurations should omit it. |

`config.example.json` remains available for advanced manual setups. Edit `config.json`
only when the automatic discovery result needs changing, then restart the service.

## How collection works

```text
Browser (127.0.0.1:8765)
        ↓
Python local service (snapshot, short-term trends, process details)
        ├─ official exporter 127.0.0.1:<auto port> → ssh gpu-01 → nvidia-smi
        └─ official exporter 127.0.0.1:<auto port> → ssh gpu-02 → nvidia-smi
```

Metrics come from the official [nvidia_gpu_exporter](https://github.com/utkuozdemir/nvidia_gpu_exporter)
v1.16.0 binary via its supported
[remote scraping mode](https://github.com/utkuozdemir/nvidia_gpu_exporter/blob/v1.16.0/docs/CONFIGURE.md#remote-scraping),
with `scripts/ssh_smi.py` as the remote `nvidia-smi` command and `--collect.compute-apps`
enabled. The Python service additionally runs `ps` over the same SSH connection to add
owner, CPU mean, command line and uptime for each GPU process. Reused SSH connections use
app-private control sockets under `.runtime/`; your own terminal sessions and keys are
untouched (keys stay with OpenSSH and are never copied).

The exporter archive is downloaded on first start from the official GitHub release with a
SHA-256 check (`scripts/bootstrap.py`) into the private, untracked `.tools/` directory.
The archive and extracted binary are rechecked on later starts; downloads and replacements
are atomic, so an interrupted download never becomes the executable. See
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

## What “idle” means

A GPU counts as idle only when the compute-process collection succeeded, the compute
process count is 0, GPU memory use is at most 512 MiB and utilization is at most 10%.
**A card holding memory for vLLM or similar at 0% utilization is still shown as occupied.**
Free memory is taken from the driver's `memory.free`, not `total − used`, because of
driver reservations. A recommendation that mentions existing jobs means “could be shared”,
never a reservation.

## Security model

- The web UI and every exporter listen on `127.0.0.1` only; the service refuses any other
  `listen` value and rejects requests whose `Host` header is not the loopback address,
  because pages expose process command lines.
- The API is read-only: there is no remote command execution, no process killing and no
  job submission.
- `.runtime/` (logs, PID file, SSH control sockets) is created with mode `0700`.
- Keep this a personal / lab tool behind your own machine; it is not designed for
  untrusted networks.

## Appearance

- Theme (Light / Dark) and language (简体中文 / English) are in the top-right Appearance
  menu and are stored per browser. Append `?lang=en` or `?theme=dark` to the URL to force
  either, which is also handy for screenshots.
- Wallpapers are optional and never committed: drop your own `background-light.jpg` and
  `background-dark.jpg` into `web/assets/` and the opacity slider appears. Without them
  the UI uses a plain background.

## Troubleshooting

Start with a complete local and remote prerequisite check:

```sh
python3 scripts/manage.py doctor
```

It checks the local SSH client, exporter platform support, dashboard-port availability and
every configured host's passwordless SSH, `nvidia-smi` and `ps` capability. It changes no
configuration and never downloads or installs anything.

```sh
ssh -o BatchMode=yes -o ConnectTimeout=6 <ssh_host> nvidia-smi
```

If that fails, fix networking / VPN / key unlocking / SSH config first; the app never asks
for a password interactively. Verify host fingerprints by connecting once in a terminal
before adding a new server.

Logs live in `.runtime/monitor.log` and `.runtime/<name>-exporter.log`. The start script
recognizes its own running instance and neither double-starts nor stops unrelated
processes that happen to use the same port. Each collector restarts its own exporter after
an unresponsive local metrics endpoint; no remote process is stopped. Long checkout paths
use a private short control-socket directory automatically, avoiding OpenSSH socket-path
limits.

On Apple Silicon without Rosetta the x86_64 exporter build cannot run; install Rosetta or
provide a compatible build yourself. Set `GPU_MONITOR_EXPORTER` to its absolute path before
starting (for example, `GPU_MONITOR_EXPORTER=/opt/bin/nvidia_gpu_exporter python3 scripts/manage.py start`).
The supplied binary is checked with `--version` but is intentionally not downloaded or
replaced by this project.

## Development and tests

```sh
python3 -m unittest discover -s tests      # offline unit tests
node tests/test_ui_logic.cjs               # offline UI logic + i18n parity tests
python3 scripts/mock_demo.py               # synthetic preview at :8799, no SSH or GPU needed
python3 tests/manual/check_live.py         # needs a running service and real SSH hosts
python3 tests/manual/check_recovery.py     # pauses one local exporter, always resumes it
```

`mock_demo.py` serves the real UI with generated data, which is how the screenshots in
`docs/` were produced.

## Limitations

Per-process instantaneous SM utilization and graphics processes are not available through
the SSH / exec backend; the CPU column is the process lifetime average from `ps`. Process
identity depends on server permissions and is shown as unknown when unreadable. Collection
and `ps` are not one atomic snapshot, so a process that exits between them may briefly
lack details. Long-term history, alerts, reservations, queuing and scheduling are out of
scope: two people can see the same idle GPU, so check the latest state before launching.

## License

MIT — see [LICENSE](LICENSE). Third-party components and attribution:
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
