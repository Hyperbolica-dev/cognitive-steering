# Cognitive Steering

Cognitive Steering 是 Codex 的认知纠偏插件。它按会话事件注入工作指令，并响应用户的纠偏反馈。

本文由启用 Cognitive Steering 的 GPT-6.2 Sol 编写及修订。

## 工作机制

| 时点 | 注入内容 |
| --- | --- |
| 会话启动、恢复、分支、清空或压缩后 | [完整指令 `instruct.md`](plugins/cognitive-steering/instruct.md) |
| 提交普通消息时 | [通用提醒 `reminder.md`](plugins/cognitive-steering/reminder.md) |
| Codex 报告工具调用结束时 | [工具后提醒 `post_tool.md`](plugins/cognitive-steering/post_tool.md)。每累计 10 次工具调用，额外注入一次通用提醒。 |
| 发送 `checktone` 时 | 完整指令和修订要求 |

工具调用按会话计数，重复投递只计一次。

`checktone` 修订上一轮回答。`checktone README.md` 指定修订对象。当客户端将 `/checktone` 作为普通消息传递时，该写法同样有效。

## 安装与升级

需要支持 Hook 的本地 Codex 和 Python 3.11+（Linux 或 macOS）。

```sh
codex plugin marketplace add hyperbolica-dev/cognitive-steering
codex plugin add cognitive-steering@hyperbolica-dev
```

从本地源码安装时，在仓库根目录把第一条命令换成 `codex plugin marketplace add "$(pwd)"`。安装后，在「设置 → Hooks」或 `/hooks` 信任三个 Hook，再打开会话。

GitHub 安装的用户先运行 `codex plugin marketplace upgrade hyperbolica-dev`。本地安装的用户先更新源码。然后重新运行 `codex plugin add cognitive-steering@hyperbolica-dev`，按 Codex 提示重新信任 Hook。

交给 Agent 安装或升级时，可复制这段话：

```text
按 https://github.com/Hyperbolica-dev/cognitive-steering 的 README 安装或升级本机插件。
沿用已配置的来源和自定义指令。用 codex plugin list --marketplace hyperbolica-dev --json 核对版本和启用状态，再告诉我需要信任哪些 Hook。
```

## 自定义指令

在以下目录创建同名 UTF-8 文件，每个文件最多 16 KiB：

```text
${CODEX_HOME:-$HOME/.codex}/plugins/data/cognitive-steering-hyperbolica-dev/
```

保存后，发 `checktone` 或恢复会话可加载 `instruct.md`。发下一条普通消息可加载 `reminder.md`。下次工具调用结束时加载 `post_tool.md`。

覆盖文件在升级后也优先于默认指令。清空覆盖文件可暂停对应注入，移走它可恢复默认指令。文件超限、编码错误或无法读取时，Codex 显示提示并尝试加载默认指令。

## 卸载

```sh
codex plugin remove cognitive-steering@hyperbolica-dev
```

这会移除插件和缓存。若 `hyperbolica-dev` 只用于本插件，再运行 `codex plugin marketplace remove hyperbolica-dev` 移除来源。

自定义指令、工具计数和回退备份集中在上面的数据目录。彻底清理时，先关闭相关会话并备份所需文件，再删除该目录。若还要清除信任记录，关闭 Codex 后，在 `config.toml` 中删掉键名以 `cognitive-steering@hyperbolica-dev:hooks/hooks.json:` 开头的 `[hooks.state."…"]` 条目。

## 开发

SQLite 只存计数和标识摘要。测试命令为 `python3 -B -m unittest discover -s tests -v`。

表达示例和输出前校验借鉴 [Caveman](https://github.com/JuliusBrussee/caveman)。
