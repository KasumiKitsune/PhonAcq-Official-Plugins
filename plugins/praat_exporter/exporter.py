# --- START OF FILE plugins/praat_exporter/exporter.py (v3.4 - Enhanced Annotation) ---

import os
import sys
from functools import partial
import re
import html
from PyQt5.QtWidgets import (QAction, QFileDialog, QMessageBox, QDialog, QVBoxLayout,
                             QHBoxLayout, QLabel, QLineEdit, QComboBox, QPushButton,
                             QListWidget, QListWidgetItem, QGroupBox, QSplitter, QInputDialog,
                             QWidget, QFormLayout, QDoubleSpinBox, QDialogButtonBox, QCheckBox, QFrame, QApplication)
from PyQt5.QtGui import QIcon, QFont, QColor, QPalette, QPixmap, QKeySequence
from PyQt5.QtCore import Qt, QSize, QEvent, pyqtSignal, QTimer

# --- 核心依赖：textgrid 库 ---
try:
    import textgrid
    TEXTGRID_LIB_AVAILABLE = True
except ImportError:
    TEXTGRID_LIB_AVAILABLE = False
    print("[Praat Exporter Warning] 'textgrid' library not found. Plugin disabled. Run: pip install textgrid")

# --- 插件API基类导入 ---
try:
    from modules.plugin_system import BasePlugin
    # 尝试导入 ToggleSwitch，如果失败则回退到 QCheckBox
    from modules.custom_widgets_module import ToggleSwitch
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin
    print("[Praat Exporter Warning] Could not import ToggleSwitch from custom_widgets_module. Using QCheckBox as fallback.")
    # 定义一个假的 ToggleSwitch 以避免崩溃
    class ToggleSwitch(QCheckBox):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.setText("Toggle (Fallback)")

# ==============================================================================
# 辅助对话框：编辑标注 (EditAnnotationDialog)
# ==============================================================================
class EditAnnotationDialog(QDialog):
    """一个用于编辑标注时间戳和文本的对话框。"""
    def __init__(self, start_time, end_time, text, total_duration, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑标注")

        layout = QFormLayout(self)

        self.start_spinbox = QDoubleSpinBox()
        self.start_spinbox.setRange(0, total_duration)
        self.start_spinbox.setDecimals(3)
        self.start_spinbox.setSuffix(" s")
        self.start_spinbox.setValue(start_time)

        self.end_spinbox = QDoubleSpinBox()
        self.end_spinbox.setRange(0, total_duration)
        self.end_spinbox.setDecimals(3)
        self.end_spinbox.setSuffix(" s")
        self.end_spinbox.setValue(end_time)
        
        self.text_edit = QLineEdit(text)

        layout.addRow("起始时间:", self.start_spinbox)
        layout.addRow("结束时间:", self.end_spinbox)
        layout.addRow("标注文本:", self.text_edit)
        
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addRow(button_box)
        
    def get_values(self):
        start = self.start_spinbox.value()
        end = self.end_spinbox.value()
        # 确保 start < end
        if start >= end:
            return None
        return start, end, self.text_edit.text()

# ==============================================================================
# 沉浸式标注小部件 (ImmersiveWidget)
# ==============================================================================
class ImmersiveWidget(QDialog):
    """一个紧凑、置顶、半透明的对话框，用于沉浸式标注。"""
    
    mode_exited = pyqtSignal()

    def __init__(self, main_workbench, parent=None):
        super().__init__(parent)
        self.main_workbench = main_workbench

        # --- 窗口样式设置 ---
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowOpacity(0.9) 
        self.setWindowTitle("沉浸式标注")
        
        # --- UI 布局 ---
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)

        top_layout = QHBoxLayout()
        
        # [新增] 分组选择下拉框
        self.group_selector = QComboBox()
        self.group_selector.setMinimumWidth(120) # 给一个最小宽度
        self.group_selector.setToolTip("选择要将标注添加到的分组")
        
        self.annotation_text = QLineEdit()
        self.add_button = QPushButton("添加")
        self.add_button.setIcon(self.main_workbench.icon_manager.get_icon("add"))
        self.add_button.setAutoDefault(False) # [修复] 防止回车键双重触发
        
        # [修改] 将 group_selector 添加到布局中
        top_layout.addWidget(self.group_selector) # 在最左侧
        top_layout.addWidget(self.annotation_text, 1)
        top_layout.addWidget(self.add_button)
        
        bottom_layout = QHBoxLayout()
        self.selection_label = QLabel("<i>无选区</i>")
        
        # [新增] 状态标签
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #4CAF50;") # 绿色成功提示

        self.exit_toggle = ToggleSwitch()
        self.exit_toggle.setChecked(True)
        self.exit_toggle.setToolTip("点击退出沉浸式标注模式")

        bottom_layout.addWidget(self.selection_label, 1)
        # [修改] 将状态标签添加到布局中
        bottom_layout.addWidget(self.status_label) 
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.exit_toggle)

        layout.addLayout(top_layout)
        layout.addLayout(bottom_layout)
        
        self.add_button.clicked.connect(self.main_workbench._add_annotation)
        self.exit_toggle.toggled.connect(self._on_exit_toggled)
        
        # [核心修改] 将 immersive widget 的 returnPressed 信号也连接到主工作台的智能处理函数
        # 这样它就会自动获得 Ctrl+Enter 添加 / Enter 播放 的功能
        self.annotation_text.returnPressed.connect(self.main_workbench._handle_return_pressed)
        
        self.group_selector.currentTextChanged.connect(
            self.main_workbench._set_active_group_from_immersive
        )
    
    def _on_exit_toggled(self, checked):
        if not checked:
            self.mode_exited.emit()

    def closeEvent(self, event):
        """当用户通过点击标题栏的'X'关闭窗口时，发射 mode_exited 信号。"""
        self.mode_exited.emit()
        super().closeEvent(event)

    # [新增] 显示和清除状态消息的方法
    def show_status_message(self, message, duration_ms=2500):
        """显示一条临时状态消息。"""
        self.status_label.setText(message)
        QTimer.singleShot(duration_ms, self._clear_status_message)

    def _clear_status_message(self):
        """清除状态消息的槽函数。"""
        self.status_label.setText("")

