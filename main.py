# -*- coding: utf-8 -*-
"""What's it? —— 磁盘目录侦探：查看任意盘符下文件夹/文件是什么、做什么用的。

本地规则秒判常见目录，未识别的可一键调用 AI（OpenAI 兼容接口）深度分析。
"""

import os
import queue
import sys
import threading
import time

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
    QSplitter, QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
    QFileDialog, QGroupBox,
)

import ai_analyzer
import rules
import scanner

APP_NAME = "What's it? 磁盘目录侦探 - Clash制作"
DELETABLE_LABEL = {
    "safe": ("✅ 可安全删除", "#2e7d32"),
    "caution": ("⚠️ 谨慎删除", "#e65100"),
    "keep": ("⛔ 不建议删除", "#c62828"),
    "unknown": ("❔ 无法判断", "#757575"),
    None: ("—", "#9e9e9e"),
}


def fmt_size(size):
    if size is None:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    v = float(size)
    for u in units:
        if v < 1024 or u == units[-1]:
            return f"{v:.1f} {u}" if u != "B" else f"{int(v)} B"
        v /= 1024


def fmt_time(ts):
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


# ============================================================
# 后台任务服务（常驻守护线程）
# ============================================================
# 设计要点（防闪退）：
# 1. 任务只携带路径/代际号等纯 Python 数据，后台线程绝不触碰
#    QTreeWidgetItem 等 Qt 对象——树被 clear() 后不会有悬空引用；
# 2. 结果经 _Relay 信号投递回主线程，主线程按路径在当前树里重新
#    查找条目，找不到就安全丢弃；
# 3. _Gate 代际号：每次清空树（刷新/切换盘符）自增，旧任务的
#    迟到结果一律作废；
# 4. 线程常驻（不再每选中一项就新建 QThread），关窗时协作式
#    取消并 join，避免 "QThread: Destroyed while still running"
#    触发 qFatal 直接杀死进程。

class _Relay(QObject):
    """工作线程 → 主线程的结果中转（跨线程信号自动排队投递）。"""
    scanResult = Signal(object)  # (gen, path, is_root, result, rows)
    sizeResult = Signal(object)  # (gen, path, result)
    aiResult = Signal(object)    # (path, info)


