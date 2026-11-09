# 算力观察 / Compute Watch

“工作空间 → 算力观察”显示今天、近三天、近一周的个人 GPU 使用量、活跃时长、单卡个人显存峰值、趋势、时间线、小时色带和使用明细。图表点击可筛选明细。支持中英文、明暗主题与窄屏布局。没有实验标签、使用建议、周报或导出功能。

## 安装远端采集进程

实时监控仍按原方式运行。历史采集是显式启用的独立功能，打开网页不会安装软件。

1. 配置免密 SSH、核验主机指纹，并运行 `python3 scripts/manage.py init`。
2. 在 `config.json` 设置 `own_users`。不同服务器的账号可以分别指定：

   ```json
   {"name": "gpu-01", "ssh_host": "gpu-01", "own_users": ["researcher"]}
   ```

   服务器级列表优先于全局列表。列表中的多个账号作为同一个人的身份集合，同卡不重复计时。不要把其他人的账号加入列表。
3. 启动本地服务，在“算力观察”中查看未部署状态与指引，点击单台服务器的“一键部署”或批量部署按钮。部署在后台执行，页面显示进度、错误原因和成功状态，重复点击不会创建重复服务。也可以使用命令行：

   ```sh
   python3 scripts/manage.py agent check gpu-01
   python3 scripts/manage.py agent install gpu-01
   python3 scripts/manage.py agent status gpu-01
   ```

省略服务器参数对配置中的全部服务器操作。支持服务器名称或 SSH 别名。服务器需要 Linux、Python 3.8+（包含 sqlite3/ctypes）、NVIDIA 驱动、可读的 `/proc`、可用的用户级 systemd。无需 pip 包、Docker、数据库服务或额外网络端口。安装使用同一 OpenSSH 配置，支持跳板机与自定义端口。

如果未启用 lingering，安装程序会使用当前 SSH 账号执行 `loginctl --no-ask-password enable-linger 用户名` 并验证结果，不会使用 sudo 或请求密码。权限不足时显示管理员操作指引。只有本次安装开启的 lingering 会在撤回时关闭；原本开启的状态保持不变。不提供可靠性较低的 nohup 后备模式。

安装目录固定为远端 SSH 用户的 `~/.local/share/gpu-monitor/`，服务为 `~/.config/systemd/user/gpu-monitor-agent.service`。配置与数据库权限仅限当前用户。每个远端 Unix 账号只有一份采集进程；多个本地 checkout 可以读取同一份历史。升级保持账号集合不变；修改账号集合需先卸载，防止不同身份的旧历史混合。

```sh
python3 scripts/manage.py agent upgrade gpu-01
# 卸载会停止服务、删除该账号的 GPU Monitor 历史数据库及部署文件，并恢复由该安装改变的 lingering。
python3 scripts/manage.py agent uninstall gpu-01
```

安装前检查 NVML/进程能力；代码按内容哈希发布，切换当前版本后验证心跳，失败回滚。退出浏览器、本地服务或 SSH 不会停止远端 systemd 服务。远端关机、驱动异常、用户服务被停止期间无法采集；重新运行后不会补造缺失记录。

## 数据与准确性

- NVML 初始化一次，默认每秒采集，按单调时钟调度。优先读取驱动缓冲样本的时间加权负载；缓冲接口不支持时尝试整卡点采样，不可用保持空值。
- 进程通过 NVML 枚举，通过 `/proc/PID` 获取所属用户。NVML 或身份读取失败时视为未知，不判断空闲。进程消失的竞态最多影响当前采样。
- 同一 GPU 上多个本人进程只累计一次使用时间。共享时段仍计入个人占用时间，但不分摊计算贡献。负载始终标为使用期间整卡负载；显存只合计本人进程。
- 跨 GPU、跨服务器的活跃时间用每分钟 60 位活动掩码取并集，分辨率为一秒；它是估算值。多服务器时钟需要正常同步。
- “单卡个人显存峰值”取单卡上本人进程显存之和的峰值，不把不同设备、不同时间的峰值相加。
- 历史按分钟存储，不保存 PID、命令行或逐秒快照。时间线/明细由连续分钟记录生成，是 GPU 使用观察区间，不是任务生命周期，不能区分采样之间结束并重启的任务。分钟内部分空缺不会被补算为占用。
- 时间范围按浏览器当地自然日划分，包含今天，最多 168 小时；夏令时切换日可能有 23 或 25 个小时。热力图重复小时会各占一格。边界采用完整分钟，最多少于一分钟。
- 覆盖率按所选时间范围中已知身份的 GPU·秒 / 已记录设备数对应的预期 GPU·秒计算；新增/移除设备会影响这一参考值。采样覆盖不完整时，用量是已观察到的部分。

