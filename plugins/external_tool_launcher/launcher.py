import os
import sys
import json
import subprocess
import uuid
from functools import partial

# PyQt5 GUI 模块导入
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QLineEdit, QFileDialog, QMessageBox, QAction, QListWidget,
                             QListWidgetItem, QSplitter, QFormLayout, QGroupBox,
                             QCheckBox, QTextEdit, QApplication, QMenu, QComboBox)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon

# 尝试从主程序环境中导入 BasePlugin。
# 这是插件与主程序交互的“契约”。
try:
    from modules.plugin_system import BasePlugin
except ImportError:
    # 如果作为独立脚本运行或在不同环境中进行测试，则需要手动添加模块路径。
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

# ==============================================================================
# 1. LauncherConfigManager - 插件专属配置管理器
# ==============================================================================
class LauncherConfigManager:
    """
    负责外部工具启动器插件的配置读写和管理。
    配置存储在插件目录下的 `config.json` 文件中。
    """
    def __init__(self):
        plugin_dir = os.path.dirname(__file__)
        self.config_path = os.path.join(plugin_dir, 'config.json')
        self.settings = self._load()

    def _load(self):
        """
        加载插件配置。如果文件不存在或损坏，则创建并返回默认配置。
        此方法也负责将内置的“系统默认播放器”工具注入到配置中，
        并兼容旧版本的工具类型。
        """
        # 定义内置的“用系统默认播放器打开”工具的配置
        SYSTEM_DEFAULT_PLAYER_TOOL = {
            "id": "system_default_player",
            "type": "simple_open", # 这是一个“简单文件打开”类型的工具
            "name": "用系统默认播放器打开",
            "path": "system_handler", # 特殊标识符，实际由插件内部处理，不指向真实路径
            "supported_formats": ".wav, .mp3, .flac, .aiff, .ogg, .m4a", # 支持的音频格式
            "enabled": True
        }

        # 定义一个默认的Praat工具示例（高级程序启动类型）
        DEFAULT_PRAAT_TOOL = {
            "id": str(uuid.uuid4()),
            "type": "advanced", # 原来的“简单程序启动”现在是“高级”
            "name": "用 Praat 打开",
            "path": "", # 用户需自行配置Praat路径
            "supported_formats": ".wav, .mp3, .flac, .aiff, .ogg",
            "command_template": '"{tool_path}" --open {filepaths_quoted}', # 典型的Praat打开命令
            "enabled": True
        }
        
        # 初始默认配置，包含Praat示例
        default_settings = {"tools": [DEFAULT_PRAAT_TOOL]}
        
        # 尝试从文件加载现有配置
        config_to_use = default_settings
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                    # 验证加载的配置结构是否有效
                    if isinstance(loaded_config.get('tools'), list):
                        config_to_use = loaded_config
                    else:
                        print("[LauncherConfig] config.json中'tools'结构无效，将使用默认设置。")
            except (json.JSONDecodeError, IOError) as e:
                print(f"[LauncherConfig] 加载config.json时出错: {e}。将使用默认设置。")
        
        # 核心逻辑：确保“系统默认播放器”工具始终存在于列表中
        tools_list = config_to_use.get("tools", [])
        has_default_player = False
        for i, tool in enumerate(tools_list):
            if tool.get('id') == SYSTEM_DEFAULT_PLAYER_TOOL['id']:
                has_default_player = True
                # [兼容性修复] 确保内置工具的类型和路径标识符正确（防止旧版本配置错误）
                if tool.get('type') != SYSTEM_DEFAULT_PLAYER_TOOL['type'] or \
                   tool.get('path') != SYSTEM_DEFAULT_PLAYER_TOOL['path']:
                    tools_list[i] = SYSTEM_DEFAULT_PLAYER_TOOL # 强制更新
                break
        
        # 如果列表中没有“系统默认播放器”工具，则将其添加到列表的开头
        if not has_default_player:
            tools_list.insert(0, SYSTEM_DEFAULT_PLAYER_TOOL)
        
        # [旧版本兼容性处理] 将旧的 'praat_script' 类型工具转换为 'advanced'
        # 并为其提供一个默认的命令模板，以免用户重新配置
        for tool in tools_list:
            if tool.get("type") == "praat_script":
                tool["type"] = "advanced"
                # 提供一个Praat的通用打开模板作为回退
                tool["command_template"] = '"{tool_path}" --open {filepaths_quoted}' 
                print(f"[LauncherConfig] 将旧的Praat脚本工具'{tool.get('name')}'转换为高级程序启动工具。")

        config_to_use['tools'] = tools_list
        return config_to_use

    def save(self):
        """将当前配置设置保存到 `config.json` 文件中。"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                # `ensure_ascii=False` 确保中文字符正确写入
                json.dump(self.settings, f, indent=4, ensure_ascii=False)
        except IOError as e:
            print(f"无法保存启动器插件配置文件: {e}")

    def get_tools(self):
        """返回所有已配置的工具列表。"""
        return self.settings.get("tools", [])

    def get_tool_by_id(self, tool_id):
        """根据ID查找并返回一个工具的配置数据。"""
        for tool in self.get_tools():
            if tool.get('id') == tool_id:
                return tool
        return None

    def update_tool(self, tool_data):
        """更新或添加一个工具的配置数据，并保存到文件。"""
        for i, tool in enumerate(self.get_tools()):
            if tool.get('id') == tool_data.get('id'):
                self.settings['tools'][i] = tool_data
                self.save()
                return
        # 如果未找到，则作为新工具添加
        self.settings['tools'].append(tool_data)
        self.save()

    def remove_tool(self, tool_id):
        """根据ID移除一个工具的配置数据，并保存到文件。"""
        self.settings['tools'] = [t for t in self.get_tools() if t.get('id') != tool_id]
        self.save()

# ==============================================================================
# 2. LauncherSettingsDialog - 插件设置对话框
# ==============================================================================
class LauncherSettingsDialog(QDialog):
    """
    用于管理所有外部工具的UI窗口。
    用户可以在此添加、编辑、移除工具，并配置其启动方式。
    """
    def __init__(self, config_manager, icon_manager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.icon_manager = icon_manager
        
        self.setWindowTitle("外部工具集成中心")
        self.setMinimumSize(800, 500)
        self.resize(850, 550) # 设置初始大小
        
        self._init_ui() # 初始化用户界面
        self._connect_signals() # 连接UI信号
        self.populate_tool_list() # 填充工具列表

    def _init_ui(self):
        """构建对话框的用户界面布局。"""
        main_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal) # 主水平分割器
        
        # --- 左侧面板：工具列表 ---
        left_widget = QGroupBox("已配置的工具")
        left_layout = QVBoxLayout(left_widget)
        self.tool_list = QListWidget()
        self.tool_list.setSpacing(2) # 列表项间距
        
        list_btn_layout = QHBoxLayout() # 列表下方按钮布局
        self.add_btn = QPushButton("添加新工具")
        self.remove_btn = QPushButton("移除选中工具")
        list_btn_layout.addWidget(self.add_btn)
        list_btn_layout.addWidget(self.remove_btn)
        
        left_layout.addWidget(self.tool_list)
        left_layout.addLayout(list_btn_layout)
        
        # --- 右侧面板：工具编辑器 ---
        right_widget = QGroupBox("工具编辑器")
        self.right_layout = QFormLayout(right_widget)
        self.right_layout.setRowWrapPolicy(QFormLayout.WrapLongRows) # 允许长行自动换行
        
        self.tool_id_label = QLabel() # 隐藏标签，用于存储当前编辑工具的ID
        self.tool_enabled_check = QCheckBox("启用此工具") # 工具启用/禁用复选框
        
        # [简化] 工具类型选择下拉框，只包含两种类型
        self.tool_type_combo = QComboBox()
        self.tool_type_combo.addItems(["简单文件打开", "高级程序启动"])
        
        self.tool_name_edit = QLineEdit() # 工具名称输入框
        
        # 程序路径/脚本路径（标签动态变化）
        self.path_label = QLabel("程序路径:") 
        path_layout = QHBoxLayout()
        self.tool_path_edit = QLineEdit()
        self.browse_btn = QPushButton("浏览...") # 浏览程序/脚本按钮
        path_layout.addWidget(self.tool_path_edit)
        path_layout.addWidget(self.browse_btn)
        
        self.tool_formats_edit = QLineEdit() # 支持格式输入框
        self.tool_formats_edit.setPlaceholderText("例如: .wav, .mp3, .txt")
        
        # 命令模板（标签和输入框动态变化）
        self.command_label = QLabel("执行命令模板:")
        self.tool_command_edit = QLineEdit()
        self.tool_command_edit.setPlaceholderText('例如: "{tool_path}" --open {filepaths_quoted}')

        # 将所有编辑控件添加到表单布局
        self.right_layout.addRow(self.tool_enabled_check)
        self.right_layout.addRow("工具类型:", self.tool_type_combo)
        self.right_layout.addRow("工具名称:", self.tool_name_edit)
        self.right_layout.addRow(self.path_label, path_layout)
        self.right_layout.addRow("支持的格式:", self.tool_formats_edit)
        self.right_layout.addRow(self.command_label, self.tool_command_edit)

        # 帮助文本区域
        help_text = QTextEdit()
        help_text.setReadOnly(True)
        help_text.setHtml("""
        <h4>命令模板变量说明 (仅限“高级程序启动”):</h4>
        <ul>
            <li><code>{tool_path}</code> - 替换为上方设置的程序路径。</li>
            <li><code>{filepaths}</code> - 替换为所有选中文件的路径，用空格分隔。</li>
            <li><code>{filepaths_quoted}</code> - <b>(推荐)</b> 替换为所有选中文件的路径，每个路径都用双引号包裹。</li>
            <li><code>{filepath}</code> - 替换为<b>第一个</b>选中文件的路径。</li>
            <li><code>{filepath_quoted}</code> - <b>(推荐)</b> 替换为<b>第一个</b>选中文件的路径，并用双引号包裹。</li>
        </ul>
        <p><b>示例 (Audacity):</b> <code>"{tool_path}" {filepaths_quoted}</code></p>
        <p><b>示例 (Praat):</b> <code>"{tool_path}" --open {filepaths_quoted}</code></p>
        """)
        help_text.setFixedHeight(180) # 固定帮助文本区域高度
        self.right_layout.addRow(help_text)
        
        # 将左右面板添加到分割器中
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 1) # 左侧宽度比例
        splitter.setStretchFactor(1, 2) # 右侧宽度比例
        
        # 底部按钮区域
        bottom_layout = QHBoxLayout()
        self.save_btn = QPushButton("保存当前工具")
        self.close_btn = QPushButton("关闭")
        bottom_layout.addStretch() # 将按钮推到右侧
        bottom_layout.addWidget(self.save_btn)
        bottom_layout.addWidget(self.close_btn)
        
        main_layout.addWidget(splitter)
        main_layout.addLayout(bottom_layout)

    def _connect_signals(self):
        """连接所有UI控件的信号到相应的槽函数。"""
        self.tool_list.currentItemChanged.connect(self.on_tool_selected)
        self.add_btn.clicked.connect(self.on_add_tool)
        self.remove_btn.clicked.connect(self.on_remove_tool)
        self.browse_btn.clicked.connect(self.on_browse)
        self.save_btn.clicked.connect(self.on_save)
        self.close_btn.clicked.connect(self.accept)
        self.tool_type_combo.currentIndexChanged.connect(self.on_tool_type_changed)

    def populate_tool_list(self):
        """从配置管理器加载工具列表，并填充到左侧QListWidget中。"""
        # 记录当前选中项的ID，以便刷新后恢复选中
        current_id = self.tool_list.currentItem().data(Qt.UserRole) if self.tool_list.currentItem() else None
        self.tool_list.clear() # 清空列表
        
        for tool in self.config_manager.get_tools():
            item = QListWidgetItem(tool.get('name', '未命名工具'))
            item.setData(Qt.UserRole, tool.get('id')) # 存储工具ID
            # 根据工具的启用状态设置图标
            item.setIcon(self.icon_manager.get_icon("success") if tool.get('enabled') else self.icon_manager.get_icon("error"))
            self.tool_list.addItem(item)
        
        # 尝试恢复之前的选中状态，或默认选中第一个
        if current_id:
            items = self.tool_list.findItems(current_id, Qt.MatchExactly)
            if items: self.tool_list.setCurrentItem(items[0])
        elif self.tool_list.count() > 0:
            self.tool_list.setCurrentRow(0) # 默认选中第一行

    def on_tool_selected(self, current, previous):
        """当用户在工具列表中选择不同工具时，更新右侧编辑表单的内容。"""
        if not current:
            # 如果没有选中任何项（例如列表被清空），则清空并禁用表单
            self.clear_form()
            self.remove_btn.setEnabled(False)
            self.set_form_enabled(False) 
            return
        
        tool_id = current.data(Qt.UserRole) # 获取选中工具的ID
        tool_data = self.config_manager.get_tool_by_id(tool_id) # 从配置管理器获取工具数据
        if tool_data:
            # 填充表单控件
            self.tool_id_label.setText(tool_data.get('id', ''))
            self.tool_enabled_check.setChecked(tool_data.get('enabled', True))
            self.tool_name_edit.setText(tool_data.get('name', ''))
            self.tool_path_edit.setText(tool_data.get('path', ''))
            self.tool_formats_edit.setText(tool_data.get('supported_formats', ''))
            self.tool_command_edit.setText(tool_data.get('command_template', ''))
            
            # [更新] 加载工具类型并设置下拉框选中项
            tool_type = tool_data.get("type", "advanced")
            if tool_type == "simple_open":
                self.tool_type_combo.setCurrentIndex(0)
            else: # "advanced" (或兼容旧的 "simple", "praat_script")
                self.tool_type_combo.setCurrentIndex(1)

        # 检查是否是系统内置工具（如“系统默认播放器”）
        is_system_tool = (tool_id == 'system_default_player')
        
        # 根据是否为系统工具，设置表单的启用状态
        self.set_form_enabled(not is_system_tool, keep_name_and_enable=True)
        self.remove_btn.setEnabled(not is_system_tool) # 系统工具不可移除

        # 触发工具类型变化事件，以确保UI根据类型正确显示/隐藏字段
        self.on_tool_type_changed(self.tool_type_combo.currentIndex())

    def clear_form(self):
        """清空右侧编辑表单的所有内容。"""
        self.tool_id_label.clear()
        self.tool_enabled_check.setChecked(True)
        self.tool_type_combo.setCurrentIndex(0) # 默认为“简单文件打开”
        self.tool_name_edit.clear()
        self.tool_path_edit.clear()
        self.tool_formats_edit.clear()
        self.tool_command_edit.clear()
        # 调用 on_tool_type_changed 确保UI重置到“简单文件打开”模式
        self.on_tool_type_changed(0) 

    def on_add_tool(self):
        """
        处理“添加新工具”按钮的点击事件。
        为新工具准备一个干净、可编辑的表单。
        """
        # [修复] 临时阻塞信号，防止 `clearSelection` 触发 `on_tool_selected`
        self.tool_list.blockSignals(True)
        self.tool_list.clearSelection()
        self.tool_list.blockSignals(False)

        self.clear_form() # 清空表单
        
        # 显式启用表单，以便用户可以编辑新工具
        self.set_form_enabled(True) 
        self.remove_btn.setEnabled(False) # 新工具还未保存，不能移除

        # 填充新工具的初始信息
        self.tool_id_label.setText(str(uuid.uuid4())) # 生成新的唯一ID
        self.tool_name_edit.setText("新工具")
        
        # 将焦点设置到名称框，方便用户立即编辑
        self.tool_name_edit.setFocus()
        self.tool_name_edit.selectAll()

    def on_remove_tool(self):
        """处理“移除选中工具”按钮的点击事件。"""
        current_item = self.tool_list.currentItem()
        if not current_item: return # 没有选中项则返回
        
        tool_id = current_item.data(Qt.UserRole)
        # 阻止移除内置的“系统默认播放器”工具
        if tool_id == 'system_default_player':
            QMessageBox.information(self, "无法移除", "“用系统默认播放器打开”是一个内置功能，无法被移除。")
            return

        reply = QMessageBox.warning(self, "确认移除", f"您确定要移除工具 '{current_item.text()}' 吗？",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.config_manager.remove_tool(tool_id) # 从配置中移除
            self.populate_tool_list() # 刷新列表

    def on_browse(self):
        """处理“浏览...”按钮的点击事件，让用户选择程序文件。"""
        # 根据操作系统设置文件过滤器
        if sys.platform == "win32": filter_str = "Programs (*.exe);;All files (*)"
        elif sys.platform == "darwin": filter_str = "Applications (*.app);;All files (*)"
        else: filter_str = "All files (*)"
            
        title = "选择程序" # 对话框标题
        
        filepath, _ = QFileDialog.getOpenFileName(self, title, "", filter_str)
        if filepath:
            self.tool_path_edit.setText(filepath)

    def on_tool_type_changed(self, index):
        """
        当用户在“工具类型”下拉框中选择不同类型时，动态更新UI。
        根据选择的类型，显示或隐藏命令模板和支持格式等字段。
        """
        tool_type_text = self.tool_type_combo.currentText()
        is_simple_open = (tool_type_text == "简单文件打开")
        is_advanced = (tool_type_text == "高级程序启动")
        
        # --- 更新标签文本 ---
        self.path_label.setText("程序路径:")
        self.browse_btn.setToolTip("浏览程序文件")
        
        # --- 控制UI元素的可见性 ---
        # 命令模板只在“高级程序启动”模式下可见
        self.command_label.setVisible(is_advanced)
        self.tool_command_edit.setVisible(is_advanced)
        
        # “支持的格式”只在“高级程序启动”模式下可见
        formats_label = self.right_layout.labelForField(self.tool_formats_edit)
        if formats_label: # 确保标签存在
            formats_label.setVisible(is_advanced)
        self.tool_formats_edit.setVisible(is_advanced)

        # --- 控制UI元素的启用状态 ---
        # 检查当前是否正在编辑受保护的系统工具
        current_tool_item = self.tool_list.currentItem()
        current_tool_id = current_tool_item.data(Qt.UserRole) if current_tool_item else None
        is_system_tool = (current_tool_id == 'system_default_player')

        if is_system_tool:
            # 如果是系统工具，大部分编辑功能被禁用
            self.set_form_enabled(False, keep_name_and_enable=True)
            self.tool_type_combo.setEnabled(False) # 系统工具类型不可更改
        else:
            # 对于非系统工具（包括新添加的工具），根据类型启用/禁用特定字段
            self.set_form_enabled(True) # 首先启用所有可编辑项
            self.tool_command_edit.setEnabled(is_advanced) # 命令模板只在高级模式下启用
            self.tool_formats_edit.setEnabled(is_advanced) # 支持格式只在高级模式下启用

    def set_form_enabled(self, enabled, keep_name_and_enable=False):
        """
        一个辅助方法，用于批量设置右侧表单中控件的启用/禁用状态。
        :param enabled: 是否启用这些控件。
        :param keep_name_and_enable: 如果为True，即使其他控件被禁用，
                                      “工具名称”和“启用此工具”复选框仍保持启用。
        """
        self.tool_name_edit.setEnabled(enabled)
        self.tool_type_combo.setEnabled(enabled)
        self.tool_path_edit.setEnabled(enabled)
        self.browse_btn.setEnabled(enabled)
        self.tool_formats_edit.setEnabled(enabled)
        self.tool_command_edit.setEnabled(enabled)
        self.save_btn.setEnabled(enabled)
        
        if keep_name_and_enable:
            self.tool_name_edit.setEnabled(True)
            self.tool_enabled_check.setEnabled(True)
            self.save_btn.setEnabled(True) # 系统工具的保存按钮只控制名称和启用状态

    def on_save(self):
        """处理“保存当前工具”按钮的点击事件，将表单数据保存到配置。"""
        tool_id = self.tool_id_label.text()
        if not tool_id:
            QMessageBox.warning(self, "无法保存", "没有要保存的工具。请先'添加新工具'或从左侧选择一个。")
            return

        tool_type_text = self.tool_type_combo.currentText()
        type_key = "advanced" if tool_type_text == "高级程序启动" else "simple_open"

        tool_data = {
            "id": tool_id,
            "type": type_key,
            "enabled": self.tool_enabled_check.isChecked(),
            "name": self.tool_name_edit.text(),
            "path": self.tool_path_edit.text(),
            # “支持的格式”只对“高级程序启动”类型有意义，简单打开类型默认为所有文件
            "supported_formats": self.tool_formats_edit.text() if type_key == "advanced" else ".*", 
            "command_template": self.tool_command_edit.text()
        }

        # 字段验证
        if not tool_data['name']:
            QMessageBox.warning(self, "信息不完整", "工具名称不能为空。")
            return

        # 如果不是系统默认播放器工具，则程序路径是必填项
        if tool_id != 'system_default_player' and not tool_data['path']:
            QMessageBox.warning(self, "信息不完整", "程序路径不能为空。")
            return
        
        # 针对“高级程序启动”类型，命令模板是必填项
        if type_key == "advanced" and not tool_data['command_template']:
            QMessageBox.warning(self, "信息不完整", "高级程序启动需要填写命令模板。")
            return
        
        # 对于“简单文件打开”类型，命令模板由插件内部生成，清空用户可能输入的无效内容
        if type_key == "simple_open":
            tool_data["command_template"] = ""

        self.config_manager.update_tool(tool_data) # 更新或添加工具配置
        QMessageBox.information(self, "成功", f"工具 '{tool_data['name']}' 已保存。")
        self.populate_tool_list() # 刷新工具列表

# ==============================================================================
# 3. ExternalToolLauncherPlugin - 插件主类
# ==============================================================================
class ExternalToolLauncherPlugin(BasePlugin):
    """
    外部工具启动器插件的主入口点。
    负责插件的生命周期管理，并提供与其他模块（如文件管理器）的集成API。
    """
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.config_manager = LauncherConfigManager()
        self.settings_dialog = None # 用于存储设置对话框实例，实现单例模式
        self.hooked_modules = [] # 记录已挂钩的模块实例

    def setup(self):
        """
        插件启用时调用。
        尝试向音频管理器模块注册钩子，使其能在右键菜单中调用此插件。
        """
        module_instance = getattr(self.main_window, 'audio_manager_page', None)
        if module_instance:
            # 将插件实例挂载到目标模块上，作为“钩子”
            setattr(module_instance, 'external_launcher_plugin_active', self)
            self.hooked_modules.append(module_instance)
            print("[Launcher Plugin] 已成功向音频管理器注册。")
            return True
        else:
            print("[Launcher Plugin] 错误: 未找到核心的音频管理器模块，插件无法启动。")
            return False # 启用失败

    def teardown(self):
        """
        插件禁用时调用。
        移除所有已注册的钩子，并关闭可能存在的设置对话框，释放资源。
        """
        for module_instance in self.hooked_modules:
            if hasattr(module_instance, 'external_launcher_plugin_active'):
                delattr(module_instance, 'external_launcher_plugin_active') # 移除钩子
        print(f"[Launcher Plugin] 已从 {len(self.hooked_modules)} 个模块注销。")
        self.hooked_modules.clear()
        if self.settings_dialog:
            self.settings_dialog.close() # 关闭对话框

    def execute(self, **kwargs):
        """
        执行插件主功能。当用户从主插件菜单点击时调用。
        打开或激活外部工具集成中心的设置对话框（采用单例模式）。
        """
        if self.settings_dialog is None:
            self.settings_dialog = LauncherSettingsDialog(self.config_manager, self.main_window.icon_manager, self.main_window)
            # 当对话框关闭时，清除对它的引用
            self.settings_dialog.finished.connect(lambda: setattr(self, 'settings_dialog', None))
        
        self.settings_dialog.show()
        self.settings_dialog.raise_() # 将窗口置于顶层
        self.settings_dialog.activateWindow() # 激活窗口

    def populate_menu(self, menu, filepaths):
        """
        [核心API] 由其他模块（如文件管理器、音频管理器）调用，
        用于动态填充其右键菜单中的“用外部工具打开”子菜单。
        :param menu: 要填充的 QMenu 对象实例。
        :param filepaths: 当前选中的文件路径列表。
        """
        if not filepaths: return # 没有文件选中则不填充

        # 创建一个子菜单，避免主菜单过于拥挤
        launcher_menu = QMenu("用外部工具打开", menu)
        launcher_menu.setIcon(self.main_window.icon_manager.get_icon("export")) # 设置子菜单图标
        
        actions_added = 0
        for tool in self.config_manager.get_tools():
            if not tool.get('enabled'): continue # 跳过未启用的工具

            supported_formats_str = tool.get('supported_formats', '')
            # 对于“简单文件打开”类型且支持所有格式的工具，或者系统默认播放器，
            # 视为支持所有文件类型。
            if tool.get('type') == 'simple_open' and supported_formats_str == ".*":
                is_supported = True
            else:
                # 否则，检查选中的文件格式是否与工具支持的格式匹配
                supported_formats = [f.strip().lower() for f in supported_formats_str.split(',') if f.strip()]
                is_supported = False
                for f_path in filepaths:
                    ext = os.path.splitext(f_path)[1].lower()
                    if ext in supported_formats:
                        is_supported = True
                        break # 只要有一个文件匹配，就认为该工具可用
            
            if is_supported:
                action = QAction(tool.get('name'), launcher_menu)
                # 使用 functools.partial 将 tool_id 和 filepaths 作为参数传递给 launch_tool
                action.triggered.connect(partial(self.launch_tool, tool.get('id'), filepaths))
                launcher_menu.addAction(action)
                actions_added += 1
        
        # 如果有任何工具被添加到子菜单，则将子菜单添加到主菜单
        if actions_added > 0:
            menu.addSeparator()
            menu.addMenu(launcher_menu)

    def launch_tool(self, tool_id, filepaths):
        """
        [v2.8 - Quoting Fix]
        根据工具配置和选中的文件列表，执行外部程序命令。
        此版本修复了高级程序启动模式下对 {tool_path} 占位符进行双重引用的问题。
        """
        # --- 系统默认播放器逻辑 (保持不变) ---
        if tool_id == 'system_default_player':
            if filepaths:
                self._launch_with_system_default(filepaths[0])
            return

        # --- 工具数据获取 (保持不变) ---
        tool = self.config_manager.get_tool_by_id(tool_id)
        if not tool:
            QMessageBox.critical(self.main_window, "错误", f"找不到 ID 为 {tool_id} 的工具。")
            return

        tool_type = tool.get("type", "advanced")
        if tool_type == "simple": tool_type = "advanced"

        tool_path = tool.get("path")
        if tool_path != "system_handler" and (not tool_path or not os.path.exists(tool_path)):
            QMessageBox.critical(self.main_window, "路径错误", f"工具 '{tool['name']}' 的路径无效或不存在:\n{tool_path}")
            return
            
        filepaths_quoted_str = " ".join([f'"{f}"' for f in filepaths])

        # --- 根据工具类型构建命令 ---
        if tool_type == "simple_open":
            # 简单文件打开：使用参数列表和 shell=False，更健壮
            command_list = [tool_path] + filepaths
            log_command = ' '.join(f'"{arg}"' if ' ' in arg else arg for arg in command_list)
            print(f"[Launcher Plugin] 执行命令: {log_command}")
            try:
                subprocess.Popen(command_list, shell=False)
            except Exception as e:
                QMessageBox.critical(self.main_window, "启动失败", f"无法执行命令: \n{log_command}\n\n错误: {e}")

        elif tool_type == "advanced":
            command_template = tool.get('command_template', '')
            if not command_template:
                 QMessageBox.critical(self.main_window, "命令模板缺失", f"工具 '{tool['name']}' 未配置命令模板。")
                 return
            
            # [核心修复] 直接替换占位符，不添加额外的引号
            # ----------------------------------------------------
            final_command_str = command_template.replace("{tool_path}", tool_path)
            # ----------------------------------------------------
            
            final_command_str = final_command_str.replace("{filepaths_quoted}", filepaths_quoted_str)
            final_command_str = final_command_str.replace("{filepaths}", " ".join(filepaths))
            
            if filepaths:
                final_command_str = final_command_str.replace("{filepath_quoted}", f'"{filepaths[0]}"')
                final_command_str = final_command_str.replace("{filepath}", filepaths[0])
            else:
                final_command_str = final_command_str.replace("{filepath_quoted}", "")
                final_command_str = final_command_str.replace("{filepath}", "")

            print(f"[Launcher Plugin] 执行命令: {final_command_str}")
            try:
                subprocess.Popen(final_command_str, shell=True)
            except Exception as e:
                QMessageBox.critical(self.main_window, "启动失败", f"无法执行命令: \n{final_command_str}\n\n错误: {e}")
            
        else: # 未知工具类型
            QMessageBox.warning(self.main_window, "未知工具类型", f"工具 '{tool['name']}' 配置了未知类型: {tool_type}")

    def _launch_with_system_default(self, filepath):
        """
        使用操作系统关联的默认程序打开文件。
        根据操作系统调用不同的命令。
        :param filepath: 要打开的文件路径。
        """
        try:
            if not os.path.exists(filepath):
                QMessageBox.warning(self.main_window, "文件不存在", f"无法找到文件:\n{filepath}")
                return
            
            print(f"[Launcher Plugin] 使用系统默认程序打开: {filepath}")
            
            if sys.platform == "win32":
                # Windows 使用 os.startfile
                os.startfile(os.path.normpath(filepath))
            elif sys.platform == "darwin":
                # macOS 使用 'open' 命令
                subprocess.Popen(['open', filepath])
            else:
                # Linux (及其他类Unix系统) 使用 'xdg-open' 命令
                subprocess.Popen(['xdg-open', filepath])
        except Exception as e:
            QMessageBox.critical(self.main_window, "打开失败", f"无法使用系统默认程序打开文件:\n{filepath}\n\n错误: {e}")

# --- END OF FILE plugins/external_tool_launcher/launcher.py ---