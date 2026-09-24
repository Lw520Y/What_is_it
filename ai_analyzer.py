# -*- coding: utf-8 -*-
"""AI 分析模块：对本地规则未识别的目录，组装上下文调用 AI（OpenAI 兼容接口）判断用途。

支持智谱GLM/DeepSeek/通义/OpenAI 等任何 OpenAI 兼容服务，配置存于同目录 config.json。
结果缓存到 cache.json，同一目录（内容摘要不变）不重复调用。
"""

import json
import os
import re
import sys
import time

import requests

# 打包（PyInstaller onefile）后 __file__ 位于临时解压目录，配置须存到 exe 旁边
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
CACHE_PATH = os.path.join(BASE_DIR, "cache.json")

DEFAULT_CONFIG = {
    "api_base_url": "https://open.bigmodel.cn/api/paas/v4",
    "api_key": "",
    "model": "glm-4-flash",
    "timeout_seconds": 30,
}

DELETABLE_DESC = {
    "safe": "可以安全删除（删除后可自动重建或重新下载）",
    "caution": "谨慎删除（删除可能影响某软件/项目，建议先确认）",
    "keep": "不建议删除（系统或重要数据）",
    "unknown": "无法判断",
}


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        merged = {**DEFAULT_CONFIG, **cfg}
        return merged
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _load_cache():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache):
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def collect_context(path):
    """组装供 AI 判断的目录上下文：一级子项 + 关键特征文件摘要。"""
    ctx = {"path": path, "name": os.path.basename(path), "children": [], "features": []}
    try:
        with os.scandir(path) as it:
            children = []
            for i, e in enumerate(it):
                if i >= 40:
                    ctx["children_truncated"] = True
                    break
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                    size = None if is_dir else e.stat(follow_symlinks=False).st_size
                except OSError:
                    is_dir, size = False, None
                children.append(("目录" if is_dir else "文件", e.name, size))
    except OSError as e:
        ctx["scan_error"] = str(e)
        return ctx
    ctx["children"] = children

    # 读关键特征文件内容摘要
    feature_names = ["pyvenv.cfg", "package.json", "requirements.txt", "README.md",
                     "readme.txt", "go.mod", "cargo.toml", "pom.xml", ".project"]
    import rules as _  # noqa: F401  （保持与 rules 模块的相对独立，无需引用）
    child_lower = {c[1].lower(): c[1] for c in children}
    for fn in feature_names:
        real = child_lower.get(fn)
        if not real:
            continue
        try:
            fp = os.path.join(path, real)
            if os.path.getsize(fp) > 20 * 1024:
                continue
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(4000)
            ctx["features"].append({"file": real, "content": content})
        except OSError:
            continue
    return ctx


def _build_prompt(ctx):
    children_lines = []
    for typ, name, size in ctx["children"][:40]:
        size_str = f", {size} bytes" if size is not None else ""
        children_lines.append(f"- [{typ}] {name}{size_str}")
    children_text = "\n".join(children_lines) or "（空目录或不可读）"
    features_text = ""
    for feat in ctx["features"]:
        features_text += f"\n--- {feat['file']} 内容 ---\n{feat['content']}\n"

    prompt = f"""你是一位 Windows 系统专家。请分析以下磁盘目录是做什么用的。

目录路径: {ctx['path']}
目录名: {ctx['name']}
一级子项（最多40个）:
{children_text}{features_text}

请严格按以下 JSON 格式回答（不要输出 JSON 以外的任何内容）:
{{
  "what": "这是什么（一句话，如：某AI工具创建的Python虚拟环境残留）",
  "purpose": "具体做什么用的（1-3句，说明来源和作用）",
  "deletable": "safe/caution/keep/unknown 之一（safe=可安全删除, caution=谨慎删除, keep=勿删, unknown=无法判断）",
  "confidence": 0到1的小数，表示你的把握,
  "advice": "给用户的处理建议（一句话，如：空壳环境无安装包，可直接删除）"
}}"""
    return prompt


def _extract_json(text):
    """从 AI 回复中提取 JSON（容忍 markdown 代码块包裹）。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    # 找第一个 { 到最后一个 }
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def analyze(path, force=False):
    """分析目录用途。返回统一结构，错误时返回 {"error": ...}。

    {"source": "ai", "purpose", "deletable", "confidence", "evidence": [...], "advice", "cached": bool}
    """
    cfg = load_config()
    if not cfg.get("api_key"):
        return {"error": "未配置 API Key，请先在设置中填写（支持智谱/DeepSeek 等 OpenAI 兼容服务）"}

    # 缓存命中判断（目录名+子项数做指纹）
    ctx = collect_context(path)
    fingerprint = json.dumps({"p": path, "n": ctx["name"],
                              "c": [c[1] for c in ctx["children"][:20]]},
                             ensure_ascii=False, sort_keys=False)
    if not force:
        cache = _load_cache()
        hit = cache.get(fingerprint)
        if hit and time.time() - hit.get("_ts", 0) < 7 * 86400:
            hit["cached"] = True
            return hit

    prompt = _build_prompt(ctx)
    url = cfg["api_base_url"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}
    payload = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    try:
        # 批量分析时连续请求易触发接口限流，超时/限流自动重试（退避 2s、4s）
        last_err = None
        for attempt in range(3):
            if attempt:
                time.sleep(2 * attempt)
            try:
                resp = requests.post(url, headers=headers, json=payload,
                                     timeout=cfg["timeout_seconds"])
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                break
            except requests.exceptions.Timeout:
                last_err = f"AI 请求超时（{cfg['timeout_seconds']}秒）"
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                # 限流与网关类临时故障值得重试；401/403 等鉴权参数错误重试无意义
                if status not in (429, 500, 502, 503, 504):
                    return {"error": f"AI 接口返回错误: {e}（请检查 Key/模型名/余额）"}
                last_err = f"AI 接口限流/繁忙（HTTP {status}）"
        else:
            return {"error": f"{last_err}，已自动重试仍失败，多为接口限流，可稍后再试"}
    except (requests.exceptions.RequestException, KeyError, IndexError) as e:
        return {"error": f"AI 请求失败: {e}"}

    try:
        parsed = _extract_json(content)
    except json.JSONDecodeError:
        return {"error": "AI 返回内容无法解析为 JSON，请重试", "raw": content[:500]}

    result = {
        "source": "ai",
        "what": str(parsed.get("what", "")),
        "purpose": str(parsed.get("purpose", "")),
        "deletable": parsed.get("deletable", "unknown"),
        "confidence": float(parsed.get("confidence", 0.5)),
        "advice": str(parsed.get("advice", "")),
        "evidence": ["AI 基于目录内容分析"],
        "cached": False,
    }
    if result["deletable"] not in DELETABLE_DESC:
        result["deletable"] = "unknown"

    # 写缓存
    cache = _load_cache()
    cache[fingerprint] = {**result, "_ts": time.time()}
    if len(cache) > 2000:
        cache = dict(sorted(cache.items(), key=lambda kv: kv[1].get("_ts", 0))[-1500:])
    _save_cache(cache)
    return result
