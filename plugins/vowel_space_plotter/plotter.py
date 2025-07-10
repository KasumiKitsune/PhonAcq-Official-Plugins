# --- START OF MODIFIED FILE plugins/vowel_space_plotter/plotter.py ---

import os
import sys
import re
import pandas as pd
import numpy as np
from itertools import cycle

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog,
                             QMessageBox, QTableView, QHeaderView, QComboBox, QCheckBox,
                             QSplitter, QGroupBox, QLineEdit, QFormLayout,
                             QColorDialog, QSlider, QWidget, QScrollArea, QMenu, QFrame, QGridLayout)
from PyQt5.QtCore import Qt, QAbstractTableModel, QSize, pyqtSignal
from PyQt5.QtGui import QIcon, QColor, QPalette, QPixmap, QFont

# --- Matplotlib 和 textgrid 库导入 (保持不变) ---
try:
    import matplotlib
    matplotlib.use('Qt5Agg')
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    from matplotlib.patches import Ellipse
    import textgrid

    def set_matplotlib_font():
        font_candidates = ['Microsoft YaHei', 'SimHei', 'Source Han Sans CN', 'PingFang SC', 'WenQuanYi Micro Hei', 'Arial Unicode MS']
        from matplotlib.font_manager import findfont, FontProperties
        found_font = next((font for font in font_candidates if findfont(FontProperties(family=font))), None)
        if found_font:
            matplotlib.rcParams['font.sans-serif'] = [found_font]; matplotlib.rcParams['axes.unicode_minus'] = False
            print(f"[Vowel Plotter] Found and set Chinese font: {found_font}")
        else:
            print("[Vowel Plotter Warning] No suitable Chinese font found.")
    set_matplotlib_font()
    LIBS_AVAILABLE = True
except ImportError as e:
    print(f"[Vowel Plotter Error] Missing required library: {e}. Please run 'pip install matplotlib textgrid'")
    LIBS_AVAILABLE = False

# --- 插件API导入等其他部分 (保持不变) ---
try:
    from modules.plugin_system import BasePlugin
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin
class PandasModel(QAbstractTableModel):
    def __init__(self, data): super().__init__(); self._data = data
    def rowCount(self, parent=None): return self._data.shape[0]
    def columnCount(self, parent=None): return self._data.shape[1]
    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role == Qt.DisplayRole: return str(self._data.iloc[index.row(), index.column()])
        return None
    def headerData(self, section, orientation, role):
        if role == Qt.DisplayRole:
            if orientation == Qt.Horizontal: return str(self._data.columns[section])
            if orientation == Qt.Vertical: return str(self._data.index[section])
        return None
class CustomColorPopup(QDialog):
    """一个简洁的弹出式调色板。"""
    colorSelected = pyqtSignal(QColor)
 
    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup)
 
        # ------------------- [核心修复] -------------------
        # 为弹窗自身设置一个固定的、中性的背景和边框，
        # 这样它就不会继承父控件(ColorButton)的背景色。
        self.setStyleSheet("""
            QDialog {
                background-color: white;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
            }
        """)
        # ----------------------------------------------------
 
        # 定义我们的调色板颜色 (保持不变)
        colors = [
            # --- Row 1: Vibrant Staples (鲜艳的标准色) ---
            '#d32f2f',  # Red
            '#f57c00',  # Orange
            '#4caf50',  # Green
            '#1976d2',  # Blue
            '#9c27b0',  # Purple
            '#e91e63',  # Pink
            
            # --- Row 2: Deeper Tones (对应的深色系) ---
            '#b71c1c',  # Dark Red
            '#e65100',  # Dark Orange
            '#1b5e20',  # Dark Green
            '#0d47a1',  # Dark Blue
            '#4a148c',  # Dark Purple
            '#880e4f',  # Dark Pink/Magenta
            
            # --- Row 3: Bright Tones (明亮的色调) ---
            '#fbc02d',  # Gold/Yellow
            '#8bc34a',  # Light Green
            '#00bcd4',  # Cyan
            '#03a9f4',  # Light Blue
            '#ff4081',  # Bright Pink
            '#ff9800',  # Amber
            
            # --- Row 4: Soft Pastels (柔和的粉彩色) ---
            '#ffcdd2',  # Pastel Red
            '#ffccbc',  # Pastel Orange (Peach)
            '#c8e6c9',  # Pastel Green
            '#bbdefb',  # Pastel Blue
            '#e1bee7',  # Pastel Purple
            '#fff9c4',  # Pastel Yellow
            
            # --- Row 5: Muted & Earthy (低饱和度/大地色) ---
            '#a1887f',  # Muted Brown
            '#795548',  # Brown
            '#8d6e63',  # Light Brown
            '#00897b',  # Teal
            '#455a64',  # Blue Grey
            '#546e7a',  # Slate Grey
            
            # --- Row 6: Grayscale Ramp (灰度色阶) ---
            '#ffffff',  # White
            '#eeeeee',  # Grey 100
            '#bdbdbd',  # Grey 400
            '#757575',  # Grey 600
            '#424242',  # Grey 800
            '#000000',  # Black
            
            # --- Row 7: Extra Accents (备用高亮色) ---
            '#cddc39',  # Lime
            '#673ab7',  # Deep Purple
            '#29b6f6',  # Sky Blue
            '#ff7043',  # Coral
            '#ec407a',  # Fuchsia
            '#7e57c2',  # Medium Purple
        ]
        
        layout = QGridLayout()
        layout.setSpacing(4)
        # 增加一点内边距，让色块不贴边
        layout.setContentsMargins(10, 10, 10, 10) 
        self.setLayout(layout)
        
        # 将颜色填充到网格中 (保持不变)
        cols = 6 
        for i, color_hex in enumerate(colors):
            row, col = divmod(i, cols)
            
            color_widget = QFrame()
            color_widget.setFixedSize(24, 24)
            # 注意这里对色块本身的样式也做了微调，使其边框更清晰
            color_widget.setStyleSheet(f"""
                QFrame {{
                    background-color: {color_hex};
                    border-radius: 4px;
                    border: 1px solid #e0e0e0;
                }}
                QFrame:hover {{
                    border: 2px solid #0078d7;
                }}
            """)
            color_widget.setCursor(Qt.PointingHandCursor)
            
            color_widget.mousePressEvent = lambda event, c=QColor(color_hex): self.on_color_click(c)
            
            layout.addWidget(color_widget, row, col)
 
    def on_color_click(self, color):
        self.colorSelected.emit(color)
        self.close()
