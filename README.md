# Cognitive Steering

长会话里，上下文压缩可能省略早先给出的工作原则中的细节。Cognitive Steering 在固定时点把你选定的推理指令补回 Codex 的上下文。例如，Codex 压缩一段代码审查会话后，会重新收到完整的审查原则。随后每次提交消息，以及该会话每累计完成十次可观测的工具调用时，它会收到一条简短提醒。

## 安装

需要支持插件 Hook 的本地 Codex，以及 PATH 中的 Python 3.11 或以上。以下命令适用于 Linux 和 macOS。从 GitHub 安装：

```sh
codex plugin marketplace add hyperbolica-dev/cognitive-steering
codex plugin add cognitive-steering@hyperbolica-dev
```

已有本地源码时，在本 README 所在目录用 `codex plugin marketplace add "$(pwd)"` 添加本地来源，再运行第二条命令。

在 Codex 桌面打开「设置 → Hooks」，或在 CLI 中输入 `/hooks`，信任 Cognitive Steering 的三个 Hook。随后打开新会话，或重新打开已有会话，以加载完整指令。可用以下命令确认安装和启用状态：

```sh
codex plugin list --marketplace hyperbolica-dev --json
```

安装命令与信任流程见 [Codex 插件安装](https://learn.chatgpt.com/docs/developer-commands#codex-plugin) 和 [Hook 信任设置](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks)。

## 使用自己的指令

参考默认的 [`instruct.md`](plugins/cognitive-steering/instruct.md)，把自己的完整工作原则写进同名文件。把希望反复提示的一两句话写进 `reminder.md`，例如“先核对实际运行路径，再依据证据给出结论”。

本地 Codex 把该插件的数据目录作为 `PLUGIN_DATA` 传给 Hook。在终端运行下面的命令以创建并定位目录，再用编辑器将覆盖文件保存为 UTF-8：

```sh
steering_data="${CODEX_HOME:-$HOME/.codex}/plugins/data/cognitive-steering-hyperbolica-dev"
mkdir -p "$steering_data"
printf '%s\n' "$steering_data"
```

两个文件可独立覆盖，单个文件上限为 8 KiB。下次触发时立即读取新内容。空白覆盖文件暂停对应注入，移走覆盖文件则恢复打包的 [`instruct.md`](plugins/cognitive-steering/instruct.md) 或 [`reminder.md`](plugins/cognitive-steering/reminder.md)。更新分发版默认指令时，修改插件包内的文件并刷新安装。

## 注入时点与本地数据

| Codex Hook | 时点 | 文件 |
| --- | --- | --- |
| `SessionStart` | 启动、恢复、分支、清空和上下文压缩后 | [`instruct.md`](plugins/cognitive-steering/instruct.md) |
| `UserPromptSubmit` | 提交用户提示后、模型处理该轮之前 | `reminder.md` |
| `PostToolUse` | 每个会话的第 10、20、30……次可观测工具调用完成后 | `reminder.md` |

Hook 将文件内容作为开发者上下文交给 Codex。计数跨轮次、恢复和压缩保留，分支独立计数。计数范围是 Codex 投递给 `PostToolUse` 的调用。 [Codex Hook 行为](https://learn.chatgpt.com/docs/hooks)

运行使用 Python 标准库。上述目录中的 `counters.sqlite3` 只保存累计次数和用于会话隔离、重复投递去重的标识摘要。插件不保存对话、工具输入或结果，不修改 `AGENTS.md`，也不发起网络请求。输入、指令文件或数据库出错时，当前注入跳过，诊断仅提示本次注入跳过。

## 测试

在本 README 所在目录运行：

```sh
python3 -B -m unittest discover -s tests -v
```

测试通过清单中的真实命令向 Hook 发送模拟事件，验证注入内容、热更新、触发间隔、并发去重、会话隔离和异常处理。项目采用 Codex 兼容清单 [`.codex-plugin/plugin.json`](plugins/cognitive-steering/.codex-plugin/plugin.json)，本地验证使用 Codex CLI 0.163.0。第一版按固定时点注入，[运行逻辑](plugins/cognitive-steering/hooks/steering.py) 预留了 `drift_feedback` 回调入口。

## 卸载

```sh
codex plugin remove cognitive-steering@hyperbolica-dev
```

如需移除整个来源，再运行 `codex plugin marketplace remove hyperbolica-dev`。自定义指令和计数文件位于前述数据目录，可分别备份或清理。
