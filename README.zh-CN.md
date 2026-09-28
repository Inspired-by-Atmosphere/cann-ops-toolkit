# cann-ops-toolkit

一组轻依赖的小脚本：**抢到稀缺的加速卡时段 → 无人值守跑完长流水线 → 结果拉回本地 → 释放时段**。
面向按卡时计费的昇腾（Ascend/NPU）云开发环境。

## 先看一眼

```bash
python -m pip install -r requirements.txt      # 只需要 `requests`
export CANNLAB_COOKIE_FILE="$HOME/.config/cannlab/cookie.cookie"
python scripts/cannlab_grab.py --status        # 只读：打印状态，不占卡
```

`--status` 只发一次读请求、不写任何状态——在用调度器跑起来之前，先用它确认 cookie 文件是通的。完整安装、接线与配置表见下面《快速开始》和《配置》两节。

## 为什么需要它

当"资源比任务本身还稀缺"时，能不能干成事取决于三件事：

1. **必须高频轮询** —— 空位几秒就没了；
2. **轮询不能烧资源** —— 环境处于「已关机」态时不消耗卡时，处于「运行」态时才计费；
3. **45 分钟的流水线绝不能塞进定时器里** —— 会被超时杀掉，还会和下一拍撞车。

本工具集就是这三条约束的答案，按"一件事一个进程"拆开：一个**抢位器**、一个**分离式流水线**、
一个**看门狗**、一个**只播报一次的播报器**，以及一个给外部数字用的**变化门控探测器**。

## 结构示意

```
cannlab_grab.py          定时器，每几分钟一次（必须快、必须便宜）
  │  探测空位 → 发一次启动请求 → 只认「RUNNING 且实例号非空」为成功
  └─▶ cannlab_auto.py    分离进程，45+ 分钟（绝不放进定时器）
        配置 ssh ─▶ 上传 ─▶ 远程跑 run_all.sh ─▶ 结果拉回
        ─▶ 本地真校验 ─▶ 释放时段 ─▶ 写结果文件
  │
cannlab_watch.py         定时器 —— 远端一出现 DONE 标记就把结果拉回来
cannlab_report.py        定时器 —— 读结果文件，同一份结果只播报一次
cannlab_stats_probe.py   定时器 —— 对某个外部 JSON 数据源输出确定性快照
cannlab_ssh.py           按需    —— 取连接信息与私钥，写好 ssh config
check_secrets.py         发布前门禁 —— 密钥/隐私/绝对路径扫描器（仅标准库）
```

所有脚本共享的设计原则：

- **默认静默**：没有输出＝没有值得打扰人的事。在定时器的 `no_agent` 模式下，stdout 为空就不发通知。
- **绝不把"受理"当"拿到"**：`200` 只代表请求被接收，不代表资源已分配。
- **白名单状态机**：只有明确认识的状态才发写请求，一切未知/中间态一律不动。
- **凭据不进代码**：全部来自浏览器导出的 cookie 文件 + 环境变量。

## 仓库结构

```
cann-ops-toolkit/
├── README.md / README.zh-CN.md
├── LICENSE                     MIT
├── requirements.txt            requests
├── docs/
│   ├── SANITIZE_LOG.md         发布前的脱敏记录（按类别）
│   └── ascend-cann-field-notes.md   去个人化的方法论笔记
├── examples/
│   ├── upload.sh.example       本地 → 远端同步模板
│   ├── run_all.sh.example      远端 build/smoke/sweep/package 模板
│   └── scheduler.md            如何把各任务接进 cron / 计划任务
└── scripts/
    ├── cannlab_grab.py
    ├── cannlab_auto.py
    ├── cannlab_watch.py
    ├── cannlab_report.py
    ├── cannlab_ssh.py
    ├── cannlab_stats_probe.py
    └── check_secrets.py
```

## 三分钟上手

