# -*- coding: utf-8 -*-
"""本地规则知识库：三级识别（L1 目录名匹配 → L2 特征文件探测 → L3 AI学习规则/待AI分析）。

返回统一结构:
    {"source": "rule"/"feature"/"learned", "purpose": 用途说明, "confidence": 0.0-1.0,
     "evidence": 判断依据, "deletable": "safe"/"caution"/"keep"/None}
"""

import json
import os
import sys
import time

# 打包（PyInstaller）后学习规则存到 exe 旁边，与源码运行时同逻辑
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LEARNED_PATH = os.path.join(_BASE_DIR, "learned_rules.json")


def _load_learned():
    try:
        with open(LEARNED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# 运行期常驻内存：{目录名小写: {what, purpose, deletable, confidence, advice, learned_at}}
LEARNED_RULES = _load_learned()


def learn(name, info):
    """把一次成功的 AI 分析结果沉淀为按目录名匹配的本地规则（同名再次分析会覆盖更新）。

    仅接受 AI 判定成功的结果；返回是否写入成功。
    """
    if not name or info.get("error"):
        return False
    what = str(info.get("what", ""))
    purpose = str(info.get("purpose", ""))
    if not what and not purpose:
        return False
    deletable = info.get("deletable")
    if deletable not in ("safe", "caution", "keep", "unknown"):
        deletable = "unknown"
    LEARNED_RULES[name.lower()] = {
        "what": what,
        "purpose": purpose,
        "deletable": deletable,
        "confidence": float(info.get("confidence", 0.5)),
        "advice": str(info.get("advice", "")),
        "learned_at": time.time(),
    }
    try:
        with open(LEARNED_PATH, "w", encoding="utf-8") as f:
            json.dump(LEARNED_RULES, f, ensure_ascii=False, indent=2)
    except OSError:
        return False
    return True

# ---------------------------------------------------------------
# L1: 目录名精确/通配匹配表
# name 精确匹配（不区分大小写）或 pattern 通配（fnmatch风格，仅对目录名）
# ---------------------------------------------------------------
EXACT_RULES = {
    # === 版本控制 ===
    ".git": ("Git 版本库——代码变更历史，删除将丢失全部提交记录", "keep"),
    ".svn": ("SVN 版本库——SVN 工作副本元数据，删除将丢失与仓库的关联", "keep"),
    ".hg": ("Mercurial 版本库元数据", "keep"),
    # === 编程语言环境/依赖 ===
    "node_modules": ("NPM/Yarn 依赖包目录——Node.js 项目的第三方库，可通过 npm install 重新安装", "safe"),
    ".venv": ("Python 虚拟环境——项目隔离的 Python 运行环境", "caution"),
    "venv": ("Python 虚拟环境——项目隔离的 Python 运行环境", "caution"),
    "env": ("Python 虚拟环境（常见命名）", "caution"),
    "__pycache__": ("Python 字节码缓存——加速模块导入，删除后自动重建", "safe"),
    ".mypy_cache": ("mypy 类型检查缓存，可安全删除后重建", "safe"),
    ".pytest_cache": ("pytest 测试框架缓存", "safe"),
    "site-packages": ("Python 第三方包安装目录——虚拟环境/解释器的依赖库", "caution"),
    ".gradle": ("Gradle 构建工具缓存与配置", "caution"),
    ".m2": ("Maven 本地仓库——Java 依赖 jar 包缓存，可重新下载", "safe"),
    ".nuget": ("NuGet 包管理器缓存（.NET 依赖包）", "caution"),
    ".cargo": ("Rust Cargo 包管理器目录（注册表缓存+项目）", "caution"),
    ".rustup": ("Rust 工具链管理器目录（rustc/cargo 多版本工具链）", "caution"),
    "gems": ("Ruby Gem 依赖包目录", "caution"),
    ".npm": ("NPM 全局缓存——下载包的缓存副本，删除后按需重新下载", "safe"),
    ".yarn": ("Yarn 包管理器缓存与配置", "caution"),
    ".pnpm-store": ("PNPM 内容寻址存储——所有项目的硬链接源", "caution"),
    ".conda": ("Conda 包管理器目录（环境+包缓存）", "caution"),
    "miniconda3": ("Miniconda——轻量 Python 发行版（含 conda 包管理器）", "caution"),
    "anaconda3": ("Anaconda——科学计算 Python 发行版（体积通常数GB）", "caution"),
    ".tox": ("Python tox 多环境测试工具缓存", "safe"),
    ".eggs": ("Python setuptools 打包产物缓存", "safe"),
    "build": ("构建输出目录——编译/打包生成的临时产物", "caution"),
    "dist": ("分发输出目录——打包成果（exe/whl/压缩包），删除前确认是否还需分发", "caution"),
    "target": ("Rust/Java(部分) 构建输出目录，可通过构建命令重建", "safe"),
    "out": ("IDE/编译器输出目录（IntelliJ/CMake 等构建产物）", "safe"),
    # === 包管理/工具缓存（AppData 常见） ===
    ".cache": ("通用缓存目录（各类开发工具写入），通常可安全清理", "safe"),
    ".m2 repository": ("Maven 本地仓库", "safe"),
    ".nuget packages": ("NuGet 包缓存", "safe"),
    "go": ("Go 语言工作目录（GOPATH：源码+依赖+编译缓存）", "caution"),
    "pkg": ("Go 模块缓存目录（下载的依赖源码，可重新下载）", "safe"),
    # === IDE/编辑器 ===
    ".idea": ("JetBrains IDE 项目配置（IntelliJ IDEA/PyCharm 等）", "caution"),
    ".vscode": ("VS Code 工作区配置（设置/插件推荐/调试配置）", "caution"),
    ".vs": ("Visual Studio 项目缓存与配置", "caution"),
    ".settings": ("Eclipse 项目配置目录", "caution"),
    ".classpath": None,  # 是文件，跳过
    "eclipse": ("Eclipse IDE 安装/配置目录", "caution"),
    ".eclipse": ("Eclipse 工作区元数据", "caution"),
    # === AI 工具 ===
    ".claude": ("Claude Code 配置与技能目录——AI 编程助手的设置/记忆/技能库", "caution"),
    ".cursor": ("Cursor AI 编辑器配置与缓存", "caution"),
    ".continue": ("Continue AI 插件配置（VS Code/JetBrains 的 AI 助手）", "caution"),
    ".copilot": ("GitHub Copilot 缓存/配置", "caution"),
    ".aider": ("Aider AI 结对编程工具配置与聊天记录", "caution"),
    ".ollama": ("Ollama 本地大模型目录——存放下载的模型文件（通常数GB~数十GB）", "caution"),
    ".lmstudio": ("LM Studio 本地大模型管理与模型文件", "caution"),
    # === 容器/虚拟化 ===
    ".docker": ("Docker CLI 配置与凭据", "caution"),
    ".minikube": ("Minikube 本地 Kubernetes 集群（含虚拟机镜像，体积大）", "caution"),
    ".vagrant.d": ("Vagrant 虚拟机盒子缓存", "caution"),
    # === 移动端 ===
    ".android": ("Android SDK/模拟器配置（AVD 虚拟设备可能占用数十GB）", "caution"),
    ".gradle brother": None,
    # === 系统目录（C盘根常见） ===
    "windows": ("Windows 操作系统目录——系统核心文件，绝对不可删除", "keep"),
    "program files": ("64 位软件默认安装目录", "keep"),
    "program files (x86)": ("32 位软件默认安装目录", "keep"),
    "programdata": ("程序公共数据目录（软件的共享配置/许可）", "keep"),
    "users": ("用户主目录——所有用户的桌面/文档/下载等个人数据", "keep"),
    "perflogs": ("Windows 性能日志（系统性能监控数据，通常为空）", "safe"),
    "intel": ("Intel 显卡/芯片组驱动解压临时目录，安装完成后可删", "safe"),
    "nvidia": ("NVIDIA 驱动安装解压临时目录，安装完成后可删", "safe"),
    "amd": ("AMD 驱动解压临时目录", "safe"),
    "drivers": ("驱动程序安装包/解压目录", "caution"),
    "oneqtech": ("驱动安装残留目录", "caution"),
    # === 用户目录常见 ===
    "desktop": ("桌面文件夹", "keep"),
    "downloads": ("下载文件夹", "keep"),
    "documents": ("文档文件夹", "keep"),
    "pictures": ("图片文件夹", "keep"),
    "appdata": ("应用程序数据目录（软件设置/缓存，隐藏属性）", "keep"),
    "local": ("当前用户本机应用数据（缓存占比大）", "keep"),
    "localappdata": None,
    "roaming": ("漫游应用数据（随账号同步的软件配置）", "keep"),
    "locallow": ("低权限应用数据", "keep"),
    "temp": ("临时文件目录——软件运行期临时产物，清理通常安全", "safe"),
    "tmp": ("临时文件目录", "safe"),
    ".ssh": ("SSH 密钥与配置——远程登录凭据，删除将无法免密登录服务器", "keep"),
    ".gnupg": ("GPG 密钥环——加密签名密钥", "keep"),
    # === 其他常见 ===
    "recycler": ("旧版 Windows 回收站", "safe"),
    "found.000": ("磁盘检查(chkdsk)恢复的碎片文件", "caution"),
    "msocache": ("Office 本地安装源缓存（装完 Office 后可删）", "safe"),
    "hiberfil.sys": None,
    "swapfile.sys": None,
}

# 通配规则（fnmatch，仅匹配目录名）
PATTERN_RULES = [
    ("*.venv-*", "Python 虚拟环境——AI 工具/项目按任务名创建的隔离环境", "caution"),
    ("*venv*", "Python 虚拟环境（名称含 venv）", "caution"),
    ("virtualenv*", "Python 虚拟环境", "caution"),
    (".virtualenvs", "virtualenvwrapper 管理的虚拟环境集合", "caution"),
    ("*cache*", "缓存目录（名称含 cache）", "safe"),
    ("*__macosx", "macOS 压缩包解压产生的资源叉目录，Windows 下无用", "safe"),
    ("$recycle.bin*", "回收站——删除文件的暂存区", "keep"),
    ("system volume information", "系统卷信息——Windows 系统还原点与索引（受保护）", "keep"),
    ("*.idb", "编译中间文件", "safe"),
    (".DS_Store", "macOS 目录元数据文件", "safe"),
]

# ---------------------------------------------------------------
# L2: 特征文件探测表
# 目录内存在对应文件/子目录时触发，优先级高于通配规则
# {特征文件名: (用途说明, 删除建议, 附加解析函数名)}
# ---------------------------------------------------------------
FEATURE_FILES = {
    "pyvenv.cfg": ("Python 虚拟环境", "caution", "_parse_pyvenv"),
    "package.json": ("Node.js 项目", "caution", "_parse_package_json"),
    "pom.xml": ("Java Maven 项目", "caution", None),
    "build.gradle": ("Java/Gradle 项目", "caution", None),
    "build.gradle.kts": ("Java/Kotlin Gradle 项目", "caution", None),
    "settings.gradle": ("Gradle 多模块项目", "caution", None),
    "cargo.toml": ("Rust 项目", "caution", None),
    "go.mod": ("Go 语言项目", "caution", None),
    "composer.json": ("PHP Composer 项目", "caution", None),
    "requirements.txt": ("Python 项目（pip 依赖清单）", "caution", None),
    "setup.py": ("Python 包项目（可安装分发）", "caution", None),
    "pyproject.toml": ("Python 项目（现代打包配置）", "caution", None),
    " Pipfile": ("Python Pipenv 项目", "caution", None),
    "environment.yml": ("Python Conda 环境定义", "caution", None),
    "makefile": ("C/C++ 等构建工程（make 构建）", "caution", None),
    "cmakelists.txt": ("C/C++ CMake 工程", "caution", None),
    "*.sln": ("Visual Studio 解决方案", "caution", None),
    "*.csproj": ("C# 项目文件", "caution", None),
    "*.vcxproj": ("Visual C++ 项目文件", "caution", None),
    "*.xcodeproj": None,  # 子目录形式，另行处理
    "pubspec.yaml": ("Flutter/Dart 项目", "caution", None),
    "gemfile": ("Ruby 项目", "caution", None),
    "*.iml": ("IntelliJ IDEA 项目模块文件", "caution", None),
    ".gitignore": None,  # 佐证太弱，不单独触发
    "dockerfile": ("Docker 容器构建工程", "caution", None),
    "docker-compose.yml": ("Docker Compose 编排工程", "caution", None),
    "*.ino": ("Arduino 项目", "caution", None),
    "*.stl": None,
}

# 目录形式的特征（子目录名）
FEATURE_DIRS = {
    ".git": ("Git 仓库工作副本（目录内有 .git 子目录则为项目源码）", "keep"),
    "src": None, "sources": None,  # 太泛，不触发
    ".cargo": ("Rust 项目结构", "caution"),
}


def _parse_pyvenv(base, evidence):
    """读取 pyvenv.cfg，识别创建工具（uv/virtualenv/pipenv）与 Python 版本。"""
    details = []
    creator = None
    try:
        with open(os.path.join(base, "pyvenv.cfg"), "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("uv ="):
                    creator = "uv"
                elif line.startswith("home") and creator is None:
                    creator = "virtualenv/pip"
                elif line.startswith("version_info"):
                    details.append(f"Python {line.split('=', 1)[1].strip()}")
    except OSError:
        pass
    tool = {"uv": "由 uv 创建", "virtualenv/pip": "由 virtualenv/pip 创建"}.get(creator, "")
    if tool and details:
        tool += "，"
    site_packages = os.path.isdir(os.path.join(base, "Lib", "site-packages"))
    empty = False
    sp = os.path.join(base, "Lib", "site-packages")
    if site_packages:
        try:
            empty = not any(os.scandir(sp))  # 只有基础文件算空壳
        except OSError:
            empty = False
    if empty:
        note = "——环境内未安装任何包，属空壳残留，可安全删除"
        deletable = "safe"
    else:
        note = ""
        deletable = "caution"
    return f"Python 虚拟环境（{tool}{'，'.join(details)}）{note}", deletable, evidence + ["pyvenv.cfg"]


def _parse_package_json(base, evidence):
    """读 package.json 的 name/description 丰富用途描述。"""
    try:
        with open(os.path.join(base, "package.json"), "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        name = data.get("name", "")
        desc = data.get("description", "")
        has_deps = bool(data.get("dependencies") or data.get("devDependencies"))
        detail = f"Node.js 项目「{name}」" + (f"：{desc}" if desc else "")
        if has_deps:
            detail += "（含依赖声明）"
        return detail, "caution", evidence + ["package.json"]
    except (OSError, json.JSONDecodeError):
        return "Node.js 项目", "caution", evidence + ["package.json"]


def identify(path, name, is_dir, entries=None):
    """识别入口：对单个目录/文件做 L1→L2 识别。

    path: 完整路径  name: 条目名  is_dir: 是否目录
    entries: 父目录扫描结果（可选，避免重复读盘）
    返回: {"source","purpose","confidence","evidence":[],"deletable"} 或 None(未识别)
    """
    if not is_dir:
        return _identify_file(name)
    low = name.lower()

    # L1 精确匹配
    if low in EXACT_RULES and EXACT_RULES[low]:
        purpose, deletable = EXACT_RULES[low]
        return {"source": "rule", "purpose": purpose, "confidence": 0.95,
                "evidence": [f"目录名「{name}」命中内置规则"], "deletable": deletable}

    # L2 特征文件探测（读子级，最多取200项判断）
    child_names = []
    if entries is None:
        try:
            with os.scandir(path) as it:
                for i, e in enumerate(it):
                    if i >= 200:
                        break
                    child_names.append(e.name)
        except OSError:
            child_names = []
    else:
        child_names = [e["name"] for e in entries] if entries and isinstance(entries[0], dict) else list(entries or [])
    child_lower = {c.lower(): c for c in child_names}

    for feat, spec in FEATURE_FILES.items():
        if spec is None:
            continue
        purpose, deletable, parser = spec
        matched = None
        if feat.startswith("*"):
            for cl, c in child_lower.items():
                if cl.endswith(feat[1:]):
                    matched = c
                    break
        elif feat.strip() in child_lower:
            matched = child_lower[feat.strip()]
        if matched:
            evidence = [f"内含特征文件 {matched}"]
            if parser:
                purpose, deletable, evidence = globals()[parser](path, evidence)
            return {"source": "feature", "purpose": purpose, "confidence": 0.9,
                    "evidence": evidence, "deletable": deletable}

    for dfeat, spec in FEATURE_DIRS.items():
        if spec and dfeat in child_lower:
            return {"source": "feature", "purpose": spec[0], "confidence": 0.88,
                    "evidence": [f"内含特征子目录 {dfeat}"], "deletable": spec[1]}

    # L1 通配匹配（放最后，优先级最低）
    import fnmatch
    for pattern, purpose, deletable in PATTERN_RULES:
        if fnmatch.fnmatch(low, pattern.lower()):
            return {"source": "rule", "purpose": purpose, "confidence": 0.7,
                    "evidence": [f"目录名「{name}」匹配模式 {pattern}"], "deletable": deletable}

    # L3: AI 学习规则兜底（仅在内置规则全部未命中时生效，按目录名匹配）
    hit = LEARNED_RULES.get(low)
    if hit:
        when = time.strftime("%Y-%m-%d", time.localtime(hit.get("learned_at", 0)))
        return {"source": "learned",
                "what": hit.get("what", ""),
                "purpose": hit.get("purpose", ""),
                "confidence": hit.get("confidence", 0.6),
                "evidence": [f"AI 学习规则——同名目录曾于 {when} 做过 AI 分析"],
                "deletable": hit.get("deletable", "unknown"),
                "advice": hit.get("advice", "")}

    return None  # 未识别 → 交给 AI


def _identify_file(name):
    """对单个文件做轻量识别（常见安装包/镜像/配置）。"""
    low = name.lower()
    file_rules = [
        (".exe", "Windows 可执行程序/安装包", "caution"),
        (".msi", "Windows 安装包（MSI 格式）", "caution"),
        (".zip", "ZIP 压缩包", None),
        (".rar", "RAR 压缩包", None),
        (".7z", "7-Zip 压缩包", None),
        (".iso", "光盘镜像文件（系统盘/软件镜像）", "caution"),
        (".gho", "Ghost 系统备份镜像", "caution"),
        (".vhd", "虚拟机硬盘镜像", "caution"),
        (".vhdx", "虚拟机硬盘镜像（Hyper-V）", "caution"),
        (".vmdk", "VMware 虚拟机硬盘", "caution"),
        (".pdf", "PDF 文档", None),
        (".doc", "Word 文档（旧格式）", None),
        (".docx", "Word 文档", None),
        (".xls", "Excel 表格（旧格式）", None),
        (".xlsx", "Excel 表格", None),
        (".ppt", "PPT 演示文稿（旧格式）", None),
        (".pptx", "PPT 演示文稿", None),
        (".txt", "文本文件", None),
        (".md", "Markdown 文档", None),
        (".log", "日志文件——程序运行记录，排查问题后可删", "safe"),
        (".tmp", "临时文件", "safe"),
        (".bak", "备份文件——修改前的副本，确认稳定后可删", "caution"),
        (".dll", "动态链接库——程序组件，勿随意移动/删除", "keep"),
        (".sys", "Windows 驱动/系统文件", "keep"),
        (".ttf", "字体文件", "caution"),
        (".otf", "字体文件（OpenType）", "caution"),
        (".apk", "Android 应用安装包", None),
        (".ipa", "iOS 应用安装包", None),
        (".whl", "Python 包分发格式（pip 安装源）", None),
        (".jar", "Java 归档（程序或库）", None),
    ]
    for ext, purpose, deletable in file_rules:
        if low.endswith(ext):
            return {"source": "rule", "purpose": purpose, "confidence": 0.8,
                    "evidence": [f"文件扩展名 {ext}"], "deletable": deletable}
    return None
