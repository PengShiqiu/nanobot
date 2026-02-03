# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在处理此代码库中的代码时提供指导。

## 项目概述

**nanobot** 是一个超轻量级的个人 AI 助手框架（约 4,000 行代码），灵感来源于 Clawdbot。它提供了一个最小化但可扩展的架构，用于构建具有工具调用能力、多通道支持（Telegram、WhatsApp）和定时任务的 AI 代理。

**关键特性：**
- 需要 Python 3.11+
- 全程使用异步/等待（使用 `asyncio`）
- 通过消息总线实现事件驱动架构
- 通过统一接口支持多 LLM 提供商
- 可扩展的工具和技能系统

## 开发命令

**安装：**
```bash
# Development install (editable mode)
pip install -e .

# With uv (recommended)
uv pip install -e .
```

**常见缺失的依赖：**
- `lark-oapi` - 飞书通道必需
- `loguru` - 日志库
- `pydantic` - 配置验证

**运行：**
```bash
# Initialize config and workspace
nanobot onboard

# Direct agent interaction
nanobot agent -m "message"

# Interactive chat mode
nanobot agent

# Start gateway (runs all enabled channels)
nanobot gateway

# Show system status
nanobot status

# Channel management
nanobot channels status
nanobot channels login  # WhatsApp QR linking
```

**定时任务（Cron）：**
```bash
nanobot cron add --name "daily" --message "Good morning!" --cron "0 9 * * *"
nanobot cron add --name "hourly" --message "Check status" --every 3600
nanobot cron list
nanobot cron remove <job_id>
```

**代码质量：**
```bash
# Lint and format (line-length: 100)
ruff check nanobot/
ruff format nanobot/

# Run tests
pytest
```

## 架构概述

系统遵循事件驱动架构，具有清晰的关注点分离：

### 核心组件

1. **代理循环** (`nanobot/agent/loop.py`)
   - 编排整个流程的中央处理引擎
   - 接收消息 → 构建上下文 → 调用 LLM → 执行工具 → 响应
   - 管理迭代限制和对话状态

2. **消息总线** (`nanobot/bus/`)
   - 用于组件间通信的解耦事件系统
   - `InboundMessage` 流向代理循环
   - `OutboundMessage` 流回通道
   - 使用 `asyncio.Queue` 进行消息传递

3. **提供商系统** (`nanobot/providers/`)
   - `LLMProvider` 抽象基类定义接口
   - `LiteLLMProvider` 封装 litellm 库以支持多提供商
   - 支持：OpenRouter、Anthropic、OpenAI、Gemini、Groq、vLLM 以及任何 OpenAI 兼容的端点
   - 配置：`providers.{provider_name}.apiKey` 和可选的 `apiBase`

4. **工具注册表** (`nanobot/agent/tools/`)
   - 工具通过 `ToolRegistry` 注册并暴露给 LLM
   - 每个工具为其函数调用定义 JSON 模式
   - 内置工具：读取、写入、编辑文件、shell 执行、网络搜索/获取
   - `SpawnTool` 创建后台子代理以执行并行任务
   - `MessageTool` 通过配置的通道发送消息

5. **上下文构建器** (`nanobot/agent/context.py`)
   - 从多个来源构建提示：
     - 来自 `workspace/SOUL.md` 的系统提示
     - 来自 `workspace/USER.md` 的用户上下文
     - 来自 `workspace/AGENTS.md` 的代理定义
     - 来自 `workspace/MEMORY.md` 的持久化记忆
     - 来自 `nanobot/skills/**/*.md` 的技能定义
     - 通过 `SessionManager` 的对话历史

6. **会话管理** (`nanobot/session/`)
   - `SessionManager` 维护每个通道/用户的对话历史
   - 将会话存储在磁盘上以实现持久化
   - 为多轮对话提供上下文

7. **通道** (`nanobot/channels/`)
   - 与外部消息平台的集成
   - 目前支持 Telegram 和 WhatsApp（通过 `bridge/` 中的 Node.js 桥接）
   - 每个通道发布 `InboundMessage` 并订阅 `OutboundMessage`

8. **Cron 系统** (`nanobot/cron/`)
   - 使用 croniter 执行定时任务
   - 在指定时间或间隔触发代理消息

### 数据流

```
User Input → Channel → MessageBus → AgentLoop
                                     ↓
                               ContextBuilder
                                     ↓
                               LLMProvider
                                     ↓
                         ToolCalls → ToolRegistry
                                     ↓
                               Channel → User
```

## 关键设计模式

- **抽象基类**：`LLMProvider` 定义所有 LLM 集成的契约
- **注册表模式**：`ToolRegistry` 动态管理可用工具
- **构建器模式**：`ContextBuilder` 从多个来源构建提示
- **事件驱动**：消息总线实现组件间的松耦合
- **基于工作区的配置**：用户数据位于 `~/.nanobot/workspace/` 以便于自定义

## 配置

配置文件位置：`~/.nanobot/config.json`

关键部分：
- `agents.defaults.model`：默认 LLM 模型
- `providers.{name}.apiKey`：每个提供商的 API 密钥
- `channels.{telegram,whatsapp}`：通道特定设置
- `tools.web.search.apiKey`：用于网络搜索的 Brave Search API

## 添加功能

**新工具：**
1. 在 `nanobot/agent/tools/` 中创建继承自 `Tool` 的类
2. 实现 `schema()` 和 `execute()` 方法
3. 在 `AgentLoop._register_default_tools()` 中注册

**新 LLM 提供商：**
1. 在 `nanobot/providers/base.py` 中继承 `LLMProvider`
2. 实现 `chat()` 和 `get_default_model()`
3. 在 `nanobot/config/settings.py` 中添加配置解析

**新通道：**
1. 在 `nanobot/channels/` 中创建通道类
2. 向总线发布 `InboundMessage` 事件
3. 订阅 `OutboundMessage` 事件
4. 添加通道配置模式

**新技能：**
1. 在 `nanobot/skills/` 中创建目录
2. 添加包含描述和工具定义的 `SKILL.md`
3. 在 `.sh` 文件中添加可选的 shell 脚本
4. 技能由 `SkillsLoader` 自动加载
