# Cognitive Steering

Cognitive Steering 是面向 Codex 的事件触发式认知纠偏器。它在长程任务中反复注入工作原则，也接受用户对具体输出的纠偏反馈。完整指令要求 Codex 按任务需要决定表达深度、行动和验证范围，并在达到目的时结束。

## 纠偏如何发生

| 时点 | Codex 收到的内容 |
| --- | --- |
| 启动、恢复、分支、清空或压缩会话后 | [完整指令](plugins/cognitive-steering/instruct.md) |
| 用户提交普通消息时 | [简短提醒](plugins/cognitive-steering/reminder.md) |
| 同一会话每完成 10 次可观测的工具调用 | 简短提醒 |
| 用户发送 `checktone` 时 | 完整指令和本轮修订要求 |

发送 `checktone`，让 Codex 按完整指令直接给出上一轮输出的修订。也可指定对象，例如 `checktone README.md`。需要了解修订原因时可以追问。客户端将 `/checktone` 作为普通消息送达时，该写法同样有效。

## 安装

在 Linux 或 macOS 上使用支持插件生命周期 Hook 的本地 Codex，并在 PATH 中提供 Python 3.11 或以上版本。

```sh
codex plugin marketplace add hyperbolica-dev/cognitive-steering
codex plugin add cognitive-steering@hyperbolica-dev
```

GitHub 安装取得远端发布的版本。试用本地源码时，在仓库根目录把第一条命令换成 `codex plugin marketplace add "$(pwd)"`。安装后，在桌面版「设置 → Hooks」或 CLI 的 `/hooks` 信任三个 Hook。随后新建或重新打开会话，加载完整指令。 [Codex 插件命令](https://learn.chatgpt.com/docs/developer-commands#codex-plugin) · [Hook 信任](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks)

Codex 管理插件缓存和 Git 来源快照。插件的自定义指令、计数和回退备份集中在下文的 `PLUGIN_DATA` 目录。使用系统现有的 Python 即可。

### 升级

从 GitHub 安装的用户运行：

```sh
codex plugin marketplace upgrade hyperbolica-dev
codex plugin add cognitive-steering@hyperbolica-dev
codex plugin list --marketplace hyperbolica-dev --json
```

从本地源码安装的用户更新源码后运行后两条命令。按 Codex 提示重新信任变更后的 Hook。升级保留插件数据，自定义指令继续优先读取。要改用新版默认指令，可在同一数据目录备份原来的同名覆盖文件，再把它移出对应的读取路径。

### 交给 Agent 安装

把下文复制给能执行本地终端命令的 Agent：

```text
请在本机安装或升级 https://github.com/Hyperbolica-dev/cognitive-steering。
检查 Codex CLI 和 Python 3.11+，用 codex plugin marketplace list --json 查看现有来源。
首次安装时运行 codex plugin marketplace add hyperbolica-dev/cognitive-steering。
已有 Git 来源时运行 codex plugin marketplace upgrade hyperbolica-dev。已有本地来源时沿用本地源码。
然后运行 codex plugin add cognitive-steering@hyperbolica-dev。
用 codex plugin list --marketplace hyperbolica-dev --json 核对安装版本和启用状态。保留我现有的自定义指令。
如需信任新的 Hook，告诉我在「设置 → Hooks」或 /hooks 操作，再重新打开会话。
```

## 修改指令

`instruct.md` 写完整的工作原则。默认内容强调独立判断、从设计决策反推原始问题，以及按实际风险决定行动和验证范围。`reminder.md` 写希望在长会话中反复提示的一两句话。

在插件的数据目录放入同名 UTF-8 文件，下一次对应的 Hook 会优先读取它。Codex 将该目录传给插件作为 `PLUGIN_DATA`。本地安装的路径可以用下面的命令定位并创建：

```sh
steering_data="${CODEX_HOME:-$HOME/.codex}/plugins/data/cognitive-steering-hyperbolica-dev"
mkdir -p "$steering_data"
printf '%s\n' "$steering_data"
```

保存后，已信任的 Hook 会在对应事件触发时重新读取文件：

- `instruct.md`：在当前会话发送 `checktone`，立即加载新版并修订上一轮输出。恢复会话或压缩上下文时也会加载新版。
- `reminder.md`：发送下一条普通消息即可加载新版。继续调用工具时，下一次累计次数达到 10 的倍数也会加载新版。

两个文件可分别覆盖，每个文件最多 16 KiB。空白覆盖文件暂停对应注入。覆盖文件超限、编码错误或无法读取时，Codex 显示提示并加载有效的默认指令。默认指令异常时，Codex 显示错误并跳过本次注入。

## 本地计数与验证

插件通过 `hookSpecificOutput.additionalContext` 把指令交给 Codex，作为开发者上下文。会话计数只纳入 Codex 投递给 `PostToolUse` 的调用。计数跨轮次、恢复和压缩保留，分支独立计数。插件使用 Python 标准库在本地处理事件，只在 `counters.sqlite3` 中保存累计次数和去重用的标识摘要。项目规则继续由 Codex 读取 `AGENTS.md`。

在仓库根目录运行以下命令可以核对触发、覆盖文件、手动纠偏和并发计数行为：

```sh
python3 -B -m unittest discover -s tests -v
```

## 卸载

移除插件和 Codex 管理的插件缓存：

```sh
codex plugin remove cognitive-steering@hyperbolica-dev
```

卸载保留自定义指令、计数和回退备份，重新安装可以继续使用。如果 `hyperbolica-dev` 来源只用于这一个插件，可以同时移除来源及其 Git 快照：

```sh
codex plugin marketplace remove hyperbolica-dev
```

彻底清除本地数据时，先关闭使用该插件的会话，备份需要保留的文件，再删除这一处数据目录：

```sh
steering_data="${CODEX_HOME:-$HOME/.codex}/plugins/data/cognitive-steering-hyperbolica-dev"
if [ -d "$steering_data" ]; then
  rm -r -- "$steering_data"
fi
```

如需同时清除旧 Hook 的信任记录，关闭 Codex 后，在其 `config.toml` 中删除键名以 `cognitive-steering@hyperbolica-dev:hooks/hooks.json:` 开头的 `[hooks.state."…"]` 段落。