```bash
# 1. 装依赖（Python 3.9+，只需要 requests）
python -m pip install -r requirements.txt

# 2. 指向你自己的会话（凭据永远不进仓库）
export CANNLAB_COOKIE_FILE="$HOME/.config/cannlab/cookie.cookie"

# 3. 先看再动：只打印状态，不发任何写请求，不占任何时段
python scripts/cannlab_grab.py --status
```

`--status` 只发一次读请求、不写状态文件。确认能打印出预期实例后，按
`examples/scheduler.md` 把各任务接进定时器。

cookie 文件由你自己导出：浏览器登录平台，把某次已认证 API 请求里的
`Cookie:` 头拷进该文件。本工具集从不索要密码，也不保存密码。

## 配置表

全部通过环境变量配置（同名命令行参数优先）。**仓库里不带任何值。**

### 抢位器 `cannlab_grab.py`

| 变量 | 默认值 | 含义 |
|---|---|---|
| `CANNLAB_COOKIE_FILE` | `~/.config/cannlab/gitcode-cookie.cookie` | 浏览器导出的 cookie 文件 |
| `CANNLAB_TOKEN_COOKIE` | `GITCODE_ACCESS_TOKEN` | 携带访问令牌的 cookie 名 |
| `CANNLAB_STATE_FILE` | 与 cookie 文件同目录 | 状态 json（去重） |
| `CANNLAB_API_BASE` | 平台资源接口 | 资源 API 基地址 |
| `CANNLAB_RESOURCE_TYPE` | `cann` | 资源类型查询值 |
| `CANNLAB_TARGET_NAME` | 第一个实例 | 优先选用的 `instance_name` |
| `CANNLAB_AUTO_SCRIPT` | `scripts/cannlab_auto.py` | 抢到后拉起的流水线 |
| `CANNLAB_AUTO_LOG` | 与 cookie 文件同目录 | 分离进程写入的日志 |

参数：`--status`、`--window 7-23`、`--force`、`--reset`、`--stuck-minutes N`。

### 流水线 `cannlab_auto.py`

| 变量 | 默认值 | 含义 |
|---|---|---|
| `CANNLAB_COOKIE_FILE` | 同上 | cookie 文件 |
| `CANNLAB_STATE_FILE` | 与 cookie 文件同目录 | 抢位器写的状态 json |
| `CANNLAB_SECRETS_DIR` | cookie 所在目录 | 锁/日志/结果文件目录 |
| `CANNLAB_API_BASE` | 平台资源接口 | 资源 API 基地址 |
| `CANNLAB_RESOURCE_TYPE` | `cann` | 资源类型查询值 |
| `CANNLAB_WORK_DIR` | `scripts/work` | 本地项目目录（含 `upload.sh`） |
| `CANNLAB_REMOTE_WORK` | `/mnt/workspace/work` | 对应的远端目录 |
| `CANNLAB_SSH_ALIAS` | `cannlab` | 使用的 ssh 主机别名 |
| `CANNLAB_BUDGET_MIN` | `45` | 远端时间预算（分钟） |

参数：`--dry`（只打印计划步骤，什么都不碰）。

### SSH 助手 `cannlab_ssh.py`

| 变量 | 默认值 | 含义 |
|---|---|---|
| `CANNLAB_COOKIE_FILE` | 同上 | cookie 文件 |
| `CANNLAB_TOKEN_COOKIE` | `GITCODE_ACCESS_TOKEN` | 令牌 cookie 名 |
| `CANNLAB_STATE_FILE` | 与 cookie 文件同目录 | 抢位器写的状态 json |
| `CANNLAB_SSH_ALIAS` | `cannlab` | 写进 ssh config 的主机别名 |
| `CANNLAB_SSH_KEY` | `~/.ssh/cannlab_key` | 私钥输出路径 |
| `CANNLAB_SSH_CONFIG` | `~/.ssh/config` | 要更新的 ssh config |
| `CANNLAB_GATEWAY` | 平台开发环境网关 | 开发环境 API 网关 |
| `CANNLAB_SOURCE` | `cannlab` | `source` 查询值 |

