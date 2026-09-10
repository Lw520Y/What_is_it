# What's it? —— 磁盘目录侦探

电脑里总有些来历不明的文件夹（比如 AI 工具残留的 `.venv-xxx` 空虚拟环境）？这个小工具帮你：

1. **选择盘符，逐级浏览**文件夹和文件（懒加载，扫 C 盘不卡死）
2. **自动识别**每个目录/文件是什么、做什么用的（内置 80+ 规则秒判）
3. 识别不了的，一键 **AI 深度分析**（读取目录内容让 AI 判断用途 + 给出删除建议）

## 快速开始

### 直接运行（需 Python 3.10+）

```bash
pip install PySide6 requests
python main.py
```

### 打包成 exe

双击 `build.bat`，产物在 `dist\WhatIsIt\WhatIsIt.exe`（单文件夹，整个目录拷走即用）。

## AI 配置（可选但推荐）

本地规则覆盖不到的目录需要 AI 兜底。任选一个 OpenAI 兼容服务商：

| 服务商 | 接口地址 | 模型 | 说明 |
|---|---|---|---|
| **智谱（推荐）** | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` | 注册送额度，flash 模型免费 |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` | 便宜量大 |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` | 需外网 |

配置方式：软件右上角 **⚙ AI 设置** → 填 API Key → 保存（设置里有智谱/DeepSeek 一键模板）。

## 识别机制

| 级别 | 方式 | 示例 |
|---|---|---|
| L1 目录名规则 | 内置 80+ 精确/通配规则 | `.git`→Git版本库、`node_modules`→NPM依赖 |
| L2 特征文件 | 探测目录内的标志性文件 | `pyvenv.cfg`→Python虚拟环境（还能识别出是 uv 创建的空壳） |
| L3 AI 分析 | 子项列表+特征文件摘要喂给 AI | AI 返回用途/置信度/删除建议，结果缓存 7 天 |

**删除建议图例**：✅ 可安全删除 ｜ ⚠️ 谨慎删除 ｜ ⛔ 不建议删除 ｜ ❔ 无法判断

## 项目结构

```
What_is_it/
├── main.py          # PySide6 主窗口（树形浏览+详情面板+设置）
├── scanner.py       # 盘符枚举/目录扫描/大小计算（后台线程）
├── rules.py         # 本地规则库（L1名称+L2特征文件）
├── ai_analyzer.py   # AI 深度分析（OpenAI兼容+缓存）
├── config.json      # AI 接口配置
├── build.bat        # PyInstaller 打包脚本
└── cache.json       # AI 结果缓存（运行后生成）
```

## 注意事项

- 仅监听本地操作，不上传任何文件内容——AI 分析只发送目录名/子项名/小配置文件内容
- 目录大小为后台递归计算，首次展开大目录（如 node_modules）需几秒
- `System Volume Information`、`$Recycle.Bin` 等系统保护目录已自动跳过
