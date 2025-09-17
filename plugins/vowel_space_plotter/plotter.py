# --- START OF MODIFIED FILE plugins/vowel_space_plotter/plotter.py ---

import os
import sys
import re
import pandas as pd
import numpy as np
import shutil
import uuid
from itertools import cycle
from copy import deepcopy # 用于深度复制图层配置

# PyQt5 模块导入
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog,
                             QMessageBox, QTableView, QHeaderView, QComboBox, QCheckBox,
                             QSplitter, QGroupBox, QLineEdit, QFormLayout,
                             QColorDialog, QSlider, QWidget, QScrollArea, QMenu, QFrame, QGridLayout, QApplication,
                             QTableWidget, QTableWidgetItem, QDialogButtonBox)
from PyQt5.QtCore import Qt, QAbstractTableModel, QSize, pyqtSignal, QEvent
from PyQt5.QtGui import QIcon, QColor, QPalette, QPixmap, QFont, QCursor, QIntValidator
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent

# Matplotlib 和 textgrid 库导入
try:
    import matplotlib
    matplotlib.use('Qt5Agg') # 指定 Matplotlib 后端
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    from matplotlib.patches import Ellipse # 用于绘制椭圆
    from matplotlib.widgets import RectangleSelector # [新增] 用于框选忽略点
    import textgrid # 用于处理 TextGrid 文件

    # 设置 Matplotlib 中文字体，避免乱码
    def set_matplotlib_font():
        font_candidates = ['Microsoft YaHei', 'SimHei', 'Source Han Sans CN', 'PingFang SC', 'WenQuanYi Micro Hei', 'Arial Unicode MS']
        from matplotlib.font_manager import findfont, FontProperties
        found_font = next((font for font in font_candidates if findfont(FontProperties(family=font))), None)
        if found_font:
            matplotlib.rcParams['font.sans-serif'] = [found_font] # 设置中文字体
            matplotlib.rcParams['axes.unicode_minus'] = False # 解决负号显示问题
            print(f"[Vowel Plotter] Found and set Chinese font: {found_font}")
        else:
            print("[Vowel Plotter Warning] No suitable Chinese font found.")
    set_matplotlib_font()
    LIBS_AVAILABLE = True # 标记依赖库是否可用
except ImportError as e:
    print(f"[Vowel Plotter Error] Missing required library: {e}. Please run 'pip install matplotlib textgrid'")
    LIBS_AVAILABLE = False # 依赖缺失，禁用相关功能

# 插件API导入
try:
    from modules.plugin_system import BasePlugin
except ImportError:
    # 如果在独立测试插件时，确保能找到 plugin_system.py
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from modules.plugin_system import BasePlugin
try:
    from modules.custom_widgets_module import ColorButton
except ImportError:
    # 如果 custom_widgets_module 不存在或导入失败，提供一个回退方案
    # 这确保了即使在旧版本或模块缺失时，插件也不会完全崩溃
    print("[Vowel Plotter Warning] Could not import ColorButton from custom_widgets_module. Using a fallback.")
    ColorButton = QPushButton
# ==============================================================================
# [新增] 合并图层对话框 (MergeLayersDialog)
# ==============================================================================
class MergeLayersDialog(QDialog):
    """一个简单的对话框，用于获取合并后新图层的名称。"""
    def __init__(self, num_layers, parent=None):
        super().__init__(parent)
        self.setWindowTitle("合并图层")
        
        layout = QVBoxLayout(self)
        
        # 提示信息
        label = QLabel(f"您正在合并 <b>{num_layers}</b> 个图层。")
        layout.addWidget(label)
        
        # 表单布局用于对齐
        form_layout = QFormLayout()
        self.name_edit = QLineEdit("merged_layer") # 默认名称
        self.name_edit.setToolTip("请输入合并后新图层的名称。")
        form_layout.addRow("新图层名称:", self.name_edit)
        
        layout.addLayout(form_layout)
        
        # 确定/取消按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_new_name(self):
        """获取用户输入的新图层名称。"""
        return self.name_edit.text().strip()
# ==============================================================================
# 辅助类：PandasModel
# 用于将 Pandas DataFrame 显示在 QTableView 中
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
            # 确保返回字符串，避免 QVariant 转换问题
            return str(self._data.iloc[index.row(), index.column()])
        return None

    def headerData(self, section, orientation, role):
        if role == Qt.DisplayRole:
            if orientation == Qt.Horizontal:
                return str(self._data.columns[section])
            if orientation == Qt.Vertical:
                return str(self._data.index[section])
        return None

# ==============================================================================
# 图层配置对话框 (LayerConfigDialog)
# 用于配置单个数据图层，包括数据文件、TextGrid、列映射、颜色方案和锁定状态
# ==============================================================================
class LayerConfigDialog(QDialog):
    def __init__(self, existing_config=None, parent=None):
        super().__init__(parent)
        self.df = None
        self.tg = None
        
        # --- [核心修复] ---
        # 使用浅复制 .copy() 来复制配置，这不会尝试复制内部的对象。
        self.config = existing_config.copy() if existing_config else {}
        if existing_config:
            # 手动恢复对大型数据对象（如DataFrame, TextGrid, audio_data）的引用，
            # 而不是复制它们。这既高效又安全。
            self.df = existing_config.get('df')
            self.tg = existing_config.get('tg')
            # 确保 audio_data 也被正确引用，而不是被 deepcopy
            self.config['audio_data'] = existing_config.get('audio_data')
        # QMediaPlayer 对象 ('player') 将不再是问题，因为它只被浅复制（引用传递）。
        # --- 修复结束 ---

        self.parent_dialog = parent
        self.split_layers = []
        self.setWindowTitle("配置数据图层")
        self.setMinimumWidth(500)
        self._init_ui()
        self._connect_signals()
        if self.config:
            self._populate_from_config()
        self._update_combos()

    def _init_ui(self):
        """
        [v12.0 - Tier选择同步版]
        初始化图层配置对话框的用户界面。
        - 新增了用于选择TextGrid Tier的下拉菜单，并初始隐藏。
        """
        layout = QVBoxLayout(self)
        form_layout = QFormLayout()
        form_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)

        self.name_edit = QLineEdit(self.config.get('name', ''))
        self.name_edit.setPlaceholderText("例如：说话人A-男声")
        self.name_edit.setToolTip("为该数据图层指定一个唯一的、有意义的名称。")

        data_layout = QHBoxLayout()
        self.load_data_btn = QPushButton("选择文件...")
        self.load_data_btn.setToolTip("加载包含共振峰数据的 Excel (.xlsx, .xls) 或 CSV (.csv) 文件。")
        self.data_file_label = QLabel(self.config.get('data_filename', "未选择"))
        self.data_file_label.setWordWrap(True)
        data_layout.addWidget(self.load_data_btn); data_layout.addWidget(self.data_file_label, 1)

        tg_layout = QHBoxLayout()
        self.load_tg_btn = QPushButton("选择文件...")
        self.load_tg_btn.setToolTip("加载 TextGrid (.TextGrid) 文件为数据点添加标签。\n数据文件必须包含 'timestamp' 列。")
        self.tg_file_label = QLabel(self.config.get('tg_filename', "未选择 (可选)"))
        self.tg_file_label.setWordWrap(True)
        tg_layout.addWidget(self.load_tg_btn); tg_layout.addWidget(self.tg_file_label, 1)

        # [核心新增] 创建用于选择TextGrid层的下拉菜单及其标签
        self.tg_tier_combo = QComboBox()
        self.tg_tier_combo.setToolTip("从加载的 TextGrid 文件中选择一个层(Tier)作为标注来源。")
        self.tg_tier_label = QLabel("选择层(Tier):")

        audio_layout = QHBoxLayout()
        self.load_audio_btn = QPushButton("选择文件...")
        self.load_audio_btn.setToolTip("为该图层加载关联的音频文件，以便进行片段播放。")
        audio_path = self.config.get('audio_path')
        audio_filename_display = os.path.basename(audio_path) if audio_path and isinstance(audio_path, str) else "未选择 (可选)"
        self.audio_file_label = QLabel(audio_filename_display)
        self.audio_file_label.setWordWrap(True)
        self.load_audio_btn.setEnabled(self.tg is not None)
        audio_layout.addWidget(self.load_audio_btn); audio_layout.addWidget(self.audio_file_label, 1)

        self.f1_combo = QComboBox(); self.f2_combo = QComboBox(); self.group_by_combo = QComboBox()
        self.color_scheme_combo = QComboBox(); self.color_scheme_combo.addItems(self.parent_dialog.COLOR_SCHEMES.keys())
        self.lock_check = QCheckBox("锁定图层")
        
        form_layout.addRow("图层名称:", self.name_edit)
        form_layout.addRow("数据文件:", data_layout)
        form_layout.addRow("TextGrid:", tg_layout)
        # [核心新增] 将Tier选择UI添加到布局中
        form_layout.addRow(self.tg_tier_label, self.tg_tier_combo)
        form_layout.addRow("音频文件:", audio_layout)
        separator = QFrame(); separator.setFrameShape(QFrame.HLine); separator.setFrameShadow(QFrame.Sunken)
        form_layout.addRow(separator)
        form_layout.addRow("F1 (Y轴):", self.f1_combo); form_layout.addRow("F2 (X轴):", self.f2_combo)
        form_layout.addRow("分组依据:", self.group_by_combo); form_layout.addRow("颜色方案:", self.color_scheme_combo)
        form_layout.addRow(self.lock_check)

        # [核心新增] 初始时隐藏Tier选择功能
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
        # [核心新增] 连接Tier选择下拉框的信号，当用户切换Tier时，自动应用
        self.tg_tier_combo.currentTextChanged.connect(self._apply_selected_tier_to_df)

    def _load_audio(self):
        """
        [v11.1 - 路径记录版]
        加载音频文件，并记录其原始路径。
        """
        filepath, _ = QFileDialog.getOpenFileName(self, "选择关联的音频文件", "", "音频文件 (*.wav *.mp3 *.flac)")
        if not filepath:
            return
        
        try:
            import librosa
            y, sr = librosa.load(filepath, sr=None, mono=True)
            
            self.config['audio_path'] = filepath # [核心修改] 现在 audio_path 存储的是原始绝对路径
            self.config['audio_data'] = (y, sr)
            
            self.audio_file_label.setText(os.path.basename(filepath))
            QMessageBox.information(self, "加载成功", f"音频文件已成功关联。")
            
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法加载或处理音频文件:\n{e}")
            self.config.pop('audio_path', None)
            self.config.pop('audio_data', None)
            self.audio_file_label.setText("加载失败")

    def _populate_from_config(self):
        """
        [v12.0 - 顽固Bug最终修复版]
        根据传入的配置字典，完整地填充对话框中所有UI控件的状态。
        """
        self.df = self.config.get('df'); self.tg = self.config.get('tg')
        if 'data_filename' in self.config: self.data_file_label.setText(self.config['data_filename'])
        if 'tg_filename' in self.config: self.tg_file_label.setText(self.config['tg_filename'])
        self.load_audio_btn.setEnabled(self.tg is not None)
        audio_path = self.config.get('audio_path')
        if audio_path and isinstance(audio_path, str): self.audio_file_label.setText(os.path.basename(audio_path))
        else: self.audio_file_label.setText("未选择 (可选)")
        self.color_scheme_combo.setCurrentText(self.config.get('color_scheme', '默认'))
        self.lock_check.setChecked(self.config.get('locked', False))
        
        tier_was_applied = False
        if self.tg:
            interval_tiers = [tier.name for tier in self.tg if isinstance(tier, textgrid.IntervalTier)]
            if interval_tiers:
                self.tg_tier_combo.addItems(interval_tiers); self.tg_tier_label.show(); self.tg_tier_combo.show()
                saved_tier = self.config.get('tg_tier')
                if saved_tier and saved_tier in interval_tiers: self.tg_tier_combo.setCurrentText(saved_tier)
                self._apply_selected_tier_to_df()
                tier_was_applied = True

        if not tier_was_applied:
             self._update_combos(preferred_group_col=self.config.get('group_col'))
        
        if self.config.get('f1_col'): self.f1_combo.setCurrentText(self.config.get('f1_col'))
        if self.config.get('f2_col'): self.f2_combo.setCurrentText(self.config.get('f2_col'))

    def _load_data(self):
        """
        [v12.0 - UI同步修复版]
        加载数据文件。加载成功后，会立即尝试自动匹配TextGrid，
        并在加载新数据时正确重置TextGrid相关的UI状态。
        """
        path, _ = QFileDialog.getOpenFileName(self, "选择数据文件", "", "表格文件 (*.xlsx *.xls *.csv)")
        if not path: return
        
        try:
            df = pd.read_excel(path) if path.lower().endswith(('.xlsx', '.xls')) else pd.read_csv(path)
            
            # 自动拆分逻辑 (如果CSV包含 'source_file' 列)
            if path.lower().endswith('.csv') and 'source_file' in df.columns:
                reply = QMessageBox.question(self, "检测到合并数据", "此CSV文件包含 'source_file' 列，是否自动拆分为多个图层？", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if reply == QMessageBox.Yes:
                    self._split_and_prepare_layers(df)
                    QMessageBox.information(self, "拆分成功", f"已成功准备 {len(self.split_layers)} 个新图层。")
                    self.accept()
                    return

            # 更新当前对话框的内部状态
            self.df = df
            base_filename = os.path.basename(path)
            layer_name_base = os.path.splitext(base_filename)[0]
            
            self.data_file_label.setText(base_filename)
            self.config['data_filename'] = base_filename
        
            if not self.name_edit.text():
                self.name_edit.setText(layer_name_base)
        
            # --- [核心修复] ---
            # 加载新数据文件时，彻底重置所有TextGrid相关的状态
            self.tg = None
            self.tg_file_label.setText("未选择 (可选)")
            self.config.pop('tg_filename', None)
            self.config.pop('original_tg_path', None)
            
            # 隐藏并清空Tier选择UI，确保界面状态同步
            self.tg_tier_label.hide()
            self.tg_tier_combo.hide()
            self.tg_tier_combo.clear()
            # --- [修复结束] ---
            
            self._update_combos()

            # 立即触发自动匹配
            temp_config_for_matching = {'name': self.name_edit.text(), 'df': self.df}
            self.parent_dialog._auto_match_textgrid_for_layer(temp_config_for_matching)
            
            # 将匹配结果（如果成功）更新回当前对话框的配置和UI
            if temp_config_for_matching.get('tg') is not None:
                self.config.update(temp_config_for_matching)
                self.tg = temp_config_for_matching['tg']
                self.tg_file_label.setText(temp_config_for_matching['tg_filename'])
                self.load_audio_btn.setEnabled(True)
                
                # 重新填充并应用Tier
                self.tg_tier_combo.clear()
                interval_tiers = [tier.name for tier in self.tg if isinstance(tier, textgrid.IntervalTier)]
                if interval_tiers:
                    self.tg_tier_combo.addItems(interval_tiers)
                    self.tg_tier_label.show()
                    self.tg_tier_combo.show()
                    self._apply_selected_tier_to_df()

        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法读取数据文件: {e}")
            self.df = None

    def _split_and_prepare_layers(self, merged_df):
        """
        [新增] 将一个合并的DataFrame按 'source_file' 列拆分成多个图层配置。
        """
        self.split_layers = [] # 清空旧的拆分结果
        unique_sources = merged_df['source_file'].unique()

        # 遍历每个唯一的来源文件
        for source_name in unique_sources:
            # 筛选出属于该来源的数据
            single_df = merged_df[merged_df['source_file'] == source_name].copy()
        
            # 为这个新图层构建一个完整的配置字典
            layer_config = {
                "name": source_name,
                "df": single_df,
                "data_filename": f"来自合并文件 ({source_name})",
                "enabled": True,
                "locked": False,
                # 自动检测F1/F2/分组列
                "f1_col": next((c for c in single_df.columns if 'f1' in c.lower()), ""),
                "f2_col": next((c for c in single_df.columns if 'f2' in c.lower()), ""),
                "group_col": next((c for c in single_df.columns if 'vowel' in c.lower() or 'label' in c.lower()), "无分组"),
                # 默认样式
                "point_size": 15, "point_alpha": 0.3, "marker": "圆点",
                "mean_enabled": False, "ellipse_enabled": False,
                "color_scheme": "默认", "groups": {}
            }
            # 如果自动检测失败，提供安全回退
            numeric_cols = single_df.select_dtypes(include=np.number).columns.tolist()
            if not layer_config['f1_col'] and len(numeric_cols) > 0: layer_config['f1_col'] = numeric_cols[0]
            if not layer_config['f2_col'] and len(numeric_cols) > 1: layer_config['f2_col'] = numeric_cols[1]

            self.split_layers.append(layer_config)

    def _load_textgrid(self, filepath=None):
        path_to_load = filepath
        if not path_to_load:
            if self.df is None or 'timestamp' not in self.df.columns:
                QMessageBox.warning(self, "需要时间戳", "请先加载一个包含 'timestamp' 列的数据文件。")
                return
            path_to_load, _ = QFileDialog.getOpenFileName(self, "选择 TextGrid 文件", "", "TextGrid 文件 (*.TextGrid)")

        if not path_to_load: return
        
        try:
            self.tg = textgrid.TextGrid.fromFile(path_to_load)
            filename = os.path.basename(path_to_load)
            self.tg_file_label.setText(filename)
            self.config['tg_filename'] = filename
            self.config['original_tg_path'] = path_to_load

            # --- [核心修改] ---
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
            # --- [修改结束] ---

            self.load_audio_btn.setEnabled(True)
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法解析 TextGrid 文件: {e}")
            self.tg = None; self.load_audio_btn.setEnabled(False)
            self.config.pop('original_tg_path', None)

    def _update_combos(self, preferred_group_col=None): # [核心修复] 增加可选参数
        """根据当前加载的 DataFrame 更新 F1, F2 和分组列的下拉选项。"""
        # --- [核心修改开始] ---
        # 1. 保存当前分组依据的值，防止被 clear() 清掉
        current_group_text = preferred_group_col if preferred_group_col else self.group_by_combo.currentText()
        # --- [核心修改结束] ---
        
        self.f1_combo.clear()
        self.f2_combo.clear()
        self.group_by_combo.clear()
        self.group_by_combo.addItem("无分组")

        if self.df is None: return

        numeric_cols = self.df.select_dtypes(include=np.number).columns.tolist()
        all_cols = self.df.columns.tolist()

        self.f1_combo.addItems(numeric_cols)
        self.f2_combo.addItems(numeric_cols)
        
        non_numeric_cols = [col for col in all_cols if col not in numeric_cols]
        if non_numeric_cols:
            self.group_by_combo.addItems(non_numeric_cols)
        
        if numeric_cols:
            if non_numeric_cols:
                self.group_by_combo.insertSeparator(self.group_by_combo.count())
            self.group_by_combo.addItems(numeric_cols)

        # 自动猜测F1/F2列
        f1 = next((c for c in numeric_cols if 'f1' in c.lower()), numeric_cols[0] if numeric_cols else "")
        f2 = next((c for c in numeric_cols if 'f2' in c.lower()), numeric_cols[1] if len(numeric_cols) > 1 else "")
        self.f1_combo.setCurrentText(f1)
        self.f2_combo.setCurrentText(f2)

        # --- [核心修改开始] ---
        # 2. 恢复或设置分组依据
        # 检查之前保存的值是否存在于新的列名列表中
        if current_group_text and current_group_text in all_cols:
            self.group_by_combo.setCurrentText(current_group_text)
        elif 'textgrid_label' in all_cols: # 兼容旧逻辑
            self.group_by_combo.setCurrentText('textgrid_label')
        else: 
            # 如果之前的选择不存在，再执行自动猜测作为后备方案
            default_group = next((c for c in all_cols if 'vowel' in c.lower() or 'label' in c.lower()), "无分组")
            self.group_by_combo.setCurrentText(default_group)
        # --- [核心修改结束] ---

    def _apply_selected_tier_to_df(self):
        """
        [核心重构] 将TextGrid中当前选中的层(Tier)的标注应用到DataFrame。
        会创建一个以层名称命名的动态列。
        """
        if self.df is None or self.tg is None: return

        selected_tier_name = self.tg_tier_combo.currentText()
        if not selected_tier_name: return

        # 1. [核心修复] 更精确地清理旧列
        for tier in self.tg:
            if isinstance(tier, textgrid.IntervalTier) and tier.name in self.df.columns:
                self.df = self.df.drop(columns=[tier.name])
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
                if 'timestamp' in self.df.columns:
                    mask = (self.df['timestamp'] >= interval.minTime) & (self.df['timestamp'] < interval.maxTime)
                    label_col.loc[mask] = interval.mark
                else:
                    break # 如果没有timestamp列，直接跳出循环
        
        self.df[new_column_name] = label_col
        
        # 4. 更新UI，自动将分组依据设置为新创建的列
        self._update_combos(preferred_group_col=new_column_name)

    def get_layer_config(self):
        """
        [v11.0 - 职责分离修复版]
        只负责从UI控件收集配置信息，并与已有的数据引用合并。
        - 明确移除了 'player' 键，确保它不会被传回主对话框的数据模型中。
        """
        if self.df is None:
            QMessageBox.warning(self, "输入无效", "请先加载数据文件。")
            return None
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "输入无效", "请输入图层名称。")
            return None
        
        # 1. 从UI控件收集所有可配置的项
        ui_config = {
            "name": name,
            "data_filename": self.data_file_label.text(),
            "tg_filename": self.tg_file_label.text(),
            "tg_tier": self.tg_tier_combo.currentText(), # [核心新增] 保存选择的Tier
            "f1_col": self.f1_combo.currentText(),
            "f2_col": self.f2_combo.currentText(),
            "group_col": self.group_by_combo.currentText(),
            "color_scheme": self.color_scheme_combo.currentText(),
            "locked": self.lock_check.isChecked(),
        }

        # 2. 将UI配置与已有的核心数据（不应被UI修改）合并
        # 我们从 self.config 开始，因为它包含了 audio_path 等信息
        final_config = self.config.copy()
        final_config.update(ui_config)
        
        # 确保核心数据对象的引用是最新的
        final_config['df'] = self.df
        final_config['tg'] = self.tg

        # [核心修复] 在返回前，明确移除 'player' 键。
        # player 对象只应该在播放时被即时创建。
        final_config.pop('player', None)
        
        return final_config