## 保留与空间

数据仅存远端 SQLite，本地只有进程内缓存。写入和清理每 15 秒执行；重启时先清理，查询与本地缓存也限制最近 168 小时。为严格避免保留超龄样本，删除跨越最旧边界的整个分钟。Agent 停止时无法物理执行删除，重新启动立即清理；查询不会返回过期记录。

SQLite 使用回滚日志、短事务与增量回收，不产生长期增长的 WAL。数据库设置约 64 MiB 页数上限，接近阈值时提前清理最旧的一天，因此极大规模下可能不足一周。数据目录所在磁盘必须有剩余空间；SQLite 日志和发布代码占少量额外空间，64 MiB 不是整个目录的硬配额。

测试中的一张卡连续使用一周，包含 10,080 条 GPU 聚合和 10,080 条全局活动记录，数据库约 **0.72 MiB**。不同卡数和实际数值影响页布局；多卡共享同一份全局活动记录，不能简单按这一数字推算严格上限。

常规数据库写入最多缓冲 15 秒；异常断电可能损失尚未提交的这部分记录。SSH 每 30 秒增量同步（首次读取一周），断线后自动重试并补取仍在保留范围内的数据。连接失败时明确标注缓存，服务状态与 SSH 可达性分别表达。

## 开发验证

```sh
python3 -m unittest discover -s tests -v
node tests/test_ui_logic.cjs
node tests/test_usage_ui.cjs
python3 scripts/mock_demo.py
```

演示服务在 8799 端口提供明确的合成数据，仅供界面预览；主服务的 `/api/usage` 只返回远端采集数据或真实的未安装/无法连接状态。

参考：[NVIDIA NVML 进程结构](https://docs.nvidia.com/deploy/nvml-api/latest/api/structnvmlProcessInfo__t.html)、[systemd lingering](https://www.freedesktop.org/software/systemd/man/258/loginctl.html)。

## 清空数据与撤回部署

两个脚本都从本地读取 `config.json`，必须明确选择 `--servers` 或 `--all`，不需要逐台登录。删除仅限当前 SSH 用户的 GPU Monitor 数据，不会操作训练进程或其他项目。

```sh
# 清空所选服务器历史，保留采集程序；会停止采集、删除数据库、重新启动并验证写入。
python3 scripts/reset_usage.py --servers Research Research46
# 清空全部配置服务器历史。
python3 scripts/reset_usage.py --all

# 撤回所选服务器部署，删除程序、服务与数据，恢复本安装改变的 lingering。
python3 scripts/rollback_agents.py --servers Research
# 一次性撤回全部部署，恢复到可重新点击“一键部署”的状态。
python3 scripts/rollback_agents.py --all
```

脚本会通知正在运行的本地服务清除对应服务器的内存缓存，防止已删除的数据继续显示。未部署的服务器也能安全重复执行撤回。首次安装失败会回滚程序、服务和由安装改变的 lingering；不会把失败报告为成功。

本地服务更新后执行 `python3 scripts/manage.py start`，会检测 Python 源码指纹并自动重启旧服务，防止新页面请求旧版后端接口而出现 404。浏览器已有页面需刷新以加载更新后的脚本。
