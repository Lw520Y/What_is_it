# -*- coding: utf-8 -*-
"""磁盘扫描模块：盘符枚举、目录子项扫描（懒加载）、目录大小计算。

设计要点：
- 懒加载：scan_dir 只返回直接子项，不递归，保证大目录（C盘根）不卡死
- 目录大小：calculate_dir_size 递归统计，供后台线程调用
- 容错：无权限目录（System Volume Information 等）返回错误标记而非抛异常
"""

import ctypes
import os
import shutil

# Windows 系统保护目录，扫描时直接跳过
SKIP_DIRS = {"system volume information", "$recycle.bin", "$windows.~bt", "$windows.~ws", "recovery", "config.msi"}

# 每目录最大返回条目数，防止异常目录（如百万文件的缓存目录）拖垮界面
MAX_ENTRIES = 5000


def get_drives():
    """枚举所有可用盘符。

    返回: [{"drive": "C:\\", "total": bytes, "used": bytes, "free": bytes, "label": "Windows"}]
    """
    drives = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if bitmask & (1 << i):
            letter = chr(ord("A") + i)
            root = f"{letter}:\\"
            if os.path.exists(root):
                try:
                    total, used, free = shutil.disk_usage(root)
                    drives.append({
                        "drive": root,
                        "total": total,
                        "used": used,
                        "free": free,
                        "label": _get_volume_label(letter),
                    })
                except OSError:
                    continue
    return drives


def _get_volume_label(letter):
    """读取盘符卷标（如"Windows""数据盘"）。"""
    volume_name = ctypes.create_unicode_buffer(261)
    fs_name = ctypes.create_unicode_buffer(261)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(f"{letter}:\\"),
        volume_name, 261, None, None, None, fs_name, 261,
    )
    return volume_name.value if ok and volume_name.value else ""


def scan_dir(path):
    """扫描目录的直接子项（不递归）。

    返回: {"entries": [...], "error": None 或错误信息, "truncated": bool}
    entry: {"name", "path", "is_dir", "size"(文件才有), "mtime"(修改时间戳)}
    """
    entries = []
    error = None
    truncated = False
    try:
        with os.scandir(path) as it:
            for index, entry in enumerate(it):
                if index >= MAX_ENTRIES:
                    truncated = True
                    break
                if entry.name.lower() in SKIP_DIRS:
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    if is_dir:
                        size = None
                    else:
                        size = entry.stat(follow_symlinks=False).st_size
                    mtime = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    # 单项损坏/无权限：仍列出但标记不可读
                    is_dir = entry.is_dir(follow_symlinks=True)
                    size = None
                    mtime = None
                entries.append({
                    "name": entry.name,
                    "path": entry.path.replace("/", "\\"),
                    "is_dir": is_dir,
                    "size": size,
                    "mtime": mtime,
                })
    except PermissionError:
        error = "无访问权限"
    except FileNotFoundError:
        error = "目录不存在（可能已被删除）"
    except OSError as e:
        error = f"无法读取: {e.strerror or e}"

    # 排序：目录在前，名称不区分大小写
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return {"entries": entries, "error": error, "truncated": truncated}


def calculate_dir_size(path):
    """递归计算目录总大小与文件数（供后台线程调用，勿在UI线程直接跑）。

    返回: {"size": bytes, "files": 文件数, "dirs": 目录数, "error": None或信息}
    """
    total = 0
    file_count = 0
    dir_count = 0
    errors = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue  # 跳过符号链接防循环
                        if entry.is_dir(follow_symlinks=False):
                            dir_count += 1
                            stack.append(entry.path)
                        else:
                            stat = entry.stat(follow_symlinks=False)
                            total += stat.st_size
                            file_count += 1
                    except OSError:
                        errors += 1
        except OSError:
            errors += 1
    return {"size": total, "files": file_count, "dirs": dir_count,
            "error": f"{errors} 个子项无法读取" if errors else None}


def is_windows_dir(path):
    """判断路径是否为 Windows 桌面/下载等特殊目录（用于详情面板提示）。"""
    specials = ["desktop", "downloads", "documents", "pictures", "videos", "music"]
    return os.path.basename(path).lower() in specials
