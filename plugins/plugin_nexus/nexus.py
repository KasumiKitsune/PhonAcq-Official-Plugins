# --- START OF FILE plugins/plugin_nexus/nexus.py (v3.3 - 批量安装版 - 完整代码) ---

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

# 尝试导入 requests 库
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# 尝试导入 plugin_system 模块
try:
    from plugin_system import BasePlugin
except ImportError:
    # 如果直接导入失败，尝试添加到 sys.path
    # 这在插件作为独立文件夹运行时可能需要
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

# 插件索引文件的URL
PLUGIN_INDEX_URL = "https://gist.githubusercontent.com/KasumiKitsune/2859bb62eb51dc8056ac72d3722b9f01/raw/index.json"

# ==============================================================================
# 1. 专用后台工作器
# ==============================================================================

# 用于获取插件索引JSON文本的工作器
class IndexFetcherWorker(QObject):
    finished = pyqtSignal(str) # 完成时发射JSON文本
    error = pyqtSignal(str)    # 发生错误时发射错误信息

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        """执行网络请求获取插件索引。"""
        try:
            response = requests.get(self.url, timeout=10) # 10秒超时
            response.raise_for_status() # 如果状态码不是2xx，则抛出HTTPError
            self.finished.emit(response.text)
        except requests.exceptions.RequestException as e:
            self.error.emit(f"网络请求失败: {e}")
        except Exception as e:
            self.error.emit(str(e))