class ColorButton(QLabel):
    """一个可点击的圆形标签，点击后会弹出自定义调色板。"""
    colorChanged = pyqtSignal()
 
    def __init__(self, color=Qt.black, parent=None):
        super().__init__(parent)
        self.setFixedSize(20, 20)
        self.clicked = self.colorChanged
        self.set_color(QColor(color))
        self.popup = None
 
    def set_color(self, color):
        self._color = QColor(color)
        self.setStyleSheet(
            f"background-color: {self._color.name()};"
            "border-radius: 10px;"
            "border: 1px solid #AAAAAA;"
        )
        self.setToolTip(f"点击选择颜色 (当前: {self._color.name()})")
        self.colorChanged.emit()
 
    def color(self):
        return self._color
 
    def mousePressEvent(self, event):
        """点击时显示自定义调色板。"""
        if event.button() == Qt.LeftButton:
            if not self.popup:
                self.popup = CustomColorPopup(self)
                self.popup.colorSelected.connect(self.set_color)
            
            # 将弹窗移动到按钮下方
            point = self.mapToGlobal(self.rect().bottomLeft())
            self.popup.move(point)
            self.popup.show()
            
# ==============================================================================
# 核心UI类：绘图器对话框 (修改后版本)
# ==============================================================================
class PlotterDialog(QDialog):
    MARKER_STYLES = {'点': 'o', '加号': '+', '叉号': 'x', '方形': 's', '三角形': '^', '菱形': 'D'}
    LINE_STYLES = {'实线': '-', '虚线': '--', '点线': ':', '点划线': '-.'}
    COLOR_SCHEMES = {
        # --- 针对色觉障碍优化的方案 ---
        "色觉友好 (无障碍)": [
            '#332288', '#117733', '#44AA99', '#88CCEE', '#DDCC77', 
            '#CC6677', '#AA4499', '#882255'
        ],
        
        # --- 标准与经典方案 ---
        "经典亮色 (Set1)": [
            '#e41a1c', '#377eb8', '#4daf4a', '#984ea3', '#ff7f00', 
            '#ffff33', '#a65628', '#f781bf', '#999999'
        ],
        "默认": [
            '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', 
            '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'
        ],
        "柔和色盘 (Set3)": [
            '#8dd3c7', '#ffffb3', '#bebada', '#fb8072', '#80b1d3', 
            '#fdb462', '#b3de69', '#fccde5', '#d9d9d9', '#bc80bd'
        ],
 
        # --- 新增风格化方案 ---
        "复古风格": [
            '#588c7e', '#f2e394', '#f2ae72', '#d96459', '#8c4646',
            '#424254', '#336b87', '#90afc5'
        ],
        "商务蓝调": [
            '#003f5c', '#374c80', '#7a5195', '#bc5090', '#ef5675', 
            '#ff764a', '#ffa600'
        ],
        "科学渐变 (Viridis)": [
            '#440154', '#482878', '#3e4989', '#31688e', '#26828e', 
            '#1f9e89', '#35b779', '#6dcd59', '#b4de2c', '#fde725'
        ]
    }

    def __init__(self, parent=None, icon_manager=None):
        super().__init__(parent)
        if not LIBS_AVAILABLE:
            QMessageBox.critical(self, "依赖缺失", "需要 'matplotlib' 和 'textgrid' 库。\n请运行: pip install matplotlib textgrid")
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(0, self.reject); return

        self.setWindowTitle("元音空间图绘制器")
        self.resize(1200, 800)
        self.setMinimumSize(1000, 700)
        self.icon_manager = icon_manager
        self.df = None
        self.tg = None
        self.group_widgets = {}

        self._init_ui()
        self._connect_signals()
        self._update_ui_state()

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        self.left_panel = self._create_left_panel()
        center_splitter = QSplitter(Qt.Vertical)
        
        self.figure = Figure()
        self.canvas = FigureCanvas(self.figure)
        # --- [核心修改 #1 & #2] 启用右键菜单和滚轮缩放 ---
        self.canvas.setContextMenuPolicy(Qt.CustomContextMenu)
        self.canvas.setToolTip("图表区域。\n- 右键可刷新/保存\n- Ctrl+滚轮可缩放")
        
        self.table_view = QTableView()
        self.table_view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_view.setToolTip("当前加载的数据预览。")
        
        center_splitter.addWidget(self.canvas)
        center_splitter.addWidget(self.table_view)
        center_splitter.setSizes([600, 200])
        self.right_panel = self._create_right_panel()
        main_layout.addWidget(self.left_panel)
        main_layout.addWidget(center_splitter, 1)
        main_layout.addWidget(self.right_panel)

    def _create_left_panel(self):
        panel = QWidget(); panel.setFixedWidth(350)
        layout = QVBoxLayout(panel)
        
        load_group = QGroupBox("1. 数据源")
        load_layout = QFormLayout(load_group)
        self.load_button = QPushButton(" 加载数据文件...")
        self.load_button.setToolTip("加载包含共振峰数据的 Excel (.xlsx, .xls) 或 CSV (.csv) 文件。\n文件必须包含 'timestamp' 列才能与TextGrid联动。")
        if self.icon_manager: self.load_button.setIcon(self.icon_manager.get_icon("open_folder"))
        self.file_label = QLabel("未加载文件"); self.file_label.setWordWrap(True)
        
        self.load_textgrid_button = QPushButton(" 加载 TextGrid...")
        self.load_textgrid_button.setToolTip("加载 TextGrid (.TextGrid) 文件为数据点添加标签。\n必须先加载一个包含 'timestamp' 列的数据文件。")
        if self.icon_manager: self.load_textgrid_button.setIcon(self.icon_manager.get_icon("textgrid"))
        self.textgrid_label = QLabel("未加载 TextGrid")
        
        self.tg_mode_combo = QComboBox()
        self.tg_mode_combo.addItems(["合并所有层", "使用单个层"])
        self.tg_mode_combo.setToolTip(
            "选择如何处理TextGrid中的层(Tier)：\n"
            "- 合并所有层: 将所有层中的标注合并到一个分组标签中 (推荐用于每个元音一个层的风格)。\n"
            "- 使用单个层: 仅使用下面选中的那个层进行标注 (推荐用于一个层包含所有元音的风格)。"
        )
        self.textgrid_tier_combo = QComboBox()
        self.textgrid_tier_combo.setToolTip("当处理模式为'使用单个层'时，在此选择要使用的具体层。")
        
        load_layout.addRow(self.load_button); load_layout.addRow(self.file_label)
        load_layout.addRow(self.load_textgrid_button); load_layout.addRow(self.textgrid_label)
        load_layout.addRow("TG处理模式:", self.tg_mode_combo)
        load_layout.addRow("标注层:", self.textgrid_tier_combo)
        
        data_spec_group = QGroupBox("2. 数据列指定")
        data_spec_layout = QFormLayout(data_spec_group)
        self.f1_combo = QComboBox(); self.f1_combo.setToolTip("选择代表第一共振峰 (F1) 的数据列，将作为图表的 Y 轴。")
        self.f2_combo = QComboBox(); self.f2_combo.setToolTip("选择代表第二共振峰 (F2) 的数据列，将作为图表的 X 轴。")
        self.group_by_combo = QComboBox(); self.group_by_combo.setToolTip("选择用于对数据点进行分组的列。\n选择'无分组'则所有点使用相同样式。\n使用TextGrid后，可选择 'textgrid_label' 进行分组。")
        data_spec_layout.addRow("F1 (Y轴):", self.f1_combo); data_spec_layout.addRow("F2 (X轴):", self.f2_combo)
        data_spec_layout.addRow("分组依据:", self.group_by_combo)

        action_group = QGroupBox("3. 绘图操作")
        action_layout = QVBoxLayout(action_group)
        self.plot_button = QPushButton(" 更新图表"); self.plot_button.setToolTip("根据当前所有设置，重新绘制图表。")
        if self.icon_manager: self.plot_button.setIcon(self.icon_manager.get_icon("chart"))
        action_layout.addWidget(self.plot_button)

        layout.addWidget(load_group); layout.addWidget(data_spec_group); layout.addWidget(action_group); layout.addStretch()
        return panel

    def _create_right_panel(self):
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFixedWidth(420); scroll.setFrameShape(QScrollArea.NoFrame)
        panel = QWidget(); layout = QVBoxLayout(panel); scroll.setWidget(panel)

        style_group = QGroupBox("全局样式"); style_layout = QFormLayout(style_group)
        self.title_edit = QLineEdit("元音空间图"); self.title_edit.setToolTip("设置图表的总标题。")
        self.xlabel_edit = QLineEdit("F2 (Hz)"); self.xlabel_edit.setToolTip("设置图表 X 轴的标签文本。")
        self.ylabel_edit = QLineEdit("F1 (Hz)"); self.ylabel_edit.setToolTip("设置图表 Y 轴的标签文本。")
        style_layout.addRow("图表标题:", self.title_edit); style_layout.addRow("X轴标签:", self.xlabel_edit); style_layout.addRow("Y轴标签:", self.ylabel_edit)

        axis_group = QGroupBox("坐标轴"); axis_layout = QFormLayout(axis_group)
        self.flip_x_check = QCheckBox("翻转 X 轴 (F2)"); self.flip_x_check.setChecked(True)
        self.flip_x_check.setToolTip("勾选后，X 轴数值将从右向左递增，符合语音学惯例。")
        self.flip_y_check = QCheckBox("翻转 Y 轴 (F1)"); self.flip_y_check.setChecked(True)
        self.flip_y_check.setToolTip("勾选后，Y 轴数值将从下向上递增，符合语音学惯例。")
        
        self.x_min_edit = QLineEdit(); self.x_max_edit = QLineEdit()
        self.x_min_edit.setPlaceholderText("自动"); self.x_max_edit.setPlaceholderText("自动")
        self.x_min_edit.setToolTip("设置X轴的最小值。留空则自动计算。"); self.x_max_edit.setToolTip("设置X轴的最大值。留空则自动计算。")
        x_range_layout = QHBoxLayout(); x_range_layout.addWidget(self.x_min_edit); x_range_layout.addWidget(QLabel("到")); x_range_layout.addWidget(self.x_max_edit)
        
        self.y_min_edit = QLineEdit(); self.y_max_edit = QLineEdit()
        self.y_min_edit.setPlaceholderText("自动"); self.y_max_edit.setPlaceholderText("自动")
        self.y_min_edit.setToolTip("设置Y轴的最小值。留空则自动计算。"); self.y_max_edit.setToolTip("设置Y轴的最大值。留空则自动计算。")
        y_range_layout = QHBoxLayout(); y_range_layout.addWidget(self.y_min_edit); y_range_layout.addWidget(QLabel("到")); y_range_layout.addWidget(self.y_max_edit)
        
        axis_layout.addRow(self.flip_x_check); axis_layout.addRow(self.flip_y_check)
        axis_layout.addRow("X轴范围:", x_range_layout); axis_layout.addRow("Y轴范围:", y_range_layout)
        
        self.points_group = QGroupBox("数据点样式 (全局)"); points_layout = QFormLayout(self.points_group)
        self.point_color_btn = ColorButton(QColor("#3498db"))
        self.point_size_slider = QSlider(Qt.Horizontal); self.point_size_slider.setRange(5, 100); self.point_size_slider.setValue(30)
        self.point_size_slider.setToolTip("调整所有数据点的大小。")
        self.point_alpha_slider = QSlider(Qt.Horizontal); self.point_alpha_slider.setRange(10, 100); self.point_alpha_slider.setValue(70)
        self.point_alpha_slider.setToolTip("调整所有数据点的不透明度，值越小越透明。")
        
        self.ungrouped_color_row = QWidget(); ungrouped_color_layout = QFormLayout(self.ungrouped_color_row)
        ungrouped_color_layout.setContentsMargins(0,0,0,0)
        ungrouped_color_layout.addRow("颜色 (无分组时):", self.point_color_btn)
        
        points_layout.addWidget(self.ungrouped_color_row)
        points_layout.addRow("大小:", self.point_size_slider)
        points_layout.addRow("不透明度:", self.point_alpha_slider)
        
        self.grouping_group = QGroupBox("分组与图例"); grouping_layout = QVBoxLayout(self.grouping_group)
        self.show_legend_check = QCheckBox("显示图例"); self.show_legend_check.setChecked(True)
        self.show_legend_check.setToolTip("是否在图表上显示图例（仅在分组时有效）。")
        self.group_settings_widget = QWidget()
        self.group_settings_layout = QVBoxLayout(self.group_settings_widget); self.group_settings_layout.setContentsMargins(0, 5, 0, 0); self.group_settings_layout.setSpacing(2)
        
        color_scheme_layout = QHBoxLayout()
        self.color_scheme_combo = QComboBox(); self.color_scheme_combo.addItems(self.COLOR_SCHEMES.keys())
        self.color_scheme_combo.setToolTip("选择一个预设的颜色方案。")
        self.apply_color_scheme_btn = QPushButton("应用"); self.apply_color_scheme_btn.setToolTip("将选择的颜色方案应用到下面的各个分组。")
        color_scheme_layout.addWidget(self.color_scheme_combo); color_scheme_layout.addWidget(self.apply_color_scheme_btn)
        grouping_layout.addWidget(self.show_legend_check); grouping_layout.addLayout(color_scheme_layout); grouping_layout.addWidget(self.group_settings_widget)

        self.mean_group = QGroupBox("平均值点"); self.mean_group.setCheckable(True); self.mean_group.setChecked(False)
        self.mean_group.setToolTip("勾选后，将为每个分组绘制一个代表其平均F1/F2值的点。")
        mean_layout = QFormLayout(self.mean_group)
        self.mean_marker_combo = QComboBox(); self.mean_marker_combo.addItems(self.MARKER_STYLES.keys()); self.mean_marker_combo.setCurrentText("加号")
        self.mean_marker_combo.setToolTip("设置平均值点的标记形状。")
        self.mean_size_slider = QSlider(Qt.Horizontal); self.mean_size_slider.setRange(20, 300); self.mean_size_slider.setValue(100)
        self.mean_size_slider.setToolTip("设置平均值点的标记大小。")
        self.mean_color_btn = ColorButton(QColor("#000000")); self.mean_color_btn.setToolTip("设置所有平均值点的颜色。")
        mean_layout.addRow("标记样式:", self.mean_marker_combo); mean_layout.addRow("标记大小:", self.mean_size_slider); mean_layout.addRow("标记颜色:", self.mean_color_btn)
        
        self.ellipse_group = QGroupBox("标准差椭圆"); self.ellipse_group.setCheckable(True); self.ellipse_group.setChecked(False)
        self.ellipse_group.setToolTip("勾选后，将为每个分组绘制一个标准差椭圆，表示数据点的分布范围。")
        ellipse_layout = QFormLayout(self.ellipse_group)
        self.ellipse_std_combo = QComboBox(); self.ellipse_std_combo.addItems(["1 (68%)", "1.5 (86%)", "2 (95%)"]); self.ellipse_std_combo.setCurrentText("2 (95%)")
        self.ellipse_std_combo.setToolTip("设置椭圆覆盖的数据范围，基于标准差倍数。")
        self.ellipse_style_combo = QComboBox(); self.ellipse_style_combo.addItems(self.LINE_STYLES.keys())
        self.ellipse_style_combo.setToolTip("设置椭圆边框的线条样式。")
        self.ellipse_width_slider = QSlider(Qt.Horizontal); self.ellipse_width_slider.setRange(1, 10); self.ellipse_width_slider.setValue(2)
        self.ellipse_width_slider.setToolTip("设置椭圆边框的线条宽度。")
        ellipse_layout.addRow("标准差倍数:", self.ellipse_std_combo); ellipse_layout.addRow("线条样式:", self.ellipse_style_combo); ellipse_layout.addRow("线条宽度:", self.ellipse_width_slider)

        layout.addWidget(style_group); layout.addWidget(axis_group); layout.addWidget(self.points_group); layout.addWidget(self.grouping_group); layout.addWidget(self.mean_group); layout.addWidget(self.ellipse_group); layout.addStretch()
        return scroll

    def _connect_signals(self):
        self.load_button.clicked.connect(self._load_data)
        self.load_textgrid_button.clicked.connect(self._load_textgrid)
        self.plot_button.clicked.connect(self._plot_data)
        self.group_by_combo.currentTextChanged.connect(self._on_grouping_changed)
        self.apply_color_scheme_btn.clicked.connect(self._apply_color_scheme)
        self.mean_group.toggled.connect(self._plot_data)
        self.ellipse_group.toggled.connect(self._plot_data)
        self.tg_mode_combo.currentIndexChanged.connect(self._on_tg_mode_changed)
        self.textgrid_tier_combo.currentIndexChanged.connect(self._on_tg_mode_changed)
        # --- [核心修改 #1] 连接右键菜单信号 ---
        self.canvas.customContextMenuRequested.connect(self._show_context_menu)

    # --- [核心修改 #1] 右键菜单功能 ---
    def _show_context_menu(self, pos):
        context_menu = QMenu(self)
        
        save_action = context_menu.addAction("保存图片...")
        if self.icon_manager: save_action.setIcon(self.icon_manager.get_icon("save"))
            
        refresh_action = context_menu.addAction("刷新图表")
        if self.icon_manager: refresh_action.setIcon(self.icon_manager.get_icon("refresh"))

        action = context_menu.exec_(self.canvas.mapToGlobal(pos))
        
        if action == save_action:
            self._save_plot_image()
        elif action == refresh_action:
            self._plot_data()

    def _save_plot_image(self):
        # 1. 获取当前图表标题
        title = self.title_edit.text()
        
        # 2. 清理标题，使其成为一个有效的文件名
        # 移除 Windows 和 Linux/macOS 不允许的字符
        safe_filename = re.sub(r'[\\/*?:"<>|]', "_", title)
        
        # 3. 弹出保存对话框，并设置默认文件名
        file_path, _ = QFileDialog.getSaveFileName(
            self, 
            "保存图片", 
            f"{safe_filename}.png",  # 设置默认文件名
            "PNG图片 (*.png);;高分辨率PDF (*.pdf);;JPEG图片 (*.jpg)"
        )
 
        if file_path:
            try:
                self.figure.savefig(file_path, dpi=300, bbox_inches='tight')
                QMessageBox.information(self, "成功", f"图表已保存到:\n{file_path}")
            except Exception as e:
                QMessageBox.critical(self, "保存失败", f"无法保存图片: {e}")

    # --- [核心修改 #2] 滚轮缩放功能 ---
    def wheelEvent(self, event):
        # 仅当鼠标在图表上且按下了Ctrl键时触发
        if self.canvas.underMouse() and event.modifiers() == Qt.ControlModifier:
            try:
                ax = self.figure.gca()
                x_data, y_data = event.x(), self.canvas.height() - event.y()
                
                # 转换鼠标像素坐标为数据坐标
                trans = ax.transData.inverted()
                mouse_x, mouse_y = trans.transform_point((x_data, y_data))

                cur_xlim = ax.get_xlim()
                cur_ylim = ax.get_ylim()

                # 根据滚轮方向确定缩放比例
                zoom_factor = 1.1 if event.angleDelta().y() > 0 else 1 / 1.1

                # 计算新的坐标轴范围
                new_xlim = [
                    mouse_x - (mouse_x - cur_xlim[0]) / zoom_factor,
                    mouse_x + (cur_xlim[1] - mouse_x) / zoom_factor
                ]
                new_ylim = [
                    mouse_y - (mouse_y - cur_ylim[0]) / zoom_factor,
                    mouse_y + (cur_ylim[1] - mouse_y) / zoom_factor
                ]

                ax.set_xlim(new_xlim)
                ax.set_ylim(new_ylim)
                self.canvas.draw()
            except Exception as e:
                print(f"Zoom failed: {e}")
        else:
            # 否则，执行默认的滚轮事件（例如滚动条滚动）
            super().wheelEvent(event)

    def _update_ui_state(self):
        """根据当前状态（是否有数据、是否分组）更新UI控件的可用性。"""
        has_data = self.df is not None
        is_grouping = has_data and self.group_by_combo.currentText() != "无分组"
        if self.group_by_combo.currentText() == 'textgrid_label' and self.tg is None:
            is_grouping = False

        self.plot_button.setEnabled(has_data)
        
        has_textgrid = self.tg is not None
        self.tg_mode_combo.setEnabled(has_textgrid)
        self.textgrid_tier_combo.setEnabled(has_textgrid and self.tg_mode_combo.currentText() == "使用单个层")

        self.points_group.setVisible(True) 
        self.ungrouped_color_row.setVisible(not is_grouping)
        self.grouping_group.setVisible(is_grouping)
        
        self.mean_group.setEnabled(is_grouping)
        self.ellipse_group.setEnabled(is_grouping)
        
        if not is_grouping:
            self.mean_group.setChecked(False)
            self.ellipse_group.setChecked(False)
            
    # --- 以下方法保持原有逻辑，未做重大修改 ---
    def _load_data(self):
        data_path, _ = QFileDialog.getOpenFileName(self, "选择数据文件", "", "表格文件 (*.xlsx *.xls *.csv)")
        if not data_path: return
        try:
            df = pd.read_excel(data_path) if data_path.lower().endswith(('.xlsx', '.xls')) else pd.read_csv(data_path)
            if 'timestamp' not in df.columns:
                QMessageBox.warning(self, "格式警告", "数据文件不包含 'timestamp' 列，将无法使用TextGrid功能。");
            self.df = df
            self.file_label.setText(os.path.basename(data_path))
            self.tg = None; self.textgrid_label.setText("未加载 TextGrid"); self.textgrid_tier_combo.clear()
            self._update_combos_and_plot()
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法读取数据文件: {e}"); self.df = None

    def _load_textgrid(self):
        if self.df is None or 'timestamp' not in self.df.columns:
            QMessageBox.warning(self, "请先加载数据", "请先加载一个包含 'timestamp' 列的数据文件。"); return
        tg_path, _ = QFileDialog.getOpenFileName(self, "选择 TextGrid 文件", "", "TextGrid 文件 (*.TextGrid)")
        if not tg_path: return
        try:
            self.tg = textgrid.TextGrid.fromFile(tg_path)
            self.textgrid_label.setText(os.path.basename(tg_path))
            self.textgrid_tier_combo.clear()
            self.textgrid_tier_combo.addItems([t.name for t in self.tg if isinstance(t, textgrid.IntervalTier)])
            self._on_tg_mode_changed()
        except Exception as e:
            QMessageBox.critical(self, "TextGrid 加载失败", f"无法解析 TextGrid 文件: {e}"); self.tg = None

    def _on_tg_mode_changed(self):
        is_single_tier_mode = self.tg_mode_combo.currentText() == "使用单个层"
        self.textgrid_tier_combo.setEnabled(is_single_tier_mode)
        self._apply_textgrid_to_dataframe()

    def _apply_textgrid_to_dataframe(self):
        if self.df is None: return
        if self.tg is None:
            if 'textgrid_label' in self.df.columns:
                self.df = self.df.drop(columns=['textgrid_label'])
            self._update_combos_and_plot()
            return
            
        label_col = pd.Series(np.nan, index=self.df.index, dtype=object)
        
        if self.tg_mode_combo.currentText() == "合并所有层":
            for tier in self.tg:
                if isinstance(tier, textgrid.IntervalTier):
                    for interval in tier:
                        if interval.mark:
                            mask = (self.df['timestamp'] >= interval.minTime) & (self.df['timestamp'] < interval.maxTime)
                            label_col[mask] = interval.mark
        else: 
            tier_name = self.textgrid_tier_combo.currentText()
            if tier_name and (tier := self.tg.getFirst(tier_name)):
                for interval in tier:
                    if interval.mark:
                        mask = (self.df['timestamp'] >= interval.minTime) & (self.df['timestamp'] < interval.maxTime)
                        label_col[mask] = interval.mark
        
        self.df['textgrid_label'] = label_col
        self._update_combos_and_plot(default_group_col='textgrid_label')

    def _update_combos_and_plot(self, default_group_col=None):
        if self.df is None: return
        all_cols = self.df.columns.tolist()
        numeric_cols = self.df.select_dtypes(include=np.number).columns.tolist()
        non_numeric_cols = self.df.select_dtypes(exclude=np.number).columns.tolist()
        if 'textgrid_label' in non_numeric_cols:
            non_numeric_cols.remove('textgrid_label')
            non_numeric_cols.insert(0, 'textgrid_label')

        self.f1_combo.clear(); self.f1_combo.addItems(numeric_cols)
        self.f2_combo.clear(); self.f2_combo.addItems(numeric_cols)
        
        self.group_by_combo.clear(); self.group_by_combo.addItem("无分组")
        if non_numeric_cols: self.group_by_combo.addItems(non_numeric_cols)
        if non_numeric_cols and numeric_cols: self.group_by_combo.insertSeparator(self.group_by_combo.count())
        if numeric_cols: self.group_by_combo.addItems(numeric_cols)

        f1 = next((c for c in numeric_cols if 'f1' in c.lower()), numeric_cols[0] if numeric_cols else "")
        f2 = next((c for c in numeric_cols if 'f2' in c.lower()), numeric_cols[1] if len(numeric_cols)>1 else "")
        self.f1_combo.setCurrentText(f1); self.f2_combo.setCurrentText(f2)
        
        if default_group_col and default_group_col in all_cols:
            self.group_by_combo.setCurrentText(default_group_col)
        else:
            default_group = next((c for c in non_numeric_cols if 'vowel' in c.lower() or 'label' in c.lower()), "无分组")
            self.group_by_combo.setCurrentText(default_group)

        self.table_view.setModel(PandasModel(self.df))
        self._on_grouping_changed()

    def _plot_data(self):
        if self.df is None: return
        f1_col, f2_col, group_col = self.f1_combo.currentText(), self.f2_combo.currentText(), self.group_by_combo.currentText()
        if not f1_col or not f2_col: return
        try:
            self.figure.clear(); ax = self.figure.add_subplot(111)
            
            point_size = self.point_size_slider.value()
            point_alpha = self.point_alpha_slider.value() / 100.0

            has_visible_groups = False
            if group_col != "无分组" and self.group_widgets:
                plot_df = self.df.dropna(subset=[f1_col, f2_col, group_col])
                for group_name, widgets in self.group_widgets.items():
                    if not widgets['cb'].isChecked(): continue
                    has_visible_groups = True
                    group_data = plot_df[plot_df[group_col].astype(str) == str(group_name)]
                    f1, f2 = group_data[f1_col], group_data[f2_col]
                    if f1.empty: continue
                    color = widgets['color'].color().name()
                    marker = self.MARKER_STYLES[widgets['marker'].currentText()]
                    
                    ax.scatter(f2, f1, label=group_name, color=color, marker=marker, 
                               s=point_size, alpha=point_alpha)

                    if self.mean_group.isChecked():
                        mean_f1, mean_f2 = f1.mean(), f2.mean()
                        unfilled_markers = ['+', 'x', '|', '_']
                        current_marker_char = self.MARKER_STYLES[self.mean_marker_combo.currentText()]
                        scatter_kwargs = {'color': self.mean_color_btn.color().name(), 's': self.mean_size_slider.value(), 'marker': current_marker_char, 'zorder': 10}
                        if current_marker_char not in unfilled_markers:
                            scatter_kwargs['edgecolors'] = 'white'; scatter_kwargs['linewidths'] = 1.5
                        ax.scatter(mean_f2, mean_f1, **scatter_kwargs)

                    if self.ellipse_group.isChecked() and len(f1) > 2:
                        self._plot_ellipse(f2, f1, ax, color)
            else:
                plot_df = self.df[[f1_col, f2_col]].copy().dropna()
                f1, f2 = plot_df[f1_col], plot_df[f2_col]
                ax.scatter(f2, f1, color=self.point_color_btn.color().name(), 
                           s=point_size, alpha=point_alpha)

            ax.set_title(self.title_edit.text(), fontsize=14); ax.set_xlabel(self.xlabel_edit.text()); ax.set_ylabel(self.ylabel_edit.text())
            ax.grid(True, linestyle='--', alpha=0.6)
            try:
                if self.x_min_edit.text() and self.x_max_edit.text(): ax.set_xlim(float(self.x_min_edit.text()), float(self.x_max_edit.text()))
                if self.y_min_edit.text() and self.y_max_edit.text(): ax.set_ylim(float(self.y_min_edit.text()), float(self.y_max_edit.text()))
            except ValueError: pass
            if self.flip_x_check.isChecked(): ax.invert_xaxis(); 
            if self.flip_y_check.isChecked(): ax.invert_yaxis()
            if self.show_legend_check.isChecked() and has_visible_groups:
                ax.legend()
            self.figure.tight_layout(pad=1.5); self.canvas.draw()
        except Exception as e: QMessageBox.critical(self, "绘图失败", f"生成图表时发生错误: {e}\n\n请检查数据列是否正确。")
    
    def _on_grouping_changed(self):
        group_col = self.group_by_combo.currentText()
        if self.df is not None and group_col != "无分组":
            try:
                unique_groups = self.df[group_col].dropna().unique(); MAX_GROUPS = 30
                if len(unique_groups) > MAX_GROUPS:
                    QMessageBox.warning(self, "分组过多", f"您选择的列 '{group_col}' 产生了超过 {MAX_GROUPS} 个分组。\n\n这通常是因为选择了一个连续数值列。\n\n已自动切换回“无分组”模式。")
                    self.group_by_combo.setCurrentText("无分组"); return
            except Exception: self.group_by_combo.setCurrentText("无分组"); return
        self._populate_group_settings(); self._update_ui_state(); self._plot_data()

    def _populate_group_settings(self):
        while self.group_settings_layout.count():
            child = self.group_settings_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()
        self.group_widgets.clear()
        
        group_col = self.group_by_combo.currentText()
        if not self.df is None and group_col != "无分组":
            try:
                groups = sorted(self.df[group_col].dropna().unique(), key=str)
                color_cycle = cycle(self.COLOR_SCHEMES.get(self.color_scheme_combo.currentText(), self.COLOR_SCHEMES['默认']))
                for group_name in groups:
                    row = QWidget()
                    layout = QHBoxLayout(row)
                    layout.setContentsMargins(0, 0, 0, 0)
                    layout.setSpacing(5) 
                    layout.setAlignment(Qt.AlignVCenter)

                    cb = QCheckBox(str(group_name)); cb.setChecked(True)
                    cb.setToolTip(f"勾选/取消勾选以在图表中显示/隐藏 '{group_name}' 分组。")
                    color_btn = ColorButton(next(color_cycle))
                    marker_combo = QComboBox(); marker_combo.addItems(self.MARKER_STYLES.keys())
                    marker_combo.setToolTip("选择此分组数据点的标记形状。")
                    color_btn.setFixedWidth(50) 
                    marker_combo.setFixedWidth(70)

                    layout.addWidget(cb, 1) 
                    layout.addWidget(color_btn)
                    layout.addWidget(marker_combo)
                    
                    self.group_settings_layout.addWidget(row)
                    self.group_widgets[group_name] = {'cb': cb, 'color': color_btn, 'marker': marker_combo}
                    cb.stateChanged.connect(self._plot_data)
                    color_btn.clicked.connect(self._plot_data)
                    marker_combo.currentTextChanged.connect(self._plot_data)
            except Exception as e: print(f"Error populating group settings: {e}")

    def _apply_color_scheme(self):
        scheme_name = self.color_scheme_combo.currentText()
        color_cycle = cycle(self.COLOR_SCHEMES.get(scheme_name, []))
        for widgets in self.group_widgets.values(): widgets['color'].set_color(next(color_cycle))
        self._plot_data()

    def _plot_ellipse(self, x, y, ax, color):
        cov = np.cov(x, y); mean_x, mean_y = np.mean(x), np.mean(y)
        lambda_, v = np.linalg.eig(cov); lambda_ = np.sqrt(lambda_)
        std_multiplier = float(self.ellipse_std_combo.currentText().split()[0])
        ell = Ellipse(xy=(mean_x, mean_y), width=lambda_[0]*std_multiplier*2, height=lambda_[1]*std_multiplier*2, angle=np.rad2deg(np.arccos(v[0, 0])), edgecolor=color, facecolor='none', linestyle=self.LINE_STYLES[self.ellipse_style_combo.currentText()], linewidth=self.ellipse_width_slider.value())
        ax.add_patch(ell)
    
    def load_dataframe(self, df, source_name="来自音频分析模块"):
        if df is None or df.empty: return
        self.df = df
        self.file_label.setText(f"数据来源: {source_name} ({len(df)}点)")
        
        has_timestamp = 'timestamp' in self.df.columns
        
        self.load_textgrid_button.setEnabled(has_timestamp)
        self.tg_mode_combo.setEnabled(has_timestamp)
        if not has_timestamp:
            self.textgrid_label.setText("数据无时间戳，无法加载TG")
            self.textgrid_label.setToolTip("从外部接收的数据必须包含一个名为 'timestamp' 的列才能使用TextGrid功能。")
            self.tg = None 
            self.textgrid_tier_combo.clear()
        else:
            self.textgrid_label.setText("未加载 TextGrid")
            self.textgrid_label.setToolTip("")

        self.tg = None
        self.textgrid_tier_combo.clear()

        self._update_combos_and_plot()
        self.group_by_combo.setCurrentText("无分组")

