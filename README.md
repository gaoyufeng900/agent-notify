# agent-notify

Claude Code / Codex 干完活、或者停下来等你批准时，通过 [Bark](https://github.com/Finb/Bark) 推一条通知到 iPhone，**Apple Watch 跟着震一下**。

> Push a Bark notification to your iPhone (and buzz your Apple Watch) when Claude Code or OpenAI Codex finishes a long turn or needs your approval. One Python file, no dependencies.

```
✅ Claude 干完了 · 重构登录模块        用时 6 分 12 秒
🔔 Codex 在等你批准 · api-server       要用 Bash
✅ [老Mac] Codex 干完了 · data-pipeline  用时 23 分 4 秒
```

## 特点

- **Claude Code 和 Codex 一起管**：同一个脚本，分别挂在两边的钩子上。
- **只在值得的时候打扰你**：一轮用时 ≥ 60 秒才推「干完了」，秒回的小问题不推；等你批准权限、回答问题时立刻推；Claude 的「空闲 60 秒」提醒不推（干完时已经推过了）。
- **不拖慢会话**：钩子 0.1 秒内返回，发送在后台进行；网络不通、配置缺失都只写日志，绝不影响 Claude / Codex 本身。
- **默认不外发内容**：只推会话名和用时。会话名优先用 Claude 桌面版侧边栏里的标题，没有就用项目目录名。想在通知里看到回复摘要，可以打开 `preview`。
- **不碰 Codex 的 `notify` 配置**：走 Codex 的 hooks 机制，`notify` 那一行（Computer Use 等功能要用）原样保留。
- **多台电脑**：给每台设个 `tag`，标题里会显示是哪台。
- 单文件、只用 Python 标准库，Python 3.8+，macOS / Linux。

## 安装

1. iPhone 装 [Bark](https://apps.apple.com/app/bark-customed-notifications/id1403753865)，打开后复制首页上的推送地址（形如 `https://api.day.app/xxxxxxxx`）。
2. 下载脚本并配置：

```bash
mkdir -p ~/.local/bin
curl -fsSL https://raw.githubusercontent.com/gaoyufeng900/agent-notify/main/agent-notify -o ~/.local/bin/agent-notify
chmod +x ~/.local/bin/agent-notify

~/.local/bin/agent-notify setup      # 粘贴 Bark 推送地址，会顺手发一条测试
~/.local/bin/agent-notify install    # 给 Claude Code 和 Codex 装钩子
```

也可以 `git clone` 后直接用仓库里的 `agent-notify`。钩子里记的是脚本的绝对路径，挪动脚本后重跑一次 `install`。

3. **Codex 只运行被信任的钩子**。装完后在 Codex 里用 `/hooks` 信任新加的两条；或者让本工具代为信任：

```bash
~/.local/bin/agent-notify install codex --trust-codex
```

它会通过 `codex app-server` 的 `hooks/list` 读出这两条钩子的当前哈希，写进 `~/.codex/config.toml` 的 `[hooks.state]`，只处理本工具自己的钩子，改之前会备份。Codex 的 hooks 功能需要在 `config.toml` 的 `[features]` 里打开（`hooks = true`），没开的话 `install` 会提醒。

4. Claude Code 新开的会话生效；Codex 新开的会话生效。

### 让 Apple Watch 震

- iPhone 上的 Watch App → 通知 → Bark，打开「镜像 iPhone 提醒」。
- iPhone **锁屏**时通知才会转到手表；正在用手机时只在手机上响。
- 默认用 Bark 的 `timeSensitive` 级别，开着专注模式也能收到（需在专注模式里允许 Bark 的时效性通知）。

## 用法

```bash
agent-notify test        # 发一条测试推送
agent-notify off         # 静音（开会、上课时）
agent-notify on          # 恢复
agent-notify status      # 配置、钩子安装情况、最近日志
agent-notify uninstall   # 卸掉 Claude Code 和 Codex 的钩子（也可只写 claude 或 codex）
```

## 配置

`~/.config/agent-notify/config.json`，所有字段都可省略：

```json
{
  "bark_url": "https://api.day.app/<key>",
  "min_seconds": 60,
  "level_done": "timeSensitive",
  "level_ask": "timeSensitive",
  "preview": false,
  "sound": "",
  "tag": "",
  "group": "",
  "proxy": ""
}
```

| 字段 | 说明 |
| --- | --- |
| `bark_url` | Bark 推送地址，`setup` 会写入。也可以用环境变量 `BARK_URL`，或 `BARK_KEY` + `BARK_SERVER`（自建服务器） |
| `min_seconds` | 一轮用时不到这么多秒，「干完了」就不推 |
| `level_done` / `level_ask` | Bark 中断级别：`passive` / `active` / `timeSensitive` / `critical` |
| `preview` | `true` 时正文附上最后一条回复的前 80 个字（内容会经过 Bark 服务器） |
| `sound` | Bark 铃声名，空为默认 |
| `tag` | 机器名，多台电脑时显示在标题里，例如 `"老Mac"` |
| `group` | Bark 分组，空则按 Claude / Codex 分组 |
| `proxy` | 直连失败后再试的代理，例如 `http://127.0.0.1:7890`；环境变量里的 `HTTPS_PROXY` 也会试 |

日志在 `~/Library/Logs/agent-notify.log`（Linux 为 `~/.local/state/agent-notify/agent-notify.log`），每次推送或跳过都会记一行原因。

## 工作原理

| 工具 | 钩子 | 时机 |
| --- | --- | --- |
| Claude Code | `Stop` | 一轮回复结束 |
| Claude Code | `Notification` | 要你批准权限、回答问题 |
| Codex | `Stop` | 一轮结束 |
| Codex | `PermissionRequest` | 要你批准 |

用时从会话记录（`transcript_path`）里算：Claude 取最后一条由人发出的消息的时间，Codex 取本轮的 `task_started`。算不出来时照样推，正文写「这一轮结束了」。

`install` 只会**追加**自己的钩子，`~/.claude/settings.json`、`~/.codex/hooks.json` 里已有的其他钩子原样保留；每次改文件前都会生成 `.bak-agent-notify-<时间>` 备份。

## 为什么不用现成的

- [code-notify](https://github.com/mylee04/code-notify) 很完整，但只发电脑桌面通知和 Slack / Discord，到不了手表；它还会改写 Codex 的 `notify` 配置。
- 几个基于 Bark 的 Claude 插件只支持 Claude Code，而且每一轮都推，聊天时手表会一直震。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

在临时 HOME 里跑，推送发到本机的假 Bark 服务器，不会动你的真实配置。

## License

MIT