# 新的批量下载工作器，用于管理多个插件的下载和安装
class BatchDownloaderWorker(QObject):
    # 信号定义
    overall_progress = pyqtSignal(int, int, str) # (当前任务索引, 总任务数, 当前插件名)
    task_finished = pyqtSignal(str, bool, str)   # (插件名, 是否成功, 详情/错误信息)
    all_finished = pyqtSignal(dict)              # (包含成功和失败列表的总结报告)
    
    def __init__(self, task_queue, target_dir):
        super().__init__()
        self.task_queue = task_queue # 任务队列，每个任务是 {'name': '...', 'url': '...'}
        self.target_dir = target_dir # 插件安装目标目录
        self._is_running = True     # 控制任务是否继续运行的标志

    def stop(self):
        """外部调用以停止当前正在进行的任务。"""
        self._is_running = False

    def run(self):
        """执行任务队列中的所有下载和安装任务。"""
        total_tasks = len(self.task_queue)
        summary = {"success": [], "failed": []} # 存储最终的成功和失败报告
        
        for i, task in enumerate(self.task_queue):
            # 检查是否被用户取消
            if not self._is_running:
                summary["failed"].append(f"{task['name']} - 操作被用户取消")
                continue # 继续循环，将剩余任务标记为失败

            # 发射总体进度信号
            self.overall_progress.emit(i + 1, total_tasks, task['name'])
            
            # 处理单个插件的下载和安装
            error_msg = self._process_single_task(task['url'], task['name'])
            
            # 根据结果更新总结报告并发射单个任务完成信号
            if error_msg is None:
                summary["success"].append(task['name'])
                self.task_finished.emit(task['name'], True, "成功安装/更新。")
            else:
                summary["failed"].append(f"{task['name']} - {error_msg}")
                self.task_finished.emit(task['name'], False, error_msg)

        # 所有任务处理完毕，发射最终总结报告
        self.all_finished.emit(summary)

    def _process_single_task(self, url, plugin_name):
        """
        处理单个插件的下载和解压任务。
        成功返回 None，失败返回错误信息字符串。
        """
        temp_zip_path = None
        try:
            # 发送网络请求，流式下载
            response = requests.get(url, stream=True, timeout=30)
            if response.status_code == 404:
                return f"服务器上未找到该版本的文件 (404 Not Found)。"
            response.raise_for_status() # 检查HTTP状态码
            
            # 创建临时文件保存下载的ZIP
            fd, temp_zip_path = tempfile.mkstemp(suffix=".zip")
            os.close(fd)
            with open(temp_zip_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if not self._is_running: # 检查是否被取消
                        raise InterruptedError("用户取消操作")
                    f.write(chunk)
            
            # 解压ZIP文件
            with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
                # 检查ZIP包格式，确保根目录只有一个插件文件夹
                top_level_dirs = {os.path.normpath(f).split(os.sep)[0] for f in zip_ref.namelist() if f}
                if len(top_level_dirs) != 1:
                    raise ValueError("ZIP包格式不正确，根目录应只包含一个插件文件夹。")
                
                plugin_folder_name = list(top_level_dirs)[0]
                final_install_path = os.path.join(self.target_dir, plugin_folder_name)
                
                # 如果目标目录已存在，先删除（用于更新/重新安装）
                if os.path.exists(final_install_path):
                    shutil.rmtree(final_install_path)
                
                zip_ref.extractall(self.target_dir) # 解压所有文件到目标目录

            return None # 任务成功完成
        except Exception as e:
            # 捕获所有异常并返回错误信息
            return f"{type(e).__name__}: {e}"
        finally:
            # 无论成功失败，都尝试清理临时文件
            if temp_zip_path and os.path.exists(temp_zip_path):
                os.remove(temp_zip_path)

# ==============================================================================
# 2. UI 对话框
# ==============================================================================
class NexusDialog(QDialog):
    def __init__(self, parent_widget, true_main_window_ref, icon_manager, plugins_dir):
        # 使用 parent_widget 作为标准的Qt父级，确保对话框层级正确
        super().__init__(parent_widget)
        
        # 保存对直接父窗口 (可能是 MainWindow 或 PluginManagementDialog) 的引用
        self.main_window = parent_widget
        # [核心修复] 保存一个永远指向真正 MainWindow 实例的引用
        self.true_main_window = true_main_window_ref
        
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
        
        # 启动时立即获取插件索引（使用 QTimer.singleShot 延迟，确保UI已就绪）
        QTimer.singleShot(0, self.fetch_plugin_index)

    def _init_ui(self):
        """初始化对话框的用户界面。"""
        main_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal) # 主界面的左右分割器

        # 左侧面板：搜索框和插件列表
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("按名称、作者或标签搜索...")
        self.plugin_list = QListWidget()
        self.plugin_list.setSpacing(2)
        self.plugin_list.setSelectionMode(QListWidget.ExtendedSelection) # 启用多选功能

        left_layout.addWidget(self.search_input)
        left_layout.addWidget(self.plugin_list)

        # 右侧面板：插件详情和操作按钮
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        self.detail_name = QLabel("请从左侧选择一个或多个插件")
        self.detail_name.setObjectName("PluginNexusTitle") # 用于QSS样式
        self.detail_author = QLabel()
        self.detail_author.setObjectName("PluginNexusAuthor") # 用于QSS样式
        self.detail_version = QLabel()
        self.detail_desc = QTextBrowser()
        self.detail_desc.setOpenExternalLinks(True) # 允许打开外部链接

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
        version_layout = QHBoxLayout()
        version_layout.addWidget(self.version_label)
        version_layout.addWidget(self.version_combo)

        self.install_btn = QPushButton("安装插件")
        self.install_btn.setObjectName("AccentButton") # 用于QSS样式
        self.reinstall_btn = QPushButton("重新安装/更新")
        self.reinstall_btn.setObjectName("UpdateButton") # 用于QSS样式

        # 布局右侧面板的各个控件
        h_layout = QHBoxLayout()
        h_layout.addWidget(self.detail_author)
        h_layout.addStretch() # 填充空白
        h_layout.addWidget(self.detail_version)

        btn_layout = QHBoxLayout()
        btn_layout.addLayout(version_layout)
        btn_layout.addStretch()
        btn_layout.addWidget(self.reinstall_btn)
        btn_layout.addWidget(self.install_btn)

        right_layout.addWidget(self.detail_name)
        right_layout.addLayout(h_layout)
        right_layout.addWidget(self.detail_desc, 1) # 详情文本浏览器占据更多空间
        right_layout.addLayout(btn_layout)

        # 将左右面板添加到主分割器
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 600]) # 设置初始宽度比例

        main_layout.addWidget(splitter)

    def _connect_signals(self):
        """连接UI控件的信号到槽函数。"""
        self.search_input.textChanged.connect(self.filter_plugin_list)
        self.plugin_list.itemSelectionChanged.connect(self.update_details_view) # 使用 itemSelectionChanged 信号以支持多选
        self.install_btn.clicked.connect(self.on_install_clicked)
        self.reinstall_btn.clicked.connect(self.on_reinstall_clicked)

    def load_installed_plugins(self):
        """扫描本地插件目录，加载已安装插件的ID。"""
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
                        if 'id' in meta:
                            self.installed_plugin_ids.add(meta['id'])
                except Exception:
                    continue

    def fetch_plugin_index(self):
        """从预设URL获取在线插件索引。"""
        if not REQUESTS_AVAILABLE:
            QMessageBox.critical(self, "依赖缺失", "无法获取在线列表，'requests' 库未安装。\n请运行: pip install requests")
            self.plugin_list.clear()
            self.plugin_list.addItem("错误: 缺少 'requests' 库")
            return
        
        # 显示进度对话框
        self.progress_dialog = QProgressDialog("正在获取插件列表...", "取消", 0, 0, self)
        self.progress_dialog.setWindowTitle("插件市场")
        self.progress_dialog.setWindowModality(Qt.WindowModal) # 模态对话框，阻塞父窗口
        self.progress_dialog.setCancelButton(None) # 隐藏取消按钮，因为这个阶段不允许取消
        self.progress_dialog.setRange(0, 0) # 设置为不确定进度模式
        self.progress_dialog.show()
        QApplication.processEvents() # 强制UI更新

        self.load_installed_plugins() # 在获取在线索引前，先加载本地已安装插件

        # 创建并启动 IndexFetcherWorker
        self.thread = QThread()
        self.worker = IndexFetcherWorker(PLUGIN_INDEX_URL)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._on_index_fetched)
        self.worker.error.connect(self._on_index_fetch_error)
        
        # 任务完成后，无论是成功还是失败，都退出并清理线程
        self.worker.finished.connect(self.thread.quit)
        self.worker.error.connect(self.thread.quit)
        self.thread.finished.connect(self.on_thread_finished) # 线程结束时清理资源
        
        self.thread.start()

    def _on_index_fetched(self, json_data):
        """处理插件索引成功获取后的逻辑。"""
        if self.progress_dialog:
            self.progress_dialog.close() # 关闭进度对话框
        try:
            self.online_plugins = json.loads(json_data) # 解析JSON数据
            self.filter_plugin_list() # 填充插件列表
        except Exception as e:
            self.plugin_list.clear()
            error_item = QListWidgetItem("解析插件列表失败。"); error_item.setTextAlignment(Qt.AlignCenter); error_item.setForeground(Qt.red)
            self.plugin_list.addItem(error_item)
            QMessageBox.critical(self, "数据解析错误", f"无法解析插件列表数据:\n{e}")

    def _on_index_fetch_error(self, error_message):
        """处理插件索引获取失败后的逻辑。"""
        if self.progress_dialog:
            self.progress_dialog.close()
        self.plugin_list.clear()
        error_item = QListWidgetItem("获取列表失败，请检查网络连接。"); error_item.setTextAlignment(Qt.AlignCenter); error_item.setForeground(Qt.red)
        self.plugin_list.addItem(error_item)
        QMessageBox.critical(self, "网络错误", f"无法从源获取插件列表:\n{error_message}")

    def filter_plugin_list(self):
        """根据搜索框的文本过滤并显示插件列表。"""
        self.plugin_list.clear()
        search_term = self.search_input.text().lower()
        for plugin_info in self.online_plugins:
            search_haystack = f"{plugin_info['name']} {plugin_info['author']} {' '.join(plugin_info.get('tags', []))}".lower()
            if search_term and search_term not in search_haystack:
                continue # 如果搜索词不匹配，则跳过
            
            item = QListWidgetItem(plugin_info['name'])
            item.setData(Qt.UserRole, plugin_info) # 将完整的插件信息存储在item的UserRole中
            
            # 根据是否已安装来设置图标
            if plugin_info['id'] in self.installed_plugin_ids:
                # 检查 icon_manager 是否可用，避免在依赖缺失时崩溃
                item.setIcon(self.icon_manager.get_icon("success") if self.icon_manager else QIcon())
            
            self.plugin_list.addItem(item)
    
    def update_details_view(self):
        """
        根据当前选择的插件项（单选或多选）更新右侧详情面板和按钮状态。
        此方法由 itemSelectionChanged 信号触发。
        """
        selected_items = self.plugin_list.selectedItems() # 获取所有选中的项
        self.version_combo.blockSignals(True) # 阻塞信号，防止在更新下拉框时触发不必要的事件

        if not selected_items:
            # 没有选中任何项时的默认显示
            self.detail_name.setText("请从左侧选择一个或多个插件")
            self.detail_author.setText(""); self.detail_version.setText(""); self.detail_desc.clear()
            self.install_btn.setVisible(False); self.reinstall_btn.setVisible(False)
            self.version_label.setVisible(False); self.version_combo.setVisible(False); self.version_combo.clear()
        
        elif len(selected_items) == 1:
            # 单选模式：显示具体插件的详细信息
            plugin_info = selected_items[0].data(Qt.UserRole)
            self.detail_name.setText(plugin_info['name'])
            self.detail_author.setText(f"作者: {plugin_info['author']}")
            self.detail_version.setText(f"最新版本: {plugin_info['version']}")
            self.detail_desc.setHtml(f"<p>{plugin_info['description']}</p><p><b>标签:</b> <i>{', '.join(plugin_info.get('tags', []))}</i></p>")
            
            # 版本选择下拉框逻辑
            self.version_combo.clear()
            release_url = plugin_info.get('release_url')
            if release_url:
                match = re.search(r'plugins-release-(\d+)\.(\d+)', release_url) # [修改] 使用两个捕获组
                if match:
                    major = int(match.group(1)) # 例如 4
                    minor = int(match.group(2)) # 例如 6
                    
                    # [核心修改] 调整版本追溯的起始和结束点
                    # 确保我们只生成到 1.2 版本
                    start_minor = minor
                    end_minor = 1 # range(x, 1, -1) 将会生成 x, x-1, ..., 2
                    
                    # 如果当前次版本号小于2，则只显示当前版本
                    if minor < 2:
                        end_minor = minor - 1

                    versions_to_add = [f"{major}.{v_minor}" for v_minor in range(start_minor, end_minor, -1)]
                    
                    self.version_combo.addItems(versions_to_add)
                    self.version_label.setVisible(True)
                    self.version_combo.setVisible(True)
            else:
                # 如果没有提供 release_url
                self.version_label.setVisible(False)
                self.version_combo.setVisible(False)
        else:
            # 多选模式：显示批量操作提示
            count = len(selected_items)
            self.detail_name.setText(f"已选中 {count} 个插件")
            self.detail_author.setText("")
            self.detail_version.setText("")
            self.detail_desc.setHtml("<p>批量操作将为每个插件选择最新的兼容版本进行安装或更新。</p>")
            self.version_label.setVisible(False)
            self.version_combo.setVisible(False) # 批量安装时隐藏版本选择

        # 更新按钮的可见性和文本（无论是单选还是多选）
        if selected_items:
            num_selected = len(selected_items)
            # 检查所有选中的插件是否都已经安装
            all_installed = all(item.data(Qt.UserRole)['id'] in self.installed_plugin_ids for item in selected_items)
            # 检查所有选中的插件是否都未安装
            none_installed = all(item.data(Qt.UserRole)['id'] not in self.installed_plugin_ids for item in selected_items)

            self.install_btn.setVisible(not all_installed)   # 如果所有都已安装，则隐藏安装按钮
            self.reinstall_btn.setVisible(not none_installed) # 如果所有都未安装，则隐藏重新安装按钮
            
            if num_selected > 1:
                # 批量操作时改变按钮文本
                self.install_btn.setText(f"批量安装 ({num_selected}个)")
                self.reinstall_btn.setText(f"批量更新 ({num_selected}个)")
            else:
                # 单选操作时恢复默认文本
                self.install_btn.setText("安装插件")
                self.reinstall_btn.setText("重新安装/更新")
        else:
            # 没有选中任何项时，隐藏所有操作按钮
            self.install_btn.setVisible(False)
            self.reinstall_btn.setVisible(False)
        
        self.version_combo.blockSignals(False) # 恢复信号

    def _start_batch_download(self):
        """
        准备并启动批量下载安装任务。
        此方法将被 on_install_clicked 和 on_reinstall_clicked 调用。
        """
        # 避免重复启动任务
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "请稍候", "一个操作正在进行中，请等待其完成后再试。")
            return

        selected_items = self.plugin_list.selectedItems()
        if not selected_items:
            return # 没有选中任何插件，不执行操作
        
        # 构建任务队列
        task_queue = []
        for item in selected_items:
            plugin_info = item.data(Qt.UserRole)
            # 获取下载URL
            url = self._get_url_for_selected_version(plugin_info)
            if url:
                task_queue.append({'name': plugin_info['name'], 'url': url})
            else:
                QMessageBox.warning(self, "链接缺失", f"插件 '{plugin_info['name']}' 缺少有效的下载链接，将从队列中跳过。")
        
        if not task_queue:
            return # 如果队列为空，则不启动任务

        # 显示进度对话框，并设置为最大值
        self.progress_dialog = QProgressDialog("正在准备批量安装...", "取消", 0, len(task_queue), self)
        self.progress_dialog.setWindowTitle("正在处理批量安装");
        self.progress_dialog.setWindowModality(Qt.WindowModal) # 模态对话框
        self.progress_dialog.setCancelButtonText("取消") # 显示取消按钮
        self.progress_dialog.show()

        # 创建并启动 BatchDownloaderWorker
        self.thread = QThread()
        self.worker = BatchDownloaderWorker(task_queue, self.plugins_dir)
        self.worker.moveToThread(self.thread)

        self.progress_dialog.canceled.connect(self.worker.stop) # 进度对话框取消时，停止工作器
        self.thread.started.connect(self.worker.run)
        
        # 连接工作器的信号到对话框的槽
        self.worker.overall_progress.connect(self.on_overall_progress)
        self.worker.all_finished.connect(self.on_all_finished)
        
        # 任务完成后，无论是成功还是失败，都退出并清理线程
        self.worker.all_finished.connect(self.thread.quit)
        # self.worker.task_finished.connect(...) # 如果需要显示每个子任务的独立状态，可以连接此信号
        self.thread.finished.connect(self.on_thread_finished) # 线程结束时清理资源

        self.thread.start()

    def _get_url_for_selected_version(self, plugin_info):
        """
        根据插件信息和当前选择（单选/多选）获取下载URL。
        批量模式下，总是返回最新版本URL。
        """
        # 如果是多选模式，或者版本下拉框不可见（意味着批量模式），总是选择最新的发布URL
        if len(self.plugin_list.selectedItems()) > 1 or not self.version_combo.isVisible():
            return plugin_info.get('release_url')
        
        # 单选模式下，使用版本下拉框选择的版本
        latest_url = plugin_info.get('release_url')
        selected_version = self.version_combo.currentText()
        if not latest_url or not selected_version:
            return None
        
        # 替换URL中的版本号
        match = re.search(r'(plugins-release-)(\d+\.\d+)', latest_url)
        if not match:
            return latest_url # 如果URL格式不匹配，直接返回原始URL
        return latest_url.replace(match.group(2), selected_version)

    def on_install_clicked(self):
        """点击“安装”按钮时的槽函数。"""
        self._start_batch_download() # 调用通用批量下载方法
        
    def on_reinstall_clicked(self):
        """点击“重新安装/更新”按钮时的槽函数。"""
        count = len(self.plugin_list.selectedItems())
        action_text = "更新" if count > 1 else "安装/更新"
        reply = QMessageBox.question(self, "确认操作",
                                     f"您确定要批量{action_text}选中的 {count} 个插件吗？\n这将覆盖本地的同名插件。",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply == QMessageBox.No:
            return
        self._start_batch_download()

    def on_overall_progress(self, current_task_index, total_tasks, current_plugin_name):
        """
        处理批量下载的总体进度更新。
        由 BatchDownloaderWorker 的 overall_progress 信号触发。
        """
        if self.progress_dialog:
            self.progress_dialog.setLabelText(f"正在安装 {current_task_index}/{total_tasks}: {current_plugin_name}...")
            self.progress_dialog.setValue(current_task_index - 1) # ProgressDialog 的值通常是0-indexed

    def on_all_finished(self, summary):
        """
        处理所有批量任务完成后的总结。
        由 BatchDownloaderWorker 的 all_finished 信号触发。
        """
        if self.progress_dialog:
            self.progress_dialog.setValue(self.progress_dialog.maximum())
            self.progress_dialog.close()
        
        # 构建总结报告文本
        success_list = "\n- ".join(summary['success'])
        failed_list = "\n- ".join(summary['failed'])
        
        report = "<b>批量安装完成！</b><hr>"
        if summary['success']:
            report += f"<p><b>成功 ({len(summary['success'])}):</b><br>- {success_list}</p>"
        if summary['failed']:
            report += f"<p><b>失败 ({len(summary['failed'])}):</b><br>- {failed_list}</p>"
            
        # 使用 QMessageBox 显示总结报告
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("批量安装报告")
        msg_box.setText(report)
        msg_box.setIcon(QMessageBox.Information)
        msg_box.setStandardButtons(QMessageBox.Ok)
        msg_box.exec_()
        
        # --- [核心修复] 使用 self.true_main_window 更新主程序UI ---
        
        # 1. 通知主窗口的插件管理器重新扫描插件目录
        self.true_main_window.plugin_manager.scan_plugins()
        
        # 2. 更新主窗口右上角的固定插件栏
        self.true_main_window.update_pinned_plugins_ui()

        # 3. [推荐] 如果此对话框的直接父级是插件管理对话框，也通知它刷新列表
        #    self.main_window 在这里指向的是直接父级
        if hasattr(self.main_window, 'populate_plugin_list'):
            self.main_window.populate_plugin_list()
        
        # 4. 最后，刷新本对话框自身的列表状态
        self.load_installed_plugins()
        self.filter_plugin_list()

    def on_download_finished(self, title, message):
        """
        处理单个插件下载完成后的逻辑（仅在单文件下载模式下使用）。
        此方法不再用于批量模式，但作为通用回调保留。
        """
        if self.progress_dialog:
            self.progress_dialog.close()
        QMessageBox.information(self, title, message)
        # 单个下载完成后也需要刷新
        self.load_installed_plugins()
        self.filter_plugin_list()
        self.main_window.plugin_manager.scan_plugins()
        self.main_window.update_pinned_plugins_ui()


    def on_download_error(self, error_message):
        """
        处理单个插件下载失败后的逻辑（仅在单文件下载模式下使用）。
        """
        if self.progress_dialog:
            self.progress_dialog.close()
        QMessageBox.critical(self, "操作失败", error_message)

    def on_thread_finished(self):
        """线程结束时进行资源清理。"""
        if self.progress_dialog:
            # 确保 progress_dialog 在线程结束后被安全关闭，即使是用户取消
            self.progress_dialog.close()
            self.progress_dialog = None # 清理引用
        
        # 延迟 deleteLater，确保所有信号都已处理完毕
        if self.worker:
            self.worker.deleteLater()
            self.worker = None
        if self.thread:
            self.thread.deleteLater()
            self.thread = None
    
    def closeEvent(self, event):
        """重写 closeEvent，确保后台任务完成才能关闭。"""
        if self.thread and self.thread.isRunning():
            reply = QMessageBox.question(self, "后台任务", "批量安装任务正在进行中，强制关闭可能会导致数据不完整。\n您确定要取消并关闭吗？",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                if self.worker:
                    self.worker.stop() # 通知工作器停止
                    self.thread.quit() # 请求线程退出
                    self.thread.wait(2000) # 等待最多2秒
                event.accept()
            else:
                event.ignore()
        else:
            super().closeEvent(event)

# ==============================================================================
# 3. 插件主入口
# ==============================================================================
class PluginNexusPlugin(BasePlugin):
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.dialog_instance = None # 保持对话框单例

    def setup(self):
        """插件启用时调用，检查 requests 库。"""
        if not REQUESTS_AVAILABLE:
            print("[Plugin Nexus] 'requests' 库未安装，插件市场功能将不可用。", file=sys.stderr)
            return False 
        return True

    def teardown(self):
        """插件禁用或程序退出时调用，关闭对话框。"""
        if self.dialog_instance:
            self.dialog_instance.close()

    def execute(self, **kwargs):
        """
        执行插件，显示插件市场对话框。
        此方法现在能正确处理上下文，并将 MainWindow 的引用传递给对话框。
        """
        # 确定对话框的直接父级。如果从插件管理对话框打开，则是它；否则是主窗口。
        parent_widget = kwargs.get('parent_dialog', self.main_window)
        
        # [核心修复] self.main_window 在插件主类中，永远指向 MainWindow 实例。
        # 我们将这个稳定的引用传递给对话框。
        true_main_window = self.main_window 
        
        # 实现单例模式：如果对话框已存在，则重用；否则创建新实例
        if self.dialog_instance is None:
            # [修改] 将 true_main_window 作为新参数传递给构造函数
            self.dialog_instance = NexusDialog(
                parent_widget, 
                true_main_window, 
                true_main_window.icon_manager, 
                self.plugin_manager.plugins_dir
            )
            # 连接 finished 信号，当对话框关闭时，清除实例引用
            self.dialog_instance.finished.connect(lambda: setattr(self, 'dialog_instance', None))
        
        # 如果父窗口改变（例如，上次从主菜单打开，这次从插件管理打开），
        # 重新设置父窗口以确保正确的UI层级关系。
        if self.dialog_instance.parent() != parent_widget:
            self.dialog_instance.setParent(parent_widget)
            # 由于父级变化，重新加载本地插件状态，以防插件管理器的状态已更新
            self.dialog_instance.load_installed_plugins()
            self.dialog_instance.filter_plugin_list()

        # 显示对话框并将其置于顶层
        self.dialog_instance.show()
        self.dialog_instance.raise_()
        self.dialog_instance.activateWindow()