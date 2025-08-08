# --- START OF FILE plugins/file_manager/file_manager.py ---

import os
import sys
import shutil
import json
import textwrap
import pathlib
from datetime import datetime, timedelta
import subprocess
import uuid

# PyQt5 核心模块导入
from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
                             QMessageBox, QSplitter, QLabel, QMenu, QHeaderView, QLineEdit,
                             QTreeWidgetItemIterator, QApplication, QShortcut, QFormLayout,
                             QSlider, QDialogButtonBox, QCheckBox, QGroupBox, QInputDialog, QAbstractItemView)
from PyQt5.QtCore import Qt, QSize, QBuffer, QByteArray, QObject, QEvent, QTimer # QBuffer, QByteArray 用于图片Base64编码
from PyQt5.QtGui import QIcon, QKeySequence, QPixmap, QPainter, QColor, QPen # QPixmap 用于图片处理，QKeySequence 用于快捷键
try:
    import numpy as np
    import soundfile as sf
    AUDIO_LIBS_AVAILABLE = True
except ImportError:
    AUDIO_LIBS_AVAILABLE = False

# --- 项目路径发现 ---
# 插件必须是自给自足的，它自己计算项目根目录。
def _get_project_root():
    """
    计算并返回 PhonAcq Assistant 项目的根目录路径。
    此函数兼容程序被 PyInstaller 打包后运行和从源代码直接运行两种情况。
    """
    if getattr(sys, 'frozen', False):
        # 如果程序被打包成可执行文件，则根目录就是可执行文件所在的目录。
        return os.path.dirname(sys.executable)
    else:
        # 如果从源代码运行，当前文件位于 '项目根目录/plugins/file_manager/'。
        # 因此需要向上返回两级目录才能到达项目根目录。
        return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

# 在模块加载时，立即计算并定义所有需要的全局路径常量。
BASE_PATH = _get_project_root()

# 定义一些核心目录的路径，用于文件类型判断和跨模块跳转。
WORD_LIST_DIR = os.path.join(BASE_PATH, "word_lists")
DIALECT_VISUAL_WORDLIST_DIR = os.path.join(BASE_PATH, "dialect_visual_wordlists")

# --- 动态导入插件系统基类 ---
try:
    # 尝试从主程序模块中导入 BasePlugin。
    from plugin_system import BasePlugin
except ImportError:
    # 如果导入失败（例如，在独立测试插件时），则将 'modules' 目录添加到 sys.path，
    # 以便找到 plugin_system.py。
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

# ==============================================================================
# 自定义表格项：用于支持按数字排序的 QTableWidgetItem
# ==============================================================================
class NumericTableWidgetItem(QTableWidgetItem):
    """
    继承自 QTableWidgetItem，重写其比较方法，
    使得表格在按此列排序时，能根据存储在 Qt.UserRole 中的原始数值进行排序，
    而不是根据显示的字符串文本。
    主要用于文件大小列的正确排序。
    """
    def __lt__(self, other):
        """
        定义“小于”操作符。当表格进行升序排序时，会调用此方法。
        """
        # 比较存储在 Qt.UserRole 中的原始数值（例如字节数）。
        return self.data(Qt.UserRole) < other.data(Qt.UserRole)

