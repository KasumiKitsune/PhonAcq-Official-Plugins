
# --- START OF FILE plugins/flashcard_manager/manager_main.py ---

import os
import sys
import json
import shutil
import subprocess
import uuid
import copy
from datetime import datetime

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QStackedWidget, QWidget,
    QSplitter, QMessageBox, QFileDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QFormLayout, QTextEdit,
    QMenu, QGroupBox, QLineEdit, QTabWidget, QInputDialog,
    QSpacerItem, QSizePolicy, QStyledItemDelegate
)
from PyQt5.QtCore import Qt, QSize, QUrl
from PyQt5.QtGui import QPixmap, QKeySequence, QPainter, QPalette
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent

# 动态导入 PhonAcq Assistant 核心模块
try:
    from plugin_system import BasePlugin
    from modules.custom_widgets_module import AnimatedListWidget
except ImportError:
    # 兼容独立运行或调试插件的情况，将项目根目录添加到 sys.path
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from modules.plugin_system import BasePlugin
    from modules.custom_widgets_module import AnimatedListWidget


# ==============================================================================
# 自定义委托：EditableListDelegate (用于 QListWidget 行内编辑)
# ==============================================================================
class EditableListDelegate(QStyledItemDelegate):
    """
    一个自定义的 QStyledItemDelegate，用于 QListWidget。
    其主要目的是确保当用户在 QListWidget 项目上进行行内编辑时，
    QLineEdit 编辑器能够正确地填充整个项目区域，避免文本被裁切。
    """
    def createEditor(self, parent, option, index):
        """
        当用户开始编辑单元格时调用。
        默认行为是创建一个适合数据类型的编辑器（通常是QLineEdit）。
        """
        editor = super().createEditor(parent, option, index)
        return editor

    def updateEditorGeometry(self, editor, option, index):
        """
        [核心修复] 重写此方法以设置编辑器的位置和大小。
        我们将编辑器的几何形状强制设置为与其所在的列表项（item）相同的矩形，
        从而使编辑器填满整个项目空间，解决宽度被裁切的问题。
        """
        editor.setGeometry(option.rect)


