# --- START OF FILE plugins/plugin_nexus/nexus.py (v3.2 - Threading & Logic Fix) ---

import os
import sys
import json
import shutil
import zipfile
import tempfile
import re

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QTextBrowser, QSplitter,
                             QMessageBox, QWidget, QApplication, QComboBox,
                             QProgressDialog)
from PyQt5.QtCore import Qt, QObject, QThread, pyqtSignal, QTimer
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
# 1. 专用后台工作器
# ==============================================================================

# [新增] 专门用于获取索引JSON文本的工作器
class IndexFetcherWorker(QObject):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            response = requests.get(self.url, timeout=10)
            response.raise_for_status()
            self.finished.emit(response.text) # 发射获取到的纯文本内容
        except requests.exceptions.RequestException as e:
            self.error.emit(f"网络请求失败: {e}")
        except Exception as e:
            self.error.emit(str(e))

# [保持不变] 用于下载和解压ZIP插件的工作器
class DownloaderWorker(QObject):
    finished = pyqtSignal(str, str)
    error = pyqtSignal(str)

    def __init__(self, url, target_dir):
        super().__init__()
        self.url = url
        self.target_dir = target_dir
        self._is_running = True

    def stop(self):
        self._is_running = False

    def run(self):
        try:
            response = requests.get(self.url, stream=True, timeout=30)
            if response.status_code == 404:
                error_msg = (f"服务器上未找到该版本的文件 (404 Not Found)。\n"
                             f"这可能意味着此插件在该版本 ({self.url.split('/')[-2]}) 尚未发布。")
                self.error.emit(error_msg)
                return
            response.raise_for_status()
            
            fd, temp_zip_path = tempfile.mkstemp(suffix=".zip")
            os.close(fd)
            with open(temp_zip_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if not self._is_running:
                        os.remove(temp_zip_path)
                        return
                    f.write(chunk)
            
            with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
                top_level_dirs = {os.path.normpath(f).split(os.sep)[0] for f in zip_ref.namelist() if f}
                if len(top_level_dirs) != 1: raise ValueError("ZIP包格式不正确，根目录应只包含一个插件文件夹。")
                
                plugin_folder_name = list(top_level_dirs)[0]
                final_install_path = os.path.join(self.target_dir, plugin_folder_name)
                
                if os.path.exists(final_install_path):
                    shutil.rmtree(final_install_path)
                
                zip_ref.extractall(self.target_dir)

            os.remove(temp_zip_path)
            self.finished.emit("操作成功！", f"插件 '{plugin_folder_name}' 已成功安装/更新。")
        except requests.exceptions.RequestException as e:
            self.error.emit(f"网络请求失败: {e}")
        except Exception as e:
            self.error.emit(f"处理失败: {e}")

# ==============================================================================
# 2. UI 对话框 (v3.2)
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
        self.progress_dialog = None
        
        self.setWindowTitle("插件市场 (Nexus)")
        self.resize(900, 600)
        self.setMinimumSize(800, 500)
        
        self._init_ui()
        self._connect_signals()
        
        QTimer.singleShot(0, self.fetch_plugin_index)

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
        self.version_label = QLabel("选择适用版本:")
        self.version_combo = QComboBox()
        self.version_combo.setToolTip(
            "<b>选择插件的兼容版本</b>"
            "<hr>"
            "这里选择的是插件所适配的<b>【主程序版本】</b>，而非插件自身的版本号。"
            "<br><br>"
            "<b>通常，您应选择列表顶部的最新版本</b>，以确保与您当前的软件兼容并获得最新功能。"
            "<br><br>"
            "仅当您特意为旧版的 PhonAcq Assistant 安装插件时，才需要选择一个较早的版本。"
        )
        version_layout = QHBoxLayout(); version_layout.addWidget(self.version_label); version_layout.addWidget(self.version_combo)
        self.install_btn = QPushButton("安装插件"); self.install_btn.setObjectName("AccentButton")
        self.reinstall_btn = QPushButton("重新安装/更新"); self.reinstall_btn.setObjectName("UpdateButton")
        h_layout = QHBoxLayout(); h_layout.addWidget(self.detail_author); h_layout.addStretch(); h_layout.addWidget(self.detail_version)
        btn_layout = QHBoxLayout(); btn_layout.addLayout(version_layout); btn_layout.addStretch(); btn_layout.addWidget(self.reinstall_btn); btn_layout.addWidget(self.install_btn)
        right_layout.addWidget(self.detail_name); right_layout.addLayout(h_layout); right_layout.addWidget(self.detail_desc, 1); right_layout.addLayout(btn_layout)
        splitter.addWidget(left_panel); splitter.addWidget(right_panel)
        splitter.setSizes([300, 600])
        main_layout.addWidget(splitter)

    def _connect_signals(self):
        self.search_input.textChanged.connect(self.filter_plugin_list)
        self.plugin_list.currentItemChanged.connect(self.update_details_view)
        self.install_btn.clicked.connect(self.on_install_clicked)
        self.reinstall_btn.clicked.connect(self.on_reinstall_clicked)

    def load_installed_plugins(self):
        self.installed_plugin_ids = set()
        if not os.path.isdir(self.plugins_dir):
            os.makedirs(self.plugins_dir, exist_ok=True)
            return
        for dir_name in os.listdir(self.plugins_dir):
            manifest_path = os.path.join(self.plugins_dir, dir_name, 'plugin.json')
            if os.path.isfile(manifest_path):
                try:
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                        if 'id' in meta: self.installed_plugin_ids.add(meta['id'])
                except Exception: continue

    def fetch_plugin_index(self):
        if not REQUESTS_AVAILABLE:
            QMessageBox.critical(self, "依赖缺失", "无法获取在线列表，'requests' 库未安装。\n请运行: pip install requests")
            self.plugin_list.clear(); self.plugin_list.addItem("错误: 缺少 'requests' 库")
            return
        
        self.progress_dialog = QProgressDialog("正在获取插件列表...", "取消", 0, 0, self)
        self.progress_dialog.setWindowTitle("插件市场"); self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setCancelButton(None); self.progress_dialog.setRange(0, 0)
        self.progress_dialog.show()
        QApplication.processEvents()
        
        self.load_installed_plugins()
        
        # [核心修正] 使用专用的 IndexFetcherWorker
        self.thread = QThread()
        self.worker = IndexFetcherWorker(PLUGIN_INDEX_URL)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._on_index_fetched)
        self.worker.error.connect(self._on_index_fetch_error)
        
        # 任务完成后，无论是成功还是失败，都退出并清理线程
        self.worker.finished.connect(self.thread.quit)
        self.worker.error.connect(self.thread.quit)
        self.thread.finished.connect(self.on_thread_finished)
        
        self.thread.start()

    def _on_index_fetched(self, json_data):
        """处理插件索引成功获取后的逻辑。"""
        if self.progress_dialog: self.progress_dialog.close()
        try:
            self.online_plugins = json.loads(json_data)
            self.filter_plugin_list()
        except Exception as e:
            self.plugin_list.clear()
            error_item = QListWidgetItem("解析插件列表失败。"); error_item.setTextAlignment(Qt.AlignCenter); error_item.setForeground(Qt.red)
            self.plugin_list.addItem(error_item)
            QMessageBox.critical(self, "数据解析错误", f"无法解析插件列表数据:\n{e}")

    def _on_index_fetch_error(self, error_message):
        """处理插件索引获取失败后的逻辑。"""
        if self.progress_dialog: self.progress_dialog.close()
        self.plugin_list.clear()
        error_item = QListWidgetItem("获取列表失败，请检查网络连接。"); error_item.setTextAlignment(Qt.AlignCenter); error_item.setForeground(Qt.red)
        self.plugin_list.addItem(error_item)
        QMessageBox.critical(self, "网络错误", f"无法从源获取插件列表:\n{error_message}")

    def filter_plugin_list(self):
        self.plugin_list.clear()
        search_term = self.search_input.text().lower()
        for plugin_info in self.online_plugins:
            search_haystack = f"{plugin_info['name']} {plugin_info['author']} {' '.join(plugin_info.get('tags', []))}".lower()
            if search_term and search_term not in search_haystack: continue
            item = QListWidgetItem(plugin_info['name'])
            item.setData(Qt.UserRole, plugin_info)
            if plugin_info['id'] in self.installed_plugin_ids:
                item.setIcon(self.icon_manager.get_icon("success") if self.icon_manager else QIcon())
            self.plugin_list.addItem(item)
    
    def update_details_view(self, current, previous):
        self.version_combo.blockSignals(True)
        if not current:
            self.detail_name.setText("请从左侧选择一个插件"); self.detail_author.setText(""); self.detail_version.setText(""); self.detail_desc.clear()
            self.install_btn.setVisible(False); self.reinstall_btn.setVisible(False)
            self.version_label.setVisible(False); self.version_combo.setVisible(False); self.version_combo.clear()
            self.version_combo.blockSignals(False)
            return
            
        plugin_info = current.data(Qt.UserRole)
        self.detail_name.setText(plugin_info['name']); self.detail_author.setText(f"作者: {plugin_info['author']}")
        self.detail_version.setText(f"最新版本: {plugin_info['version']}")
        self.detail_desc.setHtml(f"<p>{plugin_info['description']}</p><p><b>标签:</b> <i>{', '.join(plugin_info.get('tags', []))}</i></p>")
        
        self.version_combo.clear()
        release_url = plugin_info.get('release_url')
        if release_url:
            match = re.search(r'plugins-release-(\d+\.\d+)', release_url)
            if match:
                latest_major_minor = match.group(1)
                major, minor = map(int, latest_major_minor.split('.'))
                versions_to_add = []
                for v_minor in range(minor, -1, -1):
                    # 假设主版本号不变
                    versions_to_add.append(f"{major}.{v_minor}")
                self.version_combo.addItems(versions_to_add)
                self.version_label.setVisible(True); self.version_combo.setVisible(True)
            else:
                self.version_label.setVisible(False); self.version_combo.setVisible(False)
        else:
            self.version_label.setVisible(False); self.version_combo.setVisible(False)

        is_installed = plugin_info['id'] in self.installed_plugin_ids
        self.install_btn.setVisible(not is_installed)
        self.reinstall_btn.setVisible(is_installed)
        self.version_combo.blockSignals(False)

    def _start_download_task(self, url, plugin_name):
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "请稍候", "一个操作正在进行中，请等待其完成后再试。")
            return

        self.progress_dialog = QProgressDialog(f"正在准备下载 '{plugin_name}'...", "取消", 0, 0, self)
        self.progress_dialog.setWindowTitle("正在处理"); self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.show()

        # [核心修正] 使用正确的 DownloaderWorker
        self.thread = QThread()
        self.worker = DownloaderWorker(url, self.plugins_dir)
        self.worker.moveToThread(self.thread)

        self.progress_dialog.canceled.connect(self.worker.stop)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit); self.worker.error.connect(self.thread.quit)
        self.worker.finished.connect(self.on_download_finished); self.worker.error.connect(self.on_download_error)
        self.thread.finished.connect(self.on_thread_finished)
        self.thread.start()

    def _get_url_for_selected_version(self, plugin_info):
        latest_url = plugin_info.get('release_url')
        selected_version = self.version_combo.currentText()
        if not latest_url or not selected_version: return None
        match = re.search(r'(plugins-release-)(\d+\.\d+)', latest_url)
        if not match: return latest_url
        return latest_url.replace(match.group(2), selected_version)

    def on_install_clicked(self):
        current_item = self.plugin_list.currentItem();
        if not current_item: return
        plugin_info = current_item.data(Qt.UserRole)
        url = self._get_url_for_selected_version(plugin_info)
        if not url: QMessageBox.warning(self, "无可用链接", "此插件未提供发布版下载链接或无法解析版本。"); return
        self._start_download_task(url, plugin_info['name'])
        
    def on_reinstall_clicked(self):
        current_item = self.plugin_list.currentItem();
        if not current_item: return
        plugin_info = current_item.data(Qt.UserRole)
        url = self._get_url_for_selected_version(plugin_info)
        if not url: QMessageBox.warning(self, "无可用链接", "此插件未提供发布版下载链接或无法解析版本。"); return
        reply = QMessageBox.question(self, "确认操作", f"您确定要安装/更新插件 '{plugin_info['name']}' (版本: {self.version_combo.currentText()}) 吗？\n这将覆盖本地的同名插件。", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply == QMessageBox.No: return
        self._start_download_task(url, plugin_info['name'])

    def on_download_finished(self, title, message):
        if self.progress_dialog: self.progress_dialog.close()
        QMessageBox.information(self, title, message)
        self.load_installed_plugins()
        self.filter_plugin_list()
        self.main_window.plugin_manager.scan_plugins()

    def on_download_error(self, error_message):
        if self.progress_dialog: self.progress_dialog.close()
        QMessageBox.critical(self, "操作失败", error_message)

    def on_thread_finished(self):
        if self.progress_dialog: self.progress_dialog.close(); self.progress_dialog = None
        if self.worker: self.worker.deleteLater(); self.worker = None
        if self.thread: self.thread.deleteLater(); self.thread = None
    
    def closeEvent(self, event):
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "后台任务", "正在等待后台任务完成，请稍候...")
            event.ignore()
            return
        super().closeEvent(event)

# ==============================================================================
# 3. 插件主入口 (保持不变)
# ==============================================================================
class PluginNexusPlugin(BasePlugin):
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager); self.dialog_instance = None
    def setup(self):
        if not REQUESTS_AVAILABLE:
            print("[Plugin Nexus] 'requests' 库未安装，插件市场功能将不可用。", file=sys.stderr)
            return False 
        return True
    def teardown(self):
        if self.dialog_instance: self.dialog_instance.close()
    def execute(self, **kwargs):
        parent_widget = kwargs.get('parent_dialog', self.main_window)
        if self.dialog_instance is None:
            self.dialog_instance = NexusDialog(parent_widget, self.main_window.icon_manager, self.plugin_manager.plugins_dir)
            self.dialog_instance.finished.connect(lambda: setattr(self, 'dialog_instance', None))
        if self.dialog_instance.parent() != parent_widget: self.dialog_instance.setParent(parent_widget)
        self.dialog_instance.show()
        self.dialog_instance.raise_()
        self.dialog_instance.activateWindow()