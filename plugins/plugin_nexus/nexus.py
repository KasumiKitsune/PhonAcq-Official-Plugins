# --- START OF FILE plugins/plugin_nexus/nexus.py (v3.0 - Robust & Simplified) ---

import os
import sys
import json
import shutil
import zipfile
import tempfile

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QTextBrowser, QSplitter,
                             QMessageBox, QProgressBar, QWidget)
from PyQt5.QtCore import Qt, QObject, QThread, pyqtSignal
from PyQt5.QtGui import QIcon

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    from plugin_system import BasePlugin
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

PLUGIN_INDEX_URL = "https://gist.githubusercontent.com/KasumiKitsune/2859bb62eb51dc8056ac72d3722b9f01/raw/index.json"

# ==============================================================================
# 1. 后台下载工作器 (v3.0 - Simplified)
# ==============================================================================
class DownloaderWorker(QObject):
    finished = pyqtSignal(str, str)
    error = pyqtSignal(str)

    def __init__(self, url, target_dir, plugin_id_to_remove=None):
        super().__init__()
        self.url = url
        self.target_dir = target_dir
        self.plugin_id_to_remove = plugin_id_to_remove # 用于覆盖安装

    def run(self):
        try:
            response = requests.get(self.url, stream=True, timeout=30)
            response.raise_for_status()
            
            fd, temp_zip_path = tempfile.mkstemp(suffix=".zip")
            os.close(fd)
            with open(temp_zip_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192): f.write(chunk)
            
            with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
                top_level_dirs = {os.path.normpath(f).split(os.sep)[0] for f in zip_ref.namelist() if f}
                if len(top_level_dirs) != 1: raise ValueError("ZIP包格式不正确，根目录应只包含一个插件文件夹。")
                
                plugin_folder_name = list(top_level_dirs)[0]
                final_install_path = os.path.join(self.target_dir, plugin_folder_name)
                
                # 如果是更新操作，先删除旧文件夹
                if os.path.exists(final_install_path):
                    shutil.rmtree(final_install_path)
                
                zip_ref.extractall(self.target_dir)

            os.remove(temp_zip_path)
            self.finished.emit("操作成功！", f"插件 '{plugin_folder_name}' 已成功安装/更新。")
        except Exception as e:
            self.error.emit(str(e))

