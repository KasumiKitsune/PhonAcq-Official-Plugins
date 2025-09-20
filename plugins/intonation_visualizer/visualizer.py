# --- START OF COMPLETE AND REFACTORED FILE plugins/intonation_visualizer/visualizer.py ---

import os
import sys
import uuid
import pandas as pd
import numpy as np
from itertools import cycle
from copy import deepcopy
import re
import shutil
# PyQt5 核心 UI 模块
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog,
                             QMessageBox, QTableView, QHeaderView, QComboBox, QCheckBox,
                             QSplitter, QGroupBox, QLineEdit, QFormLayout,
                             QSlider, QWidget, QScrollArea, QMenu, QFrame,
                             QTableWidget, QTableWidgetItem, QAbstractItemView, QItemDelegate,
                             QApplication, QAction, QGridLayout, QDialogButtonBox)

# PyQt5 核心功能模块与图形绘制
from PyQt5.QtCore import Qt, QAbstractTableModel, pyqtSignal, QEvent, QSize, pyqtProperty, QTimer
from PyQt5.QtGui import QIcon, QColor, QPalette, QPixmap, QFont, QPainter, QCursor, QPen
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent

# Matplotlib 和 TextGrid 库导入
try:
    import matplotlib
    matplotlib.use('Qt5Agg') # 指定 Matplotlib 后端，确保与 PyQt5 兼容
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D # 用于绘制线段和点
    import textgrid # [NEW] 导入 TextGrid 库，用于处理 TextGrid 文件

    # 设置 Matplotlib 中文字体，避免乱码
    def set_matplotlib_font():
        font_candidates = ['Microsoft YaHei', 'SimHei', 'Source Han Sans CN', 'PingFang SC', 'WenQuanYi Micro Hei', 'Arial Unicode MS']
        from matplotlib.font_manager import findfont, FontProperties
        found_font = next((font for font in font_candidates if findfont(FontProperties(family=font))), None)
        if found_font:
            matplotlib.rcParams['font.sans-serif'] = [found_font] # 设置中文字体
            matplotlib.rcParams['axes.unicode_minus'] = False # 解决负号显示问题（例如 F0 可能有负值）
            print(f"[Intonation Visualizer] Found and set Chinese font: {found_font}")
        else:
            print("[Intonation Visualizer Warning] No suitable Chinese font found for Matplotlib.")
    set_matplotlib_font()
    LIBS_AVAILABLE = True # 标记所有核心依赖库是否可用
except ImportError as e:
    print(f"[Intonation Visualizer Error] Missing core libraries: {e}. Please run 'pip install matplotlib pandas numpy textgrid'")
    LIBS_AVAILABLE = False # 依赖缺失，禁用相关功能

# 插件API和自定义控件导入
try:
    from modules.plugin_system import BasePlugin
    from modules.custom_widgets_module import ColorButton, CustomColorPopup
except ImportError:
    # 如果在独立测试插件时，确保能找到 plugin_system.py 和 custom_widgets_module.py
    # 否则提供回退方案，避免崩溃
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    try:
        from plugin_system import BasePlugin
        from custom_widgets_module import ColorButton, CustomColorPopup
    except ImportError:
        print("[Intonation Visualizer Warning] Could not import custom widgets. Using fallback.")
        ColorButton = QPushButton # 回退到标准 QPushButton
        CustomColorPopup = QDialog # CustomColorPopup 没有简单回退，但其依赖 ColorButton 已被处理
# ==============================================================================
# [新增] 合并图层对话框 (MergeLayersDialog) - (从 plotter.py 移植)
# ==============================================================================
class MergeLayersDialog(QDialog):
    """一个简单的对话框，用于获取合并后新图层的名称。"""
    def __init__(self, num_layers, parent=None):
        super().__init__(parent)
        self.setWindowTitle("合并图层")
        
        layout = QVBoxLayout(self)
        label = QLabel(f"您正在合并 <b>{num_layers}</b> 个图层。")
        layout.addWidget(label)
        
        form_layout = QFormLayout()
        self.name_edit = QLineEdit("merged_intonation_layer") # 默认名称
        self.name_edit.setToolTip("请输入合并后新图层的名称。")
        form_layout.addRow("新图层名称:", self.name_edit)
        layout.addLayout(form_layout)
        
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_new_name(self):
        """获取用户输入的新图层名称。"""
        return self.name_edit.text().strip()
# ==============================================================================
# 辅助类：PandasModel
# ------------------------------------------------------------------------------
# 用于将 Pandas DataFrame 数据显示在 QTableView 中的模型。
# ==============================================================================
class PandasModel(QAbstractTableModel):
    def __init__(self, data):
        super().__init__()
        self._data = data

    def rowCount(self, parent=None):
        return self._data.shape[0]

    def columnCount(self, parent=None):
        return self._data.shape[1]

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role == Qt.DisplayRole:
            # 确保返回字符串，避免 QVariant 转换问题，保证显示兼容性
            return str(self._data.iloc[index.row(), index.column()])
        return None

    def headerData(self, section, orientation, role):
        if role == Qt.DisplayRole:
            if orientation == Qt.Horizontal:
                return str(self._data.columns[section])
            if orientation == Qt.Vertical:
                return str(self._data.index[section])
        return None

