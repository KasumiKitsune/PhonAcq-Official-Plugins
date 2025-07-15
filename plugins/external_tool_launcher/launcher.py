# --- START OF FILE plugins/external_tool_launcher/launcher.py (v2.0) ---

import os
import sys
import json
import subprocess
import uuid
from functools import partial

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QLineEdit, QFileDialog, QMessageBox, QAction, QListWidget,
                             QListWidgetItem, QSplitter, QFormLayout, QGroupBox,
                             QCheckBox, QTextEdit, QApplication, QMenu)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon

try:
    # 尝试从主程序环境中导入
    from plugin_system import BasePlugin
except ImportError:
    # 如果作为独立脚本或在不同环境中运行，则回退到相对路径导入
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

# ==============================================================================
# 1. 插件专属配置管理器 (v2.0)
# ==============================================================================
class LauncherConfigManager:
    """负责读写本插件的通用工具配置文件。"""
    def __init__(self):
        plugin_dir = os.path.dirname(__file__)
        self.config_path = os.path.join(plugin_dir, 'config.json')
        self.settings = self._load()

    def _load(self):
        # v2.0 的默认配置，包含一个 Praat 示例
        default_settings = {
            "tools": [
                {
                    "id": str(uuid.uuid4()),
                    "name": "用 Praat 打开",
                    "path": "",
                    "supported_formats": ".wav, .mp3, .flac, .aiff, .ogg",
                    "command_template": '"{tool_path}" --open {filepaths_quoted}',
                    "enabled": True
                }
            ]
        }
        if not os.path.exists(self.config_path):
            return default_settings
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # 兼容性检查：确保 'tools' 键存在
                if 'tools' not in config:
                    return default_settings
                return config
        except (json.JSONDecodeError, IOError):
            return default_settings

    def save(self):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=4, ensure_ascii=False)
        except IOError as e:
            print(f"无法保存启动器插件配置文件: {e}")

    def get_tools(self):
        return self.settings.get("tools", [])

    def get_tool_by_id(self, tool_id):
        for tool in self.get_tools():
            if tool.get('id') == tool_id:
                return tool
        return None

    def update_tool(self, tool_data):
        for i, tool in enumerate(self.get_tools()):
            if tool.get('id') == tool_data.get('id'):
                self.settings['tools'][i] = tool_data
                self.save()
                return
        # 如果没找到，说明是新工具
        self.settings['tools'].append(tool_data)
        self.save()

    def remove_tool(self, tool_id):
        self.settings['tools'] = [t for t in self.get_tools() if t.get('id') != tool_id]
        self.save()

