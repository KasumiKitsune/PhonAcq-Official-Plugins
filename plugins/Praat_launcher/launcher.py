# --- START OF FILE plugins/external_tool_launcher/launcher.py ---

import os
import sys
import json
import subprocess
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QLineEdit, QFileDialog, QMessageBox, QAction)
from PyQt5.QtCore import Qt

try:
    from plugin_system import BasePlugin
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

# ==============================================================================
# 1. 插件专属配置管理器
# ==============================================================================
class LauncherConfigManager:
    """负责读写本插件的配置文件。"""
    def __init__(self):
        plugin_dir = os.path.dirname(__file__)
        self.config_path = os.path.join(plugin_dir, 'config.json')
        self.settings = self._load()

    def _load(self):
        default_settings = {"praat_path": ""}
        if not os.path.exists(self.config_path):
            return default_settings
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return default_settings

    def save(self):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=4)
        except IOError as e:
            print(f"无法保存启动器插件配置文件: {e}")

    def get_praat_path(self):
        return self.settings.get("praat_path", "")

    def set_praat_path(self, path):
        self.settings["praat_path"] = path

# ==============================================================================
# 2. 插件设置对话框
# ==============================================================================
class LauncherSettingsDialog(QDialog):
    """用于设置 Praat.exe 路径的UI窗口。"""
    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        
        self.setWindowTitle("外部工具路径设置")
        self.setMinimumWidth(500)
        
        layout = QVBoxLayout(self)
        
        layout.addWidget(QLabel("请设置 Praat.exe 的完整路径:"))
        
        path_layout = QHBoxLayout()
        self.path_edit = QLineEdit(self.config_manager.get_praat_path())
        self.browse_btn = QPushButton("浏览...")
        path_layout.addWidget(self.path_edit)
        path_layout.addWidget(self.browse_btn)
        
        button_layout = QHBoxLayout()
        self.save_btn = QPushButton("保存")
        self.close_btn = QPushButton("关闭")
        button_layout.addStretch()
        button_layout.addWidget(self.save_btn)
        button_layout.addWidget(self.close_btn)
        
        layout.addLayout(path_layout)
        layout.addLayout(button_layout)
        
        self.browse_btn.clicked.connect(self.on_browse)
        self.save_btn.clicked.connect(self.on_save)
        self.close_btn.clicked.connect(self.reject)

    def on_browse(self):
        # 根据操作系统设置不同的文件过滤器
        if sys.platform == "win32":
            filter_str = "Praat Executable (Praat.exe)"
        elif sys.platform == "darwin":
            filter_str = "Praat Application (*.app)"
        else:
            filter_str = "All files (*)"
            
        filepath, _ = QFileDialog.getOpenFileName(self, "选择 Praat 程序", "", filter_str)
        if filepath:
            self.path_edit.setText(filepath)

    def on_save(self):
        path = self.path_edit.text()
        if not os.path.exists(path):
            QMessageBox.warning(self, "路径无效", "指定的文件路径不存在，请重新选择。")
            return
        
        self.config_manager.set_praat_path(path)
        self.config_manager.save()
        QMessageBox.information(self, "成功", "Praat 路径已成功保存。")
        self.accept()

# ==============================================================================
# 3. 插件主类
# ==============================================================================
class ExternalToolLauncherPlugin(BasePlugin):
    """外部工具启动器插件主类。"""
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.config_manager = LauncherConfigManager()
        self.settings_dialog = None
        # [修改] QAction 现在在 setup 时根据上下文创建，不再是类属性
        # self.open_with_praat_action = None

    def setup(self):
        """
        [修改] setup 方法现在只负责“打标记”，告诉音频管理器本插件已激活。
        它不再直接修改 UI，这是一种更稳健的“钩子”模式。
        """
        self.audio_manager_page = getattr(self.main_window, 'audio_manager_page', None)
        if not self.audio_manager_page:
            print("[Launcher Plugin] 错误: 未找到音频管理器模块。")
            return False
        
        # 在 audio_manager 实例上设置一个标志，表示本插件已激活
        # 这是为了让 audio_manager 在构建菜单时知道可以添加我们的功能
        setattr(self.audio_manager_page, 'external_launcher_plugin_active', self)
        
        print("[Launcher Plugin] 已向音频管理器注册。")
        return True

    def teardown(self):
        """当插件禁用时，移除标记。"""
        if hasattr(self, 'audio_manager_page') and hasattr(self.audio_manager_page, 'external_launcher_plugin_active'):
            delattr(self.audio_manager_page, 'external_launcher_plugin_active')
            print("[Launcher Plugin] 已从音频管理器注销。")
        if self.settings_dialog:
            self.settings_dialog.close()

    def execute(self, **kwargs):
        """
        当从插件菜单执行时，打开设置窗口。
        当从其他模块调用时（例如右键菜单），则执行启动逻辑。
        """
        # [修改] 检查 kwargs 中是否有 filepaths，以区分不同的调用来源
        filepaths = kwargs.get('filepaths')
        
        if filepaths:
            # 如果提供了文件路径，说明是从右键菜单调用的
            self.launch_with_praat(filepaths)
        else:
            # 如果没有，说明是从主插件菜单调用的，打开设置窗口
            if self.settings_dialog is None:
                self.settings_dialog = LauncherSettingsDialog(self.config_manager, self.main_window)
                self.settings_dialog.finished.connect(lambda: setattr(self, 'settings_dialog', None))
            
            self.settings_dialog.show()
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()

    def launch_with_praat(self, filepaths):
        """
        [v1.2 优化] 启动一个 Praat 实例并打开所有指定的文件。
        :param filepaths: 一个包含一个或多个文件路径的列表。
        """
        praat_path = self.config_manager.get_praat_path()
        if not (praat_path and os.path.exists(praat_path)):
            reply = QMessageBox.question(self, "未配置 Praat", 
                                         "尚未配置 Praat.exe 的路径。是否现在就去设置？",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.Yes:
                self.execute()
            return

        # --- [核心修改] 构建包含所有文件的单个命令 ---
        
        try:
            command = []
            
            if sys.platform == "win32":
                # Windows: [praat.exe, --open, file1, file2, ...]
                command = [praat_path, "--open"]
                command.extend(filepaths) # 将所有文件路径追加到命令列表
                
            elif sys.platform == "darwin":
                # macOS: open -a Praat.app --args --open file1 file2 ...
                command = ["open", "-a", praat_path, "--args", "--open"]
                command.extend(filepaths)
                
            else: # Linux
                # Linux: [praat, --open, file1, file2, ...]
                command = [praat_path, "--open"]
                command.extend(filepaths)
            
            # [核心修改] 只调用一次 Popen
            subprocess.Popen(command)
            
            filenames = ", ".join(os.path.basename(p) for p in filepaths[:3])
            if len(filepaths) > 3:
                filenames += ", ..."
            print(f"正在用 Praat 打开 {len(filepaths)} 个文件: {filenames}")

        except Exception as e:
            # 错误处理保持不变，但现在它会在启动前就可能因为参数列表过长等问题报错
            import traceback
            QMessageBox.critical(self, "启动失败", f"无法启动 Praat: \n{e}\n\n{traceback.format_exc()}")