# ==============================================================================
# 回收站清理策略配置对话框
# ==============================================================================
class TrashPolicyDialog(QDialog):
    """
    提供一个可视化界面，允许用户配置回收站的自动清理策略。
    策略包括：按文件保留天数、按文件数量上限、按回收站总体积上限。
    """
    def __init__(self, policy_path, parent=None):
        """
        初始化回收站策略配置对话框。
        :param policy_path: 回收站策略配置文件的完整路径。
        :param parent: 父 QWidget，通常是 FileManagerDialog 实例。
        """
        super().__init__(parent)
        self.policy_path = policy_path
        self.settings = self._load_policy() # 加载当前策略设置
        self.setWindowTitle("回收站清理策略配置")
        self.setMinimumWidth(450)
        self._init_ui() # 构建UI

    def _init_ui(self):
        """构建对话框的用户界面。"""
        layout = QVBoxLayout(self)
        
        # --- 自动清理总开关 ---
        self.enabled_check = QCheckBox("启用回收站自动清理")
        self.enabled_check.setChecked(self.settings.get("enabled", True))
        self.enabled_check.setToolTip("取消勾选将完全禁用所有自动清理规则。\n回收站将只增不减，直到您手动清空。")
        layout.addWidget(self.enabled_check)

        # --- 清理规则组框 ---
        self.policy_group = QGroupBox("清理规则 (按以下顺序执行)")
        form_layout = QFormLayout(self.policy_group)

        # 辅助函数：用于创建单个策略配置行（包含复选框、滑块和数值标签）
        def create_policy_row(key, label_text, tooltip_text, min_val, max_val, suffix):
            """
            创建一个包含复选框、滑块和数值标签的策略配置行。
            :param key: 策略设置在字典中的键名后缀 (e.g., "days", "count", "size_mb")。
            :param label_text: 复选框显示的文本。
            :param tooltip_text: 复选框的工具提示文本。
            :param min_val: 滑块的最小值。
            :param max_val: 滑块的最大值。
            :param suffix: 数值标签的单位后缀 (e.g., "天", "个", "MB")。
            :return: 返回 (复选框实例, 滑块实例)。
            """
            checkbox = QCheckBox(label_text)
            # 从设置中读取该策略是否启用，默认启用
            checkbox.setChecked(self.settings.get(f"by_{key}_enabled", True))
            checkbox.setToolTip(tooltip_text) # 设置工具提示
            
            slider = QSlider(Qt.Horizontal)
            slider.setRange(min_val, max_val)
            # 从设置中读取滑块的当前值，默认值根据策略类型而定
            slider.setValue(self.settings.get(f"max_{key}", 30)) 
            
            value_label = QLabel() # 显示滑块当前值的标签
            
            # 连接滑块的 valueChanged 信号，实时更新数值标签
            def update_label(val):
                value_label.setText(f"{val} {suffix}")
            slider.valueChanged.connect(update_label)
            update_label(slider.value()) # 初始化时更新一次标签显示
            
            # 连接复选框的 toggled 信号，控制滑块和数值标签的启用/禁用状态
            checkbox.toggled.connect(slider.setEnabled)
            checkbox.toggled.connect(value_label.setEnabled)
            # 根据复选框的初始状态设置滑块和标签的启用状态
            slider.setEnabled(checkbox.isChecked())
            value_label.setEnabled(checkbox.isChecked())
            
            h_layout = QHBoxLayout() # 水平布局，放置滑块和数值标签
            h_layout.addWidget(slider)
            h_layout.addWidget(value_label)
            
            form_layout.addRow(checkbox, h_layout) # 将复选框和水平布局添加到表单布局中
            return checkbox, slider

        # --- 创建三个策略配置行实例 ---
        self.days_check, self.days_slider = create_policy_row(
            "days", "按时间清理:",
            "勾选后，将自动删除在回收站中存放超过指定天数的文件。",
            1, 90, "天" # 1到90天
        )
        self.count_check, self.count_slider = create_policy_row(
            "count", "按数量清理:",
            "勾选后，当文件总数超过指定数量时，将从最旧的文件开始删除，直到满足数量限制。",
            50, 2000, "个" # 50到2000个文件
        )
        self.size_check, self.size_slider = create_policy_row(
            "size_mb", "按体积清理:",
            "勾选后，当回收站总体积超过指定大小时，将从最旧的文件开始删除，直到满足体积限制。",
            100, 5120, "MB" # 100MB到5GB
        )

        layout.addWidget(self.policy_group) # 将策略组框添加到主布局

        # 连接总开关的 toggled 信号，控制整个策略组的启用/禁用状态
        self.enabled_check.toggled.connect(self.policy_group.setEnabled)
        self.policy_group.setEnabled(self.enabled_check.isChecked()) # 根据初始状态设置

        # --- 保存和取消按钮 ---
        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.on_save) # 连接保存信号
        button_box.rejected.connect(self.reject) # 连接取消信号
        layout.addWidget(button_box) # 添加按钮盒

    def _load_policy(self):
        """
        从 JSON 配置文件中加载回收站清理策略。
        如果文件不存在或解析失败，则返回默认策略。
        """
        # 默认策略：所有规则都启用，并设置默认值
        defaults = {
            "enabled": True, 
            "by_days_enabled": True, "max_days": 30,
            "by_count_enabled": True, "max_count": 500,
            "by_size_mb_enabled": True, "max_size_mb": 1024
        }
        if not os.path.exists(self.policy_path):
            return defaults
        try:
            with open(self.policy_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
                defaults.update(settings) # 用从文件加载的设置覆盖默认值
                return defaults
        except (json.JSONDecodeError, IOError):
            # 如果文件损坏或无法读取，回退到默认策略
            return defaults

    def on_save(self):
        """
        将用户在UI中配置的策略保存到 JSON 配置文件中。
        """
        # 从UI控件中获取当前设置值
        self.settings["enabled"] = self.enabled_check.isChecked()
        self.settings["by_days_enabled"] = self.days_check.isChecked()
        self.settings["max_days"] = self.days_slider.value()
        self.settings["by_count_enabled"] = self.count_check.isChecked()
        self.settings["max_count"] = self.count_slider.value()
        self.settings["by_size_mb_enabled"] = self.size_check.isChecked()
        self.settings["max_size_mb"] = self.size_slider.value()
        
        try:
            # 将设置保存到 JSON 文件
            with open(self.policy_path, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=4)
            QMessageBox.information(self, "成功", "回收站策略已保存。")
            self.accept() # 关闭对话框
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法保存策略文件:\n{e}")
# --- Add these imports at the top of the file ---
from PyQt5.QtWidgets import QScrollArea, QTextEdit
from PyQt5.QtGui import QIntValidator, QDoubleValidator

# ==============================================================================
# Dynamic JSON Editor Dialog
# ==============================================================================
class DynamicJsonEditorDialog(QDialog):
    """
    A dialog that dynamically generates an editor UI based on the
    structure and data types of an input JSON file.
    """
    def __init__(self, json_path, parent=None):
        super().__init__(parent)
        self.json_path = json_path
        self.setWindowTitle(f"动态编辑: {os.path.basename(json_path)}")
        self.setMinimumSize(500, 600)

        # Stores the mapping from a full key path (e.g., 'ui.theme') to its widget and original type
        self.widget_map = {}
        
        try:
            with open(self.json_path, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法读取或解析JSON文件:\n{e}")
            # Use QTimer to close the dialog after the message box is shown
            QTimer.singleShot(0, self.reject)
            return

        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)

        # Use a QScrollArea to handle potentially long configuration files
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        main_layout.addWidget(scroll_area)
        
        container_widget = QWidget()
        scroll_area.setWidget(container_widget)
        
        self.form_layout = QFormLayout(container_widget)
        self.form_layout.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.form_layout.setLabelAlignment(Qt.AlignRight)

        # Start the recursive UI build process
        self._build_ui_recursive(self.data, self.form_layout)

        # Standard Save/Cancel buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.on_save)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def _build_ui_recursive(self, data_dict, parent_layout, prefix=""):
        """
        Recursively builds the editor UI from a dictionary.
        :param data_dict: The dictionary (or sub-dictionary) to process.
        :param parent_layout: The QLayout to add generated widgets to.
        :param prefix: The key prefix for nested objects (e.g., 'audio_settings').
        """
        for key, value in data_dict.items():
            full_key_path = f"{prefix}.{key}" if prefix else key

            if isinstance(value, dict):
                # For nested dictionaries, create a GroupBox and recurse
                group_box = QGroupBox(key)
                group_box.setStyleSheet("QGroupBox { font-weight: bold; }")
                group_layout = QFormLayout(group_box)
                group_layout.setRowWrapPolicy(QFormLayout.WrapAllRows)
                self._build_ui_recursive(value, group_layout, full_key_path)
                parent_layout.addRow(group_box)
            else:
                # For simple values, create a label and an appropriate editor widget
                label = QLabel(f"{key}:")
                widget = None
                original_type = type(value)

                if isinstance(value, bool):
                    widget = QCheckBox()
                    widget.setChecked(value)
                elif isinstance(value, int):
                    widget = QLineEdit(str(value))
                    widget.setValidator(QIntValidator())
                elif isinstance(value, float):
                    widget = QLineEdit(str(value))
                    widget.setValidator(QDoubleValidator())
                elif isinstance(value, str):
                    # Use QTextEdit for multiline strings, otherwise QLineEdit
                    if '\n' in value or len(value) > 80:
                         widget = QTextEdit(value)
                         widget.setAcceptRichText(False)
                         widget.setMinimumHeight(60)
                    else:
                         widget = QLineEdit(value)
                elif value is None:
                    widget = QLineEdit("null")
                    widget.setEnabled(False)
                else: # Fallback for other types like lists
                    widget = QLineEdit(str(value))
                    widget.setToolTip("此数据类型当前不支持直接编辑。")
                    widget.setEnabled(False)

                if widget:
                    parent_layout.addRow(label, widget)
                    # Only map editable widgets
                    if widget.isEnabled():
                        self.widget_map[full_key_path] = (widget, original_type)

    def on_save(self):
        """
        Collects data from the UI, updates the internal data dictionary,
        and saves it back to the JSON file.
        """
        try:
            for full_key, (widget, original_type) in self.widget_map.items():
                new_value = None
                if isinstance(widget, QCheckBox):
                    new_value = widget.isChecked()
                elif isinstance(widget, QTextEdit):
                    new_value = widget.toPlainText()
                elif isinstance(widget, QLineEdit):
                    text_value = widget.text()
                    if original_type is int:
                        new_value = int(text_value)
                    elif original_type is float:
                        new_value = float(text_value)
                    else: # string
                        new_value = text_value
                
                self._set_nested_dict_value(self.data, full_key, new_value)
            
            # Write the updated dictionary back to the file
            with open(self.json_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
            
            QMessageBox.information(self, "成功", "配置文件已成功保存。")
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"保存文件时出错:\n{e}")

    def _set_nested_dict_value(self, data_dict, key_path, value):
        """
        Sets a value in a nested dictionary using a dot-separated key path.
        e.g., _set_nested_dict_value(data, 'ui.theme.color', '#FFF')
        """
        keys = key_path.split('.')
        for key in keys[:-1]:
            data_dict = data_dict.setdefault(key, {})
        data_dict[keys[-1]] = value
# ==============================================================================
# 插件主类 (Plugin Entry Point)
# ==============================================================================
class FileManagerPlugin(BasePlugin):
    """
    文件管理器插件的入口点。
    负责插件的生命周期管理，并创建/显示 FileManagerDialog。
    """
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.dialog = None # 存储对话框实例，实现单例模式
        
        # [核心修正] 预先计算并存储回收站的相关路径，供API方法使用
        self.trash_path = os.path.join(BASE_PATH, ".trash")
        self.trash_metadata_path = os.path.join(self.trash_path, ".metadata.json")

    def setup(self):
        """插件启用时调用。对于独立窗口插件，通常只需返回 True。"""
        print("[File Manager] 插件已准备就绪。")
        return True

    def teardown(self):
        """插件禁用时调用。确保关闭可能存在的对话框，释放资源。"""
        if self.dialog:
            self.dialog.close()
        print("[File Manager] 插件已卸载。")

    def execute(self, **kwargs):
        """
        执行插件。当用户从插件菜单点击时调用。
        采用单例模式，如果对话框已存在则显示，否则创建新的。
        """
        if self.dialog is None:
            self.dialog = FileManagerDialog(self.main_window)
            # 连接对话框的 finished 信号，当对话框关闭时清除引用
            self.dialog.finished.connect(self._on_dialog_finished)
        
        # 显示对话框并将其置于顶层，确保用户能看到它
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def _on_dialog_finished(self):
        """当对话框关闭时，清除对话框实例的引用，以便下次可以重新创建。"""
        self.dialog = None

    # [核心新增] 公共API，供其他模块调用
    def move_to_trash(self, paths_to_delete):
        """
        一个安全的、无UI的公共API，用于将文件或文件夹移动到插件管理的回收站。
        v1.1: 采用 '文件名_时间戳.ext' 的命名格式。
        """
        if not isinstance(paths_to_delete, list):
            paths_to_delete = [paths_to_delete]

        try:
            os.makedirs(self.trash_path, exist_ok=True)
            metadata = self._load_trash_metadata()
            
            for path in paths_to_delete:
                if not os.path.exists(path):
                    continue
                
                # --- [核心] 使用新的、后缀式的命名逻辑 ---
                original_basename, original_ext = os.path.splitext(os.path.basename(path))
                timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
                trash_name = f"{original_basename}_{timestamp}{original_ext}"
                
                # 处理极低概率的重名冲突
                counter = 1
                final_trash_name = trash_name
                while os.path.exists(os.path.join(self.trash_path, final_trash_name)):
                    final_trash_name = f"{original_basename}_{timestamp}_{counter}{original_ext}"
                    counter += 1
                
                destination = os.path.join(self.trash_path, final_trash_name)
                shutil.move(path, destination)
                
                # 记录元数据
                metadata[final_trash_name] = {
                    "original_path": path,
                    "deleted_time": datetime.now().isoformat()
                }

            self._save_trash_metadata(metadata)
            return True, f"成功将 {len(paths_to_delete)} 个项目移动到回收站。"

        except Exception as e:
            error_message = f"移动到回收站时出错: {e}"
            print(f"[File Manager API] {error_message}", file=sys.stderr)
            return False, error_message

    # [核心新增] 为API服务的辅助方法 (从FileManagerDialog中复制并适配)
    def _load_trash_metadata(self):
        if not os.path.exists(self.trash_metadata_path):
            return {}
        try:
            with open(self.trash_metadata_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def _save_trash_metadata(self, metadata):
        try:
            with open(self.trash_metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=4)
        except IOError as e:
            print(f"[File Manager API] 错误: 无法保存回收站元数据: {e}")

class KeyNavigationFilter(QObject):
    def __init__(self, dialog, parent=None):
        super().__init__(parent)
        self.dialog = dialog

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
                if obj is self.dialog.file_view:
                    # [核心修改] 检查表格是否正处于编辑状态
                    if self.dialog.file_view.state() == QAbstractItemView.EditingState:
                        # 如果正在重命名，我们不拦截回车键。
                        # 返回 False 让事件继续传递给默认的编辑器，
                        # 编辑器会处理回车键以提交重命名。
                        return False
                    else:
                        # 如果不在编辑状态，则执行“打开文件”操作，模拟双击。
                        selected = self.dialog.file_view.selectedItems()
                        if selected:
                            self.dialog._on_item_double_clicked(selected[0])
                            return True # 事件已处理

                elif obj is self.dialog.nav_tree:
                    # 在左侧目录树按Enter，展开/折叠节点
                    current_item = self.dialog.nav_tree.currentItem()
                    if current_item:
                        current_item.setExpanded(not current_item.isExpanded())
                        return True # 事件已处理

            elif event.key() == Qt.Key_Right:
                if obj is self.dialog.nav_tree:
                    # 在左侧按右键，切换到右侧
                    self.dialog.file_view.setFocus()
                    if self.dialog.file_view.rowCount() > 0:
                        self.dialog.file_view.selectRow(0)
                    return True # 事件已处理

            elif event.key() == Qt.Key_Left:
                if obj is self.dialog.file_view:
                    # 在右侧按左键，切换回左侧
                    self.dialog.nav_tree.setFocus()
                    return True # 事件已处理

        # 对于其他所有事件，返回False，让它们正常传递
        return super().eventFilter(obj, event)

# ==============================================================================
# 文件管理器主对话框
# ==============================================================================
class FileManagerDialog(QDialog):
    """
    项目文件管理器的主界面对话框。
    提供文件和目录的浏览、管理、回收站功能和拖拽导入。
    """
    def __init__(self, parent=None):
        """
        初始化文件管理器对话框。
        :param parent: 父 QWidget，通常是主窗口实例。
        """
        super().__init__(parent)
        self.main_window = parent  # 保存对主窗口的引用，用于 QSS 样式和跨模块调用
        self.icon_manager = self.main_window.icon_manager # 获取全局图标管理器
        self.plugin_dir = os.path.dirname(__file__) # 插件目录，用于加载自定义图标

        # --- 内部状态变量 ---
        self.trash_path = os.path.join(BASE_PATH, ".trash") # 自定义回收站的物理路径
        self.trash_metadata_path = os.path.join(self.trash_path, ".metadata.json") # 回收站元数据文件路径
        self.trash_policy_config_path = os.path.join(self.trash_path, ".trash_policy.json") # 回收站策略配置文件路径
        
        self.clipboard_paths = [] # 剪贴板中存储的路径列表
        self.clipboard_operation = None # 剪贴板操作类型：'copy' 或 'cut'

        # --- 初始化设置和清理 ---
        self._load_icons() # 加载所有文件和目录图标
        os.makedirs(self.trash_path, exist_ok=True) # 确保回收站目录存在
        self._ensure_trash_policy_exists() # 确保回收站策略配置文件存在（如果不存在则创建默认）
        self._cleanup_trash() # 启动时自动执行回收站清理任务

        # --- UI 初始化 ---
        self.setWindowTitle("项目文件管理器")
        self.setMinimumSize(900, 600)
        self.setAcceptDrops(True) # 允许整个对话框接受文件拖拽（用于导入）
        self._init_ui() # 构建用户界面
        # 1. 创建事件过滤器实例
        self.key_filter = KeyNavigationFilter(self)
        
        # 2. 将过滤器安装到导航树和文件视图上
        self.nav_tree.installEventFilter(self.key_filter)
        self.file_view.installEventFilter(self.key_filter)
        self._connect_signals() # 连接所有UI信号
        self._populate_nav_tree() # 填充左侧导航树

    def _load_icons(self):
        """
        加载所有自定义文件和目录图标。
        图标优先级：插件icons/目录专属 > 插件icons/通用文件类型 > 主程序icons/通用。
        """
        self.icons = {}
        self.dir_icons = {}

        icon_dir = os.path.join(self.plugin_dir, 'icons')

        def load_icon(name):
            for ext in ['.svg', '.png']:
                path = os.path.join(icon_dir, f"{name}{ext}")
                if os.path.exists(path):
                    return QIcon(path)
            return None

        file_icon_map = {
            "audio": [".wav", ".mp3", ".flac", ".ogg"],
            "image": [".png", ".jpg", ".jpeg", ".bmp", ".svg"],
            "json_file": [".json"],
            "excel": [".xlsx", ".xls", ".csv"],
            "text_grid": [".textgrid"],
            "text_file": [".txt", ".md", ".log"],
            "python": [".py"],
            "qss": [".qss"],
            "backup": [".bak", ".zip.bak"],
            # [新增] fdeck 文件图标映射
            "fdeck": [".fdeck"]
        }
        for name, exts in file_icon_map.items():
            icon = load_icon(name)
            if icon:
                for ext in exts:
                    self.icons[ext] = icon
        
        dir_names = [
            "Results", "word_lists", "plugins", "themes", "assets", "config",
            "modules", "flashcards", "dialect_visual_wordlists", "PhonAcq_Archives",
            "audio_tts", "audio_record"
        ]
        for name in dir_names:
            icon = load_icon(name)
            if icon:
                self.dir_icons[name] = icon

        self.icons['folder'] = load_icon('folder') or self.icon_manager.get_icon("folder")
        self.icons['trash'] = load_icon('trash') or self.icon_manager.get_icon("delete")
        self.generic_file_icon = load_icon('unknown') or self.icon_manager.get_icon("file")

    def _init_ui(self):
        """构建对话框的用户界面布局。"""
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal) # 创建水平分割器

        # --- 左侧面板：导航树 (QTreeWidget) ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(QLabel("项目目录:"))
        self.nav_tree = QTreeWidget()
        self.nav_tree.setHeaderHidden(True) # 隐藏表头，只显示项目名称
        left_layout.addWidget(self.nav_tree)
        
        # --- 右侧面板：文件视图 (QTableWidget) ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        self.current_path_label = QLabel("请选择一个目录") # 显示当前路径
        self.current_path_label.setObjectName("BreadcrumbLabel") # 用于QSS样式
        right_layout.addWidget(self.current_path_label)
        
        # 搜索栏
        search_layout = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("在此处搜索当前目录中的文件...")
        self.search_bar.setClearButtonEnabled(True) # 添加清除按钮
        search_layout.addWidget(QLabel("搜索:"))
        search_layout.addWidget(self.search_bar)
        
        right_layout.addLayout(search_layout) # 添加搜索栏
        
        self.file_view = QTableWidget()
        self.file_view.setColumnCount(3)
        self.file_view.setHorizontalHeaderLabels(["名称", "大小", "修改日期"])
        self.file_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch) # 名称列拉伸
        self.file_view.setSelectionBehavior(QTableWidget.SelectRows) # 每次选中整行
        self.file_view.setEditTriggers(QTableWidget.NoEditTriggers) # 禁止直接编辑，重命名通过右键菜单
        self.file_view.setShowGrid(False) # 隐藏网格线
        self.file_view.verticalHeader().setVisible(False) # 隐藏垂直表头（行号）
        right_layout.addWidget(self.file_view)

        # 将左右面板添加到分割器
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([280, 620]) # 设置初始分割比例
        layout.addWidget(splitter) # 将分割器添加到主布局

    def _connect_signals(self):
        """连接所有UI控件的信号与槽。"""
        self.nav_tree.currentItemChanged.connect(self._on_nav_item_selected) # 左侧导航树选择改变
        # [核心新增] 连接左侧导航树的右键菜单请求信号
        self.nav_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.nav_tree.customContextMenuRequested.connect(self._show_nav_tree_context_menu)
        
        # 右侧文件视图的右键菜单
        self.file_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_view.customContextMenuRequested.connect(self._show_context_menu)
        
        self.file_view.itemDoubleClicked.connect(self._on_item_double_clicked) # 双击文件/文件夹
        self.file_view.itemChanged.connect(self._on_item_renamed) # 项目重命名完成

        # 实时搜索
        self.search_bar.textChanged.connect(self._filter_file_view)

        # 快捷键
        QShortcut(QKeySequence.Copy, self, self._copy_items_from_selection)
        QShortcut(QKeySequence.Cut, self, self._cut_items_from_selection)
        QShortcut(QKeySequence.Paste, self, self._paste_items)

    def _populate_nav_tree(self):
        """
        动态扫描并构建左侧导航树的完整目录结构。
        它会递归地添加所有子文件夹，并应用名称汉化、专属图标和优先级排序。
        """
        self.nav_tree.clear() # 清空现有树
        
        # 优先级排序：数字越小，越靠前
        dir_priorities = {
            # 首要用户数据区
            "Results": 10,
            "word_lists": 11,
            "dialect_visual_wordlists": 12,
            "flashcards": 13,
            "PhonAcq_Archives": 14,
            # 次要/缓存数据区
            "audio_record": 20,
            "audio_tts": 21,
            # 用户配置与扩展区
            "config": 30,
            "themes": 31,
            "plugins": 32,
            # 程序核心/静态资源区
            "modules": 90,
            "assets": 91,
        }

        # 中文别名：使用更具描述性、长短不一的名称
        dir_aliases = {
            "assets": "静态资源",
            "audio_record": "用户提示音库",
            "audio_tts": "TTS提示音",
            "config": "程序配置",
            "dialect_visual_wordlists": "图文词表",
            "flashcards": "速记卡管理",
            "modules": "核心模块",
            "PhonAcq_Archives": "项目档案库",
            "plugins": "扩展插件",
            "Results": "采集结果",
            "themes": "主题皮肤",
            "word_lists": "标准词表",
        }
        
        # 工具提示：为每个目录提供详细说明
        dir_tooltips = {
            "Results": "所有采集任务（标准朗读、看图说话等）的音频和日志文件默认保存在这里。",
            "word_lists": "存放所有标准朗读任务使用的 .json 词表文件。",
            "dialect_visual_wordlists": "存放所有“看图说话”任务使用的图文 .json 词表及其关联图片。",
            "flashcards": "存放“速记卡”模块创建的学习卡片数据。",
            "PhonAcq_Archives": "用于存放通过“档案库”插件打包或解包的项目档案。",
            "audio_record": "存放由“提示音录制”模块生成的真人发音 .wav 或 .mp3 文件。",
            "audio_tts": "存放由程序自动生成的 TTS (文本转语音) 音频缓存文件。",
            "config": "存放程序的核心配置文件，如 settings.json。",
            "themes": "存放所有 QSS 主题皮肤文件和主题专属图标。",
            "plugins": "存放所有已安装的外部插件。每个子文件夹代表一个插件。",
            "modules": "存放程序的核心功能模块 .py 文件。",
            "assets": "存放程序的通用静态资源，如默认图标、帮助文档等。",
        }

        # 定义需要排除的顶级文件夹（开发/构建/临时文件等）
        exclude_dirs = {'.git', '.idea', '.vscode', '__pycache__', 'build', 'dist', '.trash'}
        
        try:
            # 获取项目根目录下的所有文件夹
            all_root_dirs = (d for d in os.listdir(BASE_PATH) if os.path.isdir(os.path.join(BASE_PATH, d)))
            # 过滤掉排除项，并根据优先级和别名进行排序
            sorted_dirs = sorted(
                (d for d in all_root_dirs if d not in exclude_dirs),
                key=lambda d: (dir_priorities.get(d, 99), dir_aliases.get(d, d)) # 未定义优先级的排在最后
            )

            # 遍历并添加顶级文件夹到树中
            for name in sorted_dirs:
                full_path = os.path.join(BASE_PATH, name)
                display_name = dir_aliases.get(name, name) # 获取中文显示名
                
                item = QTreeWidgetItem(self.nav_tree, [display_name])
                item.setIcon(0, self.dir_icons.get(name, self.icons.get("folder"))) # 使用目录专属图标或通用图标
                item.setData(0, Qt.UserRole, full_path) # 将完整路径存储在UserData中
                
                # 设置工具提示
                tooltip = dir_tooltips.get(name)
                if tooltip:
                    item.setToolTip(0, tooltip)

                # 递归添加子文件夹
                self._add_subfolders_recursively(item, full_path)
        
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法扫描项目根目录: {e}")
        
        # 单独添加回收站条目
        trash_item = QTreeWidgetItem(self.nav_tree, ["回收站 (.trash)"])
        trash_item.setIcon(0, self.icons.get("trash"))
        trash_item.setData(0, Qt.UserRole, self.trash_path)
        trash_item.setData(0, Qt.UserRole + 1, "trash_item")
        trash_item.setToolTip(0, "所有被删除的文件和文件夹都会临时存放在这里。")
        
        # 优化默认选中和展开行为
        if self.nav_tree.topLevelItemCount() > 0:
            # 默认选中并展开“采集结果”文件夹（如果存在）
            results_item = self.nav_tree.findItems("采集结果", Qt.MatchExactly, 0)
            if results_item:
                results_item[0].setExpanded(True)
                self.nav_tree.setCurrentItem(results_item[0])
            else:
                # 否则，选中并展开第一个顶级文件夹
                first_item = self.nav_tree.topLevelItem(0)
                if first_item: # 再次检查以防列表为空
                    first_item.setExpanded(True)
                    self.nav_tree.setCurrentItem(first_item)

    def _add_subfolders_recursively(self, parent_item, parent_path):
        """
        递归辅助函数：扫描指定路径下的子文件夹，并将其添加到QTreeWidget中。
        同时处理插件目录的特殊图标显示。
        """
        # 检查当前父路径是否是 "plugins" 目录
        is_plugins_dir = os.path.basename(parent_path) == "plugins"
        
        folder_icon = self.icons.get("folder") # 通用文件夹图标

        try:
            for name in sorted(os.listdir(parent_path)):
                full_path = os.path.join(parent_path, name)
                # 只处理文件夹，并排除Python的__pycache__等临时文件夹
                if os.path.isdir(full_path) and not name.startswith('__'):
                    
                    display_name = name # 默认显示文件夹原名
                    icon = folder_icon # 默认使用通用文件夹图标

                    if is_plugins_dir:
                        # 如果是插件目录，尝试获取插件的元信息
                        plugin_meta = self._get_plugin_meta(full_path)
                        if plugin_meta:
                            # 如果成功获取，则使用json中定义的名称和图标
                            display_name = plugin_meta.get("name", name)
                            icon = plugin_meta.get("icon_qicon", icon)
                    
                    child_item = QTreeWidgetItem(parent_item, [display_name])
                    child_item.setIcon(0, icon)
                    
                    child_item.setData(0, Qt.UserRole, full_path) # 存储完整路径
                    # 递归调用自身，继续深入下一层目录
                    self._add_subfolders_recursively(child_item, full_path)
        except OSError:
            # 忽略因权限或其他问题无法访问的目录，不中断程序
            pass

    def _get_plugin_meta(self, plugin_path):
        """
        辅助函数：解析指定插件路径下的 plugin.json 文件。
        返回一个包含 'name' 和 'icon_qicon' (QIcon对象) 的字典。
        """
        manifest_path = os.path.join(plugin_path, "plugin.json")
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                
                # 准备返回结果
                result = {"name": meta.get("name")}
                
                # 处理图标
                icon_rel_path = meta.get("icon")
                if icon_rel_path:
                    icon_full_path = os.path.join(plugin_path, icon_rel_path)
                    if os.path.exists(icon_full_path):
                        result["icon_qicon"] = QIcon(icon_full_path)
                
                return result
            except (json.JSONDecodeError, IOError) as e:
                # 打印错误，但不要中断插件加载
                print(f"[File Manager] Error reading plugin manifest '{manifest_path}': {e}")
        return None # 未找到或加载失败则返回None

    def _on_nav_item_selected(self, current, previous):
        """
        当用户在左侧导航树中选择一个不同的文件夹时，刷新右侧文件视图。
        """
        if current:
            # 获取选中文件夹的完整路径
            path = current.data(0, Qt.UserRole)
            self._populate_file_view(path)
            self.search_bar.clear() # 切换目录时清空搜索框

    def _populate_file_view(self, dir_path):
        """
        [v1.1 - 回收站感知版]
        填充右侧文件视图。
        如果当前目录是回收站，则将第三列显示为“删除日期”，并从元数据中获取时间。
        """
        self.current_path_label.setText(f"正在加载: {os.path.relpath(dir_path, BASE_PATH)}...")
        self.file_view.setRowCount(0)
        self.file_view.setSortingEnabled(False)
        QApplication.processEvents()

        is_in_trash = os.path.realpath(dir_path) == os.path.realpath(self.trash_path)
        trash_metadata = {} # 默认为空字典

        # --- [核心修改 1/3] ---
        # 如果在回收站中，提前加载元数据并修改表头
        if is_in_trash:
            trash_metadata = self._load_trash_metadata()
            self.file_view.setHorizontalHeaderLabels(["名称 (原始路径)", "大小", "删除日期"])
        else:
            self.file_view.setHorizontalHeaderLabels(["名称", "大小", "修改日期"])
        
        try:
            # --- 手动处理回收站策略文件的逻辑 (保持不变) ---
            if is_in_trash and os.path.exists(self.trash_policy_config_path):
                self.file_view.insertRow(0)
                policy_path = self.trash_policy_config_path
                policy_name = os.path.basename(policy_path)
                
                name_item = QTableWidgetItem(policy_name)
                name_item.setIcon(self.main_window.icon_manager.get_icon("settings"))
                name_item.setData(Qt.UserRole, policy_path)
                name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable) 
                name_item.setToolTip("双击以配置回收站自动清理策略。")
                self.file_view.setItem(0, 0, name_item)
                
                try:
                    stat = os.stat(policy_path)
                    size_item = NumericTableWidgetItem()
                    size_item.setData(Qt.UserRole, stat.st_size)
                    size_item.setText(f"{stat.st_size} B")
                    self.file_view.setItem(0, 1, size_item)
                    # 对于策略文件，仍然显示修改日期
                    self.file_view.setItem(0, 2, QTableWidgetItem(datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')))
                except FileNotFoundError:
                    self.file_view.setItem(0, 1, QTableWidgetItem("N/A")); self.file_view.setItem(0, 2, QTableWidgetItem("N/A"))

            # --- 正常列出目录中的其他文件 ---
            items = os.listdir(dir_path)
            for name in sorted(items, key=lambda x: (not os.path.isdir(os.path.join(dir_path, x)), x.lower())):
                if is_in_trash and name in {".metadata.json", ".trash_policy.json"}:
                    continue
                
                full_path = os.path.join(dir_path, name)
                row = self.file_view.rowCount(); self.file_view.insertRow(row)
                name_item = QTableWidgetItem(name); name_item.setIcon(self._get_icon_for_path(full_path))
                name_item.setData(Qt.UserRole, full_path); name_item.setFlags(name_item.flags() | Qt.ItemIsEditable)
                name_item.setToolTip(self._generate_tooltip_for_item(full_path))
                
                # --- [核心修改 2/3] ---
                # 在回收站中，Tooltip 显示原始路径
                if is_in_trash and name in trash_metadata:
                    original_path = trash_metadata[name].get("original_path", "未知")
                    name_item.setToolTip(f"原始路径: {original_path}")

                self.file_view.setItem(row, 0, name_item)
                
                try:
                    stat = os.stat(full_path)
                    # 文件大小列的逻辑 (保持不变)
                    if os.path.isdir(full_path):
                        size_item = QTableWidgetItem("--"); size_item.setTextAlignment(Qt.AlignCenter)
                        size_item.setData(Qt.UserRole, -1)
                    else:
                        size = stat.st_size
                        if size > 1024 * 1024 * 1024: size_str = f"{size / (1024*1024*1024):.1f} GB"
                        elif size > 1024 * 1024: size_str = f"{size / (1024*1024):.1f} MB"
                        elif size > 1024: size_str = f"{size / 1024:.1f} KB"
                        else: size_str = f"{size} B"
                        size_item = NumericTableWidgetItem(); size_item.setData(Qt.UserRole, size); size_item.setText(size_str)
                    self.file_view.setItem(row, 1, size_item)

                    # --- [核心修改 3/3] ---
                    # 日期列的逻辑
                    if is_in_trash and name in trash_metadata:
                        # 如果在回收站中，从元数据获取删除日期
                        deleted_time_str = trash_metadata[name].get("deleted_time", "")
                        try:
                            # 解析ISO格式的日期并重新格式化
                            dt_obj = datetime.fromisoformat(deleted_time_str)
                            display_time = dt_obj.strftime('%Y-%m-%d %H:%M')
                        except (ValueError, TypeError):
                            display_time = "N/A" # 如果日期格式无效
                        self.file_view.setItem(row, 2, QTableWidgetItem(display_time))
                    else:
                        # 否则，显示文件的最后修改日期
                        self.file_view.setItem(row, 2, QTableWidgetItem(datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')))

                except FileNotFoundError:
                     self.file_view.setItem(row, 1, QTableWidgetItem("N/A")); self.file_view.setItem(row, 2, QTableWidgetItem("N/A"))
        except Exception as e: 
            QMessageBox.critical(self, "错误", f"无法读取目录 '{dir_path}':\n{e}")
        
        self.current_path_label.setText(f"当前: {os.path.relpath(dir_path, BASE_PATH)}")
        self.file_view.setSortingEnabled(True)

    def _filter_file_view(self, text):
        """
        根据搜索栏的文本实时过滤文件视图中的项目。
        不区分大小写匹配，隐藏不匹配的行。
        """
        search_text = text.lower()
        for row in range(self.file_view.rowCount()):
            item = self.file_view.item(row, 0) # 获取名称列的 QTableWidgetItem
            if item:
                is_visible = search_text in item.text().lower() # 判断是否匹配
                self.file_view.setRowHidden(row, not is_visible) # 隐藏或显示行

    def _handle_open_action(self):
        """
        处理右键菜单中的“打开”操作。
        根据选中的第一个项目的类型，执行不同的动作。
        """
        selected_paths = self._get_selected_paths()
        if not selected_paths:
            return

        # “打开”操作总是只针对选中的第一个项目
        path = selected_paths[0]

        if os.path.isdir(path):
            # 如果是文件夹，则执行与双击相同的内部导航逻辑
            self._sync_nav_tree_to_path(path)
        else:
            # 如果是文件，则使用系统默认程序打开
            self._open_items([path])

    def _get_icon_for_path(self, path):
        """
        根据文件/文件夹的类型或特定文件名返回对应的 QIcon。
        增加了对双重扩展名 .zip.bak 的特殊处理。
        """
        filename = os.path.basename(path)

        if filename in (".trash_policy.json", "config.json", "settings.json"):
            return self.main_window.icon_manager.get_icon("settings")

        if os.path.isdir(path):
            return self.dir_icons.get(filename, self.icons.get("folder"))
        
        # [核心修复] 对双重扩展名的特殊处理
        # 检查文件名是否以 .zip.bak 结尾
        if filename.lower().endswith('.zip.bak'):
            return self.icons.get('.zip.bak', self.generic_file_icon)

        # 常规的单扩展名处理
        ext = os.path.splitext(path)[1].lower()
        return self.icons.get(ext, self.generic_file_icon)

    def _generate_tooltip_for_item(self, path):
        file_type = self._get_file_type(path)

        if file_type == 'audio':
            if AUDIO_LIBS_AVAILABLE:
                return self._tooltip_for_audio(path)
            else:
                return self._tooltip_for_metadata(path)        
        elif file_type == 'text':
            return self._tooltip_for_text(path)
        elif file_type == 'image':
            return self._tooltip_for_image(path)
        elif file_type == 'wordlist':
            return self._tooltip_for_wordlist(path)
        # [新增] 为 fdeck 文件调用专属的 tooltip 生成器
        elif file_type == 'fdeck':
            return self._tooltip_for_fdeck(path)
        elif file_type == 'file':
            return self._tooltip_for_metadata(path)
        
        return ""

    def _tooltip_for_fdeck(self, path):
        """
        [新增] 为 .fdeck 文件生成一个包含其内部元数据摘要的 Tooltip。
        """
        try:
            import zipfile
            # 从 zip 包中直接读取 manifest.json，而不解压整个文件
            with zipfile.ZipFile(path, 'r') as zf:
                if 'manifest.json' in zf.namelist():
                    with zf.open('manifest.json') as manifest_file:
                        data = json.load(manifest_file)
                else:
                    return f"<b>{os.path.basename(path)}</b><hr>[无效的卡组包: 缺少 manifest.json]"

            meta = data.get('meta', {})
            cards = data.get('cards', [])
            
            # 从元数据中提取信息
            name = meta.get('deck_name', 'N/A')
            author = meta.get('author', '未知')
            description = meta.get('description', '无描述。').replace('\n', '<br>')
            card_count = len(cards)
            
            # 使用 _tooltip_for_metadata 的基础结构来显示通用文件信息
            base_tooltip = self._tooltip_for_metadata(path)
            
            # 将基础信息和 fdeck 专属信息组合起来
            fdeck_info = f"""
            <hr style='border: none; border-top: 1px solid #ddd;'>
            <table style='max-width:300px;'>
                <tr><td style='vertical-align:top; padding-right:8px; white-space:nowrap;'><b>卡组名称:</b></td><td style='word-wrap:break-word;'>{name}</td></tr>
                <tr><td style='vertical-align:top; padding-right:8px; white-space:nowrap;'><b>卡片数量:</b></td><td style='word-wrap:break-word;'>{card_count}</td></tr>
                <tr><td style='vertical-align:top; padding-right:8px; white-space:nowrap;'><b>描述:</b></td><td style='word-wrap:break-word;'>{textwrap.fill(description, 40)}</td></tr>
            </table>
            """
            
            return base_tooltip + fdeck_info

        except Exception as e:
            return f"<b>{os.path.basename(path)}</b><hr>[无法预览卡组包: {e}]"

    def _get_file_type(self, path):
        if os.path.isdir(path): return 'folder'
        
        ext = os.path.splitext(path)[1].lower()
        audio_exts = {".wav", ".mp3", ".flac", ".ogg"}
        text_exts = {".txt", ".md", ".log", ".py", ".qss", ".csv", ".textgrid"}
        image_exts = {".png", ".jpg", ".jpeg", ".bmp", ".svg"}

        if ext in audio_exts: return 'audio'
        if ext in text_exts: return 'text'
        if ext in image_exts: return 'image'
        # [新增] 将 .fdeck 识别为一种特殊类型
        if ext == '.fdeck': return 'fdeck'
        
        if ext == '.json':
            try:
                with open(path, 'r', encoding='utf-8') as f: data = json.load(f)
                if 'meta' in data and 'format' in data['meta']:
                    if data['meta']['format'] in ["standard_wordlist", "visual_wordlist"]:
                        return 'wordlist'
            except (json.JSONDecodeError, IOError):
                pass
            return 'text'

        return 'file'

    def _tooltip_for_text(self, path, max_lines=15, max_chars_total=800, wrap_width=80):
        """
        为文本文件生成 Tooltip 预览，支持智能换行和截断提示。
        Tooltip 的宽度由 max-width 控制，内容会在此宽度内自动换行。
        """
        preview_lines = []
        total_chars = 0
        truncated = False
        
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                for i, line in enumerate(f):
                    if i >= max_lines: # 超过最大行数则截断
                        truncated = True
                        break
                    
                    # 使用 textwrap 对单行文本进行智能换行
                    wrapped_lines = textwrap.wrap(line.strip(), width=wrap_width)
                    if not wrapped_lines: wrapped_lines = [''] # 保留空行

                    for wrapped_line in wrapped_lines:
                        if total_chars + len(wrapped_line) > max_chars_total: # 超过总字符数则截断
                            remaining_chars = max_chars_total - total_chars
                            preview_lines.append(wrapped_line[:remaining_chars])
                            truncated = True
                            break
                        else:
                            preview_lines.append(wrapped_line)
                            total_chars += len(wrapped_line)
                    if truncated: break
            
            content = "\n".join(preview_lines)
            
            # 使用 HTML 和 CSS 来强制 Tooltips 内部内容的换行和最大宽度
            # pre-wrap 会保留空白符和换行符，word-wrap 确保长单词不会溢出
            html_content = content.replace('&', '&').replace('<', '<').replace('>', '>')
            tooltip_html = f"<div style='max-width: 300px; white-space: pre-wrap; word-wrap: break-word;'>{html_content}</div>"
            
            if truncated:
                tooltip_html += "<b style='color:red;'>...</b>" # 红色省略号表示内容被截断
            
            return tooltip_html if content else "[空文件]"
        except Exception:
            return "[无法预览]" # 读写文件出错时

    def _format_time(self, ms):
        """[新增] 格式化毫秒为 MM:SS.CS 字符串。从 audio_manager 模块借鉴而来。"""
        if ms <= 0: return "00:00.00"
        total_seconds = ms / 1000.0
        m, s_frac = divmod(total_seconds, 60)
        s_int = int(s_frac)
        cs = int(round((s_frac - s_int) * 100))
        if cs == 100: cs = 0; s_int +=1
        if s_int == 60: s_int = 0; m += 1
        return f"{int(m):02d}:{s_int:02d}.{cs:02d}"

    def _tooltip_for_audio(self, path):
        """
        [新增] 为音频文件生成包含元数据和波形预览的Tooltip。
        """
        try:
            # --- 1. 读取音频数据和元信息 ---
            info = sf.info(path)
            duration_ms = info.duration * 1000
            
            # =================== [核心修正] ===================
            # 不再使用依赖新版本 soundfile 的复杂计算方式。
            # 直接使用 os.stat() 获取文件在磁盘上的实际大小，这更简单、更健壮。
            size_bytes = os.stat(path).st_size
            size_str = f"{size_bytes / 1024:.1f} KB" if size_bytes > 1024 else f"{size_bytes} B"
            # ================================================

            # --- 2. 创建一个离屏的 QPixmap 用于绘制 (保持不变) ---
            WAVEFORM_WIDTH = 280
            WAVEFORM_HEIGHT = 60
            pixmap = QPixmap(WAVEFORM_WIDTH, WAVEFORM_HEIGHT)
            pixmap.fill(Qt.transparent)

            # --- 3. 使用 QPainter 绘制波形 (保持不变) ---
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            
            data, sr = sf.read(path, dtype='float32', stop=int(info.samplerate * 5))
            if data.ndim > 1: data = data.mean(axis=1)
            
            num_samples = len(data)
            target_points = WAVEFORM_WIDTH
            if num_samples > target_points:
                step = num_samples // target_points
                peak_data = [np.max(np.abs(data[i:i+step])) for i in range(0, num_samples, step)]
                waveform_data = np.array(peak_data)
            else:
                waveform_data = data

            h, w = WAVEFORM_HEIGHT, WAVEFORM_WIDTH
            half_h = h / 2
            max_val = np.max(waveform_data) if len(waveform_data) > 0 else 1.0
            if max_val == 0: max_val = 1.0
            
            pen_color = QColor("#5D90C3") 
            painter.setPen(QPen(pen_color, 1))

            for i, val in enumerate(waveform_data):
                x = int(i * w / target_points)
                y_offset = (val / max_val) * half_h
                painter.drawLine(x, int(half_h - y_offset), x, int(half_h + y_offset))
            
            painter.end()

            # --- 4. 将 QPixmap 转换为 Base64 (保持不变) ---
            byte_array = QByteArray()
            buffer = QBuffer(byte_array)
            buffer.open(QBuffer.WriteOnly)
            pixmap.save(buffer, "PNG")
            base64_data = byte_array.toBase64().data().decode()
            uri = f"data:image/png;base64,{base64_data}"

            # --- 5. 构建最终的 HTML Tooltip (保持不变) ---
            html = f"""
            <div style='max-width: 300px;'>
                <b>{os.path.basename(path)}</b><br>
                时长: {self._format_time(duration_ms)} | {info.samplerate} Hz | {size_str}
                <hr>
                <img src='{uri}' width='{WAVEFORM_WIDTH}' height='{WAVEFORM_HEIGHT}'>
            </div>
            """
            return html

        except Exception as e:
            print(f"[File Manager] Tooltip for audio failed for '{path}': {e}")
            return self._tooltip_for_metadata(path)

    def _tooltip_for_image(self, path):
        """
        为图片文件生成高质量的缩略图 Tooltip。
        通过在 Python 中预先使用 Qt.SmoothTransformation 缩放图片，解决锯齿问题。
        """
        try:
            # 1. 加载原始图片到 QPixmap
            pixmap = QPixmap(path)
            if pixmap.isNull():
                return f"[无法加载图片: {os.path.basename(path)}]"

            # 2. 使用高质量算法，将图片平滑地缩放到适合 Tooltip 的宽度
            # scaledToWidth 会保持原始图片的宽高比
            scaled_pixmap = pixmap.scaledToWidth(250, Qt.SmoothTransformation)

            # 3. 将高质量的缩略图转换为 Base64 Data URI
            byte_array = QByteArray()
            buffer = QBuffer(byte_array)
            buffer.open(QBuffer.WriteOnly)
            scaled_pixmap.save(buffer, "PNG") # 保存为 PNG 格式以支持透明度
            base64_data = byte_array.toBase64().data().decode()
            uri = f"data:image/png;base64,{base64_data}"

            # 4. 获取文件元信息
            stat = os.stat(path)
            size_str = f"{stat.st_size / 1024:.1f} KB" if stat.st_size > 1024 else f"{stat.st_size} B"
            
            # 5. 构建最终的 HTML，现在 <img> 标签引用的已经是高质量的内嵌图片数据
            html = f"""
            <div style='max-width: 300px;'>
                <b>{os.path.basename(path)}</b> ({pixmap.width()}x{pixmap.height()}, {size_str})<hr>
                <img src='{uri}'>
            </div>
            """
            return html
        except Exception as e:
            return f"[图片预览失败: {e}]"

    def _tooltip_for_metadata(self, path, is_wordlist=False):
        """
        为非文本/非图片文件生成 Tooltip，显示基本元信息。
        对于 JSON 词表，会进一步解析其元数据。
        """
        try:
            stat = os.stat(path); size = stat.st_size
            size_str = f"{size / 1024:.1f} KB" if size > 1024 else f"{size} B"
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            ext = os.path.splitext(path)[1].lower()
            
            rows = [
                ("<b>类型:</b>", f"{ext[1:].upper() if ext else '文件'}"),
                ("<b>大小:</b>", size_str),
                ("<b>修改日期:</b>", mtime)
            ]
            
            # 如果是词表，尝试解析并添加词表特有的元数据
            if is_wordlist:
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    meta = data.get('meta', {})
                    
                    # 定义词表元数据键的中文映射
                    key_map = {
                        'name': '词表名称', 'description': '描述',
                        'author': '作者', 'version': '版本',
                        'save_date': '保存日期', 'format': '格式'
                    }
                    
                    has_wordlist_meta = False
                    for key, label in key_map.items():
                        if meta.get(key) is not None: # 使用 is not None 来检查，避免空字符串等
                            has_wordlist_meta = True
                            value = meta[key]
                            
                            # 特殊处理日期格式
                            if key == 'save_date':
                                # 移除微秒并替换 T，使其更易读
                                value = value.split('.')[0].replace('T', ' ')
                            
                            # 特殊处理描述的换行
                            if key == 'description':
                                # textwrap.fill 会自动处理长文本的换行
                                value = textwrap.fill(str(value), width=60) # 强制描述在60字符处换行
                            
                            rows.append((f"<b>{label}:</b>", str(value)))
                    
                    # 如果有词表特有元数据，插入一个分隔线
                    if has_wordlist_meta:
                        rows.insert(3, (None, None)) # None,None 作为分隔符的标记

                except (json.JSONDecodeError, IOError):
                    pass # 如果不是合法的词表 JSON，则忽略词表元数据部分

            # 构建 HTML 表格来格式化 Tooltip 内容
            # max-width 控制 Tooltip 的总宽度
            # word-wrap:break-word 确保内容在单元格内换行
            # white-space:nowrap 确保左侧的键不会自己换行
            html = f"<b>{os.path.basename(path)}</b><hr><table style='max-width:300px;'>"
            for key, val in rows:
                if key is None:
                    # 分隔线的 HTML，确保其样式在表格内正确显示
                    html += "<tr><td colspan='2' style='padding-top:3px; padding-bottom:3px;'><hr style='border: none; border-top: 1px solid #ddd;'></td></tr>"
                else:
                    html += f"<tr><td style='vertical-align:top; padding-right:8px; white-space:nowrap;'>{key}</td><td style='word-wrap:break-word;'>{val}</td></tr>"
            html += "</table>"
            return html.strip()

        except Exception:
            # 出现任何错误时，回退到只显示文件名
            return os.path.basename(path)

    def _tooltip_for_wordlist(self, path):
        """专门为词表文件调用元数据 Tooltip 生成器。"""
        return self._tooltip_for_metadata(path, is_wordlist=True)

    def _show_context_menu(self, position):
        menu = QMenu(self.file_view)
        menu.setStyleSheet(self.main_window.styleSheet())
        
        selected_paths = self._get_selected_paths()
        current_dir = self._get_current_dir()
        is_in_trash = current_dir == self.trash_path

        if selected_paths:
            is_single_selection = len(selected_paths) == 1
            first_path = selected_paths[0]
            is_folder = os.path.isdir(first_path)

            # [关键修复 2] 检查是否选中了单个备份文件
            is_single_backup_file = is_single_selection and \
                                    not is_folder and \
                                    (first_path.lower().endswith('.bak'))

            if is_in_trash:
                menu.addAction(self.icon_manager.get_icon("replace"), "恢复", lambda: self._restore_items(selected_paths))
                menu.addAction(self.icon_manager.get_icon("delete"), "永久删除", lambda: self._delete_items(selected_paths))
            
            elif is_single_backup_file:
                # --- [新增] 备份文件专属菜单 ---
                menu.addAction(self.icon_manager.get_icon("replace"), "从备份恢复", lambda: self._restore_from_backup(first_path))
                menu.addSeparator()
                menu.addAction(self.icon_manager.get_icon("delete"), "删除备份", lambda: self._delete_items(selected_paths))
            
            else:
                # --- 常规文件/文件夹菜单 ---
                open_action = menu.addAction(self.icon_manager.get_icon("open_external"), "打开")
                open_action.triggered.connect(self._handle_open_action)
                open_action.setEnabled(is_single_selection)

                if is_single_selection and not is_folder:
                    menu.addAction(self.icon_manager.get_icon("open_folder"), "打开所在文件夹", lambda: self._open_containing_folder(selected_paths))
                
                menu.addSeparator()
                backup_action = menu.addAction(self.icon_manager.get_icon("backup"), "创建备份")
                backup_action.triggered.connect(lambda: self._backup_items(selected_paths))
                
                if is_single_selection:
                    menu.addAction(self.icon_manager.get_icon("rename"), "重命名", self._rename_item)
                
                menu.addSeparator()
                menu.addAction(self.icon_manager.get_icon("copy"), "复制 (Ctrl+C)", lambda: self._copy_items(selected_paths))
                menu.addAction(self.icon_manager.get_icon("cut"), "剪切 (Ctrl+X)", lambda: self._cut_items(selected_paths))

        if not is_in_trash:
            if menu.actions():
                menu.addSeparator()
            
            menu.addAction(self.icon_manager.get_icon("add_folder"), "新建文件夹", self._create_new_folder)
            paste_action = menu.addAction(self.icon_manager.get_icon("paste"), "粘贴 (Ctrl+V)")
            paste_action.setEnabled(bool(self.clipboard_paths))
            paste_action.triggered.connect(self._paste_items)

        if selected_paths and not is_in_trash and not is_single_backup_file:
            menu.addSeparator()
            menu.addAction(self.icon_manager.get_icon("delete"), "删除到回收站", lambda: self._delete_items(selected_paths))
            
        menu.exec_(self.file_view.viewport().mapToGlobal(position))

    def _restore_from_backup(self, backup_path):
        """
        [新增] 从一个 .bak 文件恢复。
        - 移除 .bak 后缀得到原始文件名。
        - 弹窗确认覆盖。
        - 复制备份文件并重命名，以覆盖原始文件。
        - 对于 .zip.bak，先解压到临时位置，再移动/合并内容。
        """
        try:
            # 1. 确定原始文件/文件夹的路径
            if backup_path.lower().endswith('.zip.bak'):
                original_path = backup_path[:-8] # 移除 .zip.bak
                is_folder_backup = True
            else:
                original_path = backup_path[:-4] # 移除 .bak
                is_folder_backup = False

            # 2. 弹窗确认
            item_type = "文件夹" if is_folder_backup or os.path.isdir(original_path) else "文件"
            reply = QMessageBox.question(self, "确认恢复",
                                         f"您确定要从备份恢复 '{os.path.basename(backup_path)}' 吗？\n\n"
                                         f"这将覆盖现有的{item_type}：\n{os.path.basename(original_path)}>",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

            # 3. 执行恢复操作
            if is_folder_backup:
                # --- 恢复文件夹 ---
                temp_extract_dir = os.path.join(self.trash_path, f"~restore_{uuid.uuid4().hex}")
                shutil.unpack_archive(backup_path, temp_extract_dir, 'zip')
                
                # 解压后的内容在 temp_extract_dir/original_name/ 下
                source_content_dir = os.path.join(temp_extract_dir, os.path.basename(original_path))
                
                if os.path.exists(original_path):
                    # 合并内容
                    shutil.copytree(source_content_dir, original_path, dirs_exist_ok=True)
                else:
                    # 直接移动
                    shutil.move(source_content_dir, original_path)
                
                shutil.rmtree(temp_extract_dir) # 清理临时解压目录
            else:
                # --- 恢复文件 ---
                shutil.copy2(backup_path, original_path)

            # 4. 刷新UI
            self._populate_file_view(self._get_current_dir())
            if is_folder_backup:
                self._populate_nav_tree()
                self._sync_nav_tree_to_path(self._get_current_dir())

        except Exception as e:
            QMessageBox.critical(self, "恢复失败", f"从备份恢复时出错：\n{e}")

    def _backup_items(self, paths):
        """
        [新增] 为选中的文件或文件夹创建备份。
        - 对于文件，创建 .bak 后缀的副本。
        - 对于文件夹，先压缩为 .zip，再重命名为 .zip.bak。
        """
        current_dir = self._get_current_dir()
        if not current_dir: return

        for src_path in paths:
            try:
                if os.path.isfile(src_path):
                    # --- 文件备份逻辑 ---
                    dest_path = f"{src_path}.bak"
                    shutil.copy2(src_path, dest_path)

                elif os.path.isdir(src_path):
                    # --- 文件夹备份逻辑 ---
                    dir_name = os.path.basename(src_path)
                    # 1. 确定临时的 zip 文件名 (不带 .bak)
                    zip_path_temp = os.path.join(os.path.dirname(src_path), dir_name)
                    # 2. 将文件夹压缩成 zip
                    shutil.make_archive(zip_path_temp, 'zip', root_dir=os.path.dirname(src_path), base_dir=dir_name)
                    # 3. 将生成的 .zip 文件重命名为 .zip.bak
                    final_backup_path = f"{zip_path_temp}.zip.bak"
                    os.rename(f"{zip_path_temp}.zip", final_backup_path)
            
            except Exception as e:
                QMessageBox.critical(self, "备份失败", f"无法为 '{os.path.basename(src_path)}' 创建备份:\n{e}")
                # 如果一个失败了，继续尝试下一个
                continue
        
        # 所有备份操作完成后，刷新当前视图
        self._populate_file_view(current_dir)

# [新增] 创建新文件夹的逻辑
    def _create_new_folder(self):
        """
        在当前目录下创建一个新的文件夹。
        """
        current_dir = self._get_current_dir()
        if not current_dir: return

        # 1. 弹窗让用户输入新文件夹名称
        folder_name, ok = QInputDialog.getText(self, "新建文件夹", "请输入文件夹名称:", QLineEdit.Normal, "新建文件夹")

        if ok and folder_name:
            new_folder_path = os.path.join(current_dir, folder_name)
            try:
                os.makedirs(new_folder_path, exist_ok=False) # exist_ok=False 确保如果已存在则报错

                # [核心] 联动刷新：先刷新右侧，再刷新左侧，最后同步位置
                self._populate_file_view(current_dir)
                self._populate_nav_tree()
                self._sync_nav_tree_to_path(current_dir)

            except FileExistsError:
                QMessageBox.warning(self, "创建失败", f"名为 '{folder_name}' 的文件夹已存在。")
            except Exception as e:
                QMessageBox.critical(self, "创建失败", f"无法创建文件夹:\n{e}")

    def _get_current_dir(self):
        """
        辅助函数：获取左侧导航树当前选中项的完整路径。
        """
        nav_item = self.nav_tree.currentItem()
        return nav_item.data(0, Qt.UserRole) if nav_item else None
        
    def _get_selected_paths(self):
        """辅助函数：获取当前在文件视图中选中的所有文件/文件夹的完整路径。"""
        selected_items = self.file_view.selectedItems()
        # 使用 set 来避免重复路径，然后排序确保顺序稳定
        return [self.file_view.item(row, 0).data(Qt.UserRole) for row in sorted(list(set(i.row() for i in selected_items)))]

    def _copy_items_from_selection(self):
        """快捷键触发的复制操作。"""
        # [核心修正] 增加上下文检查：如果当前在回收站，则禁止操作
        if self._get_current_dir() == self.trash_path:
            return

        paths = self._get_selected_paths()
        if paths: self._copy_items(paths)

    def _cut_items_from_selection(self):
        """快捷键触发的剪切操作。"""
        # [核心修正] 增加上下文检查：如果当前在回收站，则禁止操作
        if self._get_current_dir() == self.trash_path:
            return

        paths = self._get_selected_paths()
        if paths: self._cut_items(paths)

    def _open_items(self, paths):
        """
        使用系统默认程序打开文件或在文件浏览器中打开文件夹。
        支持多选。
        """
        for path in paths:
            try:
                # 使用 os.startfile 或 subprocess.call 来执行系统命令
                if sys.platform == "win32":
                    os.startfile(os.path.realpath(path))
                elif sys.platform == "darwin":
                    subprocess.call(["open", path])
                else: # Linux
                    subprocess.call(["xdg-open", path])
            except Exception as e:
                QMessageBox.warning(self, "打开失败", f"无法打开 '{os.path.basename(path)}':\n{e}")

    def _open_containing_folder(self, paths):
        """在系统文件浏览器中打开选中文件的所在文件夹。"""
        if not paths: return
        # 只处理第一个选中项
        path_to_open = paths[0]
        if os.path.isfile(path_to_open):
            dir_path = os.path.dirname(path_to_open)
        else: # 如果是文件夹本身
            dir_path = path_to_open
        
        # 调用 _open_items 方法来执行跨平台打开操作
        self._open_items([dir_path])

    def _rename_item(self):
        """
        [v1.3 体验优化版]
        触发文件视图中的重命名编辑框。
        对于文件，会自动选中文件名部分（不包含扩展名）。
        对于文件夹，则会全选。
        """
        selected_rows = list(set(i.row() for i in self.file_view.selectedItems()))
        if not selected_rows:
            return

        # 重命名操作只针对第一个选中的项目
        row = selected_rows[0]
        item_to_rename = self.file_view.item(row, 0) # 获取名称列的 QTableWidgetItem
        if not item_to_rename:
            return

        path = item_to_rename.data(Qt.UserRole)
        filename = item_to_rename.text()

        # 开始编辑
        self.file_view.editItem(item_to_rename)

        # 检查这是否是一个文件（而不是文件夹）
        # 如果是文件夹，或者文件没有扩展名，则保持默认的全选行为
        if not os.path.isdir(path):
            # 使用 os.path.splitext 分离文件名和扩展名
            basename, extension = os.path.splitext(filename)
            
            # 只有当文件确实有扩展名时，才进行部分选中
            if extension:
                # 使用 QTimer.singleShot(0, ...) 在下一个事件循环中执行操作。
                # 这是必需的，因为 QLineEdit 编辑器在调用 editItem() 后不是立即创建的。
                def select_basename():
                    # editItem() 会在表格上创建一个临时的 QLineEdit 作为编辑器。
                    # 我们可以通过 findChild 找到这个编辑器。
                    editor = self.file_view.findChild(QLineEdit)
                    if editor:
                        # setSelection(start_position, length)
                        editor.setSelection(0, len(basename))

                QTimer.singleShot(0, select_basename)

    def _on_item_renamed(self, item):
        """文件或文件夹重命名完成后的处理，实现原地更新。"""
        # 'item' 是一个 QTableWidgetItem
        if item.column() != 0:
            return

        # [核心修正] QTableWidgetItem.data() 只接受一个 role 参数
        old_path = item.data(Qt.UserRole)
        new_name = item.text()
        
        if not old_path or os.path.basename(old_path) == new_name:
            return
            
        dir_name = os.path.dirname(old_path)
        new_path = os.path.join(dir_name, new_name)

        try:
            # 1. 执行文件系统重命名
            os.rename(old_path, new_path)
            
            # 2. [核心修正] QTableWidgetItem.setData() 也只接受 role 和 value
            item.setData(Qt.UserRole, new_path)
            
            # 3. 如果重命名的是文件夹，则在左侧导航树中找到对应项并更新它
            if os.path.isdir(new_path):
                iterator = QTreeWidgetItemIterator(self.nav_tree)
                item_to_rename_in_tree = None
                while iterator.value():
                    tree_item = iterator.value()
                    # QTreeWidgetItem.data() 需要两个参数 (column, role)，这里是正确的
                    item_path = tree_item.data(0, Qt.UserRole)
                    if item_path and os.path.realpath(item_path) == os.path.realpath(old_path):
                        item_to_rename_in_tree = tree_item
                        break
                    iterator += 1

                if item_to_rename_in_tree:
                    is_plugins_dir = os.path.basename(os.path.dirname(new_path)) == "plugins"
                    display_name = new_name
                    if is_plugins_dir:
                        plugin_meta = self._get_plugin_meta(new_path)
                        if plugin_meta and plugin_meta.get("name"):
                            display_name = plugin_meta.get("name")
                    
                    # QTreeWidgetItem.setText() 需要 column 参数，这里是正确的
                    item_to_rename_in_tree.setText(0, display_name)
                    item_to_rename_in_tree.setData(0, Qt.UserRole, new_path)
            
                    self._update_child_paths_recursively(item_to_rename_in_tree, old_path, new_path)

        except Exception as e:
            QMessageBox.critical(self, "重命名失败", f"无法重命名项目:\n{e}")
            item.setText(os.path.basename(old_path))

    def _update_child_paths_recursively(self, parent_item, old_base, new_base):
        """
        当一个父文件夹被重命名后，递归地更新其所有子项的路径数据。
        """
        if not parent_item:
            return
            
        for i in range(parent_item.childCount()):
            child = parent_item.child(i)
            old_path = child.data(0, Qt.UserRole)
            if old_path and old_path.startswith(old_base):
                # 使用 os.path.relpath 计算相对路径，然后拼接到新基础上
                relative_path = os.path.relpath(old_path, old_base)
                new_path = os.path.join(new_base, relative_path)
                child.setData(0, Qt.UserRole, new_path)
                # 递归处理孙子节点
                self._update_child_paths_recursively(child, old_base, new_path)

    def _copy_items(self, paths):
        """将选中的路径存储到剪贴板，操作类型为“复制”。"""
        self.clipboard_paths = paths
        self.clipboard_operation = 'copy'
    
    def _cut_items(self, paths):
        """将选中的路径存储到剪贴板，操作类型为“剪切”。"""
        self.clipboard_paths = paths
        self.clipboard_operation = 'cut'

    def _paste_items(self):
        """[v1.2 副本模式] 将剪贴板中的项目粘贴到当前目录。如果遇到同名冲突，则自动创建副本。"""
        dest_dir = self._get_current_dir()

        # [核心修正] 增加上下文检查：禁止向回收站粘贴文件
        if dest_dir == self.trash_path:
            QMessageBox.information(self, "操作无效", "不能向回收站中直接粘贴项目。")
            return
        
        current_path_before_paste = dest_dir
        needs_tree_refresh = False
        
        for src_path in self.clipboard_paths:
            try:
                base_name = os.path.basename(src_path)
                dest_path = os.path.join(dest_dir, base_name)
                
                # [核心修正] 重构冲突处理逻辑
                # 只要目标路径存在，就需要处理
                if os.path.exists(dest_path):
                    # 特殊情况：如果是“剪切”操作，并且源和目标是同一个文件/目录，
                    # 那么什么都不用做，直接跳过。
                    if self.clipboard_operation == 'cut' and os.path.samefile(src_path, dest_path):
                        continue

                    # 对于所有其他情况（包括“复制”到同名文件，或“剪切”到不同但同名的文件），
                    # 我们都执行创建副本的逻辑。
                    name, ext = os.path.splitext(base_name)
                    
                    copy_dest_path = os.path.join(dest_dir, f"{name} (副本){ext}")
                    if not os.path.exists(copy_dest_path):
                        dest_path = copy_dest_path
                    else:
                        counter = 2
                        while True:
                            copy_dest_path = os.path.join(dest_dir, f"{name} (副本 {counter}){ext}")
                            if not os.path.exists(copy_dest_path):
                                dest_path = copy_dest_path
                                break
                            counter += 1

                # 执行文件操作
                if os.path.isdir(src_path):
                    needs_tree_refresh = True
                
                if self.clipboard_operation == 'copy':
                    if os.path.isdir(src_path):
                        shutil.copytree(src_path, dest_path)
                    else:
                        shutil.copy2(src_path, dest_path)
                elif self.clipboard_operation == 'cut':
                    shutil.move(src_path, dest_path)
            
            except Exception as e:
                QMessageBox.critical(self, "粘贴失败", f"无法粘贴 '{os.path.basename(src_path)}':\n{e}")
                break

        # 如果是剪切操作，完成后清空剪贴板
        if self.clipboard_operation == 'cut':
            self.clipboard_paths = []
            self.clipboard_operation = None

        # 统一刷新UI
        self._populate_file_view(current_path_before_paste)
        if needs_tree_refresh:
            self._populate_nav_tree()
            self._sync_nav_tree_to_path(current_path_before_paste)


    def _on_item_double_clicked(self, item):
        path = self.file_view.item(item.row(), 0).data(Qt.UserRole)
        if not path: return

        ext = os.path.splitext(path)[1].lower()
        filename = os.path.basename(path)

        # [新增] 1. 优先处理 .fdeck 文件
        if ext == '.fdeck':
            manager_plugin = self.main_window.plugin_manager.get_plugin_instance("com.phonacq.flashcard_manager")
            if manager_plugin:
                # 执行插件，并通过 kwargs 传递被双击的文件路径
                manager_plugin.execute(fdeck_path=path) 
                return
            else:
                QMessageBox.information(self, "插件未启用", 
                                        "请先在“插件管理”中启用“速记卡管理器”插件以编辑此文件。")
                return

        # 2. 处理 .json 配置文件
        if filename.lower() in ("settings.json", "config.json"):
            editor_dialog = DynamicJsonEditorDialog(path, self)
            editor_dialog.exec_()
            return

        # 3. 处理回收站策略文件
        if os.path.realpath(path) == os.path.realpath(self.trash_policy_config_path):
            dialog = TrashPolicyDialog(self.trash_policy_config_path, self)
            dialog.exec_()
            return
            
        # 4. 处理文件夹导航
        if os.path.isdir(path):
            self._sync_nav_tree_to_path(path)
            return
        
        # 5. 处理词表文件
        if ext == '.json':
            if os.path.dirname(path) in [WORD_LIST_DIR, DIALECT_VISUAL_WORDLIST_DIR]:
                self.main_window.open_in_wordlist_editor(path)
                return

        # 6. 处理日志文件
        if filename == 'log.txt':
            if os.path.commonpath([path, os.path.join(BASE_PATH, "Results")]) == os.path.join(BASE_PATH, "Results"):
                 self.main_window.open_in_log_viewer(path)
                 return
            
        # 7. 对于所有其他文件，使用系统默认程序打开
        self._open_items([path])

    def _sync_nav_tree_to_path(self, path_to_find):
        """
        在左侧导航树中查找、展开并选中与指定路径匹配的项。
        实现右侧文件视图双击文件夹后，左侧树的同步导航。
        """
        # 使用 QTreeWidgetItemIterator 遍历所有树节点
        iterator = QTreeWidgetItemIterator(self.nav_tree)
        while iterator.value():
            item = iterator.value()
            item_path = item.data(0, Qt.UserRole) # 获取节点的完整路径
            
            # 比较路径，使用 realpath 进行规范化，处理符号链接等情况
            if item_path and os.path.realpath(item_path) == os.path.realpath(path_to_find):
                # 找到了匹配项
                # 1. 展开所有父项，直到根
                parent = item.parent()
                while parent:
                    parent.setExpanded(True)
                    parent = parent.parent()
                # 2. 选中目标项
                self.nav_tree.setCurrentItem(item)
                # 3. 确保目标项在可见区域内
                self.nav_tree.scrollToItem(item, QTreeWidget.PositionAtTop)
                return # 找到并处理完毕，退出循环
            iterator += 1

    def _delete_items(self, paths):
        """
        [v1.2 刷新修复版]
        将选中的项目移动到回收站或永久删除。
        增加了对删除文件夹后UI同步的健壮性修复。
        """
        # [核心修正] 在执行任何文件系统操作之前，预先判断是否需要刷新导航树。
        # 这是因为在文件被删除后，os.path.isdir() 将无法正确判断。
        needs_tree_refresh = any(os.path.isdir(p) for p in paths)
        
        current_dir_before_delete = self._get_current_dir()
        is_in_trash = current_dir_before_delete == self.trash_path

        # --- 分支1: 永久删除 (在回收站中) ---
        if is_in_trash:
            reply = QMessageBox.question(self, "永久删除", f"您确定要永久删除这 {len(paths)} 个项目吗？\n此操作不可撤销。",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes: return
            
            metadata = self._load_trash_metadata()
            for path in paths:
                try:
                    if os.path.isdir(path): shutil.rmtree(path)
                    else: os.remove(path)
                    metadata.pop(os.path.basename(path), None)
                except Exception as e:
                    QMessageBox.critical(self, "操作失败", f"无法删除 '{os.path.basename(path)}':\n{e}")
            self._save_trash_metadata(metadata)

        # --- 分支2: 移动到回收站 (常规目录) ---
        else:
            reply = QMessageBox.question(self, "删除到回收站", f"您确定要将这 {len(paths)} 个项目移动到回收站吗？",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply != QMessageBox.Yes: return
            
            # 调用插件自身的公共API来执行移动操作
            file_manager_plugin = self.main_window.plugin_manager.get_plugin_instance("com.phonacq.file_manager")
            if file_manager_plugin:
                # 释放可能被其他模块（如音频管理器）占用的文件句柄
                if hasattr(self.main_window, 'audio_manager_page'):
                    self.main_window.audio_manager_page.reset_player()
                    QApplication.processEvents()

                success, message = file_manager_plugin.move_to_trash(paths)
                if not success:
                    QMessageBox.critical(self, "移至回收站失败", message)
            else:
                QMessageBox.critical(self, "错误", "无法获取文件管理器插件实例。")

        # --- 统一的UI刷新 ---
        # 首先确定刷新后应该定位到哪个目录
        target_dir_after_delete = current_dir_before_delete
        if not os.path.exists(target_dir_after_delete):
            parent_dir = os.path.dirname(current_dir_before_delete)
            # 如果父目录存在，则定位到父目录；否则回退到项目根目录
            target_dir_after_delete = parent_dir if os.path.exists(parent_dir) else BASE_PATH
        
        # 总是刷新右侧的文件视图
        self._populate_file_view(target_dir_after_delete)

        # 只有在确实操作了文件夹时，才刷新计算量更大的左侧导航树
        if needs_tree_refresh:
            self._populate_nav_tree()
            # 刷新树后，确保左侧的选中状态恢复到操作完成后的目标目录
            self._sync_nav_tree_to_path(target_dir_after_delete)

    def _restore_items(self, paths_in_trash):
        """
        [v1.1] 将回收站中的项目恢复到其原始位置。
        增加了对目标路径已存在的冲突处理逻辑。
        """
        metadata = self._load_trash_metadata()
        restored_count = 0
        needs_tree_refresh = False

        for current_trash_path in paths_in_trash:
            trash_name = os.path.basename(current_trash_path)
            
            if trash_name not in metadata:
                QMessageBox.warning(self, "恢复失败", f"找不到 '{trash_name}' 的原始位置信息。")
                continue

            original_path = metadata[trash_name]["original_path"]
            is_folder = os.path.isdir(current_trash_path)

            final_dest_path = original_path

            # [核心修正] 冲突检测与处理
            if os.path.exists(original_path):
                # 弹窗询问用户如何处理冲突
                msg_box = QMessageBox(self)
                msg_box.setIcon(QMessageBox.Question)
                msg_box.setWindowTitle("恢复冲突")
                item_type = "文件夹" if is_folder else "文件"
                msg_box.setText(f"目标位置已存在一个同名{item_type}:")
                msg_box.setInformativeText(f"<b>{os.path.basename(original_path)}</b>\n\n您想如何操作？")

                # 根据是文件还是文件夹，提供不同的选项
                if is_folder:
                    merge_btn = msg_box.addButton("合并内容", QMessageBox.AcceptRole)
                else: # 文件
                    overwrite_btn = msg_box.addButton("覆盖", QMessageBox.AcceptRole)
                
                rename_btn = msg_box.addButton("恢复并重命名", QMessageBox.YesRole)
                skip_btn = msg_box.addButton("跳过", QMessageBox.RejectRole)
                
                msg_box.exec_()
                clicked_btn = msg_box.clickedButton()

                if clicked_btn == skip_btn:
                    continue # 跳过这个项目
                
                elif clicked_btn == rename_btn:
                    # 自动寻找可用的副本名称
                    name, ext = os.path.splitext(original_path)
                    counter = 1
                    while True:
                        # 命名为 "... (已恢复)" 或 "... (已恢复 2)"
                        renamed_path = f"{name} (已恢复 {counter}){ext}" if counter > 1 else f"{name} (已恢复){ext}"
                        if not os.path.exists(renamed_path):
                            final_dest_path = renamed_path
                            break
                        counter += 1
                # 对于文件夹，如果用户选择“合并”，final_dest_path 保持为 original_path
                # 对于文件，如果用户选择“覆盖”，final_dest_path 保持为 original_path
            
            try:
                dest_dir = os.path.dirname(final_dest_path)
                os.makedirs(dest_dir, exist_ok=True)

                # 执行恢复操作
                if is_folder and os.path.exists(final_dest_path):
                    # 这是“合并”文件夹的逻辑
                    # 我们需要遍历源文件夹，将其内容逐一移动到目标文件夹
                    for item in os.listdir(current_trash_path):
                        src_item_path = os.path.join(current_trash_path, item)
                        dst_item_path = os.path.join(final_dest_path, item)
                        # 注意：这里的内层移动也可能产生冲突，为简化，我们直接覆盖
                        shutil.move(src_item_path, dst_item_path)
                    shutil.rmtree(current_trash_path) # 删除回收站里的空文件夹
                else:
                    # 这是常规移动（恢复到新位置，或覆盖文件）
                    shutil.move(current_trash_path, final_dest_path)

                metadata.pop(trash_name)
                restored_count += 1
                if is_folder:
                    needs_tree_refresh = True
            
            except Exception as e:
                QMessageBox.critical(self, "恢复失败", f"无法恢复 '{trash_name}':\n{e}")
        
        self._save_trash_metadata(metadata)
        
        if restored_count > 0:
            if needs_tree_refresh:
                self._populate_nav_tree()
            self._populate_file_view(self.trash_path)
            QMessageBox.information(self, "成功", f"成功恢复了 {restored_count} 个项目。")

    def _ensure_trash_policy_exists(self):
        """
        确保回收站策略配置文件存在。如果不存在，则创建默认配置。
        """
        if not os.path.exists(self.trash_policy_config_path):
            defaults = {
                "enabled": True, 
                "by_days_enabled": True, "max_days": 30,
                "by_count_enabled": True, "max_count": 500,
                "by_size_mb_enabled": True, "max_size_mb": 1024
            }
            self._save_trash_policy(defaults)

# [新增 v1.2] 左侧导航树的右键菜单处理器
    def _show_nav_tree_context_menu(self, position):
        """当用户在左侧导航树上右键点击时调用。"""
        item = self.nav_tree.itemAt(position)
        if not item:
            return # 如果点击在空白处，则不显示菜单

        # 检查被点击的条目是否是我们标记的“回收站”
        if item.data(0, Qt.UserRole + 1) == "trash_item":
            menu = QMenu(self.nav_tree)
            
            # 添加“清空回收站”选项
            clear_action = menu.addAction(self.icon_manager.get_icon("clear_contents"), "清空回收站...")
            clear_action.triggered.connect(self._empty_trash)
            
            menu.addSeparator()

            # 添加“配置清理策略”选项
            policy_action = menu.addAction(self.icon_manager.get_icon("settings"), "配置清理策略...")
            policy_action.triggered.connect(self._open_trash_policy_dialog)

            menu.exec_(self.nav_tree.viewport().mapToGlobal(position))

    # [新增 v1.2] “清空回收站”的逻辑实现
    def _empty_trash(self):
        """执行清空回收站的操作。"""
        # 统计回收站中的项目数量 (排除元数据和策略文件)
        items_in_trash = [name for name in os.listdir(self.trash_path) if name not in {".metadata.json", ".trash_policy.json"}]
        
        if not items_in_trash:
            QMessageBox.information(self, "回收站", "回收站已经是空的。")
            return

        # 弹窗进行最终确认
        reply = QMessageBox.question(self, "确认清空回收站",
                                     f"您确定要永久删除回收站中的 {len(items_in_trash)} 个项目吗？\n此操作不可撤销！",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            metadata = {} # 准备一个空的元数据字典
            
            for name in items_in_trash:
                path = os.path.join(self.trash_path, name)
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                    else:
                        os.remove(path)
                except OSError as e:
                    QMessageBox.critical(self, "清空失败", f"无法删除 '{name}':\n{e}")
                    # 即使单个文件失败，也继续尝试删除其他文件
                    # 并保留现有的元数据，只更新成功的
                    metadata = self._load_trash_metadata()
                    metadata.pop(name, None)
            
            # 保存空的或更新后的元数据
            self._save_trash_metadata(metadata)
            
            # 刷新UI
            self._populate_file_view(self.trash_path)
            QMessageBox.information(self, "成功", "回收站已清空。")

    # [新增 v1.2] 打开策略对话框的辅助方法
    def _open_trash_policy_dialog(self):
        """打开回收站清理策略配置对话框。"""
        dialog = TrashPolicyDialog(self.trash_policy_config_path, self)
        dialog.exec_()

    def _load_trash_policy(self):
        """
        从 JSON 配置文件中加载回收站清理策略。
        如果文件不存在或解析失败，则返回默认策略。
        """
        if not os.path.exists(self.trash_policy_config_path):
            self._ensure_trash_policy_exists() # 确保文件存在
        try:
            with open(self.trash_policy_config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            # 如果文件损坏或无法读取，回退到默认策略
            return {
                "enabled": True, 
                "by_days_enabled": True, "max_days": 30,
                "by_count_enabled": True, "max_count": 500,
                "by_size_mb_enabled": True, "max_size_mb": 1024
            }

    def _save_trash_policy(self, policy):
        """
        将回收站清理策略保存到 JSON 配置文件中。
        """
        try:
            with open(self.trash_policy_config_path, 'w', encoding='utf-8') as f:
                json.dump(policy, f, indent=4)
        except IOError as e:
            print(f"[File Manager] 错误: 无法保存回收站策略文件: {e}")

    def _cleanup_trash(self):
        """
        根据配置的策略，自动清理回收站中的旧文件。
        此任务在每次文件管理器启动时执行。
        """
        policy = self._load_trash_policy()
        if not policy.get("enabled", False): # 如果自动清理未启用，则直接返回
            return
        
        metadata = self._load_trash_metadata()
        if not metadata: return # 如果没有元数据，说明回收站是空的，无需清理
        
        now = datetime.now() # 获取当前时间
        to_delete = set() # 存储待删除文件的名称集合

        # --- 策略1: 基于时间的清理 (Age-Based Cleanup) ---
        if policy.get("by_days_enabled", True): # 如果此策略启用
            max_age = timedelta(days=policy.get("max_days", 30)) # 获取最大保留天数
            for name, data in metadata.items():
                try:
                    deleted_time = datetime.fromisoformat(data["deleted_time"])
                    if now - deleted_time > max_age:
                        to_delete.add(name) # 标记为待删除
                except (ValueError, TypeError):
                    # 如果日期格式错误，忽略此项，防止崩溃
                    continue

        # 准备一个包含文件大小和修改时间（用于排序）的列表，用于后续策略
        remaining_files = []
        for name in os.listdir(self.trash_path):
            # 排除元数据文件和策略文件，以及已经被标记为待删除的文件
            if name not in {".metadata.json", ".trash_policy.json"} and name not in to_delete:
                full_path = os.path.join(self.trash_path, name)
                try:
                    stat = os.stat(full_path)
                    remaining_files.append({"name": name, "size": stat.st_size, "mtime": stat.st_mtime})
                except FileNotFoundError:
                    # 如果文件在扫描过程中被删除，忽略
                    continue
        
        # 按修改时间（近似删除时间）排序，最旧的文件在前
        remaining_files.sort(key=lambda x: x["mtime"])

        # --- 策略2: 基于数量的清理 (Count-Based Cleanup) ---
        if policy.get("by_count_enabled", True): # 如果此策略启用
            max_count = policy.get("max_count", 500) # 获取最大文件数量
            if len(remaining_files) > max_count:
                num_to_delete = len(remaining_files) - max_count # 计算需要删除的数量
                for f in remaining_files[:num_to_delete]:
                    to_delete.add(f["name"]) # 标记为待删除
                remaining_files = remaining_files[num_to_delete:] # 更新剩余文件列表

        # --- 策略3: 基于体积的清理 (Size-Based Cleanup) ---
        if policy.get("by_size_mb_enabled", True): # 如果此策略启用
            max_size_bytes = policy.get("max_size_mb", 1024) * 1024 * 1024 # 获取最大体积（转换为字节）
            current_size_bytes = sum(f["size"] for f in remaining_files) # 计算当前总大小
            
            while current_size_bytes > max_size_bytes and remaining_files:
                oldest = remaining_files.pop(0) # 移除最旧的文件
                to_delete.add(oldest["name"]) # 标记为待删除
                current_size_bytes -= oldest["size"] # 更新总大小

        # --- 执行删除操作 ---
        if to_delete:
            print(f"[File Manager] 自动清理回收站: 正在删除 {len(to_delete)} 个项目...")
            for name in to_delete:
                path = os.path.join(self.trash_path, name)
                try:
                    if os.path.isdir(path): shutil.rmtree(path) # 删除文件夹
                    else: os.remove(path) # 删除文件
                    metadata.pop(name, None) # 从元数据中移除记录
                except OSError as e:
                    # 打印错误，但继续清理其他文件
                    print(f"[File Manager] 错误: 自动清理无法删除 '{name}': {e}")
            self._save_trash_metadata(metadata) # 保存更新后的元数据

    def _load_trash_metadata(self):
        """
        从 .metadata.json 文件中加载回收站的元数据。
        元数据记录了被删除文件的原始路径和删除时间。
        """
        if not os.path.exists(self.trash_metadata_path):
            return {}
        try:
            with open(self.trash_metadata_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[File Manager] 错误: 无法加载回收站元数据文件: {e}")
            # 如果文件损坏，返回空字典，并考虑备份/删除损坏文件
            return {}

    def _save_trash_metadata(self, metadata):
        """
        将回收站的元数据保存到 .metadata.json 文件。
        """
        try:
            with open(self.trash_metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=4)
        except IOError as e:
            print(f"[File Manager] 错误: 无法保存回收站元数据文件: {e}")

    # --- 拖拽导入事件处理 ---
    def dragEnterEvent(self, event):
        """
        当文件被拖拽进入对话框区域时触发。
        检查拖拽的数据是否包含本地文件URL，如果是则接受拖拽操作。
        """
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        """
        当文件被拖拽并释放到对话框区域时触发。
        将拖拽的文件复制到当前选中的目录。
        """
        dest_dir = self._get_current_dir() # 获取当前左侧导航树中选中的目录
        if not dest_dir:
            QMessageBox.warning(self, "操作失败", "请先在左侧导航栏中选择一个目标文件夹。")
            return
        
        # 记录拖拽操作开始前，当前选中的目录路径。
        # 这用于在操作完成后，确保左侧导航树仍然选中该目录。
        current_selected_path_before_drop = dest_dir
        
        # 获取所有被拖拽的本地文件路径
        source_paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if not source_paths: return # 如果没有有效路径，则返回

        overwrite_all = False # 标记是否对所有后续冲突都选择覆盖
        skip_all = False      # 标记是否对所有后续冲突都选择跳过
        needs_tree_refresh = False # 标记是否需要刷新左侧导航树（如果拖入了文件夹）

        for src_path in source_paths:
            if os.path.isdir(src_path):
                needs_tree_refresh = True # 如果拖入的是文件夹，则需要刷新树
            
            dest_path = os.path.join(dest_dir, os.path.basename(src_path))
            
            # 检查目标位置是否已存在同名文件/文件夹，并处理冲突
            if os.path.exists(dest_path) and not os.path.samefile(src_path, dest_path):
                if overwrite_all:
                    pass # 如果已选择“全部覆盖”，则跳过提示
                elif skip_all:
                    continue # 如果已选择“全部跳过”，则跳过当前文件
                else:
                    # 弹出消息框，让用户选择如何处理冲突
                    msg_box = QMessageBox(self)
                    msg_box.setIcon(QMessageBox.Question)
                    msg_box.setText(f"目标位置已存在 '{os.path.basename(src_path)}'。")
                    msg_box.setInformativeText("您想如何处理？")
                    
                    # 添加多个按钮供用户选择
                    overwrite_btn = msg_box.addButton("覆盖", QMessageBox.AcceptRole)
                    overwrite_all_btn = msg_box.addButton("全部覆盖", QMessageBox.YesRole)
                    skip_btn = msg_box.addButton("跳过", QMessageBox.RejectRole)
                    skip_all_btn = msg_box.addButton("全部跳过", QMessageBox.NoRole)
                    msg_box.addButton("取消", QMessageBox.AbortRole) # 取消按钮
                    
                    msg_box.exec_() # 显示消息框并等待用户选择

                    # 根据用户点击的按钮更新全局操作标记
                    if msg_box.clickedButton() == overwrite_all_btn:
                        overwrite_all = True
                    elif msg_box.clickedButton() == skip_btn:
                        continue # 跳过当前文件
                    elif msg_box.clickedButton() == skip_all_btn:
                        skip_all = True
                        continue # 跳过当前文件及后续所有冲突
                    elif msg_box.clickedButton() == overwrite_btn:
                        pass # 覆盖当前文件
                    else:
                        break # 用户点击“取消”，中断所有后续操作

            try:
                # 执行文件复制操作
                if os.path.isdir(src_path):
                    shutil.copytree(src_path, dest_path, dirs_exist_ok=True) # 复制文件夹
                else:
                    shutil.copy2(src_path, dest_path) # 复制文件
            except Exception as e:
                QMessageBox.critical(self, "导入失败", f"无法复制 '{os.path.basename(src_path)}':\n{e}")
                break # 复制失败，中断所有后续操作
        
        self._populate_nav_tree()
        self._populate_file_view(dest_dir)


        # 强制将导航树的选中项恢复到操作前的位置，确保UI焦点不变
        self._sync_nav_tree_to_path(current_selected_path_before_drop)