class _Gate:
    """代际号：每清空一次树自增，用于作废在途任务的结果。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._gen = 0

    def bump(self):
        with self._lock:
            self._gen += 1
            return self._gen

    def current(self):
        with self._lock:
            return self._gen


class _Task:
    __slots__ = ("run", "cancel")

    def __init__(self, run, cancel):
        self.run = run
        self.cancel = cancel


class _Service:
    """常驻工作线程池，顺序执行同类任务（扫描/AI 单线程，大小计算 3 线程）。"""

    def __init__(self, name, workers=1):
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._current = set()  # 正在执行的任务，关窗时统一取消
        self._threads = [threading.Thread(target=self._run, name=f"{name}-{i}", daemon=True)
                         for i in range(workers)]
        for t in self._threads:
            t.start()

    def submit(self, task):
        if not self._stop.is_set():
            self._q.put(task)

    def _run(self):
        while not self._stop.is_set():
            try:
                task = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            with self._lock:
                self._current.add(task)
            try:
                if not self._stop.is_set():
                    task.run()
            finally:
                with self._lock:
                    self._current.discard(task)

    def shutdown(self, timeout=2.0):
        """停止接单、取消排队与在跑任务、等待线程退出。"""
        self._stop.set()
        while True:
            try:
                self._q.get_nowait().cancel.set()
            except queue.Empty:
                break
        with self._lock:
            current = list(self._current)
        for task in current:
            task.cancel.set()
        deadline = time.time() + timeout
        for t in self._threads:
            t.join(max(0.0, deadline - time.time()))


# ============================================================
# 设置对话框（AI API 配置）
# ============================================================

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 识别配置")
        self.setMinimumWidth(520)
        self.cfg = ai_analyzer.load_config()

        layout = QVBoxLayout(self)
        intro = QLabel(
            "配置任一 OpenAI 兼容服务后，即可对本地规则未识别的目录做 AI 深度分析。\n"
            "推荐免费方案：智谱开放平台 https://open.bigmodel.cn （glm-4-flash 免费模型）")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.ed_url = QLineEdit(self.cfg["api_base_url"])
        self.ed_url.setPlaceholderText("https://open.bigmodel.cn/api/paas/v4")
        self.ed_key = QLineEdit(self.cfg["api_key"])
        self.ed_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_key.setPlaceholderText("粘贴 API Key（sk-开头）")
        btn_show = QPushButton("显示")
        btn_show.setFixedWidth(50)
        btn_show.clicked.connect(lambda: self.ed_key.setEchoMode(
            QLineEdit.EchoMode.Normal if self.ed_key.echoMode() == QLineEdit.EchoMode.Password
            else QLineEdit.EchoMode.Password))
        key_row = QHBoxLayout()
        key_row.addWidget(self.ed_key)
        key_row.addWidget(btn_show)
        self.ed_model = QLineEdit(self.cfg["model"])
        self.ed_model.setPlaceholderText("glm-4-flash")
        form.addRow("接口地址:", self.ed_url)
        form.addRow("API Key:", key_row)
        form.addRow("模型:", self.ed_model)
        box = QGroupBox("OpenAI 兼容服务示例")
        box.setLayout(form)
        layout.addWidget(box)

        btns = QHBoxLayout()
        preset_zhipu = QPushButton("填入智谱模板")
        preset_deepseek = QPushButton("填入 DeepSeek 模板")
        preset_zhipu.clicked.connect(lambda: self._preset("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"))
        preset_deepseek.clicked.connect(lambda: self._preset("https://api.deepseek.com/v1", "deepseek-chat"))
        ok = QPushButton("保存")
        ok.setDefault(True)
        ok.clicked.connect(self._save)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        for b in (preset_zhipu, preset_deepseek):
            btns.addWidget(b)
        btns.addStretch()
        btns.addWidget(ok)
        btns.addWidget(cancel)
        layout.addLayout(btns)

    def _preset(self, url, model):
        self.ed_url.setText(url)
        self.ed_model.setText(model)

    def _save(self):
        self.cfg.update({
            "api_base_url": self.ed_url.text().strip() or ai_analyzer.DEFAULT_CONFIG["api_base_url"],
            "api_key": self.ed_key.text().strip(),
            "model": self.ed_model.text().strip() or ai_analyzer.DEFAULT_CONFIG["model"],
        })
        ai_analyzer.save_config(self.cfg)
        self.accept()


# ============================================================
# 详情面板（右侧）
# ============================================================

class DetailPanel(QWidget):
    """展示选中条目的识别结果与操作。"""

    analyzeRequested = Signal(str)     # path
    rescanRequested = Signal(str)      # path

    def __init__(self):
        super().__init__()
        self.current = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.title = QLabel("选择左侧条目查看详情")
        font = QFont()
        font.setBold(True)
        font.setPointSize(11)
        self.title.setFont(font)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)

        self.path_label = QLabel("")
        self.path_label.setWordWrap(True)
        self.path_label.setStyleSheet("color:#666;")
        layout.addWidget(self.path_label)

        self.meta_label = QLabel("")
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)

        self.purpose = QLabel("")
        self.purpose.setWordWrap(True)
        self.purpose.setStyleSheet(
            "background:#e8f0fe;border-radius:6px;padding:10px;color:#1a47b8;")
        layout.addWidget(self.purpose)

        self.deletable = QLabel("")
        self.deletable.setWordWrap(True)
        self.deletable.setStyleSheet("font-weight:bold;padding:6px;")
        layout.addWidget(self.deletable)

        self.evidence = QLabel("")
        self.evidence.setWordWrap(True)
        self.evidence.setStyleSheet("color:#555;font-size:12px;")
        layout.addWidget(self.evidence)

        layout.addStretch()

        self.btn_ai = QPushButton("🤖 AI 深度分析")
        self.btn_ai.clicked.connect(lambda: self.analyzeRequested.emit(self.current["path"]))
        self.btn_ai.setEnabled(False)
        layout.addWidget(self.btn_ai)

        self.btn_copy = QPushButton("📋 复制路径")
        self.btn_copy.clicked.connect(self._copy_path)
        self.btn_copy.setEnabled(False)
        layout.addWidget(self.btn_copy)

    def _copy_path(self):
        if self.current:
            QApplication.clipboard().setText(self.current["path"])

    def show_entry(self, entry, info):
        """entry: scanner 条目; info: rules/ai 识别结果或 None。"""
        self.current = entry
        if entry is None:
            self.title.setText("选择左侧条目查看详情")
            self.path_label.clear()
            self.meta_label.clear()
            self.purpose.clear()
            self.deletable.clear()
            self.evidence.clear()
            self.btn_ai.setEnabled(False)
            self.btn_copy.setEnabled(False)
            return
        kind = "文件夹" if entry["is_dir"] else "文件"
        self.title.setText(f"{kind}：{entry['name']}")
        self.path_label.setText(entry["path"])
        size_part = fmt_size(entry["size"]) if entry["size"] is not None else ""
        self.meta_label.setText(f"大小: {size_part or '—'}    修改时间: {fmt_time(entry['mtime']) or '—'}")

        if info:
            self.purpose.setText(f"💡 {info.get('what', '') or info.get('purpose', '')}"
                                 + (f"\n\n{info['purpose']}" if info.get("what") and info.get("purpose") else ""))
            dl = info.get("deletable")
            text, color = DELETABLE_LABEL.get(dl, DELETABLE_LABEL[None])
            extra = info.get("advice", "")
            self.deletable.setText(text + (f"  ——  {extra}" if extra else ""))
            self.deletable.setStyleSheet(f"font-weight:bold;padding:6px;color:{color};")
            self.evidence.setText("依据: " + "；".join(info.get("evidence", []))
                                  + ("（缓存结果）" if info.get("cached") else ""))
        else:
            self.purpose.setText("💡 本地规则未识别。点击下方「AI 深度分析」让 AI 根据目录内容判断。")
            self.deletable.setText("❔ 未知")
            self.deletable.setStyleSheet("font-weight:bold;padding:6px;color:#757575;")
            self.evidence.clear()

        self.btn_ai.setEnabled(True)
        self.btn_copy.setEnabled(True)

    def show_ai_result(self, info):
        """AI 分析完成后刷新。"""
        if info.get("error"):
            self.purpose.setText(f"⚠️ {info['error']}")
            self.purpose.setStyleSheet(
                "background:#fdecea;border-radius:6px;padding:10px;color:#c62828;")
            return
        self.purpose.setStyleSheet(
            "background:#e8f0fe;border-radius:6px;padding:10px;color:#1a47b8;")
        self.show_entry(self.current, info)


# ============================================================
# 主窗口
# ============================================================

COL_NAME, COL_SIZE, COL_MTIME, COL_PURPOSE = 0, 1, 2, 3
ROLE_ENTRY = Qt.ItemDataRole.UserRole + 1
ROLE_INFO = Qt.ItemDataRole.UserRole + 2
ROLE_SCANNED = Qt.ItemDataRole.UserRole + 3


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 780)
        self._ai_pending = set()  # 批量 AI 分析中尚未返回的路径
        self._ai_failed = set()   # 本轮分析失败的路径（用于结束时汇总提示）
        self._gate = _Gate()
        self._relay = _Relay()
        self._relay.scanResult.connect(self._on_scan_done)
        self._relay.sizeResult.connect(self._on_size_done)
        self._relay.aiResult.connect(self._on_ai_done)
        self._scan_svc = _Service("scan")
        self._size_svc = _Service("size", workers=3)
        self._ai_svc = _Service("ai")

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ---- 顶部工具栏 ----
        bar = QHBoxLayout()
        bar.addWidget(QLabel("盘符:"))
        self.cb_drive = QComboBox()
        self.cb_drive.setMinimumWidth(240)
        self.cb_drive.currentIndexChanged.connect(self._on_drive_changed)
        bar.addWidget(self.cb_drive)
        self.btn_refresh = QPushButton("🔄 刷新")
        self.btn_refresh.clicked.connect(self._reload_drive)
        bar.addWidget(self.btn_refresh)
        self.btn_up = QPushButton("⬆ 上一级")
        self.btn_up.clicked.connect(self._go_parent)
        bar.addWidget(self.btn_up)
        self.btn_open = QPushButton("📂 在资源管理器打开")
        self.btn_open.clicked.connect(self._open_in_explorer)
        bar.addWidget(self.btn_open)
        bar.addStretch()
        self.btn_settings = QPushButton("⚙ AI 设置")
        self.btn_settings.clicked.connect(self._open_settings)
        bar.addWidget(self.btn_settings)
        root.addLayout(bar)

        self.drive_info = QLabel("")
        self.drive_info.setStyleSheet("color:#666;padding:0 4px 4px;")
        root.addWidget(self.drive_info)

        # ---- 主区：树 + 详情 ----
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["名称", "大小", "修改时间", "识别用途"])
        self.tree.setColumnWidth(COL_NAME, 340)
        self.tree.setColumnWidth(COL_SIZE, 90)
        self.tree.setColumnWidth(COL_MTIME, 140)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.itemSelectionChanged.connect(self._on_item_selected)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        splitter.addWidget(self.tree)

        self.detail = DetailPanel()
        self.detail.analyzeRequested.connect(self._start_ai)
        self.detail.setMinimumWidth(320)
        splitter.addWidget(self.detail)
        splitter.setSizes([850, 380])
        root.addWidget(splitter, stretch=1)

        self.status = self.statusBar()
        self._root_scanning = False
        self._load_drives()

    # ---------------- 盘符 ----------------

    def _load_drives(self):
        self.cb_drive.blockSignals(True)
        self.cb_drive.clear()
        for d in scanner.get_drives():
            label = f"{d['drive']}  ({fmt_size(d['total'])}，剩余 {fmt_size(d['free'])})"
            if d["label"]:
                label = f"{d['drive']} {d['label']}  ({fmt_size(d['total'])}，剩余 {fmt_size(d['free'])})"
            self.cb_drive.addItem(label, d)
        self.cb_drive.blockSignals(False)
        if self.cb_drive.count():
            self._on_drive_changed(0)

    def _current_root(self):
        data = self.cb_drive.currentData()
        return data["drive"] if data else None

    def _on_drive_changed(self, _):
        root = self._current_root()
        if not root:
            return
        data = self.cb_drive.currentData()
        pct = data["used"] * 100 // data["total"] if data["total"] else 0
        self.drive_info.setText(
            f"已用 {fmt_size(data['used'])} / {fmt_size(data['total'])}（{pct}%），剩余 {fmt_size(data['free'])}")
        self._scan_into(self.tree, root, is_root=True)

    def _reload_drive(self):
        self._load_drives()

    def _go_parent(self):
        root = self._current_root()
        cur = self._selected_path()
        if not cur or not cur.startswith(root):
            return
        rel = os.path.relpath(cur, root)
        if rel == ".":
            return
        # 逐级回退并在树中定位
        parts = rel.split(os.sep)
        self.tree.collapseAll()
        item = None  # 顶层没有"根条目"，从盘符对应层往下找
        # 顶层即根目录子项：先找 parts[0]
        if not parts or parts[0] == ".":
            return
        item = self._find_child_by_name(self.tree.invisibleRootItem(), parts[0])
        if item is None:
            return
        for part in parts[1:-1]:
            if item is None:
                return
            self._ensure_expanded(item)
            item = self._find_child_by_name(item, part)
        if item is not None:
            item.setSelected(True)
            self.tree.scrollToItem(item)

    def _open_in_explorer(self):
        path = self._selected_path()
        if path:
            os.startfile(path)  # noqa  Windows 专用

    def _open_settings(self):
        SettingsDialog(self).exec()

    def closeEvent(self, event):
        # 关窗时作废所有在途任务并等后台线程退出，
        # 避免线程仍在运行时对象被销毁导致进程被 qFatal 杀死（闪退）
        self._gate.bump()
        for svc in (self._scan_svc, self._size_svc, self._ai_svc):
            svc.shutdown()
        super().closeEvent(event)

    # ---------------- 树扫描/交互 ----------------

    def _scan_into(self, parent_item, path, is_root=False):
        """后台扫描 path 的子项，完成后填充到 parent_item。parent_item 为 QTreeWidget 时表示根。"""
        self.status.showMessage(f"正在扫描 {path} ...")
        if is_root:
            self._root_scanning = True
            self._gate.bump()  # 作废此前所有在途扫描/大小任务
            self.tree.clear()
        else:
            parent_item.setData(COL_NAME, ROLE_SCANNED, True)
        gen = self._gate.current()
        cancel = threading.Event()

        def work():
            result = scanner.scan_dir(path, cancel=cancel)
            rows = []
            if not result.get("error") and not result.get("cancelled"):
                # 识别放在后台线程做，避免大量目录的规则探测卡住界面
                for entry in result["entries"]:
                    info = rules.identify(entry["path"], entry["name"], entry["is_dir"])
                    rows.append((entry, info))
            self._relay.scanResult.emit((gen, path, is_root, result, rows))

        self._scan_svc.submit(_Task(work, cancel))

    def _on_scan_done(self, payload):
        gen, path, is_root, result, rows = payload
        if is_root:
            self._root_scanning = False
        if gen != self._gate.current() or result.get("cancelled"):
            return  # 结果已过期（期间刷新/切换过目录）或被取消，安全丢弃
        if result.get("error"):
            self.status.showMessage(f"扫描失败: {result['error']}")
            return

        items = []
        for entry, info in rows:
            item = QTreeWidgetItem([entry["name"], fmt_size(entry["size"]),
                                    fmt_time(entry["mtime"]), info["purpose"] if info else ""])
            item.setData(COL_NAME, ROLE_ENTRY, entry)
            item.setData(COL_NAME, ROLE_INFO, info)
            if entry["is_dir"]:
                item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                _apply_dir_icon(item, info)
            else:
                _apply_file_icon(item, entry["name"])
            _apply_purpose_color(item, info)
            items.append(item)
        if result.get("truncated"):
            note = QTreeWidgetItem([f"…（条目过多，仅显示前 {scanner.MAX_ENTRIES} 项）", "", "", ""])
            note.setDisabled(True)
            items.append(note)

        if is_root:
            self.tree.addTopLevelItems(items)
            count = self.tree.topLevelItemCount()
        else:
            parent = self._find_item_by_path(self.tree.invisibleRootItem(), path)
            if parent is None:
                return  # 节点已随树清空被移除，丢弃结果
            parent.addChildren(items)
            count = parent.childCount()
        self.status.showMessage(f"扫描完成，共 {count} 项", 5000)

    def _on_item_expanded(self, item):
        if item.data(COL_NAME, ROLE_SCANNED):
            return
        entry = item.data(COL_NAME, ROLE_ENTRY)
        if entry and entry["is_dir"]:
            self._scan_into(item, entry["path"])
        elif item is self.tree.invisibleRootItem():
            pass

    def _ensure_expanded(self, item):
        if not item.data(COL_NAME, ROLE_SCANNED):
            entry = item.data(COL_NAME, ROLE_ENTRY)
            if entry:
                # 同步等待场景少，直接触发扫描（异步）
                self._scan_into(item, entry["path"])
        item.setExpanded(True)

    def _find_child_by_name(self, parent_item, name):
        """按 entry 名称找子项（条目文本带图标前缀，须比对 entry 数据）。"""
        low = name.lower()
        for i in range(parent_item.childCount()):
            ch = parent_item.child(i)
            entry = ch.data(COL_NAME, ROLE_ENTRY)
            if entry and entry["name"].lower() == low:
                return ch
        return None

    def _on_item_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return
        item = items[0]
        entry = item.data(COL_NAME, ROLE_ENTRY)
        if not entry:
            self.detail.show_entry(None, None)
            return
        info = item.data(COL_NAME, ROLE_INFO)
        self.detail.show_entry(entry, info)
        # 文件夹且未算大小：后台算；滑动多选时对每个选中项排队（已算过的自动跳过）
        for it in items:
            e = it.data(COL_NAME, ROLE_ENTRY)
            if e and e["is_dir"]:
                self._start_size_calc(it, e["path"])

    def _selected_path(self):
        items = self.tree.selectedItems()
        if not items:
            return None
        entry = items[0].data(COL_NAME, ROLE_ENTRY)
        return entry["path"] if entry else None

    def _on_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        entry = item.data(COL_NAME, ROLE_ENTRY)
        if entry is None:
            return  # 占位行（如“条目过多”提示）
        # 右键联动选中：落在既有选区内保留多选（便于批量分析），否则单选该项
        if not item.isSelected():
            self.tree.setCurrentItem(item)

        selected = [it.data(COL_NAME, ROLE_ENTRY) for it in self.tree.selectedItems()]
        selected = [e for e in selected if e]
        if len(selected) > 1:
            ai_paths = [e["path"] for e in selected]
            ai_label = f"🤖 AI 深度分析（{len(ai_paths)} 项）"
        else:
            ai_paths = [entry["path"]]
            ai_label = "🤖 AI 深度分析"

        menu = QMenu(self.tree)
        act_ai = menu.addAction(ai_label)
        act_open = menu.addAction("📂 在资源管理器打开")
        act_copy = menu.addAction("📋 复制路径")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is act_ai:
            self._start_ai(ai_paths)
        elif chosen is act_open:
            try:
                os.startfile(entry["path"])  # noqa  Windows 专用
            except OSError:
                self.status.showMessage(f"无法打开: {entry['path']}", 5000)
        elif chosen is act_copy:
            QApplication.clipboard().setText(entry["path"])
            self.status.showMessage("路径已复制", 3000)

    # ---------------- 大小计算 ----------------

    def _start_size_calc(self, item, path):
        if item.data(COL_SIZE, ROLE_SCANNED):
            return
        item.setData(COL_SIZE, ROLE_SCANNED, True)
        item.setText(COL_SIZE, "计算中…")
        gen = self._gate.current()
        cancel = threading.Event()

        def work():
            result = scanner.calculate_dir_size(path, cancel=cancel)
            self._relay.sizeResult.emit((gen, path, result))

        self._size_svc.submit(_Task(work, cancel))

    def _on_size_done(self, payload):
        gen, path, result = payload
        if gen != self._gate.current() or result.get("cancelled"):
            return  # 结果已过期或被取消
        # 在当前树中找该项（可能已切换目录）
        item = self._find_item_by_path(self.tree.invisibleRootItem(), path)
        if item:
            item.setText(COL_SIZE, fmt_size(result["size"]))
        entry = item.data(COL_NAME, ROLE_ENTRY) if item else None
        if entry and item and item.isSelected():
            files = result.get("files", 0)
            dirs = result.get("dirs", 0)
            extra = f"    （{files} 个文件, {dirs} 个子目录）"
            if result.get("error"):
                extra += f"，{result['error']}"
            self.detail.meta_label.setText(
                f"大小: {fmt_size(result['size'])}{extra}    修改时间: {fmt_time(entry['mtime']) or '—'}")

    def _find_item_by_path(self, parent, path):
        target = path.lower().rstrip("\\")
        stack = [parent]
        while stack:
            cur = stack.pop()
            for i in range(cur.childCount()):
                ch = cur.child(i)
                entry = ch.data(COL_NAME, ROLE_ENTRY)
                if entry and entry["path"].lower().rstrip("\\") == target:
                    return ch
                if ch.childCount():
                    stack.append(ch)
        return None

    # ---------------- AI ----------------

    MAX_AI_BATCH = 20  # 一次批量分析的条目上限，防止误全选触发海量请求

    def _start_ai(self, paths):
        """paths: 单个路径 str 或路径列表（批量）。已在分析中的路径自动跳过。"""
        if isinstance(paths, str):
            paths = [paths]
        new = [p for p in paths if p and p not in self._ai_pending]
        if not new:
            return
        cfg = ai_analyzer.load_config()
        if not cfg.get("api_key"):
            QMessageBox.information(
                self, "未配置 AI",
                "尚未配置 AI 接口。\n点击右上角「⚙ AI 设置」填写 API Key 后重试。\n\n"
                "推荐免费方案：智谱开放平台 open.bigmodel.cn（glm-4-flash 免费）")
            if SettingsDialog(self).exec():
                if not ai_analyzer.load_config().get("api_key"):
                    return
            else:
                return
        if len(new) > self.MAX_AI_BATCH:
            new = new[:self.MAX_AI_BATCH]
            self.status.showMessage(
                f"选中项较多，一次最多批量分析 {self.MAX_AI_BATCH} 项（其余请分批）", 8000)
        self._ai_failed = set()
        for idx, path in enumerate(new):
            self._ai_pending.add(path)
            cancel = threading.Event()
            first = idx == 0

            def work(path=path, cancel=cancel, first=first):
                # 批量请求之间留间隔，避免连续请求触发接口限流；等待可被取消
                if not first:
                    cancel.wait(1.5)
                info = ai_analyzer.analyze(path, force=False)
                self._relay.aiResult.emit((path, info))

            self._ai_svc.submit(_Task(work, cancel))
        self._update_ai_status()
        if len(new) == 1:
            self.status.showMessage(f"AI 正在分析 {new[0]} ...")
        else:
            self.status.showMessage(f"AI 正在批量分析 {len(new)} 项，逐项进行中 ...")

    def _update_ai_status(self):
        n = len(self._ai_pending)
        if n:
            self.detail.btn_ai.setText(f"🤖 AI 分析中…（剩 {n}）")
            self.detail.btn_ai.setEnabled(False)
        else:
            self.detail.btn_ai.setText("🤖 AI 深度分析")
            self.detail.btn_ai.setEnabled(True)

    def _on_ai_done(self, payload):
        path, info = payload
        self._ai_pending.discard(path)
        if info.get("error"):
            self._ai_failed.add(path)
        self._update_ai_status()
        if info.get("error"):
            self.detail.show_ai_result(info)
            self.status.showMessage(f"AI 分析失败: {info['error']}", 8000)
            return
        # 更新树行
        item = self._find_item_by_path(self.tree.invisibleRootItem(), path)
        if item:
            item.setText(COL_PURPOSE, info.get("what") or info.get("purpose", ""))
            item.setData(COL_NAME, ROLE_INFO, info)
            _apply_purpose_color(item, info)
            if item.isSelected():
                self.detail.show_entry(item.data(COL_NAME, ROLE_ENTRY), info)
        # 识别成功且仍是目录 → 沉淀为本地规则，下次同名目录免 AI 直接识别（文件按扩展名识别，不沉淀）
        learned = os.path.isdir(path) and rules.learn(os.path.basename(path.rstrip("\\/")), info)
        if self._ai_failed:
            msg = f"AI 分析完成，{len(self._ai_failed)} 项失败（常见为接口限流/超时），可稍后重试"
        elif learned:
            msg = "AI 分析完成，已加入本地规则（同名目录下次直接识别）"
        else:
            msg = "AI 分析完成"
        self.status.showMessage(msg, 5000)


# ---------------- 辅助：图标/配色/占位 ----------------

def _make_loading_item():
    item = QTreeWidgetItem(["⏳ 加载中…", "", "", ""])
    item.setDisabled(True)
    return item


def _apply_dir_icon(item, info):
    text = (info.get("purpose", "") if info else "") + (info.get("what", "") if info else "")
    icon = "📁"
    for kw, ic in [(".git", "🗂️"), ("版本库", "🗂️"), ("虚拟环境", "🐍"), ("node_modules", "📦"),
                   ("依赖", "📦"), ("缓存", "🧹"), ("项目", "💻"), ("回收站", "🗑️"),
                   ("系统", "🪟"), ("AI", "🤖"), ("模型", "🤖")]:
        if kw.lower() in text.lower():
            icon = ic
            break
    item.setText(COL_NAME, f"{icon} {item.text(COL_NAME)}")


def _apply_file_icon(item, name):
    low = name.lower()
    icon = "📄"
    for ext, ic in [(".exe", "⚙️"), (".msi", "⚙️"), (".zip", "🗜️"), (".rar", "🗜️"), (".7z", "🗜️"),
                    (".iso", "💿"), (".pdf", "📕"), (".doc", "📘"), (".docx", "📘"),
                    (".xls", "📗"), (".xlsx", "📗"), (".ppt", "📙"), (".pptx", "📙"),
                    (".dll", "🔧"), (".log", "📃"), (".md", "📝"), (".txt", "📝"),
                    (".json", "🧾"), (".xml", "🧾"), (".py", "🐍"), (".js", "📜"),
                    (".java", "☕"), (".ttf", "🔤"), (".apk", "📱")]:
        if low.endswith(ext):
            icon = ic
            break
    item.setText(COL_NAME, f"{icon} {item.text(COL_NAME)}")


def _apply_purpose_color(item, info):
    """按识别来源/删除建议给用途列上色。"""
    color = None
    if info:
        dl = info.get("deletable")
        if info.get("source") in ("ai", "learned"):
            color = QColor("#1565c0")
        elif dl == "safe":
            color = QColor("#2e7d32")
        elif dl == "keep":
            color = QColor("#c62828")
        elif dl == "caution":
            color = QColor("#e65100")
    if color:
        item.setForeground(COL_PURPOSE, color)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