# ==============================================================================
# 核心UI类：绘图器对话框 (最终版)
# ==============================================================================
class PlotterDialog(QDialog):
    PLUGIN_LAYER_TYPE = "vowel_space" # [新增] 定义本插件能处理的图层类型
    MARKER_STYLES = {'圆点': 'o', '方形': 's', '三角形': '^', '菱形': 'D', '加号': '+', '叉号': 'x'}
    LINE_STYLES = {'实线': '-', '虚线': '--', '点线': ':', '点划线': '-.'}
    # 定义所有颜色方案
    COLOR_SCHEMES = {
        "默认": ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'],
        "色觉友好 (无障碍)": ['#332288', '#117733', '#44AA99', '#88CCEE', '#DDCC77', '#CC6677', '#AA4499', '#882255'],
        "经典亮色 (Set1)": ['#e41a1c', '#377eb8', '#4daf4a', '#984ea3', '#ff7f00', '#ffff33', '#a65628', '#f781bf', '#999999'],
        "柔和色盘 (Set3)": ['#8dd3c7', '#ffffb3', '#bebada', '#fb8072', '#80b1d3', '#fdb462', '#b3de69', '#fccde5', '#d9d9d9', '#bc80bd'],
        "复古风格": ['#588c7e', '#f2e394', '#f2ae72', '#d96459', '#8c4646', '#424254', '#336b87', '#90afc5'],
        "商务蓝调": ['#003f5c', '#374c80', '#7a5195', '#bc5090', '#ef5675', '#ff764a', '#ffa600'],
        "科学渐变 (Viridis)": ['#440154', '#482878', '#3e4989', '#31688e', '#26828e', '#1f9e89', '#35b779', '#6dcd59', '#b4de2c', '#fde725']
    }

    def __init__(self, parent=None, icon_manager=None):
        super().__init__(parent)
        # 检查依赖库是否可用
        if not LIBS_AVAILABLE:
            QMessageBox.critical(self, "依赖缺失", "需要 'matplotlib' 和 'textgrid' 库。\n请运行: pip install matplotlib textgrid")
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(0, self.reject)
            return

        self.setWindowTitle("元音空间图绘制器")
        self.resize(1300, 850)
        self.setMinimumSize(1100, 700)
        self.icon_manager = icon_manager
        self.project_temp_dir = None
        self.plugin_id = "com.phonacq.vowel_space_plotter"
        # --- [核心新增] 获取并存储项目的结果目录路径 ---
        self.project_results_dir = None
        if parent and hasattr(parent, 'config'):
            # 从主窗口的配置中获取 'results_dir'
            self.project_results_dir = parent.config.get('file_settings', {}).get('results_dir')
        
        # --- 核心数据结构 ---
        self.layers = []
        self.current_selected_layer_index = -1

        # --- [核心修复] 创建一个临时目录用于存放播放的音频片段 ---
        # 导入必要的模块
        from tempfile import mkdtemp
        # 创建一个以 'plotter_audio_' 为前缀的唯一临时目录，并将其路径存储在实例属性中
        self.temp_audio_dir = mkdtemp(prefix="plotter_audio_")
        
        # --- 交互功能的状态变量 ---
        self._is_panning = False
        self._pan_start_pos = None
        self.plotted_collections = []
        self.hover_annotation = None
        self.rect_selector = None # [新增] 用于存储矩形选择器实例
        self.show_ignore_mode_info = True # [新增] 用于控制提示框是否显示

        # --- 初始化UI和连接信号 ---
        self._init_ui()
        self._connect_signals()
        self._update_ui_state()

    def _get_or_create_analysis_dirs(self):
        """
        [新增] 一个健壮的辅助方法，用于获取或创建标准的分析子目录。

        Returns:
            tuple: (charts_dir, textgrids_dir) 路径元组。如果无法确定
                   项目结果目录，则返回 (None, None)。
        """
        if not self.project_results_dir or not os.path.isdir(self.project_results_dir):
            # 如果主结果目录无效，则无法继续
            return None, None

        try:
            # 基础的 'analyze' 目录
            analyze_base_dir = os.path.join(self.project_results_dir, 'analyze')
            
            # charts 和 textgrids 子目录
            charts_dir = os.path.join(analyze_base_dir, 'charts')
            textgrids_dir = os.path.join(analyze_base_dir, 'textgrids')

            # 使用 os.makedirs 并设置 exist_ok=True，可以安全地创建目录，
            # 如果目录已存在，则不会抛出错误。
            os.makedirs(charts_dir, exist_ok=True)
            os.makedirs(textgrids_dir, exist_ok=True)
            
            return charts_dir, textgrids_dir
        except Exception as e:
            print(f"[Vowel Plotter ERROR] Failed to create analysis directories: {e}")
            return None, None

    def _init_ui(self):
        """初始化主界面的布局和控件。"""
        main_layout = QHBoxLayout(self)
        self.left_panel = self._create_left_panel() # 左侧面板：图层管理器
        
        # 中心区域使用 QSplitter 分割画布和数据预览表格
        center_splitter = QSplitter(Qt.Vertical)
        
        # Matplotlib 画布
        self.figure = Figure()
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setContextMenuPolicy(Qt.CustomContextMenu) # 启用右键菜单
        self.canvas.setToolTip("图表区域。\n- 左键拖动可平移视图\n- 右键可打开菜单\n- Ctrl+滚轮可缩放")
        
        # 数据预览表格
        self.table_view = QTableView()
        self.table_view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch) # 列宽自适应
        self.table_view.setToolTip("当前选中图层的数据预览。")
        
        center_splitter.addWidget(self.canvas)
        center_splitter.addWidget(self.table_view)
        center_splitter.setSizes([600, 200]) # 设置画布和表格的初始高度比例
        
        self.right_panel = self._create_right_panel() # 右侧面板：设置选项
        
        # 将三个主面板添加到主布局
        main_layout.addWidget(self.left_panel)
        main_layout.addWidget(center_splitter, 1) # 中心区域可伸缩
        main_layout.addWidget(self.right_panel)

    def _create_left_panel(self):
        """
        [v9.0 - 上下文操作重构版]
        创建左侧面板。
        - 移除了主界面上的音频控制面板，UI更加简洁。
        - 播放功能现已整合到分组列表的右键菜单和快捷键中。
        """
        panel = QWidget()
        panel.setFixedWidth(350)
        layout = QVBoxLayout(panel)
        
        combined_group = QGroupBox("图层与分组")
        combined_layout = QVBoxLayout(combined_group)

        splitter = QSplitter(Qt.Vertical)

        # --- 1. 上方图层列表容器 (保持不变) ---
        layer_container = QWidget()
        layer_container_layout = QVBoxLayout(layer_container)
        layer_container_layout.setContentsMargins(0, 0, 0, 0)
        self.layer_table = QTableWidget()
        # ... (layer_table 设置保持不变) ...
        self.layer_table.setColumnCount(2); self.layer_table.horizontalHeader().setVisible(False); self.layer_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch); self.layer_table.setColumnWidth(1, 40); self.layer_table.setSelectionBehavior(QTableWidget.SelectRows); self.layer_table.setSelectionMode(QTableWidget.ExtendedSelection); self.layer_table.verticalHeader().setVisible(False); self.layer_table.setToolTip("右键单击进行操作，双击可配置图层。"); self.layer_table.setContextMenuPolicy(Qt.CustomContextMenu)
        btn_layout = QHBoxLayout()
        self.add_layer_btn = QPushButton(" 添加新图层...")
        if self.icon_manager: self.add_layer_btn.setIcon(self.icon_manager.get_icon("add_row"))
        btn_layout.addWidget(self.add_layer_btn); btn_layout.addStretch()
        layer_container_layout.addWidget(self.layer_table); layer_container_layout.addLayout(btn_layout)

        # --- 2. 下方分组列表及其容器 ---
        group_container = QWidget()
        group_container_layout = QVBoxLayout(group_container)
        group_container_layout.setContentsMargins(0,0,0,0)
        
        self.group_table = QTableWidget()
        self.group_table.setColumnCount(2)
        self.group_table.horizontalHeader().setVisible(False)
        self.group_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.group_table.setColumnWidth(1, 50)
        self.group_table.verticalHeader().setVisible(False)
        # [核心修改] 默认恢复为多选模式
        self.group_table.setSelectionMode(QTableWidget.ExtendedSelection) 
        self.group_table.setToolTip("使用Ctrl/Shift进行多选，然后右键单击进行批量操作。")
        self.group_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.group_table.installEventFilter(self)

        # [核心修改] 重新引入交互模式切换复选框
        self.auto_emphasize_check = QCheckBox("选择即强调 (单选模式)")
        self.auto_emphasize_check.setToolTip(
            "勾选此项:\n"
            "- 启用“选择即强调”模式。\n"
            "- 列表将切换为单选模式以获得最佳性能。\n\n"
            "取消勾选:\n"
            "- 禁用自动强调。\n"
            "- 列表将切换回多选模式，以便进行批量操作。"
        )
        self.auto_emphasize_check.setChecked(True) # 默认启用单选模式

        group_container_layout.addWidget(self.group_table)
        group_container_layout.addWidget(self.auto_emphasize_check)
        
        splitter.addWidget(layer_container)
        splitter.addWidget(group_container)
        splitter.setSizes([400, 250])
        combined_layout.addWidget(splitter)
        layout.addWidget(combined_group, 1)

        # --- 绘图操作组框 (保持不变) ---
        action_group = QGroupBox("绘图操作")
        action_layout = QVBoxLayout(action_group)
        self.plot_button = QPushButton(" 更新并固定数轴范围")
        self.plot_button.setToolTip("根据当前所有数据点，重新计算并锁定X轴和Y轴的显示范围。")
        if self.icon_manager: self.plot_button.setIcon(self.icon_manager.get_icon("anchor")) 
        action_layout.addWidget(self.plot_button)
        layout.addWidget(action_group)
        
        return panel

    def _create_right_panel(self):
        """
        [v6.0 - UI美化与滑块版]
        创建右侧面板，包含全局设置和上下文敏感的图层设置。
        
        此方法构建了一个高度可定制的UI，其特点包括：
        - 使用QScrollArea确保所有设置在小屏幕上也能访问。
        - 将全局设置细分为“坐标轴与标题”和“强调样式”两个子组，结构清晰。
        - 将所有参数输入框替换为QSlider，提升交互体验和美观度。
        - 使用精细的布局技巧（QHBoxLayout + addStretch）实现滑块与标签的完美居中对齐。
        - 为“轮廓”增加了独立的线宽控制滑块。
        """
        # --- 1. 顶层容器：QScrollArea ---
        # 确保面板内容在窗口尺寸变小时可以滚动，而不是被截断。
        scroll = QScrollArea()
        scroll.setWidgetResizable(True) # 关键：让内部控件自动填充宽度
        scroll.setFixedWidth(420)       # 固定右侧面板的总宽度
        scroll.setFrameShape(QScrollArea.NoFrame) # 移除边框，使其与主窗口融为一体

        # --- 2. 滚动区内的主要容器 ---
        panel = QWidget()
        layout = QVBoxLayout(panel)
        scroll.setWidget(panel)

        # ======================================================================
        # 3. 全局设置组框
        # ======================================================================
        global_group = QGroupBox("全局设置")
        global_layout = QVBoxLayout(global_group) # 使用垂直布局容纳多个子组

        # --- 3.1 子组：坐标轴与标题 ---
        axis_group = QGroupBox("坐标轴与标题")
        axis_layout = QFormLayout(axis_group)
        
        self.title_edit = QLineEdit("元音空间图")
        self.xlabel_edit = QLineEdit("F2 (Hz)")
        self.ylabel_edit = QLineEdit("F1 (Hz)")
        self.title_edit.setToolTip("设置图表的总标题。")
        self.xlabel_edit.setToolTip("设置图表 X 轴的标签文本。")
        self.ylabel_edit.setToolTip("设置图表 Y 轴的标签文本。")
        axis_layout.addRow("图表标题:", self.title_edit)
        axis_layout.addRow("X轴标签:", self.xlabel_edit)
        axis_layout.addRow("Y轴标签:", self.ylabel_edit)
        
        self.flip_x_check = QCheckBox("翻转 X 轴 (F2)")
        self.flip_x_check.setChecked(True)
        self.flip_x_check.setToolTip("勾选后，X 轴数值将从右向左递增，符合语音学惯例。")
        
        self.flip_y_check = QCheckBox("翻转 Y 轴 (F1)")
        self.flip_y_check.setChecked(True)
        self.flip_y_check.setToolTip("勾选后，Y 轴数值将从下向上递增，符合语音学惯例。")
        
        self.x_min_edit, self.x_max_edit = QLineEdit(), QLineEdit()
        self.x_min_edit.setPlaceholderText("自动"); self.x_max_edit.setPlaceholderText("自动")
        x_range_layout = QHBoxLayout()
        x_range_layout.addWidget(self.x_min_edit); x_range_layout.addWidget(QLabel("到")); x_range_layout.addWidget(self.x_max_edit)
        
        self.y_min_edit, self.y_max_edit = QLineEdit(), QLineEdit()
        self.y_min_edit.setPlaceholderText("自动"); self.y_max_edit.setPlaceholderText("自动")
        y_range_layout = QHBoxLayout()
        y_range_layout.addWidget(self.y_min_edit); y_range_layout.addWidget(QLabel("到")); y_range_layout.addWidget(self.y_max_edit)
        
        axis_layout.addRow(self.flip_x_check)
        axis_layout.addRow(self.flip_y_check)
        axis_layout.addRow("X轴范围:", x_range_layout)
        axis_layout.addRow("Y轴范围:", y_range_layout)
        
        self.show_legend_check = QCheckBox("显示图例")
        self.show_legend_check.setChecked(True)
        axis_layout.addRow(self.show_legend_check)
        # --- [核心新增] ---
        self.show_hover_info_check = QCheckBox("显示悬浮信息")
        self.show_hover_info_check.setChecked(True)
        self.show_hover_info_check.setToolTip("勾选后，当鼠标悬停在数据点上时，会在右上角显示该点的详细信息。")
        axis_layout.addRow(self.show_hover_info_check)
        # --- 新增结束 ---
        # --- [核心修改] 新增子组：共振峰归一化 ---
        norm_group = QGroupBox("共振峰归一化 (Normalization)")
        norm_layout = QFormLayout(norm_group)
        norm_group.setToolTip("选择一种算法来消除不同说话人之间的生理差异，\n使得元音空间更具可比性。")
        
        self.norm_combo = QComboBox()
        self.norm_combo.addItems(["原始值 (Hz)", "Z-Score (按图层)"])
        self.norm_combo.setToolTip(
            "选择归一化方法：\n"
            "- 原始值: 不做任何处理。\n"
            "- Z-Score (按图层): 对每个图层(说话人)的F1/F2数据\n"
            "  独立进行标准化，消除个体音高差异。"
        )
        norm_layout.addRow("归一化方法:", self.norm_combo)
        # --- 结束修改 ---
        
        # --- 3.2 子组：强调样式 ---
        emphasis_group = QGroupBox("强调样式")
        emphasis_layout = QFormLayout(emphasis_group)
        emphasis_group.setToolTip("配置当一个分组被“强调”时，其视觉样式的变化。")

        # -- 强调效果1: 放大 --
        self.emphasis_magnify_check = QCheckBox("放大")
        self.emphasis_magnify_check.setChecked(True)
        
        # 创建一个专用的容器和布局来实现滑块和标签的居中对齐
        magnify_widget = QWidget()
        magnify_layout = QHBoxLayout(magnify_widget)
        magnify_layout.setContentsMargins(0, 0, 0, 0)
        self.emphasis_magnify_slider = QSlider(Qt.Horizontal)
        self.emphasis_magnify_slider.setRange(100, 500) # 放大范围：100% (不放大) 到 500%
        self.emphasis_magnify_slider.setValue(150)       # 默认放大到150%
        self.emphasis_magnify_label = QLabel(f"{self.emphasis_magnify_slider.value()}%")
        self.emphasis_magnify_label.setMinimumWidth(40) # 设置最小宽度以防止数值变化时布局跳动
        
        magnify_layout.addStretch() # 左侧弹簧
        magnify_layout.addWidget(self.emphasis_magnify_slider)
        magnify_layout.addWidget(self.emphasis_magnify_label)
        magnify_layout.addStretch() # 右侧弹簧
        
        emphasis_layout.addRow(self.emphasis_magnify_check, magnify_widget)

        # -- 强调效果2: 轮廓 --
        self.emphasis_outline_check = QCheckBox("轮廓")
        self.emphasis_outline_check.setChecked(True)
        
        # 为轮廓增加独立的线宽控制
        outline_widget = QWidget()
        outline_layout = QHBoxLayout(outline_widget)
        outline_layout.setContentsMargins(0, 0, 0, 0)
        self.emphasis_outline_width_slider = QSlider(Qt.Horizontal)
        self.emphasis_outline_width_slider.setRange(5, 50) # 实际值: 0.5px to 5.0px (乘以10)
        self.emphasis_outline_width_slider.setValue(15)    # 默认1.5px
        self.emphasis_outline_width_label = QLabel(f"{self.emphasis_outline_width_slider.value()/10.0:.1f}px")
        self.emphasis_outline_width_label.setMinimumWidth(40)
        
        outline_layout.addStretch()
        outline_layout.addWidget(QLabel("线宽:"))
        outline_layout.addWidget(self.emphasis_outline_width_slider)
        outline_layout.addWidget(self.emphasis_outline_width_label)
        outline_layout.addStretch()
        
        emphasis_layout.addRow(self.emphasis_outline_check, outline_widget)

        # -- 强调效果3: 透明度 --
        self.emphasis_opacity_check = QCheckBox("透明度")
        self.emphasis_opacity_check.setChecked(True)
        
        opacity_widget = QWidget()
        opacity_layout = QHBoxLayout(opacity_widget)
        opacity_layout.setContentsMargins(0, 0, 0, 0)
        self.emphasis_opacity_slider = QSlider(Qt.Horizontal)
        self.emphasis_opacity_slider.setRange(-100, 100) # 范围：-100% (完全透明) 到 +100% (完全不透明)
        self.emphasis_opacity_slider.setValue(50)        # 默认增加50%
        self.emphasis_opacity_label = QLabel(f"+{self.emphasis_opacity_slider.value()}%")
        self.emphasis_opacity_label.setMinimumWidth(40)
        
        opacity_layout.addStretch()
        opacity_layout.addWidget(self.emphasis_opacity_slider)
        opacity_layout.addWidget(self.emphasis_opacity_label)
        opacity_layout.addStretch()
        
        emphasis_layout.addRow(self.emphasis_opacity_check, opacity_widget)

        # --- 3.3 将所有子组添加到全局设置组的布局中 ---
        global_layout.addWidget(axis_group)
        global_layout.addWidget(norm_group) # [核心修改] 添加新的组框
        global_layout.addWidget(emphasis_group)

        # ======================================================================
        # 4. 图层设置组框 (上下文敏感)
        # ======================================================================
        self.layer_settings_group = QGroupBox("图层设置 (未选择图层)")
        self.layer_settings_group.setEnabled(False) # 默认禁用
        layer_settings_layout = QVBoxLayout(self.layer_settings_group)
        
        # 数据点样式
        points_group = QGroupBox("数据点样式")
        points_layout = QFormLayout(points_group)
        self.point_size_slider = QSlider(Qt.Horizontal)
        self.point_size_slider.setRange(5, 100)
        self.point_size_slider.setValue(15)
        self.point_size_slider.setToolTip("调整当前图层数据点的大小。")
        self.point_alpha_slider = QSlider(Qt.Horizontal)
        self.point_alpha_slider.setRange(10, 100)
        self.point_alpha_slider.setValue(30)
        self.point_alpha_slider.setToolTip("调整当前图层数据点的不透明度，值越小越透明。")
        points_layout.addRow("大小:", self.point_size_slider)
        points_layout.addRow("不透明度:", self.point_alpha_slider)
        
        # 图层样式 (标记区分)
        layer_style_group = QGroupBox("图层样式 (标记区分)")
        layer_style_layout = QFormLayout(layer_style_group)
        self.layer_marker_combo = QComboBox()
        self.layer_marker_combo.addItems(self.MARKER_STYLES.keys())
        self.layer_marker_combo.setToolTip("选择当前图层数据点的标记形状。")
        layer_style_layout.addRow("标记样式:", self.layer_marker_combo)
        
        # 分组样式 (颜色区分) - 针对当前图层
        # [核心修正] 声明为实例属性
        self.grouping_group = QGroupBox("分组样式 (颜色区分)")
        self.grouping_group.setToolTip("为当前图层中的分组（例如：元音）设置颜色和显示状态。")
        grouping_layout = QVBoxLayout(self.grouping_group)
        
        # 颜色方案选择
        color_scheme_layout = QHBoxLayout()
        self.color_scheme_combo_layer = QComboBox()
        self.color_scheme_combo_layer.addItems(self.COLOR_SCHEMES.keys())
        self.color_scheme_combo_layer.setToolTip("为当前图层内的分组选择一个预设的颜色方案。")
        self.apply_color_scheme_btn_layer = QPushButton("应用")
        self.apply_color_scheme_btn_layer.setToolTip("将选择的颜色方案应用到当前图层的各个分组。")
        color_scheme_layout.addWidget(self.color_scheme_combo_layer)
        color_scheme_layout.addWidget(self.apply_color_scheme_btn_layer)
        grouping_layout.addLayout(color_scheme_layout)

        # 动态生成的分组颜色和复选框
        self.group_settings_scroll = QScrollArea()
        self.group_settings_scroll.setWidgetResizable(True)
        self.group_settings_scroll.setMinimumHeight(200)
        self.group_settings_scroll.setFrameShape(QScrollArea.NoFrame)
        self.group_settings_widget = QWidget()
        self.group_settings_layout = QVBoxLayout(self.group_settings_widget)
        self.group_settings_scroll.setWidget(self.group_settings_widget)
        grouping_layout.addWidget(self.group_settings_scroll)
        
        # 平均值点设置
        # [核心修正] 声明为实例属性
        self.mean_group = QGroupBox("平均值点")
        self.mean_group.setCheckable(True)
        self.mean_group.setChecked(False)
        self.mean_group.setToolTip("勾选后，将为当前图层每个分组绘制一个代表其平均F1/F2值的点。")
        mean_layout = QFormLayout(self.mean_group)
        self.mean_marker_combo = QComboBox()
        self.mean_marker_combo.addItems(self.MARKER_STYLES.keys())
        self.mean_marker_combo.setCurrentText("加号")
        self.mean_size_slider = QSlider(Qt.Horizontal)
        self.mean_size_slider.setRange(20, 300)
        self.mean_size_slider.setValue(100)
        mean_layout.addRow("标记样式:", self.mean_marker_combo)
        mean_layout.addRow("标记大小:", self.mean_size_slider)
        
        # 标准差椭圆设置
        # [核心修正] 声明为实例属性
        self.ellipse_group = QGroupBox("标准差椭圆")
        self.ellipse_group.setCheckable(True)
        self.ellipse_group.setChecked(False)
        self.ellipse_group.setToolTip("勾选后，将为当前图层每个分组绘制一个标准差椭圆，表示数据点的分布范围。")
        ellipse_layout = QFormLayout(self.ellipse_group)
        self.ellipse_std_combo = QComboBox()
        self.ellipse_std_combo.addItems(["1 (68%)", "1.5 (86%)", "2 (95%)"])
        self.ellipse_std_combo.setCurrentText("2 (95%)")
        self.ellipse_style_combo = QComboBox()
        self.ellipse_style_combo.addItems(self.LINE_STYLES.keys())
        self.ellipse_width_slider = QSlider(Qt.Horizontal)
        self.ellipse_width_slider.setRange(1, 10)
        self.ellipse_width_slider.setValue(2)
        ellipse_layout.addRow("标准差倍数:", self.ellipse_std_combo)
        ellipse_layout.addRow("线条样式:", self.ellipse_style_combo)
        ellipse_layout.addRow("线条宽度:", self.ellipse_width_slider)

        # 将所有图层设置组框添加到图层设置主布局
        layer_settings_layout.addWidget(points_group)
        layer_settings_layout.addWidget(layer_style_group)
        layer_settings_layout.addWidget(self.grouping_group) # [核心修正] 使用实例属性
        layer_settings_layout.addWidget(self.mean_group)      # [核心修正] 使用实例属性
        layer_settings_layout.addWidget(self.ellipse_group)   # [核心修正] 使用实例属性
        
        # 将全局设置和图层设置添加到右侧面板布局
        layout.addWidget(global_group)
        layout.addWidget(self.layer_settings_group)
        layout.addStretch() # 底部留白
        
        return scroll

    def _save_project_to_file(self, plugin_id, target_filepath):
        """
        [内置完整版] 将当前对话框的状态保存为 .pavp 工程文件。
        """
        import tempfile, shutil, os, json, uuid, pandas as pd
        from datetime import datetime

        with tempfile.TemporaryDirectory() as temp_dir:
            # 1. 在临时目录中创建子文件夹
            data_dir = os.path.join(temp_dir, 'data')
            textgrids_dir = os.path.join(temp_dir, 'textgrids')
            audio_dir = os.path.join(temp_dir, 'audio')
            os.makedirs(data_dir); os.makedirs(textgrids_dir); os.makedirs(audio_dir)

            json_layers = []
            for layer_config in self.layers:
                layer_id = layer_config.get('id', str(uuid.uuid4()))
                layer_config['id'] = layer_id

                # a. 写入DataFrame
                df = layer_config.get('df')
                data_source_path = None
                if df is not None and not df.empty:
                    csv_filename = f"{layer_id}.csv"
                    df.to_csv(os.path.join(data_dir, csv_filename), index=False)
                    data_source_path = f"data/{csv_filename}"

                # b. [完整版] 复制外部文件
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

                # c. 构建JSON图层对象
                json_layer = {
                    "id": layer_id, "name": layer_config['name'],
                    "type": self.PLUGIN_LAYER_TYPE,
                    "data_source_path": data_source_path,
                    "textgrid_path": tg_path_relative,
                    "audio_path": audio_path_relative,
                    "config": {
                        "enabled": layer_config.get('enabled', True),
                        "locked": layer_config.get('locked', False),
                        "group_col": layer_config.get('group_col')
                    },
                    "plugin_specific_config": {
                        plugin_id: self.get_plugin_specific_layer_config(layer_config)
                    }
                }
                json_layers.append(json_layer)

            # d. 构建完整的 project.json
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
            
            # e. 打包为ZIP并重命名
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

    def _connect_signals(self):
        """
        [v9.1 - 修复版]
        连接所有UI控件的信号到槽函数。
        - 移除了对已迁移到 LayerConfigDialog 或已被移除的
          self.load_audio_btn 和 self.play_segment_btn 的无效引用。
        """
        # --- 左侧面板 ---
        self.add_layer_btn.clicked.connect(self._add_layer)
        self.layer_table.customContextMenuRequested.connect(self._show_layer_context_menu)
        self.layer_table.itemDoubleClicked.connect(self._on_layer_double_clicked)
        self.layer_table.itemSelectionChanged.connect(self._on_layer_selection_changed)
        
        self.plot_button.clicked.connect(self._update_and_lock_axes)

        self.group_table.customContextMenuRequested.connect(self._show_group_context_menu)
        
        # [核心修改] 恢复交互模式切换的信号连接
        self.auto_emphasize_check.toggled.connect(self._on_auto_emphasize_toggled)
        # 初始化一次，以设置初始的选择模式和信号连接
        self._on_auto_emphasize_toggled(self.auto_emphasize_check.isChecked())

        # --- 右侧面板 ---
        # -- 全局设置 --
        self.title_edit.textChanged.connect(self._plot_data)
        self.xlabel_edit.textChanged.connect(self._plot_data)
        self.ylabel_edit.textChanged.connect(self._plot_data)
        self.flip_x_check.stateChanged.connect(self._plot_data)
        self.flip_y_check.stateChanged.connect(self._plot_data)
        self.x_min_edit.textChanged.connect(self._plot_data)
        self.x_max_edit.textChanged.connect(self._plot_data)
        self.y_min_edit.textChanged.connect(self._plot_data)
        self.y_max_edit.textChanged.connect(self._plot_data)
        self.show_legend_check.stateChanged.connect(self._plot_data)
        self.show_hover_info_check.stateChanged.connect(self._plot_data)
        self.norm_combo.currentTextChanged.connect(self._plot_data)
        
        # -- 强调样式设置 --
        self.emphasis_magnify_check.stateChanged.connect(self._plot_data)
        self.emphasis_magnify_slider.valueChanged.connect(self._on_emphasis_slider_changed)
        self.emphasis_outline_check.stateChanged.connect(self._plot_data)
        self.emphasis_outline_width_slider.valueChanged.connect(self._on_emphasis_slider_changed)
        self.emphasis_opacity_check.stateChanged.connect(self._plot_data)
        self.emphasis_opacity_slider.valueChanged.connect(self._on_emphasis_slider_changed)

        # -- 图层设置 --
        self.point_size_slider.valueChanged.connect(self._on_current_layer_setting_changed)
        self.point_alpha_slider.valueChanged.connect(self._on_current_layer_setting_changed)
        self.layer_marker_combo.currentTextChanged.connect(self._on_current_layer_setting_changed)
        self.mean_group.toggled.connect(self._on_current_layer_setting_changed)
        self.mean_marker_combo.currentTextChanged.connect(self._on_current_layer_setting_changed)
        self.mean_size_slider.valueChanged.connect(self._on_current_layer_setting_changed)
        self.ellipse_group.toggled.connect(self._on_current_layer_setting_changed)
        self.ellipse_std_combo.currentTextChanged.connect(self._on_current_layer_setting_changed)
        self.ellipse_style_combo.currentTextChanged.connect(self._on_current_layer_setting_changed)
        self.ellipse_width_slider.valueChanged.connect(self._on_current_layer_setting_changed)
        self.apply_color_scheme_btn_layer.clicked.connect(self._apply_color_scheme_to_current_layer)
        
        # --- 画布交互 ---
        self.canvas.customContextMenuRequested.connect(self._show_context_menu)
        self.canvas.mpl_connect('button_press_event', self._on_mouse_press)
        self.canvas.mpl_connect('button_release_event', self._on_mouse_release)
        self.canvas.mpl_connect('motion_notify_event', self._on_mouse_move)

    def _on_auto_emphasize_toggled(self, checked):
        """
        [新增] 当“选择即强调”模式切换时调用。
        """
        if checked:
            # 启用“选择即强调”模式 -> 单选
            self.group_table.setSelectionMode(QTableWidget.SingleSelection)
            try:
                self.group_table.itemSelectionChanged.disconnect(self._on_group_selection_changed)
            except TypeError: pass
            self.group_table.itemSelectionChanged.connect(self._on_group_selection_changed)
            self._on_group_selection_changed() # 立即触发一次以同步状态

        else:
            # 禁用“选择即强调”模式 -> 多选
            self.group_table.setSelectionMode(QTableWidget.ExtendedSelection)
            try:
                self.group_table.itemSelectionChanged.disconnect(self._on_group_selection_changed)
            except TypeError: pass

    def eventFilter(self, source, event):
        """
        [新增] 事件过滤器，用于捕获分组列表上的键盘事件。
        """
        # 确保事件源是我们关心的 group_table
        if source is self.group_table:
            # 检查事件类型是否为“按键按下”
            if event.type() == QEvent.KeyPress:
                # 检查按下的键是否为回车键
                if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
                    # 如果是，则调用播放选中片段的逻辑
                    self._play_selected_segment()
                    # 返回 True 表示我们已经处理了这个事件，它不应再被传递
                    return True
        
        # 对于所有其他事件，调用父类的默认实现
        return super().eventFilter(source, event)

    def _update_and_lock_axes(self):
        """
        [v1.1 - 翻转轴修复版]
        1. 强制重绘图表，以确保获取到最新的、基于所有数据的数轴范围。
        2. 获取当前自动计算出的X轴和Y轴范围。
        3. [核心修复] 对获取到的范围进行标准化处理，确保其始终是 (min, max) 的顺序。
        4. 将标准化后的范围值填入右侧面板的 QLineEdit 中，实现“锁定”。
        """
        # 步骤 1: 强制重绘图表 (逻辑保持不变)
        self.x_min_edit.clear()
        self.x_max_edit.clear()
        self.y_min_edit.clear()
        self.y_max_edit.clear()
        self._plot_data()

        if not self.figure.get_axes():
            QMessageBox.information(self, "无数据", "图表中没有可用于计算范围的数据。")
            return
            
        ax = self.figure.gca()
        
        # 步骤 2: 获取可能被翻转的数轴范围
        raw_xlim = ax.get_xlim()
        raw_ylim = ax.get_ylim()

        # --- [核心修复] ---
        # 步骤 3: 标准化范围，确保我们总是处理 (min, max) 的顺序
        # 无论 get_xlim() 返回的是 (500, 3000) 还是 (3000, 500)，
        # min() 和 max() 都能保证我们得到正确的最小值和最大值。
        final_xlim_min = min(raw_xlim)
        final_xlim_max = max(raw_xlim)
        
        final_ylim_min = min(raw_ylim)
        final_ylim_max = max(raw_ylim)
        # --- 修复结束 ---

        # 步骤 4: 将标准化后的范围值填入输入框
        # 这样，输入框中的值永远是 “小值 到 大值” 的逻辑顺序。
        self.x_min_edit.setText(f"{final_xlim_min:.2f}")
        self.x_max_edit.setText(f"{final_xlim_max:.2f}")
        self.y_min_edit.setText(f"{final_ylim_min:.2f}")
        self.y_max_edit.setText(f"{final_ylim_max:.2f}")
        
        # 后续的自动重绘逻辑将正确工作：
        # _plot_data 会读取 (min, max) -> 调用 set_xlim(min, max) -> 然后根据复选框状态决定是否再次翻转。
        # 这个流程现在是逻辑自洽的。

    def _on_emphasis_slider_changed(self, value):
        """
        [新增] 当任何一个强调参数滑块的值改变时调用。
        """
        # 更新标签显示
        self.emphasis_magnify_label.setText(f"{self.emphasis_magnify_slider.value()}%")
        self.emphasis_outline_width_label.setText(f"{self.emphasis_outline_width_slider.value()/10.0:.1f}px")
        opacity_val = self.emphasis_opacity_slider.value()
        self.emphasis_opacity_label.setText(f"{opacity_val:+}%") # 使用 '+' 标志来显示正负号

        # 触发重绘
        self._plot_data()

    def _on_auto_emphasize_toggled(self, checked):
        """
        [新增] 当“选择即强调”模式切换时调用。
        """
        if checked:
            # 1. 启用“选择即强调”模式
            # 将表格切换为单选模式
            self.group_table.setSelectionMode(QTableWidget.SingleSelection)
            # 连接选择变化信号到处理器
            try:
                self.group_table.itemSelectionChanged.disconnect(self._on_group_selection_changed)
            except TypeError:
                pass # 如果之前未连接，会抛出TypeError，直接忽略
            self.group_table.itemSelectionChanged.connect(self._on_group_selection_changed)
            # 立即触发一次，以同步当前单选项的强调状态
            self._on_group_selection_changed()

        else:
            # 2. 禁用“选择即强调”模式
            # 将表格切换回多选模式
            self.group_table.setSelectionMode(QTableWidget.ExtendedSelection)
            # 断开选择变化信号的连接，防止在多选时触发
            try:
                self.group_table.itemSelectionChanged.disconnect(self._on_group_selection_changed)
            except TypeError:
                pass

    def wheelEvent(self, event):
        """
        [v2.3 - plotter简化版] 处理鼠标滚轮事件，实现以鼠标为中心的双轴缩放。
        - Ctrl + 滚轮: 以鼠标指针为中心，同时缩放 X 和 Y 轴。
        - Shift + 滚轮: 无特殊功能。
        """
        # [核心修改] 只检查 Ctrl 键
        modifiers = event.modifiers()
        is_ctrl_pressed = modifiers & Qt.ControlModifier

        if self.canvas.underMouse() and is_ctrl_pressed:
            try:
                ax = self.figure.gca()
                
                # 坐标系转换逻辑保持不变，确保缩放中心正确
                global_pos = event.globalPos()
                canvas_local_pos = self.canvas.mapFromGlobal(global_pos)
                x_pixel = canvas_local_pos.x()
                y_pixel = self.canvas.height() - canvas_local_pos.y()
                
                trans = ax.transData.inverted()
                mouse_x, mouse_y = trans.transform_point((x_pixel, y_pixel))

                zoom_factor = 1.1 if event.angleDelta().y() > 0 else 1 / 1.1

                # [核心修改] 移除 if/elif 分支，同时计算并应用两个轴的缩放
                
                # --- X轴缩放计算 ---
                cur_xlim = ax.get_xlim()
                left_dist_x = mouse_x - cur_xlim[0]
                right_dist_x = cur_xlim[1] - mouse_x
                new_xlim = [
                    mouse_x - left_dist_x / zoom_factor,
                    mouse_x + right_dist_x / zoom_factor
                ]
                
                # --- Y轴缩放计算 ---
                cur_ylim = ax.get_ylim()
                bottom_dist_y = mouse_y - cur_ylim[0]
                top_dist_y = cur_ylim[1] - mouse_y
                new_ylim = [
                    mouse_y - bottom_dist_y / zoom_factor,
                    mouse_y + top_dist_y / zoom_factor
                ]

                # --- 同时应用两个轴的新范围 ---
                ax.set_xlim(new_xlim)
                ax.set_ylim(new_ylim)
                
                self.canvas.draw_idle()

            except Exception as e:
                if 'nan' not in str(e).lower():
                    print(f"Centered zoom failed: {e}")
        else:
            # 如果没有按下Ctrl键，则执行默认的滚轮行为
            super().wheelEvent(event)

    # ==========================================================================
    # 图层管理相关方法 (左侧面板)
    # ==========================================================================
    def _add_layer(self):
        """
        [v10.1 - 自动匹配TextGrid版]
        打开 LayerConfigDialog 添加新图层。
        成功添加后，会立即尝试为新图层自动查找并关联同名的TextGrid文件。
        """
        dialog = LayerConfigDialog(parent=self)
        if dialog.exec_() != QDialog.Accepted:
            return

        result = dialog.get_layer_config()
        if not result:
            return

        layers_to_add = []
        if isinstance(result, list):
            layers_to_add = result
        elif isinstance(result, dict):
            layers_to_add.append(result)
        
        for config in layers_to_add:


            # --- 后续的图层名称唯一性检查等逻辑保持不变 ---
            if 'marker' not in config:
                used_markers = {l.get('marker') for l in self.layers if 'marker' in l}
                available = [m for m in self.MARKER_STYLES.keys() if m not in used_markers]
                config['marker'] = cycle(available or self.MARKER_STYLES.keys()).__next__()
            if 'groups' not in config:
                config['groups'] = {}
            base_name = config['name']
            final_name = base_name
            counter = 1
            existing_names = {l['name'] for l in self.layers}
            while final_name in existing_names:
                final_name = f"{base_name} ({counter})"
                counter += 1
            config['name'] = final_name
            self.layers.append(config)
        
        self._update_layer_table()
        self._update_ui_state()
        self._plot_data()

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
        from tempfile import mkdtemp
        self.temp_audio_dir = mkdtemp(prefix="plotter_audio_")
        self._update_layer_table() # 更新列表UI
        self._on_layer_selection_changed() # 模拟选择变化，清空右侧面板
        self._plot_data() # 移除图层后重绘

    def _config_layer(self, row_to_config=None):
        """
        [v11.0 - 延迟加载修复版]
        配置图层。在对话框返回后，如果音频路径发生变化或音频数据不存在，
        则在此处执行实际的音频加载操作。
        """
        current_row = row_to_config if row_to_config is not None else self.layer_table.currentRow()
        if current_row < 0: return
        
        config_to_edit = self.layers[current_row]
        if config_to_edit.get('locked', False):
            QMessageBox.information(self, "图层已锁定", "该图层已被锁定，请先解锁后再进行配置。")
            return

        dialog = LayerConfigDialog(existing_config=config_to_edit, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            new_config = dialog.get_layer_config()
            if new_config:
                # --- [核心] 实现音频的延迟加载 ---
                old_path = config_to_edit.get('audio_path')
                new_path = new_config.get('audio_path')

                # 只有在以下情况才加载音频：
                # 1. 音频路径是新的。
                # 2. 或者路径存在，但'audio_data'还未被加载。
                if new_path and (new_path != old_path or 'audio_data' not in new_config or new_config['audio_data'] is None):
                    self.parent().statusBar().showMessage(f"正在后台加载音频: {os.path.basename(new_path)}...", 3000)
                    QApplication.processEvents() # 确保状态栏消息显示
                    try:
                        import librosa
                        y, sr = librosa.load(new_path, sr=None, mono=True)
                        new_config['audio_data'] = (y, sr)
                        self.parent().statusBar().showMessage(f"音频 '{os.path.basename(new_path)}' 加载成功。", 3000)
                    except Exception as e:
                        QMessageBox.critical(self, "音频加载失败", f"无法加载文件 {new_path}:\n{e}")
                        # 加载失败，清理相关字段
                        new_config['audio_data'] = None
                        new_config['audio_path'] = None
                
                self.layers[current_row] = new_config
                self._update_layer_table_row(current_row)
                self._on_layer_selection_changed()
                self._plot_data()

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
        """更新图层表格中指定行的内容和图标。"""
        if row >= len(self.layers): return # 越界检查
        layer = self.layers[row]
        
        # 确保行存在
        if row >= self.layer_table.rowCount():
            self.layer_table.insertRow(row)
        
        # 图层名称列
        name_item = QTableWidgetItem(layer['name'])
        # 只有未锁定的图层名称可编辑
        name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
        
        # 设置显示/隐藏图标
        is_enabled = layer.get('enabled', True)
        if self.icon_manager:
            icon_name = "success" if is_enabled else "hidden"
            name_item.setIcon(self.icon_manager.get_icon(icon_name))
        
        # 设置 Tooltip
        tooltip_parts = [f"<b>图层: {layer['name']}</b><hr>"]
        df = layer.get('df')
        tooltip_parts.append(f"<b>数据源:</b> {layer.get('data_filename', 'N/A')} ({len(df)}点)" if df is not None else "<b>数据源:</b> 无")
        tooltip_parts.append(f"<b>TextGrid:</b> {layer.get('tg_filename', 'N/A')}" if layer.get('tg') else "<b>TextGrid:</b> 无")
        tooltip_parts.append(f"<b>颜色方案:</b> {layer.get('color_scheme', '默认')}")
        if layer.get('locked', False):
            tooltip_parts.append("<hr><font color='red'><b>此图层已被锁定</b></font>")
        name_item.setToolTip("\n".join(tooltip_parts))
        self.layer_table.setItem(row, 0, name_item)
        
        # --- [核心修正 2] 使用 setCellWidget 实现完美居中 ---
        
        # 1. 创建一个容器 QWidget，它将填充整个单元格
        cell_widget = QWidget()
        # 2. 为容器创建一个水平布局
        layout = QHBoxLayout(cell_widget)
        # 3. 移除所有边距，并设置对齐方式为居中
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignCenter)

        # 4. 创建一个 QLabel 来显示图标
        icon_label = QLabel()

        # 5. 如果图层被锁定，则在 QLabel 上设置图标
        if layer.get('locked', False) and self.icon_manager:
            # 使用 pixmap 可以确保图标大小一致
            icon_label.setPixmap(self.icon_manager.get_icon("lock").pixmap(QSize(24, 24)))
            icon_label.setToolTip("此图层已锁定")

        # 6. 将 QLabel 添加到居中布局中
        layout.addWidget(icon_label)
        
        # 7. 最后，将包含居中图标的整个 QWidget 放入单元格
        self.layer_table.setCellWidget(row, 1, cell_widget)

    def _show_layer_context_menu(self, pos):
        """显示图层列表的右键上下文菜单。"""
        selected_rows = sorted(list(set(item.row() for item in self.layer_table.selectedItems())))
    
        if not selected_rows: return

        menu = QMenu(self)
    
        # --- 合并图层功能 (保持不变) ---
        if len(selected_rows) > 1:
            merge_action = menu.addAction(self.icon_manager.get_icon("concatenate"), f"合并选中的 {len(selected_rows)} 个图层...")
            merge_action.triggered.connect(lambda: self._merge_selected_layers(selected_rows))
            menu.addSeparator()

        # --- 单个图层操作 ---
        if len(selected_rows) == 1:
            row = selected_rows[0]
            layer = self.layers[row]
        
            # --- [新增] 拆分图层功能 ---
            # 检查当前图层的DataFrame是否包含 'source_file' 列
            df = layer.get('df')
            if df is not None and 'source_file' in df.columns:
                split_action = menu.addAction(self.icon_manager.get_icon("cut"), "拆分此图层")
                split_action.triggered.connect(lambda: self._split_single_layer(row))
                menu.addSeparator()
            is_enabled = layer.get('enabled', True)
            is_locked = layer.get('locked', False)

            # 显示/隐藏动作
            if is_enabled: toggle_action = menu.addAction(self.icon_manager.get_icon("hidden"), "隐藏图层")
            else: toggle_action = menu.addAction(self.icon_manager.get_icon("show"), "显示图层")
        
            # 锁定/解锁动作
            if is_locked: lock_action = menu.addAction(self.icon_manager.get_icon("unlock"), "解锁图层")
            else: lock_action = menu.addAction(self.icon_manager.get_icon("lock"), "锁定图层")
        
            menu.addSeparator()
        
            rename_action = menu.addAction(self.icon_manager.get_icon("rename"), "重命名...")
            rename_action.setEnabled(not is_locked)
            config_action = menu.addAction(self.icon_manager.get_icon("settings"), "配置...")
            config_action.setEnabled(not is_locked)
            remove_action = menu.addAction(self.icon_manager.get_icon("delete"), "移除图层")
            remove_action.setEnabled(not is_locked)
        
            menu.addSeparator()
        
            save_action = menu.addAction(self.icon_manager.get_icon("save"), "保存单层图片...")
            save_action.setEnabled(is_enabled)
    
            action = menu.exec_(self.layer_table.mapToGlobal(pos))
        
            if action == toggle_action: self._toggle_layer_visibility(row)
            elif action == lock_action: self._toggle_layer_lock(row)
            elif action == rename_action: self.layer_table.editItem(self.layer_table.item(row, 0))
            elif action == config_action: self._config_layer(row)
            elif action == remove_action: self._remove_layer(row)
            elif action == save_action: self._save_single_layer_image(row)
        else: # 多选时，只显示合并菜单项
             action = menu.exec_(self.layer_table.mapToGlobal(pos))
             # 此处无需 if 判断，因为 merge_action 的 triggered 信号已经连接

    def _split_single_layer(self, row_to_split):
        """
        [新增] 核心功能：将单个合并后的图层，按 'source_file' 列拆分成多个新图层。
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
        base_config = deepcopy(layer_to_split) # 使用被拆分图层的配置作为模板

        for source_name in unique_sources:
            # 筛选出属于该来源的数据
            single_df = merged_df[merged_df['source_file'] == source_name].copy()
            # 移除 'source_file' 列，因为它在新图层中已无意义
            single_df.drop(columns=['source_file'], inplace=True)
        
            # 检查并确保新图层名称唯一
            final_name = str(source_name) # 确保是字符串
            counter = 1
            existing_names = {l['name'] for l in self.layers}
            while final_name in existing_names:
                final_name = f"{source_name} ({counter})"
                counter += 1

            # 构建新图层的配置
            new_config = deepcopy(base_config)
            new_config['name'] = final_name
            new_config['df'] = single_df
            new_config['data_filename'] = f"拆分自 ({layer_to_split['name']})"
            # 恢复分组依据为自动猜测或无分组
            new_config['group_col'] = next((c for c in single_df.columns if 'vowel' in c.lower() or 'label' in c.lower()), "无分组")
            new_config['groups'] = {} # 清空分组，让其自动生成

            new_layers_to_add.append(new_config)

        # 2. 询问用户是否删除原图层
        reply = QMessageBox.question(self, "确认拆分",
                                     f"将把图层 '{layer_to_split['name']}' 拆分为 {len(new_layers_to_add)} 个新图层。\n\n"
                                     f"是否在拆分后删除原始的合并图层？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)

        # 3. 执行操作
        # 先删除原图层（如果用户同意）
        if reply == QMessageBox.Yes:
            del self.layers[row_to_split]
    
        # 再添加所有新图层
        self.layers.extend(new_layers_to_add)

        # 4. 刷新UI
        self._update_layer_table()
        # 选中新添加的第一个图层
        for i, layer in enumerate(self.layers):
            if layer['name'] == new_layers_to_add[0]['name']:
                self.layer_table.selectRow(i)
                break
        self._plot_data()

    def _merge_selected_layers(self, rows_to_merge):
        """
        [v2.0 - TextGrid感知版]
        核心功能：合并所有选中的图层。
        此版本智能地处理 TextGrid 标签，通过创建复合标签 (source_label)
        来同时保留来源文件和原始分组（如元音）的信息。
        """
        # 1. 获取新图层名称 (保持不变)
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
        
        # 3. [核心逻辑] 遍历所有选中的图层并智能合并数据
        for row in rows_to_merge:
            layer = self.layers[row]
            df = layer.get('df')
            
            if df is None or df.empty:
                continue

            df_copy = df.copy()
            source_name = layer['name']
            
            # 3.1. 添加 source_file 列
            df_copy['source_file'] = source_name
            
            # 3.2. [关键] 创建复合的 source_label 列
            original_group_col = layer.get('group_col')
            
            if original_group_col and original_group_col != "无分组" and original_group_col in df_copy.columns:
                # --- [核心修复] 在创建复合标签前，先过滤掉 NaN 值 ---
                # 1. 使用 .dropna() 移除在原始分组列中值为 NaN 的行
                df_copy.dropna(subset=[original_group_col], inplace=True)
                
                # 2. 如果过滤后 DataFrame 为空，则跳过此图层
                if df_copy.empty:
                    continue
                # --- 修复结束 ---
                
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

        # 更新新图层的配置
        new_layer_config = base_config
        new_layer_config['name'] = new_name
        new_layer_config['df'] = merged_df
        new_layer_config['data_filename'] = f"合并自 {len(rows_to_merge)} 个图层"
        
        # [关键] 将分组依据设置为新的 'source_label' 列
        new_layer_config['group_col'] = 'source_label' 
        
        new_layer_config['tg'] = None
        new_layer_config['tg_filename'] = "N/A"
        new_layer_config['locked'] = False
        new_layer_config['groups'] = {} # 清空旧的分组，让其根据新的复合标签自动生成

        self.layers.append(new_layer_config)
        
        # 5. 可选：删除原始图层
        # --- [核心修复] 修正 f-string 语法错误 ---
        # 原来的代码: f"...原来的 {len(rows_to_merge)} 个子图层？"
        # 错误原因: Python 解释器认为 {len...} 是一个需要格式化的表达式
        
        # 正确做法: 使用 .format() 方法，或者确保 f-string 中没有其他大括号
        question_text = (f"已成功创建合并图层 '{new_name}'。\n\n"
                         "新图层将按'来源-标签'进行分组。\n"
                         f"是否要删除原来的 {len(rows_to_merge)} 个子图层？")
        
        reply = QMessageBox.question(self, "操作完成",
                                     question_text,
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        # --- 修复结束 ---

        if reply == QMessageBox.Yes:
            for row in sorted(rows_to_merge, reverse=True):
                del self.layers[row]

        # 6. 刷新UI (保持不变)
        self._update_layer_table()
        for i, layer in enumerate(self.layers):
            if layer['name'] == new_name:
                self.layer_table.selectRow(i)
                break
        self._plot_data()

    def _on_layer_double_clicked(self, item):
        """双击图层项时，打开配置对话框。"""
        if item.column() == 0: # 确保点击的是名称列
            self._config_layer(item.row())

    def _on_layer_renamed(self, item):
        """处理图层名称单元格文本改变（重命名完成）的信号。"""
        if item.column() == 0: # 确保是名称列
            row = item.row()
            # 避免在 _update_layer_table_row 内部设置item时触发此槽
            if not self.layer_table.signalsBlocked(): 
                old_name = self.layers[row]['name']
                new_name = item.text().strip() # 获取新名称并去除空白

                if not new_name: # 如果新名称为空
                    QMessageBox.warning(self, "名称无效", "图层名称不能为空。")
                    item.setText(old_name) # 恢复旧名称
                    return

                if new_name == old_name: # 如果名称没有改变
                    return

                # 检查名称是否重复
                if any(layer['name'] == new_name for i, layer in enumerate(self.layers) if i != row):
                    QMessageBox.warning(self, "名称重复", f"图层名称 '{new_name}' 已存在，请使用其他名称。")
                    item.setText(old_name) # 恢复旧名称
                    return
                    
                # 更新内部数据结构
                self.layers[row]['name'] = new_name
                self._update_layer_settings_panel_title() # 更新右侧面板标题
                self._plot_data() # 重命名可能影响图例，所以重绘

    def _on_layer_selection_changed(self):
        """
        [v9.1 - 修复版]
        处理图层列表选中行变化。
        - 移除了对已不存在的 audio_control_panel 和 audio_file_label 的引用。
        - 此方法现在只负责更新数据预览、右侧图层设置和分组列表。
        """
        current_row = self.layer_table.currentRow()
        self.current_selected_layer_index = current_row

        # --- 更新数据预览和右侧图层设置面板 (逻辑不变) ---
        if current_row > -1 and current_row < len(self.layers):
            layer_config = self.layers[current_row]
            # 更新数据预览表格
            self.table_view.setModel(PandasModel(layer_config.get('df', pd.DataFrame())))
            
            # 填充右侧图层设置面板
            self._populate_layer_settings_panel(layer_config)
            
            is_locked = layer_config.get('locked', False)
            title = f"图层设置 ({layer_config['name']})"
            if is_locked:
                title += " (已锁定)"
            self.layer_settings_group.setTitle(title)
            self.layer_settings_group.setEnabled(not is_locked)
        else:
            # 没有选中行，清空UI
            self.table_view.setModel(None)
            self.layer_settings_group.setTitle("图层设置 (未选择图层)")
            self.layer_settings_group.setEnabled(False)
        
        # --- [核心修改] 移除了所有与 audio_control_panel 相关的代码 ---
        # 旧的显示/隐藏音频面板的逻辑已不再需要。

        # --- 更新下方的分组列表 ---
        self._update_group_table()
        
        # --- 更新整体UI状态 ---
        self._update_ui_state()

    def _load_audio_for_layer(self):
        """
        [新增] 为当前选中的图层加载关联的音频文件。
        """
        row = self.current_selected_layer_index
        if row < 0 or row >= len(self.layers):
            return
        
        layer_config = self.layers[row]
        
        # 打开文件对话框
        filepath, _ = QFileDialog.getOpenFileName(self, "选择关联的音频文件", "", "音频文件 (*.wav *.mp3 *.flac)")
        if not filepath:
            return
        
        try:
            # 使用 librosa 加载音频数据。sr=None 保持原始采样率
            import librosa
            y, sr = librosa.load(filepath, sr=None, mono=True)
            
            # 创建 QMediaPlayer 实例
            from PyQt5.QtMultimedia import QMediaPlayer
            player = QMediaPlayer()
            
            # 更新图层配置
            layer_config['audio_path'] = filepath
            layer_config['audio_data'] = (y, sr)
            layer_config['player'] = player
            
            # 更新UI
            self.audio_file_label.setText(f"<b>{os.path.basename(filepath)}</b>")
            QMessageBox.information(self, "加载成功", f"音频文件已成功关联到图层 '{layer_config['name']}'。")
            
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法加载或处理音频文件:\n{e}")
            # 清理失败的加载
            layer_config.pop('audio_path', None)
            layer_config.pop('audio_data', None)
            layer_config.pop('player', None)
            self.audio_file_label.setText("<i>加载失败</i>")

    def _play_selected_segment(self):
        """
        [v10.1 - 播放错误最终修复版]
        - 每次播放都生成一个带时间戳的、唯一的临时文件名，彻底避免文件写入冲突。
        - 每次播放都使用全新的QMediaPlayer实例，确保资源隔离。
        """
        # --- 1. 获取选中的图层和分组 (逻辑不变) ---
        layer_row = self.current_selected_layer_index
        if layer_row < 0 or layer_row >= len(self.layers): return

        group_row = self.group_table.currentRow()
        if group_row < 0:
            QMessageBox.warning(self, "未选择", "请在下方列表中选择一个TextGrid区间进行播放。")
            return

        group_name = self.group_table.item(group_row, 0).text()
        layer_config = self.layers[layer_row]

        # --- 2. 检查所需数据 (逻辑不变) ---
        tg = layer_config.get('tg')
        audio_data = layer_config.get('audio_data')
        
        if not all([tg, audio_data]):
            QMessageBox.warning(self, "数据不完整", "请确保已为当前图层加载了TextGrid和关联的音频文件。")
            return

        # --- 3. 在TextGrid中查找区间 (逻辑不变) ---
        y, sr = audio_data
        target_interval = None
        for tier in tg:
            if isinstance(tier, textgrid.IntervalTier):
                for interval in tier:
                    if interval.mark == group_name:
                        target_interval = interval; break
            if target_interval: break
        
        if not target_interval:
            QMessageBox.warning(self, "未找到区间", f"在TextGrid中未找到名为 '{group_name}' 的区间。")
            return

        # --- 4. 切片音频并写入临时文件 (核心修复) ---
        try:
            import soundfile as sf
            from PyQt5.QtMultimedia import QMediaPlayer
            from PyQt5.QtCore import QUrl
            import os
            import time # 导入time模块以获取时间戳

            start_sample = int(target_interval.minTime * sr)
            end_sample = int(target_interval.maxTime * sr)
            start_sample = max(0, start_sample); end_sample = min(len(y), end_sample)

            if start_sample >= end_sample: return

            segment_data = y[start_sample:end_sample]
            
            # --- [核心修复] 生成带时间戳的唯一文件名 ---
            # 这样每次播放都会创建一个全新的文件，永远不会冲突。
            timestamp = int(time.time() * 1000) # 获取毫秒级时间戳
            safe_group_name = "".join(c for c in group_name if c.isalnum()) # 清理特殊字符
            temp_file_path = os.path.join(self.temp_audio_dir, f"segment_{safe_group_name}_{timestamp}.wav")
            # --- 修复结束 ---
            
            sf.write(temp_file_path, segment_data, sr)
            
            # --- 5. 使用全新的QMediaPlayer实例播放 ---
            # 清理上一个播放器实例
            old_player = layer_config.get('player')
            if old_player:
                old_player.stop()
            
            # 创建新实例
            player = QMediaPlayer()
            # 存储新实例，以便下次可以清理它
            layer_config['player'] = player
            
            # 播放新的、唯一命名的临时文件
            player.setMedia(QMediaContent(QUrl.fromLocalFile(temp_file_path)))
            player.play()

        except Exception as e:
            QMessageBox.critical(self, "播放失败", f"处理或播放音频片段时发生错误:\n{e}")



    def _update_group_table(self):
        """
        [v4.1 - 配套修复版]
        根据当前选中的图层，完整地刷新下方的分组控制表格。
        此方法现在只在图层切换时被调用，不再参与选择事件的处理。
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

        groups = sorted(df[group_col].dropna().astype(str).unique(), key=str)

        if 'groups' not in layer_config:
            layer_config['groups'] = {}

        for i, group_name in enumerate(groups):
            if group_name not in layer_config['groups']:
                layer_config['groups'][group_name] = {'enabled': True, 'color': QColor(), 'emphasized': False}

            settings = layer_config['groups'][group_name]
            self.group_table.insertRow(i)
            
            # --- 第0列：分组名称 (带图标) ---
            name_item = QTableWidgetItem(group_name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)

            is_emphasized = settings.get('emphasized', False)
            if is_emphasized and self.icon_manager:
                name_item.setIcon(self.icon_manager.get_icon("favorite"))
                name_item.setToolTip(f"分组 '{group_name}' (已强调)")
            else:
                name_item.setIcon(QIcon()) 
                name_item.setToolTip(f"分组 '{group_name}'")
            
            self.group_table.setItem(i, 0, name_item)

            # --- 第1列：显示复选框 ---
            enabled_check = QCheckBox()
            enabled_check.setChecked(settings.get('enabled', True))
            enabled_widget = QWidget()
            enabled_layout = QHBoxLayout(enabled_widget)
            enabled_layout.addWidget(enabled_check)
            enabled_layout.setAlignment(Qt.AlignCenter)
            enabled_layout.setContentsMargins(0,0,0,0)
            self.group_table.setCellWidget(i, 1, enabled_widget)

            enabled_check.toggled.connect(lambda state, gn=group_name: self._on_group_toggled(gn, 'enabled', state))

        self.group_table.blockSignals(False)

    def closeEvent(self, event):
        """
        [新增] 重写关闭事件，以清理临时音频文件目录。
        """
        import shutil
        try:
            # 停止所有图层的播放器
            for layer in self.layers:
                player = layer.get('player')
                if player and player.state() != QMediaPlayer.StoppedState:
                    player.stop()
            
            # 递归删除临时目录及其所有内容
            if self.temp_audio_dir and os.path.exists(self.temp_audio_dir):
                shutil.rmtree(self.temp_audio_dir)
                print(f"[Vowel Plotter] Cleaned up temporary audio directory: {self.temp_audio_dir}")
        except Exception as e:
            print(f"[Vowel Plotter Warning] Failed to clean up temp directory: {e}")
        
        super().closeEvent(event)

    def _on_group_toggled(self, group_name, prop, state):
        """
        [v3.0 - 简化版]
        当分组表格中的“显示”复选框被点击时调用。
        """
        row = self.current_selected_layer_index
        if row < 0 or row >= len(self.layers):
            return

        # 确保只处理 'enabled' 属性
        if prop != 'enabled':
            return

        layer_config = self.layers[row]
        if 'groups' in layer_config and group_name in layer_config['groups']:
            layer_config['groups'][group_name][prop] = state
            self._plot_data() # 更新数据模型后立即重绘图表

    def _on_group_selection_changed(self):
        """
        [v10.0 - 无变化]
        在“选择即强调”模式下，当分组表格中的选择项发生变化时调用。
        此版本不再重建UI，而是直接修改现有UI元素的状态，彻底避免无限循环。
        """
        layer_row = self.current_selected_layer_index
        if layer_row < 0 or layer_row >= len(self.layers): return
        layer_config = self.layers[layer_row]
        
        all_groups_in_layer = layer_config.get('groups', {})
        if not all_groups_in_layer: return

        selected_items = self.group_table.selectedItems()
        selected_group_name = selected_items[0].text() if selected_items else None

        for row in range(self.group_table.rowCount()):
            item = self.group_table.item(row, 0)
            if not item: continue
            
            group_name = item.text()
            is_selected = (group_name == selected_group_name)
            
            if group_name in all_groups_in_layer:
                all_groups_in_layer[group_name]['emphasized'] = is_selected
            
            if is_selected and self.icon_manager:
                item.setIcon(self.icon_manager.get_icon("favorite"))
            else:
                item.setIcon(QIcon())
        self._plot_data()

    def _show_group_context_menu(self, pos):
        """
        [v9.0 - 上下文播放版]
        显示分组/TextGrid项列表的右键上下文菜单。

        此方法根据右键点击的位置和当前选中的图层状态，动态构建菜单：
        - **播放片段**: 如果当前图层已关联音频，并且用户点击了一个项目，
          则显示“播放此片段”选项。
        - **批量操作**: 为当前所有选中的项目（可能是一个或多个）提供统一的
          “显示/隐藏”和“强调/取消强调”操作。
        - **全局操作**: 如果用户点击在列表的空白区域，则提供作用于当前图层
          所有分组的“全部强调”和“全部取消强调”选项。
        """
        # --- 1. 获取当前选中的图层配置 ---
        layer_row = self.current_selected_layer_index
        if layer_row < 0 or layer_row >= len(self.layers):
            return # 如果没有选中任何图层，则不显示菜单
        layer_config = self.layers[layer_row]

        # --- 2. 创建菜单实例 ---
        menu = QMenu(self)
        
        # --- 3. 判断点击位置并获取选中项 ---
        item_at_pos = self.group_table.itemAt(pos)
        selected_items = self.group_table.selectedItems()
        
        # 如果没有选中任何项，但点击在了某个项上，则临时选中它
        # 这改善了用户体验，用户不必先单击再右键
        if not selected_items and item_at_pos:
            self.group_table.setCurrentItem(item_at_pos)
            selected_items = self.group_table.selectedItems()

        # --- 4. 动态构建菜单项 ---
        if selected_items:
            # --- 场景 A: 用户已选中一个或多个项目 ---
            
            # 提取选中的行号和分组名称
            selected_rows = sorted(list(set(item.row() for item in selected_items)))
            num_selected = len(selected_rows)
            selected_group_names = [self.group_table.item(row, 0).text() for row in selected_rows]

            # -- 播放动作 (仅在单选且音频可用时显示) --
            if num_selected == 1 and 'audio_data' in layer_config and 'player' in layer_config:
                play_action = menu.addAction(self.icon_manager.get_icon("play"), "播放此片段 (Enter)")
                play_action.triggered.connect(self._play_selected_segment)
                menu.addSeparator()

            # -- 批量显示/隐藏动作 --
            show_action = menu.addAction(self.icon_manager.get_icon("show"), f"显示选中的 {num_selected} 项")
            hide_action = menu.addAction(self.icon_manager.get_icon("hidden"), f"隐藏选中的 {num_selected} 项")
            menu.addSeparator()

            # -- 批量强调/取消强调动作 --
            emphasize_action = menu.addAction(self.icon_manager.get_icon("favorite"), f"强调选中的 {num_selected} 项")
            unemphasize_action = menu.addAction(self.icon_manager.get_icon("unfavorite"), f"取消强调选中的 {num_selected} 项")
            
            # -- 连接批量操作的信号 --
            show_action.triggered.connect(lambda: self._apply_to_selected_groups('enabled', True, selected_group_names))
            hide_action.triggered.connect(lambda: self._apply_to_selected_groups('enabled', False, selected_group_names))
            emphasize_action.triggered.connect(lambda: self._apply_to_selected_groups('emphasized', True, selected_group_names))
            unemphasize_action.triggered.connect(lambda: self._apply_to_selected_groups('emphasized', False, selected_group_names))
        
        else:
            # --- 场景 B: 用户右键点击在列表的空白区域 ---
            
            # -- 全局强调/取消强调动作 --
            emphasize_all_action = menu.addAction("全部强调")
            unemphasize_all_action = menu.addAction("全部取消强调")

            # -- 连接全局操作的信号 --
            all_group_names = list(layer_config.get('groups', {}).keys())
            emphasize_all_action.triggered.connect(lambda: self._apply_to_selected_groups('emphasized', True, all_group_names))
            unemphasize_all_action.triggered.connect(lambda: self._apply_to_selected_groups('emphasized', False, all_group_names))
        
        # --- 5. 显示菜单 ---
        # 只有当菜单中有内容时才显示
        if not menu.isEmpty():
            menu.exec_(self.group_table.mapToGlobal(pos))

    def _apply_to_selected_groups(self, prop, value, group_names):
        """
        [新增] 将一个属性变更应用到所有指定的分组上。
        
        Args:
            prop (str): 要修改的属性 ('enabled' 或 'emphasized').
            value (bool): 要设置的新值 (True 或 False).
            group_names (list): 要修改的分组名称列表。
        """
        layer_row = self.current_selected_layer_index
        if layer_row < 0 or layer_row >= len(self.layers):
            return
            
        layer_config = self.layers[layer_row]
        
        # 1. 循环修改数据模型
        for name in group_names:
            if name in layer_config.get('groups', {}):
                layer_config['groups'][name][prop] = value
        
        # 2. 修改完成后，刷新UI并重绘图表
        self._update_group_table()
        self._plot_data()

    def _toggle_layer_visibility(self, row):
        """切换图层的可见性，并高效地只更新受影响的UI元素。"""
        if row < len(self.layers):
            self.layers[row]['enabled'] = not self.layers[row].get('enabled', True)
            self._plot_data() # 重新绘图
            self._update_layer_table_row(row) # 更新表格中的图标

    def _toggle_layer_lock(self, row):
        """切换图层的锁定状态。"""
        if row < len(self.layers):
            # 切换数据模型中的状态
            is_locked = not self.layers[row].get('locked', False)
            self.layers[row]['locked'] = is_locked
            
            # 更新表格中的图标和可编辑状态
            self._update_layer_table_row(row)
            
            # --- [新增] 如果被锁定/解锁的是当前选中的图层，则立即刷新右侧面板的状态 ---
            if row == self.current_selected_layer_index:
                title = f"图层设置 ({self.layers[row]['name']})"
                if is_locked:
                    title += " (已锁定)"
                self.layer_settings_group.setTitle(title)
                self.layer_settings_group.setEnabled(not is_locked)

    def _save_single_layer_image(self, row):
        """
        保存单个图层的渲染图。
        临时隐藏其他图层，绘制，保存，然后恢复。
        """
        if row >= len(self.layers): return
        layer_to_save = self.layers[row]
        
        title = layer_to_save['name']
        safe_filename = re.sub(r'[\\/*?:"<>|]', "_", title) # 清理文件名中的非法字符
        
        file_path, _ = QFileDialog.getSaveFileName(self, f"保存图层 '{title}'", f"{safe_filename}.png", "PNG图片 (*.png);;高分辨率PDF (*.pdf);;JPEG图片 (*.jpg);;SVG矢量图 (*.svg)")
        if not file_path: return # 用户取消

        # 记录所有图层的原始启用状态
        original_states = {i: l.get('enabled', True) for i, l in enumerate(self.layers)}
        
        # 临时将所有图层设置为禁用，只启用当前要保存的图层
        for i, layer in enumerate(self.layers):
            layer['enabled'] = (i == row)
        
        self._plot_data() # 用单图层数据重绘图表
        
        try:
            # 保存画布内容为图片
            self.figure.savefig(file_path, dpi=300, bbox_inches='tight', facecolor='white')
            QMessageBox.information(self, "成功", f"图层 '{title}' 已成功保存为图片:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"无法保存图片: {e}")
        finally:
            # 恢复所有图层的原始显示状态
            for i, state in original_states.items():
                if i < len(self.layers): # 确保索引有效
                    self.layers[i]['enabled'] = state
            self._plot_data() # 恢复完整图表

    # ==========================================================================
    # 右侧面板 (图层设置) 相关方法
    # ==========================================================================
    def _populate_layer_settings_panel(self, layer_config):
        """
        用选中图层的配置填充右侧的图层设置面板。
        这是一个上下文敏感的UI更新。
        """
        # 阻止信号，避免在设置值时立即触发 _on_current_layer_setting_changed
        self.point_size_slider.blockSignals(True)
        self.point_alpha_slider.blockSignals(True)
        self.layer_marker_combo.blockSignals(True)
        self.mean_group.blockSignals(True)
        self.mean_marker_combo.blockSignals(True)
        self.mean_size_slider.blockSignals(True)
        self.ellipse_group.blockSignals(True)
        self.ellipse_std_combo.blockSignals(True)
        self.ellipse_style_combo.blockSignals(True)
        self.ellipse_width_slider.blockSignals(True)
        self.color_scheme_combo_layer.blockSignals(True) # 新增

        # 填充数据点样式
        self.point_size_slider.setValue(layer_config.get('point_size', 15))
        # alpha是0-1的浮点数，滑块是10-100的整数，需要转换
        self.point_alpha_slider.setValue(int(layer_config.get('point_alpha', 0.3) * 100))
        
        # 填充图层标记样式
        self.layer_marker_combo.setCurrentText(layer_config.get('marker', '圆点'))
        
        # 填充平均值点设置
        self.mean_group.setChecked(layer_config.get('mean_enabled', False))
        self.mean_marker_combo.setCurrentText(layer_config.get('mean_marker', '加号'))
        self.mean_size_slider.setValue(layer_config.get('mean_size', 100))
        
        # 填充标准差椭圆设置
        self.ellipse_group.setChecked(layer_config.get('ellipse_enabled', False))
        self.ellipse_std_combo.setCurrentText(layer_config.get('ellipse_std', '2 (95%)'))
        self.ellipse_style_combo.setCurrentText(layer_config.get('ellipse_style', '实线'))
        self.ellipse_width_slider.setValue(layer_config.get('ellipse_width', 2))
        
        # 填充颜色方案选择器 (图层专属)
        self.color_scheme_combo_layer.setCurrentText(layer_config.get('color_scheme', '默认'))
        
        # 填充该图层特有的分组颜色和复选框
        self._populate_group_settings_for_layer(layer_config)

        # 解除信号阻止
        self.point_size_slider.blockSignals(False)
        self.point_alpha_slider.blockSignals(False)
        self.layer_marker_combo.blockSignals(False)
        self.mean_group.blockSignals(False)
        self.mean_marker_combo.blockSignals(False)
        self.mean_size_slider.blockSignals(False)
        self.ellipse_group.blockSignals(False)
        self.ellipse_std_combo.blockSignals(False)
        self.ellipse_style_combo.blockSignals(False)
        self.ellipse_width_slider.blockSignals(False)
        self.color_scheme_combo_layer.blockSignals(False) # 新增

    def _populate_group_settings_for_layer(self, layer_config):
        """
        为单个选中的图层填充其分组颜色设置。
        这些设置（启用状态和颜色）是图层内部的，与全局分组独立。
        """
        # 清除旧的UI控件
        while self.group_settings_layout.count():
            child = self.group_settings_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()
        
        group_col = layer_config.get('group_col')
        df = layer_config.get('df')

        # 如果没有有效的DataFrame或分组列，则隐藏分组设置
        if not (df is not None and group_col and group_col != "无分组" and group_col in df.columns):
            self.grouping_group.setVisible(False)
            return
        
        self.grouping_group.setVisible(True)
        # 获取该图层的所有唯一分组名称
        groups = sorted(df[group_col].dropna().astype(str).unique(), key=str)
        
        # 获取该图层选定的颜色方案
        color_scheme_name = layer_config.get('color_scheme', '默认')
        current_color_scheme = self.COLOR_SCHEMES.get(color_scheme_name, self.COLOR_SCHEMES['默认'])
        color_cycle = cycle(current_color_scheme)
        
        # 获取或初始化图层内部的分组设置
        if 'groups' not in layer_config:
            layer_config['groups'] = {} # 每个图层有自己的 'groups' 字典
        
        # 动态创建分组的复选框和颜色按钮
        for group_name in groups:
            if group_name not in layer_config['groups']:
                # 新分组，赋予默认启用状态和下一个循环颜色
                layer_config['groups'][group_name] = {'enabled': True, 'color': QColor(next(color_cycle))}
            
            settings = layer_config['groups'][group_name]
            row = QWidget(); layout = QHBoxLayout(row); layout.setContentsMargins(0, 0, 0, 0)
            
            cb = QCheckBox(group_name); cb.setChecked(settings['enabled'])
            cb.setToolTip(f"勾选/取消勾选以在图表中显示/隐藏 '{group_name}' 分组。")
            
            color_btn = ColorButton(settings['color'])
            color_btn.setToolTip(f"点击选择 '{group_name}' 分组的颜色。")
            color_btn.setFixedWidth(50) # 保持宽度一致
            
            layout.addWidget(cb, 1); layout.addWidget(color_btn); self.group_settings_layout.addWidget(row)

            # 连接信号，更新的是 layer_config['groups'] 中的值
            cb.stateChanged.connect(lambda state, n=group_name: self._on_layer_group_prop_changed(n, 'enabled', state == Qt.Checked))
            color_btn.colorChanged.connect(lambda n=group_name, btn=color_btn: self._on_layer_group_prop_changed(n, 'color', btn.color()))

    def _on_layer_group_prop_changed(self, group_name, prop, value):
        """处理当前选中图层内部某个分组的属性（启用状态或颜色）变化。"""
        row = self.layer_table.currentRow()
        if row < 0: return # 没有选中图层
        layer_config = self.layers[row]
        
        if 'groups' in layer_config and group_name in layer_config['groups']:
            layer_config['groups'][group_name][prop] = value
            self._plot_data() # 属性变化后重新绘图

    def _apply_color_scheme_to_current_layer(self):
        """将当前图层设置面板中选择的颜色方案应用到当前图层的分组。"""
        row = self.layer_table.currentRow()
        if row < 0: return # 没有选中图层
        layer_config = self.layers[row]

        scheme_name = self.color_scheme_combo_layer.currentText()
        layer_config['color_scheme'] = scheme_name # 保存到图层配置中

        current_color_scheme = self.COLOR_SCHEMES.get(scheme_name, self.COLOR_SCHEMES['默认'])
        color_cycle = cycle(current_color_scheme)
        
        if 'groups' in layer_config:
            for group_name in sorted(layer_config['groups'].keys(), key=str):
                new_color = QColor(next(color_cycle))
                layer_config['groups'][group_name]['color'] = new_color # 更新模型中的颜色
            self._populate_group_settings_for_layer(layer_config) # 重新填充UI以更新颜色按钮
            self._plot_data() # 重新绘图

    def _on_current_layer_setting_changed(self):
        """当右侧图层设置面板的任何控件变化时调用。"""
        row = self.layer_table.currentRow()
        if row < 0: return # 没有选中图层
        layer = self.layers[row]
        
        # 从UI控件获取值并保存到当前选中图层的配置字典中
        layer['point_size'] = self.point_size_slider.value()
        layer['point_alpha'] = self.point_alpha_slider.value() / 100.0 # 滑块值转换回0-1
        layer['marker'] = self.layer_marker_combo.currentText()
        layer['mean_enabled'] = self.mean_group.isChecked()
        layer['mean_marker'] = self.mean_marker_combo.currentText()
        layer['mean_size'] = self.mean_size_slider.value()
        layer['ellipse_enabled'] = self.ellipse_group.isChecked()
        layer['ellipse_std'] = self.ellipse_std_combo.currentText()
        layer['ellipse_style'] = self.ellipse_style_combo.currentText()
        layer['ellipse_width'] = self.ellipse_width_slider.value()
        
        self._plot_data() # 重新绘图以应用新设置

    # ==========================================================================
    # 核心绘图逻辑
    # ==========================================================================
    def _plot_data(self):
        """
        [v7.0 - 归一化版] 核心绘图逻辑。
        - 增加了Z-Score归一化处理。
        - 动态更新坐标轴标签以反映归一化状态。
        """
        try:
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            self.plotted_collections.clear()
            self.hover_annotation = None
            has_any_visible_data = False

            # --- [核心修改 1] 读取归一化方法 ---
            norm_method = self.norm_combo.currentText()
            # --- 结束修改 ---

            magnify_enabled = self.emphasis_magnify_check.isChecked()
            outline_enabled = self.emphasis_outline_check.isChecked()
            opacity_enabled = self.emphasis_opacity_check.isChecked()
            magnify_percent = self.emphasis_magnify_slider.value()
            outline_width = self.emphasis_outline_width_slider.value() / 10.0
            opacity_change_percent = self.emphasis_opacity_slider.value()

            for layer_config in self.layers:
                if not layer_config.get('enabled', True): continue
                
                df_original = layer_config.get('df')
                if df_original is not None and '_is_ignored' in df_original.columns:
                    df = df_original[df_original['_is_ignored'] == False].copy()
                else:
                    df = df_original

                f1_col, f2_col, group_col = layer_config.get('f1_col'), layer_config.get('f2_col'), layer_config.get('group_col')
                if df is None or not all(c in df.columns for c in [f1_col, f2_col]): continue

                # --- [核心修改 2] Z-Score (按图层) 预处理 ---
                f1_mean, f1_std, f2_mean, f2_std = 0, 1, 0, 1
                if norm_method == "Z-Score (按图层)":
                    f1_data = df[f1_col].dropna()
                    f2_data = df[f2_col].dropna()
                    f1_mean, f1_std = f1_data.mean(), f1_data.std()
                    f2_mean, f2_std = f2_data.mean(), f2_data.std()
                    if f1_std == 0 or np.isnan(f1_std): f1_std = 1
                    if f2_std == 0 or np.isnan(f2_std): f2_std = 1
                # --- 结束修改 ---
                
                base_point_size = layer_config.get('point_size', 15)
                base_point_alpha = layer_config.get('point_alpha', 0.3)
                layer_marker = self.MARKER_STYLES.get(layer_config.get('marker', '圆点'), 'o')
                
                if group_col != "无分组" and group_col in df.columns:
                    plot_df_base = df.dropna(subset=[f1_col, f2_col, group_col])
                    groups_in_layer = layer_config.get('groups', {})
                    
                    for group_name_str, group_settings in groups_in_layer.items():
                        if not group_settings.get('enabled', True): continue
                        
                        group_data = plot_df_base[plot_df_base[group_col].astype(str) == group_name_str]
                        if group_data.empty: continue

                        f1_raw, f2_raw = group_data[f1_col], group_data[f2_col]
                        
                        # --- [核心修改 3] 应用归一化 ---
                        if norm_method == "Z-Score (按图层)":
                            f1 = (f1_raw - f1_mean) / f1_std
                            f2 = (f2_raw - f2_mean) / f2_std
                        else: # 原始值 (Hz)
                            f1, f2 = f1_raw, f2_raw
                        # --- 结束修改 ---

                        color_hex = group_settings['color'].name()
                        label = f"{layer_config['name']} - {group_name_str}"
                        is_emphasized = group_settings.get('emphasized', False)
                        
                        scatter_kwargs = { 'label': label, 'color': color_hex, 'marker': layer_marker, 'picker': True, 's': base_point_size, 'alpha': base_point_alpha, 'zorder': 3 }
                        if is_emphasized:
                            if magnify_enabled: scatter_kwargs['s'] = base_point_size * (magnify_percent / 100.0)
                            if outline_enabled: scatter_kwargs['edgecolor'] = 'black'; scatter_kwargs['linewidth'] = outline_width
                            if opacity_enabled:
                                new_alpha = base_point_alpha + (opacity_change_percent / 100.0)
                                scatter_kwargs['alpha'] = min(1.0, max(0.0, new_alpha))
                            scatter_kwargs['zorder'] = 5

                        collection = ax.scatter(f2, f1, **scatter_kwargs)
                        # 将原始数据和归一化后的数据都存起来，用于悬浮提示
                        hover_data = pd.DataFrame({f1_col: f1_raw, f2_col: f2_raw, 'f1_norm': f1, 'f2_norm': f2})
                        self.plotted_collections.append({'collection': collection, 'label': label, 'data': hover_data})
                        has_any_visible_data = True

                        if layer_config.get('mean_enabled', False):
                            self._plot_mean(f2, f1, ax, color_hex, layer_config, is_emphasized, magnify_enabled, magnify_percent)
                        if layer_config.get('ellipse_enabled', False) and len(f1) > 2:
                            self._plot_ellipse(f2, f1, ax, color_hex, layer_config, is_emphasized, outline_enabled, outline_width, opacity_enabled, opacity_change_percent)
                
                else:
                    plot_df = df.dropna(subset=[f1_col, f2_col])
                    if plot_df.empty: continue
                    
                    f1_raw, f2_raw = plot_df[f1_col], plot_df[f2_col]
                    # --- [核心修改 3] 应用归一化 ---
                    if norm_method == "Z-Score (按图层)":
                        f1 = (f1_raw - f1_mean) / f1_std
                        f2 = (f2_raw - f2_mean) / f2_std
                    else:
                        f1, f2 = f1_raw, f2_raw
                    # --- 结束修改 ---

                    label = layer_config['name']
                    color_hex = QColor(Qt.darkGray).name()
                    
                    collection = ax.scatter(f2, f1, label=label, color=color_hex, marker=layer_marker, s=base_point_size, alpha=base_point_alpha, picker=True)
                    hover_data = pd.DataFrame({f1_col: f1_raw, f2_col: f2_raw, 'f1_norm': f1, 'f2_norm': f2})
                    self.plotted_collections.append({'collection': collection, 'label': label, 'data': hover_data})
                    has_any_visible_data = True
            
            if has_any_visible_data:
                self.hover_annotation = ax.text(0.98, 0.98, '', transform=ax.transAxes, ha='right', va='top', fontsize=9, bbox=dict(boxstyle='round,pad=0.3', fc='wheat', alpha=0.9))
                self.hover_annotation.set_visible(False)
            
            ax.set_title(self.title_edit.text(), fontsize=14)
            # --- [核心修改 4] 动态设置轴标签 ---
            if norm_method == "Z-Score (按图层)":
                ax.set_xlabel("F2 (Z-Score)")
                ax.set_ylabel("F1 (Z-Score)")
            else: # 原始值
                ax.set_xlabel(self.xlabel_edit.text())
                ax.set_ylabel(self.ylabel_edit.text())
            # --- 结束修改 ---
            ax.grid(True, linestyle='--', alpha=0.6)
            
            try:
                if self.x_min_edit.text() and self.x_max_edit.text(): ax.set_xlim(float(self.x_min_edit.text()), float(self.x_max_edit.text()))
                if self.y_min_edit.text() and self.y_max_edit.text(): ax.set_ylim(float(self.y_min_edit.text()), float(self.y_max_edit.text()))
            except ValueError: pass
            
            if self.flip_x_check.isChecked(): ax.invert_xaxis()
            if self.flip_y_check.isChecked(): ax.invert_yaxis()
            
            if self.show_legend_check.isChecked() and has_any_visible_data:
                ax.legend(fontsize='small', labelspacing=1.2)

            self.figure.tight_layout(pad=1.5)
            self.canvas.draw()
            
        except Exception as e:
            import traceback; traceback.print_exc()
            QMessageBox.critical(self, "绘图失败", f"生成图表时发生错误: {e}\n\n请检查数据列和图层配置。")
            self.figure.clear(); self.canvas.draw()

    def _plot_mean(self, x, y, ax, color_hex, layer_config, is_emphasized=False, 
                   magnify_enabled=False, magnify_percent=150):
        """
        [v6.1 - 签名修复版]
        绘制平均值点，并应用强调放大效果。
        - 修复了因函数签名未同步更新导致的 positional arguments 错误。
        """
        mean_x, mean_y = x.mean(), y.mean()
        marker_char = self.MARKER_STYLES.get(layer_config.get('mean_marker', '加号'), '+')
        mean_size = layer_config.get('mean_size', 100)
        
        # 只有在分组被强调且放大功能启用时，才应用放大效果
        if is_emphasized and magnify_enabled:
            mean_size *= (magnify_percent / 100.0)

        kwargs = {'color': color_hex, 's': mean_size, 'marker': marker_char, 'zorder': 10}
        # 为非线条型标记添加白色边框以增加可见性
        if marker_char not in ['+', 'x', '|', '_']: 
            kwargs.update({'edgecolors': 'white', 'linewidths': 1.5})
            
        ax.scatter(mean_x, mean_y, **kwargs)

    def _plot_ellipse(self, x, y, ax, color_hex, layer_config, is_emphasized=False,
                      outline_enabled=False, outline_width=1.5, opacity_enabled=False, opacity_change_percent=50):
        """
        [v6.1 - 签名修复版]
        绘制标准差椭圆，并应用强调的轮廓和透明度效果。
        - 修复了因函数签名未同步更新导致的 positional arguments 错误。
        """
        cov = np.cov(x, y)
        mean_x, mean_y = np.mean(x), np.mean(y)
        lambda_, v = np.linalg.eig(cov)
        lambda_ = np.sqrt(lambda_)
        std_multiplier = float(layer_config.get('ellipse_std', '2 (95%)').split()[0])
        
        # 基础样式
        line_width = layer_config.get('ellipse_width', 2)
        edge_color_q = QColor(color_hex)
        
        # 只有在分组被强调时，才应用强调样式
        if is_emphasized:
            if outline_enabled:
                # 使用全局设置的轮廓线宽
                line_width = outline_width
            if opacity_enabled:
                # 调整颜色的alpha值
                current_alpha = edge_color_q.alpha()
                opacity_change = (opacity_change_percent / 100.0) * 255
                new_alpha = int(min(255, max(0, current_alpha + opacity_change)))
                edge_color_q.setAlpha(new_alpha)

        # Matplotlib 需要 (R, G, B, A) 格式的颜色，其中每个值都在 [0, 1] 区间
        edgecolor_rgba = (edge_color_q.redF(), edge_color_q.greenF(), edge_color_q.blueF(), edge_color_q.alphaF())

        ell = Ellipse(
            xy=(mean_x, mean_y),
            width=lambda_[0] * std_multiplier * 2,
            height=lambda_[1] * std_multiplier * 2,
            angle=np.rad2deg(np.arccos(v[0, 0])),
            edgecolor=edgecolor_rgba,
            facecolor='none',
            linestyle=self.LINE_STYLES.get(layer_config.get('ellipse_style', '实线'), '-'),
            linewidth=line_width
        )
        ax.add_patch(ell)

    # ==========================================================================
    # UI状态更新和辅助方法
    # ==========================================================================
    def _update_ui_state(self):
        """根据当前图层数据和选中状态更新UI控件的可用性。"""
        has_layers = bool(self.layers)
        
        # [核心修复] 在访问 self.layers 前，检查索引的有效性
        is_layer_selected = (
            self.current_selected_layer_index >= 0 and
            self.current_selected_layer_index < len(self.layers)
        )
        
        self.plot_button.setEnabled(has_layers)
        
        # [核心修复] 只有在索引有效时，才启用右侧面板
        self.layer_settings_group.setEnabled(is_layer_selected)

        has_active_grouping_in_selected_layer = False
        if is_layer_selected: # 只有在索引有效时才继续
            layer = self.layers[self.current_selected_layer_index] # 现在这一行是安全的
            group_col = layer.get('group_col')
            df = layer.get('df')
            if df is not None and group_col and group_col != "无分组" and group_col in df.columns:
                if not df[group_col].dropna().empty:
                    has_active_grouping_in_selected_layer = True

        # --- [核心修正] ---
        # 现在这些都是 self 的属性，可以直接访问
        self.grouping_group.setVisible(has_active_grouping_in_selected_layer)
        self.mean_group.setEnabled(has_active_grouping_in_selected_layer)
        self.ellipse_group.setEnabled(has_active_grouping_in_selected_layer)
        
        if not has_active_grouping_in_selected_layer:
            self.mean_group.setChecked(False)
            self.ellipse_group.setChecked(False)

    def _update_layer_settings_panel_title(self):
        """更新右侧图层设置面板的标题。"""
        current_row = self.layer_table.currentRow()
        if current_row > -1 and current_row < len(self.layers):
            self.layer_settings_group.setTitle(f"图层设置 ({self.layers[current_row]['name']})")
        else:
            self.layer_settings_group.setTitle("图层设置 (未选择图层)")

    def _show_context_menu(self, pos):
        """显示图表画布的右键上下文菜单。"""
        context_menu = QMenu(self)
        open_proj_action = context_menu.addAction(self.icon_manager.get_icon("open_folder"), "打开工程 (.pavp)...")
        save_proj_action = context_menu.addAction(self.icon_manager.get_icon("save_as"), "保存工程 (.pavp)...")
        context_menu.addSeparator()
 
        # 核心操作
        refresh_action = context_menu.addAction("刷新图表")
        reset_view_action = context_menu.addAction("重置视图/缩放")
        if self.icon_manager: 
            refresh_action.setIcon(self.icon_manager.get_icon("refresh"))
            reset_view_action.setIcon(self.icon_manager.get_icon("zoom_selection")) 
        context_menu.addSeparator()

        # [新增] 离群点处理功能
        is_layer_selected = self.current_selected_layer_index != -1
        ignore_action = context_menu.addAction("框选忽略点 (当前图层)...")
        restore_action = context_menu.addAction("恢复所有忽略的点 (当前图层)")
        if self.icon_manager:
            ignore_action.setIcon(self.icon_manager.get_icon("select_object"))
            restore_action.setIcon(self.icon_manager.get_icon("undo"))
        ignore_action.setEnabled(is_layer_selected)
        restore_action.setEnabled(is_layer_selected)
        context_menu.addSeparator()
 
        # 导出操作
        copy_action = context_menu.addAction("复制图片到剪贴板")
        save_action = context_menu.addAction("保存图片...")
        if self.icon_manager:
            copy_action.setIcon(self.icon_manager.get_icon("copy"))
            save_action.setIcon(self.icon_manager.get_icon("save"))
            
        context_menu.addSeparator()
        
        # 清理操作
        clear_action = context_menu.addAction("清空所有图层...")
        if self.icon_manager:
            clear_action.setIcon(self.icon_manager.get_icon("clear_contents"))
 
        action = context_menu.exec_(self.canvas.mapToGlobal(pos))
        if action == open_proj_action: self._handle_open_project()
        elif action == save_proj_action: self._handle_save_project()        
        elif action == refresh_action: self._plot_data()
        elif action == reset_view_action: self._reset_view()
        elif action == ignore_action: self._start_ignore_selection() # [新增]
        elif action == restore_action: self._restore_ignored_points() # [新增]
        elif action == copy_action: self._copy_plot_to_clipboard()
        elif action == save_action: self._save_plot_image()
        elif action == clear_action: self._clear_all_data()


    def _handle_open_project(self):
        """处理“打开工程”的用户操作。"""
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
                import shutil
                shutil.rmtree(self.project_temp_dir, ignore_errors=True)
                self.project_temp_dir = None

    def _handle_save_project(self):
        """处理“保存工程”的用户操作。"""
        if not self.layers:
            QMessageBox.warning(self, "无内容", "没有可保存的图层。")
            return
            
        filepath, _ = QFileDialog.getSaveFileName(self, "保存工程文件", "未命名元音工程.pavp", "PhonAcq 工程文件 (*.pavp)")
        if not filepath: return

        try:
            self._save_project_to_file(self.plugin_id, filepath)
            if hasattr(self.parent(), 'statusBar'):
                self.parent().statusBar().showMessage(f"工程已保存到 '{os.path.basename(filepath)}'。", 3000)
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "保存工程失败", f"无法保存工程文件:\n{e}")

    def _restore_state_from_pavp(self, data, temp_dir):
        """
        [v1.3 - UI状态完全恢复版] 根据.pavp文件恢复整个对话框的状态。
        """
        import pandas as pd
        import librosa
        import textgrid
        from itertools import cycle
        from PyQt5.QtGui import QColor

        # 1. 清理当前状态并设置新的临时目录
        self._clear_all_data()
        self.project_temp_dir = temp_dir
        
        # 2. [核心修复] 完整地恢复所有全局UI设置
        # a. 恢复通用全局设置
        gs = data.get('global_settings', {})
        self.title_edit.setText(gs.get('title', '元音空间图'))
        self.xlabel_edit.setText(gs.get('xlabel', 'F2 (Hz)'))
        self.ylabel_edit.setText(gs.get('ylabel', 'F1 (Hz)'))
        self.show_legend_check.setChecked(gs.get('show_legend', True))
        
        x_range = gs.get('x_axis_range', {})
        self.x_min_edit.setText(x_range.get('min', ''))
        self.x_max_edit.setText(x_range.get('max', ''))
        
        y_range = gs.get('y_axis_range', {})
        self.y_min_edit.setText(y_range.get('min', ''))
        self.y_max_edit.setText(y_range.get('max', ''))

        # b. 恢复插件专属全局设置
        ps = data.get('plugin_specific_settings', {}).get(self.plugin_id, {})
        self.flip_x_check.setChecked(ps.get('flip_x_axis', True))
        self.flip_y_check.setChecked(ps.get('flip_y_axis', True))
        self.show_hover_info_check.setChecked(ps.get('show_hover_info', True))
        self.norm_combo.setCurrentText(ps.get('normalization_method', "原始值 (Hz)"))
        
        emphasis_magnify = ps.get('emphasis_magnify', {})
        self.emphasis_magnify_check.setChecked(emphasis_magnify.get('enabled', True))
        self.emphasis_magnify_slider.setValue(emphasis_magnify.get('value', 150))
        
        emphasis_outline = ps.get('emphasis_outline', {})
        self.emphasis_outline_check.setChecked(emphasis_outline.get('enabled', True))
        self.emphasis_outline_width_slider.setValue(emphasis_outline.get('value', 15))

        emphasis_opacity = ps.get('emphasis_opacity', {})
        self.emphasis_opacity_check.setChecked(emphasis_opacity.get('enabled', True))
        self.emphasis_opacity_slider.setValue(emphasis_opacity.get('value', 50))
        
        # 3. 循环恢复每个图层的数据模型 (此部分逻辑已正确，无需修改)
        for layer_json in data.get('layers', []):
            if layer_json.get('type') != self.PLUGIN_LAYER_TYPE:
                continue
            
            # a. 合并通用配置和插件专属配置
            layer_config = {}
            layer_config.update(layer_json.get('config', {}))
            layer_config.update(layer_json.get('plugin_specific_config', {}).get(self.plugin_id, {}))
            layer_config['id'] = layer_json.get('id', str(uuid.uuid4()))
            layer_config['name'] = layer_json.get('name', '未命名图层')

            # b. 读取DataFrame
            if layer_json.get('data_source_path'):
                csv_path = os.path.join(temp_dir, layer_json['data_source_path'])
                if os.path.exists(csv_path):
                    layer_config['df'] = pd.read_csv(csv_path)
                    layer_config['data_filename'] = f"{layer_json['name']} (来自工程)"

            # c. 处理TextGrid
            predefined_tg_path = None
            if layer_json.get('textgrid_path'):
                predefined_tg_path = os.path.join(temp_dir, layer_json['textgrid_path'])
            self._auto_match_textgrid_for_layer(layer_config, predefined_tg_path)

            # d. 加载音频数据
            if layer_json.get('audio_path'):
                audio_path_abs = os.path.join(temp_dir, layer_json['audio_path'])
                if os.path.exists(audio_path_abs):
                    try:
                        y, sr = librosa.load(audio_path_abs, sr=None, mono=True)
                        layer_config['audio_data'] = (y, sr)
                        layer_config['audio_path'] = audio_path_abs
                    except Exception as e:
                         print(f"Failed to load audio from project for layer '{layer_config['name']}': {e}")
            
            # e. 生成图层内部的分组信息
            df = layer_config.get('df')
            group_col = layer_config.get('group_col')
            if df is not None and group_col and group_col != "无分组" and group_col in df.columns:
                groups = sorted(df[group_col].dropna().astype(str).unique(), key=str)
                layer_config['groups'] = {}
                color_scheme_name = layer_config.get('color_scheme', '默认')
                color_scheme = self.COLOR_SCHEMES.get(color_scheme_name, self.COLOR_SCHEMES['默认'])
                color_cycle = cycle(color_scheme)
                for group_name in groups:
                    layer_config['groups'][group_name] = {'enabled': True, 'color': QColor(next(color_cycle)), 'emphasized': False}
            else:
                layer_config['groups'] = {}

            self.layers.append(layer_config)

        # 4. 所有数据加载完毕后，进行一次性的、彻底的UI刷新
        self._update_layer_table()

        if self.layers:
            self.layer_table.selectRow(0)
        
        # 最后，进行一次最终的重绘，这次重绘将使用所有已恢复的UI设置
        self._plot_data()

    def get_global_settings(self):
        """
        [v3.0 - 完整版] 辅助方法: 收集所有通用全局设置。
        """
        # [核心修复] 收集所有与坐标轴、标题、图例相关的通用UI状态
        return {
            "title": self.title_edit.text(),
            "xlabel": self.xlabel_edit.text(),
            "ylabel": self.ylabel_edit.text(),
            "show_legend": self.show_legend_check.isChecked(),
            "x_axis_range": {
                "min": self.x_min_edit.text(),
                "max": self.x_max_edit.text()
            },
            "y_axis_range": {
                "min": self.y_min_edit.text(),
                "max": self.y_max_edit.text()
            }
        }


    def get_plugin_specific_global_settings(self):
        """
        [v3.0 - 完整版] 辅助方法: 收集本插件专属的全局设置。
        """
        # [核心修复] 收集所有归一化和强调样式的专属UI状态
        return {
            "flip_x_axis": self.flip_x_check.isChecked(),
            "flip_y_axis": self.flip_y_check.isChecked(),
            "normalization_method": self.norm_combo.currentText(),
            "show_hover_info": self.show_hover_info_check.isChecked(),
            "emphasis_magnify": {
                "enabled": self.emphasis_magnify_check.isChecked(),
                "value": self.emphasis_magnify_slider.value()
            },
            "emphasis_outline": {
                "enabled": self.emphasis_outline_check.isChecked(),
                "value": self.emphasis_outline_width_slider.value()
            },
            "emphasis_opacity": {
                "enabled": self.emphasis_opacity_check.isChecked(),
                "value": self.emphasis_opacity_slider.value()
            }
        }
        
    def get_plugin_specific_layer_config(self, layer_config):
        """
        [v2.0 - 完整版] 辅助方法: 收集本插件专属的所有图层设置。
        """
        # [核心修复 2A] 返回一个包含所有右侧面板设置的完整字典
        return {
            "f1_col": layer_config.get('f1_col'),
            "f2_col": layer_config.get('f2_col'),
            "marker": layer_config.get('marker', '圆点'),
            "color_scheme": layer_config.get('color_scheme', '默认'),
            
            # 数据点样式
            "point_size": layer_config.get('point_size', 15),
            "point_alpha": layer_config.get('point_alpha', 0.3),
            
            # 平均值点
            "mean_enabled": layer_config.get('mean_enabled', False),
            "mean_marker": layer_config.get('mean_marker', '加号'),
            "mean_size": layer_config.get('mean_size', 100),
            
            # 标准差椭圆
            "ellipse_enabled": layer_config.get('ellipse_enabled', False),
            "ellipse_std": layer_config.get('ellipse_std', '2 (95%)'),
            "ellipse_style": layer_config.get('ellipse_style', '实线'),
            "ellipse_width": layer_config.get('ellipse_width', 2)
        }

    # [修改] closeEvent
    def closeEvent(self, event):
        """重写关闭事件，增加清理临时目录的逻辑。"""
        import shutil
        # ... (原有的停止播放器和清理临时音频片段目录的逻辑) ...
        try:
            if self.temp_audio_dir and os.path.exists(self.temp_audio_dir):
                shutil.rmtree(self.temp_audio_dir)
        except Exception as e:
            print(f"[Vowel Plotter Warning] Failed to clean up temp directory: {e}")
        
        # [新增] 清理工程临时目录
        if hasattr(self, 'project_temp_dir') and self.project_temp_dir:
            shutil.rmtree(self.project_temp_dir, ignore_errors=True)
            self.project_temp_dir = None
        
        super().closeEvent(event)

    def _start_ignore_selection(self):
        """激活矩形选择器以忽略数据点。"""
        if self.current_selected_layer_index < 0:
            QMessageBox.warning(self, "无操作对象", "请先在左侧列表中选择一个图层。")
            return
        
        ax = self.figure.gca()

        if self.show_ignore_mode_info:
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Information)
            msg_box.setWindowTitle("进入框选忽略模式")
            
            # [核心修改] 使用 setTextFormat(Qt.RichText) 来启用HTML解析
            msg_box.setTextFormat(Qt.RichText)
            
            msg_box.setText(
                "已进入框选忽略模式。<br><br>"  # 使用 <br> 替代 \n 效果更好
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

        rect_props = dict(
            facecolor='gray',     # 填充颜色改为灰色
            edgecolor='gray',     # 边框颜色改为灰色
            alpha=0.2,            # 保持半透明填充
            fill=True,
            linestyle='--'        # 线条样式改为虚线
        )

        self.rect_selector = RectangleSelector(
            ax,
            self._on_ignore_selection,
            useblit=True,
            button=[1],
            minspanx=5, minspany=5,
            spancoords='pixels',
            interactive=True,
            props=rect_props
        )
        self.canvas.setCursor(Qt.CrossCursor)

    def _on_ignore_selection(self, eclick, erelease):
        """
        [v7.1 - 归一化感知版]
        当用户完成一次矩形选择后的回调函数。
        此版本在判断时会应用与当前图表一致的归一化，确保坐标匹配。
        """
        x1, y1 = eclick.xdata, eclick.ydata
        x2, y2 = erelease.xdata, erelease.ydata

        if self.rect_selector:
            self.rect_selector.set_active(False)
        self.canvas.setCursor(Qt.ArrowCursor)

        layer_index = self.current_selected_layer_index
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

        f1_col = layer_config.get('f1_col')
        f2_col = layer_config.get('f2_col')
        group_col = layer_config.get('group_col')
        if not f1_col or not f2_col:
            self.canvas.draw()
            return

        # --- [核心修复] ---
        # 1. 获取当前的归一化方法
        norm_method = self.norm_combo.currentText()
        df_for_selection = df.copy() # 创建一个副本用于计算

        # 2. 如果是Z-Score，则对副本数据进行与绘图时完全相同的归一化
        if norm_method == "Z-Score (按图层)":
            f1_data = df_for_selection[f1_col].dropna()
            f2_data = df_for_selection[f2_col].dropna()
            f1_mean, f1_std = f1_data.mean(), f1_data.std()
            f2_mean, f2_std = f2_data.mean(), f2_data.std()
            if f1_std == 0 or np.isnan(f1_std): f1_std = 1
            if f2_std == 0 or np.isnan(f2_std): f2_std = 1
            
            # 创建新的归一化列用于坐标判断
            df_for_selection[f1_col + '_norm'] = (df_for_selection[f1_col] - f1_mean) / f1_std
            df_for_selection[f2_col + '_norm'] = (df_for_selection[f2_col] - f2_mean) / f2_std
            
            # 更新用于空间判断的列名
            f1_col_to_use = f1_col + '_norm'
            f2_col_to_use = f2_col + '_norm'
        else:
            # 如果是原始值，则直接使用原始列名
            f1_col_to_use = f1_col
            f2_col_to_use = f2_col
        # --- 结束修复 ---

        selected_items = self.group_table.selectedItems()
        selected_group_names = {item.text() for item in selected_items if item.column() == 0}

        min_x, max_x = min(x1, x2), max(x1, x2)
        min_y, max_y = min(y1, y2), max(y1, y2)
        
        # 使用正确的列进行空间判断
        spatial_mask = (
            (df_for_selection[f2_col_to_use] >= min_x) & (df_for_selection[f2_col_to_use] <= max_x) &
            (df_for_selection[f1_col_to_use] >= min_y) & (df_for_selection[f1_col_to_use] <= max_y)
        )

        final_mask = None
        if selected_group_names and group_col and group_col != "无分组" and group_col in df.columns:
            group_mask = df_for_selection[group_col].astype(str).isin(selected_group_names)
            final_mask = spatial_mask & group_mask
        else:
            final_mask = spatial_mask
        
        # 将掩码应用回原始的DataFrame 'df'
        # 我们使用 df_for_selection 的索引来定位 df 中的行
        indices_to_ignore = df_for_selection[final_mask].index
        
        num_ignored = len(indices_to_ignore)
        if num_ignored > 0:
            df.loc[indices_to_ignore, '_is_ignored'] = True
            print(f"[Vowel Plotter] Ignored {num_ignored} points in layer '{layer_config['name']}'.")
            
            self._plot_data()
            QMessageBox.information(self, "操作完成", f"已成功忽略 {num_ignored} 个数据点。")
        else:
            print("[Vowel Plotter] No points were selected to be ignored in the specified context.")
            # self.canvas.draw()  # 旧代码（可能不存在）
            self.canvas.draw_idle() # 使用 draw_idle() 来请求一次重绘，这会清除选择框

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
                print(f"[Vowel Plotter] Restored {num_restored} points in layer '{layer_config['name']}'.")
                self._plot_data()
                QMessageBox.information(self, "操作完成", f"已恢复 {num_restored} 个被忽略的点。")
            else:
                QMessageBox.information(self, "无需操作", "当前图层没有被忽略的点。")
        else:
            QMessageBox.information(self, "无需操作", "当前图层没有被忽略的点。")
 
    def _reset_view(self):
        """重置坐标轴范围并重绘图表。"""
        self.x_min_edit.clear()
        self.x_max_edit.clear()
        self.y_min_edit.clear()
        self.y_max_edit.clear()
        self._plot_data()
 
    def _copy_plot_to_clipboard(self):
        """将当前图表画布渲染为图片并复制到系统剪贴板。"""
        try: 
            pixmap = self.canvas.grab() # 抓取画布内容
            QApplication.clipboard().setPixmap(pixmap) # 复制到剪贴板
            # 可选：短暂的状态栏提示
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
        
        self.table_view.setModel(None) # 清空数据预览表格
        
        self._update_layer_table() # 更新图层列表UI (会清空列表)
        self._on_layer_selection_changed() # 模拟选择变化，清空右侧面板
        
        # 清空图表
        self.figure.clear()
        self.canvas.draw()
        
        self._update_ui_state() # 更新UI状态

    def _save_plot_image(self):
        """
        [v10.0 - 标准化路径版]
        保存图表为图片文件。
        - 默认保存路径现在是 `Results/analyze/charts/`。
        """
        title = self.title_edit.text()
        safe_filename = re.sub(r'[\\/*?:"<>|]', "_", title)
        
        # --- [核心修改] 获取标准的图表保存目录 ---
        charts_dir, _ = self._get_or_create_analysis_dirs()
        
        # 如果无法创建标准目录，则回退到 None，让 QFileDialog 使用默认位置
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

    # ==========================================================================
    # Matplotlib 交互相关方法
    # ==========================================================================
    def _on_mouse_press(self, event):
        """
        [v10.0 - 智能选择版]
        处理鼠标按下事件。
        - 左键单击数据点：自动在左侧面板选中对应的图层和分组。
        - 左键拖动空白区域：开始平移。
        """
        # 初始检查：确保事件有效且不在框选模式下
        if not event.inaxes or event.button != 1:
            return
        if self.rect_selector is not None and self.rect_selector.active:
            return
        self._pan_start_pixel_pos = event.guiEvent.pos()
        # --- 1. 尝试识别被点击的数据点 ---
        clicked_on_point = False
        # 遍历所有已绘制的数据点集合
        for plot_item in self.plotted_collections:
            collection = plot_item['collection']
            # 使用 Matplotlib 的 contains 方法检查鼠标事件是否落在该集合的某个点上
            contains, _ = collection.contains(event)
            if contains:
                # 如果点击到了一个点，提取其标签信息
                label = plot_item['label']
                
                # 标签格式为 "图层名 - 分组名" 或 "图层名"
                parts = label.split(' - ')
                target_layer_name = parts[0]
                target_group_name = parts[1] if len(parts) > 1 else None
                
                # 调用新的辅助函数来处理UI选择
                self._select_layer_and_group_by_name(target_layer_name, target_group_name)
                
                clicked_on_point = True
                break # 找到第一个匹配的点后就停止，避免处理重叠的点

        # --- 2. 如果没有点击到任何点，则执行平移操作 ---
        if not clicked_on_point:
            self._is_panning = True
            self._pan_start_pos = (event.xdata, event.ydata)
            self.canvas.setCursor(Qt.ClosedHandCursor)

    def _select_layer_and_group_by_name(self, layer_name, group_name):
        """
        [v1.1 - 强制刷新版]
        根据名称在UI中选中图层和分组。
        此版本确保在单选模式下，即使点击已选中的分组，也能强制刷新强调状态。
        """
        # --- 1. 选中图层 (逻辑不变) ---
        target_layer_row = -1
        for i, layer_config in enumerate(self.layers):
            if layer_config['name'] == layer_name:
                target_layer_row = i
                break
        
        if target_layer_row == -1:
            return

        if self.layer_table.currentRow() != target_layer_row:
            self.layer_table.blockSignals(True)
            self.layer_table.selectRow(target_layer_row)
            self._on_layer_selection_changed()
            self.layer_table.blockSignals(False)

        # --- 2. 选中分组 ---
        if not group_name:
            return
            
        QApplication.processEvents()
        
        target_group_row = -1
        for i in range(self.group_table.rowCount()):
            item = self.group_table.item(i, 0)
            if item and item.text() == group_name:
                target_group_row = i
                break
        
        if target_group_row != -1:
            # --- [核心修改] ---
            # 无论当前是否选中，都执行以下操作
            
            self.group_table.blockSignals(True)
            # 1. 以编程方式设置当前行，这会清除其他选择（在单选模式下）
            self.group_table.setCurrentCell(target_group_row, 0)
            
            # 2. 如果启用了自动强调，则强制调用其处理函数来更新高亮
            if self.auto_emphasize_check.isChecked():
                # _on_group_selection_changed 会读取 setCurrentCell 设置的当前行
                # 并正确地更新所有分组的 'emphasized' 状态
                self._on_group_selection_changed()
                
            self.group_table.blockSignals(False)
            # --- 修改结束 ---

    def _on_mouse_release(self, event):
        """
        [v2.0 - Click vs. Drag aware]
        处理鼠标释放事件。
        - 如果是拖动结束，则完成平移。
        - 如果是单击结束（移动距离很小），则取消所有强调。
        """
        if self._is_panning:
            # --- [核心修改] 区分单击和拖动 ---
            release_pos = event.guiEvent.pos()
            # 计算曼哈顿距离，比欧几里得距离计算更快
            distance = (release_pos - self._pan_start_pixel_pos).manhattanLength()

            # 如果移动距离小于5像素，我们认为这是一次单击
            if distance < 5:
                self._clear_emphasis_and_selection()
            
            # --- 修改结束 ---

            # 无论如何，结束平移状态
            self._is_panning = False
            self.canvas.setCursor(Qt.ArrowCursor)

    def _clear_emphasis_and_selection(self):
        """
        [新增] 取消当前选中图层中所有分组的强调状态，并清空UI选择。
        """
        # 仅在“选择即强调”模式下生效
        if not self.auto_emphasize_check.isChecked():
            return

        layer_row = self.current_selected_layer_index
        if layer_row < 0 or layer_row >= len(self.layers):
            return
            
        layer_config = self.layers[layer_row]
        all_groups = layer_config.get('groups', {})
        
        # 检查是否真的有需要取消的强调
        needs_update = any(settings.get('emphasized', False) for settings in all_groups.values())
        if not needs_update and self.group_table.selectedItems() == []:
             return

        # 1. 在数据模型中，将所有分组的 'emphasized' 状态设为 False
        for group_settings in all_groups.values():
            group_settings['emphasized'] = False
        
        # 2. 清空下方分组列表的UI选择
        self.group_table.clearSelection()
        
        # 3. 重新绘制图表以应用无强调的状态
        self._plot_data()
        
        # 4. 手动更新分组列表的图标（移除所有星标）
        self._update_group_table()

    def _on_mouse_move(self, event):
        """处理鼠标移动事件，用于平移或悬浮提示。"""
        # --- [核心修改] ---
        # 1. 检查总开关是否关闭，如果关闭则直接返回
        if not self.show_hover_info_check.isChecked():
            # 确保如果之前有残留的提示，它会被隐藏
            if self.hover_annotation and self.hover_annotation.get_visible():
                self.hover_annotation.set_visible(False)
                self.canvas.draw_idle()
            return
        # --- 修改结束 ---
        if self.rect_selector is not None and self.rect_selector.active:
            return
        # --- 优化结束 ---
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
            
            cur_xlim = ax.get_xlim() # 获取当前X轴范围
            cur_ylim = ax.get_ylim() # 获取当前Y轴范围
            
            # 设置新的轴范围
            ax.set_xlim(cur_xlim[0] - dx, cur_xlim[1] - dx)
            ax.set_ylim(cur_ylim[0] - dy, cur_ylim[1] - dy)
            self.canvas.draw_idle() # 异步重绘
            return # 拖动时不进行悬浮检测
            
        # --- 悬浮提示逻辑 ---
        if self.hover_annotation is None: return # 如果没有悬浮提示对象，则跳过
 
        found_point = False
        
        # 遍历所有已绘制的集合
        for plot_item in self.plotted_collections:
            collection = plot_item['collection']
            contains, ind = collection.contains(event) # 检查鼠标是否在某个点附近
            if contains:
                # 获取被悬浮点的数据
                data_index = ind['ind'][0] # 获取最近点的索引
                point_data = plot_item['data'].iloc[data_index] # 从原始数据中获取该行数据
                
                # 获取F1/F2的值（假设它们是DataFrame的前两列）
                f2_val = point_data.iloc[0] # F2是第一列
                f1_val = point_data.iloc[1] # F1是第二列
 
                # 更新悬浮文本
                label = plot_item['label']
                text = f"{label}\nF2: {f2_val:.1f} Hz\nF1: {f1_val:.1f} Hz"
                self.hover_annotation.set_text(text)
                self.hover_annotation.set_visible(True)
                self.canvas.draw_idle() # 异步重绘
                found_point = True
                break # 找到一个点就停止
        
        # 如果没有找到点，且之前是可见的，则隐藏
        if not found_point and self.hover_annotation.get_visible():
            self.hover_annotation.set_visible(False)
            self.canvas.draw_idle()

    
    def load_dataframe(self, df, source_name="来自外部模块", audio_filepath=None):
        """
        [v12.0 - 顽固Bug最终修复版]
        从外部加载 DataFrame，并将其作为新的图层。
        修复了分组依据后备逻辑错误覆盖TextGrid自动匹配结果的Bug。
        """
        if df is None or df.empty:
            QMessageBox.warning(self, "加载失败", "传入的 DataFrame 为空或无效。")
            return
        
        # 1. 计算唯一的图层名称
        base_name = source_name if source_name else "新数据"
        layer_name = base_name
        counter = 1
        while any(layer['name'] == layer_name for layer in self.layers):
            layer_name = f"{base_name} ({counter})"
            counter += 1
        
        # 2. 创建基础的图层配置字典
        new_layer_config = {
            "name": layer_name,
            "df": df,
            "data_filename": f"{source_name} (实时数据)",
            "enabled": True, "locked": False,
            "group_col": "无分组", # 预设为默认值
            "tg": None, "tg_filename": "未关联",
            "audio_path": None, "audio_data": None, "player": None,
            "point_size": 15, "point_alpha": 0.3, "marker": "圆点", 
            "mean_enabled": False, "ellipse_enabled": False, "color_scheme": "默认", "groups": {}
        }

        # 3. 自动加载传入的音频
        if audio_filepath and os.path.exists(audio_filepath):
            try:
                import librosa
                from PyQt5.QtMultimedia import QMediaPlayer
                y, sr = librosa.load(audio_filepath, sr=None, mono=True)
                new_layer_config['audio_path'] = audio_filepath
                new_layer_config['audio_data'] = (y, sr)
            except Exception as e:
                print(f"[Vowel Plotter ERROR] Failed to auto-load audio for layer '{layer_name}': {e}")
        
        # 步骤 4: 尝试通过TextGrid自动设置分组依据
        self._auto_match_textgrid_for_layer(new_layer_config)

        # --- [核心修复] ---
        # 步骤 5: 只有当自动匹配未能设置分组依据时，才执行后备的猜测逻辑。
        if not new_layer_config.get('group_col') or new_layer_config.get('group_col') == "无分组":
            all_cols = df.columns.tolist()
            default_group = next((c for c in all_cols if 'vowel' in c.lower() or 'label' in c.lower()), "无分组")
            new_layer_config['group_col'] = default_group
        # --- [修复结束] ---

        # 步骤 6: 自动检测 F1 和 F2 列
        numeric_cols = df.select_dtypes(include=np.number).columns.tolist()
        f1_auto = next((c for c in numeric_cols if 'f1' in c.lower()), numeric_cols[0] if numeric_cols else "")
        f2_auto = next((c for c in numeric_cols if 'f2' in c.lower()), numeric_cols[1] if len(numeric_cols) > 1 else "")
        new_layer_config['f1_col'] = f1_auto
        new_layer_config['f2_col'] = f2_auto

        # 步骤 7: 添加新图层并刷新UI
        self.layers.append(new_layer_config)
        self._update_layer_table()
        self._plot_data()

    def _auto_match_textgrid_for_layer(self, layer_config, predefined_tg_path=None):
        """
        [v12.0 - Tier选择同步版]
        为一个图层自动查找、加载并应用TextGrid。
        - 优先使用 predefined_tg_path (用于加载工程)。
        - 自动选择找到的第一个IntervalTier作为默认分组依据。
        """
        import re
        import textgrid
        import pandas as pd

        found_tg_path = None
        # 1. 优先使用预设路径
        if predefined_tg_path and os.path.exists(predefined_tg_path):
            found_tg_path = predefined_tg_path
        else:
            # 2. 否则，在标准目录中扫描
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

        # 3. 如果找到文件，则加载并应用
        if found_tg_path:
            print(f"[Vowel Plotter] Auto-matching TextGrid for layer '{layer_config['name']}' from '{os.path.basename(found_tg_path)}'")
            try:
                tg_object = textgrid.TextGrid.fromFile(found_tg_path)
                df = layer_config.get('df')

                if df is not None and 'timestamp' in df.columns:
                    # --- [核心分层逻辑] ---
                    first_interval_tier = next((tier for tier in tg_object if isinstance(tier, textgrid.IntervalTier)), None)
                    
                    if first_interval_tier:
                        if 'textgrid_label' in df.columns: df.drop(columns=['textgrid_label'], inplace=True)
                        
                        new_col_name = first_interval_tier.name
                        label_col = pd.Series(np.nan, index=df.index, dtype=object)
                        for interval in first_interval_tier:
                            if interval.mark:
                                mask = (df['timestamp'] >= interval.minTime) & (df['timestamp'] < interval.maxTime)
                                label_col.loc[mask] = interval.mark
                        df[new_col_name] = label_col

                        # 更新传入的配置字典
                        layer_config['tg'] = tg_object
                        layer_config['tg_filename'] = os.path.basename(found_tg_path)
                        layer_config['original_tg_path'] = found_tg_path 
                        layer_config['tg_tier'] = new_col_name # 保存使用的Tier
                        layer_config['group_col'] = new_col_name # 自动设为分组依据
                    # --- [逻辑结束] ---
                else:
                    print(f"[Vowel Plotter WARNING] Layer '{layer_config['name']}' has a matching TextGrid, but its DataFrame is missing a 'timestamp' column.")

            except Exception as e:
                print(f"[Vowel Plotter ERROR] Failed to load or apply TextGrid '{found_tg_path}': {e}")

# ==============================================================================
# 插件主入口类
# ==============================================================================
class VowelSpacePlotterPlugin(BasePlugin):
    """
    元音空间绘图器插件。
    负责插件的生命周期管理，并创建/显示 PlotterDialog。
    """
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.plotter_dialog = None # 存储对话框实例，实现单例模式

    def setup(self):
        """插件初始化设置。"""
        if not LIBS_AVAILABLE:
            print("[Vowel Plotter Error] Missing dependencies. Plugin setup failed.")
            return False 
        # 尝试将插件实例注册到音频分析模块，以实现数据发送功能
        self.audio_analysis_page = getattr(self.main_window, 'audio_analysis_page', None)
        if self.audio_analysis_page and hasattr(self.audio_analysis_page, 'spectrogram_widget'):
            # 将插件实例传递给声谱图部件，以便它可以在需要时调用 load_dataframe
            self.audio_analysis_page.spectrogram_widget.vowel_plotter_plugin_active = self
            print("[Vowel Plotter] Successfully hooked into Audio Analysis module.")
        else:
            print("[Vowel Plotter] Running in standalone mode (Audio Analysis module not found or not ready).")
        return True

    def teardown(self):
        """插件卸载清理。"""
        # 从音频分析模块中解钩
        if hasattr(self, 'audio_analysis_page') and self.audio_analysis_page and hasattr(self.audio_analysis_page, 'spectrogram_widget'):
            if getattr(self.audio_analysis_page.spectrogram_widget, 'vowel_plotter_plugin_active', None) is self:
                self.audio_analysis_page.spectrogram_widget.vowel_plotter_plugin_active = None
                print("[Vowel Plotter] Unhooked from Audio Analysis module.")
        # 关闭可能存在的对话框
        if self.plotter_dialog:
            self.plotter_dialog.close()
            self.plotter_dialog = None
        print("[Vowel Plotter] Plugin has been torn down.")

    def execute(self, **kwargs):
        """
        [v3.0 - 音频路径感知版]
        插件执行入口点。现在可以接收并处理传入的 audio_filepath。
        """
        # 实现单例模式：如果对话框已存在则显示，否则创建新的
        if self.plotter_dialog is None:
            self.plotter_dialog = PlotterDialog(parent=self.main_window, icon_manager=getattr(self.main_window, 'icon_manager', None))
            self.plotter_dialog.finished.connect(self._on_dialog_finished)
        
        # --- [核心修改] ---
        # 1. 从 kwargs 中安全地提取所有可能的数据
        dataframe_to_load = kwargs.get('dataframe')
        source_name = kwargs.get('source_name')
        audio_filepath = kwargs.get('audio_filepath') # 新增

        # 2. 检查是否有 DataFrame 参数传入
        if dataframe_to_load is not None:
            # 3. 将所有提取到的信息一起传递给对话框的加载方法
            self.plotter_dialog.load_dataframe(
                dataframe_to_load, 
                source_name, 
                audio_filepath=audio_filepath # 新增
            )

        # 显示对话框并将其置于顶层
        self.plotter_dialog.show()
        self.plotter_dialog.raise_()
        self.plotter_dialog.activateWindow()

    def _on_dialog_finished(self):
        """绘图器对话框关闭时的回调。"""
        self.plotter_dialog = None

# --- END OF MODIFIED FILE ---