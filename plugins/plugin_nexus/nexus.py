# --- START OF FILE plugins/plugin_nexus/nexus.py ---

import os
import sys
import json
import shutil
import zipfile
import subprocess
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

# 插件市场的索引文件URL
PLUGIN_INDEX_URL = "https://gist.githubusercontent.com/KasumiKitsune/2859bb62eb51dc8056ac72d3722b9f01/raw/c46cd5dd38847c545aa880b452c908f4814ecaa5/index.json" # <-- 替换成你自己的Gist Raw URL

# ==============================================================================
# 1. 后台下载/克隆工作器
# ==============================================================================
class DownloaderWorker(QObject):
    finished = pyqtSignal(str, str)
    error = pyqtSignal(str)

    def __init__(self, task_type, url, target_dir):
        super().__init__()
        self.task_type = task_type
        self.url = url
        self.target_dir = target_dir

    def run(self):
        try:
            if self.task_type == 'release':
                self.download_release()
            elif self.task_type == 'source':
                self.clone_source()
        except Exception as e:
            self.error.emit(str(e))

    def download_release(self):
        response = requests.get(self.url, stream=True, timeout=30)
        response.raise_for_status()
        
        # 创建一个临时文件来保存zip
        fd, temp_zip_path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)

        with open(temp_zip_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        # 解压
        with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
            # 检查zip包的顶层目录
            top_level_dirs = {os.path.normpath(f).split(os.sep)[0] for f in zip_ref.namelist() if f}
            if len(top_level_dirs) != 1:
                raise ValueError("ZIP包格式不正确，根目录应只包含一个插件文件夹。")
            
            plugin_folder_name = list(top_level_dirs)[0]
            if os.path.exists(os.path.join(self.target_dir, plugin_folder_name)):
                raise FileExistsError(f"插件目录 '{plugin_folder_name}' 已存在。")

            zip_ref.extractall(self.target_dir)
        
        os.remove(temp_zip_path)
        self.finished.emit("安装成功！", f"插件 '{plugin_folder_name}' 已成功安装。")

    def clone_source(self):
        if not shutil.which('git'):
            raise EnvironmentError("`git` 命令未找到。请安装 Git 并确保它在系统的 PATH 中。")
        
        repo_name = os.path.splitext(os.path.basename(self.url))[0]
        target_path = os.path.join(self.target_dir, repo_name)
        
        if os.path.exists(target_path):
            raise FileExistsError(f"目标目录 '{target_path}' 已存在。")
            
        process = subprocess.Popen(['git', 'clone', self.url, target_path],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            raise RuntimeError(f"Git clone 失败:\n{stderr}")
        
        self.finished.emit("克隆成功！", f"插件仓库 '{repo_name}' 已成功克隆。")

# ==============================================================================
# 2. UI 对话框
# ==============================================================================
class NexusDialog(QDialog):
    def __init__(self, parent=None, icon_manager=None, plugins_dir=None):
        super().__init__(parent)
        self.icon_manager = icon_manager
        self.plugins_dir = plugins_dir
        self.online_plugins = []
        self.installed_plugin_ids = set(os.listdir(self.plugins_dir))

        self.setWindowTitle("插件市场")
        self.resize(900, 600)
        self.setMinimumSize(800, 500)
        self._init_ui()
        self._connect_signals()
        self.fetch_plugin_index()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        
        # Left Panel
        left_panel = QWidget(); left_layout = QVBoxLayout(left_panel)
        self.search_input = QLineEdit(); self.search_input.setPlaceholderText("按名称、作者或标签搜索...")
        self.plugin_list = QListWidget(); self.plugin_list.setSpacing(2)
        left_layout.addWidget(self.search_input); left_layout.addWidget(self.plugin_list)
        
        # Right Panel
        right_panel = QWidget(); right_layout = QVBoxLayout(right_panel)
        self.detail_name = QLabel("请从左侧选择一个插件"); self.detail_name.setObjectName("PluginNexusTitle")
        self.detail_author = QLabel(); self.detail_author.setObjectName("PluginNexusAuthor")
        self.detail_version = QLabel()
        self.detail_desc = QTextBrowser(); self.detail_desc.setOpenExternalLinks(True)
        self.install_release_btn = QPushButton("安装发布版"); self.install_release_btn.setObjectName("AccentButton")
        self.clone_source_btn = QPushButton("克隆源代码")
        
        h_layout = QHBoxLayout(); h_layout.addWidget(self.detail_author); h_layout.addStretch(); h_layout.addWidget(self.detail_version)
        btn_layout = QHBoxLayout(); btn_layout.addStretch(); btn_layout.addWidget(self.clone_source_btn); btn_layout.addWidget(self.install_release_btn)
        
        right_layout.addWidget(self.detail_name); right_layout.addLayout(h_layout)
        right_layout.addWidget(self.detail_desc, 1); right_layout.addLayout(btn_layout)
        
        splitter.addWidget(left_panel); splitter.addWidget(right_panel)
        splitter.setSizes([300, 600])
        main_layout.addWidget(splitter)
        
        self.progress_bar = QProgressBar(); self.progress_bar.setVisible(False); self.progress_bar.setTextVisible(False)
        main_layout.addWidget(self.progress_bar)

    def _connect_signals(self):
        self.search_input.textChanged.connect(self.filter_plugin_list)
        self.plugin_list.currentItemChanged.connect(self.update_details_view)
        self.install_release_btn.clicked.connect(lambda: self.on_install_clicked('release'))
        self.clone_source_btn.clicked.connect(lambda: self.on_install_clicked('source'))

    def fetch_plugin_index(self):
        if not REQUESTS_AVAILABLE:
            QMessageBox.critical(self, "依赖缺失", "无法获取在线列表，'requests' 库未安装。\n请运行: pip install requests"); return
        
        self.set_ui_enabled(False, "正在获取插件列表...")
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
            # 搜索逻辑
            search_haystack = f"{plugin_info['name']} {plugin_info['author']} {' '.join(plugin_info.get('tags', []))}".lower()
            if search_term not in search_haystack:
                continue
                
            item = QListWidgetItem(plugin_info['name'])
            item.setData(Qt.UserRole, plugin_info)
            # 标记已安装的插件
            if plugin_info['id'] in self.installed_plugin_ids:
                item.setIcon(self.icon_manager.get_icon("success") if self.icon_manager else QIcon())
            self.plugin_list.addItem(item)
    
    def update_details_view(self, current, previous):
        if not current:
            self.detail_name.setText("请从左侧选择一个插件"); self.detail_author.setText(""); self.detail_version.setText(""); self.detail_desc.clear()
            self.install_release_btn.setEnabled(False); self.clone_source_btn.setEnabled(False); return
            
        plugin_info = current.data(Qt.UserRole)
        self.detail_name.setText(plugin_info['name'])
        self.detail_author.setText(f"作者: {plugin_info['author']}")
        self.detail_version.setText(f"版本: {plugin_info['version']}")
        self.detail_desc.setHtml(f"<p>{plugin_info['description']}</p><p><b>标签:</b> <i>{', '.join(plugin_info.get('tags', []))}</i></p>")
        
        self.install_release_btn.setEnabled(bool(plugin_info.get('release_url')))
        self.clone_source_btn.setEnabled(bool(plugin_info.get('source_url')))

    def on_install_clicked(self, task_type):
        current_item = self.plugin_list.currentItem()
        if not current_item: return
        
        plugin_info = current_item.data(Qt.UserRole)
        url = plugin_info.get('release_url') if task_type == 'release' else plugin_info.get('source_url')
        if not url: return

        self.set_ui_enabled(False, "正在处理，请稍候...")
        self.thread = QThread()
        self.worker = DownloaderWorker(task_type, url, self.plugins_dir)
        self.worker.moveToThread(self.thread)
        
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.on_download_finished)
        self.worker.error.connect(self.on_download_error)
        self.thread.start()

    def on_download_finished(self, title, message):
        self.set_ui_enabled(True)
        QMessageBox.information(self, title, message)
        # 刷新已安装列表
        self.installed_plugin_ids = set(os.listdir(self.plugins_dir))
        self.filter_plugin_list()
        self.main_window.plugin_manager.scan_plugins() # 通知主程序插件列表已更新

    def on_download_error(self, error_message):
        self.set_ui_enabled(True)
        QMessageBox.critical(self, "操作失败", error_message)

    def set_ui_enabled(self, enabled, message=""):
        self.plugin_list.setEnabled(enabled)
        self.search_input.setEnabled(enabled)
        self.progress_bar.setVisible(not enabled)
        if not enabled: self.progress_bar.setRange(0,0); self.progress_bar.setToolTip(message)
        else: self.progress_bar.setRange(0,100)

# ==============================================================================
# 3. 插件主入口
# ==============================================================================
class PluginNexusPlugin(BasePlugin):
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.dialog_instance = None

    def setup(self): return True
    def teardown(self):
        if self.dialog_instance: self.dialog_instance.close()

    def execute(self, **kwargs):
        if self.dialog_instance is None:
            self.dialog_instance = NexusDialog(
                self.main_window,
                self.main_window.icon_manager,
                self.plugin_manager.plugins_dir
            )
            self.dialog_instance.finished.connect(lambda: setattr(self, 'dialog_instance', None))
        
        self.dialog_instance.show()
        self.dialog_instance.raise_()
        self.dialog_instance.activateWindow()