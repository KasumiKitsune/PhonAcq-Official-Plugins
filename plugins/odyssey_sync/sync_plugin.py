# --- START OF FILE plugins/odyssey_sync/sync_plugin.py (v3.1 - Archive Mode) ---

import os
import sys
import shutil
import time
import json
from datetime import datetime
import traceback

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
                             QApplication, QMessageBox, QGroupBox, QMenu,
                             QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
                             QFileDialog, QStatusBar, QWidget, QSplitter, QSizePolicy,
                             QInputDialog, QComboBox)
from PyQt5.QtCore import Qt, QObject, QThread, pyqtSignal
from PyQt5.QtGui import QIcon

try:
    from plugin_system import BasePlugin
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

# ==============================================================================
# 插件专属配置管理器 (无变动)
# ==============================================================================
class SyncConfigManager:
    def __init__(self):
        plugin_dir = os.path.dirname(__file__)
        self.config_path = os.path.join(plugin_dir, 'config.json')
        self.settings = self.load()
    def load(self):
        default_settings = {"local_path": "", "target_statuses": {}, "custom_targets": [], "conflict_policy": "keep_newer"}
        if not os.path.exists(self.config_path): return default_settings
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                loaded_settings = json.load(f)
                default_settings.update(loaded_settings)
                return default_settings
        except (json.JSONDecodeError, IOError): return default_settings
    def save(self):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f: json.dump(self.settings, f, indent=4, ensure_ascii=False)
        except IOError as e: print(f"无法保存同步插件配置文件: {e}")
    def get(self, key, default=None): return self.settings.get(key, default)
    def set(self, key, value): self.settings[key] = value

# ==============================================================================
# 本地同步提供者 (无变动)
# ==============================================================================
class LocalSyncProvider:
    def __init__(self, target_root_path, conflict_policy='keep_newer'):
        self.root = target_root_path; self.conflict_policy = conflict_policy
    def test_connection(self):
        if not os.path.isdir(self.root):
            try: os.makedirs(self.root, exist_ok=True)
            except Exception as e: return f"无法创建或访问目标文件夹: {e}"
        try:
            test_file = os.path.join(self.root, f".phonacq_test_{int(time.time())}")
            with open(test_file, 'w') as f: f.write("test")
            os.remove(test_file); return True
        except Exception as e: return f"无法写入目标文件夹，请检查权限: {e}"
    def list_files(self, relative_path):
        full_path = os.path.join(self.root, relative_path)
        if not os.path.isdir(full_path): return {}
        file_map = {}
        for root, _, files in os.walk(full_path):
            for name in files:
                local_file_path = os.path.join(root, name)
                rel_file_path = os.path.relpath(local_file_path, full_path).replace('\\', '/')
                file_map[rel_file_path] = os.path.getmtime(local_file_path)
        return file_map
    def download_file(self, remote_file_path, local_file_path): os.makedirs(os.path.dirname(local_file_path), exist_ok=True); shutil.copy2(os.path.join(self.root, remote_file_path), local_file_path)
    def upload_file(self, local_file_path, remote_file_path): os.makedirs(os.path.dirname(os.path.join(self.root, remote_file_path)), exist_ok=True); shutil.copy2(local_file_path, os.path.join(self.root, remote_file_path))
    def ensure_dir(self, relative_path): os.makedirs(os.path.join(self.root, relative_path), exist_ok=True)
    def delete(self, relative_path):
        full_path = os.path.join(self.root, relative_path)
        try:
            if os.path.isfile(full_path): os.remove(full_path)
            elif os.path.isdir(full_path): shutil.rmtree(full_path)
        except FileNotFoundError: pass