# ==============================================================================
# 标注工作台对话框 (AnnotationWorkbenchDialog) v3.4
# ==============================================================================
class AnnotationWorkbenchDialog(QDialog):
    def __init__(self, audio_filepath, total_duration, sr, icon_manager, audio_analysis_page, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TextGrid 标注工作台")
        self.setMinimumSize(600, 700)

        # --- 核心数据 ---
        self.audio_filepath = audio_filepath
        self.total_duration = total_duration
        self.sr = sr
        self.icon_manager = icon_manager
        # [新增] 保存对主分析页面的引用
        self.audio_analysis_page = audio_analysis_page
        
        # --- 中心化状态管理 ---
        self.current_selection = None
        self.current_selected_group_name = None 

        self.annotations = {}
        self.color_cycle = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf']
        self.group_colors = {}
        self.immersive_widget = None

        self._init_ui()
        self._connect_signals()
        
        self.delete_action = QAction("删除选中的标注", self)
        self.delete_action.setShortcuts([QKeySequence.Delete, Qt.Key_Backspace])
        self.delete_action.triggered.connect(self._delete_selected_annotations)
        self.annotation_list.addAction(self.delete_action)

        self._update_ui_state()
        
    def _connect_signals(self):
        """连接所有UI控件的信号到相应的槽函数。"""
        self.add_group_btn.clicked.connect(self._add_group)
        self.remove_group_btn.clicked.connect(self._remove_group)
        self.group_list.currentItemChanged.connect(self._on_group_selected)
        self.add_annotation_btn.clicked.connect(self._add_annotation)
        
        # [修改] 将 returnPressed 连接到新的智能处理函数
        self.annotation_text.returnPressed.connect(self._handle_return_pressed)
        
        self.annotation_list.itemDoubleClicked.connect(self._edit_annotation)
        self.import_button.clicked.connect(self._import_textgrid)
        self.export_button.clicked.connect(self._export_textgrid)
        self.immersive_toggle.toggled.connect(self._toggle_immersive_mode)
        
    def _init_ui(self):
        """构建标注工作台的用户界面。"""
        main_layout = QVBoxLayout(self)
        
        # 1. 获取原始文件名
        base_filename = os.path.basename(self.audio_filepath)
        duration_text = f" | <b>总时长:</b> {self.total_duration:.3f}s"
        
        # 2. 设定最大字符数并进行截断
        MAX_FILENAME_CHARS = 40  # 您可以根据需要调整这个数字
        
        if len(base_filename) > MAX_FILENAME_CHARS:
            # 从文件名开头截取，并在末尾添加省略号
            truncated_filename = base_filename[:MAX_FILENAME_CHARS] + "..."
        else:
            truncated_filename = base_filename
            
        # 3. 组合最终的文本并设置给 QLabel
        full_text = f"<b>文件:</b> {truncated_filename}{duration_text}"
        info_label = QLabel(full_text)
        
        # --- [核心修改] ---
        # 1. 设定 ToolTip 的最大像素宽度
        MAX_TOOLTIP_WIDTH = 500  # 您可以根据需要调整这个宽度值

        # 2. 对文件路径进行HTML转义，防止路径中的特殊字符（如 '<' 或 '>'）被误认为是HTML标签
        escaped_filepath = html.escape(self.audio_filepath)
        
        # 3. 创建一个富文本格式的字符串
        #    我们使用一个 <p> 标签，并通过 style 属性来设置它的宽度。
        #    Qt的渲染引擎会自动处理 <p> 标签内文本的换行。
        rich_text_tooltip = (
            f'<p style="width: {MAX_TOOLTIP_WIDTH}px;">'
            f'<b>完整路径:</b><br>{escaped_filepath}'  # 使用<br>让路径从新的一行开始，更清晰
            f'</p>'
        )

        # 4. 将富文本 ToolTip 设置给标签
        info_label.setToolTip(rich_text_tooltip)
        # --- [修改结束] ---
        
        main_layout.addWidget(info_label, 0)

        splitter = QSplitter(Qt.Horizontal)
        left_panel = self._create_left_panel()
        right_panel = self._create_right_panel()
        splitter.addWidget(left_panel); splitter.addWidget(right_panel)
        splitter.setSizes([250, 350])
        
        main_layout.addWidget(splitter, 1)

        bottom_layout = QHBoxLayout()
        
        self.immersive_toggle = ToggleSwitch()
        self.immersive_toggle.setToolTip("进入/退出沉浸式标注模式 (F11)")
        self.immersive_toggle.setShortcut("F11")
        
        bottom_layout.addWidget(QLabel("沉浸模式:"))
        bottom_layout.addWidget(self.immersive_toggle)
        bottom_layout.addStretch()

        self.import_button = QPushButton(" 导入现有 TextGrid...")
        self.import_button.setIcon(self.icon_manager.get_icon("open_folder"))
        self.import_button.setAutoDefault(False)
        self.export_button = QPushButton(" 导出到 TextGrid...")
        self.export_button.setIcon(self.icon_manager.get_icon("save"))
        self.export_button.setAutoDefault(False)
        
        bottom_layout.addWidget(self.import_button)
        bottom_layout.addWidget(self.export_button)
        
        main_layout.addLayout(bottom_layout, 0)
        
    def _toggle_immersive_mode(self, checked):
        """
        [v3.7 修改]
        切换主窗口和沉浸式悬浮窗的显示状态，并确保双向状态同步。
        此版本将沉浸式窗口定位到主工作台的右上角。
        """
        if self.immersive_widget is None:
            self.immersive_widget = ImmersiveWidget(self, self.parent())
            self.immersive_widget.mode_exited.connect(lambda: self.immersive_toggle.setChecked(False))
        
        if checked:
            # --- 进入沉浸模式 ---
            # 1. 同步UI状态
            self.immersive_widget.annotation_text.setText(self.annotation_text.text())
            self.immersive_widget.selection_label.setText(self.selection_label.text())
            self._refresh_group_selectors()
            self.immersive_widget.exit_toggle.setChecked(True)

            # --- [核心修改] 定位逻辑 ---
            # 2. 获取主工作台的几何信息
            main_window_geom = self.geometry()
            
            # 3. 获取沉浸窗口的尺寸提示（确保尺寸已更新）
            immersive_size_hint = self.immersive_widget.sizeHint()
            
            # 4. 计算新的x, y坐标
            # 新x = 主窗口右上角的x - 沉浸窗口的宽度 - 边距
            # 新y = 主窗口右上角的y + 边距
            margin = 40 # 窗口边距
            new_x = main_window_geom.right() - immersive_size_hint.width() - margin
            new_y = main_window_geom.top() + margin
            
            # 5. 移动窗口到计算出的新位置
            self.immersive_widget.move(new_x, new_y)
            # --- [修改结束] ---
            
            self.immersive_widget.show()
            self.hide()
            QTimer.singleShot(100, self.request_focus_on_input)

        else:
            # --- 退出沉浸模式 (逻辑保持不变) ---
            self.annotation_text.setText(self.immersive_widget.annotation_text.text())
            self.immersive_widget.hide()
            self.immersive_toggle.blockSignals(True)
            self.immersive_toggle.setChecked(False)
            self.immersive_toggle.blockSignals(False)
            self.show()
            self.raise_()
            self.activateWindow()
            QTimer.singleShot(100, self.request_focus_on_input)

    def _create_left_panel(self):
        """创建标注工作台的左侧面板。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 5, 0)

        group_box = QGroupBox("分组 (对应 Tier)")
        group_layout = QVBoxLayout(group_box)
        self.group_list = QListWidget()
        self.group_list.setToolTip("当前的分组列表。选择一个分组以在该组中添加标注。")
        group_btn_layout = QHBoxLayout()
        group_btn_layout.setSpacing(5)
        self.add_group_btn = QPushButton(" 添加分组")
        self.add_group_btn.setIcon(self.icon_manager.get_icon("add_row"))
        self.add_group_btn.setToolTip("添加新分组 (快捷键: Ctrl+N)")
        self.add_group_btn.setAutoDefault(False)
        self.add_group_btn.setShortcut("Ctrl+N")
        self.remove_group_btn = QPushButton(" 删除分组")
        self.remove_group_btn.setIcon(self.icon_manager.get_icon("remove_row"))
        self.remove_group_btn.setToolTip("删除选中分组及其所有标注")
        self.remove_group_btn.setAutoDefault(False)
        group_btn_layout.addWidget(self.add_group_btn)
        group_btn_layout.addWidget(self.remove_group_btn)
        group_btn_layout.addStretch()
        group_layout.addWidget(self.group_list)
        group_layout.addLayout(group_btn_layout)

        annotate_box = QGroupBox("添加新标注")
        annotate_layout = QFormLayout(annotate_box)
        self.selection_label = QLabel("<i>请在主窗口选择区域...</i>")
        self.selection_label.setStyleSheet("color: gray;")
        self.annotation_text = QLineEdit()
        self.annotation_text.setToolTip("输入要应用于当前选区的标注文本。")
        self.add_annotation_btn = QPushButton(" 添加标注到选中分组")
        self.add_annotation_btn.setIcon(self.icon_manager.get_icon("add"))
        self.add_annotation_btn.setAutoDefault(False) # [修复] 防止回车键双重触发
        
        annotate_layout.addRow("当前选区:", self.selection_label)
        annotate_layout.addRow("标注文本:", self.annotation_text)
        annotate_layout.addRow(self.add_annotation_btn)
        
        layout.addWidget(group_box, 1)
        layout.addWidget(annotate_box, 0)
        return panel
    
    def _create_right_panel(self):
        """创建标注工作台的右侧面板。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(5, 0, 0, 0)
        box = QGroupBox("已添加的标注")
        box_layout = QVBoxLayout(box)
        self.annotation_list = QListWidget()
        self.annotation_list.setToolTip("双击可编辑标注文本，按Delete键可删除。")
        box_layout.addWidget(self.annotation_list, 1)
        layout.addWidget(box)
        return panel


    def _delete_selected_annotations(self):
        """删除在标注列表中所有被选中的项。"""
        selected_items = self.annotation_list.selectedItems()
        if not selected_items: return
        reply = QMessageBox.question(self, "确认删除", f"您确定要删除选中的 {len(selected_items)} 条标注吗？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.No: return
        for item in reversed(selected_items):
            group_name, index = item.data(Qt.UserRole)
            if group_name in self.annotations and index < len(self.annotations[group_name]):
                del self.annotations[group_name][index]
        self._refresh_annotation_list()
        self._update_ui_state()
        self.annotation_text.setFocus()
        self.annotation_text.selectAll()

    def _handle_return_pressed(self):
        """
        [新增] 智能处理回车键事件。
        - Ctrl + Enter: 添加标注
        - Enter: 播放当前选区
        """
        modifiers = QApplication.keyboardModifiers()
        
        if modifiers == Qt.ControlModifier:
            # 如果按下了Ctrl键，则执行添加标注的逻辑
            self._add_annotation()
        else:
            # 如果只按了回车键，则调用主分析页面的播放功能
            if self.audio_analysis_page:
                self.audio_analysis_page.toggle_playback()

    def _update_ui_state(self):
        """根据当前状态更新UI控件的可用性。"""
        is_group_selected = self.current_selected_group_name is not None
        has_selection = self.current_selection is not None
        has_annotations = bool(self.annotations) and any(self.annotations.values())
        self.remove_group_btn.setEnabled(self.group_list.currentItem() is not None)
        self.add_annotation_btn.setEnabled(is_group_selected and has_selection)
        self.export_button.setEnabled(has_annotations)

    def update_selection(self, start_sample, end_sample):
        """
        一个统一的函数，用于更新选区状态并同步所有相关的UI。
        """
        if self.sr is None or self.sr <= 0: return

        display_text = ""
        style_sheet = ""

        if start_sample is None or end_sample is None:
            self.current_selection = None
            display_text = "<i>无选区</i>"
            style_sheet = "color: gray;"
        else:
            self.current_selection = (start_sample / self.sr, end_sample / self.sr)
            start_t, end_t = self.current_selection
            display_text = f"{start_t:.3f} - {end_t:.3f}s"
            style_sheet = ""
        
        self.selection_label.setText(display_text)
        self.selection_label.setStyleSheet(style_sheet)
        
        if self.immersive_widget:
            self.immersive_widget.selection_label.setText(display_text)

        self._update_ui_state()

    # [新增] 请求聚焦输入框的方法
    def request_focus_on_input(self):
        """
        [v3.6 最终修复版] 一个公共方法，用于将光标焦点设置到当前活动的输入框。
        此版本通过 QTimer.singleShot 同时处理窗口激活和控件聚焦，
        确保在用户松开鼠标后，正确的窗口被置顶，并且输入框获得焦点。
        """
        
        def _activate_and_set_focus():
            """一个内部函数，封装了窗口激活、控件聚焦和全选的完整流程。"""
            
            target_window = None
            target_widget = None

            # 1. 确定目标窗口和目标控件
            if self.immersive_widget and self.immersive_widget.isVisible():
                # 如果在沉浸模式，目标就是沉浸窗口和它的输入框
                target_window = self.immersive_widget
                target_widget = self.immersive_widget.annotation_text
            else:
                # 否则，目标就是主工作台窗口和它的输入框
                target_window = self
                target_widget = self.annotation_text
            
            if target_window and target_widget:
                # 2. [核心修复] 激活目标窗口
                target_window.raise_()         # 将窗口提升到顶层
                target_window.activateWindow() # 请求操作系统给予窗口焦点
                
                # 3. 设置控件焦点并全选文本
                target_widget.setFocus(Qt.OtherFocusReason)
                target_widget.selectAll()

        # [核心策略] 依然使用0毫秒延迟的定时器来调用我们的“激活-聚焦”函数。
        # 这确保了整个流程在Qt事件循环空闲时原子性地执行，效果最稳定。
        QTimer.singleShot(0, _activate_and_set_focus)

    def _add_group(self, predefined_name=None):
        """添加一个新的标注分组 (Tier)。"""
        text = predefined_name
        if not text:
            text, ok = QInputDialog.getText(self, '添加新分组', '请输入新分组 (Tier) 的名称:')
            if not (ok and text): return
        if text in self.annotations:
            QMessageBox.warning(self, "名称重复", "该分组名称已存在。")
            return
        self.annotations[text] = []
        color = self.color_cycle[len(self.annotations) % len(self.color_cycle)]
        self.group_colors[text] = color
        item = QListWidgetItem(text)
        pixmap = QPixmap(16, 16); pixmap.fill(QColor(color))
        item.setIcon(QIcon(pixmap))
        self.group_list.addItem(item)
        self.group_list.setCurrentItem(item)
        self._update_ui_state()
        self._refresh_group_selectors() # [新增] 添加分组后刷新所有选择器

    def _remove_group(self):
        """删除当前选中的分组及其所有标注。"""
        current_item = self.group_list.currentItem()
        if not current_item: return
        group_name = current_item.text()
        reply = QMessageBox.question(self, "确认删除", f"您确定要删除分组 '{group_name}' 及其所有标注吗？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            row = self.group_list.row(current_item)
            self.group_list.takeItem(row)
            if group_name in self.annotations: del self.annotations[group_name]
            if group_name in self.group_colors: del self.group_colors[group_name]
            self._refresh_annotation_list()
            self._update_ui_state()
            self._refresh_group_selectors() # [新增] 删除分组后刷新所有选择器

    def _on_group_selected(self, current, previous):
        """当用户在分组列表中选择不同的分组时调用。"""
        if current:
            self.current_selected_group_name = current.text()
        else:
            self.current_selected_group_name = None
        self._refresh_annotation_list()
        self._update_ui_state()
        self._refresh_group_selectors() # [新增] 主列表选择变化时，同步沉浸式下拉框

    # [新增] 新的公共方法，用于从沉浸窗口设置活动分组
    def _set_active_group_from_immersive(self, group_name):
        """从沉浸窗口的下拉框选择，同步主工作台的选中分组。"""
        if not group_name:
            return
        self.current_selected_group_name = group_name
        for i in range(self.group_list.count()):
            item = self.group_list.item(i)
            if item.text() == group_name:
                self.group_list.blockSignals(True)
                self.group_list.setCurrentItem(item)
                self.group_list.blockSignals(False)
                break
        self._refresh_annotation_list()

    # [新增] 新的私有方法，用于刷新所有分组选择器
    def _refresh_group_selectors(self):
        """刷新主窗口和沉浸窗口中的分组选择器，并保持选择一致。"""
        all_groups = list(self.annotations.keys())
        
        # 同步主窗口的列表（主要用于添加/删除分组后）
        # 这里的刷新逻辑在 _add_group 和 _remove_group 中已经处理，
        # 主要是确保 self.current_selected_group_name 始终是最新的。

        # 同步沉浸窗口的下拉框
        if self.immersive_widget:
            selector = self.immersive_widget.group_selector
            selector.blockSignals(True) # 阻塞信号，防止循环调用
            
            # 记录当前沉浸式下拉框的选择，尝试恢复
            current_immersive_text = selector.currentText()
            selector.clear()
            if all_groups:
                selector.addItems(all_groups)
                # 尝试恢复之前的选择，或者使用主工作台的当前选择
                if current_immersive_text in all_groups:
                    selector.setCurrentText(current_immersive_text)
                elif self.current_selected_group_name in all_groups:
                    selector.setCurrentText(self.current_selected_group_name)
                elif len(all_groups) > 0: # 如果都没有，且有分组，默认选中第一个
                    selector.setCurrentIndex(0)
                    self.current_selected_group_name = all_groups[0] # 更新主工作台状态

            selector.blockSignals(False) # 解除阻塞

    def _refresh_annotation_list(self):
        """根据当前选中的分组，刷新右侧的标注列表。"""
        self.annotation_list.clear()
        if not self.current_selected_group_name: return
        group_name = self.current_selected_group_name
        if group_name in self.annotations:
            for i, (start, end, text) in enumerate(self.annotations[group_name]):
                item = QListWidgetItem(f"[{i+1}] {start:.3f} - {end:.3f}: {text}")
                item.setData(Qt.UserRole, (group_name, i))
                self.annotation_list.addItem(item)
    
    def _add_annotation(self):
        """处理“添加标注”按钮的点击事件。"""
        if self.current_selection is None:
            QMessageBox.warning(self, "操作无效", "请先在主窗口中选择一个有效的区域。")
            return
        
        group_name = self.current_selected_group_name
        if not group_name:
            if self.group_list.count() == 0:
                # [核心修复] 动态确定父窗口
                parent_widget = self.immersive_widget if self.immersive_widget and self.immersive_widget.isVisible() else self

                text, ok = QInputDialog.getText(parent_widget, # <-- 使用正确的父窗口
                                                '创建第一个分组', 
                                                '你还没有创建任何分组 (Tier)。\n请输入第一个分组的名称:')
                if ok and text:
                    self._add_group(predefined_name=text)
                    group_name = self.current_selected_group_name
                else:
                    return
            else:
                QMessageBox.warning(self, "操作无效", "请先在分组列表中选择一个要添加标注的分组。")
                return
        if not group_name: return

        text_to_add = self.immersive_widget.annotation_text.text() if self.immersive_widget and self.immersive_widget.isVisible() else self.annotation_text.text()
        self._perform_add_annotation(text_to_add, group_name)

    def _perform_add_annotation(self, text, group_name):
        """
        [v3.5 修复版]
        封装了添加标注的核心逻辑，包括重叠检查和数据更新。
        此版本修复了自动调整边界功能失效的bug。
        """
        if self.current_selection is None:
            # 这种情况理论上不应发生，因为按钮会被禁用，但作为安全检查
            return 
            
        new_start, new_end = self.current_selection
        if abs(new_start - new_end) < 1e-6:
            QMessageBox.warning(self, "选区无效", "选区时长过短，无法添加为区间标注。")
            return

        intervals = self.annotations.get(group_name, [])

        # --- 核心修复：重写重叠处理逻辑 ---
        for i, (existing_start, existing_end, existing_text) in enumerate(intervals):
            # 判断是否存在重叠
            if new_start < existing_end and new_end > existing_start:
                reply = QMessageBox.warning(self, "标注重叠",
                                            f"新选区 ({new_start:.3f}s - {new_end:.3f}s) 与已有标注 "
                                            f"'{existing_text}' ({existing_start:.3f}s - {existing_end:.3f}s) 存在重叠。\n\n"
                                            "是否自动调整边界？\n"
                                            "（将以重叠部分的中点为界）",
                                            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                
                if reply == QMessageBox.No:
                    return # 用户取消操作，直接返回

                # 1. 计算重叠区域的中点
                mid_point = (max(new_start, existing_start) + min(new_end, existing_end)) / 2.0
                
                # 2. 根据新旧标注的相对位置，直接、清晰地修改边界
                if new_start < existing_start:
                    # 新标注在左，旧标注在右
                    new_end = mid_point  # 将新标注的结束时间设为中点
                    intervals[i] = (mid_point, existing_end, existing_text) # 更新旧标注的开始时间
                else:
                    # 旧标注在左，新标注在右
                    intervals[i] = (existing_start, mid_point, existing_text) # 更新旧标注的结束时间
                    new_start = mid_point # 将新标注的开始时间设为中点
                
                # 3. 检查调整后的新标注时长是否有效
                if abs(new_start - new_end) < 1e-6:
                    QMessageBox.information(self, "调整失败", "自动调整后，新标注时长过短，已取消添加。")
                    self._refresh_annotation_list() # 刷新以显示被修改的现有标注
                    return

                # 4. 只处理第一个发现的重叠，然后退出循环
                break 
        # --- 修复结束 ---

        intervals.append((new_start, new_end, text))
        intervals.sort(key=lambda x: x[0])
        self.annotations[group_name] = intervals
        self._refresh_annotation_list()
        self._update_ui_state()

        if self.immersive_widget and self.immersive_widget.isVisible():
            if len(text) > 8:
                display_text = text[:8] + "..."
            else:
                display_text = text
            if not display_text.strip():
                 status_message = "✓ 空标注已添加"
            else:
                 status_message = f"✓ 已添加: '{display_text}'"
            self.immersive_widget.show_status_message(status_message)

        self.annotation_text.clear()
        self.annotation_text.setFocus()
        if self.immersive_widget:
            self.immersive_widget.annotation_text.clear()
            self.immersive_widget.annotation_text.setFocus()

    def _edit_annotation(self, item):
        """双击标注项时，打开编辑对话框。"""
        group_name, index = item.data(Qt.UserRole)
        if group_name not in self.annotations or index >= len(self.annotations[group_name]): return
        start, end, old_text = self.annotations[group_name][index]
        dialog = EditAnnotationDialog(start, end, old_text, self.total_duration, self)
        if dialog.exec_():
            new_values = dialog.get_values()
            if new_values:
                new_start, new_end, new_text = new_values
                self.annotations[group_name][index] = (new_start, new_end, new_text)
                self.annotations[group_name].sort(key=lambda x: x[0])
                self._refresh_annotation_list()
            else:
                QMessageBox.warning(self, "输入无效", "起始时间必须小于结束时间。")

    def _import_textgrid(self):
        """从文件导入TextGrid标注。"""
        path, _ = QFileDialog.getOpenFileName(self, "导入 TextGrid", os.path.dirname(self.audio_filepath), "TextGrid 文件 (*.TextGrid)")
        if not path: return
        try:
            tg = textgrid.TextGrid.fromFile(path)
            reply = QMessageBox.question(self, "确认导入", "导入将覆盖当前工作台上的所有未保存标注。\n\n如果导入的 TextGrid 文件与当前音频时长不匹配，超出时长的标注将被自动忽略。\n\n是否继续？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No: return
            self.annotations.clear(); self.group_list.clear(); self.group_colors.clear()
            ignored_count = 0
            for tier in tg:
                if isinstance(tier, textgrid.IntervalTier):
                    group_name = tier.name
                    self.annotations[group_name] = []
                    color = self.color_cycle[len(self.annotations) % len(self.color_cycle)]
                    self.group_colors[group_name] = color
                    item = QListWidgetItem(group_name)
                    pixmap = QPixmap(16, 16); pixmap.fill(QColor(color))
                    item.setIcon(QIcon(pixmap))
                    self.group_list.addItem(item)
                    for interval in tier:
                        if interval.minTime < self.total_duration and interval.maxTime <= self.total_duration:
                            if interval.minTime < interval.maxTime:
                                self.annotations[group_name].append((interval.minTime, interval.maxTime, interval.mark))
                            else: ignored_count += 1
                        elif interval.mark:
                            ignored_count += 1
            if self.group_list.count() > 0:
                self.group_list.setCurrentRow(0)
                self._on_group_selected(self.group_list.currentItem(), None) # 触发选中事件
            self._refresh_annotation_list()
            self._update_ui_state()
            self._refresh_group_selectors() # [新增] 导入后刷新选择器
            success_message = "已成功从文件加载标注。"
            if ignored_count > 0:
                success_message += f"\n\n注意：有 {ignored_count} 个标注因时间戳超出当前音频范围或时长为零而被忽略。"
            QMessageBox.information(self, "导入完成", success_message)
        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"无法解析 TextGrid 文件：\n{e}")

    def _export_textgrid(self):
        """将当前工作台中的标注导出为TextGrid文件。"""
        default_dir = None
        main_window = self.parent()
        if main_window and hasattr(main_window, 'config'):
            results_dir = main_window.config.get('file_settings', {}).get('results_dir')
            if results_dir and os.path.isdir(results_dir):
                try:
                    analyze_dir = os.path.join(results_dir, 'analyze')
                    textgrids_dir = os.path.join(analyze_dir, 'textgrids')
                    os.makedirs(textgrids_dir, exist_ok=True)
                    default_dir = textgrids_dir
                except Exception as e:
                    print(f"[Praat Exporter Warning] Could not create standard textgrids directory: {e}")
        if default_dir is None:
            default_dir = os.path.dirname(self.audio_filepath)
        base_filename = os.path.splitext(os.path.basename(self.audio_filepath))[0]
        default_save_path = os.path.join(default_dir, f"{base_filename}.TextGrid")
        save_path, _ = QFileDialog.getSaveFileName(self, "导出到 TextGrid", default_save_path, "TextGrid 文件 (*.TextGrid)")
        if not save_path: return
        try:
            tg = textgrid.TextGrid(minTime=0, maxTime=self.total_duration)
            for group_name, annotation_list in self.annotations.items():
                tier = textgrid.IntervalTier(name=group_name, minTime=0, maxTime=self.total_duration)
                clean_annotations = []
                for start, end, text in annotation_list:
                    clean_start = max(0.0, start); clean_start = min(self.total_duration, clean_start)
                    clean_end = max(clean_start, end); clean_end = min(self.total_duration, clean_end)
                    MIN_DURATION = 0.001
                    if (clean_end - clean_start) >= MIN_DURATION:
                        if text.strip():
                             clean_annotations.append((clean_start, clean_end, text))
                        else:
                             print(f"[Praat Exporter Warning] Skipping empty annotation in group '{group_name}' at {clean_start:.3f}s during export.")
                    else:
                        print(f"[Praat Exporter Warning] Skipping zero-duration or too short interval in group '{group_name}': {start}-{end}, '{text}' during export.")
                if clean_annotations:
                    for clean_start, clean_end, text in clean_annotations:
                        tier.add(minTime=clean_start, maxTime=clean_end, mark=text)
                    if len(tier) > 0:
                        tg.append(tier)
            if len(tg) == 0:
                QMessageBox.information(self, "没有内容可导出", "没有有效的标注被添加，已取消导出。")
                return
            tg.write(save_path)
            QMessageBox.information(self, "导出成功", f"标注已成功导出至:\n{save_path}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "导出失败", f"写入 TextGrid 文件时发生错误:\n{e}")

# ==============================================================================
# 插件主入口类 (PraatExporterPlugin) v3.4
# ==============================================================================
class PraatExporterPlugin(BasePlugin):
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.workbench_dialog = None
        self.audio_analysis_page = None

    def setup(self):
        """插件初始化设置：检查依赖，并钩入音频分析模块。"""
        if not TEXTGRID_LIB_AVAILABLE: return False
        self.audio_analysis_page = getattr(self.main_window, 'audio_analysis_page', None)
        if not self.audio_analysis_page or not hasattr(self.audio_analysis_page, 'spectrogram_widget'):
            print("[Praat Exporter v3.4] Audio Analysis module not found or not ready. Plugin running in limited mode.")
            return True
        spectrogram_widget = self.audio_analysis_page.spectrogram_widget
        setattr(spectrogram_widget, 'praat_exporter_plugin_active', self)
        spectrogram_widget.selectionChanged.connect(self.on_selection_changed)
        print("[Praat Exporter v3.4] Plugin hooked and listening for selections from Audio Analysis.")
        return True

    def teardown(self):
        """插件卸载清理：关闭工作台，并从音频分析模块解钩。"""
        if self.workbench_dialog:
            self.workbench_dialog.close()
        if self.audio_analysis_page and hasattr(self.audio_analysis_page, 'spectrogram_widget'):
            spectrogram_widget = self.audio_analysis_page.spectrogram_widget
            if hasattr(spectrogram_widget, 'praat_exporter_plugin_active'):
                delattr(spectrogram_widget, 'praat_exporter_plugin_active')
            try:
                spectrogram_widget.selectionChanged.disconnect(self.on_selection_changed)
            except TypeError:
                pass
            print("[Praat Exporter v3.4] Plugin unhooked from Audio Analysis.")

    def create_action_for_menu(self, parent_widget):
        """为音频分析模块的右键菜单创建动作。"""
        meta = self.plugin_manager.available_plugins.get('com.phonacq.praat_exporter')
        icon_file = meta.get('icon', '') if meta else ""
        icon_path = os.path.join(meta['path'], icon_file) if meta and icon_file else ""
        plugin_icon = self.main_window.icon_manager.get_icon("edit")
        action = QAction(plugin_icon, "打开标注工作台...", parent_widget)
        action.setToolTip("打开一个工作台，进行多点、多层(Tier)的标注，并最终导出为 TextGrid。")
        action.triggered.connect(self.execute)
        action.setEnabled(self.audio_analysis_page and self.audio_analysis_page.audio_data is not None)
        return action

    def execute(self, **kwargs):
        """
        [已优化] 插件执行入口点。打开或激活标注工作台。
        此版本将 audio_analysis_page 的引用传递给对话框。
        """
        if not self.audio_analysis_page or self.audio_analysis_page.audio_data is None:
            QMessageBox.warning(self.main_window, "无法操作", "请先在“音频分析”页面加载一个音频文件。")
            return
        if not self.audio_analysis_page.sr or self.audio_analysis_page.sr <= 0:
            QMessageBox.critical(self.main_window, "数据错误", "音频采样率无效，无法打开标注工作台。")
            return
        if self.workbench_dialog is None:
            self.workbench_dialog = AnnotationWorkbenchDialog(
                audio_filepath=self.audio_analysis_page.current_filepath,
                total_duration=len(self.audio_analysis_page.audio_data) / self.audio_analysis_page.sr,
                sr=self.audio_analysis_page.sr,
                icon_manager=self.main_window.icon_manager,
                # [新增] 传递主分析页面的引用
                audio_analysis_page=self.audio_analysis_page,
                parent=self.main_window
            )
            self.workbench_dialog.finished.connect(self._on_workbench_closed)
        self.workbench_dialog.show()
        self.workbench_dialog.raise_()
        self.workbench_dialog.activateWindow()

    def on_selection_changed(self, selection):
        """
        当音频分析页面选区改变时，此槽函数被调用。
        移除 isVisible() 检查，确保信号总能被传递。
        现在它还负责请求将焦点设置回输入框。
        """
        if self.workbench_dialog:
            if selection:
                start_sample, end_sample = selection
                self.workbench_dialog.update_selection(start_sample, end_sample)
            else:
                self.workbench_dialog.update_selection(None, None)
            
            # [新增] 无论选区是否有效，都尝试将焦点设置回输入框
            self.workbench_dialog.request_focus_on_input()

    def _on_workbench_closed(self):
        """标注工作台对话框关闭时的回调。"""
        self.workbench_dialog = None
        print("[Praat Exporter] Annotation workbench closed.")

# --- END OF FILE plugins/praat_exporter/exporter.py ---