# ==============================================================================
# 插件主入口类 (保持不变)
# ==============================================================================
class VowelSpacePlotterPlugin(BasePlugin):
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.plotter_dialog = None
    def setup(self):
        if not LIBS_AVAILABLE:
            print("[Vowel Plotter Error] Missing dependencies. Plugin setup failed.")
            return False 
        self.audio_analysis_page = getattr(self.main_window, 'audio_analysis_page', None)
        if self.audio_analysis_page and hasattr(self.audio_analysis_page, 'spectrogram_widget'):
            self.audio_analysis_page.spectrogram_widget.vowel_plotter_plugin_active = self; print("Vowel Plotter hooked.")
        else:
            print("Vowel Plotter: Standalone mode only.")
        return True
    def teardown(self):
        if hasattr(self, 'audio_analysis_page') and self.audio_analysis_page and hasattr(self.audio_analysis_page, 'spectrogram_widget'):
            if getattr(self.audio_analysis_page.spectrogram_widget, 'vowel_plotter_plugin_active', None) is self:
                self.audio_analysis_page.spectrogram_widget.vowel_plotter_plugin_active = None; print("Vowel Plotter unhooked.")
        if self.plotter_dialog: self.plotter_dialog.close()
    def execute(self, **kwargs):
        if self.plotter_dialog is None:
            self.plotter_dialog = PlotterDialog(parent=self.main_window, icon_manager=getattr(self.main_window, 'icon_manager', None))
            self.plotter_dialog.finished.connect(self._on_dialog_finished)
        dataframe_to_load = kwargs.get('dataframe')
        if dataframe_to_load is not None: self.plotter_dialog.load_dataframe(dataframe_to_load)
        self.plotter_dialog.show(); self.plotter_dialog.raise_(); self.plotter_dialog.activateWindow()
    def _on_dialog_finished(self):
        self.plotter_dialog = None

# --- END OF MODIFIED FILE ---
