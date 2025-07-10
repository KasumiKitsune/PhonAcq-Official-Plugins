# --- START OF FILE plugins/praat_exporter/exporter.py (v3.0 - Annotation Workbench) ---

import os
import sys
from functools import partial

from PyQt5.QtWidgets import (QAction, QFileDialog, QMessageBox, QDialog, QVBoxLayout,
                             QHBoxLayout, QLabel, QLineEdit, QComboBox, QPushButton,
                             QListWidget, QListWidgetItem, QGroupBox, QSplitter, QInputDialog, QWidget, QFormLayout, QDoubleSpinBox, QDialogButtonBox)
from PyQt5.QtGui import QIcon, QFont, QColor, QPalette, QPixmap, QKeySequence
from PyQt5.QtCore import Qt, QSize

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
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

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
# 标注工作台 对话框 (v3.0.1)
# ==============================================================================
class AnnotationWorkbenchDialog(QDialog):
    def __init__(self, audio_filepath, total_duration, sr, icon_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TextGrid 标注工作台")
        self.setMinimumSize(600, 700)

        # --- 核心数据 ---
        self.audio_filepath = audio_filepath
        self.total_duration = total_duration
        self.sr = sr
        self.icon_manager = icon_manager
        self.current_selection = None # (start_sec, end_sec)
        self.annotations = {}  # {group_name: [(start, end, text), ...]}

        # --- 预设颜色 ---
        self.color_cycle = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf']
        self.group_colors = {}

        self._init_ui()
        self._connect_signals()
        
        # --- [核心修改] 新增：设置删除动作和快捷键 ---
        self.delete_action = QAction("删除选中的标注", self)
        self.delete_action.setShortcut(QKeySequence.Delete) # 绑定 Delete 键
        # 为了兼容macOS等可能没有独立Delete键的系统，可以额外绑定 Backspace
        self.delete_action.setShortcuts([QKeySequence.Delete, Qt.Key_Backspace]) 
        self.delete_action.triggered.connect(self._delete_selected_annotations)
        # 将这个 Action 添加到标注列表控件上，这样只有当列表获得焦点时快捷键才生效
        self.annotation_list.addAction(self.delete_action)
        # --- 修改结束 ---

        self._update_ui_state()
        
    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        
        info_label = QLabel(f"<b>文件:</b> {os.path.basename(self.audio_filepath)} | <b>总时长:</b> {self.total_duration:.3f}s")
        main_layout.addWidget(info_label)

        splitter = QSplitter(Qt.Horizontal)
        left_panel = self._create_left_panel()
        right_panel = self._create_right_panel()

        splitter.addWidget(left_panel); splitter.addWidget(right_panel)
        splitter.setSizes([250, 350])
        main_layout.addWidget(splitter)
        
        bottom_layout = QHBoxLayout()
        self.import_button = QPushButton(" 导入现有 TextGrid...")
        # [修复] 修正 get_icon 调用
        self.import_button.setIcon(self.icon_manager.get_icon("open_folder"))
        self.export_button = QPushButton(" 导出到 TextGrid...")
        self.export_button.setIcon(self.icon_manager.get_icon("save"))
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.import_button)
        bottom_layout.addWidget(self.export_button)
        main_layout.addLayout(bottom_layout)

    def _create_left_panel(self):
        panel = QWidget(); layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 5, 0)

        group_box = QGroupBox("分组 (对应 Tier)")
        group_layout = QVBoxLayout(group_box)
        self.group_list = QListWidget(); self.group_list.setToolTip("当前的分组列表。选择一个分组以在该组中添加标注。")
        
        group_btn_layout = QHBoxLayout()
        # [优化] 设置按钮间的间距
        group_btn_layout.setSpacing(5)

        # --- [核心修改] 为按钮添加文本 ---
        # 1. 修改 "添加分组" 按钮
        self.add_group_btn = QPushButton(" 添加分组") # 直接在构造函数中加入文本
        self.add_group_btn.setIcon(self.icon_manager.get_icon("add_row"))
        # self.add_group_btn.setIconSize(QSize(16, 16)) # 移除固定的IconSize，让它自适应按钮大小
        self.add_group_btn.setToolTip("添加新分组 (快捷键: Ctrl+N)")
        self.add_group_btn.setAutoDefault(False)
        self.add_group_btn.setShortcut("Ctrl+N")
        
        # 2. 修改 "删除分组" 按钮
        self.remove_group_btn = QPushButton(" 删除分组") # 直接在构造函数中加入文本
        self.remove_group_btn.setIcon(self.icon_manager.get_icon("remove_row")) 
        # self.remove_group_btn.setIconSize(QSize(16, 16)) # 移除固定的IconSize
        self.remove_group_btn.setToolTip("删除选中分组及其所有标注")
        self.remove_group_btn.setAutoDefault(False)
        # --- 修改结束 ---
        
        # 3. （可选）调整按钮样式，让图标和文字看起来更协调
        # from PyQt5.QtCore import Qt
        # self.add_group_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        # self.remove_group_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        group_btn_layout.addWidget(self.add_group_btn)
        group_btn_layout.addWidget(self.remove_group_btn)
        group_btn_layout.addStretch()
        group_layout.addWidget(self.group_list)
        group_layout.addLayout(group_btn_layout)

        # 标注控制
        annotate_box = QGroupBox("添加新标注")
        annotate_layout = QFormLayout(annotate_box)
        self.selection_label = QLabel("<i>请在主窗口选择区域...</i>")
        self.selection_label.setStyleSheet("color: gray;")
        self.annotation_text = QLineEdit()
        self.annotation_text.setToolTip("输入要应用于当前选区的标注文本。")
        self.add_annotation_btn = QPushButton(" 添加标注到选中分组")
        self.add_annotation_btn.setIcon(self.icon_manager.get_icon("add"))
        
        # --- [核心修改 3] ---
        self.add_annotation_btn.setAutoDefault(True) # 明确将此按钮设为可成为默认
        self.add_annotation_btn.setDefault(True)     # 强制将此按钮设为当前的默认按钮
        # --- 修改结束 ---
        
        annotate_layout.addRow("当前选区:", self.selection_label)
        annotate_layout.addRow("标注文本:", self.annotation_text)
        annotate_layout.addRow(self.add_annotation_btn)
        
        layout.addWidget(group_box, 1)
        layout.addWidget(annotate_box)
        return panel
    
    def _create_right_panel(self):
        panel = QWidget(); layout = QVBoxLayout(panel)
        layout.setContentsMargins(5, 0, 0, 0)
        box = QGroupBox("已添加的标注")
        box_layout = QVBoxLayout(box)
        self.annotation_list = QListWidget(); self.annotation_list.setToolTip("双击可编辑标注文本，按Delete键可删除。")
        box_layout.addWidget(self.annotation_list, 1)
        layout.addWidget(box)
        return panel

    def _connect_signals(self):
        self.add_group_btn.clicked.connect(self._add_group)
        self.remove_group_btn.clicked.connect(self._remove_group)
        self.group_list.currentItemChanged.connect(self._on_group_selected)
        self.add_annotation_btn.clicked.connect(self._add_annotation)
        self.annotation_list.itemDoubleClicked.connect(self._edit_annotation)
        self.import_button.clicked.connect(self._import_textgrid)
        self.export_button.clicked.connect(self._export_textgrid)

    def _delete_selected_annotations(self):
        """删除在标注列表中所有被选中的项。"""
        selected_items = self.annotation_list.selectedItems()
        if not selected_items:
            return

        # 弹框确认，防止误删
        reply = QMessageBox.question(self, "确认删除", 
                                     f"您确定要删除选中的 {len(selected_items)} 条标注吗？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        
        if reply == QMessageBox.No:
            return

        # 从后往前删，避免因列表变化导致的索引错乱
        for item in reversed(selected_items):
            group_name, index = item.data(Qt.UserRole)
            if group_name in self.annotations and index < len(self.annotations[group_name]):
                del self.annotations[group_name][index]
        
        # 删除后，必须重新刷新整个列表以重建索引
        self._refresh_annotation_list()
        self._update_ui_state()
        self.annotation_text.setFocus()
        self.annotation_text.selectAll()

    def _update_ui_state(self):
        is_group_selected = self.group_list.currentItem() is not None
        has_selection = self.current_selection is not None
        
        self.remove_group_btn.setEnabled(is_group_selected)
        self.add_annotation_btn.setEnabled(is_group_selected and has_selection)
        self.export_button.setEnabled(bool(self.annotations) and any(self.annotations.values()))

    def update_selection(self, start_sample, end_sample):
        # [优化] 增加sr的有效性检查，避免除以零错误
        if self.sr is None or self.sr <= 0:
            return
        self.current_selection = (start_sample / self.sr, end_sample / self.sr)
        start_t, end_t = self.current_selection
        self.selection_label.setText(f"{start_t:.3f} - {end_t:.3f}s")
        self.selection_label.setStyleSheet("")
        self._update_ui_state()

    def _add_group(self):
        text, ok = QInputDialog.getText(self, '添加新分组', '请输入新分组 (Tier) 的名称:')
        if ok and text:
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
            # [优化] 新增分组后，主动更新UI状态
            self._update_ui_state()
    
    def _remove_group(self):
        current_item = self.group_list.currentItem()
        if not current_item: return
        
        group_name = current_item.text()
        reply = QMessageBox.question(self, "确认删除", f"您确定要删除分组 '{group_name}' 及其所有标注吗？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            row = self.group_list.row(current_item)
            self.group_list.takeItem(row)
            if group_name in self.annotations: del self.annotations[group_name]
            if group_name in self.group_colors: del self.group_colors[group_name]
            self._refresh_annotation_list()
            self._update_ui_state()
            
    def _on_group_selected(self, current, previous):
        self._refresh_annotation_list()
        self._update_ui_state()
        
    def _refresh_annotation_list(self):
        self.annotation_list.clear()
        current_group_item = self.group_list.currentItem()
        if not current_group_item: return
        
        group_name = current_group_item.text()
        # [优化] 增加对 self.annotations[group_name] 存在性的检查
        if group_name in self.annotations:
            for i, (start, end, text) in enumerate(self.annotations[group_name]):
                item = QListWidgetItem(f"[{i+1}] {start:.3f} - {end:.3f}: {text}")
                item.setData(Qt.UserRole, (group_name, i))
                self.annotation_list.addItem(item)
    
    def _add_annotation(self):
        if self.current_selection is None:
            QMessageBox.warning(self, "操作无效", "请先在主窗口中选择一个有效的区域。")
            return
        if self.group_list.currentItem() is None:
            QMessageBox.warning(self, "操作无效", "请先选择一个要添加标注的分组。")
            return

        new_start, new_end = self.current_selection
        
        if abs(new_start - new_end) < 1e-6:
            QMessageBox.warning(self, "选区无效", "选区时长过短，无法添加为区间标注。")
            return

        text = self.annotation_text.text()
        group_name = self.group_list.currentItem().text()
        intervals = self.annotations.get(group_name, [])

        # --- [核心修改] 检查并处理重叠 ---
        for i, (existing_start, existing_end, _) in enumerate(intervals):
            # 检查新的标注是否与现有标注重叠
            # 重叠条件: new_start < existing_end AND new_end > existing_start
            if new_start < existing_end and new_end > existing_start:
                reply = QMessageBox.warning(self, "标注重叠",
                                            f"新选区 ({new_start:.3f}s - {new_end:.3f}s) 与已有标注 "
                                            f"'{intervals[i][2]}' ({existing_start:.3f}s - {existing_end:.3f}s) 存在重叠。\n\n"
                                            "是否自动调整边界？\n"
                                            "（将以重叠部分的中点为界）",
                                            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                
                if reply == QMessageBox.No:
                    return # 用户取消操作

                # --- 自动调整边界 ---
                # 情况1: 新标注在现有标注之前重叠
                if new_start < existing_start:
                    mid_point = (existing_start + new_end) / 2.0
                    # 调整新标注的结束时间
                    new_end = mid_point
                    # 更新现有标注的起始时间
                    intervals[i] = (mid_point, existing_end, intervals[i][2])
                # 情况2: 新标注在现有标注之后重叠
                else: # new_start >= existing_start
                    mid_point = (new_start + existing_end) / 2.0
                    # 更新现有标注的结束时间
                    intervals[i] = (existing_start, mid_point, intervals[i][2])
                    # 调整新标注的起始时间
                    new_start = mid_point
                
                # 调整后，再次检查新标注的时长是否有效
                if abs(new_start - new_end) < 1e-6:
                    QMessageBox.information(self, "调整失败", "自动调整后，新标注时长过短，已取消添加。")
                    self._refresh_annotation_list() # 刷新以显示被修改的现有标注
                    return

                break # 处理完第一个重叠后即跳出循环

        intervals.append((new_start, new_end, text))
        intervals.sort(key=lambda x: x[0])
        self.annotations[group_name] = intervals
        self._refresh_annotation_list()
        self._update_ui_state()

    def _edit_annotation(self, item):
        group_name, index = item.data(Qt.UserRole)
        if group_name not in self.annotations or index >= len(self.annotations[group_name]):
            return

        start, end, old_text = self.annotations[group_name][index]
        
        dialog = EditAnnotationDialog(start, end, old_text, self.total_duration, self)
        if dialog.exec_():
            new_values = dialog.get_values()
            if new_values:
                new_start, new_end, new_text = new_values
                # 简单地替换。注意：这并未处理编辑后与其他项的重叠，
                # 这是一个更复杂的问题，留待未来迭代。
                self.annotations[group_name][index] = (new_start, new_end, new_text)
                # 排序以防时间被修改
                self.annotations[group_name].sort(key=lambda x: x[0])
                self._refresh_annotation_list()
            else:
                QMessageBox.warning(self, "输入无效", "起始时间必须小于结束时间。")

    def _import_textgrid(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入 TextGrid", os.path.dirname(self.audio_filepath), "TextGrid 文件 (*.TextGrid)")
        if not path: return

        try:
            tg = textgrid.TextGrid.fromFile(path)
            
            # --- [核心修复] ---
            # 1. 在导入前，先弹框确认，并警告用户可能的数据不匹配问题
            reply = QMessageBox.question(self, "确认导入", 
                                         "导入将覆盖当前工作台上的所有未保存标注。\n\n"
                                         "如果导入的 TextGrid 文件与当前音频时长不匹配，超出时长的标注将被自动忽略。\n\n"
                                         "是否继续？",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                return

            # 2. 清理现有数据
            self.annotations.clear(); self.group_list.clear(); self.group_colors.clear()
            
            # 3. 准备记录被忽略的标注
            ignored_count = 0
            
            for tier in tg:
                if isinstance(tier, textgrid.IntervalTier):
                    group_name = tier.name; self.annotations[group_name] = []
                    color = self.color_cycle[len(self.annotations) % len(self.color_cycle)]
                    self.group_colors[group_name] = color
                    
                    item = QListWidgetItem(group_name)
                    pixmap = QPixmap(16, 16); pixmap.fill(QColor(color))
                    item.setIcon(QIcon(pixmap))
                    self.group_list.addItem(item)
                    
                    for interval in tier:
                        # 4. 严格的边界检查
                        # 只有当区间的起始点和结束点都在当前音频的总时长之内时，才接受它
                        # 我们允许结束点恰好等于总时长
                        if interval.mark and interval.minTime < self.total_duration and interval.maxTime <= self.total_duration:
                            # 确保 minTime < maxTime，以防导入格式错误的文件
                            if interval.minTime < interval.maxTime:
                                self.annotations[group_name].append((interval.minTime, interval.maxTime, interval.mark))
                            else:
                                ignored_count += 1
                        elif interval.mark:
                            # 如果标注有内容但时间戳越界，则计数
                            ignored_count += 1
            
            if self.group_list.count() > 0:
                self.group_list.setCurrentRow(0)
            self._refresh_annotation_list()
            self._update_ui_state()

            # 5. 给出明确的反馈
            success_message = "已成功从文件加载标注。"
            if ignored_count > 0:
                success_message += f"\n\n注意：有 {ignored_count} 个标注因时间戳超出当前音频范围而被忽略。"
            QMessageBox.information(self, "导入完成", success_message)
            
        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"无法解析 TextGrid 文件：\n{e}")

    def _export_textgrid(self):
        save_path, _ = QFileDialog.getSaveFileName(self, "导出到 TextGrid", self.audio_filepath.replace(os.path.splitext(self.audio_filepath)[1], ".TextGrid"), "TextGrid 文件 (*.TextGrid)")
        if not save_path: return
        
        try:
            tg = textgrid.TextGrid(minTime=0, maxTime=self.total_duration)
            
            for group_name, annotation_list in self.annotations.items():
                # 创建一个干净的、空的 tier
                tier = textgrid.IntervalTier(name=group_name, minTime=0, maxTime=self.total_duration)
                
                # 创建一个临时列表来存储净化后的标注
                clean_annotations = []
                
                for start, end, text in annotation_list:
                    
                    # --- [核心修复] ---
                    # 1. 净化和钳制时间戳，防止浮点数精度问题
                    
                    # 确保 start 不会是微小的负数，并且不会超出总时长
                    clean_start = max(0.0, start)
                    clean_start = min(self.total_duration, clean_start)
                    
                    # 确保 end 不会小于 start，并且不会超出总时长
                    clean_end = max(clean_start, end)
                    clean_end = min(self.total_duration, clean_end)
                    
                    # 2. 再次检查时长，确保不是零时长或负时长
                    # 使用一个合理的最小阈值，比如 1 毫秒
                    MIN_DURATION = 0.001 
                    if (clean_end - clean_start) >= MIN_DURATION:
                        # 3. 过滤掉空的标注文本，这是一个好的实践
                        if text.strip():
                             clean_annotations.append((clean_start, clean_end, text))
                        else:
                             print(f"[Praat Exporter Warning] Skipping empty annotation in group '{group_name}' at {clean_start:.3f}s.")
                    else:
                        print(f"[Praat Exporter Warning] Skipping zero-duration or too short interval in group '{group_name}': {start}-{end}, '{text}'")

                # 如果有净化后的有效标注，则添加到tier中
                if clean_annotations:
                    for clean_start, clean_end, text in clean_annotations:
                        tier.add(minTime=clean_start, maxTime=clean_end, mark=text)
                    
                    # 只有当层中有内容时才追加
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
# 插件主入口类 (v3.0.1)
# ==============================================================================
class PraatExporterPlugin(BasePlugin):
    # (这个类中的 __init__, setup, teardown, create_action_for_menu, execute,
    # on_selection_changed, _on_workbench_closed 均保持不变，无需修改)
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.workbench_dialog = None
        self.audio_analysis_page = None

    def setup(self):
        if not TEXTGRID_LIB_AVAILABLE: return False
            
        self.audio_analysis_page = getattr(self.main_window, 'audio_analysis_page', None)
        if not self.audio_analysis_page or not hasattr(self.audio_analysis_page, 'spectrogram_widget'):
            return False

        spectrogram_widget = self.audio_analysis_page.spectrogram_widget
        setattr(spectrogram_widget, 'praat_exporter_plugin_active', self)
        spectrogram_widget.selectionChanged.connect(self.on_selection_changed)
        
        print("[Praat Exporter v3.0] Plugin hooked and listening for selections.")
        return True

    def teardown(self):
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
            print("[Praat Exporter v3.0] Plugin unhooked.")

    def create_action_for_menu(self, parent_widget):
        meta = self.plugin_manager.available_plugins.get('com.phonacq.praat_exporter')
        icon_path = os.path.join(meta['path'], meta.get('icon', '')) if meta else ""
        plugin_icon = QIcon(icon_path) if os.path.exists(icon_path) else self.main_window.icon_manager.get_icon("edit")
        
        action = QAction(plugin_icon, "打开标注工作台...", parent_widget)
        action.setToolTip("打开一个工作台，进行多点、多层(Tier)的标注，并最终导出为 TextGrid。")
        action.triggered.connect(self.execute)
        
        action.setEnabled(self.audio_analysis_page.audio_data is not None)
        return action

    def execute(self, **kwargs):
        if self.audio_analysis_page.audio_data is None:
            QMessageBox.warning(self.main_window, "无法操作", "请先在“音频分析”页面加载一个音频文件。")
            return
            
        if self.workbench_dialog is None:
            # [优化] 增加对 sr 的检查
            if not self.audio_analysis_page.sr or self.audio_analysis_page.sr <= 0:
                QMessageBox.critical(self.main_window, "数据错误", "音频采样率无效，无法打开标注工作台。")
                return

            self.workbench_dialog = AnnotationWorkbenchDialog(
                audio_filepath=self.audio_analysis_page.current_filepath,
                total_duration=len(self.audio_analysis_page.audio_data) / self.audio_analysis_page.sr,
                sr=self.audio_analysis_page.sr,
                icon_manager=self.main_window.icon_manager,
                parent=self.main_window
            )
            self.workbench_dialog.finished.connect(self._on_workbench_closed)

        self.workbench_dialog.show()
        self.workbench_dialog.raise_()
        self.workbench_dialog.activateWindow()
        
    def on_selection_changed(self, selection):
        if self.workbench_dialog and self.workbench_dialog.isVisible():
            if selection:
                start_sample, end_sample = selection
                self.workbench_dialog.update_selection(start_sample, end_sample)

    def _on_workbench_closed(self):
        self.workbench_dialog = None
        print("[Praat Exporter v3.0] Annotation workbench closed.")