参数：`--setup`、`--raw`、`--ssh`、`--run "cmd"`。

### 看门狗 `cannlab_watch.py`

| 变量 | 默认值 | 含义 |
|---|---|---|
| `CANNLAB_SSH_CONFIG` | `~/.ssh/config` | 保存主机信息的 ssh config |
| `CANNLAB_SSH_HOST` | config 里第一个具体 `Host` | 直接指定主机别名 |
| `CANNLAB_REMOTE_WORK` | `/mnt/workspace/work` | 远端工作目录 |
| `CANNLAB_DEST` | `./results` | 结果拉回的本地目录 |
| `CANNLAB_STATE_FILE` | `scripts/cannlab_watch.state` | 运行去重状态文件 |

参数：`--host`、`--ssh-config`、`--remote-work`、`--dest`、`--state`。

### 播报器 `cannlab_report.py`

| 变量 | 默认值 | 含义 |
|---|---|---|
| `CANNLAB_SECRETS_DIR` | 脚本所在目录 | 结果文件与 seen 文件所在目录 |
| `CANNLAB_RESULT_FILE` | `<secrets>/cannlab_auto.result.json` | 流水线写的结果 json |

参数：`--result-file`、`--seen`。

### 变化门控探测器 `cannlab_stats_probe.py`

| 变量 | 默认值 | 含义 |
|---|---|---|
| `CANNLAB_STATS_URL` | *（无，必填）* | JSON 数据接口 |
| `CANNLAB_PROBE_ID_FIELD` | `id` | 唯一 id 所在字段 |
| `CANNLAB_PROBE_ID` | *（无）* | 要跟踪的对象 id |
| `CANNLAB_PROBE_NAME` | *（无）* | id 匹配不上时改用名称匹配 |

参数：`--url`、`--track-id`、`--track-name`、`--id-field`、`--name-field`、
`--score-field`、`--pass-field`、`--cutoff-rank`、`--rank-band`、`--items-band`、
`--cut-band`、`--timeout`。

## 局限

- **平台接口是未公开的。** 默认写死的 API 路径是从某一次浏览器会话里逆推出的，
  不是公开契约，随时可能变。所有 URL 都可覆盖（`CANNLAB_API_BASE`、
  `CANNLAB_GATEWAY`、`CANNLAB_STATS_URL`），且接口变了脚本只会打警告，不会抛栈。
- **只在一套环境上验证过。** 脚本在 Windows 11 + Python 3.14 上针对一套参考部署实测过，
  **未在陌生/干净机器上验证**。逐条未验证项见 `docs/SANITIZE_LOG.md`。
- **只有 cookie 认证，没有 OAuth / API key 路径。** 登录流程一变，本工具集就失效，
  直到你更新 cookie 名（`CANNLAB_TOKEN_COOKIE`）。
- **`cannlab_auto.py` 依赖两个没有以可运行代码形式收录的文件**：
  `$CANNLAB_WORK_DIR/upload.sh` 与远端的 `run_all.sh`。`examples/` 里是说明契约的模板
  （参数、约定的变量、产物布局）；**端到端流水线未从本导出包跑通**。
- **分离进程的参数是 Windows 专有的。** POSIX 上等价做法是 `fork` + `setsid`，本仓库未实现。
- **历史文件无限增长。** 抢位器的状态 json 与播报器的 seen 文件从不清理。
- **没有自带自动化测试。** 验证方式是手动的：每个脚本 `--help` + 流水线干跑。
- **本仓库不做任何 GPU/NPU 计算。** 这些脚本只是驱动远端环境并搬运产物，
  真正的算子/内核工具链在别处。

## 许可

MIT —— 见 [LICENSE](LICENSE)。

## 免责声明

本工具只是自动化**你自己**对一个你有权使用的服务的已认证访问。你有责任遵守该服务的
服务条款、配额规则与可接受使用政策。请勿用它绕过配额、规避访问控制或共享账号。
作者按"现状"提供，不附带任何担保，也不对使用方式承担责任。