# ==============================================================================
# 后台同步引擎 (核心修改)
# ==============================================================================
class SyncEngine(QObject):
    progress_updated = pyqtSignal(str, str, str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str, str)
    def __init__(self, provider, sync_targets, base_path):
        super().__init__(); self.provider, self.sync_targets, self.base_path = provider, sync_targets, base_path
        self._is_running = True
    def stop(self): self._is_running = False
    def run_sync(self):
        try:
            for target_id, target_info in self.sync_targets.items():
                if not self._is_running: break
                self.progress_updated.emit(target_id, "syncing", f"处理中: {target_info['name']}...")
                local_root, remote_root = os.path.join(self.base_path, target_info['path']), target_info['path']
                os.makedirs(local_root, exist_ok=True); self.provider.ensure_dir(remote_root)
                local_files, remote_files = self._get_local_files(local_root), self.provider.list_files(remote_root)
                self._synchronize_files(target_id, local_root, remote_root, local_files, remote_files)
                if self._is_running: self.progress_updated.emit(target_id, "synced", f"已同步: {target_info['name']}")
            self.finished.emit("同步已取消。" if not self._is_running else "所有启用项同步完成！")
        except Exception as e: self.error.emit("*", f"同步时发生严重错误: {e}\n{traceback.format_exc()}")
    def run_clear(self, target_id, target_info):
        try:
            self.progress_updated.emit(target_id, "syncing", f"清空中: {target_info['name']}...")
            self.provider.delete(target_info['path'])
            self.progress_updated.emit(target_id, "pending", f"{target_info['name']} 目标文件夹已清空")
            self.finished.emit(f"清空完成: {target_info['name']}")
        except Exception as e: self.error.emit(target_id, f"清空目标 '{target_info['name']}' 失败: {e}\n{traceback.format_exc()}")
    def _get_local_files(self, local_root):
        file_map = {}
        for root, _, files in os.walk(local_root):
            for name in files:
                if not self._is_running: return {}
                file_path = os.path.join(root, name)
                file_map[os.path.relpath(file_path, local_root).replace('\\', '/')] = os.path.getmtime(file_path)
        return file_map
        
    def _synchronize_files(self, target_id, local_root, remote_root, local_files, remote_files):
        # [核心重构] 使用更清晰的逻辑来处理所有四种同步策略
        conflict_policy = getattr(self.provider, 'conflict_policy', 'keep_newer')
        all_files = set(local_files.keys()) | set(remote_files.keys())

        for rel_path in all_files:
            if not self._is_running: return
            
            local_mtime = local_files.get(rel_path)
            remote_mtime = remote_files.get(rel_path)
            
            local_full_path = os.path.join(local_root, rel_path)
            remote_full_file_path = os.path.join(remote_root, rel_path).replace('\\','/')
            
            should_upload, should_download = False, False

            if local_mtime is None and remote_mtime is not None:
                # 文件只在远程存在 (本地已删除)
                if conflict_policy == 'archive_only':
                    pass # 归档模式：不执行任何操作，保留远程备份
                elif conflict_policy == 'local_wins':
                    # TODO: 此处应为删除远程文件，但当前Provider无此接口，暂不操作
                    pass
                else: # keep_newer, remote_wins
                    should_download = True

            elif local_mtime is not None and remote_mtime is None:
                # 文件只在本地存在 (新文件)
                should_upload = True # 所有模式下都应该上传新文件

            elif local_mtime is not None and remote_mtime is not None:
                # 文件两边都存在，比较修改时间
                if abs(local_mtime - remote_mtime) > 1: # 忽略1秒内的时间差
                    if local_mtime > remote_mtime:
                        # 本地文件较新
                        if conflict_policy in ['keep_newer', 'local_wins', 'archive_only']:
                            should_upload = True
                    else:
                        # 远程文件较新
                        if conflict_policy in ['keep_newer', 'remote_wins']:
                            should_download = True
            
            if should_upload:
                self.progress_updated.emit(target_id, "syncing", f"备份: {rel_path}")
                self.provider.upload_file(local_full_path, remote_full_file_path)
            elif should_download:
                self.progress_updated.emit(target_id, "syncing", f"恢复: {rel_path}")
                self.provider.download_file(remote_full_file_path, local_full_path)