# ==============================================================================
# 2. UI 对话框 (v3.0)
# ==============================================================================
class NexusDialog(QDialog):
    def __init__(self, main_window, icon_manager, plugins_dir):
        super().__init__(main_window)
        self.main_window = main_window
        self.icon_manager = icon_manager
        self.plugins_dir = plugins_dir
        self.online_plugins = []
        self.installed_plugin_ids = set()
        self.worker = None
        self.thread = None
        
        self.setWindowTitle("插件市场 (Nexus)")
        self.resize(900, 600)
        self.setMinimumSize(800, 500)
        self._init_ui()
        self._connect_signals()
        self.fetch_plugin_index()

    def _init_ui(self):
        main_layout = QVBoxLayout(self); splitter = QSplitter(Qt.Horizontal)
        left_panel = QWidget(); left_layout = QVBoxLayout(left_panel)
        self.search_input = QLineEdit(); self.search_input.setPlaceholderText("按名称、作者或标签搜索...")
        self.plugin_list = QListWidget(); self.plugin_list.setSpacing(2)
        left_layout.addWidget(self.search_input); left_layout.addWidget(self.plugin_list)
        right_panel = QWidget(); right_layout = QVBoxLayout(right_panel)
        self.detail_name = QLabel("请从左侧选择一个插件"); self.detail_name.setObjectName("PluginNexusTitle")
        self.detail_author = QLabel(); self.detail_author.setObjectName("PluginNexusAuthor"); self.detail_version = QLabel()
        self.detail_desc = QTextBrowser(); self.detail_desc.setOpenExternalLinks(True)
        self.install_btn = QPushButton("安装插件"); self.install_btn.setObjectName("AccentButton")
        self.reinstall_btn = QPushButton("重新安装/更新"); self.reinstall_btn.setObjectName("UpdateButton")
        
        h_layout = QHBoxLayout(); h_layout.addWidget(self.detail_author); h_layout.addStretch(); h_layout.addWidget(self.detail_version)
        btn_layout = QHBoxLayout(); btn_layout.addStretch(); btn_layout.addWidget(self.reinstall_btn); btn_layout.addWidget(self.install_btn)
        right_layout.addWidget(self.detail_name); right_layout.addLayout(h_layout); right_layout.addWidget(self.detail_desc, 1); right_layout.addLayout(btn_layout)
        
        splitter.addWidget(left_panel); splitter.addWidget(right_panel)
        splitter.setSizes([300, 600])
        main_layout.addWidget(splitter)
        
        self.progress_bar = QProgressBar(); self.progress_bar.setVisible(False); self.progress_bar.setTextVisible(False)
        main_layout.addWidget(self.progress_bar)

    def _connect_signals(self):
        self.search_input.textChanged.connect(self.filter_plugin_list)
        self.plugin_list.currentItemChanged.connect(self.update_details_view)
        self.install_btn.clicked.connect(self.on_install_clicked)
        self.reinstall_btn.clicked.connect(self.on_reinstall_clicked)

    def load_installed_plugins(self):
        self.installed_plugin_ids = set()
        for dir_name in os.listdir(self.plugins_dir):
            manifest_path = os.path.join(self.plugins_dir, dir_name, 'plugin.json')
            if os.path.isfile(manifest_path):
                try:
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                        self.installed_plugin_ids.add(meta['id'])
                except Exception:
                    continue

    def fetch_plugin_index(self):
        if not REQUESTS_AVAILABLE:
            QMessageBox.critical(self, "依赖缺失", "无法获取在线列表，'requests' 库未安装。\n请运行: pip install requests"); return
        
        self.set_ui_enabled(False, "正在获取插件列表...")
        self.load_installed_plugins()
        
        try:
            response = requests.get(PLUGIN_INDEX_URL, timeout=10)
            response.raise_for_status()
            self.online_plugins = response.json()
            self.filter_plugin_list()
            self.set_ui_enabled(True)
        except Exception as e:
            self.set_ui_enabled(True, "获取列表失败")
            QMessageBox.critical(self, "网络错误", f"无法从源获取插件列表:\n{e}")

    def filter_plugin_list(self):
        self.plugin_list.clear()
        search_term = self.search_input.text().lower()
        for plugin_info in self.online_plugins:
            search_haystack = f"{plugin_info['name']} {plugin_info['author']} {' '.join(plugin_info.get('tags', []))}".lower()
            if search_term and search_term not in search_haystack:
                continue
            
            item = QListWidgetItem(plugin_info['name'])
            item.setData(Qt.UserRole, plugin_info)
            if plugin_info['id'] in self.installed_plugin_ids:
                item.setIcon(self.icon_manager.get_icon("success") if self.icon_manager else QIcon())
            self.plugin_list.addItem(item)
    
    def update_details_view(self, current, previous):
        if not current:
            self.detail_name.setText("请从左侧选择一个插件"); self.detail_author.setText(""); self.detail_version.setText(""); self.detail_desc.clear()
            self.install_btn.setVisible(False); self.reinstall_btn.setVisible(False); return
            
        plugin_info = current.data(Qt.UserRole)
        self.detail_name.setText(plugin_info['name'])
        self.detail_author.setText(f"作者: {plugin_info['author']}")
        self.detail_version.setText(f"版本: {plugin_info['version']}")
        self.detail_desc.setHtml(f"<p>{plugin_info['description']}</p><p><b>标签:</b> <i>{', '.join(plugin_info.get('tags', []))}</i></p>")
        
        is_installed = plugin_info['id'] in self.installed_plugin_ids
        self.install_btn.setVisible(not is_installed)
        self.reinstall_btn.setVisible(is_installed)

    def _start_download_task(self, url):
        """统一的启动后台下载任务的入口。"""
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "请稍候", "一个操作正在进行中，请等待其完成后再试。")
            return

        self.set_ui_enabled(False, "正在处理，请稍候...")
        self.thread = QThread()
        self.worker = DownloaderWorker(url, self.plugins_dir)
        self.worker.moveToThread(self.thread)

        # --- [核心修正] 重新梳理信号连接 ---

        # 1. 线程启动后，开始执行 worker 的任务
        self.thread.started.connect(self.worker.run)

        # 2. worker 完成或出错后，都应该让线程退出
        self.worker.finished.connect(self.thread.quit)
        self.worker.error.connect(self.thread.quit)

        # 3. worker 的信号也连接到UI更新槽函数
        self.worker.finished.connect(self.on_download_finished)
        self.worker.error.connect(self.on_download_error)
        
        # 4. 线程的 finished 信号只负责最终的清理工作
        self.thread.finished.connect(self.on_thread_finished)
        
        # --- 结束修正 ---

        self.thread.start()

    def on_install_clicked(self):
        current_item = self.plugin_list.currentItem();
        if not current_item: return
        plugin_info = current_item.data(Qt.UserRole); url = plugin_info.get('release_url')
        if not url: QMessageBox.warning(self, "无可用链接", "此插件未提供发布版下载链接。"); return
        self._start_download_task(url)
        
    def on_reinstall_clicked(self):
        current_item = self.plugin_list.currentItem();
        if not current_item: return
        plugin_info = current_item.data(Qt.UserRole); url = plugin_info.get('release_url')
        if not url: QMessageBox.warning(self, "无可用链接", "此插件未提供发布版下载链接。"); return
            
        reply = QMessageBox.question(self, "确认操作", f"您确定要重新安装/更新插件 '{plugin_info['name']}' 吗？\n这将覆盖本地的同名插件。", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply == QMessageBox.No: return
        self._start_download_task(url)

    def on_download_finished(self, title, message):
        QMessageBox.information(self, title, message)
        self.load_installed_plugins()
        self.filter_plugin_list()
        self.main_window.plugin_manager.scan_plugins()

    def on_download_error(self, error_message):
        QMessageBox.critical(self, "操作失败", error_message)

    def on_thread_finished(self):
        """[新增] 线程安全地清理worker和thread对象。"""
        if self.worker: self.worker.deleteLater(); self.worker = None
        if self.thread: self.thread.deleteLater(); self.thread = None
        self.set_ui_enabled(True)

    def set_ui_enabled(self, enabled, message=""):
        self.plugin_list.setEnabled(enabled); self.search_input.setEnabled(enabled)
        self.progress_bar.setVisible(not enabled)
        if not enabled: self.progress_bar.setRange(0,0); self.progress_bar.setToolTip(message)
        else: self.progress_bar.setRange(0,100)
    
    def closeEvent(self, event):
        """[新增] 在关闭窗口时，确保后台线程已停止。"""
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "后台任务", "正在等待后台任务完成，请稍候...")
            # 这是一个简单的处理方式，不让用户关闭。
            # 更复杂的可以实现取消功能。
            event.ignore()
            return
        super().closeEvent(event)

# ==============================================================================
# 3. 插件主入口 (保持不变)
# ==============================================================================
class PluginNexusPlugin(BasePlugin):
    # ... (此类代码完全不变)
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager); self.dialog_instance = None
    def setup(self): return True
    def teardown(self):
        if self.dialog_instance: self.dialog_instance.close()
    def execute(self, **kwargs):
        # [核心修正] 从 kwargs 中获取 parent_dialog，如果没有，则默认为主窗口
        parent_widget = kwargs.get('parent_dialog', self.main_window)
        
        if self.dialog_instance is None:
            # [核心修正] 将正确的父级传递给 NexusDialog 的构造函数
            self.dialog_instance = NexusDialog(
                parent_widget, # 使用动态获取的父级
                self.main_window.icon_manager,
                self.plugin_manager.plugins_dir
            )
            self.dialog_instance.finished.connect(lambda: setattr(self, 'dialog_instance', None))
        
        # 确保窗口的父级是最新的（以防万一）
        if self.dialog_instance.parent() != parent_widget:
            self.dialog_instance.setParent(parent_widget)

        self.dialog_instance.show()
        self.dialog_instance.raise_()
        self.dialog_instance.activateWindow()