class ColorWidget(QWidget):
    # 定义一个信号，当颜色改变时发射，参数为新的 QColor 对象
    colorChanged = pyqtSignal(QColor)

    def __init__(self, color, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(50, 22) # 稍微增加高度以改善视觉效果
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点击更改颜色")
        self.popup = None # 用于存储颜色选择弹窗的引用

    def color(self):
        """返回当前颜色。"""
        return self._color

    def setColor(self, color):
        """设置新颜色并触发重绘。"""
        new_color = QColor(color)
        if self._color != new_color:
            self._color = new_color
            self.update()

    def paintEvent(self, event):
        """绘制颜色矩形。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(self._color)
        # 使用一个柔和的灰色边框
        painter.setPen(QPen(QColor("#AAAAAA"))) 
        # 绘制圆角矩形
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 5, 5)

    def mousePressEvent(self, event):
        """点击时弹出颜色选择器。"""
        if event.button() == Qt.LeftButton:
            # 确保同一时间只有一个弹窗
            if self.popup and self.popup.isVisible():
                self.popup.close()

            # 使用 CustomColorPopup（如果可用）来获得更好的体验
            self.popup = CustomColorPopup(initial_color=self._color, parent=self)
            self.popup.colorSelected.connect(self.on_color_selected)
            
            # 将弹窗定位在颜色块的下方
            self.popup.move(self.mapToGlobal(self.rect().bottomLeft()))
            self.popup.show()
            
    def on_color_selected(self, color):
        """当颜色选择器返回一个颜色时调用。"""
        if color.isValid() and color != self._color:
            self.setColor(color)
            self.colorChanged.emit(color) # 发射 colorChanged 信号

# ==============================================================================
# [NEW] 辅助类：ReadOnlyDelegate (从 plotter.py 移植)
# ------------------------------------------------------------------------------
# 这是一个QItemDelegate，用于使表格的某些列变为只读。
# ==============================================================================
class ReadOnlyDelegate(QItemDelegate):
    def createEditor(self, parent, option, index):
        return None # 返回None意味着此单元格不可编辑

# ==============================================================================
# [MODIFIED] 图层配置对话框 (LayerConfigDialog)
# ------------------------------------------------------------------------------
# 此对话框用于配置单个数据图层。它现在支持加载 TextGrid 文件，
# 并能够选择 DataFrame 中的列作为分组依据。
# ==============================================================================
class LayerConfigDialog(QDialog):
    def __init__(self, existing_config=None, parent=None):
        super().__init__(parent)
        self.df = None
        self.tg = None
        self.original_filepath = None
        
        # --- [核心修复] 移除 deepcopy，采用浅复制和手动数据恢复 ---
        self.config = existing_config.copy() if existing_config else {}
        if existing_config:
            # 手动恢复对大型数据对象的引用，而不是复制它们
            self.df = existing_config.get('df')
            self.tg = existing_config.get('tg')
            # 同样，手动恢复 audio_data
            self.config['audio_data'] = existing_config.get('audio_data')

        self.parent_dialog = parent
        self.setWindowTitle("配置数据图层")
        self.setMinimumWidth(500)
        self._init_ui()
        self._connect_signals()
        
        if self.config:
            self._populate_from_config()
        
        self._update_combos()

    def _init_ui(self):
        """初始化对话框的用户界面。"""
        layout = QVBoxLayout(self)
        form_layout = QFormLayout()
    
        self.name_edit = QLineEdit(self.config.get('name', ''))
        self.name_edit.setPlaceholderText("例如：陈述句-男声")
        self.name_edit.setToolTip("为该数据图层指定一个唯一的名称。")
    
        data_layout = QHBoxLayout()
        self.load_data_btn = QPushButton("选择文件...")
        self.load_data_btn.setToolTip("加载包含时间与F0数据的 Excel (.xlsx, .xls) 或 CSV (.csv) 文件。")
        self.data_file_label = QLabel(self.config.get('data_filename', "未选择"))
        self.data_file_label.setWordWrap(True)
        data_layout.addWidget(self.load_data_btn); data_layout.addWidget(self.data_file_label, 1)

        tg_layout = QHBoxLayout()
        self.load_tg_btn = QPushButton("选择文件...")
        self.load_tg_btn.setToolTip("加载 TextGrid (.TextGrid) 文件为数据点添加标签。\n数据文件必须包含 'timestamp' 列。")
        self.tg_file_label = QLabel(self.config.get('tg_filename', "未选择 (可选)"))
        self.tg_file_label.setWordWrap(True)
        tg_layout.addWidget(self.load_tg_btn); tg_layout.addWidget(self.tg_file_label, 1)

        # [新增] 创建用于选择TextGrid层的下拉菜单及其标签
        self.tg_tier_combo = QComboBox()
        self.tg_tier_combo.setToolTip("从加载的 TextGrid 文件中选择一个层(Tier)作为标注来源。")
        self.tg_tier_label = QLabel("选择层(Tier):")

        audio_layout = QHBoxLayout()
        self.load_audio_btn = QPushButton("选择音频文件...")
        self.load_audio_btn.setToolTip("为该图层加载关联的音频文件，以便进行片段播放。")
    
        audio_path = self.config.get('audio_path')
        audio_filename_display = os.path.basename(audio_path) if audio_path and isinstance(audio_path, str) else "未选择 (可选)"
        self.audio_file_label = QLabel(audio_filename_display)
        self.audio_file_label.setWordWrap(True)
    
        self.load_audio_btn.setEnabled(self.tg is not None)

        audio_layout.addWidget(self.load_audio_btn)
        audio_layout.addWidget(self.audio_file_label, 1)

        self.time_combo = QComboBox(); self.f0_combo = QComboBox(); self.group_by_combo = QComboBox()

        form_layout.addRow("图层名称:", self.name_edit)
        form_layout.addRow("数据文件:", data_layout)
        form_layout.addRow("TextGrid:", tg_layout)
        # [修改] 将新创建的Tier选择功能添加到布局中
        form_layout.addRow(self.tg_tier_label, self.tg_tier_combo)
        form_layout.addRow("音频文件:", audio_layout)
        form_layout.addRow(QFrame(frameShape=QFrame.HLine))
        form_layout.addRow("时间 (X轴):", self.time_combo)
        form_layout.addRow("F0 (Y轴):", self.f0_combo)
        form_layout.addRow("分组依据:", self.group_by_combo)

        # [新增] 初始时隐藏Tier选择功能，直到加载TextGrid后才显示
        self.tg_tier_label.hide()
        self.tg_tier_combo.hide()

        layout.addLayout(form_layout)
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept); button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _connect_signals(self):
        """连接UI控件的信号到槽函数。"""
        self.load_data_btn.clicked.connect(self._load_data)
        self.load_tg_btn.clicked.connect(self._load_textgrid)
        self.load_audio_btn.clicked.connect(self._load_audio)
        self.tg_tier_combo.currentTextChanged.connect(self._apply_selected_tier_to_df)

    def _load_audio(self):
        """
        [v11.1 - 健壮性修复版]
        加载音频文件，并直接将数据和路径存入 self.config。
        """
        filepath, _ = QFileDialog.getOpenFileName(self, "选择关联的音频文件", "", "音频文件 (*.wav *.mp3 *.flac)")
        if not filepath:
            return
        
        try:
            import librosa
            y, sr = librosa.load(filepath, sr=None, mono=True)
            
            # [核心修改] audio_path 现在存储的是原始绝对路径
            self.config['audio_path'] = filepath
            self.config['audio_data'] = (y, sr)
            
            self.audio_file_label.setText(os.path.basename(filepath))
            QMessageBox.information(self, "加载成功", f"音频文件 '{os.path.basename(filepath)}' 已成功关联。")
            
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法加载或处理音频文件:\n{e}")
            self.config.pop('audio_path', None)
            self.config.pop('audio_data', None)
            self.audio_file_label.setText("加载失败")

    def _populate_from_config(self):
        """
        [v2.1 - 顽固Bug最终修复版]
        根据传入的配置字典，完整地填充对话框中所有UI控件的状态。
        此版本修复了在初始化时，因信号未触发而导致分组依据不正确的时序问题。
        """
        # --- 步骤 1: 恢复基础数据和UI状态 (这部分不变) ---
        self.df = self.config.get('df')
        self.tg = self.config.get('tg')

        if 'data_filename' in self.config:
            self.data_file_label.setText(self.config['data_filename'])
        if 'tg_filename' in self.config:
            self.tg_file_label.setText(self.config['tg_filename'])

        self.load_audio_btn.setEnabled(self.tg is not None)

        audio_path = self.config.get('audio_path')
        if audio_path and isinstance(audio_path, str):
            self.audio_file_label.setText(os.path.basename(audio_path))
        else:
            self.audio_file_label.setText("未选择 (可选)")
        
        # --- 步骤 2: [核心修复] 处理Tier选择和应用的逻辑 ---
        tier_was_applied = False
        if self.tg:
            interval_tiers = [tier.name for tier in self.tg if isinstance(tier, textgrid.IntervalTier)]
            if interval_tiers:
                self.tg_tier_combo.addItems(interval_tiers)
                self.tg_tier_label.show()
                self.tg_tier_combo.show()
                
                # 恢复之前选择的Tier
                saved_tier = self.config.get('tg_tier')
                if saved_tier and saved_tier in interval_tiers:
                    self.tg_tier_combo.setCurrentText(saved_tier)
                
                # *** 这是最关键的修复 ***
                # 因为 programmatically setting the text does not emit the signal,
                # we must MANUALLY call the slot function to apply the tier to the DataFrame.
                self._apply_selected_tier_to_df()
                tier_was_applied = True

        # --- 步骤 3: 更新列选择下拉框 ---
        # 如果没有TextGrid或者没有可用的Tier，我们才需要用旧的group_col来更新
        if not tier_was_applied:
             self._update_combos(preferred_group_col=self.config.get('group_col'))
        
        # --- 步骤 4: 恢复时间列和F0列的选择 (这部分可以保持) ---
        # 确保即使在_update_combos自动猜测后，也以保存的配置为准
        if self.config.get('time_col'):
            self.time_combo.setCurrentText(self.config.get('time_col'))
        if self.config.get('f0_col'):
            self.f0_combo.setCurrentText(self.config.get('f0_col'))

    def _load_data(self):
        """
        加载数据文件（Excel、CSV或Praat .txt）到DataFrame。
        [已修改] 增加了对 Praat .txt 格式的支持，并记录原始文件路径。
        """
        path, _ = QFileDialog.getOpenFileName(self, "选择F0数据文件", "", "表格文件 (*.xlsx *.xls *.csv *.txt)")
        if not path: return
        
        try:
            df = None
            if path.lower().endswith(('.xlsx', '.xls')):
                df = pd.read_excel(path)
            elif path.lower().endswith('.csv'):
                df = pd.read_csv(path)
            else: # 默认为 Praat .txt 格式
                temp_df = pd.read_csv(path, delim_whitespace=True, na_values='--undefined--')

                f0_col_praat = next((c for c in temp_df.columns if 'f0' in c.lower() or 'frequency' in c.lower()), None)
                if f0_col_praat:
                    temp_df.dropna(subset=[f0_col_praat], inplace=True)
                
                rename_mapping = {}
                for col in temp_df.columns:
                    if 'time' in col.lower():
                        rename_mapping[col] = 'timestamp'
                    elif 'f0' in col.lower() or 'frequency' in col.lower():
                        rename_mapping[col] = 'f0_hz'
                temp_df.rename(columns=rename_mapping, inplace=True)
                
                temp_df.reset_index(drop=True, inplace=True)

                if 'timestamp' not in temp_df.columns or 'f0_hz' not in temp_df.columns:
                    QMessageBox.warning(self, "列名不匹配", "无法在文件中找到标准的时间和F0列。\n请确保列名包含 'Time' 和 'F0' 或 'Frequency'。")
                    return
                df = temp_df

            self.df = df
            self.original_filepath = path  # [新增] 记录加载文件的完整路径
            self.data_file_label.setText(os.path.basename(path))
            self.config['data_filename'] = os.path.basename(path)
            
            # --- [核心修改开始] ---
            # 只有当加载的文件名不是 info.txt 时，才清空 TextGrid 和音频
            # 并且只有当文件名是'info.txt'时，才不清空用户可能已输入的名称
            if os.path.basename(path).lower() != 'info.txt':
                if not self.name_edit.text():
                    self.name_edit.setText(os.path.splitext(os.path.basename(path))[0])
                
                # 清理旧的TextGrid数据
                self.tg = None
                self.tg_file_label.setText("未选择 (可选)")
                self.config.pop('tg_filename', None)
                self.config.pop('original_tg_path', None)
                
                # 隐藏并清空Tier选择UI
                self.tg_tier_label.hide()
                self.tg_tier_combo.hide()
                self.tg_tier_combo.clear()
            else:
                 # 如果是 info.txt，我们只更新数据，不清空其他设置
                 # 也不自动修改图层名称
                 pass
            # --- [核心修改结束] ---

            self._update_combos()
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法读取数据文件: {e}")
            self.df = None
            self.original_filepath = None # [新增] 加载失败时清空路径

    def _load_textgrid(self):
        """加载 TextGrid 文件并将其标签应用到 DataFrame。"""
        if self.df is None or 'timestamp' not in self.df.columns:
            QMessageBox.warning(self, "需要时间戳", "请先加载一个包含 'timestamp' 列的数据文件。")
            return
        
        # --- [核心修改开始] ---
        # 1. 获取推荐的默认目录
        default_dir = ""
        if self.parent_dialog:
            # 调用父对话框的辅助方法来获取路径
            _, textgrids_dir = self.parent_dialog._get_or_create_analysis_dirs()
            if textgrids_dir and os.path.isdir(textgrids_dir):
                default_dir = textgrids_dir
        
        # 2. 将获取到的目录作为第三个参数传入文件对话框
        path, _ = QFileDialog.getOpenFileName(self, "选择 TextGrid 文件", default_dir, "TextGrid 文件 (*.TextGrid)")
        # --- [核心修改结束] ---

        if not path: return
        try:
            self.tg = textgrid.TextGrid.fromFile(path)
            self.tg_file_label.setText(os.path.basename(path))
            self.config['tg_filename'] = os.path.basename(path)
            self.config['original_tg_path'] = path

            self.tg_tier_combo.clear()
            interval_tiers = [tier.name for tier in self.tg if isinstance(tier, textgrid.IntervalTier)]
            if interval_tiers:
                self.tg_tier_combo.addItems(interval_tiers)
                self.tg_tier_label.show()
                self.tg_tier_combo.show()
                self._apply_selected_tier_to_df() 
            else:
                QMessageBox.warning(self, "未找到有效的层", "此 TextGrid 文件中不包含任何 IntervalTier。")
                self.tg_tier_label.hide()
                self.tg_tier_combo.hide()

            self.load_audio_btn.setEnabled(True) 
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法解析 TextGrid 文件: {e}"); self.tg = None
            self.config.pop('original_tg_path', None)

    def _apply_selected_tier_to_df(self):
        """
        [重构] 将TextGrid中当前选中的层(Tier)的标注应用到DataFrame。
        会创建一个以层名称命名的动态列。
        """
        if self.df is None or self.tg is None: return

        selected_tier_name = self.tg_tier_combo.currentText()
        if not selected_tier_name: return

        # 1. [核心修复] 更精确地清理旧列
        # 只清理那些与当前TextGrid中Interval Tiers同名的列
        for tier in self.tg:
            if isinstance(tier, textgrid.IntervalTier) and tier.name in self.df.columns:
                self.df = self.df.drop(columns=[tier.name])
        
        # 兼容性清理，以防万一
        if 'textgrid_label' in self.df.columns:
             self.df = self.df.drop(columns=['textgrid_label'])

        # 2. 找到选中的Tier
        target_tier = self.tg.getFirst(selected_tier_name)
        if not target_tier or not isinstance(target_tier, textgrid.IntervalTier): return
        
        # 3. 创建新列并应用标注
        new_column_name = target_tier.name
        label_col = pd.Series(np.nan, index=self.df.index, dtype=object)
        
        for interval in target_tier:
            if interval.mark and interval.mark.strip(): 
                mask = (self.df['timestamp'] >= interval.minTime) & (self.df['timestamp'] < interval.maxTime)
                label_col.loc[mask] = interval.mark
        
        self.df[new_column_name] = label_col
        
        # 4. 更新UI，自动将分组依据设置为新创建的列
        self._update_combos(preferred_group_col=new_column_name) # [修改] 传递参数
        self.group_by_combo.setCurrentText(new_column_name)

    def _update_combos(self, preferred_group_col=None): # [修改] 增加可选参数
        """根据当前加载的 DataFrame 更新所有列选择的下拉选项。"""
        # --- [核心修改开始] ---
        # 1. 保存当前分组依据的值，防止被 clear() 清掉
        current_group_text = preferred_group_col if preferred_group_col else self.group_by_combo.currentText()
        # --- [核心修改结束] ---

        self.time_combo.clear(); self.f0_combo.clear(); self.group_by_combo.clear()
        self.group_by_combo.addItem("无分组")

        if self.df is None: return

        numeric_cols = self.df.select_dtypes(include=np.number).columns.tolist()
        all_cols = self.df.columns.tolist()

        self.time_combo.addItems(numeric_cols); self.f0_combo.addItems(numeric_cols)
        
        if all_cols: self.group_by_combo.addItems(all_cols)

        # 自动猜测时间列和F0列的逻辑保持不变
        time_auto = next((c for c in numeric_cols if 'time' in c.lower() or 'timestamp' in c.lower()), numeric_cols[0] if numeric_cols else "")
        f0_auto = next((c for c in numeric_cols if 'f0' in c.lower() or 'hz' in c.lower()), numeric_cols[1] if len(numeric_cols) > 1 else "")
        self.time_combo.setCurrentText(time_auto); self.f0_combo.setCurrentText(f0_auto)

        # --- [核心修改开始] ---
        # 2. 恢复或设置分组依据
        # 检查之前保存的值是否存在于新的列名列表中
        if current_group_text and current_group_text in all_cols:
            self.group_by_combo.setCurrentText(current_group_text)
        elif 'textgrid_label' in all_cols: # 兼容旧逻辑
            self.group_by_combo.setCurrentText('textgrid_label')
        else: 
            # 如果之前的选择不存在，再执行自动猜测作为后备方案
            self.group_by_combo.setCurrentText(next((c for c in all_cols if 'group' in c.lower() or 'label' in c.lower()), "无分组"))
        # --- [核心修改结束] ---

    def get_layer_config(self):
        """
        从UI控件收集信息，并与已有的数据引用合并成最终配置。
        [已修改] 增加了在确认时自动重命名 info.txt 的逻辑。
        """
        if self.df is None:
            QMessageBox.warning(self, "输入无效", "请先加载数据文件。")
            return None
        
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "输入无效", "请输入图层名称。")
            return None
        
        # --- [核心修改开始] 自动重命名 info.txt 的逻辑 ---
        final_data_filename = self.data_file_label.text()
        # 检查是否加载了文件，且文件名是 info.txt
        if self.original_filepath and os.path.basename(self.original_filepath).lower() == 'info.txt':
            dir_path = os.path.dirname(self.original_filepath)
            new_filename = f"{name}.txt"
            new_filepath = os.path.join(dir_path, new_filename)

            # 检查目标文件是否已存在 (除了它自己)
            if os.path.exists(new_filepath) and new_filepath != self.original_filepath:
                 reply = QMessageBox.question(self, 
                                              "文件已存在", 
                                              f"文件 '{new_filename}' 已存在于同一目录下。是否要覆盖它？", 
                                              QMessageBox.Yes | QMessageBox.No, 
                                              QMessageBox.No)
                 if reply == QMessageBox.No:
                     return None # 用户取消，中止操作

            try:
                # 执行重命名
                os.rename(self.original_filepath, new_filepath)
                # 更新配置中要保存的文件名
                final_data_filename = new_filename
                # 更新原始路径记录，以防用户再次打开配置
                self.original_filepath = new_filepath
            except Exception as e:
                QMessageBox.critical(self, "重命名失败", f"无法将 info.txt 重命名为 '{new_filename}':\n{e}")
                return None # 重命名失败，中止操作
        # --- [核心修改结束] ---

        final_config = {
            'df': self.df,
            'tg': self.tg,
            'audio_data': self.config.get('audio_data') 
        }

        ui_config = {
            "name": name,
            "data_filename": final_data_filename, # [修改] 使用可能已更新的文件名
            "tg_filename": self.tg_file_label.text(),
            "tg_tier": self.tg_tier_combo.currentText(),
            "audio_path": self.config.get('audio_path'),
            "time_col": self.time_combo.currentText(),
            "f0_col": self.f0_combo.currentText(),
            "group_col": self.group_by_combo.currentText(),
        }

        merged_config = self.config.copy()
        merged_config.update(final_config)
        merged_config.update(ui_config)
        
        merged_config.pop('player', None)
        
        return merged_config

# ==============================================================================
# 核心UI类：语调可视化器 (VisualizerDialog)
# ------------------------------------------------------------------------------
# 这是一个多功能对话框，用于加载、处理、可视化F0和强度数据，
# 支持多图层、分组显示、时间/F0归一化、曲线平滑和平均轮廓线。
# ==============================================================================
class VisualizerDialog(QDialog):
    # 定义所有颜色方案 (与 plotter.py 保持一致)
    COLOR_SCHEMES = {
        "默认": ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'],
        "色觉友好": ['#332288', '#117733', '#44AA99', '#88CCEE', '#DDCC77', '#CC6677', '#AA4499', '#882255'],
        "经典亮色": ['#e41a1c', '#377eb8', '#4daf4a', '#984ea3', '#ff7f00', '#a65628', '#f781bf'],
        "柔和色盘": ['#8dd3c7', '#ffffb3', '#bebada', '#fb8072', '#80b1d3', '#fdb462', '#b3de69', '#fccde5', '#d9d9d9', '#bc80bd'],
        "复古风格": ['#588c7e', '#f2e394', '#f2ae72', '#d96459', '#8c4646', '#424254', '#336b87', '#90afc5'],
        "商务蓝调": ['#003f5c', '#374c80', '#7a5195', '#bc5090', '#ef5675', '#ff764a', '#ffa600'],
        "科学渐变 (Viridis)": ['#440154', '#482878', '#3e4989', '#31688e', '#26828e', '#1f9e89', '#35b779', '#6dcd59', '#b4de2c', '#fde725']
    }
    
    # 支持拖拽的文件类型 (增加了 .TextGrid)
    SUPPORTED_EXTENSIONS = ('.csv', '.xlsx', '.xls', '.TextGrid', '.txt') # [修改]
    PLUGIN_LAYER_TYPE = "intonation"
    def __init__(self, parent=None, icon_manager=None):
        """
        初始化语调可视化器对话框。
        :param parent: 父 QWidget，通常是主窗口实例。
        :param icon_manager: 用于获取图标的 IconManager 实例。
        """
        super().__init__(parent)
        # 检查依赖库是否可用，如果不可用则立即关闭对话框
        if not LIBS_AVAILABLE:
            QMessageBox.critical(self, "依赖缺失", "需要 'matplotlib', 'pandas', 'numpy', 'textgrid' 库。\n请运行: pip install matplotlib pandas numpy textgrid")
            QTimer.singleShot(0, self.reject); return

        self.setWindowTitle("语调可视化")
        self.resize(1400, 900); self.setMinimumSize(1200, 750)
        self.icon_manager = icon_manager
        self.project_temp_dir = None
        self.plugin_id = "com.phonacq.intonation_visualizer"
        # --- [核心新增] 获取并存储项目的结果目录路径 ---
        self.project_results_dir = None
        if parent and hasattr(parent, 'config'):
            # 从主窗口的配置中获取 'results_dir'
            self.project_results_dir = parent.config.get('file_settings', {}).get('results_dir')
         # [核心新增] 创建一个临时目录用于存放播放的音频片段
        from tempfile import mkdtemp
        self.temp_audio_dir = mkdtemp(prefix="visualizer_audio_")       
        # --- 核心数据结构 ---
        self.layers = []
        self.current_selected_layer_index = -1 # 当前在图层表格中选中的图层索引
        
        # [NEW] 全局分组设置：存储所有活跃分组的样式和启用状态
        self.global_groups = {} 
        # [NEW] 颜色循环器：为新添加的图层和新发现的分组分配默认颜色
        self.color_cycler = cycle(self.COLOR_SCHEMES['默认']) 
        
        self.plotted_lines = [] # 存储 Matplotlib 绘制的 Line2D 对象，用于鼠标交互 (悬浮提示)

        # --- [核心修改] 新增交互功能的状态变量 ---
        self._is_panning = False 
        self._pan_start_pos = None 
        self.hover_annotation = None
        self.rect_selector = None # 用于存储矩形选择器实例
        self.show_ignore_mode_info = True # 用于控制提示框是否显示
        # --- 结束修改 ---
        # 初始化UI和连接信号
        self._init_ui()
        self._connect_signals()
        self._update_ui_state() # 初始化UI控件的可用状态
        self.group_table.installEventFilter(self)
        
        # 拖拽功能设置
        self.setAcceptDrops(True)
        self._create_drop_overlay() # 创建拖拽提示覆盖层

    def _get_or_create_analysis_dirs(self):
        """
        [v3.5 - 路径简化版]
        一个健壮的辅助方法，用于获取或创建标准的分析子目录。
        - 图表现在直接保存到 `charts` 目录，不再创建 `intonation` 子目录。
        """
        if not self.project_results_dir or not os.path.isdir(self.project_results_dir):
            return None, None

        try:
            analyze_base_dir = os.path.join(self.project_results_dir, 'analyze')
            
            # --- [核心修改] ---
            # 直接使用 charts 目录，不再创建 intonation 子目录
            charts_dir = os.path.join(analyze_base_dir, 'charts')
            # --- 结束修改 ---
            
            textgrids_dir = os.path.join(analyze_base_dir, 'textgrids')

            os.makedirs(charts_dir, exist_ok=True)
            os.makedirs(textgrids_dir, exist_ok=True)
            
            return charts_dir, textgrids_dir
        except Exception as e:
            print(f"[Intonation Visualizer ERROR] Failed to create analysis directories: {e}")
            return None, None

    def _create_drop_overlay(self):
        """创建用于拖拽提示的覆盖层。"""
        self.drop_overlay = QLabel(f"拖拽 {', '.join(self.SUPPORTED_EXTENSIONS)} 文件到此处", self)
        self.drop_overlay.setAlignment(Qt.AlignCenter)
        font = self.font(); font.setPointSize(20); font.setBold(True); self.drop_overlay.setFont(font)
        
        palette = self.palette()
        text_color = palette.color(QPalette.BrightText).name()
        bg_color = palette.color(QPalette.Highlight).name()
        
        self.drop_overlay.setStyleSheet(f"""
            QLabel {{
                background-color: rgba({QColor(bg_color).red()}, {QColor(bg_color).green()}, {QColor(bg_color).blue()}, 180);
                color: {text_color};
                border: 2px dashed {text_color};
                border-radius: 15px;
            }}
        """)
        self.drop_overlay.hide() # 初始状态隐藏

    def _init_ui(self):
        """初始化主界面的布局和控件。"""
        main_layout = QHBoxLayout(self)
        self.left_panel = self._create_left_panel() # 左侧面板：图层管理器
        
        # 中心区域使用 QSplitter 分割画布和数据预览表格
        center_splitter = QSplitter(Qt.Vertical)
        
        # Matplotlib 画布区域
        self.figure = Figure()
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setContextMenuPolicy(Qt.CustomContextMenu) # 启用右键菜单
        self.canvas.setToolTip("图表区域。\n- 左键拖动可平移视图\n- Ctrl+滚轮可缩放\n- 右键可打开菜单")
        # --- [核心修改-步骤1] 创建一个容器来包装表格和复选框 ---
        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(5)
        
        # 数据预览表格
        self.table_view = QTableView()
        self.table_view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch) # 列宽自适应
        self.table_view.setToolTip("当前选中图层的数据预览。")
        
        # --- [核心修改-步骤2] 创建新的控制复选框 ---
        self.show_all_data_check = QCheckBox("显示所有数据行 (包括 NaN)")
        self.show_all_data_check.setToolTip("取消勾选，则只显示通过TextGrid成功标注的有效数据行。")
        self.show_all_data_check.setChecked(False) # 默认不勾选，即只显示有效行

        # --- [核心修改-步骤3] 将表格和复选框添加到容器布局中 ---
        table_layout.addWidget(self.table_view)
        table_layout.addWidget(self.show_all_data_check)
        
        center_splitter.addWidget(self.canvas)
        center_splitter.addWidget(table_container) # <-- 将容器添加到分割器中
        center_splitter.setSizes([600, 200])
        
        self.right_panel = self._create_right_panel() # 右侧面板：设置选项
        
        # 将三个主面板添加到主布局
        main_layout.addWidget(self.left_panel); main_layout.addWidget(center_splitter, 1); main_layout.addWidget(self.right_panel)

    def _create_left_panel(self):
        """
        [v3.2 - Checkbox恢复版]
        创建左侧面板。
        - group_table 恢复为3列，包含“显示”复选框。
        - 表头仍然隐藏。
        """
        panel = QWidget()
        panel.setFixedWidth(450)
        layout = QVBoxLayout(panel)
        
        combined_group = QGroupBox("图层与分组")
        combined_layout = QVBoxLayout(combined_group)

        splitter = QSplitter(Qt.Vertical)

        layer_container = QWidget()
        layer_container_layout = QVBoxLayout(layer_container)
        layer_container_layout.setContentsMargins(0, 0, 0, 0)

        self.layer_table = QTableWidget()
        self.layer_table.setColumnCount(2)
        self.layer_table.setHorizontalHeaderLabels(["图层名称", "分组依据"])
        self.layer_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.layer_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.layer_table.setColumnWidth(1, 120)
        self.layer_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.layer_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.layer_table.verticalHeader().setVisible(False)
        self.layer_table.setToolTip("右键单击进行操作，双击名称可配置图层。")
        self.layer_table.setContextMenuPolicy(Qt.CustomContextMenu)
        
        btn_layout = QHBoxLayout()
        self.add_layer_btn = QPushButton(" 添加新图层...")
        if self.icon_manager:
            self.add_layer_btn.setIcon(self.icon_manager.get_icon("add_row"))
        self.add_layer_btn.setAutoDefault(False)
        btn_layout.addWidget(self.add_layer_btn)
        btn_layout.addStretch()
        
        layer_container_layout.addWidget(self.layer_table)
        layer_container_layout.addLayout(btn_layout)

        group_container = QWidget()
        group_container_layout = QVBoxLayout(group_container)
        group_container_layout.setContentsMargins(0, 0, 0, 0)

        self.group_table = QTableWidget()
        # --- [核心修改 1] ---
        self.group_table.setColumnCount(2) # 名称 和 显示Checkbox
        self.group_table.horizontalHeader().setVisible(False)
        self.group_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.group_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents) # Checkbox自适应宽度
        # --- 结束修改 ---
        
        self.group_table.verticalHeader().setVisible(False)
        self.group_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.group_table.setToolTip("使用Ctrl/Shift进行多选，然后右键单击进行批量操作。")
        self.group_table.setContextMenuPolicy(Qt.CustomContextMenu)

        self.auto_emphasize_check = QCheckBox("选择即强调 (单选模式)")
        self.auto_emphasize_check.setToolTip(
            "勾选此项:\n- 启用“选择即强调”模式。\n- 列表将切换为单选模式以获得最佳性能。\n\n"
            "取消勾选:\n- 禁用自动强调。\n- 列表将切换回多选模式，以便进行批量操作。"
        )
        self.auto_emphasize_check.setChecked(True)

        group_container_layout.addWidget(self.group_table)
        group_container_layout.addWidget(self.auto_emphasize_check)
        
        splitter.addWidget(layer_container)
        splitter.addWidget(group_container)
        splitter.setSizes([600, 200])

        combined_layout.addWidget(splitter)
        layout.addWidget(combined_group, 1)

        action_group = QGroupBox("绘图操作")
        action_layout = QVBoxLayout(action_group)
        self.plot_button = QPushButton(" 更新图表")
        self.plot_button.setToolTip("根据当前的设置重新绘制图表。")
        if self.icon_manager:
            self.plot_button.setIcon(self.icon_manager.get_icon("refresh"))
        self.plot_button.setAutoDefault(False)
        
        action_layout.addWidget(self.plot_button)
        layout.addWidget(action_group)
        
        return panel

    def _save_project_to_file(self, plugin_id, target_filepath):
        """
        [内置完整版] 将当前对话框的状态保存为 .pavp 工程文件。
        """
        import tempfile, shutil, os, json, uuid, pandas as pd
        from datetime import datetime

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = os.path.join(temp_dir, 'data')
            textgrids_dir = os.path.join(temp_dir, 'textgrids')
            audio_dir = os.path.join(temp_dir, 'audio')
            os.makedirs(data_dir); os.makedirs(textgrids_dir); os.makedirs(audio_dir)

            json_layers = []
            for layer_config in self.layers:
                layer_id = layer_config.get('id', str(uuid.uuid4()))
                layer_config['id'] = layer_id

                df = layer_config.get('df')
                data_source_path = None
                if df is not None and not df.empty:
                    csv_filename = f"{layer_id}.csv"
                    df.to_csv(os.path.join(data_dir, csv_filename), index=False)
                    data_source_path = f"data/{csv_filename}"

                tg_path_relative = None
                original_tg_path = layer_config.get('original_tg_path')
                if original_tg_path and os.path.exists(original_tg_path):
                     tg_filename = os.path.basename(original_tg_path)
                     shutil.copy(original_tg_path, os.path.join(textgrids_dir, tg_filename))
                     tg_path_relative = f"textgrids/{tg_filename}"

                audio_path_relative = None
                original_audio_path = layer_config.get('audio_path')
                if original_audio_path and os.path.exists(original_audio_path):
                     audio_filename = os.path.basename(original_audio_path)
                     shutil.copy(original_audio_path, os.path.join(audio_dir, audio_filename))
                     audio_path_relative = f"audio/{audio_filename}"

                json_layer = {
                    "id": layer_id, "name": layer_config['name'],
                    "type": self.PLUGIN_LAYER_TYPE,
                    "data_source_path": data_source_path,
                    "textgrid_path": tg_path_relative,
                    "audio_path": audio_path_relative,
                    "config": {"enabled": layer_config.get('enabled', True)},
                    "plugin_specific_config": {
                        plugin_id: self.get_plugin_specific_layer_config(layer_config)
                    }
                }
                json_layers.append(json_layer)

            project_json = {
                "project_format_version": "2.0", "pavp_version": "1.0",
                "created_at": datetime.utcnow().isoformat() + "Z",
                "created_by_plugin": plugin_id,
                "global_settings": self.get_global_settings(),
                "plugin_specific_settings": {
                    plugin_id: self.get_plugin_specific_global_settings()
                },
                "layers": json_layers
            }
            with open(os.path.join(temp_dir, 'project.json'), 'w', encoding='utf-8') as f:
                json.dump(project_json, f, indent=4)
            
            base_name = os.path.splitext(target_filepath)[0]
            zip_path = shutil.make_archive(base_name, 'zip', temp_dir)
            if os.path.exists(target_filepath):
                os.remove(target_filepath)
            os.rename(zip_path, target_filepath)

    def _open_project_from_file(self, filepath):
        """
        [内置完整版] 打开一个 .pavp 工程文件并返回其内容和临时解压路径。
        """
        import tempfile, shutil, os, json, zipfile
        
        if hasattr(self, 'project_temp_dir') and self.project_temp_dir:
            shutil.rmtree(self.project_temp_dir, ignore_errors=True)

        temp_dir = tempfile.mkdtemp(prefix="pavp_proj_")
        
        try:
            with zipfile.ZipFile(filepath, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            
            project_json_path = os.path.join(temp_dir, 'project.json')
            if not os.path.exists(project_json_path):
                raise FileNotFoundError("工程文件损坏：缺少 project.json。")
                
            with open(project_json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            return data, temp_dir
        except Exception as e:
            shutil.rmtree(temp_dir)
            raise e

    def get_global_settings(self):
        """
        [完整版] 辅助方法: 收集所有通用全局设置。
        """
        return {
            "title": self.title_edit.text(),
            "xlabel": self.xlabel_edit.text(),
            "ylabel": self.ylabel_edit.text(),
            "show_legend": self.show_legend_check.isChecked(),
        }

    def get_plugin_specific_global_settings(self):
        """
        [完整版] 辅助方法: 收集本插件专属的全局设置。
        """
        return {
            "normalize_time": self.normalize_time_check.isChecked(),
            "interpolate_gaps": self.interpolate_gaps_check.isChecked(),
            "f0_normalization": {
                "method": self.norm_combo.currentText(),
                "st_ref_hz": self.st_ref_edit.text(),
                "z_score_scope": self.z_scope_combo.currentText(),
            },
            "mean_contour": {
                "enabled": self.show_mean_contour_check.isChecked(),
                "average_only": self.show_average_only_check.isChecked(),
            }
        }

    def get_plugin_specific_layer_config(self, layer_config):
        """
        [完整版] 辅助方法: 收集本插件专属的所有图层设置。
        """
        return {
            "time_col": layer_config.get('time_col'),
            "f0_col": layer_config.get('f0_col'),
            "group_col": layer_config.get('group_col'),
            "smoothing_enabled": layer_config.get('smoothing_enabled', True),
            "smoothing_window": layer_config.get('smoothing_window', 4),
            "show_points": layer_config.get('show_points', False),
            "point_size": layer_config.get('point_size', 10),
            "point_alpha": layer_config.get('point_alpha', 0.4),
        }

    def _restore_state_from_pavp(self, data, temp_dir):
        """
        [完整版] 根据.pavp文件恢复整个对话框的状态。
        """
        import pandas as pd
        import librosa
        import textgrid
        from itertools import cycle
        from PyQt5.QtGui import QColor

        self._clear_all_data()
        self.project_temp_dir = temp_dir
        
        # 恢复通用全局设置
        gs = data.get('global_settings', {})
        self.title_edit.setText(gs.get('title', '语调曲线对比'))
        self.xlabel_edit.setText(gs.get('xlabel', '时间'))
        self.ylabel_edit.setText(gs.get('ylabel', 'F0'))
        self.show_legend_check.setChecked(gs.get('show_legend', True))

        # 恢复插件专属全局设置
        ps = data.get('plugin_specific_settings', {}).get(self.plugin_id, {})
        self.normalize_time_check.setChecked(ps.get('normalize_time', False))
        self.interpolate_gaps_check.setChecked(ps.get('interpolate_gaps', True))
        
        f0_norm = ps.get('f0_normalization', {})
        self.norm_combo.setCurrentText(f0_norm.get('method', "原始值 (Hz)"))
        self.st_ref_edit.setText(f0_norm.get('st_ref_hz', '100'))
        self.z_scope_combo.setCurrentText(f0_norm.get('z_score_scope', "按分组"))
        
        mean_contour = ps.get('mean_contour', {})
        self.show_mean_contour_check.setChecked(mean_contour.get('enabled', False))
        self.show_average_only_check.setChecked(mean_contour.get('average_only', False))
        
        # 恢复图层数据模型
        for layer_json in data.get('layers', []):
            if layer_json.get('type') != self.PLUGIN_LAYER_TYPE:
                continue
            
            layer_config = {}
            layer_config.update(layer_json.get('config', {}))
            layer_config.update(layer_json.get('plugin_specific_config', {}).get(self.plugin_id, {}))
            layer_config['id'] = layer_json.get('id', str(uuid.uuid4()))
            layer_config['name'] = layer_json.get('name', '未命名图层')
            
            if layer_json.get('data_source_path'):
                csv_path = os.path.join(temp_dir, layer_json['data_source_path'])
                if os.path.exists(csv_path):
                    layer_config['df'] = pd.read_csv(csv_path)
                    layer_config['data_filename'] = f"{layer_json['name']} (来自工程)"

            predefined_tg_path = None
            if layer_json.get('textgrid_path'):
                predefined_tg_path = os.path.join(temp_dir, layer_json['textgrid_path'])
            self._auto_match_textgrid_for_layer(layer_config, predefined_tg_path)
            
            if layer_json.get('audio_path'):
                audio_path_abs = os.path.join(temp_dir, layer_json['audio_path'])
                if os.path.exists(audio_path_abs):
                    try:
                        y, sr = librosa.load(audio_path_abs, sr=None, mono=True)
                        layer_config['audio_data'] = (y, sr)
                        layer_config['audio_path'] = audio_path_abs
                    except Exception as e:
                         print(f"Error loading audio for layer '{layer_config['name']}': {e}")
            
            self.layers.append(layer_config)

        # 所有数据模型加载完毕后，进行一次性的、彻底的UI刷新
        self._update_layer_table()
        self._update_all_group_settings()

        if self.layers:
            self.layer_table.selectRow(0)
        
        self._update_plot()

    def _create_right_panel(self):
        """
        [v3.1 - UI对齐plotter版]
        创建右侧面板，将全局分组样式面板恢复到此处。
        """
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFixedWidth(420); scroll.setFrameShape(QScrollArea.NoFrame)
        panel = QWidget(); layout = QVBoxLayout(panel); scroll.setWidget(panel)

        global_group = QGroupBox("全局设置"); global_layout = QFormLayout(global_group)
        self.title_edit = QLineEdit("语调曲线对比"); self.xlabel_edit = QLineEdit("时间"); self.ylabel_edit = QLineEdit("F0")
        self.title_edit.setToolTip("设置图表的总标题。"); self.xlabel_edit.setToolTip("设置图表 X 轴的标签文本。"); self.ylabel_edit.setToolTip("设置图表 Y 轴的标签文本。")
        global_layout.addRow("标题:", self.title_edit); global_layout.addRow("X轴标签:", self.xlabel_edit); global_layout.addRow("Y轴标签:", self.ylabel_edit)
        
        self.show_legend_check = QCheckBox("显示图例"); self.show_legend_check.setChecked(True); self.show_legend_check.setToolTip("是否在图表上显示图例（仅在分组时有效）。")
        global_layout.addRow(self.show_legend_check)

        self.normalize_time_check = QCheckBox("时间归一化 (0-100%)"); self.normalize_time_check.setToolTip("勾选后，每条曲线的时间轴将被归一化到0-100%，\n便于对比不同时长的句子的语调形态。")
        global_layout.addRow(self.normalize_time_check)
        
        self.interpolate_gaps_check = QCheckBox("插值填充F0间隙"); self.interpolate_gaps_check.setChecked(True); self.interpolate_gaps_check.setToolTip("勾选后，将使用线性插值填充F0数据中的无声间隙(NaN)，\n使曲线看起来更连续、平滑。")
        global_layout.addRow(self.interpolate_gaps_check)

        self.norm_combo = QComboBox(); self.norm_combo.addItems(["原始值 (Hz)", "半音 (Semitone)", "Z-Score"])
        self.norm_combo.setToolTip("选择对F0值进行变换的方式：\n- 原始值: 不做任何处理。\n- 半音: 转换为对数尺度的半音。\n- Z-Score: 对F0进行标准化。")
        
        self.st_ref_edit = QLineEdit("100"); self.st_ref_edit.setToolTip("半音归一化的参考基频，单位Hz。")
        self.st_param_widget = QWidget(); st_layout = QHBoxLayout(self.st_param_widget); st_layout.setContentsMargins(0,0,0,0); st_layout.addWidget(QLabel("基准(Hz):")); st_layout.addWidget(self.st_ref_edit)
        
        self.z_scope_combo = QComboBox(); self.z_scope_combo.addItems(["按分组", "按整个数据集"])
        self.z_scope_combo.setToolTip("Z-Score归一化的统计范围。")
        self.z_param_widget = QWidget(); z_layout = QHBoxLayout(self.z_param_widget); z_layout.setContentsMargins(0,0,0,0); z_layout.addWidget(QLabel("范围:")); z_layout.addWidget(self.z_scope_combo)
        
        self.st_param_widget.setVisible(False); self.z_param_widget.setVisible(False)
        
        global_layout.addRow("F0归一化:", self.norm_combo); global_layout.addRow(self.st_param_widget); global_layout.addRow(self.z_param_widget)

        self.layer_settings_group = QGroupBox("图层设置 (未选择图层)")
        self.layer_settings_group.setEnabled(False)
        layer_settings_layout = QVBoxLayout(self.layer_settings_group)
        
        self.smoothing_group = QGroupBox("曲线平滑 (移动平均)")
        self.smoothing_group.setCheckable(True); self.smoothing_group.setChecked(True)
        self.smoothing_group.setToolTip("勾选后，对当前图层的F0曲线进行移动平均平滑。")
        smoothing_layout = QFormLayout(self.smoothing_group)
        self.smoothing_window_slider = QSlider(Qt.Horizontal)
        self.smoothing_window_slider.setRange(1, 25); self.smoothing_window_slider.setValue(4)
        self.smoothing_window_slider.setToolTip("移动平均的窗口大小（半长），最终窗口大小为 2*值+1。")
        self.smoothing_label = QLabel("窗口: 9 点")
        smoothing_layout.addRow(self.smoothing_label, self.smoothing_window_slider)
        
        self.display_group = QGroupBox("显示选项")
        display_layout = QFormLayout(self.display_group)
        self.show_points_check = QCheckBox("显示数据点"); self.show_points_check.setToolTip("勾选后，在当前图层的F0曲线上方显示原始的F0数据点。")
        self.point_size_slider = QSlider(Qt.Horizontal); self.point_size_slider.setRange(2, 50); self.point_size_slider.setValue(10); self.point_size_slider.setToolTip("调整当前图层数据点的大小。")
        self.point_alpha_slider = QSlider(Qt.Horizontal); self.point_alpha_slider.setRange(10, 100); self.point_alpha_slider.setValue(40); self.point_alpha_slider.setToolTip("调整当前图层数据点的不透明度。")
        display_layout.addRow(self.show_points_check); display_layout.addRow("点大小:", self.point_size_slider); display_layout.addRow("点透明度:", self.point_alpha_slider)
        
        layer_settings_layout.addWidget(self.smoothing_group); layer_settings_layout.addWidget(self.display_group)

        # --- [核心修改 3] 恢复全局分组样式面板 ---
        self.grouping_group = QGroupBox("全局分组样式 (颜色区分)")
        self.grouping_group.setToolTip("为所有图层中具有相同标签的分组设置统一的颜色和显示状态。")
        grouping_layout = QVBoxLayout(self.grouping_group)
        
        color_scheme_layout = QHBoxLayout(); self.color_scheme_combo = QComboBox(); self.color_scheme_combo.addItems(self.COLOR_SCHEMES.keys()); self.apply_color_scheme_btn = QPushButton("应用"); color_scheme_layout.addWidget(self.color_scheme_combo); color_scheme_layout.addWidget(self.apply_color_scheme_btn)
        
        self.group_settings_scroll = QScrollArea()
        self.group_settings_scroll.setWidgetResizable(True)
        self.group_settings_scroll.setFrameShape(QScrollArea.NoFrame)
        self.group_settings_scroll.setMinimumHeight(150)
        
        self.group_settings_widget = QWidget()
        self.group_settings_layout = QVBoxLayout(self.group_settings_widget)
        self.group_settings_scroll.setWidget(self.group_settings_widget)
        
        self.show_mean_contour_check = QCheckBox("显示分组平均轮廓")
        self.show_mean_contour_check.setToolTip("勾选后，将为每个分组计算并绘制一条平均语调轮廓线。\n(必须勾选时间归一化)")
        
        self.show_average_only_check = QCheckBox("仅显示平均值")
        self.show_average_only_check.setToolTip("勾选后，将隐藏所有原始的语调曲线，只显示平均轮廓线。")
        self.show_average_only_check.setEnabled(False)

        mean_contour_layout = QHBoxLayout()
        mean_contour_layout.addWidget(self.show_mean_contour_check)
        mean_contour_layout.addWidget(self.show_average_only_check)
        mean_contour_layout.addStretch()
        
        grouping_layout.addLayout(color_scheme_layout)
        grouping_layout.addWidget(self.group_settings_scroll)
        grouping_layout.addLayout(mean_contour_layout)
        # --- 结束修改 ---

        layout.addWidget(global_group); layout.addWidget(self.layer_settings_group); layout.addWidget(self.grouping_group); layout.addStretch()
        return scroll

    def _connect_signals(self):
        """连接所有UI控件的信号到槽函数。"""
        # --- 左侧面板 - 图层管理 ---
        self.add_layer_btn.clicked.connect(self._add_layer)
        self.layer_table.customContextMenuRequested.connect(self._show_layer_context_menu)
        self.layer_table.itemDoubleClicked.connect(self._on_layer_double_clicked)
        # [核心修改] 移除了下面这一行，以禁用双击重命名
        # self.layer_table.itemChanged.connect(self._on_layer_renamed)
        self.layer_table.itemSelectionChanged.connect(self._on_layer_selection_changed)
        
        # [核心修改] 将 plot_button 连接到新的处理器
        self.plot_button.clicked.connect(self._update_plot)
        self.group_table.customContextMenuRequested.connect(self._show_group_context_menu_global)
        # [核心修改] 恢复交互模式切换的信号连接
        self.auto_emphasize_check.toggled.connect(self._on_auto_emphasize_toggled)
        # 初始化一次，以设置初始的选择模式和信号连接
        self._on_auto_emphasize_toggled(self.auto_emphasize_check.isChecked())

        self.show_all_data_check.stateChanged.connect(self._on_layer_selection_changed)

        # 右侧 - 全局设置
        self.title_edit.textChanged.connect(self._update_plot); self.xlabel_edit.textChanged.connect(self._update_plot); self.ylabel_edit.textChanged.connect(self._update_plot)
        self.show_legend_check.stateChanged.connect(self._update_plot); self.interpolate_gaps_check.stateChanged.connect(self._update_plot)
        # 这些全局设置变化，会联动更新 Plot，并可能影响平均轮廓的启用状态
        self.normalize_time_check.stateChanged.connect(self._on_global_setting_changed) 
        self.norm_combo.currentTextChanged.connect(self._on_global_setting_changed)
        self.st_ref_edit.textChanged.connect(self._on_global_setting_changed); self.z_scope_combo.currentTextChanged.connect(self._on_global_setting_changed)
        
        # 右侧 - 图层设置 (所有变化都触发 _on_current_layer_setting_changed)
        self.smoothing_group.toggled.connect(self._on_current_layer_setting_changed)
        self.smoothing_window_slider.valueChanged.connect(self._on_current_layer_setting_changed); self.smoothing_window_slider.valueChanged.connect(self._update_smoothing_label) # 实时更新标签
        self.show_points_check.stateChanged.connect(self._on_current_layer_setting_changed); self.point_size_slider.valueChanged.connect(self._on_current_layer_setting_changed); self.point_alpha_slider.valueChanged.connect(self._on_current_layer_setting_changed)
        
        # 右侧 - 全局分组设置
        self.apply_color_scheme_btn.clicked.connect(self._apply_color_scheme_globally)
        # --- [核心修改] ---
        # 将 show_mean_contour_check 的 stateChanged 信号连接到新的逻辑处理函数
        self.show_mean_contour_check.stateChanged.connect(self._on_mean_contour_toggle)
        # show_average_only_check 的状态变化只需要触发重绘即可
        self.show_average_only_check.stateChanged.connect(self._update_plot)
        
        # 画布交互 (鼠标平移、滚轮缩放、右键菜单)
        self.canvas.setMouseTracking(True) # 启用鼠标跟踪，用于悬浮提示
        self.canvas.customContextMenuRequested.connect(self._show_context_menu)
        self.canvas.mpl_connect('button_press_event', self._on_mouse_press)
        self.canvas.mpl_connect('button_release_event', self._on_mouse_release)
        self.canvas.mpl_connect('motion_notify_event', self._on_mouse_move)
        # 这里没有实现滚轮缩放的直接信号连接，而是通过重写 wheelEvent 处理

    def _on_auto_emphasize_toggled(self, checked):
        """
        [新增] 当“选择即强调”模式切换时调用。
        """
        if checked:
            # 启用“选择即强调”模式 -> 单选
            self.group_table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.group_table.setToolTip("单击选择以动态强调曲线。")
            try:
                self.group_table.itemSelectionChanged.disconnect(self._on_group_selection_changed)
            except TypeError: pass
            self.group_table.itemSelectionChanged.connect(self._on_group_selection_changed)
            self._on_group_selection_changed()

        else:
            # 禁用“选择即强调”模式 -> 多选
            self.group_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.group_table.setToolTip("使用Ctrl/Shift进行多选，然后右键单击进行批量操作。")
            try:
                self.group_table.itemSelectionChanged.disconnect(self._on_group_selection_changed)
            except TypeError: pass

    def _on_group_selection_changed(self):
        """
        [v3.3 - 图层隔离强调版]
        在“选择即强调”模式下，当分组表格中的选择项发生变化时调用。
        此版本只修改当前选中图层的强调状态，实现了图层隔离。
        """
        layer_row = self.current_selected_layer_index
        if layer_row < 0 or layer_row >= len(self.layers): return
        layer_config = self.layers[layer_row]
        
        all_groups_in_layer = layer_config.get('groups', {})
        if not all_groups_in_layer: return

        selected_items = self.group_table.selectedItems()
        selected_group_name = selected_items[0].text() if selected_items else None

        # 遍历当前图层内的所有分组，并更新它们的 'emphasized' 状态
        for group_name, settings in all_groups_in_layer.items():
            settings['emphasized'] = (group_name == selected_group_name)
        
        # 刷新UI以反映变化
        self._update_group_table() # 更新左侧列表的强调图标
        self._update_plot()      # 重绘图表以应用新的强调样式

    def wheelEvent(self, event):
        """
        [v2.2 - 坐标系修正最终版] 处理鼠标滚轮事件，实现以鼠标为中心的单轴缩放。
        """
        modifiers = event.modifiers()
        is_ctrl_pressed = modifiers & Qt.ControlModifier
        is_shift_pressed = modifiers & Qt.ShiftModifier

        if self.canvas.underMouse() and (is_ctrl_pressed or is_shift_pressed):
            try:
                ax = self.figure.gca()
                
                # --- [核心修正] 坐标系转换 ---
                global_pos = event.globalPos()
                canvas_local_pos = self.canvas.mapFromGlobal(global_pos)
                x_pixel = canvas_local_pos.x()
                y_pixel = self.canvas.height() - canvas_local_pos.y()
                # --- 修正结束 ---

                trans = ax.transData.inverted()
                mouse_x, mouse_y = trans.transform_point((x_pixel, y_pixel))
                
                zoom_factor = 1.1 if event.angleDelta().y() > 0 else 1 / 1.1

                if is_ctrl_pressed:
                    cur_xlim = ax.get_xlim()
                    left_dist = mouse_x - cur_xlim[0]
                    right_dist = cur_xlim[1] - mouse_x
                    new_xlim = [
                        mouse_x - left_dist / zoom_factor,
                        mouse_x + right_dist / zoom_factor
                    ]
                    ax.set_xlim(new_xlim)
                
                elif is_shift_pressed:
                    cur_ylim = ax.get_ylim()
                    bottom_dist = mouse_y - cur_ylim[0]
                    top_dist = cur_ylim[1] - mouse_y
                    new_ylim = [
                        mouse_y - bottom_dist / zoom_factor,
                        mouse_y + top_dist / zoom_factor
                    ]
                    ax.set_ylim(new_ylim)

                self.canvas.draw()
            except Exception as e:
                if 'nan' not in str(e).lower():
                    print(f"Single-axis zoom failed in visualizer: {e}")
        else:
            super().wheelEvent(event)

    # ==========================================================================
    # 图层管理相关方法 (左侧面板)
    # ==========================================================================

    def _on_mean_contour_toggle(self, state):
        """
        [NEW] 当“显示分组平均轮廓”复选框状态改变时调用。
        """
        is_checked = (state == Qt.Checked)
        
        # 1. 控制“仅显示平均值”复选框的启用状态
        self.show_average_only_check.setEnabled(is_checked)
        
        # 2. 如果主复选框被取消，则强制取消“仅显示平均值”
        if not is_checked:
            self.show_average_only_check.setChecked(False)
            
        # 3. 触发重绘
        self._update_plot()
    def _add_layer(self):
        """
        [v2.1 - 自动匹配版]
        打开 LayerConfigDialog 添加新图层，并在成功后立即尝试自动匹配TextGrid。
        """
        dialog = LayerConfigDialog(parent=self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_layer_config()
            if config:
                # [核心新增] 调用自动匹配
                self._auto_match_textgrid_for_layer(config)

                self.layers.append(config)
                self._update_layer_table()
                self._update_ui_state()
                self._update_all_group_settings()
                self._update_plot()

    def _remove_layer(self, row_to_remove=None):
        """移除指定行或当前选中行的图层。"""
        current_row = row_to_remove if row_to_remove is not None else self.layer_table.currentRow()
        if current_row < 0: return # 没有选中行或无效行

        reply = QMessageBox.question(
            self,
            "确认移除",
            f"您确定要移除图层 '{self.layers[current_row]['name']}' 吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.No: return

        self.layers.pop(current_row) # 从列表中移除图层
        self.current_selected_layer_index = -1 # 重置选中状态
        self._update_layer_table() # 更新列表UI
        self._on_layer_selection_changed() # 模拟选择变化，清空右侧面板
        self._update_all_group_settings() # [NEW] 移除图层后更新全局分组
        self._update_plot() # 移除图层后重绘

    def _config_layer(self, row_to_config=None):
        """
        [v11.1 - 延迟加载修复版]
        配置图层。在对话框返回后，如果音频路径发生变化，则在此处加载音频。
        """
        current_row = row_to_config if row_to_config is not None else self.layer_table.currentRow()
        if current_row < 0: return
        
        config_to_edit = self.layers[current_row]

        dialog = LayerConfigDialog(existing_config=config_to_edit, parent=self)
        if dialog.exec() == QDialog.Accepted:
            new_config = dialog.get_layer_config()
            if new_config:
                # --- 实现延迟加载 ---
                old_path = config_to_edit.get('audio_path')
                new_path = new_config.get('audio_path')

                if new_path and (new_path != old_path or 'audio_data' not in new_config or new_config['audio_data'] is None):
                    self.parent().statusBar().showMessage(f"正在后台加载音频: {os.path.basename(new_path)}...", 3000)
                    try:
                        import librosa
                        y, sr = librosa.load(new_path, sr=None, mono=True)
                        new_config['audio_data'] = (y, sr)
                        self.parent().statusBar().showMessage(f"音频 '{os.path.basename(new_path)}' 加载成功。", 3000)
                    except Exception as e:
                        QMessageBox.critical(self, "音频加载失败", f"无法加载文件 {new_path}:\n{e}")
                        new_config['audio_data'] = None
                        new_config['audio_path'] = None
                
                self.layers[current_row] = new_config
                self._update_layer_table_row(current_row)
                self._on_layer_selection_changed()
                self._update_all_group_settings()
                self._update_plot()

    def _update_layer_table(self):
        """刷新整个图层表格 UI。"""
        self.layer_table.blockSignals(True) # 阻止信号，避免在UI更新时触发槽函数
        self.layer_table.setRowCount(0) # 清空所有行
        for i in range(len(self.layers)):
            self._update_layer_table_row(i) # 逐行更新
        self.layer_table.blockSignals(False)
        self._update_ui_state() # 更新整体UI状态
        
        # 尝试选中最后添加的图层，如果没有则不选中
        if self.layer_table.rowCount() > 0:
            last_row_index = self.layer_table.rowCount() - 1
            self.layer_table.selectRow(last_row_index)
            self.current_selected_layer_index = last_row_index # 更新当前选中索引

    def _update_layer_table_row(self, row):
        """
        [v2.0 - UI对齐重构版]
        更新图层表格中指定行的内容。
        - 现在有两列：图层名称和分组依据。
        - 名称列被设置为不可在表格内直接编辑。
        """
        if row >= len(self.layers): return
        layer = self.layers[row]
        
        if row >= self.layer_table.rowCount():
            self.layer_table.insertRow(row)
        
        # Column 0: 图层名称 (不可编辑，包含显示/隐藏图标)
        name_item = QTableWidgetItem(layer['name'])
        # [核心修改] 明确设置为不可编辑，统一交互
        name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable) 
        
        is_enabled = layer.get('enabled', True)
        if self.icon_manager:
            icon_name = "success" if is_enabled else "hidden"
            name_item.setIcon(self.icon_manager.get_icon(icon_name))
        
        tooltip_parts = [f"<b>图层: {layer['name']}</b><hr>"]
        df = layer.get('df')
        tooltip_parts.append(f"<b>数据源:</b> {layer.get('data_filename', 'N/A')} ({len(df)}点)" if df is not None else "<b>数据源:</b> 无")
        tooltip_parts.append(f"<b>TextGrid:</b> {layer.get('tg_filename', 'N/A')}" if layer.get('tg') else "<b>TextGrid:</b> 无")
        tooltip_parts.append(f"<b>时间列:</b> {layer.get('time_col', 'N/A')}")
        tooltip_parts.append(f"<b>F0列:</b> {layer.get('f0_col', 'N/A')}")
        tooltip_parts.append(f"<b>分组依据:</b> {layer.get('group_col', '无分组')}")
        name_item.setToolTip("\n".join(tooltip_parts))
        self.layer_table.setItem(row, 0, name_item)
        
        # Column 1: 分组依据 (不可编辑)
        group_item = QTableWidgetItem(layer.get('group_col', '无分组'))
        group_item.setFlags(group_item.flags() & ~Qt.ItemIsEditable)
        self.layer_table.setItem(row, 1, group_item)

    def _show_layer_context_menu(self, pos):
        """
        [v2.0 - 合并/拆分版]
        显示图层列表的右键上下文菜单。
        此版本支持多选，并能根据选中的图层数量和内容，
        动态地显示“合并”或“拆分”等高级操作。
        """
        # 1. 获取所有选中的行（去重并排序）
        selected_rows = sorted(list(set(item.row() for item in self.layer_table.selectedItems())))
        
        # 如果没有选中任何行（例如在空白处右键），则不显示菜单
        if not selected_rows: 
            return

        menu = QMenu(self)
        num_selected = len(selected_rows)

        # --- 2. [核心逻辑] 根据选中数量，构建不同的菜单 ---

        # 2.1. 如果选中了多个图层 (num_selected > 1)
        if num_selected > 1:
            # 添加“合并图层”功能
            merge_action = menu.addAction(self.icon_manager.get_icon("concatenate"), f"合并选中的 {num_selected} 个图层...")
            merge_action.triggered.connect(lambda: self._merge_selected_layers(selected_rows))
        
        # 2.2. 如果只选中了一个图层 (num_selected == 1)
        elif num_selected == 1:
            row = selected_rows[0]
            layer = self.layers[row]
            
            # --- [新增] 检查并添加“拆分图层”功能 ---
            df = layer.get('df')
            # 只有当图层的 DataFrame 存在且包含 'source_file' 列时，才认为它是可拆分的
            if df is not None and 'source_file' in df.columns:
                split_action = menu.addAction(self.icon_manager.get_icon("split"), "拆分此图层 (按来源)")
                split_action.triggered.connect(lambda: self._split_single_layer(row))
                menu.addSeparator()

            # --- 添加所有常规的单图层操作 ---
            is_enabled = layer.get('enabled', True)
            action_text = "隐藏图层" if is_enabled else "显示图层"
            icon_name = "hidden" if is_enabled else "show"
            toggle_action = menu.addAction(self.icon_manager.get_icon(icon_name), action_text)
            
            menu.addSeparator()
            
            rename_action = menu.addAction(self.icon_manager.get_icon("rename"), "重命名...")
            config_action = menu.addAction(self.icon_manager.get_icon("settings"), "配置...")
            remove_action = menu.addAction(self.icon_manager.get_icon("delete"), "移除图层")
            
            menu.addSeparator()
            
            save_action = menu.addAction(self.icon_manager.get_icon("save"), "保存单层图片...")
            save_action.setEnabled(is_enabled) # 只有显示的图层才能保存
        
        # 3. 执行菜单并处理用户选择 (只在单选模式下需要)
        action = menu.exec_(self.layer_table.mapToGlobal(pos))
        
        # 如果是单选模式，则根据返回的 action 执行相应操作
        if num_selected == 1:
            if action == toggle_action: self._toggle_layer_visibility(row)
            elif action == rename_action: self.layer_table.editItem(self.layer_table.item(row, 0))
            elif action == config_action: self._config_layer(row)
            elif action == remove_action: self._remove_layer(row)
            elif action == save_action: self._save_single_layer_image(row)
            # 注意：拆分和合并的 triggered 信号已直接连接，无需在此处处理

    def _merge_selected_layers(self, rows_to_merge):
        """
        [新增] 核心功能：合并所有选中的图层，并智能处理 TextGrid 标签。
        (逻辑移植自 plotter.py)
        """
        # 1. 获取新图层名称
        dialog = MergeLayersDialog(len(rows_to_merge), self)
        if dialog.exec_() != QDialog.Accepted:
            return
        new_name = dialog.get_new_name()
        if not new_name:
            QMessageBox.warning(self, "名称无效", "新图层的名称不能为空。")
            return
        if any(layer['name'] == new_name for layer in self.layers):
            QMessageBox.warning(self, "名称冲突", f"图层名称 '{new_name}' 已存在。")
            return

        # 2. 初始化合并过程
        dfs_to_concat = []
        base_config = deepcopy(self.layers[rows_to_merge[0]])
    
        # 3. 遍历所有选中的图层并智能合并数据
        for row in rows_to_merge:
            layer = self.layers[row]
            df = layer.get('df')
            if df is None or df.empty:
                continue

            df_copy = df.copy()
            source_name = layer['name']
            df_copy['source_file'] = source_name
        
            original_group_col = layer.get('group_col')
            if original_group_col and original_group_col != "无分组" and original_group_col in df_copy.columns:
                df_copy.dropna(subset=[original_group_col], inplace=True)
                if df_copy.empty:
                    continue
                original_labels = df_copy[original_group_col].astype(str)
                df_copy['source_label'] = source_name + " - " + original_labels
            else:
                df_copy['source_label'] = source_name

            dfs_to_concat.append(df_copy)

        if not dfs_to_concat:
            QMessageBox.warning(self, "合并失败", "选中的图层中没有可合并的数据。")
            return
        
        # 4. 创建新的合并后的图层
        merged_df = pd.concat(dfs_to_concat, ignore_index=True)
        new_layer_config = base_config
        new_layer_config['name'] = new_name
        new_layer_config['df'] = merged_df
        new_layer_config['data_filename'] = f"合并自 {len(rows_to_merge)} 个图层"
        new_layer_config['group_col'] = 'source_label' # 关键：分组依据设为复合标签
        new_layer_config['tg'] = None
        new_layer_config['tg_filename'] = "N/A"
    
        self.layers.append(new_layer_config)
    
        # 5. 可选：删除原始图层
        question_text = (f"已成功创建合并图层 '{new_name}'。\n\n"
                         "新图层将按'来源-标签'进行分组。\n"
                         f"是否要删除原来的 {len(rows_to_merge)} 个子图层？")
        reply = QMessageBox.question(self, "操作完成", question_text, QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply == QMessageBox.Yes:
            for row in sorted(rows_to_merge, reverse=True):
                del self.layers[row]

        # 6. 刷新UI
        self._update_layer_table()
        for i, layer in enumerate(self.layers):
            if layer['name'] == new_name:
                self.layer_table.selectRow(i); break
        self._update_all_group_settings()
        self._update_plot()

    def _split_single_layer(self, row_to_split):
        """
        [新增] 核心功能：将单个合并后的图层，按 'source_file' 列拆分成多个新图层。
        (逻辑移植自 plotter.py)
        """
        if not (0 <= row_to_split < len(self.layers)):
            return

        layer_to_split = self.layers[row_to_split]
        merged_df = layer_to_split.get('df')
    
        if merged_df is None or 'source_file' not in merged_df.columns:
            QMessageBox.warning(self, "无法拆分", "此图层不包含 'source_file' 列，无法进行拆分。")
            return
        
        unique_sources = merged_df['source_file'].unique()
        if len(unique_sources) <= 1:
            QMessageBox.information(self, "无需拆分", "此图层只包含一个来源，无需拆分。")
            return

        # 1. 准备拆分
        new_layers_to_add = []
        base_config = deepcopy(layer_to_split)

        for source_name in unique_sources:
            single_df = merged_df[merged_df['source_file'] == source_name].copy()
            # 移除 'source_file' 和 'source_label' 列
            single_df.drop(columns=['source_file', 'source_label'], inplace=True, errors='ignore')
        
            final_name = str(source_name)
            counter = 1
            existing_names = {l['name'] for l in self.layers}
            while final_name in existing_names:
                final_name = f"{source_name} ({counter})"
                counter += 1

            new_config = deepcopy(base_config)
            new_config['name'] = final_name
            new_config['df'] = single_df
            new_config['data_filename'] = f"拆分自 ({layer_to_split['name']})"
            # 恢复分组依据为自动猜测或无分组
            new_config['group_col'] = next((c for c in single_df.columns if 'vowel' in c.lower() or 'label' in c.lower() or 'group' in c.lower()), "无分组")

            new_layers_to_add.append(new_config)

        # 2. 询问用户是否删除原图层
        reply = QMessageBox.question(self, "确认拆分",
                                     f"将把图层 '{layer_to_split['name']}' 拆分为 {len(new_layers_to_add)} 个新图层。\n\n"
                                     f"是否在拆分后删除原始的合并图层？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)

        # 3. 执行操作
        if reply == QMessageBox.Yes:
            del self.layers[row_to_split]
    
        self.layers.extend(new_layers_to_add)

        # 4. 刷新UI
        self._update_layer_table()
        for i, layer in enumerate(self.layers):
            if layer['name'] == new_layers_to_add[0]['name']:
                self.layer_table.selectRow(i); break
        self._update_all_group_settings()
        self._update_plot()

    def _on_layer_double_clicked(self, item):
        """双击图层项时，打开配置对话框。"""
        if item.column() == 0: # 确保点击的是名称列
            self._config_layer(item.row())

    def _on_layer_renamed(self, item):
        """处理图层名称单元格文本改变（重命名完成）的信号。"""
        if item.column() == 0 and not self.layer_table.signalsBlocked(): # 确保是名称列且不是信号阻塞导致
            row = item.row(); old_name = self.layers[row]['name']; new_name = item.text().strip()
            
            if not new_name: # 如果新名称为空
                QMessageBox.warning(self, "名称无效", "图层名称不能为空。")
                item.setText(old_name); return
            if new_name == old_name: return # 如果名称没有改变
            
            # 检查名称是否重复
            if any(l['name'] == new_name for i, l in enumerate(self.layers) if i != row):
                QMessageBox.warning(self.parent(), "名称重复", f"图层名称 '{new_name}' 已存在，请使用其他名称。")
                item.setText(old_name); return
                
            self.layers[row]['name'] = new_name
            self._update_plot() # 重命名可能影响图例，所以重绘

    def _on_layer_selection_changed(self):
        """
        [v3.3 - 标题截断版]
        处理图层列表选中行变化。
        - 调用新的 _update_layer_settings_panel_title 来处理长标题。
        """
        self.current_selected_layer_index = self.layer_table.currentRow()
        row = self.current_selected_layer_index

        if row > -1 and row < len(self.layers):
            layer_config = self.layers[row]
            df_to_show = layer_config.get('df', pd.DataFrame())

            if not self.show_all_data_check.isChecked():
                if df_to_show is not None and not df_to_show.empty and 'textgrid_label' in df_to_show.columns:
                    df_to_show = df_to_show.dropna(subset=['textgrid_label'])

            self.table_view.setModel(PandasModel(df_to_show))
            self._populate_layer_settings_panel(layer_config)
            # --- [核心修改 3] 调用新的标题更新方法 ---
            self._update_layer_settings_panel_title()
            self.layer_settings_group.setEnabled(True)
        else:
            self.table_view.setModel(None)
            self.layer_settings_group.setTitle("图层设置 (未选择图层)")
            self.layer_settings_group.setEnabled(False)
        
        self._update_group_table()
        self._update_ui_state()

    def _update_layer_settings_panel_title(self):
        """
        [v3.4 - 暴力截断版]
        更新右侧图层设置面板的标题。
        如果标题超过15个字符，则暴力截断为 "首6...尾6" 的格式。
        """
        row = self.current_selected_layer_index
        if row > -1 and row < len(self.layers):
            layer_config = self.layers[row]
            base_title = layer_config['name']
            is_locked = layer_config.get('locked', False)
            
            # --- [核心修改] 暴力截断逻辑 ---
            if len(base_title) > 15:
                truncated_title = f"{base_title[:6]}...{base_title[-6:]}"
            else:
                truncated_title = base_title
            # --- 结束修改 ---
            
            final_title = f"图层设置 ({truncated_title})"
            if is_locked:
                final_title += " (已锁定)"
            self.layer_settings_group.setTitle(final_title)
        else:
            self.layer_settings_group.setTitle("图层设置 (未选择图层)")

    def _update_group_table(self):
        """
        [v3.3 - 图层隔离强调版]
        根据当前选中的图层，刷新左侧的分组列表。
        - “强调”状态现在从图层自身的配置中读取。
        """
        self.group_table.blockSignals(True)
        self.group_table.setRowCount(0)

        row = self.current_selected_layer_index
        if row < 0 or row >= len(self.layers):
            self.group_table.setEnabled(False)
            self.group_table.blockSignals(False)
            return

        layer_config = self.layers[row]
        group_col = layer_config.get('group_col')
        df = layer_config.get('df')

        has_groups = df is not None and group_col and group_col != "无分组" and group_col in df.columns
        self.group_table.setEnabled(has_groups)
        
        if not has_groups:
            self.group_table.blockSignals(False)
            return

        groups_in_layer = sorted(df[group_col].dropna().astype(str).unique(), key=str)
        
        if 'groups' not in layer_config:
            layer_config['groups'] = {}

        for i, group_name in enumerate(groups_in_layer):
            if group_name not in layer_config['groups']:
                layer_config['groups'][group_name] = {'enabled': True, 'emphasized': False}
            
            settings = layer_config['groups'][group_name]
            self.group_table.insertRow(i)
            
            # --- Column 0: 分组名称 (带强调图标) ---
            name_item = QTableWidgetItem(group_name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            
            # --- [核心修改] ---
            # 直接从图层内部的 settings 获取 emphasized 状态
            is_emphasized = settings.get('emphasized', False)
            if is_emphasized and self.icon_manager:
                name_item.setIcon(self.icon_manager.get_icon("favorite"))
            else:
                name_item.setIcon(QIcon())
            # --- 结束修改 ---
            self.group_table.setItem(i, 0, name_item)

            # --- Column 1: 显示复选框 ---
            cb = QCheckBox()
            cb.setChecked(settings.get('enabled', True))
            cell_widget_cb = QWidget()
            layout_cb = QHBoxLayout(cell_widget_cb); layout_cb.addWidget(cb); layout_cb.setAlignment(Qt.AlignCenter); layout_cb.setContentsMargins(0,0,0,0)
            self.group_table.setCellWidget(i, 1, cell_widget_cb)
            cb.stateChanged.connect(lambda state, n=group_name: self._on_group_toggled(n, 'enabled', state == Qt.Checked))

        self.group_table.blockSignals(False)

    def _on_group_toggled(self, group_name, prop, state):
        """
        [新增] 当左侧分组列表中的“显示”复选框被点击时调用。
        只更新当前选中图层的数据模型。
        """
        row = self.current_selected_layer_index
        if row < 0 or row >= len(self.layers):
            return

        if prop != 'enabled':
            return

        layer_config = self.layers[row]
        if 'groups' in layer_config and group_name in layer_config['groups']:
            layer_config['groups'][group_name][prop] = state
            self._update_plot()

    def _toggle_layer_visibility(self, row):
        """切换图层的可见性，并高效地只更新受影响的UI元素。"""
        if row < len(self.layers):
            self.layers[row]['enabled'] = not self.layers[row].get('enabled', True)
            self._update_all_group_settings() # [NEW] 更新全局分组列表（因为可见性影响分组的出现）
            self._update_plot() # 重新绘图
            self._update_layer_table_row(row) # 更新表格中的图标

    def _save_single_layer_image(self, row):
        """
        保存单个图层的渲染图。
        临时隐藏其他图层，绘制，保存，然后恢复。
        """
        if row >= len(self.layers): return
        layer_to_save = self.layers[row]
        
        title = layer_to_save['name']; safe_filename = re.sub(r'[\\/*?:"<>|]', "_", title)
        
        file_path, _ = QFileDialog.getSaveFileName(self, f"保存图层 '{title}'", f"{safe_filename}.png", "PNG图片 (*.png);;PDF (*.pdf);;JPEG图片 (*.jpg);;SVG矢量图 (*.svg)")
        if not file_path: return # 用户取消

        # 记录所有图层的原始启用状态
        original_states = {i: l.get('enabled', True) for i, l in enumerate(self.layers)}
        
        # 临时将所有图层设置为禁用，只启用当前要保存的图层
        for i, layer in enumerate(self.layers): layer['enabled'] = (i == row)
        
        self._update_plot() # 用单图层数据重绘图表
        
        try:
            self.figure.savefig(file_path, dpi=300, bbox_inches='tight', facecolor='white')
            QMessageBox.information(self, "成功", f"图层 '{title}' 已成功保存为图片:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"无法保存图片: {e}")
        finally:
            # 恢复所有图层的原始显示状态
            for i, state in original_states.items():
                if i < len(self.layers): self.layers[i]['enabled'] = state
            self._update_plot() # 恢复完整图表

    # ==========================================================================
    # 右侧面板 (图层设置 & 全局分组样式) 相关方法
    # ==========================================================================
    def _populate_layer_settings_panel(self, layer_config):
        """
        用选中图层的配置填充右侧的图层设置面板（平滑、显示选项）。
        这是一个上下文敏感的UI更新。
        """
        # 阻止信号，避免在设置值时立即触发 _on_current_layer_setting_changed
        self.smoothing_group.blockSignals(True)
        self.smoothing_window_slider.blockSignals(True)
        self.show_points_check.blockSignals(True)
        self.point_size_slider.blockSignals(True)
        self.point_alpha_slider.blockSignals(True)

        # 填充平滑设置
        self.smoothing_group.setChecked(layer_config.get('smoothing_enabled', True))
        self.smoothing_window_slider.setValue(layer_config.get('smoothing_window', 4))
        self._update_smoothing_label(self.smoothing_window_slider.value())

        # 填充数据点显示设置
        self.show_points_check.setChecked(layer_config.get('show_points', False))
        self.point_size_slider.setValue(layer_config.get('point_size', 10))
        self.point_alpha_slider.setValue(int(layer_config.get('point_alpha', 0.4) * 100)) # 滑块值转换回0-1

        # 解除信号阻止
        self.smoothing_group.blockSignals(False)
        self.smoothing_window_slider.blockSignals(False)
        self.show_points_check.blockSignals(False)
        self.point_size_slider.blockSignals(False)
        self.point_alpha_slider.blockSignals(False)

    def _update_all_group_settings(self):
        """
        [v3.2 - 选色修复版]
        扫描所有启用图层，收集所有唯一的分组名称，并更新全局分组
        数据模型 (self.global_groups) 和右侧的UI面板。
        """
        all_groups = set()
        for layer in self.layers:
            if not layer.get('enabled', True): continue
            group_col = layer.get('group_col')
            df = layer.get('df')
            if df is not None and group_col and group_col != "无分组" and group_col in df.columns:
                all_groups.update(df[group_col].dropna().astype(str).unique())
        
        old_groups_copy = self.global_groups.copy()
        self.global_groups.clear()
        for group_name_str in sorted(list(all_groups), key=str):
            if group_name_str in old_groups_copy:
                self.global_groups[group_name_str] = old_groups_copy[group_name_str]
            else:
                self.global_groups[group_name_str] = {'enabled': True, 'color': QColor(next(self.color_cycler))}
        
        while self.group_settings_layout.count():
            child = self.group_settings_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()

        if not self.global_groups:
            self.grouping_group.setVisible(False)
            return

        self.grouping_group.setVisible(True)

        for group_name_str, settings in self.global_groups.items():
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 5, 0, 5)

            cb = QCheckBox(group_name_str)
            cb.setChecked(settings.get('enabled', True))
            cb.setToolTip(f"在所有图层中全局显示/隐藏 '{group_name_str}' 分组。")
            cb.stateChanged.connect(lambda state, n=group_name_str: self._on_global_group_prop_changed(n, 'enabled', state == Qt.Checked))

            color_widget = ColorWidget(settings.get('color', QColor(Qt.black)))
            color_widget.setToolTip(f"为 '{group_name_str}' 分组设置全局颜色。")
            
            # --- [核心修复 1] ---
            # lambda函数必须捕获颜色参数 'c'
            color_widget.colorChanged.connect(lambda c, n=group_name_str: self._on_global_group_prop_changed(n, 'color', c))
            # --- 结束修复 ---
            
            row_layout.addWidget(cb, 1)
            row_layout.addWidget(color_widget)
            self.group_settings_layout.addWidget(row_widget)

    def _show_group_context_menu_global(self, pos):
        """
        [v2.1 - 批量强调版]
        显示全局分组列表的右键菜单，增加批量强调/取消强调功能。
        """
        selected_items = self.group_table.selectedItems()
        if not selected_items: return

        selected_rows = sorted(list(set(item.row() for item in selected_items)))
        num_selected = len(selected_rows)
        selected_group_names = [self.group_table.item(row, 0).text() for row in selected_rows]
        layer_row = self.current_selected_layer_index
        if layer_row < 0: return # 必须有一个选中的图层
        layer_config = self.layers[layer_row]

        menu = QMenu(self)
        # [核心新增] 播放片段动作
        # 只有当图层有关联音频时，才添加此动作
        if 'audio_data' in layer_config:
            play_action = menu.addAction(self.icon_manager.get_icon("play"), "播放此片段 (Enter)")
            play_action.triggered.connect(self._play_selected_segment)
            menu.addSeparator()
        
        show_action = menu.addAction(self.icon_manager.get_icon("show"), f"显示选中的 {num_selected} 项")
        hide_action = menu.addAction(self.icon_manager.get_icon("hidden"), f"隐藏选中的 {num_selected} 项")
        menu.addSeparator()

        # [核心新增] 批量强调/取消强调
        emphasize_action = menu.addAction(self.icon_manager.get_icon("favorite"), f"强调选中的 {num_selected} 项")
        unemphasize_action = menu.addAction(self.icon_manager.get_icon("unfavorite"), f"取消强调选中的 {num_selected} 项")
        
        show_action.triggered.connect(lambda: self._apply_to_selected_groups('enabled', True, selected_group_names))
        hide_action.triggered.connect(lambda: self._apply_to_selected_groups('enabled', False, selected_group_names))
        emphasize_action.triggered.connect(lambda: self._apply_to_selected_groups('emphasized', True, selected_group_names))
        unemphasize_action.triggered.connect(lambda: self._apply_to_selected_groups('emphasized', False, selected_group_names))
        
        menu.exec_(self.group_table.mapToGlobal(pos))

    def eventFilter(self, source, event):
        """
        [新增] 事件过滤器，用于捕获分组列表上的键盘事件。
        """
        if source is self.group_table and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._play_selected_segment()
                return True # 事件已处理
        
        return super().eventFilter(source, event)

    def _play_selected_segment(self):
        """
        [v11.1 - 变量修复版]
        使用即时创建的、局部的QMediaPlayer实例，不将其存入任何配置。
        此版本包含了所有必要的变量定义。
        """
        # --- 1. 获取选中的图层和分组 ---
        layer_row = self.current_selected_layer_index
        if layer_row < 0 or layer_row >= len(self.layers):
            return

        group_row = self.group_table.currentRow()
        if group_row < 0:
            QMessageBox.warning(self, "未选择", "请在分组列表中选择一个TextGrid区间进行播放。")
            return

        group_name = self.group_table.item(group_row, 0).text()
        layer_config = self.layers[layer_row]

        # --- 2. 检查所需数据 ---
        tg = layer_config.get('tg')
        audio_data = layer_config.get('audio_data')
        
        if not all([tg, audio_data]):
            QMessageBox.warning(self, "数据不完整", "请确保已为当前图层加载了TextGrid和关联的音频文件。")
            return

        # --- 3. 在TextGrid中查找区间 ---
        y, sr = audio_data
        target_interval = None
        for tier in tg:
            if isinstance(tier, textgrid.IntervalTier):
                for interval in tier:
                    if interval.mark == group_name:
                        target_interval = interval
                        break
            if target_interval:
                break
        
        if not target_interval:
            QMessageBox.warning(self, "未找到区间", f"在TextGrid中未找到名为 '{group_name}' 的区间。")
            return

        # --- 4. 切片音频并写入唯一的临时文件 ---
        try:
            import soundfile as sf
            from PyQt5.QtMultimedia import QMediaContent
            from PyQt5.QtCore import QUrl
            import os
            import time

            start_sample = int(target_interval.minTime * sr)
            end_sample = int(target_interval.maxTime * sr)
            
            start_sample = max(0, start_sample)
            end_sample = min(len(y), end_sample)

            if start_sample >= end_sample:
                return # 空片段，不播放

            segment_data = y[start_sample:end_sample]
            
            # [核心修复] 确保 temp_file_path 被正确定义
            timestamp = int(time.time() * 1000)
            safe_group_name = "".join(c for c in group_name if c.isalnum())
            temp_file_path = os.path.join(self.temp_audio_dir, f"segment_{safe_group_name}_{timestamp}.wav")
            
            sf.write(temp_file_path, segment_data, sr)
            
            # --- 5. 使用全新的QMediaPlayer实例播放 ---
            old_player = self.layers[layer_row].get('player')
            if old_player:
                old_player.stop()
            
            player = QMediaPlayer()
            # 将这个新实例存入一个临时的、不会被 deepcopy 的位置
            self.layers[layer_row]['player'] = player
            
            player.setMedia(QMediaContent(QUrl.fromLocalFile(temp_file_path)))
            player.play()

        except Exception as e:
            QMessageBox.critical(self, "播放失败", f"处理或播放音频片段时发生错误:\n{e}")

    def closeEvent(self, event):
        """
        重写关闭事件，以清理所有临时目录。
        """
        import shutil
        try:
            # 清理音频片段临时目录
            if hasattr(self, 'temp_audio_dir') and self.temp_audio_dir and os.path.exists(self.temp_audio_dir):
                shutil.rmtree(self.temp_audio_dir)
            # 清理工程文件临时目录
            if hasattr(self, 'project_temp_dir') and self.project_temp_dir and os.path.exists(self.project_temp_dir):
                shutil.rmtree(self.project_temp_dir)
        except Exception as e:
            print(f"[Intonation Visualizer Warning] Failed to clean up temp directories: {e}")
        
        super().closeEvent(event)
        
    def _apply_to_selected_groups(self, prop, value, group_names):
        """
        [v3.3 - 图层隔离强调版]
        将属性变更应用到所有选定的全局分组。
        - 'emphasized' 属性现在只应用于当前选中的图层。
        """
        if prop == 'emphasized':
            # 强调是图层级操作
            layer_row = self.current_selected_layer_index
            if layer_row < 0 or layer_row >= len(self.layers):
                return
            layer_config = self.layers[layer_row]
            
            for name in group_names:
                if name in layer_config.get('groups', {}):
                    layer_config['groups'][name]['emphasized'] = value
            self._update_group_table() # 更新左侧列表的图标
        else:
            # 颜色和启用是全局操作
            for name in group_names:
                if name in self.global_groups:
                    self.global_groups[name][prop] = value
            self._update_all_group_settings() # 更新右侧全局UI
        
        self._update_plot()

    def _on_global_group_prop_changed(self, group_name, prop, value):
        """处理全局分组属性（启用状态或颜色）的变化。"""
        if group_name in self.global_groups:
            self.global_groups[group_name][prop] = value
            self._update_plot() # 属性变化后重新绘图

    def _apply_color_scheme_globally(self):
        """
        [NEW] 将当前选择的颜色方案应用到所有全局分组。
        """
        scheme_name = self.color_scheme_combo.currentText()
        color_cycle = cycle(self.COLOR_SCHEMES.get(scheme_name, [])) # 获取选定颜色方案的循环器
        
        # 遍历所有全局分组，为其分配新颜色
        for group_name_str in sorted(self.global_groups.keys(), key=str):
            self.global_groups[group_name_str]['color'] = QColor(next(color_cycle))
        
        self._update_all_group_settings() # 重新填充UI以更新颜色按钮
        self._update_plot() # 重新绘图以应用新颜色

    def _on_global_setting_changed(self):
        """
        [MODIFIED] 当全局设置（时间归一化，F0归一化）变化时，更新UI和绘图。
        """
        # 处理 F0 归一化参数 widgets 的可见性
        norm_method = self.norm_combo.currentText()
        is_st = (norm_method == "半音 (Semitone)"); is_z = (norm_method == "Z-Score")
        self.st_param_widget.setVisible(is_st); self.z_param_widget.setVisible(is_z)
        
        # 平均轮廓显示条件的检查
        # 只有在有任何活跃分组且时间归一化开启时，平均轮廓功能才可用
        has_active_grouping = any(s.get('enabled', True) for s in self.global_groups.values())
        
        self.show_mean_contour_check.setEnabled(has_active_grouping and self.normalize_time_check.isChecked())
        # 如果平均轮廓功能不再可用，则强制取消勾选
        if not self.show_mean_contour_check.isEnabled():
            self.show_mean_contour_check.setChecked(False)

        self._update_plot() # 重新绘图

    def _on_current_layer_setting_changed(self):
        """当右侧图层设置面板的任何控件变化时调用，将UI状态保存回数据模型。"""
        row = self.current_selected_layer_index;
        if row < 0: return; # 没有选中图层
        
        layer = self.layers[row]
        
        # 从UI控件获取值并保存到当前选中图层的配置字典中
        layer['smoothing_enabled'] = self.smoothing_group.isChecked()
        layer['smoothing_window'] = self.smoothing_window_slider.value()
        layer['show_points'] = self.show_points_check.isChecked()
        layer['point_size'] = self.point_size_slider.value()
        layer['point_alpha'] = self.point_alpha_slider.value() / 100.0 # 滑块值转换回0-1
        
        self._update_plot() # 重新绘图以应用新设置

    def _update_smoothing_label(self, value):
        """更新平滑窗口滑块旁边的标签文本。"""
        window_size = 2 * value + 1
        self.smoothing_label.setText(f"窗口: {window_size} 点")

    # ==========================================================================
    # 核心绘图逻辑
    # ==========================================================================
    def _update_plot(self):
        """
        [v3.4 - 图层隔离强调 & 离群点处理版] 核心绘图逻辑。
        - “强调”状态从每个图层独立的配置中读取。
        - 在绘图前，会先过滤掉被标记为 `_is_ignored` 的数据点。
        - 同时考虑全局和图层内部的分组可见性。
        """
        try:
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            self.plotted_lines.clear()
            self.hover_annotation = None
            has_any_visible_data = False
            grouped_data_for_mean_contour = {}
            legend_handles, legend_labels = [], []

            global_mean, global_std = None, None
            if self.norm_combo.currentText() == "Z-Score" and self.z_scope_combo.currentText() == "按整个数据集":
                all_f0_data = []
                for layer_config in self.layers:
                    if not layer_config.get('enabled', True): continue
                    
                    df_to_process = layer_config.get('df')
                    if df_to_process is not None and '_is_ignored' in df_to_process.columns:
                        df_to_process = df_to_process[df_to_process['_is_ignored'] == False]

                    f0_col = layer_config.get('f0_col')
                    if df_to_process is not None and f0_col and f0_col in df_to_process.columns:
                        all_f0_data.append(df_to_process[f0_col].dropna())
                
                if all_f0_data:
                    all_f0 = pd.concat(all_f0_data)
                    global_mean, global_std = all_f0.mean(), all_f0.std()
                    if global_std == 0 or np.isnan(global_std): global_std = 1

            for layer_config in self.layers:
                if not layer_config.get('enabled', True): continue
                
                df_original = layer_config.get('df')
                
                if df_original is not None and '_is_ignored' in df_original.columns:
                    df = df_original[df_original['_is_ignored'] == False].copy()
                else:
                    df = df_original
                
                time_col, f0_col, group_col = layer_config.get('time_col'), layer_config.get('f0_col'), layer_config.get('group_col')

                if df is None or not all(c in df.columns for c in [time_col, f0_col]):
                    continue
                
                if group_col != "无分组" and group_col in df.columns:
                    plot_df_base = df.dropna(subset=[time_col, f0_col, group_col]).copy()
                    
                    for global_group_name_str, global_group_settings in self.global_groups.items():
                        is_globally_enabled = global_group_settings.get('enabled', True)
                        
                        layer_groups = layer_config.get('groups', {})
                        layer_group_settings = layer_groups.get(global_group_name_str, {})
                        is_layer_enabled = layer_group_settings.get('enabled', True)

                        if not (is_globally_enabled and is_layer_enabled):
                            continue
                        
                        current_group_df = plot_df_base[plot_df_base[group_col].astype(str) == global_group_name_str].copy()
                        if current_group_df.empty: continue

                        df_processed = self._process_single_dataframe(current_group_df, time_col, f0_col, layer_config, global_mean, global_std)
                        if df_processed is None or df_processed.empty: continue

                        t_data, f0_data = df_processed[time_col], df_processed[f0_col]
                        color_hex = global_group_settings['color'].name()
                        
                        is_emphasized = layer_group_settings.get('emphasized', False)
                        
                        if not self.show_average_only_check.isChecked():
                            plot_kwargs = { 'color': color_hex, 'picker': 5, 'linewidth': 1.5, 'alpha': 0.8, 'zorder': 10 }
                            # --- [核心修改] ---
                            scatter_kwargs = {
                                'color': color_hex,
                                's': layer_config.get('point_size', 10),
                                'alpha': layer_config.get('point_alpha', 0.4),
                                'zorder': 5,
                                'edgecolors': 'black', # 添加白色轮廓
                                'linewidths': 0.5      # 设置轮廓线宽
                            }
                            # --- 结束修改 ---
                            scatter_kwargs = { 'color': color_hex, 's': layer_config.get('point_size', 10), 'alpha': layer_config.get('point_alpha', 0.4), 'zorder': 5 }
                            if is_emphasized:
                                plot_kwargs['linewidth'] = 4.0; plot_kwargs['alpha'] = 1.0; plot_kwargs['zorder'] = 20
                                scatter_kwargs['alpha'] = min(1.0, layer_config.get('point_alpha', 0.4) * 2)

                            line, = ax.plot(t_data, f0_data, **plot_kwargs)
                            
                            label = f"{layer_config['name']} - {global_group_name_str}"
                            self.plotted_lines.append({'line': line, 'label': label, 'data': df_processed[[time_col, f0_col]]})
                            has_any_visible_data = True
                            
                            if layer_config.get('show_points', False): 
                                # --- [核心修改] ---
                                scatter_kwargs = {
                                    'color': color_hex,
                                    's': layer_config.get('point_size', 10),
                                    'alpha': layer_config.get('point_alpha', 0.4),
                                    'zorder': 5,
                                    'edgecolors': 'black', # 添加白色轮廓
                                    'linewidths': 0.5      # 设置轮廓线宽
                                }
                                ax.scatter(t_data, f0_data, **scatter_kwargs)
                                # --- 结束修改 ---
                        
                        if global_group_name_str not in grouped_data_for_mean_contour: 
                            grouped_data_for_mean_contour[global_group_name_str] = {'curves': [], 'color': global_group_settings['color']}
                        grouped_data_for_mean_contour[global_group_name_str]['curves'].append(df_processed)
                
                else:
                    df_processed = self._process_single_dataframe(df.copy() if df is not None else pd.DataFrame(), time_col, f0_col, layer_config, global_mean, global_std)
                    if df_processed is None or df_processed.empty: continue
                    if not self.show_average_only_check.isChecked():
                        t_data, f0_data = df_processed[time_col], df_processed[f0_col]
                        label = layer_config['name']
                        color_hex = QColor(Qt.darkGray).name()
                        line, = ax.plot(t_data, f0_data, color=color_hex, zorder=10, picker=5, linewidth=1.5, alpha=0.8)
                        self.plotted_lines.append({'line': line, 'label': label, 'data': df_processed[[time_col, f0_col]]})
                        has_any_visible_data = True
                        if layer_config.get('show_points', False): 
                            ax.scatter(t_data, f0_data, color=color_hex, s=layer_config.get('point_size', 10), alpha=layer_config.get('point_alpha', 0.4), zorder=5)

            if self.show_mean_contour_check.isChecked() and self.normalize_time_check.isChecked():
                self._plot_mean_contours(ax, grouped_data_for_mean_contour, legend_handles, legend_labels)
                has_any_visible_data = True
            if has_any_visible_data:
                self.hover_annotation = ax.text(0.98, 0.98, '', transform=ax.transAxes, ha='right', va='top', fontsize=9, bbox=dict(boxstyle='round,pad=0.3', fc='wheat', alpha=0.9), zorder=100)
                self.hover_annotation.set_visible(False)
            ax.set_title(self.title_edit.text(), fontsize=14); ax.set_xlabel(self.xlabel_edit.text()); ax.set_ylabel(self.ylabel_edit.text()); ax.grid(True, linestyle='--', alpha=0.6); ax.autoscale_view()
            if self.show_legend_check.isChecked():
                if not self.show_average_only_check.isChecked():
                    legend_handles.clear(); legend_labels.clear()

                    # --- [核心优化开始] ---
                    # 1. 创建一个集合，用于存储所有在可见图层中被启用的分组名称
                    visible_and_enabled_groups = set()
                    for layer_config in self.layers:
                        # 只考虑可见的图层
                        if not layer_config.get('enabled', True):
                            continue
                        
                        # 遍历该图层内部的分组设置
                        layer_groups_settings = layer_config.get('groups', {})
                        for group_name, settings in layer_groups_settings.items():
                            # 如果该分组在此图层中是启用的，就把它加到集合里
                            if settings.get('enabled', True):
                                visible_and_enabled_groups.add(group_name)
                    
                    # 2. 遍历全局分组，但现在要同时检查它是否在上面的集合里
                    for group_name, settings in sorted(self.global_groups.items()):
                        is_globally_enabled = settings.get('enabled', True)
                        
                        # 只有当分组是全局启用，并且在至少一个可见图层中也被启用了，才显示图例
                        if is_globally_enabled and group_name in visible_and_enabled_groups:
                            color = settings.get('color', QColor(Qt.black))
                            line = Line2D([0], [0], color=color.name(), lw=2)
                            legend_handles.append(line)
                            legend_labels.append(group_name)
                    # --- [核心优化结束] ---

                if legend_handles:
                    ax.legend(handles=legend_handles, labels=legend_labels, loc='center left', bbox_to_anchor=(1.02, 0.5), fontsize='small', labelspacing=1.2)
            
            self.figure.tight_layout(rect=[0, 0, 1, 1]); self.canvas.draw()
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "绘图失败", f"生成图表时发生错误: {e}\n\n请检查数据列和图层配置。")
            self.figure.clear(); self.canvas.draw()

    def _process_single_dataframe(self, df, time_col, f0_col, layer_config, global_zscore_mean=None, global_zscore_std=None):
        """
        对单个 DataFrame 进行归一化、平滑等处理。
        此方法根据全局设置和图层级设置处理数据。
        """
        df_processed = df[[time_col, f0_col]].copy()
        
        # F0 间隙插值 (全局设置)
        if self.interpolate_gaps_check.isChecked():
            df_processed[f0_col] = df_processed[f0_col].interpolate(method='linear')
        df_processed.dropna(inplace=True) # 丢弃所有 NaN 的行
        if df_processed.empty: return None

        # F0 归一化 (全局设置)
        norm_method = self.norm_combo.currentText()
        if norm_method == "半音 (Semitone)":
            try: ref = float(self.st_ref_edit.text())
            except ValueError: ref = 100.0 # 默认值
            if ref <= 0: ref = 1 # 避免除以零
            df_processed[f0_col] = 12 * np.log2(df_processed[f0_col] / ref)
        elif norm_method == "Z-Score":
            z_scope = self.z_scope_combo.currentText()
            if z_scope == "按分组":
                mean = df_processed[f0_col].mean(); std = df_processed[f0_col].std()
                df_processed[f0_col] = (df_processed[f0_col] - mean) / (std if std != 0 else 1)
            elif z_scope == "按整个数据集":
                if global_zscore_mean is not None and global_zscore_std is not None and global_zscore_std != 0:
                    df_processed[f0_col] = (df_processed[f0_col] - global_zscore_mean) / global_zscore_std

        # 时间归一化 (全局设置)
        if self.normalize_time_check.isChecked():
            t_min, t_max = df_processed[time_col].min(), df_processed[time_col].max()
            if t_max > t_min:
                df_processed[time_col] = 100 * (df_processed[time_col] - t_min) / (t_max - t_min)
            else:
                df_processed[time_col] = 0 # 避免单点或零时长曲线导致NaN
        
        # 曲线平滑 (图层级设置)
        if layer_config.get('smoothing_enabled', True):
            win_val = layer_config.get('smoothing_window', 4)
            win_size = 2 * win_val + 1 # 窗口大小 (例如：4 -> 9点窗口)
            # 确保窗口大小不大于数据长度，否则 rolling 会返回 NaN
            if len(df_processed) >= win_size:
                df_processed[f0_col] = df_processed[f0_col].rolling(window=win_size, center=True, min_periods=1).mean()
        
        return df_processed.dropna() # 再次dropna以处理平滑后的NaN

    def _plot_mean_contours(self, ax, grouped_data, legend_handles, legend_labels):
        """
        [MODIFIED] 计算并绘制分组的平均轮廓线，并将其句柄和标签添加到图例列表中。
        """
        mean_time_axis = np.linspace(0, 100, 101)

        for group_name_str, data in grouped_data.items():
            if not self.normalize_time_check.isChecked(): continue

            resampled_curves = []
            for df_processed in data['curves']:
                resampled_f0 = np.interp(mean_time_axis, df_processed.iloc[:,0], df_processed.iloc[:,1])
                resampled_curves.append(resampled_f0)
            
            if len(resampled_curves) > 0:
                mean_f0_curve = np.mean(np.array(resampled_curves), axis=0)
                
                # --- [核心修复] ---
                # 1. 绘制平均线，不再需要 label 参数，但要捕获返回的 line 对象
                line, = ax.plot(mean_time_axis, mean_f0_curve,
                                color=data['color'].name(),
                                linestyle='--',
                                linewidth=3,
                                zorder=20)
                
                # 2. 将 line 句柄和我们想要的标签手动添加到列表中
                legend_handles.append(line)
                legend_labels.append(f"{group_name_str} (平均)")

    # ==========================================================================
    # UI状态更新和辅助方法
    # ==========================================================================
    def _update_ui_state(self):
        """根据当前图层数据和选中状态更新UI控件的可用性。"""
        has_layers = bool(self.layers)
        is_layer_selected = self.current_selected_layer_index > -1
        
        self.plot_button.setEnabled(has_layers) # 有图层才能绘图
        self.layer_settings_group.setEnabled(is_layer_selected) # 选中图层才能调整设置

        # 检查当前是否有任何活跃的分组
        has_active_grouping = any(s.get('enabled', True) for s in self.global_groups.values())
        
        # 全局分组样式面板的可见性 (始终可见，但内容可能动态变化)
        # self.grouping_group.setVisible(has_active_grouping) # 此行已不需要，因为面板始终可见
        
        # 平均轮廓的启用状态逻辑 (在 _on_global_setting_changed 中处理)
        self.show_mean_contour_check.setEnabled(has_active_grouping and self.normalize_time_check.isChecked())
        if not (has_active_grouping and self.normalize_time_check.isChecked()):
            self.show_mean_contour_check.setChecked(False) # 不满足条件时强制取消勾选

    def _show_context_menu(self, pos):
        """
        [v3.4 - 离群点处理版]
        显示画布的右键上下文菜单。
        - 增加了“框选忽略点”和“恢复所有忽略的点”功能。
        """
        menu = QMenu(self)
        # [核心新增] 工程文件操作
        open_proj_action = menu.addAction(self.icon_manager.get_icon("open_folder"), "打开工程 (.pavp)...")
        save_proj_action = menu.addAction(self.icon_manager.get_icon("save_as"), "保存工程 (.pavp)...")
        menu.addSeparator()
        
        refresh_action = menu.addAction(self.icon_manager.get_icon("refresh"), "刷新图表")
        reset_view_action = menu.addAction(self.icon_manager.get_icon("zoom_selection"), "重置视图/缩放")
        menu.addSeparator()

        # --- [核心修改] 新增离群点处理功能 ---
        is_layer_selected = self.current_selected_layer_index != -1
        ignore_action = menu.addAction(self.icon_manager.get_icon("select_object"), "框选忽略点 (当前图层)...")
        restore_action = menu.addAction(self.icon_manager.get_icon("undo"), "恢复所有忽略的点 (当前图层)")
        ignore_action.setEnabled(is_layer_selected)
        restore_action.setEnabled(is_layer_selected)
        menu.addSeparator()
        # --- 结束修改 ---
        
        copy_action = menu.addAction(self.icon_manager.get_icon("copy"), "复制图片到剪贴板")
        save_action = menu.addAction(self.icon_manager.get_icon("save"), "保存图片...")
        menu.addSeparator()
        
        clear_action = menu.addAction(self.icon_manager.get_icon("clear_contents"), "清空所有图层...")
        
        action = menu.exec_(self.canvas.mapToGlobal(pos))

        if action == open_proj_action: self._handle_open_project()
        elif action == save_proj_action: self._handle_save_project()
        elif action == refresh_action: self._update_plot()
        elif action == reset_view_action: self._reset_view()
        elif action == ignore_action: self._start_ignore_selection()
        elif action == restore_action: self._restore_ignored_points()
        elif action == copy_action: self._copy_plot_to_clipboard()
        elif action == save_action: self._save_plot_image()
        elif action == clear_action: self._clear_all_data()

    # [新增] handle 方法
    def _handle_open_project(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "打开工程文件", "", "PhonAcq 工程文件 (*.pavp)")
        if not filepath: return
        try:
            data, temp_dir = self._open_project_from_file(filepath)
            if data and temp_dir:
                self._restore_state_from_pavp(data, temp_dir)
                if hasattr(self.parent(), 'statusBar'):
                    self.parent().statusBar().showMessage(f"工程 '{os.path.basename(filepath)}' 已加载。", 3000)
        except Exception as e:
            QMessageBox.critical(self, "打开工程失败", f"无法加载工程文件:\n{e}")
            if hasattr(self, 'project_temp_dir') and self.project_temp_dir:
                shutil.rmtree(self.project_temp_dir, ignore_errors=True)
                self.project_temp_dir = None

    # [新增] handle 方法
    def _handle_save_project(self):
        if not self.layers:
            QMessageBox.warning(self, "无内容", "没有可保存的图层。")
            return
        filepath, _ = QFileDialog.getSaveFileName(self, "保存工程文件", "未命名语调工程.pavp", "PhonAcq 工程文件 (*.pavp)")
        if not filepath: return
        try:
            self._save_project_to_file(self.plugin_id, filepath)
            if hasattr(self.parent(), 'statusBar'):
                self.parent().statusBar().showMessage(f"工程已保存到 '{os.path.basename(filepath)}'。", 3000)
        except Exception as e:
            QMessageBox.critical(self, "保存工程失败", f"无法保存工程文件:\n{e}")
 
    def _load_data_from_file_dialog(self):
        """通过文件对话框加载数据（CSV/Excel/TextGrid），并添加到图层。"""
        path, _ = QFileDialog.getOpenFileName(self, "选择数据文件", "", f"支持的文件 (*{' '.join(self.SUPPORTED_EXTENSIONS)});;所有文件 (*.*)")
        if path:
            self._load_and_add_file(path)

    def _reset_view(self):
        """重置坐标轴范围并重绘图表。"""
        # 对于 Matplotlib，调用 autoscale_view() 即可重置到数据默认范围
        self.figure.gca().autoscale_view()
        self._update_plot()
 
    def _copy_plot_to_clipboard(self):
        """将当前图表画布渲染为图片并复制到系统剪贴板。"""
        try: 
            pixmap = self.canvas.grab() # 抓取画布内容
            QApplication.clipboard().setPixmap(pixmap) # 复制到剪贴板
            if hasattr(self.parent(), 'statusBar'):
               self.parent().statusBar().showMessage("图表已复制到剪贴板", 2000)
        except Exception as e: 
            QMessageBox.critical(self, "复制失败", f"无法将图片复制到剪贴板: {e}")
 
    def _clear_all_data(self):
        """清空所有已加载的图层和图表，恢复到初始状态。"""
        reply = QMessageBox.question(
            self,
            "确认清空",
            "您确定要清空所有已加载的图层和配置吗？\n此操作不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.No: return
 
        self.layers.clear() # 清空所有图层数据
        self.current_selected_layer_index = -1 # 重置选中索引
        self.global_groups.clear() # [NEW] 清空全局分组状态
        
        self.table_view.setModel(None) # 清空数据预览表格
        
        self._update_layer_table() # 更新图层列表UI (会清空列表)
        self._on_layer_selection_changed() # 模拟选择变化，清空右侧面板
        self._update_all_group_settings() # [NEW] 清空数据后更新全局分组UI
        
        # 清空图表
        self.figure.clear(); self.canvas.draw()
        
        self._update_ui_state() # 更新UI状态

    def _save_plot_image(self):
        """
        [v2.1 - 标准化路径版]
        保存图表为图片文件。
        - 默认保存路径现在是 `Results/analyze/charts/intonation/`。
        """
        title = self.title_edit.text()
        safe_filename = re.sub(r'[\\/*?:"<>|]', "_", title)
        
        # --- [核心修改] 获取标准的图表保存目录 ---
        charts_dir, _ = self._get_or_create_analysis_dirs()
        
        default_dir = charts_dir if charts_dir else None
        default_path = os.path.join(default_dir, f"{safe_filename}.png") if default_dir else f"{safe_filename}.png"

        file_path, _ = QFileDialog.getSaveFileName(
            self, 
            "保存图片", 
            default_path, # 使用新的默认路径
            "PNG图片 (*.png);;高分辨率PDF (*.pdf);;JPEG图片 (*.jpg);;SVG矢量图 (*.svg)"
        )
 
        if file_path:
            try: 
                self.figure.savefig(file_path, dpi=300, bbox_inches='tight', facecolor='white')
                QMessageBox.information(self, "成功", f"图表已保存到:\n{file_path}")
            except Exception as e: 
                QMessageBox.critical(self, "保存失败", f"无法保存图片: {e}")

    def _auto_match_textgrid_for_layer(self, layer_config, predefined_tg_path=None):
        """
        [v11.2 - 预设路径增强版]
        为一个图层自动查找、加载并应用TextGrid。
        - 新增 predefined_tg_path 参数，如果提供，则优先加载此文件。
        """
        import re
        import textgrid
        import pandas as pd

        found_tg_path = None
        # 1. 如果有预设路径且该文件存在，则优先使用它
        if predefined_tg_path and os.path.exists(predefined_tg_path):
            found_tg_path = predefined_tg_path
        else:
            # 2. 否则，执行扫描逻辑
            _, textgrids_dir = self._get_or_create_analysis_dirs()
            if not textgrids_dir: return

            layer_name = layer_config.get('name')
            if not layer_name: return
            
            core_layer_name = re.sub(r'(_analysis.*|_slice.*)', '', layer_name, flags=re.IGNORECASE)

            for filename in os.listdir(textgrids_dir):
                if filename.lower().endswith('.textgrid'):
                    tg_base_name = os.path.splitext(filename)[0]
                    if tg_base_name.lower() == core_layer_name.lower():
                        found_tg_path = os.path.join(textgrids_dir, filename)
                        break

        # 3. 如果找到了 TextGrid 文件，则加载并应用
        if found_tg_path:
            print(f"[Intonation Visualizer] Loading TextGrid for layer '{layer_config['name']}' from '{os.path.basename(found_tg_path)}'")
            try:
                tg_object = textgrid.TextGrid.fromFile(found_tg_path)
                df = layer_config.get('df')

                if df is not None and 'timestamp' in df.columns:
                    # --- [核心修改开始] ---
                    # 1. 找到第一个 IntervalTier 作为默认选择
                    first_interval_tier = next((tier for tier in tg_object if isinstance(tier, textgrid.IntervalTier)), None)
                    
                    if first_interval_tier:
                        # 2. 清理旧列
                        if 'textgrid_label' in df.columns:
                            df.drop(columns=['textgrid_label'], inplace=True)

                        # 3. 应用第一个找到的Tier
                        new_col_name = first_interval_tier.name
                        label_col = pd.Series(np.nan, index=df.index, dtype=object)
                        for interval in first_interval_tier:
                            if interval.mark:
                                mask = (df['timestamp'] >= interval.minTime) & (df['timestamp'] < interval.maxTime)
                                label_col.loc[mask] = interval.mark
                        df[new_col_name] = label_col

                        # 4. 更新图层配置
                        layer_config['tg'] = tg_object
                        layer_config['tg_filename'] = os.path.basename(found_tg_path)
                        layer_config['original_tg_path'] = found_tg_path 
                        layer_config['tg_tier'] = new_col_name # 保存使用的Tier名称
                        layer_config['group_col'] = new_col_name # 设置分组依据
                    else:
                        print(f"[Intonation Visualizer WARNING] Matched TextGrid '{found_tg_path}' contains no IntervalTiers.")
                    # --- [核心修改结束] ---
                else:
                    print(f"[Intonation Visualizer WARNING] Layer '{layer_config['name']}' has a matching TextGrid, but its DataFrame is missing a 'timestamp' column.")

            except Exception as e:
                print(f"[Intonation Visualizer ERROR] Failed to load or apply TextGrid '{found_tg_path}': {e}")

    # ==========================================================================
    # Matplotlib 交互相关方法
    # ==========================================================================
    def _on_mouse_press(self, event):
        """
        [v3.4 - 离群点处理版]
        处理鼠标按下事件，用于开始平移。
        - 如果框选器激活，则不执行平移。
        """
        # --- [核心修改] ---
        if self.rect_selector is not None and self.rect_selector.active:
            return
        # --- 结束修改 ---

        if event.inaxes and event.button == 1:
            self._is_panning = True
            self._pan_start_pos = (event.xdata, event.ydata)
            self.canvas.setCursor(Qt.ClosedHandCursor)

    def _on_mouse_release(self, event):
        """处理鼠标释放事件，结束平移。"""
        if self._is_panning:
            self._is_panning = False
            self.canvas.setCursor(Qt.ArrowCursor) # 恢复鼠标光标

    def _on_mouse_move(self, event):
        """处理鼠标移动事件，用于平移或悬浮提示。"""
        if not event.inaxes:
            # 如果鼠标移出坐标轴，隐藏悬浮提示
            if self.hover_annotation and self.hover_annotation.get_visible():
                self.hover_annotation.set_visible(False)
                self.canvas.draw_idle() # 异步重绘，避免卡顿
            return
        
        # --- 拖动视图逻辑 ---
        if self._is_panning:
            ax = event.inaxes
            # 确保 event.xdata 和 event.ydata 有效，避免在鼠标快速移动出图表时报错
            if self._pan_start_pos is None or event.xdata is None or event.ydata is None: return 
            
            dx = event.xdata - self._pan_start_pos[0] # 计算X轴平移量
            dy = event.ydata - self._pan_start_pos[1] # 计算Y轴平移量
            
            cur_xlim = ax.get_xlim(); cur_ylim = ax.get_ylim()
            
            # 设置新的轴范围
            ax.set_xlim(cur_xlim[0] - dx, cur_xlim[1] - dx)
            ax.set_ylim(cur_ylim[0] - dy, cur_ylim[1] - dy)
            self.canvas.draw_idle() # 异步重绘
            return # 拖动时不进行悬浮检测
            
        # --- 悬浮提示逻辑 ---
        if self.hover_annotation is None: return # 如果没有悬浮提示对象，则跳过
 
        found_line = False # 标记是否找到最近的曲线
        
        for plot_item in self.plotted_lines:
            line = plot_item['line']
            # 使用 line.contains 来检测鼠标是否在绘制的曲线上
            contains, ind = line.contains(event) 
            
            if contains:
                if 'ind' in ind and len(ind['ind']) > 0:
                    data_index = ind['ind'][0] # 获取最近点的索引
                else:
                    # Fallback：如果 `ind` 没有返回索引，则手动计算最近点
                    x_data, y_data = line.get_data()
                    distances = np.sqrt((x_data - event.xdata)**2 + (y_data - event.ydata)**2)
                    data_index = np.argmin(distances)

                point_data = plot_item['data'].iloc[data_index] # 从原始数据中获取该行数据
                time_val = point_data.iloc[0] # 时间
                f0_val = point_data.iloc[1]   # F0
                
                label = plot_item['label']
                text = f"{label}\n时间: {time_val:.3f} s\nF0: {f0_val:.1f} Hz"
                self.hover_annotation.set_text(text)
                self.hover_annotation.set_visible(True)
                self.canvas.draw_idle() # 异步重绘
                found_line = True
                break # 找到一个点就停止
        
        # 如果没有找到点，且之前是可见的，则隐藏
        if not found_line and self.hover_annotation.get_visible():
            self.hover_annotation.set_visible(False)
            self.canvas.draw_idle()
    def _start_ignore_selection(self):
        """
        [v3.5 - 自动显示数据点版]
        激活矩形选择器以忽略数据点。
        - 进入此模式时，会自动勾选并应用“显示数据点”选项。
        """
        if self.current_selected_layer_index < 0:
            QMessageBox.warning(self, "无操作对象", "请先在左侧列表中选择一个图层。")
            return
        
        # --- [核心修改] ---
        # 1. 自动勾选“显示数据点”并更新图层配置
        self.show_points_check.setChecked(True)
        # 2. 手动调用一次槽函数，以确保配置更新和图表重绘
        self._on_current_layer_setting_changed()
        # 3. 强制处理UI事件，确保图表重绘完成，点都显示出来
        QApplication.processEvents()
        # --- 结束修改 ---
        
        ax = self.figure.gca()

        if self.show_ignore_mode_info:
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Information)
            msg_box.setWindowTitle("进入框选忽略模式")
            msg_box.setTextFormat(Qt.RichText)
            msg_box.setText(
                "已进入框选忽略模式。<br><br>"
                "■ 如果左侧<b>未选择</b>任何分组，将忽略框内所有点。<br>"
                "■ 如果左侧<b>已选择</b>一个或多个分组，将只忽略框内属于这些分组的点。<br><br>"
                "请拖动鼠标进行选择，按 'Esc' 键可取消。"
            )
            checkbox = QCheckBox("本次会话不再提示")
            msg_box.setCheckBox(checkbox)
            msg_box.exec()
            if checkbox.isChecked():
                self.show_ignore_mode_info = False

        if self.rect_selector:
            self.rect_selector.set_active(False)

        rect_props = dict(facecolor='red', edgecolor='red', alpha=0.2, fill=True)
        self.rect_selector = matplotlib.widgets.RectangleSelector(
            ax, self._on_ignore_selection, useblit=False, button=[1],
            minspanx=5, minspany=5, spancoords='pixels', interactive=True, props=rect_props
        )
        self.canvas.setCursor(Qt.CrossCursor)

    def _on_ignore_selection(self, eclick, erelease):
        """
        [v3.5 - 自动关闭数据点版]
        当用户完成一次矩形选择后的回调函数。
        - 无论操作是否成功，都会自动取消勾选“显示数据点”。
        """
        x1, y1 = eclick.xdata, eclick.ydata
        x2, y2 = erelease.xdata, erelease.ydata

        if self.rect_selector:
            self.rect_selector.set_active(False)
        self.canvas.setCursor(Qt.ArrowCursor)

        layer_index = self.current_selected_layer_index
        
        # --- [核心修改] ---
        # 使用 finally 块确保无论发生什么，都会执行清理操作
        try:
            if layer_index < 0:
                self.canvas.draw()
                return
                
            layer_config = self.layers[layer_index]
            df = layer_config.get('df')
            if df is None:
                self.canvas.draw()
                return

            if '_is_ignored' not in df.columns:
                df['_is_ignored'] = False

            time_col = layer_config.get('time_col')
            f0_col = layer_config.get('f0_col')
            group_col = layer_config.get('group_col')
            if not time_col or not f0_col:
                self.canvas.draw()
                return

            selected_items = self.group_table.selectedItems()
            selected_group_names = {item.text() for item in selected_items if item.column() == 0}

            min_x, max_x = min(x1, x2), max(x1, x2)
            min_y, max_y = min(y1, y2), max(y1, y2)
            
            spatial_mask = (
                (df[time_col] >= min_x) & (df[time_col] <= max_x) &
                (df[f0_col] >= min_y) & (df[f0_col] <= max_y)
            )

            final_mask = None
            if selected_group_names and group_col and group_col != "无分组" and group_col in df.columns:
                group_mask = df[group_col].astype(str).isin(selected_group_names)
                final_mask = spatial_mask & group_mask
            else:
                final_mask = spatial_mask
            
            num_ignored = final_mask.sum()
            if num_ignored > 0:
                df.loc[final_mask, '_is_ignored'] = True
                self._update_plot() # 在关闭数据点之前重绘一次，显示忽略结果
                QMessageBox.information(self, "操作完成", f"已成功忽略 {num_ignored} 个数据点。")
            else:
                self.canvas.draw()
        finally:
            # 无论是否成功忽略，都自动取消勾选“显示数据点”
            self.show_points_check.setChecked(False)
            self._on_current_layer_setting_changed() # 应用更改并最终重绘
        # --- 结束修改 ---

    def _restore_ignored_points(self):
        """恢复当前图层中所有被忽略的点。"""
        layer_index = self.current_selected_layer_index
        if layer_index < 0:
            QMessageBox.warning(self, "无操作对象", "请先在左侧列表中选择一个图层。")
            return
            
        layer_config = self.layers[layer_index]
        df = layer_config.get('df')

        if df is not None and '_is_ignored' in df.columns:
            num_restored = df['_is_ignored'].sum()
            if num_restored > 0:
                df['_is_ignored'] = False
                print(f"[Intonation Visualizer] Restored {num_restored} points in layer '{layer_config['name']}'.")
                self._update_plot()
                QMessageBox.information(self, "操作完成", f"已恢复 {num_restored} 个被忽略的点。")
            else:
                QMessageBox.information(self, "无需操作", "当前图层没有被忽略的点。")
        else:
            QMessageBox.information(self, "无需操作", "当前图层没有被忽略的点。")

    # ==========================================================================
    # 拖拽事件处理
    # ==========================================================================
    def dragEnterEvent(self, event):
        """当鼠标拖着文件进入窗口时触发"""
        mime_data = event.mimeData()
        # 检查是否包含URL(即文件路径)
        if mime_data.hasUrls():
            # 检查文件扩展名是否受支持
            if any(url.toLocalFile().lower().endswith(self.SUPPORTED_EXTENSIONS) for url in mime_data.urls()):
                event.acceptProposedAction()
                self.drop_overlay.show()  # 显示覆盖层提示

    def dragLeaveEvent(self, event):
        """当鼠标拖着文件离开窗口时触发"""
        self.drop_overlay.hide()

    def dropEvent(self, event):
        """当在窗口上释放文件时触发"""
        self.drop_overlay.hide()  # 隐藏覆盖层
        paths = [url.toLocalFile() for url in event.mimeData().urls()]
        
        for path in paths:
            if path.lower().endswith(self.SUPPORTED_EXTENSIONS):
                self._load_and_add_file(path) # 调用加载和添加函数

    def resizeEvent(self, event):
        """当窗口大小改变时，确保覆盖层也跟着改变大小"""
        super().resizeEvent(event)
        self.drop_overlay.setGeometry(self.rect())

    # ==========================================================================
    # 外部接口 (被 PluginManager 调用)
    # ==========================================================================
    def _load_and_add_file(self, file_path):
        """
        核心的文件加载和添加逻辑，可被多处调用 (拖拽、文件对话框、外部插件)。
        自动判断文件类型是数据文件还是 TextGrid。
        [已修复] 使用 delim_whitespace=True 来正确解析 Praat 的空格分隔文件。
        """
        try:
            if file_path.lower().endswith(('.xlsx', '.xls', '.csv')):
                # 加载标准表格文件，并打开配置对话框
                df = pd.read_excel(file_path) if file_path.lower().endswith(('.xlsx', '.xls')) else pd.read_csv(file_path)
                
                # 为新图层创建默认配置，并传入加载的 df 和文件名
                new_config = {
                    "df": df,
                    "data_filename": os.path.basename(file_path),
                    "name": os.path.splitext(os.path.basename(file_path))[0],
                    "tg": None, # 默认没有 TextGrid
                    "tg_filename": "未选择 (可选)",
                    "enabled": True,
                    "smoothing_enabled": True, "smoothing_window": 4,
                    "show_points": False, "point_size": 10, "point_alpha": 0.4
                }
                
                # 尝试自动检测列名
                numeric_cols = df.select_dtypes(include=np.number).columns.tolist()
                new_config['time_col'] = next((c for c in numeric_cols if 'time' in c.lower() or 'timestamp' in c.lower()), numeric_cols[0] if numeric_cols else "")
                new_config['f0_col'] = next((c for c in numeric_cols if 'f0' in c.lower() or 'hz' in c.lower()), numeric_cols[1] if len(numeric_cols) > 1 else "")
                new_config['group_col'] = next((c for c in df.columns if 'group' in c.lower() or 'label' in c.lower()), "无分组")

                # 弹出配置对话框让用户确认或修改设置
                dialog = LayerConfigDialog(existing_config=new_config, parent=self)
                if dialog.exec_() == QDialog.Accepted:
                    final_config = dialog.get_layer_config()
                    if final_config:
                        self.layers.append(final_config)
                        self._update_layer_table()
                        self._update_ui_state()
                        self._update_all_group_settings()
                        self._update_plot()

            # [核心修复] 为 Praat 的 .txt 文件添加专门的转换和清理逻辑
            elif file_path.lower().endswith('.txt'):
                # 1. [关键修改] 使用 delim_whitespace=True，并将 '--undefined--' 识别为 NaN
                df = pd.read_csv(file_path, delim_whitespace=True, na_values='--undefined--')

                # 2. 找到 F0 列并移除所有 NaN 行
                f0_col_praat = next((c for c in df.columns if 'f0' in c.lower() or 'frequency' in c.lower()), None)
                if f0_col_praat:
                    df.dropna(subset=[f0_col_praat], inplace=True)
                
                # 3. 重命名列以匹配插件内部标准
                rename_mapping = {}
                for col in df.columns:
                    if 'time' in col.lower():
                        rename_mapping[col] = 'timestamp'
                    elif 'f0' in col.lower() or 'frequency' in col.lower():
                        rename_mapping[col] = 'f0_hz'
                df.rename(columns=rename_mapping, inplace=True)
                
                # 4. 重置索引，使之连续
                df.reset_index(drop=True, inplace=True)

                # 检查转换是否成功
                if 'timestamp' not in df.columns or 'f0_hz' not in df.columns:
                    QMessageBox.warning(self, "列名不匹配", "无法在文件中找到标准的时间和F0列。\n请确保列名包含 'Time' 和 'F0' 或 'Frequency'。")
                    return
                
                # 创建新图层配置，现在 df 已经是干净的了
                new_config = {
                    "df": df,
                    "data_filename": os.path.basename(file_path),
                    "name": os.path.splitext(os.path.basename(file_path))[0],
                    "time_col": "timestamp", # 使用标准列名
                    "f0_col": "f0_hz",       # 使用标准列名
                    "group_col": "无分组",
                    "enabled": True,
                    "tg": None,
                    "tg_filename": "未选择 (可选)",
                    "smoothing_enabled": True, "smoothing_window": 4,
                    "show_points": False, "point_size": 10, "point_alpha": 0.4
                }

                # 弹出配置对话框让用户确认
                dialog = LayerConfigDialog(existing_config=new_config, parent=self)
                if dialog.exec_() == QDialog.Accepted:
                    final_config = dialog.get_layer_config()
                    if final_config:
                        self.layers.append(final_config)
                        self._update_layer_table()
                        self._update_ui_state()
                        self._update_all_group_settings()
                        self._update_plot()
            # [核心修复结束]

            elif file_path.lower().endswith('.textgrid'):
                # ... (此部分代码保持不变) ...
                current_row = self.layer_table.currentRow()
                if current_row == -1:
                    QMessageBox.warning(self, "未选择图层", "请先在左侧列表中选择一个数据图层，再拖入 TextGrid 文件。")
                    return

                layer_config = self.layers[current_row]
                if layer_config.get('df') is None:
                    QMessageBox.warning(self, "无数据文件", "当前选中的图层没有加载数据文件，无法关联 TextGrid。")
                    return
                if 'timestamp' not in layer_config['df'].columns:
                    QMessageBox.warning(self, "缺少时间戳", "当前图层的数据文件缺少 'timestamp' 列，无法匹配 TextGrid 标注。")
                    return
                
                try:
                    tg = textgrid.TextGrid.fromFile(file_path)
                    layer_config['tg'] = tg
                    layer_config['tg_filename'] = os.path.basename(file_path)
                    
                    temp_dialog = LayerConfigDialog(existing_config=layer_config, parent=self)
                    temp_dialog._apply_textgrid()
                    updated_config = temp_dialog.get_layer_config()
                    if updated_config:
                        self.layers[current_row] = updated_config
                        self._update_layer_table_row(current_row)
                        self._update_all_group_settings()
                        self._update_plot()
                        QMessageBox.information(self, "TextGrid 已应用", f"TextGrid '{os.path.basename(file_path)}' 已成功应用于图层 '{layer_config['name']}'。")
                except Exception as e:
                    QMessageBox.critical(self, "TextGrid 错误", f"无法加载或应用 TextGrid 文件: {e}")

            else:
                QMessageBox.warning(self, "文件类型不支持", f"文件 '{os.path.basename(file_path)}' 的类型不支持。请选择 .csv, .xlsx 或 .TextGrid 文件。")

        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法读取文件 '{os.path.basename(file_path)}':\n{e}")

    def add_data_source(self, df, source_name="从外部加载", audio_filepath=None):
        """
        [v11.3 - 顽固Bug最终修复版]
        从外部加载 DataFrame。此版本修复了分组依据后备逻辑错误覆盖
        TextGrid自动匹配结果的Bug。
        """
        if df is None or df.empty:
            QMessageBox.warning(self, "数据无效", "传入的 DataFrame 为空或无效。")
            return

        numeric_cols = df.select_dtypes(include=np.number).columns.tolist()
        if len(numeric_cols) < 2:
            QMessageBox.warning(self, "数据格式错误", "传入的DataFrame至少需要两列数值型数据（时间和F0）。")
            return

        base_name = source_name if source_name else "新数据"
        
        layer_name = base_name; counter = 1
        while any(layer['name'] == layer_name for layer in self.layers):
            layer_name = f"{base_name} ({counter})"; counter += 1
        
        new_layer_config = {
            "name": layer_name,
            "df": df,
            "data_filename": f"{source_name} (实时数据)",
            "enabled": True,
            "tg": None, "tg_filename": "未选择 (可选)",
            "group_col": "无分组", # 预设为默认值
            "audio_path": None, "audio_data": None,
            "smoothing_enabled": True, "smoothing_window": 4,
            "show_points": False, "point_size": 10, "point_alpha": 0.4
        }

        if audio_filepath and os.path.exists(audio_filepath):
            try:
                import librosa
                print(f"[Intonation Visualizer] Auto-loading audio for layer '{layer_name}' from: {audio_filepath}")
                y, sr = librosa.load(audio_filepath, sr=None, mono=True)
                
                new_layer_config['audio_path'] = audio_filepath
                new_layer_config['audio_data'] = (y, sr)
                print(f"[Intonation Visualizer] Audio loaded successfully. Shape: {y.shape}, SR: {sr}")
            except Exception as e:
                print(f"[Intonation Visualizer ERROR] Failed to auto-load audio for layer '{layer_name}': {e}")
        
        # 步骤 1: 尝试通过TextGrid自动设置分组依据
        self._auto_match_textgrid_for_layer(new_layer_config)

        # --- [核心修复] ---
        # 步骤 2: 只有当自动匹配未能设置分组依据时，才执行后备的猜测逻辑。
        # 原来的 if new_layer_config.get('group_col') != 'textgrid_label': 是错误的。
        if not new_layer_config.get('group_col') or new_layer_config.get('group_col') == "无分组":
            all_cols = df.columns.tolist()
            # 执行猜测
            new_layer_config['group_col'] = next((c for c in all_cols if 'group' in c.lower() or 'label' in c.lower() or 'category' in c.lower()), "无分组")
        # --- [修复结束] ---

        # 步骤 3: 设置时间列和F0列
        new_layer_config['time_col'] = next((c for c in numeric_cols if 'time' in c.lower() or 'timestamp' in c.lower()), numeric_cols[0])
        new_layer_config['f0_col'] = next((c for c in numeric_cols if 'f0' in c.lower() or 'hz' in c.lower()), numeric_cols[1] if len(numeric_cols) > 1 else numeric_cols[0])

        # 步骤 4: 将最终正确的配置添加到图层列表并更新UI
        self.layers.append(new_layer_config)
        self._update_layer_table()
        self._update_all_group_settings()
        self._update_plot()

# ==============================================================================
# 插件主入口类
# ------------------------------------------------------------------------------
# 此类是插件的统一入口，负责插件的生命周期管理，并创建/显示 VisualizerDialog。
# ==============================================================================
class IntonationVisualizerPlugin(BasePlugin):
    def __init__(self, main_window=None, plugin_manager=None):
        super().__init__(main_window, plugin_manager)
        self.visualizer_dialog = None # 存储对话框实例，实现单例模式

    def setup(self):
        """插件初始化设置。"""
        if not LIBS_AVAILABLE:
            print("[Intonation Visualizer Error] Missing core dependencies. Plugin setup failed.")
            return False 
        # 尝试将插件实例注册到主程序的音频分析模块
        if hasattr(self, 'main_window') and self.main_window:
            self.audio_analysis_page = getattr(self.main_window, 'audio_analysis_page', None)
            if self.audio_analysis_page and hasattr(self.audio_analysis_page, 'spectrogram_widget'):
                # 注入一个钩子，让音频分析模块能找到本插件实例
                self.audio_analysis_page.spectrogram_widget.intonation_visualizer_plugin_active = self
                print("[Intonation Visualizer] Successfully hooked into Audio Analysis module.")
        else:
            print("[Intonation Visualizer] Running in standalone mode or main window not found.")
        return True

    def teardown(self):
        """插件卸载清理。"""
        # 从音频分析模块中解钩
        if hasattr(self, 'audio_analysis_page') and self.audio_analysis_page:
            if getattr(getattr(self.audio_analysis_page, 'spectrogram_widget', None), 'intonation_visualizer_plugin_active', None) is self:
                del self.audio_analysis_page.spectrogram_widget.intonation_visualizer_plugin_active
                print("Intonation Visualizer unhooked.")
        # 关闭可能存在的对话框
        if self.visualizer_dialog:
            self.visualizer_dialog.close()
            self.visualizer_dialog = None
        print("[Intonation Visualizer] Plugin has been torn down.")

    def execute(self, **kwargs):
        """
        [v2.2 - 音频路径感知版]
        插件的统一入口。现在可以接收并处理传入的 audio_filepath。
        """
        if self.visualizer_dialog is None:
            parent = self.main_window if hasattr(self, 'main_window') else None
            icon_manager = getattr(parent, 'icon_manager', None) if parent else None
            self.visualizer_dialog = VisualizerDialog(parent=parent, icon_manager=icon_manager)
            self.visualizer_dialog.finished.connect(self._on_dialog_finished)

        dataframe_to_load = kwargs.get('dataframe')
        source_name = kwargs.get('source_name')
        # [核心新增] 从 kwargs 中安全地获取 audio_filepath
        audio_filepath = kwargs.get('audio_filepath')

        if dataframe_to_load is not None:
            # [核心新增] 将 audio_filepath 传递给 add_data_source 方法
            self.visualizer_dialog.add_data_source(
                dataframe_to_load, 
                source_name, 
                audio_filepath=audio_filepath
            )

        self.visualizer_dialog.show()
        self.visualizer_dialog.raise_()
        self.visualizer_dialog.activateWindow()

    def _on_dialog_finished(self):
        """当对话框关闭时，重置实例变量。"""
        self.visualizer_dialog = None

# --- 用于独立测试运行 ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    
    # 创建一个模拟的插件实例
    plugin = IntonationVisualizerPlugin()
    
    # 创建两个模拟的DataFrame
    time1 = np.linspace(0, 1.5, 150)
    f0_1 = 120 + 40 * np.sin(2 * np.pi * 1.2 * time1) + np.random.randn(150) * 5
    df1 = pd.DataFrame({'timestamp': time1, 'f0_hz': f0_1, 'group_label': '陈述句', 'gender': '男'})

    time2 = np.linspace(0, 1.2, 120)
    f0_2 = 220 - 50 * np.cos(2 * np.pi * 1.5 * time2) + np.random.randn(120) * 8
    df2 = pd.DataFrame({'time': time2, 'F0': f0_2, 'category': '疑问句', 'gender': '女'})

    # 通过execute方法加载数据
    plugin.execute(dataframe=df1, source_name="说话人A-男声")
    plugin.execute(dataframe=df2, source_name="说话人B-女声")

    sys.exit(app.exec_())

# --- END OF COMPLETE AND REFACTORED FILE ---