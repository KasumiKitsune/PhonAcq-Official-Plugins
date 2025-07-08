# -*- coding: utf-8 -*-

import os
import re
import sys
from PyQt5.QtWidgets import (QAction, QFileDialog, QMessageBox, QDialog, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QComboBox, QDialogButtonBox)
from PyQt5.QtGui import QIcon

# 导入插件API基类
try:
    from plugin_system import BasePlugin
except ImportError:
    # 假设主程序已将 'modules' 目录添加到了 sys.path
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from plugin_system import BasePlugin

# ==============================================================================
# 导出配置对话框 (已添加详细Tooltips)
# ==============================================================================
class ExportDialog(QDialog):
    """
    一个配置对话框，用于在导出TextGrid前获取用户输入。
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出为 TextGrid")
        
        # --- UI Elements ---
        self.tier_name_input = QLineEdit("tier1")
        self.tier_name_input.setToolTip(
            "设置将在 Praat 中显示的层(Tier)的名称。\n"
            "例如：'phonemes', 'words', 'notes'。\n"
            "如果选择追加模式，请输入您希望追加到的目标层的名称。"
        )
        
        self.annotation_input = QLineEdit("selection")
        self.annotation_input.setToolTip(
            "设置要标记在选区上的文本内容。\n"
            "例如，一个音标 /a/、一个单词 'hello' 或一个事件备注。"
        )
        
        self.tier_type_combo = QComboBox()
        self.tier_type_combo.addItems(["IntervalTier", "PointTier"])
        self.tier_type_combo.setToolTip(
            "选择要创建的标注层类型：\n"
            "- IntervalTier: 用于标记一个时间段，拥有起点和终点（例如一个音素或单词的持续时间）。\n"
            "- PointTier: 用于在单个时间点上做标记（例如一个音爆或事件的发生时刻）。"
        )
        
        # --- Layout ---
        layout = QVBoxLayout(self)
        form_layout = QHBoxLayout()
        
        label_layout = QVBoxLayout()
        label_layout.addWidget(QLabel("层名称 (Tier Name):"))
        label_layout.addWidget(QLabel("标注文本 (Annotation Text):"))
        label_layout.addWidget(QLabel("层类型 (Tier Type):"))
        
        input_layout = QVBoxLayout()
        input_layout.addWidget(self.tier_name_input)
        input_layout.addWidget(self.annotation_input)
        input_layout.addWidget(self.tier_type_combo)
        
        form_layout.addLayout(label_layout)
        form_layout.addLayout(input_layout)
        
        # --- Buttons ---
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        
        layout.addLayout(form_layout)
        layout.addWidget(self.button_box)

    def get_settings(self):
        """返回用户配置的设置。"""
        return {
            "tier_name": self.tier_name_input.text().strip(),
            "annotation": self.annotation_input.text().strip(),
            "tier_type": self.tier_type_combo.currentText()
        }

# ==============================================================================
# 插件主类 (核心修改)
# ==============================================================================
class PraatExporterPlugin(BasePlugin):
    """
    Praat TextGrid 导出器插件 (v2.1 带详细注释)。
    功能:
    - 导出选区为 Praat .TextGrid 文件。
    - 支持自定义层名称和标注内容。
    - 支持 IntervalTier 和 PointTier。
    - 支持向现有 TextGrid 文件中追加标注。
    """

    def setup(self):
        """设置插件，注入菜单项。"""
        try:
            self.audio_analysis_page = getattr(self.main_window, 'audio_analysis_page', None)
            if not self.audio_analysis_page: return False
            self.spectrogram_widget = getattr(self.audio_analysis_page, 'spectrogram_widget', None)
            if not self.spectrogram_widget: return False

            # --- 加载插件图标 ---
            meta = self.plugin_manager.available_plugins.get('com.phonacq.praat_exporter')
            icon_path = os.path.join(meta['path'], 'export_textgrid.png') if meta else ""
            
            if os.path.exists(icon_path):
                plugin_icon = QIcon(icon_path)
            else:
                # 使用一个后备的通用图标
                plugin_icon = QIcon.fromTheme("document-save-as")

            # --- 创建 Action (已更新Tooltip) ---
            self.export_action = QAction(plugin_icon, "导出选区为 TextGrid...", self.spectrogram_widget)
            self.export_action.setToolTip(
                "将当前在语谱图上选择的时间范围导出一个 Praat .TextGrid 文件。\n"
                "此功能支持创建新文件，或向一个已存在的 TextGrid 文件中追加新的标注层或标注点。"
            )
            self.export_action.triggered.connect(self.execute)

            # --- 注入到右键菜单 ---
            self.original_context_menu_event = self.spectrogram_widget.contextMenuEvent
            self.spectrogram_widget.contextMenuEvent = self.custom_context_menu_event
            
            print("[Praat Exporter] 插件已成功设置。")
            return True
        except Exception as e:
            print(f"[Praat Exporter] 设置失败: {e}")
            return False

    def teardown(self):
        """卸载插件，恢复原始的右键菜单事件。"""
        if hasattr(self, 'spectrogram_widget') and hasattr(self, 'original_context_menu_event'):
            self.spectrogram_widget.contextMenuEvent = self.original_context_menu_event
            print("[Praat Exporter] 插件已成功卸载。")

    def custom_context_menu_event(self, event):
        """自定义的右键菜单事件处理器。"""
        # 假设目标模块提供了一个创建菜单的方法
        menu = self.spectrogram_widget.create_context_menu()
        
        has_selection = self.audio_analysis_page.current_selection is not None
        self.export_action.setEnabled(has_selection)
        
        menu.addSeparator()
        menu.addAction(self.export_action)
        
        menu.exec_(self.spectrogram_widget.mapToGlobal(event.pos()))

    def execute(self, **kwargs):
        """
        当用户点击菜单项时执行，弹出配置对话框并处理导出逻辑。
        """
        if self.audio_analysis_page.current_selection is None:
            QMessageBox.warning(self.main_window, "无法导出", "请先在“音频分析”页面的语谱图上加载音频并用鼠标拖拽选择一个区域。")
            return

        # 1. 弹出配置对话框
        dialog = ExportDialog(self.main_window)
        if not dialog.exec_():
            return  # 用户取消

        settings = dialog.get_settings()
        if not settings["tier_name"]:
            QMessageBox.warning(self.main_window, "输入无效", "层名称不能为空。")
            return

        # 2. 获取数据
        start_sample, end_sample = self.audio_analysis_page.current_selection
        sr = self.audio_analysis_page.sr
        settings['start_time'] = start_sample / sr
        settings['end_time'] = end_sample / sr
        settings['total_duration'] = len(self.audio_analysis_page.audio_data) / sr

        # 3. 弹出文件保存对话框
        base_name = os.path.splitext(os.path.basename(self.audio_analysis_page.current_filepath))[0]
        default_path = os.path.join(os.path.dirname(self.audio_analysis_page.current_filepath), f"{base_name}.TextGrid")
        save_path, _ = QFileDialog.getSaveFileName(self.main_window, "保存 TextGrid 文件", default_path, "TextGrid 文件 (*.TextGrid)")

        if not save_path:
            return

        # 4. 核心处理逻辑：创建或更新 TextGrid
        try:
            self._process_textgrid_file(save_path, settings)
            QMessageBox.information(self.main_window, "导出成功", f"TextGrid 文件已成功保存至:\n{save_path}")
        except Exception as e:
            QMessageBox.critical(self.main_window, "导出失败", f"处理 TextGrid 文件时发生错误:\n{e}")

    def _process_textgrid_file(self, path, settings):
        """
        处理TextGrid文件的主要逻辑：判断是创建、覆盖还是追加。
        """
        if not os.path.exists(path):
            # 文件不存在，直接创建
            content = self._create_new_textgrid(settings)
        else:
            # 文件存在，询问用户操作
            msg_box = QMessageBox(self.main_window)
            msg_box.setIcon(QMessageBox.Question)
            msg_box.setWindowTitle("文件已存在")
            msg_box.setText(f"文件 '{os.path.basename(path)}' 已存在。")
            msg_box.setInformativeText("您想覆盖它，还是在其中追加新的标注？")
            overwrite_button = msg_box.addButton("覆盖 (Overwrite)", QMessageBox.AcceptRole)
            append_button = msg_box.addButton("追加 (Append)", QMessageBox.YesRole)
            msg_box.addButton(QMessageBox.Cancel)
            msg_box.exec_()
            
            clicked_button = msg_box.clickedButton()
            if clicked_button == overwrite_button:
                content = self._create_new_textgrid(settings)
            elif clicked_button == append_button:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    existing_content = f.read()
                content = self._append_to_textgrid(existing_content, settings)
            else: # Cancel
                return

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

    def _create_new_textgrid(self, settings):
        """根据设置创建一个全新的TextGrid文件内容。"""
        total_duration = settings['total_duration']
        tier_name = settings['tier_name']
        
        header = f'''File type = "ooTextFile"
Object class = "TextGrid"

xmin = 0
xmax = {total_duration:.6f}
tiers? <exists> 
size = 1
item []: 
    item [1]:
'''
        if settings['tier_type'] == 'IntervalTier':
            start_time = settings['start_time']
            end_time = settings['end_time']
            annotation = settings['annotation']
            tier_content = f'''        class = "IntervalTier" 
        name = "{tier_name}" 
        xmin = 0 
        xmax = {total_duration:.6f}
        intervals: size = 3 
        intervals [1]:
            xmin = 0 
            xmax = {start_time:.6f} 
            text = "" 
        intervals [2]:
            xmin = {start_time:.6f} 
            xmax = {end_time:.6f} 
            text = "{annotation}" 
        intervals [3]:
            xmin = {end_time:.6f} 
            xmax = {total_duration:.6f} 
            text = "" 
'''
        else: # PointTier
            point_time = settings['start_time'] # 使用选区起点作为时间点
            annotation = settings['annotation']
            tier_content = f'''        class = "PointTier" 
        name = "{tier_name}" 
        xmin = 0 
        xmax = {total_duration:.6f}
        points: size = 1 
        points [1]:
            number = {point_time:.6f} 
            mark = "{annotation}" 
'''
        return header + tier_content

    def _append_to_textgrid(self, content, settings):
        """
        向现有的TextGrid内容中追加新的标注。
        这是一个简化的实现，主要处理层存在和不存在的情况。
        注意：这个实现不处理重叠标注的复杂情况。
        """
        lines = content.splitlines()
        tier_name = settings['tier_name']
        tier_type = settings['tier_type']
        
        # 查找是否已存在同名、同类型的层
        tier_index = -1
        tier_start_line = -1
        
        for i, line in enumerate(lines):
            if f'name = "{tier_name}"' in line:
                # 检查类型是否匹配
                if f'class = "{tier_type}"' in lines[i-1]:
                    tier_index = int(re.search(r'item \[(\d+)\]:', lines[i-2]).group(1))
                    tier_start_line = i - 2
                    break
        
        if tier_index != -1:
            # --- 层已存在，向其中添加标注 ---
            if tier_type == 'IntervalTier':
                # 简化处理：直接在末尾添加一个新的标注，这在实际中可能不理想
                # 一个完整的实现需要分割现有区间，非常复杂。
                # 这里我们采取一个折衷：在末尾添加一个新的标注区间
                size_line_index = -1
                for i in range(tier_start_line, len(lines)):
                    if "intervals: size =" in lines[i]:
                        size_line_index = i
                        break
                
                current_size = int(re.search(r'size = (\d+)', lines[size_line_index]).group(1))
                lines[size_line_index] = f'        intervals: size = {current_size + 1}'
                
                new_interval = f'''        intervals [{current_size + 1}]:
            xmin = {settings['start_time']:.6f}
            xmax = {settings['end_time']:.6f}
            text = "{settings['annotation']}"'''
                lines.append(new_interval)

            else: # PointTier
                size_line_index = -1
                for i in range(tier_start_line, len(lines)):
                    if "points: size =" in lines[i]:
                        size_line_index = i
                        break
                
                current_size = int(re.search(r'size = (\d+)', lines[size_line_index]).group(1))
                lines[size_line_index] = f'        points: size = {current_size + 1}'
                
                new_point = f'''        points [{current_size + 1}]:
            number = {settings['start_time']:.6f}
            mark = "{settings['annotation']}"'''
                lines.append(new_point)
        else:
            # --- 层不存在，添加一个新层 ---
            size_line_index = lines.index("tiers? <exists> ") + 1
            current_size = int(re.search(r'size = (\d+)', lines[size_line_index]).group(1))
            lines[size_line_index] = f'size = {current_size + 1}'
            
            # 创建新层的内容
            new_tier_header = f'''    item [{current_size + 1}]:'''
            
            # 使用创建新文件的逻辑来生成层内容
            new_tier_full_content = self._create_new_textgrid(settings)
            # 提取新层内容部分
            new_tier_body = '\n'.join(new_tier_full_content.split('item [1]:')[1].strip().splitlines())
            
            lines.append(new_tier_header)
            lines.append(new_tier_body)
            
        return '\n'.join(lines)