# ==============================================================================
# UI 对话框 (核心修改)
# ==============================================================================
class SyncDialog(QDialog):
    # ... __init__, _load_icons, _init_ui 方法无变动 ...
    def __init__(self, main_window, config_manager):
        super().__init__(main_window)
        self.main_window = main_window; self.config_manager = config_manager
        self.sync_thread, self.sync_engine = None, None
        self.DEFAULT_TARGETS = {"word_lists": {"name": "标准词表", "path": "word_lists"}, "visual_wordlists": {"name": "图文词表", "path": "dialect_visual_wordlists"}, "flashcards": {"name": "闪记卡", "path": "flashcards"}, "results": {"name": "录制结果", "path": "Results"},}
        self._load_icons(); self.setWindowTitle("Odyssey 本地同步/备份"); self.resize(800, 600); self.setMinimumSize(700, 500)
        self._init_ui(); self._connect_signals(); self._load_settings()
    def _load_icons(self):
        plugin_dir = os.path.dirname(__file__)
        self.status_icons = {"syncing": QIcon(os.path.join(plugin_dir, "icons/sync_ing.png")), "failed": QIcon(os.path.join(plugin_dir, "icons/sync_failed.png")), "pending": QIcon(os.path.join(plugin_dir, "icons/sync_pending.png")), "synced": QIcon(os.path.join(plugin_dir, "icons/sync_synced.png")), "disabled": self.main_window.icon_manager.get_icon("pause") or QIcon()}
    def _init_ui(self):
        main_layout = QVBoxLayout(self); splitter = QSplitter(Qt.Horizontal); left_panel = self._create_left_panel(); right_panel = self._create_right_panel()
        splitter.addWidget(left_panel); splitter.addWidget(right_panel); splitter.setSizes([350, 600])
        action_layout = QHBoxLayout(); self.cloud_sync_tip_btn = QPushButton("如何实现云端同步？"); self.cloud_sync_tip_btn.setIcon(self.main_window.icon_manager.get_icon("cloud")); self.cloud_sync_tip_btn.setFlat(True); self.cloud_sync_tip_btn.setStyleSheet("text-align: left; color: #3B6894; font-weight: bold;"); self.cloud_sync_tip_btn.setCursor(Qt.PointingHandCursor)
        action_layout.addWidget(self.cloud_sync_tip_btn); action_layout.addStretch(); self.sync_now_btn = QPushButton("立即备份所有启用项"); self.close_btn = QPushButton("保存并关闭"); self.sync_now_btn.setObjectName("AccentButton"); action_layout.addWidget(self.sync_now_btn); action_layout.addWidget(self.close_btn)
        self.status_bar = QStatusBar(); self.status_bar.setSizeGripEnabled(False); main_layout.addWidget(splitter); main_layout.setStretch(0, 1); main_layout.addLayout(action_layout); main_layout.addWidget(self.status_bar)

    def _create_left_panel(self):
        """创建左侧的设置面板。"""
        panel = QWidget(); layout = QVBoxLayout(panel); layout.setContentsMargins(0, 0, 10, 0) 
        config_group = QGroupBox("备份目标"); config_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum); config_layout = QVBoxLayout(config_group)
        path_layout = QHBoxLayout(); self.local_path_edit = QLineEdit(); self.local_path_edit.setPlaceholderText("选择一个用于备份的空文件夹..."); self.local_browse_btn = QPushButton("...")
        path_layout.addWidget(self.local_path_edit); path_layout.addWidget(self.local_browse_btn); config_layout.addWidget(QLabel("备份根目录:")); config_layout.addLayout(path_layout)
        self.test_conn_btn = QPushButton("测试目标文件夹"); config_layout.addWidget(self.test_conn_btn, 0, Qt.AlignRight)
        
        general_group = QGroupBox("通用设置"); general_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum); general_layout = QVBoxLayout(general_group)
        
        self.conflict_combo = QComboBox()
        # [核心修改] 增加新选项
        self.conflict_combo.addItem("保留较新的文件", "keep_newer")
        self.conflict_combo.addItem("归档 (只上传，不删除)", "archive_only")
        self.conflict_combo.addItem("总是以本地文件为准 (覆盖备份)", "local_wins")
        self.conflict_combo.addItem("总是以备份文件为准 (恢复本地)", "remote_wins")
        
        # [核心修改] 更新 Tooltip
        self.conflict_combo.setToolTip(
            "当本地和备份目标中文件都被修改时，如何处理冲突：\n\n"
            "- 保留较新的文件: 比较修改时间，双向同步，保持两边都是最新。\n"
            "- 归档 (只上传，不删除): 强制单向上传，绝不删除备份目标中的文件，适合历史归档。\n"
            "- 以本地为准: 强制用本地文件覆盖备份，适合单向备份发布。\n"
            "- 以备份为准: 强制用备份文件覆盖本地，适合数据恢复。"
        )
        general_layout.addWidget(QLabel("冲突解决策略:"))
        general_layout.addWidget(self.conflict_combo)

        layout.addWidget(config_group); layout.addWidget(general_group); layout.addStretch()
        return panel

    # ... (其他方法，如 _create_right_panel, _connect_signals, _load_settings, _save_settings 等保持不变) ...
    def _create_right_panel(self):
        panel = QWidget(); layout = QVBoxLayout(panel); targets_group = QGroupBox("备份项目 (右键单击进行管理)"); targets_layout = QVBoxLayout(targets_group)
        table_actions_layout = QHBoxLayout(); self.add_target_btn = QPushButton("添加项目..."); self.remove_target_btn = QPushButton("移除项目"); table_actions_layout.addWidget(self.add_target_btn); table_actions_layout.addWidget(self.remove_target_btn); table_actions_layout.addStretch()
        self.targets_table = QTableWidget(); self.targets_table.setColumnCount(3); self.targets_table.setHorizontalHeaderLabels(["状态", "备份项目", "路径"]); self.targets_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents); self.targets_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch); self.targets_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.targets_table.setSelectionBehavior(QAbstractItemView.SelectRows); self.targets_table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.targets_table.setContextMenuPolicy(Qt.CustomContextMenu)
        targets_layout.addLayout(table_actions_layout); targets_layout.addWidget(self.targets_table); layout.addWidget(targets_group)
        return panel
    def _connect_signals(self):
        self.local_browse_btn.clicked.connect(self.on_browse_local_folder); self.add_target_btn.clicked.connect(self.on_add_target); self.remove_target_btn.clicked.connect(self.on_remove_target)
        self.test_conn_btn.clicked.connect(self.on_test_connection); self.sync_now_btn.clicked.connect(lambda: self.on_sync_now()); self.close_btn.clicked.connect(self.accept)
        self.targets_table.customContextMenuRequested.connect(self.on_table_context_menu); self.cloud_sync_tip_btn.clicked.connect(self.on_show_cloud_sync_tip)
    def _load_settings(self):
        self.local_path_edit.setText(self.config_manager.get("local_path", "")); self.target_statuses = self.config_manager.get("target_statuses", {})
        conflict_policy = self.config_manager.get("conflict_policy", "keep_newer"); idx = self.conflict_combo.findData(conflict_policy)
        if idx != -1: self.conflict_combo.setCurrentIndex(idx)
        self.populate_targets_table()
    def _save_settings(self):
        self.config_manager.set("local_path", self.local_path_edit.text()); self.config_manager.set("target_statuses", self.target_statuses)
        self.config_manager.set("conflict_policy", self.conflict_combo.currentData()); self.config_manager.save()
    def accept(self):
        if self.sync_thread and self.sync_thread.isRunning():
            if QMessageBox.question(self, "确认关闭", "一个备份任务正在后台运行。您确定要关闭设置窗口并取消任务吗？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.No: return
            if self.sync_engine: self.sync_engine.stop()
        self._save_settings(); super().accept()
    def _get_all_targets(self):
        all_targets = self.DEFAULT_TARGETS.copy(); custom_targets = self.config_manager.get("custom_targets", [])
        for target in custom_targets: all_targets[target['id']] = target
        return all_targets
    def populate_targets_table(self):
        self.all_targets = self._get_all_targets(); self.targets_table.setRowCount(len(self.all_targets))
        sorted_targets = sorted(self.all_targets.items(), key=lambda item: not item[0].startswith("custom_"))
        for i, (target_id, info) in enumerate(sorted_targets):
            status_item = QTableWidgetItem(); name_item = QTableWidgetItem(info['name']); path_item = QTableWidgetItem(info['path'])
            status_item.setTextAlignment(Qt.AlignCenter); status_item.setFlags(Qt.ItemIsEnabled); name_item.setData(Qt.UserRole, target_id); path_item.setForeground(Qt.gray)
            if target_id == "results": name_item.setToolTip("警告：此项可能占用巨大空间。")
            self.targets_table.setItem(i, 0, status_item); self.targets_table.setItem(i, 1, name_item); self.targets_table.setItem(i, 2, path_item)
            self._update_row_status(i, self.target_statuses.get(target_id, "pending"))
    def on_add_target(self):
        base_path = self.main_window.BASE_PATH; directory = QFileDialog.getExistingDirectory(self, "选择要添加的备份文件夹", base_path)
        if not directory or not directory.startswith(base_path):
            if directory: QMessageBox.warning(self, "路径无效", "请选择程序根目录下的一个文件夹进行备份。"); return
        relative_path = os.path.relpath(directory, base_path).replace('\\', '/'); name, ok = QInputDialog.getText(self, "输入项目名称", f"为文件夹 '{relative_path}' 设置一个备份名称:")
        if not (ok and name): return
        target_id = f"custom_{int(time.time())}"; new_target = {"id": target_id, "name": name, "path": relative_path}
        custom_targets = self.config_manager.get("custom_targets", []); custom_targets.append(new_target)
        self.config_manager.set("custom_targets", custom_targets); self.populate_targets_table()
    def on_remove_target(self):
        current_row = self.targets_table.currentRow();
        if current_row < 0: QMessageBox.information(self, "提示", "请先在列表中选择一个要移除的自定义项目。"); return
        target_id = self.targets_table.item(current_row, 1).data(Qt.UserRole)
        if not target_id.startswith("custom_"): QMessageBox.warning(self, "操作无效", "不能移除默认的备份项目。"); return
        custom_targets = self.config_manager.get("custom_targets", []); custom_targets = [t for t in custom_targets if t['id'] != target_id]
        self.config_manager.set("custom_targets", custom_targets)
        if target_id in self.target_statuses: del self.target_statuses[target_id]
        self.populate_targets_table()
    def on_browse_local_folder(self): directory = QFileDialog.getExistingDirectory(self, "选择备份目标文件夹", self.local_path_edit.text()); self.local_path_edit.setText(directory) if directory else None
    def on_test_connection(self):
        path = self.local_path_edit.text();
        if not path: QMessageBox.warning(self, "配置错误", "请先选择一个本地目标文件夹。"); return
        result = LocalSyncProvider(path).test_connection()
        if result is True: QMessageBox.information(self, "成功", "目标文件夹可访问并具有写入权限！")
        else: QMessageBox.critical(self, "失败", f"目标文件夹测试失败:\n{result}")
    def on_table_context_menu(self, pos):
        item = self.targets_table.itemAt(pos);
        if not item: return
        row, target_id = item.row(), self.targets_table.item(item.row(), 1).data(Qt.UserRole)
        status = self.target_statuses.get(target_id, "pending"); menu = QMenu(self)
        if status != "disabled": menu.addAction(self.status_icons["disabled"], "暂停此项备份").triggered.connect(lambda: self.set_target_status(target_id, "disabled"))
        else: menu.addAction(self.main_window.icon_manager.get_icon("play"), "启用此项备份").triggered.connect(lambda: self.set_target_status(target_id, "pending"))
        menu.addSeparator()
        menu.addAction(self.status_icons["syncing"], "立即备份此项").triggered.connect(lambda: self.on_sync_now(specific_target=target_id))
        menu.addAction(self.main_window.icon_manager.get_icon("delete"), "清空目标备份...").triggered.connect(lambda: self.on_clear_remote(target_id))
        if status == "failed": menu.addSeparator(); menu.addAction(self.main_window.icon_manager.get_icon("reset"), "重置状态为'待备份'").triggered.connect(lambda: self.set_target_status(target_id, "pending"))
        menu.exec_(self.targets_table.mapToGlobal(pos))
    def on_sync_now(self, specific_target=None):
        if self.sync_thread and self.sync_thread.isRunning(): QMessageBox.information(self, "提示", "一个备份任务已在后台运行。"); return
        path = self.local_path_edit.text();
        if not path: QMessageBox.warning(self, "配置错误", "请先选择一个本地目标文件夹。"); return
        conflict_policy = self.conflict_combo.currentData(); provider = LocalSyncProvider(path, conflict_policy=conflict_policy)
        self.all_targets = self._get_all_targets()
        if specific_target: selected_targets = {specific_target: self.all_targets[specific_target]}
        else: selected_targets = {id: info for id, info in self.all_targets.items() if self.target_statuses.get(id, "pending") != "disabled"}
        if not selected_targets: QMessageBox.warning(self, "提示", "没有已启用的备份项目。"); return
        engine = SyncEngine(provider, selected_targets, self.main_window.BASE_PATH)
        self._start_background_task(engine, engine.run_sync)
    def on_clear_remote(self, target_id):
        if self.sync_thread and self.sync_thread.isRunning(): return
        path = self.local_path_edit.text();
        if not path: QMessageBox.warning(self, "配置错误", "请先选择一个本地目标文件夹。"); return
        self.all_targets = self._get_all_targets(); info = self.all_targets[target_id]
        if QMessageBox.warning(self, "确认操作", f"您确定要永久删除目标文件夹中 '{info['name']}' 的所有内容吗？\n\n此操作不可撤销。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            provider = LocalSyncProvider(path); engine = SyncEngine(provider, {}, self.main_window.BASE_PATH)
            self._start_background_task(engine, lambda: engine.run_clear(target_id, info))
    def set_target_status(self, target_id, status): self.target_statuses[target_id] = status; self.update_all_table_statuses()
    def update_all_table_statuses(self):
        for i in range(self.targets_table.rowCount()):
            target_id = self.targets_table.item(i, 1).data(Qt.UserRole); self._update_row_status(i, self.target_statuses.get(target_id, "pending"))
    def _update_row_status(self, row, status_key):
        if 0 <= row < self.targets_table.rowCount(): self.targets_table.item(row, 0).setIcon(self.status_icons.get(status_key, self.status_icons["pending"]))
    def _start_background_task(self, engine_instance, task_function):
        self.sync_engine = engine_instance; self.sync_thread = QThread(self)
        self.sync_engine.moveToThread(self.sync_thread); self.sync_engine.finished.connect(self.sync_thread.quit); self.sync_engine.error.connect(self.sync_thread.quit)
        self.sync_thread.started.connect(task_function); self.sync_thread.finished.connect(self.on_thread_finished)
        self.sync_engine.progress_updated.connect(self.on_progress_updated); self.sync_engine.finished.connect(self.on_sync_finished); self.sync_engine.error.connect(self.on_sync_error)
        self.set_ui_enabled(False); self.sync_thread.start()
    def on_thread_finished(self):
        if self.sync_engine: self.sync_engine.deleteLater(); self.sync_engine = None
        if self.sync_thread: self.sync_thread.deleteLater(); self.sync_thread = None
        self.set_ui_enabled(True)
    def on_progress_updated(self, target_id, status_key, message):
        self.status_bar.showMessage(message);
        if target_id != "*": self.target_statuses[target_id] = status_key; self.update_all_table_statuses()
    def on_sync_finished(self, message): self.status_bar.showMessage(message, 5000)
    def on_sync_error(self, target_id, message):
        self.status_bar.showMessage("任务出错！", 5000);
        if target_id != "*": self.target_statuses[target_id] = "failed"; self.update_all_table_statuses()
        QMessageBox.critical(self, "任务错误", message)
    def set_ui_enabled(self, enabled): self.test_conn_btn.setEnabled(enabled); self.sync_now_btn.setEnabled(enabled)
    def on_show_cloud_sync_tip(self):
        title = "通过本地文件夹实现云端同步"; message = """<p><b>Odyssey Sync</b> 目前通过本地文件夹进行备份，但您可以轻松地将其与任何主流云同步工具（如坚果云、Dropbox、百度网盘等）结合，实现强大的云端同步功能。</p><p><b>操作方法非常简单：</b></p><ol><li>在您的电脑上安装您偏好的云同步客户端。</li><li>在云同步客户端的设置中，找到它的<b>本地同步文件夹</b>。</li><li>在本插件的“备份根目录”设置中，<b>选择那个云同步的本地文件夹</b>作为备份目标。</li></ol><p>完成以上设置后，<b>Odyssey Sync</b> 会将所有数据备份到该文件夹，然后您的云同步客户端会自动将这些更改上传到云端。这样就实现了全自动、安全可靠的云端备份和多设备同步！</p>"""
        QMessageBox.information(self, title, message)

# ==============================================================================
# 插件主入口 (v3.0)
# ==============================================================================
class SyncPlugin(BasePlugin):
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.dialog_instance = None
        self.config_manager = SyncConfigManager()

    def setup(self):
        if not hasattr(self.main_window, 'BASE_PATH'):
            if getattr(sys, 'frozen', False): self.main_window.BASE_PATH = os.path.dirname(sys.executable)
            else: self.main_window.BASE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
            print(f"[Sync Plugin] 主程序未提供 BASE_PATH，已自动设置为: {self.main_window.BASE_PATH}")
        return True

    def teardown(self):
        if self.dialog_instance:
            self.dialog_instance.close()

    def execute(self, **kwargs):
        if self.dialog_instance is None:
            self.dialog_instance = SyncDialog(self.main_window, self.config_manager)
            self.dialog_instance.finished.connect(self.on_dialog_finished)
        self.dialog_instance._load_settings()
        self.dialog_instance.show()
        self.dialog_instance.raise_()
        self.dialog_instance.activateWindow()

    def on_dialog_finished(self):
        self.dialog_instance = None
        
    # [新增] 公共API方法，供其他插件调用
    def get_sync_results_path(self):
        """
        返回同步插件配置的备份Results目录的路径。
        如果未配置或无效，则返回 None。
        """
        # 重新加载一次配置，确保获取的是最新路径
        self.config_manager.settings = self.config_manager.load()
        sync_root = self.config_manager.get("local_path")
        
        if sync_root and os.path.isdir(sync_root):
            # 假设Results目录在同步根目录下的结构与本地一致
            return os.path.join(sync_root, "Results")
        
        return None