# ==============================================================================
# 2. 插件设置对话框 (v2.0)
# ==============================================================================
class LauncherSettingsDialog(QDialog):
    """用于管理所有外部工具的UI窗口。"""
    def __init__(self, config_manager, icon_manager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.icon_manager = icon_manager
        
        self.setWindowTitle("外部工具集成中心")
        self.setMinimumSize(800, 500)
        self.resize(850, 550)
        
        self._init_ui()
        self._connect_signals()
        self.populate_tool_list()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        
        # --- Left Panel: Tool List ---
        left_widget = QGroupBox("已配置的工具")
        left_layout = QVBoxLayout(left_widget)
        self.tool_list = QListWidget()
        self.tool_list.setSpacing(2)
        
        list_btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("添加新工具")
        self.remove_btn = QPushButton("移除选中工具")
        list_btn_layout.addWidget(self.add_btn)
        list_btn_layout.addWidget(self.remove_btn)
        
        left_layout.addWidget(self.tool_list)
        left_layout.addLayout(list_btn_layout)
        
        # --- Right Panel: Tool Editor ---
        right_widget = QGroupBox("工具编辑器")
        self.right_layout = QFormLayout(right_widget)
        self.right_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        
        self.tool_id_label = QLabel() # Hidden, for internal use
        self.tool_enabled_check = QCheckBox("启用此工具")
        self.tool_name_edit = QLineEdit()
        
        path_layout = QHBoxLayout()
        self.tool_path_edit = QLineEdit()
        self.browse_btn = QPushButton("浏览...")
        path_layout.addWidget(self.tool_path_edit)
        path_layout.addWidget(self.browse_btn)
        
        self.tool_formats_edit = QLineEdit()
        self.tool_formats_edit.setPlaceholderText("例如: .wav, .mp3, .txt")
        
        self.tool_command_edit = QLineEdit()
        self.tool_command_edit.setPlaceholderText('例如: "{tool_path}" --open {filepaths_quoted}')

        self.right_layout.addRow(self.tool_enabled_check)
        self.right_layout.addRow("工具名称:", self.tool_name_edit)
        self.right_layout.addRow("程序路径:", path_layout)
        self.right_layout.addRow("支持的格式:", self.tool_formats_edit)
        self.right_layout.addRow("执行命令模板:", self.tool_command_edit)

        # --- Help Text ---
        help_text = QTextEdit()
        help_text.setReadOnly(True)
        help_text.setHtml("""
        <h4>命令模板变量说明:</h4>
        <ul>
            <li><code>{tool_path}</code> - 替换为上方设置的程序路径。</li>
            <li><code>{filepaths}</code> - 替换为所有选中文件的路径，用空格分隔。<b>路径中若含空格可能导致问题。</b></li>
            <li><code>{filepaths_quoted}</code> - <b>(推荐)</b> 替换为所有选中文件的路径，每个路径都用双引号包裹。</li>
            <li><code>{filepath}</code> - 替换为<b>第一个</b>选中文件的路径。</li>
            <li><code>{filepath_quoted}</code> - <b>(推荐)</b> 替换为<b>第一个</b>选中文件的路径，并用双引号包裹。</li>
        </ul>
        <p><b>示例 (Praat):</b> <code>"{tool_path}" --open {filepaths_quoted}</code></p>
        <p><b>示例 (Praat 脚本):</b> <code>"{tool_path}" --run "C:/scripts/my_script.praat" {filepath_quoted}</code></p>
        """)
        help_text.setFixedHeight(180)
        self.right_layout.addRow(help_text)
        
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        
        # --- Bottom Buttons ---
        bottom_layout = QHBoxLayout()
        self.save_btn = QPushButton("保存当前工具")
        self.close_btn = QPushButton("关闭")
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.save_btn)
        bottom_layout.addWidget(self.close_btn)
        
        main_layout.addWidget(splitter)
        main_layout.addLayout(bottom_layout)

    def _connect_signals(self):
        self.tool_list.currentItemChanged.connect(self.on_tool_selected)
        self.add_btn.clicked.connect(self.on_add_tool)
        self.remove_btn.clicked.connect(self.on_remove_tool)
        self.browse_btn.clicked.connect(self.on_browse)
        self.save_btn.clicked.connect(self.on_save)
        self.close_btn.clicked.connect(self.accept)

    def populate_tool_list(self):
        current_id = self.tool_list.currentItem().data(Qt.UserRole) if self.tool_list.currentItem() else None
        self.tool_list.clear()
        for tool in self.config_manager.get_tools():
            item = QListWidgetItem(tool.get('name', '未命名工具'))
            item.setData(Qt.UserRole, tool.get('id'))
            item.setIcon(self.icon_manager.get_icon("success") if tool.get('enabled') else self.icon_manager.get_icon("error"))
            self.tool_list.addItem(item)
        
        if current_id:
            items = self.tool_list.findItems(current_id, Qt.MatchExactly)
            if items: self.tool_list.setCurrentItem(items[0])
        elif self.tool_list.count() > 0:
            self.tool_list.setCurrentRow(0)

    def on_tool_selected(self, current, previous):
        if not current:
            self.clear_form()
            self.remove_btn.setEnabled(False)
            return
        
        tool_id = current.data(Qt.UserRole)
        tool_data = self.config_manager.get_tool_by_id(tool_id)
        if tool_data:
            self.tool_id_label.setText(tool_data.get('id', ''))
            self.tool_enabled_check.setChecked(tool_data.get('enabled', True))
            self.tool_name_edit.setText(tool_data.get('name', ''))
            self.tool_path_edit.setText(tool_data.get('path', ''))
            self.tool_formats_edit.setText(tool_data.get('supported_formats', ''))
            self.tool_command_edit.setText(tool_data.get('command_template', ''))
        self.remove_btn.setEnabled(True)

    def clear_form(self):
        self.tool_id_label.clear()
        self.tool_enabled_check.setChecked(True)
        self.tool_name_edit.clear()
        self.tool_path_edit.clear()
        self.tool_formats_edit.clear()
        self.tool_command_edit.clear()

    def on_add_tool(self):
        self.tool_list.clearSelection()
        self.clear_form()
        self.tool_id_label.setText(str(uuid.uuid4()))
        self.tool_name_edit.setText("新工具")
        self.tool_name_edit.setFocus()
        self.tool_name_edit.selectAll()

    def on_remove_tool(self):
        current_item = self.tool_list.currentItem()
        if not current_item: return
        
        reply = QMessageBox.warning(self, "确认移除", f"您确定要移除工具 '{current_item.text()}' 吗？",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            tool_id = current_item.data(Qt.UserRole)
            self.config_manager.remove_tool(tool_id)
            self.populate_tool_list()

    def on_browse(self):
        if sys.platform == "win32": filter_str = "Programs (*.exe);;All files (*)"
        elif sys.platform == "darwin": filter_str = "Applications (*.app);;All files (*)"
        else: filter_str = "All files (*)"
            
        filepath, _ = QFileDialog.getOpenFileName(self, "选择程序", "", filter_str)
        if filepath:
            self.tool_path_edit.setText(filepath)

    def on_save(self):
        tool_id = self.tool_id_label.text()
        if not tool_id:
            QMessageBox.warning(self, "无法保存", "没有要保存的工具。请先'添加新工具'或从左侧选择一个。")
            return

        tool_data = {
            "id": tool_id,
            "enabled": self.tool_enabled_check.isChecked(),
            "name": self.tool_name_edit.text(),
            "path": self.tool_path_edit.text(),
            "supported_formats": self.tool_formats_edit.text(),
            "command_template": self.tool_command_edit.text()
        }

        if not all([tool_data['name'], tool_data['path'], tool_data['command_template']]):
            QMessageBox.warning(self, "信息不完整", "请填写工具名称、程序路径和命令模板。")
            return
            
        self.config_manager.update_tool(tool_data)
        QMessageBox.information(self, "成功", f"工具 '{tool_data['name']}' 已保存。")
        self.populate_tool_list()

# ==============================================================================
# 3. 插件主类 (v2.0)
# ==============================================================================
class ExternalToolLauncherPlugin(BasePlugin):
    """外部工具启动器插件主类 (v2.0)。"""
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.config_manager = LauncherConfigManager()
        self.settings_dialog = None
        self.hooked_modules = []

    def setup(self):
        """
        [v2.1 修改] 向音频管理器模块注册钩子。
        此版本只专注于为音频管理器提供服务。
        """
        # 1. 尝试获取音频管理器模块的实例
        module_instance = getattr(self.main_window, 'audio_manager_page', None)
    
        # 2. 如果成功找到，则设置钩子并记录
        if module_instance:
            setattr(module_instance, 'external_launcher_plugin_active', self)
            self.hooked_modules.append(module_instance)
            print("[Launcher Plugin] 已成功向音频管理器注册。")
            return True
        else:
            # 如果找不到，则插件启用失败
            print("[Launcher Plugin] 错误: 未找到核心的音频管理器模块，插件无法启动。")
            return False

    def teardown(self):
        """移除所有钩子。"""
        for module_instance in self.hooked_modules:
            if hasattr(module_instance, 'external_launcher_plugin_active'):
                delattr(module_instance, 'external_launcher_plugin_active')
        print(f"[Launcher Plugin] 已从 {len(self.hooked_modules)} 个模块注销。")
        self.hooked_modules.clear()
        if self.settings_dialog:
            self.settings_dialog.close()

    def execute(self, **kwargs):
        """当从主插件菜单执行时，打开设置窗口。"""
        if self.settings_dialog is None:
            self.settings_dialog = LauncherSettingsDialog(self.config_manager, self.main_window.icon_manager, self.main_window)
            self.settings_dialog.finished.connect(lambda: setattr(self, 'settings_dialog', None))
        
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def populate_menu(self, menu, filepaths):
        """
        [核心API] 由其他模块调用，用于动态填充右键菜单。
        :param menu: 要填充的 QMenu 对象。
        :param filepaths: 选中的文件路径列表。
        """
        if not filepaths: return

        # 创建一个子菜单，避免主菜单过于拥挤
        launcher_menu = QMenu("用外部工具打开", menu)
        launcher_menu.setIcon(self.main_window.icon_manager.get_icon("export"))
        
        actions_added = 0
        for tool in self.config_manager.get_tools():
            if not tool.get('enabled'): continue

            supported_formats = [f.strip().lower() for f in tool.get('supported_formats', '').split(',') if f.strip()]
            
            # 检查是否有任何选中文件的格式被支持
            is_supported = False
            for f_path in filepaths:
                ext = os.path.splitext(f_path)[1].lower()
                if ext in supported_formats:
                    is_supported = True
                    break
            
            if is_supported:
                action = QAction(tool.get('name'), launcher_menu)
                # 使用 partial 来传递参数，比 lambda 更清晰
                action.triggered.connect(partial(self.launch_tool, tool.get('id'), filepaths))
                launcher_menu.addAction(action)
                actions_added += 1
        
        if actions_added > 0:
            menu.addSeparator()
            menu.addMenu(launcher_menu)

    def launch_tool(self, tool_id, filepaths):
        """根据工具配置和文件列表，执行命令。"""
        tool = self.config_manager.get_tool_by_id(tool_id)
        if not tool:
            QMessageBox.critical(self.main_window, "错误", f"找不到 ID 为 {tool_id} 的工具。")
            return

        command_template = tool.get('command_template', '')
        tool_path = tool.get('path', '')

        if not os.path.exists(tool_path):
            QMessageBox.critical(self.main_window, "路径错误", f"工具 '{tool['name']}' 的路径无效:\n{tool_path}")
            return

        # 准备替换值
        replacements = {
            "{tool_path}": tool_path,
            "{filepath}": filepaths[0] if filepaths else "",
            "{filepath_quoted}": f'"{filepaths[0]}"' if filepaths else "",
            "{filepaths}": " ".join(filepaths),
            "{filepaths_quoted}": " ".join([f'"{f}"' for f in filepaths])
        }

        # 构建最终命令
        final_command = command_template
        for key, value in replacements.items():
            final_command = final_command.replace(key, value)
            
        print(f"[Launcher Plugin] 执行命令: {final_command}")

        try:
            # 在 Windows 上，如果命令本身包含引号，需要特殊处理
            if sys.platform == "win32":
                subprocess.Popen(final_command, shell=False)
            else:
                # 在 macOS 和 Linux 上，使用 shell=True 更容易处理复杂的命令字符串
                subprocess.Popen(final_command, shell=True)
        except Exception as e:
            QMessageBox.critical(self.main_window, "启动失败", f"无法执行命令: \n{final_command}\n\n错误: {e}")