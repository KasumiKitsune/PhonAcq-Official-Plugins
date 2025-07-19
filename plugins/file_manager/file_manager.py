# --- START OF FILE plugins/file_manager/file_manager.py ---

import os
import sys
import shutil
import json
import textwrap
import pathlib
from datetime import datetime, timedelta
import subprocess

# PyQt5 核心模块导入
from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
                             QMessageBox, QSplitter, QLabel, QMenu, QHeaderView, QLineEdit,
                             QTreeWidgetItemIterator, QApplication, QShortcut, QFormLayout,
                             QSlider, QDialogButtonBox, QCheckBox, QGroupBox)
from PyQt5.QtCore import Qt, QSize, QBuffer, QByteArray, QObject, QEvent # QBuffer, QByteArray 用于图片Base64编码
from PyQt5.QtGui import QIcon, QKeySequence, QPixmap # QPixmap 用于图片处理，QKeySequence 用于快捷键

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

class KeyNavigationFilter(QObject):
    def __init__(self, dialog, parent=None):
        super().__init__(parent)
        self.dialog = dialog

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
                if obj is self.dialog.file_view:
                    # 在右侧文件列表按Enter，模拟双击
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
                    # 如果右侧列表为空，则不选中；否则选中第一行
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
        self.icons = {} # 存储文件扩展名到 QIcon 的映射
        self.dir_icons = {} # 存储目录名称到 QIcon 的映射

        icon_dir = os.path.join(self.plugin_dir, 'icons') # 插件图标目录

        def load_icon(name):
            """尝试从插件的 icons 目录加载指定名称的图标。"""
            for ext in ['.svg', '.png']: # 优先 SVG，其次 PNG
                path = os.path.join(icon_dir, f"{name}{ext}")
                if os.path.exists(path):
                    return QIcon(path)
            return None # 如果找不到，返回 None

        # --- 1. 加载文件类型图标 ---
        # 键是图标文件名，值是对应的文件扩展名列表
        file_icon_map = {
            "audio": [".wav", ".mp3", ".flac", ".ogg"],
            "image": [".png", ".jpg", ".jpeg", ".bmp", ".svg"],
            "json_file": [".json"],
            "excel": [".xlsx", ".xls", ".csv"],
            "text_grid": [".textgrid"],
            "text_file": [".txt", ".md", ".log"],
            "python": [".py"],
            "qss": [".qss"]
        }
        for name, exts in file_icon_map.items():
            icon = load_icon(name)
            if icon:
                for ext in exts:
                    self.icons[ext] = icon
        
        # --- 2. 加载目录专属图标 ---
        # 键是目录名（原始英文名），值是图标文件名
        # 插件会尝试加载与目录名同名的图标
        dir_names = [
            "Results", "word_lists", "plugins", "themes", "assets", "config",
            "modules", "flashcards", "dialect_visual_wordlists", "PhonAcq_Archives",
            "audio_tts", "audio_record"
        ]
        for name in dir_names:
            icon = load_icon(name) # 尝试加载如 "Results.svg"
            if icon:
                self.dir_icons[name] = icon

        # --- 3. 加载通用文件夹、回收站和未知文件图标 ---
        # 确保这些关键图标总能被加载，即使自定义图标缺失也能回退到主程序图标
        self.icons['folder'] = load_icon('folder') or self.icon_manager.get_icon("folder") # 通用文件夹图标
        self.icons['trash'] = load_icon('trash') or self.icon_manager.get_icon("delete") # 回收站图标
        self.generic_file_icon = load_icon('unknown') or self.icon_manager.get_icon("file") # 未知文件类型图标

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
        填充右侧文件视图：显示指定目录下的所有文件和子文件夹。
        同时为文件项生成丰富的Tooltip。
        """
        # 显示“正在加载”提示，并强制UI刷新，以避免长时间冻结
        self.current_path_label.setText(f"正在加载: {os.path.relpath(dir_path, BASE_PATH)}...")
        self.file_view.setRowCount(0) # 清空现有内容
        self.file_view.setSortingEnabled(False) # 临时禁用排序，提高填充速度
        QApplication.processEvents() # 确保“正在加载”被渲染

        is_in_trash = os.path.realpath(dir_path) == os.path.realpath(self.trash_path)
        
        try:
            # --- [核心修复] 手动处理回收站策略文件 ---
            if is_in_trash and os.path.exists(self.trash_policy_config_path):
                # 1. 手动添加策略文件到列表顶部
                self.file_view.insertRow(0)
                policy_path = self.trash_policy_config_path
                policy_name = os.path.basename(policy_path)
                
                name_item = QTableWidgetItem(policy_name)
                name_item.setIcon(self.main_window.icon_manager.get_icon("settings")) # 使用设置图标
                name_item.setData(Qt.UserRole, policy_path)
                # 禁止重命名和删除策略文件
                name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable) 
                name_item.setToolTip("双击以配置回收站自动清理策略。")
                self.file_view.setItem(0, 0, name_item)
                
                # 为策略文件填充大小和日期信息
                try:
                    stat = os.stat(policy_path)
                    size_item = NumericTableWidgetItem()
                    size_item.setData(Qt.UserRole, stat.st_size)
                    size_item.setText(f"{stat.st_size} B")
                    self.file_view.setItem(0, 1, size_item)
                    self.file_view.setItem(0, 2, QTableWidgetItem(datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')))
                except FileNotFoundError:
                    self.file_view.setItem(0, 1, QTableWidgetItem("N/A")); self.file_view.setItem(0, 2, QTableWidgetItem("N/A"))

            # --- 正常列出目录中的其他文件 ---
            items = os.listdir(dir_path)
            for name in sorted(items, key=lambda x: (not os.path.isdir(os.path.join(dir_path, x)), x.lower())):
                # 2. 移除所有特殊过滤，只过滤元数据文件
                if is_in_trash and name == ".metadata.json":
                    continue
                # 3. 移除对 .trash_policy.json 的过滤，因为它已经被手动添加了
                if is_in_trash and name == ".trash_policy.json":
                    continue
                
                full_path = os.path.join(dir_path, name)
                row = self.file_view.rowCount(); self.file_view.insertRow(row)
                name_item = QTableWidgetItem(name); name_item.setIcon(self._get_icon_for_path(full_path))
                name_item.setData(Qt.UserRole, full_path); name_item.setFlags(name_item.flags() | Qt.ItemIsEditable)
                name_item.setToolTip(self._generate_tooltip_for_item(full_path))
                self.file_view.setItem(row, 0, name_item)
                
                try:
                    stat = os.stat(full_path)
                    if os.path.isdir(full_path):
                        size_item = QTableWidgetItem("--"); size_item.setTextAlignment(Qt.AlignCenter)
                        size_item.setData(Qt.UserRole, -1) # 负值用于排序，确保文件夹排在文件前面
                    else:
                        size = stat.st_size
                        # 扩展格式化逻辑以支持 MB 和 GB
                        if size > 1024 * 1024 * 1024: size_str = f"{size / (1024*1024*1024):.1f} GB"
                        elif size > 1024 * 1024: size_str = f"{size / (1024*1024):.1f} MB"
                        elif size > 1024: size_str = f"{size / 1024:.1f} KB"
                        else: size_str = f"{size} B"
                        size_item = NumericTableWidgetItem(); size_item.setData(Qt.UserRole, size); size_item.setText(size_str)
                    
                    self.file_view.setItem(row, 1, size_item)
                    self.file_view.setItem(row, 2, QTableWidgetItem(datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')))
                except FileNotFoundError:
                     self.file_view.setItem(row, 1, QTableWidgetItem("N/A")); self.file_view.setItem(row, 2, QTableWidgetItem("N/A"))
        except Exception as e: QMessageBox.critical(self, "错误", f"无法读取目录 '{dir_path}':\n{e}")
        
        # 恢复显示并启用排序
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

    def _get_icon_for_path(self, path):
        """
        根据文件/文件夹的类型或特定文件名返回对应的 QIcon。
        图标优先级：特定文件名 > 目录专属 > 文件扩展名 > 通用图标。
        """
        filename = os.path.basename(path)

        # 1. 优先检查特定文件名（如回收站策略文件、通用配置文件）
        if filename in (".trash_policy.json", "config.json", "settings.json"):
            return self.main_window.icon_manager.get_icon("settings")

        # 2. 检查是否为文件夹，并尝试获取目录专属图标
        if os.path.isdir(path):
            # 尝试获取与目录名匹配的专属图标，否则回退到通用文件夹图标
            return self.dir_icons.get(filename, self.icons.get("folder"))
        
        # 3. 根据文件扩展名进行回退
        ext = os.path.splitext(path)[1].lower() # 获取文件扩展名
        return self.icons.get(ext, self.generic_file_icon) # 根据扩展名查找图标，否则回退到通用文件图标

    def _generate_tooltip_for_item(self, path):
        """
        根据文件类型，调度不同的函数来生成详细的 HTML 格式工具提示。
        """
        file_type = self._get_file_type(path)
        
        if file_type == 'text':
            return self._tooltip_for_text(path)
        elif file_type == 'image':
            return self._tooltip_for_image(path)
        elif file_type == 'wordlist':
            return self._tooltip_for_wordlist(path)
        elif file_type == 'file':
            return self._tooltip_for_metadata(path)
        
        return "" # 文件夹不需要 Tooltip

    def _get_file_type(self, path):
        """
        辅助函数：判断文件的通用类型（文件夹、文本、图片、词表、其他文件）。
        用于 Tooltip 生成和双击打开逻辑。
        """
        if os.path.isdir(path): return 'folder'
        ext = os.path.splitext(path)[1].lower()
        
        text_exts = {".txt", ".md", ".log", ".py", ".qss", ".csv", ".textgrid"}
        image_exts = {".png", ".jpg", ".jpeg", ".bmp", ".svg"}

        if ext in text_exts: return 'text'
        if ext in image_exts: return 'image'
        
        if ext == '.json':
            # 特别处理 JSON 文件，尝试判断是否为词表
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if 'meta' in data and 'format' in data['meta']:
                    # 检查是否是标准词表或图文词表
                    if data['meta']['format'] in ["standard_wordlist", "visual_wordlist"]:
                        return 'wordlist'
            except (json.JSONDecodeError, IOError):
                pass # 不是合法的 JSON 或无法读取，按普通文本文件处理
            return 'text' # 默认将未知结构的 JSON 也视为文本文件

        return 'file' # 其他未知文件类型

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
        """显示右键上下文菜单。"""
        menu = QMenu(self.file_view) # 菜单属于 file_view
        menu.setStyleSheet(self.main_window.styleSheet()) # 应用主窗口的 QSS 样式
        
        selected_paths = self._get_selected_paths()

        is_in_trash = self._get_current_dir() == self.trash_path

        if selected_paths:
            if is_in_trash:
                menu.addAction(self.icon_manager.get_icon("replace"), "恢复", lambda: self._restore_items(selected_paths))
                # “永久删除”在回收站视图中依然是主要操作，位置不变
                menu.addAction(self.icon_manager.get_icon("delete"), "永久删除", lambda: self._delete_items(selected_paths))
            else:
                menu.addAction(self.icon_manager.get_icon("open_external"), "打开", lambda: self._open_items(selected_paths))
                menu.addAction(self.icon_manager.get_icon("open_folder"), "打开所在文件夹", lambda: self._open_containing_folder(selected_paths))
                if len(selected_paths) == 1:
                    menu.addAction(self.icon_manager.get_icon("rename"), "重命名", self._rename_item)
                menu.addSeparator() # 分隔线
                menu.addAction(self.icon_manager.get_icon("copy"), "复制 (Ctrl+C)", lambda: self._copy_items(selected_paths))
                menu.addAction(self.icon_manager.get_icon("cut"), "剪切 (Ctrl+X)", lambda: self._cut_items(selected_paths))

        if not is_in_trash:
            paste_action = menu.addAction(self.icon_manager.get_icon("paste"), "粘贴 (Ctrl+V)")
            paste_action.setEnabled(bool(self.clipboard_paths))
            paste_action.triggered.connect(self._paste_items)
        
        # 只有在选中了文件且不在回收站时，才在菜单末尾添加“删除到回收站”
        if selected_paths and not is_in_trash:
            if menu.actions(): # 确保菜单不为空时才加分隔符
                menu.addSeparator()
            menu.addAction(self.icon_manager.get_icon("delete"), "删除到回收站", lambda: self._delete_items(selected_paths))
            
        menu.exec_(self.file_view.viewport().mapToGlobal(position))

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
        paths = self._get_selected_paths()
        if paths: self._copy_items(paths)

    def _cut_items_from_selection(self):
        """快捷键触发的剪切操作。"""
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
        """触发文件视图中的重命名编辑框。"""
        selected = self.file_view.selectedItems()
        if selected:
            # 只有第一列的 item 可以编辑（即名称）
            self.file_view.editItem(selected[0])

    def _on_item_renamed(self, item):
        """文件或文件夹重命名完成后的处理。"""
        if item.column() == 0: # 确保是名称列
            old_path = item.data(Qt.UserRole)
            new_name = item.text()
            
            # 如果路径无效或名称未改变，则不做任何操作
            if not old_path or os.path.basename(old_path) == new_name:
                return
            
            dir_name = os.path.dirname(old_path)
            new_path = os.path.join(dir_name, new_name)
            
            try:
                os.rename(old_path, new_path)
                item.setData(Qt.UserRole, new_path) # 更新 UserData 中的新路径
                # 如果是文件夹被重命名，需要刷新整个导航树以反映变化
                if os.path.isdir(new_path):
                    self._populate_nav_tree()
            except Exception as e:
                QMessageBox.critical(self, "重命名失败", f"无法重命名文件:\n{e}")
                item.setText(os.path.basename(old_path)) # 恢复旧名称

    def _copy_items(self, paths):
        """将选中的路径存储到剪贴板，操作类型为“复制”。"""
        self.clipboard_paths = paths
        self.clipboard_operation = 'copy'
    
    def _cut_items(self, paths):
        """将选中的路径存储到剪贴板，操作类型为“剪切”。"""
        self.clipboard_paths = paths
        self.clipboard_operation = 'cut'

    def _paste_items(self):
        """将剪贴板中的项目粘贴到当前目录。"""
        dest_dir = self._get_current_dir() # 获取当前目标目录
        if not dest_dir: return # 如果没有选中目标目录，则返回

        needs_tree_refresh = False # 标记是否需要刷新左侧导航树
        for src_path in self.clipboard_paths:
            try:
                if os.path.isdir(src_path): # 如果粘贴的是文件夹，肯定需要刷新树
                    needs_tree_refresh = True
                
                dest_path = os.path.join(dest_dir, os.path.basename(src_path))
                
                # 检查目标路径是否已存在，并提示用户是否覆盖
                if os.path.exists(dest_path) and not os.path.samefile(src_path, dest_path):
                    reply = QMessageBox.question(self, "文件冲突", f"目标位置已存在 '{os.path.basename(src_path)}'。\n是否要覆盖它？",
                                               QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                    if reply == QMessageBox.No:
                        continue # 用户选择不覆盖，跳过此文件
                
                if self.clipboard_operation == 'copy':
                    # 复制操作：如果是文件夹，使用 copytree；否则使用 copy2
                    if os.path.isdir(src_path): shutil.copytree(src_path, dest_path, dirs_exist_ok=True)
                    else: shutil.copy2(src_path, dest_path)
                elif self.clipboard_operation == 'cut':
                    # 剪切操作：直接移动文件/文件夹
                    shutil.move(src_path, dest_path)
            except Exception as e:
                QMessageBox.critical(self, "粘贴失败", f"无法粘贴 '{os.path.basename(src_path)}':\n{e}")
        
        # 如果是剪切操作，粘贴完成后清空剪贴板
        if self.clipboard_operation == 'cut':
            self.clipboard_paths = []
            self.clipboard_operation = None
        
        # 刷新UI以反映粘贴操作
        if needs_tree_refresh:
            self._populate_nav_tree() # 重建左侧树
        self._populate_file_view(dest_dir) # 刷新右侧文件列表

    def _on_item_double_clicked(self, item):
        path = self.file_view.item(item.row(), 0).data(Qt.UserRole)
        if not path: return
        
        # 拦截对回收站策略文件的双击，打开配置对话框
        if os.path.realpath(path) == os.path.realpath(self.trash_policy_config_path):
            dialog = TrashPolicyDialog(self.trash_policy_config_path, self)
            dialog.exec_()
            return
            
        if os.path.isdir(path):
            # 如果是文件夹，同步左侧导航树并刷新右侧视图
            self._sync_nav_tree_to_path(path)
            return
        
        ext = os.path.splitext(path)[1].lower()
        filename = os.path.basename(path)

        # 检查是否是词表文件
        if ext == '.json':
            # 只有在特定词表目录下才认为是词表，避免误判其他json文件
            if os.path.dirname(path) in [WORD_LIST_DIR, DIALECT_VISUAL_WORDLIST_DIR]:
                self.main_window.open_in_wordlist_editor(path)
                return

        # 检查是否是日志文件 (log.txt 通常在 Results/common/participant_X/ 或 Results/visual/participant_X-wordlist/ 目录下)
        if filename == 'log.txt':
            # 进一步判断，确保是结果目录下的日志文件
            if os.path.commonpath([path, os.path.join(BASE_PATH, "Results")]) == os.path.join(BASE_PATH, "Results"):
                 self.main_window.open_in_log_viewer(path)
                 return
            
        # 对于所有其他文件（包括音频文件），都使用系统默认程序打开
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

    # --- 回收站管理方法 ---
    def _delete_items(self, paths):
        """
        将选中的项目移动到回收站或永久删除。
        根据当前目录是否为回收站，决定执行软删除或硬删除。
        """
        # --- [核心修复] ---
        # 1. 记录删除操作开始时的当前目录
        current_dir_before_delete = self._get_current_dir()
        
        is_in_trash = current_dir_before_delete == self.trash_path
        
        if is_in_trash:
            reply = QMessageBox.question(self, "永久删除", f"您确定要永久删除这 {len(paths)} 个项目吗？\n此操作不可撤销。",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        else:
            reply = QMessageBox.question(self, "删除到回收站", f"您确定要将这 {len(paths)} 个项目移动到回收站吗？",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) # 默认选中“是”
        
        if reply != QMessageBox.Yes: return

        metadata = self._load_trash_metadata() # 加载回收站元数据
        needs_tree_refresh = False # 标记是否需要刷新左侧导航树

        for path in paths:
            try:
                if os.path.isdir(path): # 如果删除的是文件夹，可能需要刷新树
                    needs_tree_refresh = True
                
                if is_in_trash:
                    # 如果已经在回收站，则执行永久删除
                    if os.path.isdir(path): shutil.rmtree(path)
                    else: os.remove(path)
                    metadata.pop(os.path.basename(path), None) # 从元数据中移除记录
                else:
                    # 移动到回收站，添加时间戳避免名称冲突
                    trash_name = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{os.path.basename(path)}"
                    shutil.move(path, os.path.join(self.trash_path, trash_name))
                    # 在元数据中记录原始路径和删除时间
                    metadata[trash_name] = {"original_path": path, "deleted_time": datetime.now().isoformat()}
            except Exception as e:
                QMessageBox.critical(self, "操作失败", f"无法删除 '{os.path.basename(path)}':\n{e}")
        
        self._save_trash_metadata(metadata) # 保存更新后的元数据
        
        # 刷新UI以反映删除操作
        if needs_tree_refresh:
            self._populate_nav_tree() # 重建左侧树
        
        # 2. 尝试恢复到删除前的目录
        # 如果删除的是当前目录本身，或者当前目录在刷新后不存在了，
        # 则回退到其父目录，或者回收站（如果是从回收站删除）
        target_dir_after_delete = current_dir_before_delete
        if not os.path.exists(target_dir_after_delete):
            parent_dir = os.path.dirname(current_dir_before_delete)
            if os.path.exists(parent_dir):
                target_dir_after_delete = parent_dir
            elif is_in_trash: # 如果是从回收站删除，且父目录也不存在，就回到回收站根目录
                target_dir_after_delete = self.trash_path
            else: # 否则就回到项目根目录
                target_dir_after_delete = BASE_PATH

        self._sync_nav_tree_to_path(target_dir_after_delete)
        self._populate_file_view(target_dir_after_delete) # 刷新右侧文件列表

    def _restore_items(self, paths_in_trash):
        """
        将回收站中的项目恢复到其原始位置。
        """
        metadata = self._load_trash_metadata()
        restored_count = 0
        for path in paths_in_trash:
            trash_name = os.path.basename(path)
            if trash_name in metadata:
                original_path = metadata[trash_name]["original_path"]
                dest_dir = os.path.dirname(original_path)
                try:
                    # 确保目标目录存在
                    os.makedirs(dest_dir, exist_ok=True)
                    # 移动文件回原始位置
                    shutil.move(path, original_path)
                    metadata.pop(trash_name) # 从元数据中移除记录
                    restored_count += 1
                except Exception as e:
                    QMessageBox.critical(self, "恢复失败", f"无法恢复 '{trash_name}' 到 '{original_path}':\n{e}")
            else:
                QMessageBox.warning(self, "恢复失败", f"找不到 '{trash_name}' 的原始位置信息。")
        
        self._save_trash_metadata(metadata) # 保存更新后的元数据
        
        # 刷新UI以反映恢复操作
        if restored_count > 0:
            self._populate_nav_tree() # 刷新树（以防恢复了文件夹）
            self._populate_file_view(self.trash_path) # 刷新回收站视图
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
        
        # 只有在拖入了文件夹时才刷新左侧导航树
        if needs_tree_refresh:
            self._populate_nav_tree()
        
        # 刷新右侧文件视图，以显示新导入的文件
        self._populate_file_view(dest_dir)

        # 强制将导航树的选中项恢复到操作前的位置，确保UI焦点不变
        self._sync_nav_tree_to_path(current_selected_path_before_drop)