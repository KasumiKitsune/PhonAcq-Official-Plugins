# --- START OF COMPLETE AND REFACTORED FILE plugins/intonation_visualizer/visualizer.py ---

import os
import sys
import uuid
import pandas as pd
import numpy as np
from itertools import cycle
from copy import deepcopy
import re

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
        """
        初始化图层配置对话框。
        :param existing_config: 一个可选的字典，包含现有图层的配置，用于编辑模式。
        :param parent: 父 QWidget。
        """
        super().__init__(parent)
        self.df = None # 存储加载的 F0 数据 DataFrame
        self.tg = None # [NEW] 存储加载的 TextGrid 对象
        # 深度复制一份配置，避免直接修改传入的字典，确保“取消”操作不影响原数据
        self.config = deepcopy(existing_config) if existing_config else {}
        self.parent_dialog = parent # 保存对主对话框 (VisualizerDialog) 的引用

        self.setWindowTitle("配置数据图层")
        self.setMinimumWidth(500)
        self._init_ui()
        self._connect_signals()
        if self.config:
            self._populate_from_config() # 如果是编辑现有图层，则填充UI
        
        # 确保在初始化时更新组合框，即使没有预设配置
        # 这也处理了初始加载df时自动填充列名的情况
        self._update_combos()

    def _init_ui(self):
        """初始化对话框的用户界面。"""
        layout = QVBoxLayout(self)
        form_layout = QFormLayout()
        
        # 图层名称输入框
        self.name_edit = QLineEdit(self.config.get('name', ''))
        self.name_edit.setPlaceholderText("例如：陈述句-男声")
        self.name_edit.setToolTip("为该数据图层指定一个唯一的名称。")
        
        # 数据文件选择 (Excel 或 CSV)
        data_layout = QHBoxLayout()
        self.load_data_btn = QPushButton("选择文件...")
        self.load_data_btn.setToolTip("加载包含时间与F0数据的 Excel (.xlsx, .xls) 或 CSV (.csv) 文件。")
        self.data_file_label = QLabel(self.config.get('data_filename', "未选择"))
        self.data_file_label.setWordWrap(True)
        data_layout.addWidget(self.load_data_btn); data_layout.addWidget(self.data_file_label, 1)

        # [NEW] TextGrid 文件选择
        tg_layout = QHBoxLayout()
        self.load_tg_btn = QPushButton("选择文件...")
        self.load_tg_btn.setToolTip("加载 TextGrid (.TextGrid) 文件为数据点添加标签。\n数据文件必须包含 'timestamp' 列。")
        self.tg_file_label = QLabel(self.config.get('tg_filename', "未选择 (可选)"))
        self.tg_file_label.setWordWrap(True)
        tg_layout.addWidget(self.load_tg_btn); tg_layout.addWidget(self.tg_file_label, 1)

        # 数据列指定（时间, F0, 分组）
        self.time_combo = QComboBox(); self.time_combo.setToolTip("选择代表时间的数据列，将作为图表的 X 轴。")
        self.f0_combo = QComboBox(); self.f0_combo.setToolTip("选择代表基频 (F0) 的数据列，将作为图表的 Y 轴。")
        
        # [MODIFIED] 分组依据现在是 QComboBox，允许选择 DataFrame 中的列
        self.group_by_combo = QComboBox()
        self.group_by_combo.setToolTip("选择用于对数据点进行分组的列。\n选择'无分组'则所有点使用相同样式。\n使用TextGrid后，可选择 'textgrid_label' 进行分组。")
        
        # 将所有控件添加到表单布局
        form_layout.addRow("图层名称:", self.name_edit)
        form_layout.addRow("数据文件:", data_layout)
        form_layout.addRow("TextGrid:", tg_layout) # [NEW] 将 TextGrid 行添加到布局
        form_layout.addRow(QFrame(frameShape=QFrame.HLine)) # 分隔线
        form_layout.addRow("时间 (X轴):", self.time_combo)
        form_layout.addRow("F0 (Y轴):", self.f0_combo)
        form_layout.addRow("分组依据:", self.group_by_combo) # [MODIFIED] 使用 QComboBox

        layout.addLayout(form_layout)
        
        # 标准确定/取消按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept); button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _connect_signals(self):
        """连接UI控件的信号到槽函数。"""
        self.load_data_btn.clicked.connect(self._load_data)
        self.load_tg_btn.clicked.connect(self._load_textgrid) # [NEW] 连接加载 TextGrid 按钮

    def _populate_from_config(self):
        """根据传入的配置字典填充UI控件。"""
        self.df = self.config.get('df')
        self.tg = self.config.get('tg') # [NEW] 恢复 TextGrid 对象
        
        # 恢复文件名标签显示
        if 'data_filename' in self.config: self.data_file_label.setText(self.config['data_filename'])
        if 'tg_filename' in self.config: self.tg_file_label.setText(self.config['tg_filename']) # [NEW]
        
        self._update_combos() # 先更新组合框内容
        
        # 再设置当前选中项
        self.time_combo.setCurrentText(self.config.get('time_col', ''))
        self.f0_combo.setCurrentText(self.config.get('f0_col', ''))
        self.group_by_combo.setCurrentText(self.config.get('group_col', '')) # [MODIFIED]

    def _load_data(self):
        """加载数据文件（Excel或CSV）到DataFrame。"""
        path, _ = QFileDialog.getOpenFileName(self, "选择F0数据文件", "", "表格文件 (*.xlsx *.xls *.csv)")
        if not path: return
        try:
            df = pd.read_excel(path) if path.lower().endswith(('.xlsx', '.xls')) else pd.read_csv(path)
            self.df = df
            self.data_file_label.setText(os.path.basename(path))
            self.config['data_filename'] = os.path.basename(path)
            
            # 如果图层名称为空，则使用文件名作为默认名称
            if not self.name_edit.text():
                self.name_edit.setText(os.path.splitext(os.path.basename(path))[0])
            
            # [MODIFIED] 当加载新的数据文件时，清除任何已加载的 TextGrid 信息，因为它们可能不再匹配
            self.tg = None
            self.tg_file_label.setText("未选择 (可选)")
            self.config.pop('tg_filename', None) # 从配置中移除旧的tg文件名

            self._update_combos() # 更新列选择组合框
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法读取数据文件: {e}"); self.df = None

    # [NEW] 从 plotter.py 移植的 TextGrid 加载方法
    def _load_textgrid(self):
        """加载 TextGrid 文件并将其标签应用到 DataFrame。"""
        # 检查是否已加载数据文件，且数据文件包含 'timestamp' 列
        if self.df is None or 'timestamp' not in self.df.columns:
            QMessageBox.warning(self, "需要时间戳", "请先加载一个包含 'timestamp' 列的数据文件。")
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择 TextGrid 文件", "", "TextGrid 文件 (*.TextGrid)")
        if not path: return
        try:
            self.tg = textgrid.TextGrid.fromFile(path)
            self.tg_file_label.setText(os.path.basename(path))
            self.config['tg_filename'] = os.path.basename(path)
            self._apply_textgrid() # 将 TextGrid 标注应用到 DataFrame
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法解析 TextGrid 文件: {e}"); self.tg = None

    # [NEW] 从 plotter.py 移植的 TextGrid 应用方法
    def _apply_textgrid(self):
        """将 TextGrid 的标注应用到 DataFrame，创建 'textgrid_label' 列。"""
        if self.df is None or self.tg is None: return
        
        # 如果已经存在 'textgrid_label' 列，则先移除
        if 'textgrid_label' in self.df.columns:
            self.df = self.df.drop(columns=['textgrid_label'])

        # 创建一个新的 Series 来存储 TextGrid 标签，初始为 NaN
        label_col = pd.Series(np.nan, index=self.df.index, dtype=object)
        
        # 遍历 TextGrid 的所有 Tier (层) 和 Interval (区间)
        for tier in self.tg:
            # 只处理 IntervalTier (包含时间区间的标注)
            if isinstance(tier, textgrid.IntervalTier):
                for interval in tier:
                    # 如果 Interval 有标注内容且非空
                    if interval.mark and interval.mark.strip(): 
                        # 使用时间戳匹配 DataFrame 中的数据点
                        mask = (self.df['timestamp'] >= interval.minTime) & (self.df['timestamp'] < interval.maxTime)
                        label_col.loc[mask] = interval.mark # 将标注赋给匹配的数据点
        
        self.df['textgrid_label'] = label_col # 将新的标签列添加到 DataFrame
        self._update_combos() # 更新组合框，以便选择新的 'textgrid_label' 列
        self.group_by_combo.setCurrentText('textgrid_label') # 自动选中 TextGrid 标签作为分组

    def _update_combos(self):
        """[MODIFIED] 根据当前加载的 DataFrame 更新所有列选择的下拉选项。"""
        self.time_combo.clear(); self.f0_combo.clear(); self.group_by_combo.clear()
        self.group_by_combo.addItem("无分组") # 默认分组选项

        if self.df is None: return

        numeric_cols = self.df.select_dtypes(include=np.number).columns.tolist()
        all_cols = self.df.columns.tolist() # 获取所有列名

        self.time_combo.addItems(numeric_cols); self.f0_combo.addItems(numeric_cols)
        
        # [MODIFIED] 现在将所有列都添加到分组依据的下拉框中，包括数值列和非数值列
        if all_cols: self.group_by_combo.addItems(all_cols)

        # 尝试自动选择时间/F0列
        time_auto = next((c for c in numeric_cols if 'time' in c.lower() or 'timestamp' in c.lower()), numeric_cols[0] if numeric_cols else "")
        f0_auto = next((c for c in numeric_cols if 'f0' in c.lower() or 'hz' in c.lower()), numeric_cols[1] if len(numeric_cols) > 1 else "")
        self.time_combo.setCurrentText(time_auto); self.f0_combo.setCurrentText(f0_auto)

        # 优先选择 'textgrid_label' 作为分组依据
        if 'textgrid_label' in all_cols: self.group_by_combo.setCurrentText('textgrid_label')
        else: 
            # 否则尝试选择包含 'group' 或 'label' 的列，最后回退到“无分组”
            self.group_by_combo.setCurrentText(next((c for c in all_cols if 'group' in c.lower() or 'label' in c.lower()), "无分组"))

    def get_layer_config(self):
        """
        获取当前对话框中配置的图层信息。
        返回一个字典，包含图层名称、DataFrame、TextGrid对象、列名等。
        """
        if self.df is None: QMessageBox.warning(self, "输入无效", "请先加载数据文件."); return None
        name = self.name_edit.text().strip()
        if not name: QMessageBox.warning(self, "输入无效", "请输入图层名称."); return None
        
        time_col = self.time_combo.currentText()
        f0_col = self.f0_combo.currentText()
        group_col = self.group_by_combo.currentText() # [MODIFIED] 从 QComboBox 获取分组列名

        if not time_col or not f0_col: QMessageBox.warning(self, "输入无效", "请为时间和F0指定数据列."); return None
        
        # 收集并保留样式设置 (这些设置将与图层绑定，但实际绘图时可能被全局设置覆盖)
        current_layer_settings = {
            "smoothing_enabled": self.config.get('smoothing_enabled', True),
            "smoothing_window": self.config.get('smoothing_window', 4),
            "show_points": self.config.get('show_points', False),
            "point_size": self.config.get('point_size', 10),
            "point_alpha": self.config.get('point_alpha', 0.4)
        }
        
        # 更新配置字典，并返回
        self.config.update({
            "name": name, 
            "df": self.df, 
            "tg": self.tg, # [NEW] 保存 TextGrid 对象
            "data_filename": self.data_file_label.text(), 
            "tg_filename": self.tg_file_label.text(), # [NEW] 保存 TextGrid 文件名用于显示
            "time_col": time_col, 
            "f0_col": f0_col, 
            "group_col": group_col, # [MODIFIED]
            "enabled": self.config.get('enabled', True), # 图层默认启用
            **current_layer_settings
        })
        return self.config

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
    SUPPORTED_EXTENSIONS = ('.csv', '.xlsx', '.xls', '.TextGrid') 

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
        
        # --- 核心数据结构 ---
        self.layers = [] # 存储所有图层的配置信息 (列表中的每个元素是一个字典)
        self.current_selected_layer_index = -1 # 当前在图层表格中选中的图层索引
        
        # [NEW] 全局分组设置：存储所有活跃分组的样式和启用状态
        self.global_groups = {} 
        # [NEW] 颜色循环器：为新添加的图层和新发现的分组分配默认颜色
        self.color_cycler = cycle(self.COLOR_SCHEMES['默认']) 
        
        self.plotted_lines = [] # 存储 Matplotlib 绘制的 Line2D 对象，用于鼠标交互 (悬浮提示)

        # --- 交互功能的状态变量 ---
        self._is_panning = False # 标记是否正在平移图表
        self._pan_start_pos = None # 平移起始点（数据坐标）
        self.hover_annotation = None # 用于显示鼠标悬停信息的文本对象

        # 初始化UI和连接信号
        self._init_ui()
        self._connect_signals()
        self._update_ui_state() # 初始化UI控件的可用状态
        
        # 拖拽功能设置
        self.setAcceptDrops(True)
        self._create_drop_overlay() # 创建拖拽提示覆盖层

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
        [MODIFIED] 创建左侧面板，包含图层管理器和绘图操作按钮。
        图层表格现在有两列：图层名称和分组依据。
        """
        panel = QWidget(); panel.setFixedWidth(450); layout = QVBoxLayout(panel)
        
        # 图层管理器组框
        layer_group = QGroupBox("图层管理器"); layer_layout = QVBoxLayout(layer_group)
        self.layer_table = QTableWidget()
        
        # [MODIFIED] 表格改为2列：图层名称和分组依据
        self.layer_table.setColumnCount(2)
        self.layer_table.setHorizontalHeaderLabels(["图层名称", "分组依据"])
        # 图层名称列拉伸，分组依据列可交互调整宽度
        self.layer_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.layer_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.layer_table.setColumnWidth(1, 120) # 初始宽度
        
        self.layer_table.setSelectionBehavior(QAbstractItemView.SelectRows); self.layer_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.layer_table.verticalHeader().setVisible(False); self.layer_table.setToolTip("右键单击进行操作，双击名称可配置图层。"); self.layer_table.setContextMenuPolicy(Qt.CustomContextMenu)
        
        # 添加新图层按钮
        btn_layout = QHBoxLayout(); self.add_layer_btn = QPushButton(" 添加新图层...")
        if self.icon_manager: self.add_layer_btn.setIcon(self.icon_manager.get_icon("add_row"))
        btn_layout.addWidget(self.add_layer_btn); btn_layout.addStretch()
        layer_layout.addWidget(self.layer_table); layer_layout.addLayout(btn_layout)
        
        # 绘图操作组框
        action_group = QGroupBox("绘图操作"); action_layout = QVBoxLayout(action_group)
        self.plot_button = QPushButton(" 更新图表");
        if self.icon_manager: self.plot_button.setIcon(self.icon_manager.get_icon("chart"))
        action_layout.addWidget(self.plot_button)
        
        layout.addWidget(layer_group, 1); layout.addWidget(action_group)
        return panel

    def _create_right_panel(self):
        """
        [MODIFIED] 创建右侧面板，包含全局设置、图层设置和全局分组样式面板。
        布局与 plotter.py 对齐。
        """
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFixedWidth(420); scroll.setFrameShape(QScrollArea.NoFrame)
        panel = QWidget(); layout = QVBoxLayout(panel); scroll.setWidget(panel)

        # --- 全局设置组框 ---
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

        # F0归一化设置
        self.norm_combo = QComboBox(); self.norm_combo.addItems(["原始值 (Hz)", "半音 (Semitone)", "Z-Score"])
        self.norm_combo.setToolTip("选择对F0值进行变换的方式：\n- 原始值: 不做任何处理。\n- 半音: 转换为对数尺度的半音，基准通常为100Hz或某个平均值。\n- Z-Score: 对F0进行标准化，消除个体音高差异。")
        
        self.st_ref_edit = QLineEdit("100"); self.st_ref_edit.setToolTip("半音归一化的参考基频，单位Hz。")
        self.st_param_widget = QWidget(); st_layout = QHBoxLayout(self.st_param_widget); st_layout.setContentsMargins(0,0,0,0); st_layout.addWidget(QLabel("基准(Hz):")); st_layout.addWidget(self.st_ref_edit)
        
        self.z_scope_combo = QComboBox(); self.z_scope_combo.addItems(["按分组", "按整个数据集"])
        self.z_scope_combo.setToolTip("Z-Score归一化的统计范围：\n- 按分组: 对每个分组内的F0数据独立计算均值和标准差。\n- 按整个数据集: 对所有可见图层的所有F0数据计算一个总体的均值和标准差。")
        self.z_param_widget = QWidget(); z_layout = QHBoxLayout(self.z_param_widget); z_layout.setContentsMargins(0,0,0,0); z_layout.addWidget(QLabel("范围:")); z_layout.addWidget(self.z_scope_combo)
        
        self.st_param_widget.setVisible(False); self.z_param_widget.setVisible(False) # 初始隐藏
        
        global_layout.addRow("F0归一化:", self.norm_combo); global_layout.addRow(self.st_param_widget); global_layout.addRow(self.z_param_widget)

        # --- 图层设置组框 (简化版) ---
        self.layer_settings_group = QGroupBox("图层设置 (未选择图层)")
        self.layer_settings_group.setEnabled(False) # 默认禁用
        layer_settings_layout = QVBoxLayout(self.layer_settings_group)
        
        # 曲线平滑设置
        self.smoothing_group = QGroupBox("曲线平滑 (移动平均)")
        self.smoothing_group.setCheckable(True); self.smoothing_group.setChecked(True)
        self.smoothing_group.setToolTip("勾选后，对当前图层的F0曲线进行移动平均平滑。")
        smoothing_layout = QFormLayout(self.smoothing_group)
        self.smoothing_window_slider = QSlider(Qt.Horizontal)
        self.smoothing_window_slider.setRange(1, 25); self.smoothing_window_slider.setValue(4) # 默认4，对应 (2*4+1)=9点窗口
        self.smoothing_window_slider.setToolTip("移动平均的窗口大小（半长），最终窗口大小为 2*值+1。\n值越大曲线越平滑。")
        self.smoothing_label = QLabel("窗口: 9 点") # 实时显示窗口大小
        smoothing_layout.addRow(self.smoothing_label, self.smoothing_window_slider)
        
        # 数据点显示设置
        self.display_group = QGroupBox("显示选项")
        display_layout = QFormLayout(self.display_group)
        self.show_points_check = QCheckBox("显示数据点"); self.show_points_check.setToolTip("勾选后，在当前图层的F0曲线上方显示原始的F0数据点。")
        self.point_size_slider = QSlider(Qt.Horizontal); self.point_size_slider.setRange(2, 50); self.point_size_slider.setValue(10); self.point_size_slider.setToolTip("调整当前图层数据点的大小。")
        self.point_alpha_slider = QSlider(Qt.Horizontal); self.point_alpha_slider.setRange(10, 100); self.point_alpha_slider.setValue(40); self.point_alpha_slider.setToolTip("调整当前图层数据点的不透明度，值越小越透明。")
        display_layout.addRow(self.show_points_check); display_layout.addRow("点大小:", self.point_size_slider); display_layout.addRow("点透明度:", self.point_alpha_slider)
        
        layer_settings_layout.addWidget(self.smoothing_group); layer_settings_layout.addWidget(self.display_group)

        # --- [NEW] 全局分组样式面板 (从 plotter.py 移植) ---
        self.grouping_group = QGroupBox("全局分组样式 (颜色区分)")
        self.grouping_group.setToolTip("为所有图层中具有相同标签的分组设置统一的颜色和显示状态。")
        grouping_layout = QVBoxLayout(self.grouping_group)
        
        # 颜色方案选择
        color_scheme_layout = QHBoxLayout(); self.color_scheme_combo = QComboBox(); self.color_scheme_combo.addItems(self.COLOR_SCHEMES.keys()); self.apply_color_scheme_btn = QPushButton("应用"); color_scheme_layout.addWidget(self.color_scheme_combo); color_scheme_layout.addWidget(self.apply_color_scheme_btn)
        
        # 动态生成的分组颜色和复选框会放在这里
        # 动态生成的分组颜色和复选框会放在这里
        self.group_settings_scroll = QScrollArea()
        self.group_settings_scroll.setWidgetResizable(True)
        self.group_settings_scroll.setFrameShape(QScrollArea.NoFrame)
        
        # --- [核心修复-步骤1] ---
        # 为滚动区域设置一个最小高度，确保即使只有一个分组时也不会太扁。
        # 150px 是一个比较合理的经验值。
        self.group_settings_scroll.setMinimumHeight(150)
        
        self.group_settings_widget = QWidget()
        self.group_settings_layout = QVBoxLayout(self.group_settings_widget)
        self.group_settings_scroll.setWidget(self.group_settings_widget)
        

        
        self.show_mean_contour_check = QCheckBox("显示分组平均轮廓"); self.show_mean_contour_check.setToolTip("勾选后，将为每个分组计算并绘制一条平均语调轮廓线。\n(必须勾选时间归一化)")
        
        grouping_layout.addLayout(color_scheme_layout); grouping_layout.addWidget(self.group_settings_scroll)
                # --- [核心修改-步骤1] 创建两个复选框 ---
        self.show_mean_contour_check = QCheckBox("显示分组平均轮廓")
        self.show_mean_contour_check.setToolTip("勾选后，将为每个分组计算并绘制一条平均语调轮廓线。\n(必须勾选时间归一化)")
        
        self.show_average_only_check = QCheckBox("仅显示平均值")
        self.show_average_only_check.setToolTip("勾选后，将隐藏所有原始的语调曲线，只显示平均轮廓线，\n便于观察总体趋势。")
        self.show_average_only_check.setEnabled(False) # 初始状态下禁用

        # --- [核心修改-步骤2] 创建一个水平布局来容纳它们 ---
        mean_contour_layout = QHBoxLayout()
        mean_contour_layout.addWidget(self.show_mean_contour_check)
        mean_contour_layout.addWidget(self.show_average_only_check)
        mean_contour_layout.addStretch() # 添加弹簧，将它们推到左侧
        
        grouping_layout.addLayout(color_scheme_layout)
        grouping_layout.addWidget(self.group_settings_scroll)
        grouping_layout.addLayout(mean_contour_layout) # <-- [核心修改] 添加新的水平布局

        # 将所有组框添加到右侧面板的主布局
        layout.addWidget(global_group); layout.addWidget(self.layer_settings_group); layout.addWidget(self.grouping_group); layout.addStretch()
        return scroll

    def _connect_signals(self):
        """连接所有UI控件的信号到槽函数。"""
        # 左侧面板 - 图层管理
        self.add_layer_btn.clicked.connect(self._add_layer)
        self.layer_table.customContextMenuRequested.connect(self._show_layer_context_menu) # 右键菜单
        self.layer_table.itemDoubleClicked.connect(self._on_layer_double_clicked) # 双击配置
        self.layer_table.itemChanged.connect(self._on_layer_renamed) # 重命名完成
        self.layer_table.itemSelectionChanged.connect(self._on_layer_selection_changed) # 选中行变化
        self.plot_button.clicked.connect(self._update_plot) # 更新图表
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

    def wheelEvent(self, event):
        """处理鼠标滚轮事件，用于缩放图表（Qt事件）。"""
        # 仅当鼠标在图表上且按下了Ctrl键时触发
        if self.canvas.underMouse() and event.modifiers() == Qt.ControlModifier:
            try:
                ax = self.figure.gca() # 获取当前激活的坐标轴
                
                # 将Qt事件的像素坐标转换为Matplotlib的数据坐标
                # event.pos() 给出的是 QPoint，event.x(), event.y() 给出的是 int
                # self.canvas.height() - event.y() 是因为Qt的Y轴向下，Matplotlib的Y轴向上
                x_pixel, y_pixel = event.x(), self.canvas.height() - event.y() 
                
                # Matplotlib的transform_point需要的是(x_pixel, y_pixel)
                trans = ax.transData.inverted()
                mouse_x, mouse_y = trans.transform_point((x_pixel, y_pixel))

                cur_xlim = ax.get_xlim(); cur_ylim = ax.get_ylim()

                # 根据滚轮方向确定缩放比例
                # event.angleDelta().y() > 0 表示向上滚动（放大），< 0 表示向下滚动（缩小）
                zoom_factor = 1.1 if event.angleDelta().y() > 0 else 1 / 1.1

                # 计算新的坐标轴范围，以鼠标位置为中心进行缩放
                new_xlim = [
                    mouse_x - (mouse_x - cur_xlim[0]) / zoom_factor,
                    mouse_x + (cur_xlim[1] - mouse_x) / zoom_factor
                ]
                new_ylim = [
                    mouse_y - (mouse_y - cur_ylim[0]) / zoom_factor,
                    mouse_y + (cur_ylim[1] - mouse_y) / zoom_factor
                ]

                ax.set_xlim(new_xlim); ax.set_ylim(new_ylim); self.canvas.draw()
            except Exception as e:
                print(f"Zoom failed: {e}")
        else:
            # 如果不是Ctrl+滚轮在图表上，则将事件传递给父类处理（例如滚动滚动条）
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
        """打开 LayerConfigDialog 添加新图层。"""
        dialog = LayerConfigDialog(parent=self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_layer_config()
            if config:
                # 添加新图层
                self.layers.append(config)
                self._update_layer_table() # 更新图层列表UI
                self._update_ui_state() # 更新UI状态（如按钮启用状态）
                self._update_all_group_settings() # [NEW] 扫描新图层以更新全局分组
                self._update_plot() # 重新绘图

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
        """配置指定行或当前选中行的图层。"""
        current_row = row_to_config if row_to_config is not None else self.layer_table.currentRow()
        if current_row < 0: return # 没有选中行或无效行
        
        config_to_edit = self.layers[current_row]
        dialog = LayerConfigDialog(existing_config=config_to_edit, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            new_config = dialog.get_layer_config() # 获取更新后的配置
            if new_config:
                self.layers[current_row] = new_config # 更新图层列表中的配置
                self._update_layer_table_row(current_row) # 只更新该行UI
                self._on_layer_selection_changed() # 模拟选择变化，刷新右侧面板
                self._update_all_group_settings() # [NEW] 更新全局分组
                self._update_plot() # 配置可能影响图表，重绘

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
        [MODIFIED] 更新图层表格中指定行的内容。
        现在有两列：图层名称和分组依据。
        """
        if row >= len(self.layers): return # 越界检查
        layer = self.layers[row]
        
        # 确保行存在
        if row >= self.layer_table.rowCount():
            self.layer_table.insertRow(row)
        
        # Column 0: 图层名称 (可编辑，包含显示/隐藏图标)
        name_item = QTableWidgetItem(layer['name'])
        name_item.setFlags(name_item.flags() | Qt.ItemIsEditable) 
        is_enabled = layer.get('enabled', True)
        if self.icon_manager:
            icon_name = "success" if is_enabled else "hidden"
            name_item.setIcon(self.icon_manager.get_icon(icon_name))
        
        # 设置 Tooltip (显示更多图层信息)
        tooltip_parts = [f"<b>图层: {layer['name']}</b><hr>"]
        df = layer.get('df')
        tooltip_parts.append(f"<b>数据源:</b> {layer.get('data_filename', 'N/A')} ({len(df)}点)" if df is not None else "<b>数据源:</b> 无")
        tooltip_parts.append(f"<b>TextGrid:</b> {layer.get('tg_filename', 'N/A')}" if layer.get('tg') else "<b>TextGrid:</b> 无") # [NEW]
        tooltip_parts.append(f"<b>时间列:</b> {layer.get('time_col', 'N/A')}")
        tooltip_parts.append(f"<b>F0列:</b> {layer.get('f0_col', 'N/A')}")
        tooltip_parts.append(f"<b>分组依据:</b> {layer.get('group_col', '无分组')}")
        name_item.setToolTip("\n".join(tooltip_parts))
        self.layer_table.setItem(row, 0, name_item)
        
        # Column 1: 分组依据 (不可直接编辑，但显示分组列名)
        group_item = QTableWidgetItem(layer.get('group_col', '无分组'))
        group_item.setFlags(group_item.flags() & ~Qt.ItemIsEditable) # 不在表格中直接编辑分组列
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
        [MODIFIED] 处理图层列表选中行变化。
        现在会根据复选框状态决定是显示完整数据还是仅显示有效标注数据。
        """
        self.current_selected_layer_index = self.layer_table.currentRow()
        row = self.current_selected_layer_index

        if row > -1 and row < len(self.layers):
            layer_config = self.layers[row]
            
            # --- [核心修复] ---
            df_to_show = layer_config.get('df', pd.DataFrame())

            # 只有当用户未勾选“显示所有数据”时，才进行过滤
            if not self.show_all_data_check.isChecked():
                # 检查是否存在 'textgrid_label' 列，并且 DataFrame 不为空
                if df_to_show is not None and not df_to_show.empty and 'textgrid_label' in df_to_show.columns:
                    # 使用 .dropna() 过滤掉 'textgrid_label' 列中值为 NaN 的行
                    df_to_show = df_to_show.dropna(subset=['textgrid_label'])

            # 更新数据预览表格
            self.table_view.setModel(PandasModel(df_to_show))
            
            # ... (填充右侧面板的逻辑保持不变) ...
            self._populate_layer_settings_panel(layer_config)
            self.layer_settings_group.setTitle(f"图层设置 ({layer_config['name']})")
            self.layer_settings_group.setEnabled(True)

        else:
            # 没有选中行或图层被移除，清空预览并禁用右侧面板
            self.table_view.setModel(None)
            self.layer_settings_group.setTitle("图层设置 (未选择图层)")
            self.layer_settings_group.setEnabled(False)
            
        self._update_ui_state()

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
        [NEW] 扫描所有启用图层，收集所有唯一的分组名称，并更新全局分组列表和UI。
        此方法会同步 `self.global_groups` 字典和右侧面板的 UI。
        """
        all_groups = set() # 存储所有图层中出现过的唯一分组名称
        for layer in self.layers:
            # 只考虑启用的图层
            if not layer.get('enabled', True): continue
            
            group_col = layer.get('group_col')
            df = layer.get('df')

            if df is not None and group_col and group_col != "无分组" and group_col in df.columns:
                # 收集该图层中所有唯一的分组名称 (转换为字符串并去除 NaN)
                all_groups.update(df[group_col].dropna().astype(str).unique())
        
        # 清除旧的 UI 控件 (避免重复)
        while self.group_settings_layout.count():
            child = self.group_settings_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()
        
        # 更新 self.global_groups 模型，保留旧设置（如颜色、启用状态）
        old_groups_copy = self.global_groups.copy()
        self.global_groups.clear()
        
        # 按字母顺序遍历所有唯一分组名称
        for group_name_str in sorted(list(all_groups), key=str):
            if group_name_str in old_groups_copy:
                # 如果是已知分组，则保留其旧设置
                self.global_groups[group_name_str] = old_groups_copy[group_name_str]
            else: # 如果是新发现的分组
                # 赋予默认启用状态和来自颜色循环器的新颜色
                self.global_groups[group_name_str] = {'enabled': True, 'color': QColor(next(self.color_cycler))}
        
        for group_name_str, settings in self.global_groups.items():
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            
            cb = QCheckBox(group_name_str)
            cb.setChecked(settings.get('enabled', True))
            # ... (设置 Tooltip 的逻辑) ...

            # --- [核心修复] ---
            # 1. 实例化我们新引入的 ColorWidget，而不是 ColorButton
            color_widget = ColorWidget(settings.get('color', QColor(Qt.black)))
            
            row_layout.addWidget(cb, 1)
            row_layout.addWidget(color_widget) # 将 ColorWidget 添加到布局
            self.group_settings_layout.addWidget(row_widget)
            
            # 2. 连接 ColorWidget 定义的、正确的 colorChanged(QColor) 信号
            #    这个信号只发射一个 QColor 参数，所以 lambda c: ... 是正确的
            cb.stateChanged.connect(lambda state, n=group_name_str: self._on_global_group_prop_changed(n, 'enabled', state == Qt.Checked))
            color_widget.colorChanged.connect(lambda c, n=group_name_str: self._on_global_group_prop_changed(n, 'color', c))

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
        [v2.2 - 图例修复最终版] 核心绘图逻辑。
        此版本通过动态收集绘图句柄，彻底解决了“仅显示平均值”时图例消失的问题，
        并能正确处理从列分组，使用全局分组颜色，以及将图例放置在图表外部。
        """
        try:
            # --- 1. 准备绘图环境 ---
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            self.plotted_lines.clear()
            self.hover_annotation = None
            has_any_visible_data = False
            grouped_data_for_mean_contour = {}

            # [核心修复] 创建一个临时的列表来动态收集所有需要显示在图例中的绘图句柄和标签
            legend_handles, legend_labels = [], []

            # --- 2. 全局Z-Score统计 (如果需要) ---
            global_mean, global_std = None, None
            if self.norm_combo.currentText() == "Z-Score" and self.z_scope_combo.currentText() == "按整个数据集":
                # 收集所有启用图层的F0数据来计算全局均值和标准差
                all_f0_data = []
                for layer_config in self.layers:
                    if not layer_config.get('enabled', True): continue
                    df = layer_config.get('df')
                    f0_col = layer_config.get('f0_col')
                    if df is not None and f0_col and f0_col in df.columns:
                        all_f0_data.append(df[f0_col].dropna())
                
                if all_f0_data:
                    all_f0 = pd.concat(all_f0_data)
                    global_mean, global_std = all_f0.mean(), all_f0.std()
                    if global_std == 0 or np.isnan(global_std): global_std = 1

            # --- 3. 遍历所有图层进行数据处理和绘图 ---
            for layer_config in self.layers:
                if not layer_config.get('enabled', True): continue # 跳过被禁用的图层
                
                df_original = layer_config.get('df')
                time_col, f0_col, group_col = layer_config.get('time_col'), layer_config.get('f0_col'), layer_config.get('group_col')

                if df_original is None or not all(c in df_original.columns for c in [time_col, f0_col]):
                    continue
                
                # 判断分组依据是否有效且在DataFrame中
                if group_col != "无分组" and group_col in df_original.columns:
                    plot_df_base = df_original.dropna(subset=[time_col, f0_col, group_col]).copy()
                    
                    # 遍历全局分组列表，按每个分组绘制曲线
                    for global_group_name_str, global_group_settings in self.global_groups.items():
                        # 只有当全局分组是启用的，并且该图层中有这个分组的数据时才绘制
                        if not global_group_settings.get('enabled', True): continue
                        
                        current_group_df = plot_df_base[plot_df_base[group_col].astype(str) == global_group_name_str].copy()
                        if current_group_df.empty: continue

                        # 处理数据 (归一化、平滑等)
                        df_processed = self._process_single_dataframe(current_group_df, time_col, f0_col, layer_config, global_mean, global_std)
                        if df_processed is None or df_processed.empty: continue

                        t_data, f0_data = df_processed[time_col], df_processed[f0_col]
                        color_hex = global_group_settings['color'].name() # 使用全局分组的颜色

                        # [核心修复] 只有当“仅显示平均值”未被勾选时，才绘制原始曲线和数据点
                        if not self.show_average_only_check.isChecked():
                            # 绘制线条，不再需要 label 参数
                            line, = ax.plot(t_data, f0_data, color=color_hex, zorder=10, picker=5)
                            
                            # 为悬浮提示和可能的其他交互保存数据
                            label = f"{layer_config['name']} - {global_group_name_str}"
                            self.plotted_lines.append({'line': line, 'label': label, 'data': df_processed[[time_col, f0_col]]})
                            has_any_visible_data = True
                            
                            # 绘制数据点
                            if layer_config.get('show_points', False): 
                                ax.scatter(t_data, f0_data, color=color_hex, s=layer_config.get('point_size', 10), alpha=layer_config.get('point_alpha', 0.4), zorder=5)
                        
                        # 无论是否显示原始曲线，都需要为计算平均轮廓准备数据
                        if global_group_name_str not in grouped_data_for_mean_contour: 
                            grouped_data_for_mean_contour[global_group_name_str] = {'curves': [], 'color': global_group_settings['color']}
                        grouped_data_for_mean_contour[global_group_name_str]['curves'].append(df_processed)
                
                else: # 如果没有有效的分组列，则作为无分组图层整体绘制
                    df_processed = self._process_single_dataframe(df_original.copy(), time_col, f0_col, layer_config, global_mean, global_std)
                    if df_processed is None or df_processed.empty: continue

                    if not self.show_average_only_check.isChecked(): # 无分组数据不计算平均值，所以直接判断
                        t_data, f0_data = df_processed[time_col], df_processed[f0_col]
                        label = layer_config['name']
                        color_hex = QColor(Qt.darkGray).name()
                        
                        line, = ax.plot(t_data, f0_data, color=color_hex, zorder=10, picker=5)
                        self.plotted_lines.append({'line': line, 'label': label, 'data': df_processed[[time_col, f0_col]]})
                        has_any_visible_data = True
                        if layer_config.get('show_points', False): 
                            ax.scatter(t_data, f0_data, color=color_hex, s=layer_config.get('point_size', 10), alpha=layer_config.get('point_alpha', 0.4), zorder=5)

            # --- 4. 绘制平均轮廓线（如果需要）---
            # 这个函数现在也会向 legend_handles 和 legend_labels 添加内容
            if self.show_mean_contour_check.isChecked() and self.normalize_time_check.isChecked():
                self._plot_mean_contours(ax, grouped_data_for_mean_contour, legend_handles, legend_labels)
                has_any_visible_data = True # 如果画了平均线，就认为有可见数据

            # --- 5. 设置悬浮提示 ---
            if has_any_visible_data:
                self.hover_annotation = ax.text(0.98, 0.98, '', transform=ax.transAxes,
                                                ha='right', va='top', fontsize=9,
                                                bbox=dict(boxstyle='round,pad=0.3', fc='wheat', alpha=0.9),
                                                zorder=100)
                self.hover_annotation.set_visible(False)

            # --- 6. 设置图表样式 ---
            ax.set_title(self.title_edit.text(), fontsize=14)
            ax.set_xlabel(self.xlabel_edit.text())
            ax.set_ylabel(self.ylabel_edit.text())
            ax.grid(True, linestyle='--', alpha=0.6)
            ax.autoscale_view()
            
            # --- 7. 最终的图例生成 ---
            if self.show_legend_check.isChecked():
                
                # [核心修复] 如果用户选择“仅显示平均值”，则图例只包含平均线
                # 否则，我们需要手动为原始曲线创建图例（因为我们没有在ax.plot中设置label）
                if not self.show_average_only_check.isChecked():
                    # 清空并重新生成图例句柄
                    legend_handles.clear()
                    legend_labels.clear()
                    
                    # 从 `self.global_groups` 中收集所有启用的分组信息
                    for group_name, settings in sorted(self.global_groups.items()):
                        if settings.get('enabled', True):
                            color = settings.get('color', QColor(Qt.black))
                            line = Line2D([0], [0], color=color.name(), lw=2) # 实线样本
                            legend_handles.append(line)
                            legend_labels.append(group_name)

                # 如果有任何有效的图例条目，则绘制图例
                if legend_handles:
                    ax.legend(handles=legend_handles, 
                              labels=legend_labels,
                              loc='center left', 
                              bbox_to_anchor=(1.02, 0.5),
                              fontsize='small',
                              labelspacing=1.2)

            # --- 8. 调整布局并重绘画布 ---
            # 调整布局以确保图例不会被裁切 (为右侧的图例留出15%的空间)
            self.figure.tight_layout(rect=[0, 0, 1, 1]) 
            self.canvas.draw()
            
        except Exception as e:
            QMessageBox.critical(self, "绘图失败", f"生成图表时发生错误: {e}\n\n请检查数据列和图层配置。")
            self.figure.clear()
            self.canvas.draw()

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
        """显示画布的右键上下文菜单。"""
        menu = QMenu(self)
        
        # 刷新图表和重置视图
        refresh_action = menu.addAction(self.icon_manager.get_icon("refresh"), "刷新图表")
        reset_view_action = menu.addAction(self.icon_manager.get_icon("zoom_selection"), "重置视图/缩放")
        menu.addSeparator()
        
        # 复制和保存图片
        copy_action = menu.addAction(self.icon_manager.get_icon("copy"), "复制图片到剪贴板")
        save_action = menu.addAction(self.icon_manager.get_icon("save"), "保存图片...")
        menu.addSeparator()
        
        # 清空所有图层
        clear_action = menu.addAction(self.icon_manager.get_icon("clear_contents"), "清空所有图层...")
        
        # 连接动作
        refresh_action.triggered.connect(self._update_plot)
        reset_view_action.triggered.connect(self._reset_view)
        copy_action.triggered.connect(self._copy_plot_to_clipboard)
        save_action.triggered.connect(self._save_plot_image)
        clear_action.triggered.connect(self._clear_all_data)
            
        menu.exec_(self.canvas.mapToGlobal(pos))
 
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
        """保存图表为图片文件。"""
        title = self.title_edit.text()
        safe_filename = re.sub(r'[\\/*?:"<>|]', "_", title) # 替换文件名中的非法字符
        
        file_path, _ = QFileDialog.getSaveFileName(
            self, 
            "保存图片", 
            f"{safe_filename}.png",
            "PNG图片 (*.png);;高分辨率PDF (*.pdf);;JPEG图片 (*.jpg);;SVG矢量图 (*.svg)"
        )
 
        if file_path:
            try: 
                # facecolor='white' 确保保存的图片背景是白色，而不是透明（默认）
                self.figure.savefig(file_path, dpi=300, bbox_inches='tight', facecolor='white')
                QMessageBox.information(self, "成功", f"图表已保存到:\n{file_path}")
            except Exception as e: 
                QMessageBox.critical(self, "保存失败", f"无法保存图片: {e}")

    # ==========================================================================
    # Matplotlib 交互相关方法
    # ==========================================================================
    def _on_mouse_press(self, event):
        """处理鼠标按下事件，用于开始平移。"""
        # 仅当鼠标在坐标轴内且使用左键时触发 (button=1)
        if event.inaxes and event.button == 1:
            self._is_panning = True
            self._pan_start_pos = (event.xdata, event.ydata) # 记录鼠标在数据坐标系中的起始位置
            self.canvas.setCursor(Qt.ClosedHandCursor) # 改变鼠标光标为抓手

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
        """
        try:
            if file_path.lower().endswith(('.xlsx', '.xls', '.csv')):
                # 加载数据文件，并打开配置对话框
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

            elif file_path.lower().endswith('.textgrid'):
                # 如果是 TextGrid 文件，尝试与当前选中的图层关联
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
                    
                    # 临时创建一个 LayerConfigDialog 来应用 TextGrid 并更新配置
                    temp_dialog = LayerConfigDialog(existing_config=layer_config, parent=self)
                    temp_dialog._apply_textgrid() # 强制应用 TextGrid
                    # 重新获取更新后的配置，并覆盖原图层配置
                    updated_config = temp_dialog.get_layer_config()
                    if updated_config:
                        self.layers[current_row] = updated_config
                        self._update_layer_table_row(current_row) # 刷新表格行
                        self._update_all_group_settings() # 刷新全局分组
                        self._update_plot() # 重绘
                        QMessageBox.information(self, "TextGrid 已应用", f"TextGrid '{os.path.basename(file_path)}' 已成功应用于图层 '{layer_config['name']}'。")
                except Exception as e:
                    QMessageBox.critical(self, "TextGrid 错误", f"无法加载或应用 TextGrid 文件: {e}")

            else:
                QMessageBox.warning(self, "文件类型不支持", f"文件 '{os.path.basename(file_path)}' 的类型不支持。请选择 .csv, .xlsx 或 .TextGrid 文件。")

        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法读取文件 '{os.path.basename(file_path)}':\n{e}")

    def add_data_source(self, df, source_name="从外部加载"):
        """
        [MODIFIED] 从外部（如音频分析模块）加载 DataFrame，并将其作为新的图层添加到可视化器中。
        此版本根据传入的 df 结构自动选择合适的列，并为图层设置默认样式。
        :param df: 要加载的 Pandas DataFrame。
        :param source_name: 数据的来源名称，用于生成默认图层名和文件名显示。
        """
        if df is None or df.empty:
            QMessageBox.warning(self, "数据无效", "传入的 DataFrame 为空或无效。")
            return
        
        # 检查是否包含必要的数值列 (时间, F0)
        numeric_cols = df.select_dtypes(include=np.number).columns.tolist()
        if len(numeric_cols) < 2:
            QMessageBox.warning(self, "数据格式错误", "传入的DataFrame至少需要两列数值型数据（时间和F0）。")
            return

        # 自动生成图层名称，确保唯一性
        base_name = os.path.splitext(source_name)[0] if source_name else "新数据"
        layer_name = base_name; counter = 1
        while any(layer['name'] == layer_name for layer in self.layers):
            layer_name = f"{base_name} ({counter})"; counter += 1
        
        # 创建一个新的图层配置，并尝试自动填充列名和默认样式
        new_layer_config = {
            "name": layer_name,
            "df": df,
            "tg": None, # 外部传入的 DataFrame 通常不带 TextGrid
            "data_filename": f"{source_name} (实时数据)",
            "tg_filename": "未选择 (可选)",
            "enabled": True, # 默认启用
            "time_col": "", # 待自动检测或用户选择
            "f0_col": "", # 待自动检测或用户选择
            "group_col": "无分组", # 初始无分组
            # 默认样式设置
            "smoothing_enabled": True, # 默认启用平滑
            "smoothing_window": 4,     # 默认4，对应9点窗口
            "show_points": False,      # 默认不显示点
            "point_size": 10,
            "point_alpha": 0.4
        }

        # 尝试自动选择时间/F0列
        new_layer_config['time_col'] = next((c for c in numeric_cols if 'time' in c.lower() or 'timestamp' in c.lower()), numeric_cols[0])
        new_layer_config['f0_col'] = next((c for c in numeric_cols if 'f0' in c.lower() or 'hz' in c.lower()), numeric_cols[1] if len(numeric_cols) > 1 else numeric_cols[0])

        # 尝试选择包含“group”或“label”的列作为默认分组
        all_cols = df.columns.tolist()
        new_layer_config['group_col'] = next((c for c in all_cols if 'group' in c.lower() or 'label' in c.lower() or 'category' in c.lower()), "无分组")

        self.layers.append(new_layer_config)
        self._update_layer_table() # 更新图层列表UI
        self._update_all_group_settings() # [NEW] 更新全局分组
        self._update_plot() # 重新绘图

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
        插件的统一入口。此版本修复了 source_name 未被正确处理的问题。
        """
        # 如果窗口不存在，则创建
        if self.visualizer_dialog is None:
            parent = self.main_window if hasattr(self, 'main_window') else None
            icon_manager = getattr(parent, 'icon_manager', None) if parent else None
            self.visualizer_dialog = VisualizerDialog(parent=parent, icon_manager=icon_manager)
            self.visualizer_dialog.finished.connect(self._on_dialog_finished)

        # 1. 从 kwargs 中提取 dataframe 和 source_name
        dataframe_to_load = kwargs.get('dataframe')
        source_name = kwargs.get('source_name') # 如果不存在，会是 None

        # 2. 检查是否有DataFrame参数传入
        if dataframe_to_load is not None:
            # 3. 将 dataframe 和 source_name 一起传递给对话框
            self.visualizer_dialog.add_data_source(dataframe_to_load, source_name)

        # 显示窗口
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