# ==============================================================================
# ScalableImageLabel 类 (用于图片预览，自动缩放)
# ==============================================================================
class ScalableImageLabel(QLabel):
    """
    一个自定义的 QLabel，能够自动缩放其显示的 QPixmap 内容，
    以适应 QLabel 自身的当前大小，并保持图片的原始宽高比。
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pixmap = QPixmap()  # 存储原始 QPixmap
        self.setAlignment(Qt.AlignCenter)  # 居中对齐
        self.setMinimumSize(1, 1)  # 确保 QLabel 即使在没有内容时也有最小尺寸

    def set_pixmap(self, pixmap):
        """
        设置要在此 QLabel 中显示的 QPixmap。
        如果传入 None 或无效的 QPixmap，则清空当前显示。
        """
        if pixmap and not pixmap.isNull():
            self.pixmap = pixmap
        else:
            self.pixmap = QPixmap()  # 设置为空 QPixmap
        self.update()  # 强制重绘 QLabel

    def paintEvent(self, event):
        """
        重写 Qt 的 paintEvent 方法，以实现自定义的绘制逻辑。
        如果存在 QPixmap，则绘制缩放后的图像；否则调用父类方法绘制文本。
        """
        if self.pixmap.isNull():
            # 如果没有要绘制的图像，则调用父类的 paintEvent 来绘制 QLabel 的文本内容
            super().paintEvent(event)
            return

        # 获取 QLabel 的当前尺寸
        label_size = self.size()
        
        # 将原始 QPixmap 缩放到 QLabel 的尺寸，同时保持宽高比，并进行平滑变换
        scaled_pixmap = self.pixmap.scaled(label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        # 计算绘制图像的起始坐标，使其在 QLabel 内部居中
        x = (self.width() - scaled_pixmap.width()) / 2
        y = (self.height() - scaled_pixmap.height()) / 2
        
        # 使用 QPainter 在 QLabel 上绘制缩放后的图像
        painter = QPainter(self)
        painter.drawPixmap(int(x), int(y), scaled_pixmap)

    def resizeEvent(self, event):
        """
        当 QLabel 的大小改变时调用。
        在此处触发重新绘制，以确保图像始终适应新的 QLabel 尺寸。
        """
        self.update()


# ==============================================================================
# FlashcardManagerPlugin - 插件主入口类
# ==============================================================================
class FlashcardManagerPlugin(BasePlugin):
    """
    PhonAcq Assistant 的速记卡管理器插件的入口点。
    负责在插件系统加载时进行初始化，并在执行时创建和管理 DeckStudioDialog 实例。
    """
    def __init__(self, main_window, plugin_manager):
        """
        构造函数。
        :param main_window: 对主应用程序窗口的引用。
        :param plugin_manager: 对插件管理器的引用。
        """
        super().__init__(main_window, plugin_manager)
        self.manager_dialog = None  # 用于存储 DeckStudioDialog 的实例

    def setup(self):
        """
        插件启用时调用。
        在此处进行插件的初始化设置。
        """
        # 返回 True 表示插件成功设置
        return True

    def teardown(self):
        """
        插件禁用时调用。
        在此处进行插件的清理工作，确保资源被正确释放。
        """
        # 如果对话框实例存在，则关闭它
        if self.manager_dialog:
            self.manager_dialog.close()

    def execute(self, **kwargs):
        """
        [vFinal] 插件的主要执行逻辑。
        现在可以接受一个 'fdeck_path' 参数来直接加载指定的卡组。
        """
        # 从 kwargs 中获取由文件管理器传递过来的路径
        fdeck_path = kwargs.get("fdeck_path")

        # 如果对话框尚未创建
        if self.manager_dialog is None:
            # 在创建时传入初始路径
            self.manager_dialog = DeckStudioDialog(self.main_window, initial_deck_path=fdeck_path)
            self.manager_dialog.finished.connect(self._on_dialog_closed)
            self.manager_dialog.show()
        else:
            # 如果对话框已存在，并且收到了新的路径
            if fdeck_path:
                # 调用一个新的公共方法来加载指定的卡组
                self.manager_dialog.load_deck_from_path(fdeck_path)
        
        # 激活并置顶对话框
        self.manager_dialog.raise_()
        self.manager_dialog.activateWindow()

    def _on_dialog_closed(self):
        """
        当 DeckStudioDialog 关闭时，此槽函数会被调用。
        用于清理对话框创建的临时文件和重置插件内部对对话框的引用。
        """
        if self.manager_dialog:
            self.manager_dialog.cleanup_temp_dir()  # 清理临时目录
        self.manager_dialog = None  # 清空对话框实例的引用


# ==============================================================================
# DeckStudioDialog - 速记卡组工作室对话框
# ==============================================================================
class DeckStudioDialog(QDialog):
    """
    一个功能完备的 .fdeck 卡组编辑器对话框。
    提供卡组的创建、加载、元数据编辑、卡片增删改查、媒体文件关联及批量操作。
    采用“解包-编辑-打包”工作流，并通过快照对比确保数据完整性。
    """
    def __init__(self, parent=None, initial_deck_path=None):
        """
        [vFinal] 构造函数。
        新增 'initial_deck_path' 参数以支持启动时直接加载。
        """
        super().__init__(parent)
        self.parent_window = parent
        self.icon_manager = self.parent_window.icon_manager
        self.FLASHCARDS_DIR = os.path.join(self.parent_window.BASE_PATH, "flashcards")
        self.TEMP_DIR = os.path.join(self.FLASHCARDS_DIR, ".manager_temp")
        os.makedirs(self.FLASHCARDS_DIR, exist_ok=True)
        self.cleanup_temp_dir()
        
        # --- 状态管理 ---
        self.current_deck_path = None
        self.working_dir = None
        self.manifest_data = {}
        self.initial_manifest_state = None
        self.ADD_NEW_CARD_ROLE = Qt.UserRole + 101
        # [新增] 存储初始加载路径
        self.initial_deck_path = initial_deck_path

        # --- 多媒体 ---
        self.player = QMediaPlayer(self)

        # --- UI 初始化 ---
        self.setWindowTitle("速记卡组管理器")
        self.setGeometry(150, 150, 1300, 900)
        self.setMinimumSize(1200, 800)
        self._init_ui()
        self._connect_signals()
        self.populate_deck_list()
        # [新增] 在填充列表后，尝试加载初始卡组
        self._load_initial_deck()
        self._update_ui_state()

    # --- UI 构建方法 ---

    def _init_ui(self):
        """构建对话框的整体用户界面布局。"""
        main_layout = QVBoxLayout(self)  # 主布局为垂直布局
        splitter = QSplitter(Qt.Horizontal)  # 使用水平分割器，允许用户调整左右面板宽度

        # 创建左侧面板 (卡组列表与操作)
        left_panel = self._create_left_panel()
        # 创建右侧面板 (编辑器核心区)
        right_panel = self._create_right_panel()

        # 将左右面板添加到分割器
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        # 设置分割器的初始尺寸比例：左侧 300px，右侧 1100px (总宽度 1400px)
        splitter.setSizes([300, 1100])  
        
        # 将分割器添加到主布局
        main_layout.addWidget(splitter)

    def _create_left_panel(self):
        widget = QWidget()
        # [关键修复 3] 设置卡组列表面板的固定宽度为 250px
        widget.setFixedWidth(250)
        
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("<b>速记卡组 (.fdeck)</b>"))
        self.deck_list_widget = AnimatedListWidget()
        self.deck_list_widget.setToolTip("在 'flashcards' 文件夹中找到的所有 .fdeck 卡组包。")
        self.deck_list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        
        self.new_deck_btn = QPushButton("新建卡组")
        self.new_deck_btn.setIcon(self.icon_manager.get_icon("add_row"))
        
        # [关键修复 2] 将 Save 按钮移到此处
        self.save_deck_btn = QPushButton("保存对卡组的更改")
        self.save_deck_btn.setObjectName("AccentButton")
        self.save_deck_btn.setIcon(self.icon_manager.get_icon("save_2"))

        layout.addWidget(self.deck_list_widget, 1)
        layout.addWidget(self.new_deck_btn)
        layout.addWidget(self.save_deck_btn) # Save 按钮在 New 下方
        return widget

    def _create_right_panel(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.editor_tabs = QTabWidget()
        self.editor_tabs.setObjectName("SubTabWidget")
        metadata_widget = self._create_metadata_tab()
        self.editor_tabs.addTab(metadata_widget, "卡组信息 (元数据)")
        card_editor_widget = self._create_card_editor_tab()
        self.editor_tabs.addTab(card_editor_widget, "卡片编辑器")
        raw_editor_widget = self._create_raw_editor_tab()
        self.editor_tabs.addTab(raw_editor_widget, "源码")
        
        # [关键修复 2] 移除 Save 按钮的创建和添加
        # self.save_deck_btn = QPushButton("保存对卡组的更改") ...
        
        layout.addWidget(self.editor_tabs)
        # layout.addWidget(self.save_deck_btn)
        return widget

    def _create_metadata_tab(self):
        """创建“卡组信息 (元数据)”标签页的UI。"""
        widget = QWidget()
        layout = QFormLayout(widget)  # 表单布局，键值对对齐
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 元数据编辑字段 (QLineEdit 和 QTextEdit)
        self.meta_deck_name_edit = QLineEdit()
        self.meta_author_edit = QLineEdit()
        self.meta_desc_edit = QTextEdit()
        self.meta_desc_edit.setFixedHeight(120)
        
        # UUID 标签，设置为只读和无边框，方便用户复制文本
        self.meta_id_label = QLineEdit("<i>未加载卡组</i>")
        self.meta_id_label.setReadOnly(True)
        self.meta_id_label.setFrame(False)  # 移除边框
        self.meta_id_label.setStyleSheet("background: transparent;")  # 透明背景，使其融入布局
        
        # [新增功能] 用于显示当前卡组 capabilities 的 QLabel
        self.meta_capabilities_label = QLabel("<i>N/A</i>")
        self.meta_capabilities_label.setToolTip("根据卡组内容自动检测的能力。")

        # 添加到表单布局
        layout.addRow("<b>卡组名称:</b>", self.meta_deck_name_edit)
        layout.addRow("<b>作者:</b>", self.meta_author_edit)
        layout.addRow("<b>描述:</b>", self.meta_desc_edit)
        layout.addRow("<b>卡组ID:</b>", self.meta_id_label)
        layout.addRow("<b>能力:</b>", self.meta_capabilities_label)  # 添加 capabilities 显示行
        
        # 添加一个弹性空间，将所有表单项推到顶部，避免底部留白过多
        layout.addItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))
        return widget

    def _create_card_editor_tab(self):
        """创建“卡片编辑器”标签页的UI，它使用一个内嵌的水平分割器。"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 10, 0, 0)
        
        splitter = QSplitter(Qt.Horizontal)  # 卡片列表和详情编辑器之间使用分割器

        # 左侧：卡片列表面板 (包含 QListWidget)
        card_list_panel = self._create_card_list_panel()
        # 右侧：卡片详情编辑器面板 (包含单个卡片的编辑字段)
        card_detail_panel = self._create_card_detail_editor()
        
        # 将卡片列表和详情面板添加到分割器
        splitter.addWidget(card_list_panel)
        splitter.addWidget(card_detail_panel)
        
        # 将分割器添加到标签页布局
        layout.addWidget(splitter)
        return widget

    def _create_card_list_panel(self):
        widget = QWidget()
        # [关键修复 3] 设置卡片列表面板的固定宽度为 250px
        widget.setFixedWidth(250)
        
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("<b>卡片列表</b>"))
        self.card_list_widget = QListWidget()
        self.card_list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.card_list_delegate = EditableListDelegate(self.card_list_widget)
        self.card_list_widget.setItemDelegate(self.card_list_delegate)
        layout.addWidget(self.card_list_widget, 1)
        return widget

    def _create_card_detail_editor(self):
        widget = QWidget()
        main_layout = QVBoxLayout(widget)
        
        text_group = QGroupBox("文本信息")
        text_layout = QFormLayout(text_group)
        self.card_id_edit = QLineEdit()
        self.card_question_edit = QLineEdit()
        self.card_answer_edit = QLineEdit()
        self.card_answer_edit.setToolTip("支持多个答案，请用双竖线 '||' 分隔。")
        self.card_hint_edit = QTextEdit()
        self.card_hint_edit.setFixedHeight(80)
        text_layout.addRow("<b>卡片ID (唯一):</b>", self.card_id_edit)
        text_layout.addRow("<b>问题:</b>", self.card_question_edit)
        text_layout.addRow("<b>答案 (用 '||' 分隔):</b>", self.card_answer_edit)
        text_layout.addRow("<b>提示:</b>", self.card_hint_edit)
        
        media_group = QGroupBox("媒体资源")
        media_layout = QHBoxLayout(media_group)
        
        image_panel = QWidget()
        image_layout = QVBoxLayout(image_panel)
        self.card_image_preview = ScalableImageLabel("无图片")
        self.card_image_preview.setMinimumSize(200, 150)
        img_btn_layout = QHBoxLayout()
        self.set_image_btn = QPushButton("设置图片")
        self.clear_image_btn = QPushButton("清除图片")
        img_btn_layout.addWidget(self.set_image_btn)
        img_btn_layout.addWidget(self.clear_image_btn)
        image_layout.addWidget(self.card_image_preview, 1)
        image_layout.addLayout(img_btn_layout)

        audio_main_panel = QWidget()
        audio_main_layout = QVBoxLayout(audio_main_panel)
        
        # 单词音频区
        word_audio_group = QGroupBox("单词音频")
        word_audio_layout = QVBoxLayout(word_audio_group)
        self.card_audio_label = QLabel("无音频文件")
        self.card_audio_label.setAlignment(Qt.AlignCenter)
        self.card_audio_label.setWordWrap(True)
        
        # [关键修复] 将三个按钮放在一个水平布局中
        word_audio_btn_layout = QHBoxLayout()
        self.play_audio_btn = QPushButton("播放")
        self.set_audio_btn = QPushButton("设置")
        self.clear_audio_btn = QPushButton("清除")
        word_audio_btn_layout.addWidget(self.play_audio_btn)
        word_audio_btn_layout.addWidget(self.set_audio_btn)
        word_audio_btn_layout.addWidget(self.clear_audio_btn)
        
        word_audio_layout.addWidget(self.card_audio_label, 1)
        word_audio_layout.addLayout(word_audio_btn_layout)

        # 例句音频区
        sentence_audio_group = QGroupBox("例句音频")
        sentence_audio_layout = QVBoxLayout(sentence_audio_group)
        self.sentence_audio_label = QLabel("无例句音频")
        self.sentence_audio_label.setAlignment(Qt.AlignCenter)
        self.sentence_audio_label.setWordWrap(True)
        
        # [关键修复] 将三个按钮放在一个水平布局中
        sentence_audio_btn_layout = QHBoxLayout()
        self.play_sentence_audio_btn = QPushButton("播放")
        self.set_sentence_audio_btn = QPushButton("设置")
        self.clear_sentence_audio_btn = QPushButton("清除")
        sentence_audio_btn_layout.addWidget(self.play_sentence_audio_btn)
        sentence_audio_btn_layout.addWidget(self.set_sentence_audio_btn)
        sentence_audio_btn_layout.addWidget(self.clear_sentence_audio_btn)
        
        sentence_audio_layout.addWidget(self.sentence_audio_label, 1)
        sentence_audio_layout.addLayout(sentence_audio_btn_layout)

        audio_main_layout.addWidget(word_audio_group)
        audio_main_layout.addWidget(sentence_audio_group)

        media_layout.addWidget(image_panel)
        media_layout.addWidget(audio_main_panel)
        main_layout.addWidget(text_group)
        main_layout.addWidget(media_group)
        main_layout.addStretch()
        return widget

    def _create_raw_editor_tab(self):
        """[vFinal] 创建“源码 (manifest.json)”标签页的UI，现在为只读模式。"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 10, 10, 10)

        # [关键修复 1] 将 QTextEdit 设置为只读
        self.raw_json_edit = QTextEdit()
        self.raw_json_edit.setReadOnly(True)
        self.raw_json_edit.setPlaceholderText("加载卡组后，此处将显示 manifest.json 的源码（只读）。")
        self.raw_json_edit.setFontFamily("Courier New")
        
        # [移除] 不再需要警告标签
        # warning_label = QLabel(...)
        # layout.addWidget(warning_label)
        
        layout.addWidget(self.raw_json_edit, 1)

        return widget

    def _connect_signals(self):
        # ... (大部分连接保持不变) ...
        self.deck_list_widget.currentItemChanged.connect(self._on_deck_selected)
        self.deck_list_widget.customContextMenuRequested.connect(self._show_deck_context_menu)
        self.new_deck_btn.clicked.connect(self._create_new_deck)
        self.save_deck_btn.clicked.connect(self._save_current_deck)
        self.editor_tabs.currentChanged.connect(self._on_tab_changed)
        self.card_list_widget.currentItemChanged.connect(self._on_card_selected)
        self.card_list_widget.itemClicked.connect(self._on_card_list_item_clicked)
        self.card_list_widget.itemChanged.connect(self._on_card_id_renamed)
        self.card_list_widget.customContextMenuRequested.connect(self._show_card_context_menu)
        
        self.set_image_btn.clicked.connect(self._set_card_image)
        self.clear_image_btn.clicked.connect(self._clear_card_image)
        
        # [修改] 明确指定音频类型
        self.play_audio_btn.clicked.connect(lambda: self._play_card_audio('word'))
        self.set_audio_btn.clicked.connect(lambda: self._set_card_audio('word'))
        self.clear_audio_btn.clicked.connect(lambda: self._clear_card_audio('word'))

        # [新增] 连接例句音频按钮的信号
        self.play_sentence_audio_btn.clicked.connect(lambda: self._play_card_audio('sentence'))
        self.set_sentence_audio_btn.clicked.connect(lambda: self._set_card_audio('sentence'))
        self.clear_sentence_audio_btn.clicked.connect(lambda: self._clear_card_audio('sentence'))
        
        for editor in [self.meta_deck_name_edit, self.meta_author_edit, self.meta_desc_edit,
                       self.card_id_edit, self.card_question_edit, self.card_answer_edit,
                       self.card_hint_edit, self.raw_json_edit]:
            # ... (信号连接逻辑保持不变) ...
            if isinstance(editor, QLineEdit): editor.textChanged.connect(self._on_ui_changed)
            elif isinstance(editor, QTextEdit): editor.textChanged.connect(self._on_ui_changed)

    # --- UI 状态与数据填充 ---

    def populate_deck_list(self):
        """扫描 'flashcards' 目录并动画化填充卡组列表。"""
        self.deck_list_widget.clear()  # 清空现有列表
        # 查找所有 .fdeck 文件并按名称排序
        decks = [f for f in os.listdir(self.FLASHCARDS_DIR) if f.endswith('.fdeck')]
        self.deck_list_widget.addItemsWithAnimation(sorted(decks))  # 动画添加项目

    def _on_ui_changed(self):
        """
        当任何可编辑的UI字段（除了列表项编辑）发生变化时调用的轻量级槽。
        它只负责触发UI状态的重新评估，不会导致递归。
        """
        self._update_ui_state()

    def _update_ui_state(self):
        """
        根据当前卡组加载状态和“脏”状态，更新UI组件的启用/禁用状态。
        每次UI内容或卡组选择发生变化时调用。
        """
        is_deck_loaded = self.current_deck_path is not None  # 判断是否有卡组被加载
        
        # 标签页和保存按钮的启用状态，由精确的脏状态检查决定
        self.editor_tabs.setEnabled(is_deck_loaded)
        self.save_deck_btn.setEnabled(self._check_if_dirty())
        
        # 如果没有卡组加载，清空所有编辑器
        if not is_deck_loaded: 
            self._clear_all_editors()

    def _clear_all_editors(self):
        """清空所有编辑器字段和卡片列表。"""
        # 定义所有文本编辑器控件
        editors = [self.meta_deck_name_edit, self.meta_author_edit, self.meta_desc_edit,
                   self.card_id_edit, self.card_question_edit, self.card_answer_edit,
                   self.card_hint_edit, self.raw_json_edit] # 包含新的 raw_json_edit

        # 在清空UI内容前，阻塞所有相关编辑器的信号，防止触发 textChanged 信号
        for editor in editors: 
            editor.blockSignals(True)
        
        # 清空元数据编辑器
        self.meta_deck_name_edit.clear()
        self.meta_author_edit.clear()
        self.meta_desc_edit.clear()
        self.meta_id_label.setText("<i>未加载卡组</i>")
        self.meta_capabilities_label.setText("<i>N/A</i>") # 清理 capabilities 标签
        
        # 清空源码编辑器
        self.raw_json_edit.clear()
        
        # 清空卡片列表和详情编辑器
        self.card_list_widget.clear()
        self._clear_card_detail_editor()  # 这个方法内部也会阻塞信号
        
        # 恢复所有编辑器的信号
        for editor in editors: 
            editor.blockSignals(False)

    def _clear_card_detail_editor(self):
        # ... (前半部分保持不变) ...
        for w in [self.card_id_edit, self.card_question_edit, self.card_answer_edit, self.card_hint_edit]:
            w.blockSignals(True)
        self.card_id_edit.clear(); self.card_question_edit.clear(); self.card_answer_edit.clear(); self.card_hint_edit.clear()
        for w in [self.card_id_edit, self.card_question_edit, self.card_answer_edit, self.card_hint_edit]:
            w.blockSignals(False)
        self.card_image_preview.set_pixmap(None); self.card_image_preview.setText("无图片")
        
        # [修改] 清理两个音频标签和按钮状态
        self.card_audio_label.setText("无音频文件"); self.play_audio_btn.setEnabled(False)
        self.sentence_audio_label.setText("无例句音频"); self.play_sentence_audio_btn.setEnabled(False)

    def _add_placeholder_card(self):
        """
        在卡片列表末尾添加一个特殊的“＋ 添加新卡片…”占位符。
        模仿 `wordlist_editor` 的“点击添加”功能，提供友好的用户引导。
        """
        placeholder_item = QListWidgetItem("＋ 添加新卡片…")
        # 将特殊用户数据关联到此项，用于识别
        placeholder_item.setData(self.ADD_NEW_CARD_ROLE, True)
        # 设置为灰色文字，表示是提示信息
        placeholder_item.setForeground(self.palette().color(QPalette.Disabled, QPalette.Text))
        # 设置标志，使其不可选中、不可编辑、不可拖拽等，仅可点击
        flags = placeholder_item.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsEnabled
        placeholder_item.setFlags(flags)
        self.card_list_widget.addItem(placeholder_item)

    def _remove_placeholder_card(self):
        """
        安全地移除卡片列表末尾的占位符行（如果存在）。
        在添加/删除卡片前调用，以保持列表的正确结构。
        """
        count = self.card_list_widget.count()
        if count > 0:
            last_item = self.card_list_widget.item(count - 1)
            # 检查最后一项是否是我们的占位符
            if last_item and last_item.data(self.ADD_NEW_CARD_ROLE):
                self.card_list_widget.takeItem(count - 1)  # 移除它
                
    def _on_card_list_item_clicked(self, item):
        """
        处理卡片列表项的单击事件。
        如果点击的是“添加新卡片”占位符，则触发添加新卡片逻辑。
        """
        if item and item.data(self.ADD_NEW_CARD_ROLE): 
            self._add_new_card()

    def _on_tab_changed(self, index):
        """[vFinal] 当用户切换编辑器标签页时触发。现在只处理切换到源码页的逻辑。"""
        if not self.manifest_data:
            return

        current_tab_title = self.editor_tabs.tabText(index)
        
        # 只有在切换到“源码”标签页时，才执行操作
        if current_tab_title == "源码":
            # 将当前UI字段的数据同步到内存模型
            self._save_ui_to_manifest()
            try:
                # 将内存模型序列化并显示在源码编辑器中
                json_string = json.dumps(self.manifest_data, indent=2, ensure_ascii=False)
                self.raw_json_edit.setPlainText(json_string)
            except Exception as e:
                self.raw_json_edit.setPlainText(f"无法序列化为JSON: \n{e}")


    # --- 核心数据处理和状态比较 ---

    def _check_if_dirty(self):
        """[vFinal] 通过对比当前 manifest 和初始状态来精确判断是否有未保存的更改。"""
        if self.initial_manifest_state is None:
            return False
        
        # 总是从UI字段构建数据进行比较
        current_manifest = self._build_manifest_from_ui()
            
        return self.initial_manifest_state != current_manifest

    def _build_manifest_from_ui(self):
        """
        [vFinal] 从当前UI控件中的数据构建一个临时的 manifest 字典。
        此方法仅用于脏状态对比，绝不修改任何UI或实例变量。
        它是一个“快照生成器”。
        """
        # 如果没有加载卡组，则返回空字典
        if not self.manifest_data: 
            return {}
            
        # 1. 创建 self.manifest_data 的深拷贝，作为构建的基础副本
        ui_manifest_copy = copy.deepcopy(self.manifest_data)
        
        # 2. 从元数据UI控件中获取最新值并更新到副本
        if 'meta' not in ui_manifest_copy: ui_manifest_copy['meta'] = {}
        ui_manifest_copy['meta']['deck_name'] = self.meta_deck_name_edit.text()
        ui_manifest_copy['meta']['deck_author'] = self.meta_author_edit.text()
        ui_manifest_copy['meta']['deck_description'] = self.meta_desc_edit.toPlainText()
        
        # 3. 将当前卡片详情编辑器中的值更新到副本中对应的卡片
        current_item = self.card_list_widget.currentItem()
        # 确保当前选中项不是占位符，且有效
        if current_item and not current_item.data(self.ADD_NEW_CARD_ROLE):
            idx = self.card_list_widget.row(current_item)
            if 0 <= idx < len(ui_manifest_copy.get('cards', [])):
                card = ui_manifest_copy['cards'][idx]
                # 更新卡片的各个字段
                card['id'] = self.card_id_edit.text()
                card['question'] = self.card_question_edit.text()
                card['answer'] = self.card_answer_edit.text()
                card['hint'] = self.card_hint_edit.toPlainText()

        return ui_manifest_copy

    def _update_capabilities(self):
        if not self.manifest_data: return
        cards = self.manifest_data.get('cards', [])
        new_caps = {"text", "text_input"}
        for card in cards:
            if card.get('image_path'): new_caps.add('image')
            if card.get('audio_path'): new_caps.add('audio')
            # [新增] 检测例句音频路径
            if card.get('sentence_audio_path'): new_caps.add('sentence_audio')
        
        meta = self.manifest_data.setdefault('meta', {})
        meta['capabilities'] = sorted(list(new_caps))


    # --- 核心工作流逻辑：卡组加载与保存 ---
    
    def _on_deck_selected(self, current, previous):
        """
        [vFinal] 当用户在左侧卡组列表中选择一个新卡组时触发。
        处理未保存的更改提示，并加载新文件。
        这是卡组状态切换的核心逻辑。
        """
        # 如果选择的是同一个项目，不做任何事
        if current and previous and current.text() == previous.text(): 
            return

        # 步骤 1: 检查旧卡组的“脏”状态并处理用户决策
        # 只有在有 previous 项时才执行此检查，避免程序启动时对 None 进行检查
        if previous and self._check_if_dirty():
            reply = QMessageBox.question(self, "未保存的更改", 
                                       "当前卡组有未保存的更改。要先保存吗？",
                                       QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
            
            if reply == QMessageBox.Save: 
                self._save_current_deck()  # 用户选择保存
            elif reply == QMessageBox.Cancel:
                # 用户取消操作，恢复到之前的选中状态，并阻止 currentItemChanged 信号再次触发
                self.deck_list_widget.blockSignals(True)
                self.deck_list_widget.setCurrentItem(previous)
                self.deck_list_widget.blockSignals(False)
                return  # 中止操作

        # 步骤 2: 彻底重置所有状态，为加载新卡组做准备
        # 这是防止数据污染的关键步骤：清除所有旧数据的痕迹。
        self.manifest_data = {}  # 清空内存中的 manifest 数据
        self.initial_manifest_state = None  # 清空初始状态快照
        self.current_deck_path = None  # 清空当前卡组路径
        self._clear_all_editors()  # 清空所有UI编辑器内容

        # 步骤 3: 加载新卡组
        if not current:  # 如果当前没有选中任何卡组 (例如删除了当前卡组)
            self.cleanup_temp_dir()  # 清理临时工作目录
            self._update_ui_state()  # 更新UI到“未加载”状态
            return
            
        # 确定新选中的卡组路径
        self.current_deck_path = os.path.join(self.FLASHCARDS_DIR, current.text())
        
        # 解包卡组到临时目录，加载 manifest 到UI，并更新UI状态
        self._unpack_current_deck()
        # _load_manifest_to_ui 会处理 manifest_data 和 initial_manifest_state 的填充
        self._load_manifest_to_ui() 
        self._update_ui_state() # 最终更新UI状态 (保存按钮应根据实际脏状态启用/禁用)
        
    def _unpack_current_deck(self):
        """解包当前选中的 .fdeck 文件到临时工作目录。"""
        self.cleanup_temp_dir()  # 先清理旧的临时目录，确保干净的工作环境
        # 创建一个新的、唯一的临时工作目录
        self.working_dir = os.path.join(self.TEMP_DIR, str(uuid.uuid4()))
        os.makedirs(self.working_dir, exist_ok=True)
        
        try:
            import zipfile  # 确保 zipfile 模块可用
            with zipfile.ZipFile(self.current_deck_path, 'r') as zf:
                zf.extractall(self.working_dir)  # 解压所有内容到临时目录
        except Exception as e:
            QMessageBox.critical(self, "解包失败", f"无法解压卡组文件：\n{e}")
            self.working_dir = None  # 解包失败则清空工作目录引用

    def _load_manifest_to_ui(self, from_raw_edit=False):
        """
        从工作目录加载 manifest.json 文件内容到内存并填充UI。
        :param from_raw_edit: 如果为 True，表示是从源码编辑器应用更改，此时不重新读取文件，
                              也不设置 initial_manifest_state，因为 manifest_data 已经是最新状态。
        """
        if not self.working_dir: return  # 如果没有工作目录，则无法加载
        
        # 只有在非源码编辑模式下（即初次加载文件），才需要从磁盘读取文件和执行媒体扫描
        if not from_raw_edit:
            manifest_path = os.path.join(self.working_dir, 'manifest.json')
            if not os.path.exists(manifest_path):
                QMessageBox.critical(self, "卡组损坏", "卡组包内缺少 manifest.json 文件。"); return
            
            with open(manifest_path, 'r', encoding='utf-8') as f: 
                self.manifest_data = json.load(f)
            
            # [核心修复] 立即创建原始数据的深拷贝快照，用于脏状态对比
            self.initial_manifest_state = copy.deepcopy(self.manifest_data)
            
            # 在工作副本上进行媒体扫描和修复，并根据结果决定是否提示保存
            media_was_repaired = self._scan_and_update_media_paths()
            if media_was_repaired:
                QMessageBox.information(self, "媒体修复", "检测到并自动修复了部分缺失的媒体文件链接。\n请点击“保存”以更新卡组文件。")
        
        # --- 后续的UI填充逻辑对初次加载和从源码编辑器刷新都执行 ---
        
        # 填充元数据编辑器 (加载时阻塞信号，防止触发 _on_ui_changed)
        for w in [self.meta_deck_name_edit, self.meta_author_edit, self.meta_desc_edit]: 
            w.blockSignals(True)
        meta = self.manifest_data.get('meta', {})
        self.meta_deck_name_edit.setText(meta.get('deck_name', ''))
        self.meta_author_edit.setText(meta.get('deck_author', ''))
        self.meta_desc_edit.setText(meta.get('deck_description', ''))
        self.meta_id_label.setText(meta.get('deck_id', 'N/A'))
        
        # 填充 capabilities 标签
        caps_list = meta.get('capabilities', [])
        self.meta_capabilities_label.setText(", ".join(caps_list))

        for w in [self.meta_deck_name_edit, self.meta_author_edit, self.meta_desc_edit]: 
            w.blockSignals(False)
        
        # 清空卡片列表和详情编辑器，然后重新填充
        self.card_list_widget.clear()
        self._clear_card_detail_editor()
        
        cards = self.manifest_data.get('cards', [])
        for card in cards:
            item = QListWidgetItem(card.get('id', '无ID卡片'))
            item.setFlags(item.flags() | Qt.ItemIsEditable)  # 使所有卡片项都可编辑
            self.card_list_widget.addItem(item)
        self._add_placeholder_card()  # 添加“添加新卡片”占位符

        self._update_ui_state() # 最终更新UI状态 (保存按钮应根据实际脏状态启用/禁用)

    def _save_current_deck(self):
        """[vFinal] 将当前工作目录的内容打包并保存为 .fdeck 文件。"""
        if not self.current_deck_path or not self.working_dir: 
            return

        # [关键修复 1] 保存操作总是从UI字段同步数据到内存模型
        self._save_ui_to_manifest()
        
        # 更新 capabilities 元数据
        self._update_capabilities()
        self.meta_capabilities_label.setText(", ".join(self.manifest_data.get('meta', {}).get('capabilities', [])))
        
        # 将最终的 manifest 写入临时文件
        manifest_path = os.path.join(self.working_dir, 'manifest.json')
        with open(manifest_path, 'w', encoding='utf-8') as f: 
            json.dump(self.manifest_data, f, indent=2, ensure_ascii=False)
        
        # 执行打包
        try:
            import zipfile
            with zipfile.ZipFile(self.current_deck_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(self.working_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, self.working_dir)
                        zf.write(file_path, arcname)
            
            self.initial_manifest_state = copy.deepcopy(self.manifest_data)
            
            self.card_list_widget.blockSignals(True)
            self._remove_placeholder_card()
            for i in range(self.card_list_widget.count()):
                item = self.card_list_widget.item(i)
                new_id = self.manifest_data['cards'][i].get('id', '无ID卡片')
                if item.text() != new_id:
                    item.setText(new_id)
            self._add_placeholder_card()
            self.card_list_widget.blockSignals(False)
            
            self._update_ui_state()
            QMessageBox.information(self, "保存成功", "卡组已成功保存！")

        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"打包卡组文件时出错：\n{e}")

    def _save_ui_to_manifest(self):
        """
        将当前UI编辑器中的数据保存到内存中的 `self.manifest_data` 字典。
        此方法是 UI -> 内存数据同步的桥梁。
        它不负责检查脏状态，只负责将UI的最新状态写入内存数据模型。
        """
        if not self.manifest_data: return
        
        # 1. 保存元数据编辑器中的值
        if 'meta' not in self.manifest_data: self.manifest_data['meta'] = {}
        self.manifest_data['meta']['deck_name'] = self.meta_deck_name_edit.text()
        self.manifest_data['meta']['deck_author'] = self.meta_author_edit.text()
        self.manifest_data['meta']['deck_description'] = self.meta_desc_edit.toPlainText()
        
        # 2. 保存当前正在编辑的卡片详情到 manifest_data
        #    注意：此方法只会保存当前卡片编辑器的内容，而不会遍历所有卡片列表项。
        #    所有卡片列表项的ID变化在 _on_card_id_renamed 中实时更新。
        self._save_current_card_details()

    # --- 卡片编辑逻辑 ---
    
    def _on_card_selected(self, current, previous):
        """
        当在卡片列表中选择一个新卡片时触发。
        保存之前卡片的更改，并填充新卡片的数据。
        """
        # 如果选中了占位符，阻止其被选中并跳过后续逻辑
        if current and current.data(self.ADD_NEW_CARD_ROLE):
            self.card_list_widget.setCurrentItem(previous)  # 恢复之前的选中项
            return

        # 如果有前一个选中项，则保存它的详情数据到内存 manifest
        if previous: 
            self._save_current_card_details(previous_item=previous)

        if not current:  # 如果没有选中任何卡片 (例如删除了最后一项，且没有其他项)
            self._clear_card_detail_editor()  # 清空详情编辑器
            return
            
        self._populate_card_details(current)  # 填充新选中卡片的数据到详情编辑器
        self._update_ui_state()  # 更新UI状态，检查是否有脏数据

    def _populate_card_details(self, item):
        idx = self.card_list_widget.row(item)
        if not (0 <= idx < len(self.manifest_data.get('cards', []))): return
        card = self.manifest_data['cards'][idx]
        
        # ... (文本和图片填充逻辑保持不变) ...
        for w in [self.card_id_edit, self.card_question_edit, self.card_answer_edit, self.card_hint_edit]: w.blockSignals(True)
        self.card_id_edit.setText(card.get('id', '')); self.card_question_edit.setText(card.get('question', ''))
        self.card_answer_edit.setText(card.get('answer', '')); self.card_hint_edit.setText(card.get('hint', ''))
        for w in [self.card_id_edit, self.card_question_edit, self.card_answer_edit, self.card_hint_edit]: w.blockSignals(False)
        img_path = card.get('image_path', '')
        if img_path:
            full_img_path = os.path.join(self.working_dir, img_path)
            pixmap = QPixmap(full_img_path)
            if not pixmap.isNull(): self.card_image_preview.set_pixmap(pixmap)
            else: self.card_image_preview.setText(f"图片丢失:\n{img_path}")
        else: self.card_image_preview.set_pixmap(None); self.card_image_preview.setText("无图片")
        
        # [修改] 分别处理单词和例句音频
        # 单词音频
        audio_path = card.get('audio_path', '')
        if audio_path:
            full_audio_path = os.path.join(self.working_dir, audio_path)
            if os.path.exists(full_audio_path):
                self.card_audio_label.setText(os.path.basename(audio_path)); self.play_audio_btn.setEnabled(True)
            else:
                self.card_audio_label.setText(f"音频丢失:\n{audio_path}"); self.play_audio_btn.setEnabled(False)
        else:
            self.card_audio_label.setText("无音频文件"); self.play_audio_btn.setEnabled(False)
        
        # [新增] 例句音频
        sentence_audio_path = card.get('sentence_audio_path', '')
        if sentence_audio_path:
            full_sentence_path = os.path.join(self.working_dir, sentence_audio_path)
            if os.path.exists(full_sentence_path):
                self.sentence_audio_label.setText(os.path.basename(sentence_audio_path)); self.play_sentence_audio_btn.setEnabled(True)
            else:
                self.sentence_audio_label.setText(f"例句丢失:\n{sentence_audio_path}"); self.play_sentence_audio_btn.setEnabled(False)
        else:
            self.sentence_audio_label.setText("无例句音频"); self.play_sentence_audio_btn.setEnabled(False)

    def _save_current_card_details(self, previous_item=None):
        """
        [vFinal] 将详情编辑器中的数据保存回 `self.manifest_data` 中对应的卡片。
        此方法现在能正确地阻塞 QListWidget 的信号以防止递归。
        """
        if not previous_item:
            previous_item = self.card_list_widget.currentItem()
        
        if not previous_item or not self.manifest_data.get('cards') or previous_item.data(self.ADD_NEW_CARD_ROLE):
            return
            
        idx = self.card_list_widget.row(previous_item)
        if not (0 <= idx < len(self.manifest_data.get('cards', []))):
            return
        
        card = self.manifest_data['cards'][idx]
        card['id'] = self.card_id_edit.text()
        card['question'] = self.card_question_edit.text()
        card['answer'] = self.card_answer_edit.text()
        card['hint'] = self.card_hint_edit.toPlainText()
        
        # [关键修复] 阻塞 QListWidget (而不是 QListWidgetItem) 的信号
        self.card_list_widget.blockSignals(True)
        previous_item.setText(card['id'])
        self.card_list_widget.blockSignals(False)


    def _add_new_card(self):
        # ... (前半部分保持不变) ...
        base_id = "new_card"; i = 1
        new_id = f"{base_id}_{i}"
        # 收集当前卡组中所有已存在的ID，用于唯一性检查
        existing_ids = {c.get('id') for c in self.manifest_data.get('cards', [])}
        while new_id in existing_ids:
            i += 1
            new_id = f"{base_id}_{i}"

        # 2. 创建新卡片数据结构 (初始值为空)
        # [修改] 新卡片结构中增加 sentence_audio_path
        new_card = {"id": new_id, "question": "", "answer": "", "hint": "", "image_path": "", "audio_path": "", "sentence_audio_path": ""}
        # 确保 manifest_data 中有 'cards' 列表
        if 'cards' not in self.manifest_data: self.manifest_data['cards'] = []
        
        # 3. 更新UI和数据模型
        self._remove_placeholder_card()  # 移除旧的占位符
        
        self.manifest_data['cards'].append(new_card)  # 添加到数据模型
        item = QListWidgetItem(new_id)
        item.setFlags(item.flags() | Qt.ItemIsEditable)  # 设为可编辑，允许用户直接重命名
        self.card_list_widget.addItem(item)  # 添加到UI列表
        
        self._add_placeholder_card()  # 重新添加占位符
        
        # 4. 选中新项并立即进入编辑模式
        self.card_list_widget.setCurrentItem(item)  # 选中新添加的卡片
        self.card_list_widget.editItem(item)  # 立即激活行内编辑
        
        self._update_ui_state()  # 更新UI状态，标记为脏

    def _load_initial_deck(self):
        """
        [新增] 如果在对话框启动时提供了初始路径，则自动加载该卡组。
        """
        if self.initial_deck_path and os.path.exists(self.initial_deck_path):
            self.load_deck_from_path(self.initial_deck_path)

    def load_deck_from_path(self, fdeck_path):
        """
        [新增] 一个公共方法，用于从外部（如插件的execute方法）加载指定的卡组。
        """
        if fdeck_path and os.path.exists(fdeck_path):
            filename = os.path.basename(fdeck_path)
            # 在列表中查找与文件名匹配的项
            items = self.deck_list_widget.findItems(filename, Qt.MatchExactly)
            if items:
                # 找到了匹配项，则将其设置为当前选中项。
                # 这将自动触发 _on_deck_selected 信号，从而加载卡组。
                self.deck_list_widget.setCurrentItem(items[0])

    def _on_card_id_renamed(self, item):
        """
        当一个卡片列表项完成编辑（即用户重命名了卡片ID）后触发。
        此槽函数负责验证新ID的有效性并更新内存中的数据模型。
        """
        # 获取编辑后的新ID，并去除首尾空白
        new_id = item.text().strip()
        
        # 获取其在列表中的行号，这对应于其在 manifest_data['cards'] 中的索引
        row = self.card_list_widget.row(item)
        # 检查索引是否有效，防止越界
        if not (0 <= row < len(self.manifest_data.get('cards', []))):
            return

        # 获取原始ID
        old_id = self.manifest_data['cards'][row].get('id')

        # 如果ID没有变化，则什么都不做
        if new_id == old_id:
            return

        # --- 验证新ID的有效性 ---
        # 1. 检查ID是否为空
        if not new_id:
            QMessageBox.warning(self, "ID无效", "卡片ID不能为空。")
            item.blockSignals(True) # 阻塞信号，防止setText再次触发itemChanged
            item.setText(old_id)  # 恢复旧ID
            item.blockSignals(False)
            return
            
        # 2. 检查ID是否在其他卡片中重复
        # 收集除当前卡片外的所有其他卡片ID
        other_ids = {self.manifest_data['cards'][i].get('id') for i in range(len(self.manifest_data['cards'])) if i != row}
        if new_id in other_ids:
            QMessageBox.warning(self, "ID冲突", f"卡片ID '{new_id}' 已存在，请输入一个唯一的ID。")
            item.blockSignals(True) # 阻塞信号
            item.setText(old_id)  # 恢复旧ID
            item.blockSignals(False)
            return

        # --- 更新数据模型 ---
        # 更新内存中的 manifest 数据
        self.manifest_data['cards'][row]['id'] = new_id
        
        # 如果详情编辑器当前显示的是这个卡片，也更新其ID字段
        if self.card_list_widget.currentItem() == item:
            self.card_id_edit.blockSignals(True)  # 阻塞信号
            self.card_id_edit.setText(new_id)
            self.card_id_edit.blockSignals(False)  # 恢复信号

        # 标记为脏并更新UI状态
        self._update_ui_state()

    def _remove_selected_card(self):
        """移除当前选中的卡片。"""
        current_item = self.card_list_widget.currentItem()
        # 如果没有选中项，或者选中项是占位符，则不执行删除
        if not current_item or current_item.data(self.ADD_NEW_CARD_ROLE): return
            
        reply = QMessageBox.question(self, "确认移除", f"确定要移除卡片 '{current_item.text()}' 吗？\n此操作不可撤销。", 
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            row = self.card_list_widget.row(current_item)
            
            self._remove_placeholder_card()  # 移除旧的占位符
            
            # 从数据模型中删除卡片
            if 0 <= row < len(self.manifest_data.get('cards', [])):
                del self.manifest_data['cards'][row]
            self.card_list_widget.takeItem(row)  # 从UI列表中移除
            
            self._add_placeholder_card()  # 重新添加占位符
            
            # 如果列表只剩下占位符（即所有卡片都被删除了），则清空详情编辑器
            if self.card_list_widget.count() == 1 and self.card_list_widget.item(0).data(self.ADD_NEW_CARD_ROLE): 
                self._clear_card_detail_editor()
            
            self._update_ui_state()  # 更新UI状态，标记为脏

    def _duplicate_selected_card(self):
        # ... (前半部分保持不变) ...
        current_item = self.card_list_widget.currentItem()
        if not current_item or current_item.data(self.ADD_NEW_CARD_ROLE): return
        row = self.card_list_widget.row(current_item)
        if not (0 <= row < len(self.manifest_data.get('cards', []))): return
        original_card_data = self.manifest_data['cards'][row]
        new_card_data = copy.deepcopy(original_card_data)
        base_id = original_card_data.get('id', 'copy') + "_copy"; i = 1; new_id = base_id
        existing_ids = {c.get('id') for c in self.manifest_data.get('cards', [])}
        while new_id in existing_ids: new_id = f"{base_id}_{i}"; i += 1
        new_card_data['id'] = new_id
        
        # [修改] 清空所有媒体路径，包括例句音频
        new_card_data['image_path'] = ""
        new_card_data['audio_path'] = ""
        new_card_data['sentence_audio_path'] = ""

        # ... (后续逻辑保持不变) ...
        self._remove_placeholder_card()
        insert_pos = row + 1
        self.manifest_data['cards'].insert(insert_pos, new_card_data)
        new_item = QListWidgetItem(new_id)
        new_item.setFlags(new_item.flags() | Qt.ItemIsEditable)
        self.card_list_widget.insertItem(insert_pos, new_item)
        self._add_placeholder_card()
        self.card_list_widget.setCurrentItem(new_item)
        self._update_ui_state()

    # --- 媒体资源操作 ---

    def _set_card_image(self):
        """为当前卡片设置或更换图片。"""
        current_item = self.card_list_widget.currentItem()
        if not current_item or current_item.data(self.ADD_NEW_CARD_ROLE): return
        row = self.card_list_widget.row(current_item)
        card = self.manifest_data['cards'][row]
        card_id = card['id']

        filepath, _ = QFileDialog.getOpenFileName(self, "选择图片文件", "", "图片文件 (*.png *.jpg *.jpeg *.bmp)")
        if not filepath: return
        
        # [OPTIMIZATION] 在更新前，获取旧的相对路径
        old_relative_path = card.get('image_path', '')

        images_dir = os.path.join(self.working_dir, 'images')
        os.makedirs(images_dir, exist_ok=True)
        ext = os.path.splitext(filepath)[1]
        dest_filename = f"{card_id}{ext}"
        dest_path = os.path.join(images_dir, dest_filename)
        
        try:
            shutil.copy2(filepath, dest_path)
            new_relative_path = os.path.join('images', dest_filename).replace("\\", "/")
            
            # 更新 manifest
            card['image_path'] = new_relative_path
            
            # 更新UI
            self.card_image_preview.set_pixmap(QPixmap(dest_path))
            self._update_ui_state()

            # [OPTIMIZATION] 如果旧文件存在且与新文件不同，则删除旧文件
            if old_relative_path and old_relative_path != new_relative_path:
                old_full_path = os.path.join(self.working_dir, old_relative_path)
                if os.path.exists(old_full_path):
                    os.remove(old_full_path)

        except Exception as e: 
            QMessageBox.critical(self, "设置图片失败", f"无法复制图片文件：\n{e}")

    def _clear_card_image(self):
        """清除当前卡片的图片关联。"""
        current_item = self.card_list_widget.currentItem()
        if not current_item or current_item.data(self.ADD_NEW_CARD_ROLE): return
        row = self.card_list_widget.row(current_item)
        self.manifest_data['cards'][row]['image_path'] = ""
        self.card_image_preview.set_pixmap(None); self.card_image_preview.setText("无图片"); self._update_ui_state()

    def _play_card_audio(self, audioType='word'):
        current_item = self.card_list_widget.currentItem()
        if not current_item or current_item.data(self.ADD_NEW_CARD_ROLE): return
        row = self.card_list_widget.row(current_item); card = self.manifest_data['cards'][row]
        
        # [修改] 根据 audioType 选择正确的路径键
        path_key = 'sentence_audio_path' if audioType == 'sentence' else 'audio_path'
        audio_path = card.get(path_key, '')
        
        if audio_path:
            full_audio_path = os.path.join(self.working_dir, audio_path)
            if os.path.exists(full_audio_path):
                self.player.setMedia(QMediaContent(QUrl.fromLocalFile(full_audio_path))); self.player.play()

    def _set_card_audio(self, audioType='word'):
        current_item = self.card_list_widget.currentItem()
        if not current_item or current_item.data(self.ADD_NEW_CARD_ROLE): return
        row = self.card_list_widget.row(current_item)
        card = self.manifest_data['cards'][row]
        card_id = card['id']
        
        filepath, _ = QFileDialog.getOpenFileName(self, f"选择{'例句' if audioType == 'sentence' else '单词'}音频文件", "", "音频文件 (*.wav *.mp3 *.flac *.ogg)")
        if not filepath: return

        subdir = 'sentence' if audioType == 'sentence' else 'audio'
        path_key = 'sentence_audio_path' if audioType == 'sentence' else 'audio_path'
        
        # [OPTIMIZATION] 在更新前，获取旧的相对路径
        old_relative_path = card.get(path_key, '')

        audio_dir = os.path.join(self.working_dir, subdir)
        os.makedirs(audio_dir, exist_ok=True)
        ext = os.path.splitext(filepath)[1]
        dest_filename = f"{card_id}{ext}"
        dest_path = os.path.join(audio_dir, dest_filename)
        
        try:
            shutil.copy2(filepath, dest_path)
            new_relative_path = os.path.join(subdir, dest_filename).replace("\\", "/")
            
            # 更新 manifest
            card[path_key] = new_relative_path
            
            # 更新UI
            if audioType == 'sentence':
                self.sentence_audio_label.setText(dest_filename)
                self.play_sentence_audio_btn.setEnabled(True)
            else:
                self.card_audio_label.setText(dest_filename)
                self.play_audio_btn.setEnabled(True)
            
            self._update_ui_state()

            # [OPTIMIZATION] 如果旧文件存在且与新文件不同，则删除旧文件
            if old_relative_path and old_relative_path != new_relative_path:
                old_full_path = os.path.join(self.working_dir, old_relative_path)
                if os.path.exists(old_full_path):
                    os.remove(old_full_path)

        except Exception as e: 
            QMessageBox.critical(self, "设置音频失败", f"无法复制音频文件：\n{e}")
    
    def _clear_card_audio(self, audioType='word'):
        """清除当前卡片的音频关联。"""
        current_item = self.card_list_widget.currentItem()
        if not current_item or current_item.data(self.ADD_NEW_CARD_ROLE): return
        row = self.card_list_widget.row(current_item)

        # [修改] 根据 audioType 确定路径键和UI元素
        path_key = 'sentence_audio_path' if audioType == 'sentence' else 'audio_path'
        self.manifest_data['cards'][row][path_key] = ""
        
        if audioType == 'sentence':
            self.sentence_audio_label.setText("无例句音频"); self.play_sentence_audio_btn.setEnabled(False)
        else:
            self.card_audio_label.setText("无音频文件"); self.play_audio_btn.setEnabled(False)
            
        self._update_ui_state()

    def _scan_and_update_media_paths(self):
        """
        在加载卡组后，自动扫描工作目录下的 `images/` 和 `audio/` 子文件夹。
        如果发现媒体文件与卡片ID匹配，但其路径未在 manifest 中记录，则自动更新 manifest。
        这将修复旧卡组的媒体引用，或因外部操作导致链接丢失的情况。
        返回 True 如果有任何媒体路径被修复，否则返回 False。
        """
        if not self.working_dir: return False
        repaired_count = 0
        cards_by_id = {card['id']: card for card in self.manifest_data.get('cards', [])}
        
        # 遍历图片、单词音频和例句音频三种媒体类型
        for media_type_data in [('image', 'images', 'image_path'), 
                                ('word_audio', 'audio', 'audio_path'), 
                                ('sentence_audio', 'sentence', 'sentence_audio_path')]:
            _, subdir, key = media_type_data
            media_dir = os.path.join(self.working_dir, subdir)
            if not os.path.isdir(media_dir): continue # 如果媒体子文件夹不存在，跳过
            
            # 遍历媒体文件夹中的所有文件
            for filename in os.listdir(media_dir):
                file_id, _ = os.path.splitext(filename) # 从文件名中提取ID (不含扩展名)
                if file_id in cards_by_id: # 如果找到匹配的卡片ID
                    card = cards_by_id[file_id]
                    # 如果卡片的对应媒体路径为空，说明可能丢失或未被记录
                    if not card.get(key): 
                        # 构造相对路径并更新 manifest
                        card[key] = os.path.join(subdir, filename).replace("\\", "/")
                        repaired_count += 1 # 增加修复计数
        
        return repaired_count > 0  # 返回是否有任何媒体路径被修复

    def _batch_import_media(self, media_type):
        if not self.working_dir: 
            QMessageBox.warning(self, "操作无效", "请先加载一个卡组。")
            return
        
        source_dir = QFileDialog.getExistingDirectory(self, f"选择包含媒体文件的文件夹")
        if not source_dir: return

        type_map = {
            'image': ('images', 'image_path'),
            'word': ('audio', 'audio_path'),
            'sentence': ('sentence', 'sentence_audio_path')
        }
        if media_type not in type_map: return
        
        target_subdir, target_key = type_map[media_type]
        
        dest_dir = os.path.join(self.working_dir, target_subdir)
        os.makedirs(dest_dir, exist_ok=True)
        cards_by_id = {card['id']: card for card in self.manifest_data.get('cards', [])}
        found_count, not_found_count = 0, 0
        
        for filename in os.listdir(source_dir):
            file_id, ext = os.path.splitext(filename)
            if file_id in cards_by_id:
                card = cards_by_id[file_id]
                
                # [OPTIMIZATION] 1. 获取旧文件路径
                old_relative_path = card.get(target_key, '')

                # 2. 复制新文件
                src_path = os.path.join(source_dir, filename)
                dest_path = os.path.join(dest_dir, filename)
                shutil.copy2(src_path, dest_path)
                
                # 3. 更新 manifest
                new_relative_path = os.path.join(target_subdir, filename).replace("\\", "/")
                card[target_key] = new_relative_path
                found_count += 1

                # [OPTIMIZATION] 4. 如果旧文件存在且不同，则删除
                if old_relative_path and old_relative_path != new_relative_path:
                    old_full_path = os.path.join(self.working_dir, old_relative_path)
                    if os.path.exists(old_full_path):
                        try:
                            os.remove(old_full_path)
                        except OSError as e:
                            print(f"Warning: Could not remove old media file {old_full_path}: {e}")

            else:
                not_found_count += 1
        
        # [OPTIMIZATION] 修复保存按钮不激活的问题
        if found_count > 0:
            # 刷新当前卡片详情视图，以显示新导入的媒体（如果正好是当前卡片）
            current_item = self.card_list_widget.currentItem()
            if current_item and not current_item.data(self.ADD_NEW_CARD_ROLE):
                self._populate_card_details(current_item)
            
            # 触发UI状态检查，这将检测到manifest已更改并激活保存按钮
            self._update_ui_state()

        QMessageBox.information(self, "批量导入完成", f"成功匹配并导入 {found_count} 个文件。\n{not_found_count} 个文件未找到匹配的卡片ID。")

    # --- 文件系统操作 (新建、显示、复制、删除) ---

    def _create_new_deck(self):
        """
        创建一个全新的、空的 .fdeck 卡组。
        在执行前，会检查当前文件是否有未保存的更改。
        """
        # 1. 检查是否有未保存的更改，并处理用户决策
        if self._check_if_dirty():
            reply = QMessageBox.question(self, "未保存的更改", 
                                         "当前卡组有未保存的更改。您想先保存吗？",
                                         QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
            if reply == QMessageBox.Save: self._save_current_deck()
            elif reply == QMessageBox.Cancel: return  # 用户取消，中止创建操作

        # 2. 获取新卡组的文件名
        filename, ok = QInputDialog.getText(self, "创建新卡组", "请输入新卡组的文件名 (无需扩展名):")
        if not ok or not filename: return  # 用户取消或未输入文件名
        
        deck_path = os.path.join(self.FLASHCARDS_DIR, f"{filename}.fdeck")
        if os.path.exists(deck_path):
            QMessageBox.warning(self, "文件已存在", "同名卡组文件已存在。"); return
            
        # 3. 构造默认 manifest 数据
        default_manifest = {
            "meta": {
                "format_version": "1.0",
                "deck_id": str(uuid.uuid4()),  # 生成唯一的 UUID
                "deck_name": filename,
                "deck_author": "",
                "deck_description": "",  # 默认为空
                "creation_date": datetime.now().isoformat(),  # 记录创建时间
                "capabilities": ["text", "text_input"]  # 默认支持文本和文本输入
            },
            "cards": []  # 初始为空卡片列表
        }
        
        # 4. 清理临时工作目录，并创建临时的 manifest.json 文件
        self.cleanup_temp_dir() 
        os.makedirs(self.TEMP_DIR, exist_ok=True)
        temp_manifest_path = os.path.join(self.TEMP_DIR, "manifest.json")
        with open(temp_manifest_path, 'w', encoding='utf-8') as f: 
            json.dump(default_manifest, f, indent=2)

        # 5. 将临时 manifest 打包成新的 .fdeck 文件
        try:
            import zipfile
            with zipfile.ZipFile(deck_path, 'w') as zf:
                zf.write(temp_manifest_path, "manifest.json")  # 只打包 manifest.json
            
            # 6. 更新UI，显示新创建的卡组
            self.populate_deck_list()
            # 自动选中新创建的卡组，这将触发 _on_deck_selected 加载它
            items = self.deck_list_widget.findItems(f"{filename}.fdeck", Qt.MatchExactly)
            if items: 
                self.deck_list_widget.setCurrentItem(items[0])
                
        except Exception as e: 
            QMessageBox.critical(self, "创建失败", f"无法创建新的卡组文件：\n{e}")

    def _show_deck_context_menu(self, position):
        item = self.deck_list_widget.itemAt(position)
        if not item: return

        menu = QMenu(self)
        show_action = menu.addAction(self.icon_manager.get_icon("open_folder"), "在文件浏览器中显示")
        duplicate_action = menu.addAction(self.icon_manager.get_icon("copy"), "创建副本")
        delete_action = menu.addAction(self.icon_manager.get_icon("delete"), "删除")
        
        menu.addSeparator()
        batch_import_img_action = menu.addAction(self.icon_manager.get_icon("image"), "批量导入图片...")
        batch_import_aud_action = menu.addAction(self.icon_manager.get_icon("wav"), "批量导入单词音频...")
        # [新增] 批量导入例句菜单项
        batch_import_sentence_action = menu.addAction(self.icon_manager.get_icon("play_audio"), "批量导入例句音频...")

        action = menu.exec_(self.deck_list_widget.mapToGlobal(position))
        
        if action == show_action: self._show_in_explorer(item)
        elif action == duplicate_action: self._duplicate_deck(item)
        elif action == delete_action: self._delete_deck(item)
        elif action == batch_import_img_action:
            self._batch_import_media('image')
        elif action == batch_import_aud_action:
            # [修改] 明确指定导入类型为 'word'
            self._batch_import_media('word')
        # [新增] 批量导入例句动作处理
        elif action == batch_import_sentence_action:
            self._batch_import_media('sentence')

    def _show_card_context_menu(self, position):
        """显示卡片列表的右键上下文菜单。"""
        item = self.card_list_widget.itemAt(position)
        # 如果点击空白处或占位符，则不显示菜单
        if not item or item.data(self.ADD_NEW_CARD_ROLE):
            return

        menu = QMenu(self)
        rename_action = menu.addAction(self.icon_manager.get_icon("rename"), "重命名")
        duplicate_action = menu.addAction(self.icon_manager.get_icon("copy"), "创建副本")
        menu.addSeparator()
        delete_action = menu.addAction(self.icon_manager.get_icon("clear_contents"), "删除")

        action = menu.exec_(self.card_list_widget.mapToGlobal(position))

        if action == rename_action:
            self.card_list_widget.editItem(item)  # 激活行内编辑
        elif action == duplicate_action:
            self._duplicate_selected_card()  # 复制选中卡片
        elif action == delete_action:
            self._remove_selected_card()  # 移除选中卡片

    def _duplicate_selected_card(self):
        """创建当前选中卡片的副本。"""
        current_item = self.card_list_widget.currentItem()
        # 检查是否有选中卡片，且不是占位符
        if not current_item or current_item.data(self.ADD_NEW_CARD_ROLE):
            return

        row = self.card_list_widget.row(current_item)
        if not (0 <= row < len(self.manifest_data.get('cards', []))):
            return
            
        # 1. 创建卡片数据的深拷贝，确保是独立的副本
        original_card_data = self.manifest_data['cards'][row]
        new_card_data = copy.deepcopy(original_card_data)

        # 2. 生成唯一的ID
        base_id = original_card_data.get('id', 'copy') + "_copy"
        i = 1
        new_id = base_id
        # 收集所有现有ID，用于唯一性检查
        existing_ids = {c.get('id') for c in self.manifest_data.get('cards', [])}
        while new_id in existing_ids:
            new_id = f"{base_id}_{i}"
            i += 1
        new_card_data['id'] = new_id
        
        # 3. 清空媒体路径，因为媒体文件尚未被复制到临时目录
        #    用户需要手动为副本设置新的媒体文件或批量导入
        new_card_data['image_path'] = ""
        new_card_data['audio_path'] = ""
        new_card_data['sentence_audio_path'] = "" # NEW: Clear sentence audio path for duplicate

        # 4. 更新UI和数据模型
        self._remove_placeholder_card()  # 移除旧的占位符
        
        insert_pos = row + 1  # 在原卡片下方插入新副本
        self.manifest_data['cards'].insert(insert_pos, new_card_data)  # 插入到数据模型
        
        new_item = QListWidgetItem(new_id)
        new_item.setFlags(new_item.flags() | Qt.ItemIsEditable)  # 设为可编辑
        self.card_list_widget.insertItem(insert_pos, new_item)  # 插入到UI列表
        
        self._add_placeholder_card()  # 重新添加占位符
        self.card_list_widget.setCurrentItem(new_item)  # 选中新创建的副本
        self._update_ui_state()  # 更新UI状态，标记为脏
    
    def _show_in_explorer(self, item):
        """在文件浏览器中显示选中的 .fdeck 文件。"""
        path = os.path.join(self.FLASHCARDS_DIR, item.text())  # 构造文件完整路径
        if not os.path.exists(path): return  # 文件不存在则返回
        
        try:
            # 根据操作系统调用不同的命令打开文件浏览器并选中文件
            if sys.platform == 'win32':
                subprocess.run(['explorer', '/select,', os.path.normpath(path)])
            elif sys.platform == 'darwin':
                subprocess.run(['open', '-R', path])  # macOS 的打开方式
            else: # Linux
                subprocess.run(['xdg-open', os.path.dirname(path)])  # Linux 通常只能打开文件夹
        except Exception as e:
            QMessageBox.critical(self, "操作失败", f"无法打开文件所在位置: {e}")

    def _duplicate_deck(self, item):
        """创建选中 .fdeck 卡组的副本。"""
        src_path = os.path.join(self.FLASHCARDS_DIR, item.text())  # 源文件路径
        base, ext = os.path.splitext(os.path.basename(src_path))
        
        # 生成不冲突的新文件名 (例如: deck_copy.fdeck, deck_copy_1.fdeck)
        dest_path = os.path.join(os.path.dirname(src_path), f"{base}_copy{ext}")
        i = 1
        while os.path.exists(dest_path):
            dest_path = os.path.join(os.path.dirname(src_path), f"{base}_copy_{i}{ext}")
            i += 1
        
        try:
            import zipfile
            shutil.copy2(src_path, dest_path)  # 复制文件
            
            # 读取副本的 manifest，修改其内部的 deck_id 和 deck_name，确保唯一性
            # 为了修改 zip 内部文件，需要先解压到临时目录，修改，再重新打包
            
            # 1. 临时解压副本
            temp_dir_for_copy = os.path.join(self.TEMP_DIR, f"copy_temp_{str(uuid.uuid4())}")
            os.makedirs(temp_dir_for_copy, exist_ok=True)
            with zipfile.ZipFile(dest_path, 'r') as zf:
                zf.extractall(temp_dir_for_copy)
            
            # 2. 读取并修改 manifest
            manifest_path_in_temp = os.path.join(temp_dir_for_copy, 'manifest.json')
            with open(manifest_path_in_temp, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
            
            manifest['meta']['deck_id'] = str(uuid.uuid4())  # 新的 UUID
            manifest['meta']['deck_name'] = os.path.splitext(os.path.basename(dest_path))[0]  # 新的名称
            
            with open(manifest_path_in_temp, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
            
            # 3. 重新打包副本
            with zipfile.ZipFile(dest_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(temp_dir_for_copy):
                    for file in files:
                        file_path = os.path.join(root, file)
                        # 注意这里的 arcname 必须是相对于 zip 文件根目录的相对路径
                        arcname = os.path.relpath(file_path, temp_dir_for_copy)
                        zf.write(file_path, arcname)
            
            shutil.rmtree(temp_dir_for_copy)  # 清理临时目录
            
            self.populate_deck_list()  # 刷新卡组列表以显示新副本
        except Exception as e:
            QMessageBox.critical(self, "创建副本失败", f"无法创建或修改副本: {e}")

    def _delete_deck(self, item):
        """删除选中的 .fdeck 卡组。"""
        path = os.path.join(self.FLASHCARDS_DIR, item.text())  # 获取文件完整路径
        
        reply = QMessageBox.question(self, "确认删除", 
                                     f"您确定要永久删除卡组 '{item.text()}' 吗？\n此操作不可撤销！", 
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                # 如果删除的是当前正在编辑的卡组，则先取消选中，这将触发状态清理
                if path == self.current_deck_path:
                    self.deck_list_widget.setCurrentRow(-1)  # 取消选中
                os.remove(path)  # 执行文件删除
                self.populate_deck_list()  # 刷新列表
            except Exception as e: 
                QMessageBox.critical(self, "删除失败", f"删除文件时出错:\n{e}")
    
    def cleanup_temp_dir(self):
        """
        安全地删除临时工作目录。
        在程序启动、对话框关闭、或切换卡组时调用。
        """
        if os.path.exists(self.TEMP_DIR):
            try: 
                shutil.rmtree(self.TEMP_DIR)
            except Exception as e: 
                print(f"Error cleaning up temp directory: {e}", file=sys.stderr)
        self.working_dir = None  # 清空工作目录引用

    def closeEvent(self, event):
        """
        重写 QDialog 的 closeEvent，在关闭对话框前，
        检查是否有未保存的更改并进行清理。
        """
        if self._check_if_dirty():  # 如果有未保存的更改
            reply = QMessageBox.question(self, "未保存的更改", 
                                       "您有未保存的更改。确定要关闭吗？", 
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No: 
                event.ignore()  # 用户选择不关闭，忽略事件
                return
        self.cleanup_temp_dir()  # 清理临时目录
        super().closeEvent(event)  # 调用父类的 closeEvent 完成关闭