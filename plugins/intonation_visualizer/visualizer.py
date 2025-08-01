# --- START OF COMPLETE AND REFACTORED FILE plugins/intonation_visualizer/visualizer.py ---

import os
import sys
import uuid
import pandas as pd
import numpy as np
from itertools import cycle
from copy import deepcopy # 用于深度复制图层配置
import re # 用于清理文件名

# PyQt5 模块导入
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog,
                             QMessageBox, QTableView, QHeaderView, QComboBox, QCheckBox,
                             QSplitter, QGroupBox, QLineEdit, QFormLayout,
                             QColorDialog, QSlider, QWidget, QScrollArea, QMenu, QFrame,
                             QTableWidget, QTableWidgetItem, QAbstractItemView, QItemDelegate,
                             QApplication, QAction, QGridLayout, QDialogButtonBox)
from PyQt5.QtCore import Qt, QAbstractTableModel, pyqtSignal, QEvent, QSize
from PyQt5.QtGui import QIcon, QColor, QPalette, QPixmap, QFont, QPainter, QCursor, QPen

# Matplotlib 和 TextGrid 库导入
try:
    import matplotlib
    matplotlib.use('Qt5Agg') # 指定 Matplotlib 后端
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D # 用于绘制线段和点

    # 设置 Matplotlib 中文字体，避免乱码
    def set_matplotlib_font():
        font_candidates = ['Microsoft YaHei', 'SimHei', 'Source Han Sans CN', 'PingFang SC', 'WenQuanYi Micro Hei', 'Arial Unicode MS']
        from matplotlib.font_manager import findfont, FontProperties
        found_font = next((font for font in font_candidates if findfont(FontProperties(family=font))), None)
        if found_font:
            matplotlib.rcParams['font.sans-serif'] = [found_font] # 设置中文字体
            matplotlib.rcParams['axes.unicode_minus'] = False # 解决负号显示问题
            print(f"[Intonation Visualizer] Found and set Chinese font: {found_font}")
        else:
            print("[Intonation Visualizer Warning] No suitable Chinese font found for Matplotlib.")
    set_matplotlib_font()
    LIBS_AVAILABLE = True # 标记依赖库是否可用
except ImportError as e:
    print(f"[Intonation Visualizer Error] Missing core libraries: {e}. Please run 'pip install matplotlib pandas numpy'")
    LIBS_AVAILABLE = False # 依赖缺失，禁用相关功能

# 插件API导入
try:
    from modules.plugin_system import BasePlugin
except ImportError:
    # 如果在独立测试插件时，确保能找到 plugin_system.py
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from modules.plugin_system import BasePlugin
try:
    from modules.custom_widgets_module import ColorButton, CustomColorPopup
except ImportError:
    # 如果 custom_widgets_module 不存在或导入失败，提供一个回退方案
    # 这确保了即使在旧版本或模块缺失时，插件也不会完全崩溃
    print("[Intonation Visualizer Warning] Could not import color widgets from custom_widgets_module. Using fallback.")
    # 使用普通的 QPushButton 作为 ColorButton 的替代品
    ColorButton = QPushButton 
    # CustomColorPopup 无法简单替代，但依赖它的 ColorButton 已经是 QPushButton，所以不会被调用
    CustomColorPopup = QDialog
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
# 辅助类：ColorWidget (用于 TableWidget 内嵌 ColorButton，与plotter.py保持一致)
# ==============================================================================
class ColorWidget(QWidget):
    """表格中用于显示和选择颜色的小部件，使用导入的自定义弹窗。"""
    colorChanged = pyqtSignal(QColor)

    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.set_color(color)
        self.setFixedSize(50, 20)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点击更改颜色")
        self.popup = None

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def color(self):
        return self._color

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(self._color)
        # 修正：边框颜色应该更柔和，或者与ColorButton一致
        painter.setPen(QPen(QColor("#AAAAAA"))) 
        # 绘制圆角矩形
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 每次点击都创建新的弹窗，并传递当前颜色
            self.popup = CustomColorPopup(initial_color=self._color, parent=self)
            self.popup.colorSelected.connect(self.on_color_selected)
            self.popup.move(self.mapToGlobal(self.rect().bottomLeft()))
            self.popup.show()
            
    def on_color_selected(self, color):
        if color.isValid():
            self.set_color(color)
            self.colorChanged.emit(color)

# ==============================================================================
# 辅助类：ReadOnlyDelegate (与plotter.py保持一致)
# ==============================================================================
class ReadOnlyDelegate(QItemDelegate):
    """使表格某些列只读的委托"""
    def createEditor(self, parent, option, index):
        return None # 返回None表示不可编辑

# ==============================================================================
# 图层配置对话框 (LayerConfigDialog)
# 用于配置单个数据图层，包括数据文件、列映射、锁定状态
# ==============================================================================
class LayerConfigDialog(QDialog):
    def __init__(self, existing_config=None, parent=None):
        super().__init__(parent)
        self.df = None # 存储加载的 DataFrame
        # 深度复制一份配置，避免直接修改传入的字典，确保“取消”操作不影响原数据
        self.config = deepcopy(existing_config) if existing_config else {}
        self.parent_dialog = parent # 保存对主对话框 (VisualizerDialog) 的引用

        self.setWindowTitle("配置数据图层")
        self.setMinimumWidth(500)
        self._init_ui()
        self._connect_signals()
        if self.config:
            self._populate_from_config()
        
        # 确保在初始化时更新组合框，即使没有预设配置
        self._update_combos()

    def _init_ui(self):
        """初始化对话框的用户界面。"""
        layout = QVBoxLayout(self)
        form_layout = QFormLayout()
        
        # 图层名称输入框
        self.name_edit = QLineEdit(self.config.get('name', ''))
        self.name_edit.setPlaceholderText("例如：陈述句-男声")
        self.name_edit.setToolTip("为该数据图层指定一个唯一的名称。")
        
        # 数据文件选择
        data_layout = QHBoxLayout()
        self.load_data_btn = QPushButton("选择文件...")
        self.load_data_btn.setToolTip("加载包含时间与F0数据的 Excel (.xlsx, .xls) 或 CSV (.csv) 文件。")
        self.data_file_label = QLabel(self.config.get('data_filename', "未选择"))
        self.data_file_label.setWordWrap(True)
        data_layout.addWidget(self.load_data_btn)
        data_layout.addWidget(self.data_file_label, 1)

        # 数据列指定（时间, F0）
        self.time_combo = QComboBox()
        self.time_combo.setToolTip("选择代表时间的数据列，将作为图表的 X 轴。")
        self.f0_combo = QComboBox()
        self.f0_combo.setToolTip("选择代表基频 (F0) 的数据列，将作为图表的 Y 轴。")
        
        # --- [修正点] 分组标签改为可编辑的文本框 ---
        self.group_by_edit = QLineEdit(self.config.get('group_col', ''))
        self.group_by_edit.setPlaceholderText("输入分组名称 (可选)")
        self.group_by_edit.setToolTip("为该图层的所有数据点指定一个统一的分组标签。\n留空则该图层在图例中只显示图层名称。")
        
        # --- [修正点] 移除锁定图层复选框 ---
        # self.lock_check = QCheckBox("锁定图层") 

        # 将所有控件添加到表单布局
        form_layout.addRow("图层名称:", self.name_edit)
        form_layout.addRow("数据文件:", data_layout)
        form_layout.addRow(QFrame(frameShape=QFrame.HLine)) # 分隔线
        form_layout.addRow("时间 (X轴):", self.time_combo)
        form_layout.addRow("F0 (Y轴):", self.f0_combo)
        form_layout.addRow("分组标签:", self.group_by_edit)
        # form_layout.addRow(self.lock_check) # <-- 移除

        layout.addLayout(form_layout)
        
        # 标准确定/取消按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _connect_signals(self):
        """连接UI控件的信号到槽函数。"""
        self.load_data_btn.clicked.connect(self._load_data)

    def _populate_from_config(self):
        """根据传入的配置字典填充UI控件。"""
        self.df = self.config.get('df') # 恢复 DataFrame 对象
        
        # 恢复文件名标签显示
        if 'data_filename' in self.config:
            self.data_file_label.setText(self.config['data_filename'])

        # --- [修正点] 移除锁定状态的加载 ---
        # self.lock_check.setChecked(self.config.get('locked', False))

        self._update_combos() # 先更新组合框内容
        # 再设置当前选中项
        self.time_combo.setCurrentText(self.config.get('time_col', ''))
        self.f0_combo.setCurrentText(self.config.get('f0_col', ''))
        # --- [修正点] 填充分组文本框 ---
        self.group_by_edit.setText(self.config.get('group_col', ''))

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
            
            self._update_combos() # 更新列选择组合框
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法读取数据文件: {e}")
            self.df = None # 加载失败则清空df

    def _update_combos(self):
        """根据当前加载的 DataFrame 更新时间, F0 列的下拉选项。"""
        self.time_combo.clear()
        self.f0_combo.clear()

        if self.df is None: return

        numeric_cols = self.df.select_dtypes(include=np.number).columns.tolist()

        self.time_combo.addItems(numeric_cols)
        self.f0_combo.addItems(numeric_cols)
        
        # 尝试自动选择时间/F0列
        time_auto = next((c for c in numeric_cols if 'time' in c.lower() or 'timestamp' in c.lower()), numeric_cols[0] if numeric_cols else "")
        f0_auto = next((c for c in numeric_cols if 'f0' in c.lower() or 'hz' in c.lower()), numeric_cols[1] if len(numeric_cols) > 1 else "")
        self.time_combo.setCurrentText(time_auto)
        self.f0_combo.setCurrentText(f0_auto)

    def get_layer_config(self):
        """
        获取当前对话框中配置的图层信息。
        返回一个字典，包含图层名称、DataFrame、列名等。
        """
        if self.df is None:
            QMessageBox.warning(self, "输入无效", "请先加载数据文件。")
            return None
        
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "输入无效", "请输入图层名称。")
            return None
        
        time_col = self.time_combo.currentText()
        f0_col = self.f0_combo.currentText()
        # --- [修正点] 从文本框获取分组标签 ---
        group_col = self.group_by_edit.text().strip()
        # 如果用户没有输入，将其视为空字符串，表示无分组
        if not group_col:
            group_col = "无分组"

        if not time_col or not f0_col:
            QMessageBox.warning(self, "输入无效", "请为时间和F0指定数据列。")
            return None
            
        # 收集当前图层的所有样式设置，以便保存 (与_populate_layer_settings_panel的默认值保持一致)
        current_layer_settings = {
            "smoothing_enabled": self.config.get('smoothing_enabled', True),
            "smoothing_window": self.config.get('smoothing_window', 4), # 默认4，对应9点
            "show_points": self.config.get('show_points', False),
            "point_size": self.config.get('point_size', 10),
            "point_alpha": self.config.get('point_alpha', 0.4),
            "show_mean_contour": self.config.get('show_mean_contour', False),
            "color_scheme": self.config.get('color_scheme', '默认'), # 保存图层独立的颜色方案
            "groups": deepcopy(self.config.get('groups', {})) # 深度复制分组设置
        }

        # 更新配置字典
        self.config.update({
            "name": name,
            "df": self.df,
            "data_filename": self.data_file_label.text(), # 保存文件名用于显示
            "time_col": time_col,
            "f0_col": f0_col,
            "group_col": group_col,
            "enabled": self.config.get('enabled', True), # 默认启用
            "locked": False, # 总是设置为False
            **current_layer_settings # 将所有样式设置也合并进来
        })
        return self.config
# ==============================================================================
# 核心UI类：语调可视化器 (架构与Plotter一致)
# ==============================================================================
class VisualizerDialog(QDialog):
    # 定义所有颜色方案 (与plotter.py保持一致)
    COLOR_SCHEMES = {
        "默认": ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'],
        "色觉友好": ['#332288', '#117733', '#44AA99', '#88CCEE', '#DDCC77', '#CC6677', '#AA4499', '#882255'],
        "经典亮色": ['#e41a1c', '#377eb8', '#4daf4a', '#984ea3', '#ff7f00', '#a65628', '#f781bf'],
        "柔和色盘": ['#8dd3c7', '#ffffb3', '#bebada', '#fb8072', '#80b1d3', '#fdb462', '#b3de69', '#fccde5', '#d9d9d9', '#bc80bd'],
        "复古风格": ['#588c7e', '#f2e394', '#f2ae72', '#d96459', '#8c4646', '#424254', '#336b87', '#90afc5'],
        "商务蓝调": ['#003f5c', '#374c80', '#7a5195', '#bc5090', '#ef5675', '#ff764a', '#ffa600'],
        "科学渐变 (Viridis)": ['#440154', '#482878', '#3e4989', '#31688e', '#26828e', '#1f9e89', '#35b779', '#6dcd59', '#b4de2c', '#fde725']
    }
    
    SUPPORTED_EXTENSIONS = ('.csv', '.xlsx', '.xls') # 支持拖拽的文件类型

    def __init__(self, parent=None, icon_manager=None):
        """
        初始化语调可视化器对话框。
        :param parent: 父 QWidget，通常是主窗口实例。
        :param icon_manager: 用于获取图标的 IconManager 实例。
        """
        super().__init__(parent)
        # 检查依赖库是否可用
        if not LIBS_AVAILABLE:
            QMessageBox.critical(self, "依赖缺失", "需要 'matplotlib' 和 'pandas' 库。\n请运行: pip install matplotlib pandas")
            # 延迟关闭对话框，确保错误消息能显示
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(0, self.reject); return

        self.setWindowTitle("语调可视化")
        self.resize(1400, 900) # 初始窗口大小
        self.setMinimumSize(1200, 750) # 最小窗口大小
        self.icon_manager = icon_manager # 获取图标管理器实例
        
        # --- 核心数据结构 ---
        self.layers = [] # 存储所有图层的配置信息 (列表中的每个元素是一个字典)
        self.current_selected_layer_index = -1 # 当前在图层表格中选中的图层索引
        # --- [核心修正] 恢复全局分组设置 ---
        # 存储所有活跃分组的设置：{'group_name': {'enabled': bool, 'color': QColor}}
        self.global_groups = {} 
        # `color_cycler` 用于为新添加的图层和新发现的分组分配默认颜色
        self.color_cycler = cycle(self.COLOR_SCHEMES['默认']) 
        # 存储 Matplotlib 绘制的 Line2D 对象，用于鼠标交互
        self.plotted_lines = [] 

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
        """创建用于拖拽提示的覆盖层 (与plotter.py保持一致)"""
        self.drop_overlay = QLabel("拖拽 CSV / Excel 文件到此处进行分析", self)
        self.drop_overlay.setAlignment(Qt.AlignCenter)
        font = self.font(); font.setPointSize(20); font.setBold(True); self.drop_overlay.setFont(font)
        
        palette = self.palette() # 使用当前应用的调色板
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
        self.left_panel = self._create_left_panel()
        
        # 中心区域使用 QSplitter 分割画布和数据预览表格
        center_splitter = QSplitter(Qt.Vertical)
        
        # Matplotlib 画布
        self.figure = Figure()
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setContextMenuPolicy(Qt.CustomContextMenu) # 启用右键菜单
        self.canvas.setToolTip("图表区域。\n- 左键拖动可平移视图\n- Ctrl+滚轮可缩放\n- 右键可打开菜单")
        
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
        """创建左侧面板，包含图层管理器和绘图操作按钮。"""
        panel = QWidget(); panel.setFixedWidth(450); layout = QVBoxLayout(panel)
        
        # 图层管理器组框
        layer_group = QGroupBox("图层管理器"); layer_layout = QVBoxLayout(layer_group)
        self.layer_table = QTableWidget()
        
        # --- [修正版] 恢复并优化三列表格 ---
        self.layer_table.setColumnCount(3)
        self.layer_table.setHorizontalHeaderLabels(["颜色", "图层名称", "分组依据"])
        self.layer_table.setColumnWidth(0, 60) # 颜色列固定宽度
        self.layer_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch) # 名称列拉伸
        self.layer_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive) # 分组列可交互调整
        self.layer_table.setColumnWidth(2, 120)
        
        self.layer_table.setSelectionBehavior(QAbstractItemView.SelectRows); self.layer_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.layer_table.verticalHeader().setVisible(False); self.layer_table.setToolTip("右键单击进行操作，双击名称可配置图层。"); self.layer_table.setContextMenuPolicy(Qt.CustomContextMenu)
        
        btn_layout = QHBoxLayout(); self.add_layer_btn = QPushButton(" 添加新图层...")
        if self.icon_manager: self.add_layer_btn.setIcon(self.icon_manager.get_icon("add_row"))
        btn_layout.addWidget(self.add_layer_btn); btn_layout.addStretch()
        layer_layout.addWidget(self.layer_table); layer_layout.addLayout(btn_layout)
        
        action_group = QGroupBox("绘图操作"); action_layout = QVBoxLayout(action_group)
        self.plot_button = QPushButton(" 更新图表");
        if self.icon_manager: self.plot_button.setIcon(self.icon_manager.get_icon("chart"))
        action_layout.addWidget(self.plot_button)
        
        layout.addWidget(layer_group, 1); layout.addWidget(action_group)
        return panel

    def _create_right_panel(self):
        """[修正版] 恢复全局分组样式面板，简化图层设置面板"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(420) # 固定宽度
        scroll.setFrameShape(QScrollArea.NoFrame) # 无边框
        
        # 滚动区域内的主要容器
        panel = QWidget()
        layout = QVBoxLayout(panel)
        scroll.setWidget(panel)

        # --- 全局设置组框 (不变) ---
        global_group = QGroupBox("全局设置")
        global_layout = QFormLayout(global_group)
        self.title_edit = QLineEdit("语调曲线对比")
        self.xlabel_edit = QLineEdit("时间")
        self.ylabel_edit = QLineEdit("F0")
        self.title_edit.setToolTip("设置图表的总标题。")
        self.xlabel_edit.setToolTip("设置图表 X 轴的标签文本。")
        self.ylabel_edit.setToolTip("设置图表 Y 轴的标签文本。")
        global_layout.addRow("标题:", self.title_edit)
        global_layout.addRow("X轴标签:", self.xlabel_edit)
        global_layout.addRow("Y轴标签:", self.ylabel_edit)
        
        self.show_legend_check = QCheckBox("显示图例")
        self.show_legend_check.setChecked(True)
        self.show_legend_check.setToolTip("是否在图表上显示图例（仅在分组时有效）。")
        global_layout.addRow(self.show_legend_check)

        self.normalize_time_check = QCheckBox("时间归一化 (0-100%)")
        self.normalize_time_check.setToolTip("勾选后，每条曲线的时间轴将被归一化到0-100%，\n便于对比不同时长的句子的语调形态。")
        global_layout.addRow(self.normalize_time_check)
        self.interpolate_gaps_check = QCheckBox("插值填充F0间隙")
        self.interpolate_gaps_check.setChecked(True) # 默认开启，以匹配音频分析模块的平滑效果
        self.interpolate_gaps_check.setToolTip("勾选后，将使用线性插值填充F0数据中的无声间隙(NaN)，\n使曲线看起来更连续、平滑。")
        global_layout.addRow(self.interpolate_gaps_check)

        # F0归一化设置
        self.norm_combo = QComboBox()
        self.norm_combo.addItems(["原始值 (Hz)", "半音 (Semitone)", "Z-Score"])
        self.norm_combo.setToolTip("选择对F0值进行变换的方式：\n- 原始值: 不做任何处理。\n- 半音: 转换为对数尺度的半音，基准通常为100Hz或某个平均值。\n- Z-Score: 对F0进行标准化，消除个体音高差异。")
        
        self.st_ref_edit = QLineEdit("100")
        self.st_ref_edit.setToolTip("半音归一化的参考基频，单位Hz。")
        self.st_param_widget = QWidget()
        st_layout = QHBoxLayout(self.st_param_widget); st_layout.setContentsMargins(0,0,0,0); st_layout.addWidget(QLabel("基准(Hz):")); st_layout.addWidget(self.st_ref_edit)
        
        self.z_scope_combo = QComboBox()
        self.z_scope_combo.addItems(["按分组", "按整个数据集"])
        self.z_scope_combo.setToolTip("Z-Score归一化的统计范围：\n- 按分组: 对每个分组内的F0数据独立计算均值和标准差。\n- 按整个数据集: 对所有可见图层的所有F0数据计算一个总体的均值和标准差。")
        self.z_param_widget = QWidget(); z_layout = QHBoxLayout(self.z_param_widget); z_layout.setContentsMargins(0,0,0,0); z_layout.addWidget(QLabel("范围:")); z_layout.addWidget(self.z_scope_combo)
        
        self.st_param_widget.setVisible(False) # 初始隐藏
        self.z_param_widget.setVisible(False) # 初始隐藏
        
        global_layout.addRow("F0归一化:", self.norm_combo)
        global_layout.addRow(self.st_param_widget)
        global_layout.addRow(self.z_param_widget)

        # --- 图层设置组框 (简化版) ---
        self.layer_settings_group = QGroupBox("图层设置 (未选择图层)")
        self.layer_settings_group.setEnabled(False) # 默认禁用
        layer_settings_layout = QVBoxLayout(self.layer_settings_group)
        
        # 曲线平滑设置
        self.smoothing_group = QGroupBox("曲线平滑 (移动平均)")
        self.smoothing_group.setCheckable(True)
        self.smoothing_group.setChecked(True)
        self.smoothing_group.setToolTip("勾选后，对当前图层的F0曲线进行移动平均平滑。")
        smoothing_layout = QFormLayout(self.smoothing_group)
        self.smoothing_window_slider = QSlider(Qt.Horizontal)
        self.smoothing_window_slider.setRange(1, 25) # 1到25点
        self.smoothing_window_slider.setValue(4) # 默认4，对应 (2*4+1)=9点窗口
        self.smoothing_window_slider.setToolTip("移动平均的窗口大小（半长），最终窗口大小为 2*值+1。\n值越大曲线越平滑。")
        self.smoothing_label = QLabel("窗口: 9 点") # 实时显示窗口大小
        smoothing_layout.addRow(self.smoothing_label, self.smoothing_window_slider)
        
        # 数据点显示设置
        self.display_group = QGroupBox("显示选项")
        display_layout = QFormLayout(self.display_group)
        self.show_points_check = QCheckBox("显示数据点")
        self.show_points_check.setToolTip("勾选后，在当前图层的F0曲线上方显示原始的F0数据点。")
        self.point_size_slider = QSlider(Qt.Horizontal)
        self.point_size_slider.setRange(2, 50)
        self.point_size_slider.setValue(10)
        self.point_size_slider.setToolTip("调整当前图层数据点的大小。")
        self.point_alpha_slider = QSlider(Qt.Horizontal)
        self.point_alpha_slider.setRange(10, 100)
        self.point_alpha_slider.setValue(40)
        self.point_alpha_slider.setToolTip("调整当前图层数据点的不透明度，值越小越透明。")
        display_layout.addRow(self.show_points_check); display_layout.addRow("点大小:", self.point_size_slider); display_layout.addRow("点透明度:", self.point_alpha_slider)
        
        layer_settings_layout.addWidget(self.smoothing_group); layer_settings_layout.addWidget(self.display_group)

        # --- 全局分组样式面板 ---
        # 移到此处，并由 self.grouping_group 引用
        self.grouping_group = QGroupBox("全局分组样式 (颜色区分)")
        grouping_layout = QVBoxLayout(self.grouping_group)
        
        color_scheme_layout = QHBoxLayout(); self.color_scheme_combo = QComboBox(); self.color_scheme_combo.addItems(self.COLOR_SCHEMES.keys()); self.apply_color_scheme_btn = QPushButton("应用"); color_scheme_layout.addWidget(self.color_scheme_combo); color_scheme_layout.addWidget(self.apply_color_scheme_btn)
        
        # 动态生成的分组颜色和复选框会放在这里
        self.group_settings_scroll = QScrollArea(); self.group_settings_scroll.setWidgetResizable(True); self.group_settings_scroll.setFrameShape(QScrollArea.NoFrame)
        self.group_settings_widget = QWidget(); self.group_settings_layout = QVBoxLayout(self.group_settings_widget); self.group_settings_scroll.setWidget(self.group_settings_widget)
        
        self.show_mean_contour_check = QCheckBox("显示分组平均轮廓"); self.show_mean_contour_check.setToolTip("勾选后，将为每个分组计算并绘制一条平均语调轮廓线。\n(必须勾选时间归一化)")
        
        grouping_layout.addLayout(color_scheme_layout); grouping_layout.addWidget(self.group_settings_scroll); grouping_layout.addWidget(self.show_mean_contour_check)

        layout.addWidget(global_group); layout.addWidget(self.layer_settings_group); layout.addWidget(self.grouping_group); layout.addStretch()
        return scroll

    def _connect_signals(self):
        """连接所有UI控件的信号到槽函数。"""
        # 左侧面板 - 图层管理
        self.add_layer_btn.clicked.connect(self._add_layer)
        self.layer_table.customContextMenuRequested.connect(self._show_layer_context_menu) # 右键菜单
        self.layer_table.itemDoubleClicked.connect(self._on_layer_double_clicked) # 双击配置
        self.layer_table.itemChanged.connect(self._on_layer_renamed) # 重命名完成
        self.layer_table.itemChanged.connect(self._on_group_renamed_in_table) # 处理表格中分组列的编辑
        self.layer_table.itemSelectionChanged.connect(self._on_layer_selection_changed) # 选中行变化
        self.plot_button.clicked.connect(self._update_plot) # 更新图表

        # 右侧 - 全局设置
        self.title_edit.textChanged.connect(self._update_plot)
        self.xlabel_edit.textChanged.connect(self._update_plot)
        self.ylabel_edit.textChanged.connect(self._update_plot)
        self.show_legend_check.stateChanged.connect(self._update_plot)
        self.interpolate_gaps_check.stateChanged.connect(self._update_plot)
        # 这些全局设置变化，会联动更新Plot，并可能影响平均轮廓的启用状态
        self.normalize_time_check.stateChanged.connect(self._on_global_setting_changed) 
        self.norm_combo.currentTextChanged.connect(self._on_global_setting_changed)
        self.st_ref_edit.textChanged.connect(self._on_global_setting_changed)
        self.z_scope_combo.currentTextChanged.connect(self._on_global_setting_changed)
        
        # 右侧 - 图层设置 (所有变化都触发 _on_current_layer_setting_changed)
        self.smoothing_group.toggled.connect(self._on_current_layer_setting_changed)
        self.smoothing_window_slider.valueChanged.connect(self._on_current_layer_setting_changed)
        self.smoothing_window_slider.valueChanged.connect(self._update_smoothing_label) # 实时更新标签
        self.show_points_check.stateChanged.connect(self._on_current_layer_setting_changed)
        self.point_size_slider.valueChanged.connect(self._on_current_layer_setting_changed)
        self.point_alpha_slider.valueChanged.connect(self._on_current_layer_setting_changed)
        
        # 右侧 - 全局分组设置
        self.apply_color_scheme_btn.clicked.connect(self._apply_color_scheme_globally)
        # 平均轮廓的显示是全局的，因此连接到全局更新
        self.show_mean_contour_check.stateChanged.connect(self._update_plot) 
        
        # 画布交互 (鼠标平移、滚轮缩放、右键菜单)
        self.canvas.setMouseTracking(True) # 启用鼠标跟踪，用于悬浮提示
        self.canvas.customContextMenuRequested.connect(self._show_context_menu)
        self.canvas.mpl_connect('button_press_event', self._on_mouse_press)
        self.canvas.mpl_connect('button_release_event', self._on_mouse_release)
        self.canvas.mpl_connect('motion_notify_event', self._on_mouse_move)

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

                cur_xlim = ax.get_xlim()
                cur_ylim = ax.get_ylim()

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

                ax.set_xlim(new_xlim)
                ax.set_ylim(new_ylim)
                self.canvas.draw()
            except Exception as e:
                print(f"Zoom failed: {e}")
        else:
            # 如果不是Ctrl+滚轮在图表上，则将事件传递给父类处理（例如滚动滚动条）
            super().wheelEvent(event)

    # ==========================================================================
    # 图层管理相关方法 (左侧面板)
    # ==========================================================================
    def _add_layer(self):
        """打开 LayerConfigDialog 添加新图层。"""
        dialog = LayerConfigDialog(parent=self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_layer_config()
            if config:
                # --- [修正点] 自动为新图层分配颜色 ---
                # 确保每次添加新图层时，它都能获得一个来自循环器的新颜色
                config['color'] = QColor(next(self.color_cycler))
                
                self.layers.append(config)
                self._update_layer_table() # 更新图层列表UI
                self._update_ui_state() # 更新UI状态（如按钮启用状态）
                self._update_all_group_settings() # 扫描新图层以更新全局分组
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
        self._update_all_group_settings() # 移除图层后更新全局分组
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
                # 保留原始颜色，因为颜色在LayerConfigDialog中没有被修改
                # 即使旧配置中没有颜色，也会在 add_data_source 或 _config_layer 中安全地分配
                new_config['color'] = config_to_edit.get('color', QColor(next(self.color_cycler)))
                self.layers[current_row] = new_config # 更新图层列表中的配置
                self._update_layer_table_row(current_row) # 只更新该行UI
                self._on_layer_selection_changed() # 模拟选择变化，刷新右侧面板
                self._update_all_group_settings() # 更新全局分组
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
        """更新图层表格中指定行的内容和控件。"""
        if row >= len(self.layers): return
        layer = self.layers[row]
        
        if row >= self.layer_table.rowCount():
            self.layer_table.insertRow(row)
        
        # --- [核心修正] Column 0: Color - 使用 ColorButton 替换 ColorWidget ---
        # 1. 直接创建 ColorButton 实例
        color_button = ColorButton(layer.get('color', QColor(Qt.black)))
        # 2. 连接其 colorChanged 信号
        color_button.colorChanged.connect(lambda r=row, btn=color_button: self._on_layer_color_changed(r, btn.color()))
        # 3. 创建一个容器来居中显示按钮
        cell_widget_color = QWidget()
        cell_layout_color = QHBoxLayout(cell_widget_color)
        cell_layout_color.addWidget(color_button)
        cell_layout_color.setAlignment(Qt.AlignCenter)
        cell_layout_color.setContentsMargins(0,0,0,0)
        # 4. 将容器放入单元格
        self.layer_table.setCellWidget(row, 0, cell_widget_color)

        # Column 1: Name (包含显示/隐藏图标)
        name_item = QTableWidgetItem(layer['name'])
        name_item.setFlags(name_item.flags() | Qt.ItemIsEditable) 
        is_enabled = layer.get('enabled', True)
        if self.icon_manager:
            icon_name = "success" if is_enabled else "hidden"
            name_item.setIcon(self.icon_manager.get_icon(icon_name))
        tooltip_parts = [f"<b>图层: {layer['name']}</b><hr>"]
        df = layer.get('df')
        tooltip_parts.append(f"<b>数据源:</b> {layer.get('data_filename', 'N/A')} ({len(df)}点)" if df is not None else "<b>数据源:</b> 无")
        tooltip_parts.append(f"<b>时间列:</b> {layer.get('time_col', 'N/A')}")
        tooltip_parts.append(f"<b>F0列:</b> {layer.get('f0_col', 'N/A')}")
        tooltip_parts.append(f"<b>分组标签:</b> {layer.get('group_col', '无分组')}")
        name_item.setToolTip("\n".join(tooltip_parts))
        self.layer_table.setItem(row, 1, name_item)
        
        # Column 2: Grouping (分组)
        group_item = QTableWidgetItem(layer.get('group_col', '无分组'))
        group_item.setFlags(group_item.flags() | Qt.ItemIsEditable) 
        self.layer_table.setItem(row, 2, group_item)

    def _on_layer_color_changed(self, row, color):
        """当用户通过ColorWidget更改图层颜色时调用。"""
        if row < len(self.layers):
            self.layers[row]['color'] = color
            self._update_plot()

    def _show_layer_context_menu(self, pos):
        """显示图层列表的右键上下文菜单。"""
        row = self.layer_table.rowAt(pos.y())
        if row < 0: return # 未选中有效行

        menu = QMenu(self)
        layer = self.layers[row]
        is_enabled = layer.get('enabled', True)

        # 显示/隐藏动作
        if is_enabled: toggle_action = menu.addAction(self.icon_manager.get_icon("hidden"), "隐藏图层")
        else: toggle_action = menu.addAction(self.icon_manager.get_icon("show"), "显示图层")
        
        menu.addSeparator()
        
        # 其他操作
        rename_action = menu.addAction(self.icon_manager.get_icon("rename"), "重命名...")
        config_action = menu.addAction(self.icon_manager.get_icon("settings"), "配置...")
        remove_action = menu.addAction(self.icon_manager.get_icon("delete"), "移除图层")
        
        menu.addSeparator()
        
        # 保存单层图片动作
        save_action = menu.addAction(self.icon_manager.get_icon("save"), "保存单层图片...")
        save_action.setEnabled(is_enabled) # 只有显示的图层才能保存单层图片
        
        # 执行菜单并根据选择执行动作
        action = menu.exec_(self.layer_table.mapToGlobal(pos))
        
        if action == toggle_action: self._toggle_layer_visibility(row)
        elif action == rename_action: self.layer_table.editItem(self.layer_table.item(row, 1)) # 触发编辑 (名称在第1列)
        elif action == config_action: self._config_layer(row)
        elif action == remove_action: self._remove_layer(row)
        elif action == save_action: self._save_single_layer_image(row)

    def _on_layer_double_clicked(self, item):
        """双击图层项时，打开配置对话框。"""
        # 现在双击行为是在第1列（名称列）触发的
        if item.column() == 1: 
            self._config_layer(item.row())

    def _on_layer_renamed(self, item):
        """处理图层名称单元格文本改变（重命名完成）的信号。"""
        if item.column() == 1: # 确保是名称列
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
                self._update_plot() # 重命名可能影响图例，所以重绘

    def _on_group_renamed_in_table(self, item):
        """当用户在表格中直接编辑分组标签时调用。"""
        if item.column() == 2: # 确保是分组列
            row = item.row()
            # 避免在 _update_layer_table_row 内部设置item时触发此槽
            if not self.layer_table.signalsBlocked() and row < len(self.layers):
                new_group_name = item.text().strip()
                if not new_group_name: # 如果输入为空，则视为“无分组”
                    new_group_name = "无分组"
                
                # 更新数据模型
                self.layers[row]['group_col'] = new_group_name
                
                # 刷新UI和图表
                self._update_all_group_settings() # 修复后需要更新全局分组
                self._update_plot()

    def _on_layer_selection_changed(self):
        """处理图层列表选中行变化，更新数据预览表格和右侧图层设置面板。"""
        current_row = self.layer_table.currentRow()
        self.current_selected_layer_index = current_row # 更新当前选中索引

        if current_row > -1 and current_row < len(self.layers):
            layer_config = self.layers[current_row]
            # 更新数据预览表格
            self.table_view.setModel(PandasModel(layer_config.get('df', pd.DataFrame())))
            
            # 填充右侧图层设置面板
            self._populate_layer_settings_panel(layer_config)
            
            # 更新右侧面板的标题和启用状态
            title = f"图层设置 ({layer_config['name']})"
            self.layer_settings_group.setTitle(title)
            self.layer_settings_group.setEnabled(True)

        else:
            # 没有选中行或图层被移除，清空预览并禁用右侧面板
            self.table_view.setModel(None)
            self.layer_settings_group.setTitle("图层设置 (未选择图层)")
            self.layer_settings_group.setEnabled(False)
            
        self._update_ui_state() # 更新整体UI状态

    def _toggle_layer_visibility(self, row):
        """切换图层的可见性，并高效地只更新受影响的UI元素。"""
        if row < len(self.layers):
            self.layers[row]['enabled'] = not self.layers[row].get('enabled', True)
            self._update_all_group_settings() # 更新全局分组列表
            self._update_plot() # 重新绘图
            self._update_layer_table_row(row) # 更新表格中的图标

    def _toggle_layer_lock(self, row):
        """[移除] 此功能已从需求中移除。保留此空方法以防止错误。"""
        pass # 此功能已移除，不再有实际操作

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
        
        self._update_plot() # 用单图层数据重绘图表
        
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
            self._update_plot() # 恢复完整图表

    # ==========================================================================
    # 右侧面板 (图层设置) 相关方法
    # ==========================================================================
    def _populate_layer_settings_panel(self, layer_config):
        """
        用选中图层的配置填充右侧的图层设置面板。
        这是一个上下文敏感的UI更新。
        """
        # 阻止信号，避免在设置值时立即触发 _on_current_layer_setting_changed
        self.smoothing_group.blockSignals(True)
        self.smoothing_window_slider.blockSignals(True)
        self.show_points_check.blockSignals(True)
        self.point_size_slider.blockSignals(True)
        self.point_alpha_slider.blockSignals(True)
        # 移除 show_mean_contour_check 的信号阻止，因为它现在是全局的
        # self.show_mean_contour_check.blockSignals(True) 
        self.color_scheme_combo.blockSignals(True) # 这是全局的颜色方案下拉框

        # 填充平滑设置
        self.smoothing_group.setChecked(layer_config.get('smoothing_enabled', True))
        self.smoothing_window_slider.setValue(layer_config.get('smoothing_window', 4))
        self._update_smoothing_label(self.smoothing_window_slider.value())

        # 填充数据点显示设置
        self.show_points_check.setChecked(layer_config.get('show_points', False))
        self.point_size_slider.setValue(layer_config.get('point_size', 10))
        self.point_alpha_slider.setValue(int(layer_config.get('point_alpha', 0.4) * 100))
        
        # 填充颜色方案选择器 (全局的)
        self.color_scheme_combo.setCurrentText(layer_config.get('color_scheme', '默认'))
        # 填充该图层特有的分组颜色和复选框 (已移到 _update_all_group_settings)

        # 填充平均轮廓显示 (这是全局的)
        # self.show_mean_contour_check.setChecked(layer_config.get('show_mean_contour', False))

        # 解除信号阻止
        self.smoothing_group.blockSignals(False)
        self.smoothing_window_slider.blockSignals(False)
        self.show_points_check.blockSignals(False)
        self.point_size_slider.blockSignals(False)
        self.point_alpha_slider.blockSignals(False)
        # 移除 show_mean_contour_check 的信号阻止
        # self.show_mean_contour_check.blockSignals(False) 
        self.color_scheme_combo.blockSignals(False)

    def _update_all_group_settings(self):
        """[修正版] 扫描所有启用图层，更新全局分组列表和UI"""
        all_groups = set()
        for layer in self.layers:
            if not layer.get('enabled', True): continue
            group_col = layer.get('group_col')
            if group_col and group_col != "无分组":
                # 如果分组依据是数据列
                if layer.get('df') is not None and group_col in layer.get('df').columns:
                    all_groups.update(layer['df'][group_col].dropna().astype(str).unique())
                else: # 如果是手动输入的分组
                    all_groups.add(group_col)
        
        # 清除旧的UI控件
        while self.group_settings_layout.count():
            child = self.group_settings_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()
        
        # 更新 self.global_groups 模型，保留旧设置
        old_groups_copy = self.global_groups.copy()
        self.global_groups.clear()
        
        for group_name_str in sorted(list(all_groups), key=str):
            if group_name_str in old_groups_copy:
                self.global_groups[group_name_str] = old_groups_copy[group_name_str]
            else: # 新分组
                self.global_groups[group_name_str] = {'enabled': True, 'color': QColor(next(self.color_cycler))}
        
        # 重建UI
        for group_name_str, settings in self.global_groups.items():
            row_widget = QWidget(); row_layout = QHBoxLayout(row_widget); row_layout.setContentsMargins(0, 0, 0, 0)
            cb = QCheckBox(group_name_str); cb.setChecked(settings['enabled'])
            color_btn = ColorButton(settings['color'])
            row_layout.addWidget(cb, 1); row_layout.addWidget(color_btn); self.group_settings_layout.addWidget(row_widget)
            cb.stateChanged.connect(lambda state, n=group_name_str: self._on_global_group_prop_changed(n, 'enabled', state == Qt.Checked))
            color_btn.colorChanged.connect(lambda n=group_name_str, btn=color_btn: self._on_global_group_prop_changed(n, 'color', btn.color()))

    def _on_global_group_prop_changed(self, group_name, prop, value):
        """处理全局分组属性的变化"""
        if group_name in self.global_groups:
            self.global_groups[group_name][prop] = value
            self._update_plot()

    def _apply_color_scheme_globally(self):
        """[修正版] 将颜色方案应用到所有全局分组"""
        scheme_name = self.color_scheme_combo.currentText()
        # 将颜色方案保存到当前选中图层的配置中
        if self.current_selected_layer_index != -1:
            self.layers[self.current_selected_layer_index]['color_scheme'] = scheme_name

        color_cycle = cycle(self.COLOR_SCHEMES.get(scheme_name, []))
        for group_name_str in sorted(self.global_groups.keys(), key=str):
            self.global_groups[group_name_str]['color'] = QColor(next(color_cycle))
        self._update_all_group_settings() # 重新填充UI以更新颜色按钮
        self._update_plot()
    
    def _on_global_setting_changed(self):
        """[修正版] 当全局设置（时间归一化，F0归一化）变化时，更新UI和绘图。"""
        # 处理F0归一化参数widgets的可见性
        norm_method = self.norm_combo.currentText()
        is_st = (norm_method == "半音 (Semitone)"); is_z = (norm_method == "Z-Score")
        self.st_param_widget.setVisible(is_st); self.z_param_widget.setVisible(is_z)
        
        # 检查平均轮廓显示条件 (这里只需设置其启用状态，setChecked在_update_plot中判断)
        has_active_grouping = False
        for group_name_str, settings in self.global_groups.items():
            if settings.get('enabled', True):
                has_active_grouping = True
                break
        
        self.show_mean_contour_check.setEnabled(has_active_grouping and self.normalize_time_check.isChecked())
        if not (has_active_grouping and self.normalize_time_check.isChecked()):
            self.show_mean_contour_check.setChecked(False)


        self._update_plot() # 重新绘图

    def _on_current_layer_setting_changed(self):
        """当右侧图层设置面板的任何控件变化时调用，将UI状态保存回数据模型。"""
        row = self.layer_table.currentRow()
        if row < 0: return # 没有选中图层
        layer = self.layers[row]
        
        # 从UI控件获取值并保存到当前选中图层的配置字典中
        layer['smoothing_enabled'] = self.smoothing_group.isChecked()
        layer['smoothing_window'] = self.smoothing_window_slider.value()
        layer['show_points'] = self.show_points_check.isChecked()
        layer['point_size'] = self.point_size_slider.value()
        layer['point_alpha'] = self.point_alpha_slider.value() / 100.0 # 滑块值转换回0-1
        # 移除 show_mean_contour_check 的保存，它现在是全局的
        # layer['show_mean_contour'] = self.show_mean_contour_check.isChecked()
        
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
        [最终修正版] 核心绘图逻辑。
        能正确处理从列分组、手动分组和无分组三种情况，并使用全局分组颜色。
        """
        try:
            # 准备绘图
            self.figure.clear(); ax = self.figure.add_subplot(111); self.plotted_lines.clear(); self.hover_annotation = None
            has_any_visible_data = False # 标记是否有任何数据被绘制

            # 全局Z-Score统计 (不变)
            global_mean, global_std = None, None
            if self.norm_combo.currentText() == "Z-Score" and self.z_scope_combo.currentText() == "按整个数据集":
                all_f0 = pd.Series(dtype=float)
                for layer_config in self.layers:
                    if not layer_config.get('enabled', True): continue
                    df = layer_config.get('df')
                    f0_col = layer_config.get('f0_col')
                    if df is not None and f0_col and f0_col in df.columns:
                        all_f0 = pd.concat([all_f0, df[f0_col].dropna()])
                global_mean, global_std = all_f0.mean(), all_f0.std()
                if global_std == 0 or np.isnan(global_std): global_std = 1

            # 用于计算平均轮廓的数据容器
            grouped_data_for_mean_contour = {}

            # 遍历所有数据源进行绘制
            for layer_config in self.layers:
                if not layer_config.get('enabled', True): continue
                
                df_original = layer_config.get('df')
                time_col, f0_col = layer_config.get('time_col'), layer_config.get('f0_col')
                group_col_config = layer_config.get('group_col')

                if df_original is None or not all(c in df_original.columns for c in [time_col, f0_col]):
                    continue
                
                is_manual_group = (group_col_config and group_col_config != "无分组" and group_col_config not in df_original.columns)
                
                # Case 1: 分组依据是DataFrame中的一列
                if group_col_config != "无分组" and not is_manual_group:
                    plot_df_base = df_original.dropna(subset=[time_col, f0_col, group_col_config]).copy()
                    
                    for group_name_str, global_group_settings in self.global_groups.items():
                        if not global_group_settings.get('enabled', True): continue
                        current_group_df = plot_df_base[plot_df_base[group_col_config].astype(str) == group_name_str].copy()
                        if current_group_df.empty: continue

                        df_processed = self._process_single_dataframe(current_group_df, time_col, f0_col, layer_config, global_mean, global_std)
                        if df_processed is None or df_processed.empty: continue

                        t_data, f0_data = df_processed[time_col], df_processed[f0_col]; 
                        color_hex = global_group_settings['color'].name(); 
                        label = f"{layer_config['name']} - {group_name_str}"
                        
                        line, = ax.plot(t_data, f0_data, label=label, color=color_hex, zorder=10, picker=True)
                        # --- [核心修正] 设置拾取半径，替代 tolerance 参数 ---
                        line.set_pickradius(5)
                        self.plotted_lines.append({'line': line, 'label': label, 'data': df_processed[[time_col, f0_col]]})
                        has_any_visible_data = True
                        if layer_config.get('show_points', False): ax.scatter(t_data, f0_data, color=color_hex, s=layer_config.get('point_size', 10), alpha=layer_config.get('point_alpha', 0.4), zorder=5)
                        if group_name_str not in grouped_data_for_mean_contour: grouped_data_for_mean_contour[group_name_str] = {'curves': [], 'color': global_group_settings['color']}
                        grouped_data_for_mean_contour[group_name_str]['curves'].append(df_processed)

                # Case 2: 手动分组
                elif is_manual_group:
                    if group_col_config not in self.global_groups or not self.global_groups[group_col_config].get('enabled', True):
                        continue
                    
                    df_processed = self._process_single_dataframe(df_original.copy(), time_col, f0_col, layer_config, global_mean, global_std)
                    if df_processed is None or df_processed.empty: continue

                    t_data, f0_data = df_processed[time_col], df_processed[f0_col]
                    line_color_obj = self.global_groups[group_col_config]['color']
                    line_color_hex = line_color_obj.name()
                    label = f"{layer_config['name']} - {group_col_config}"

                    line, = ax.plot(t_data, f0_data, label=label, color=line_color_hex, zorder=10, picker=True)
                    # --- [核心修正] 设置拾取半径，替代 tolerance 参数 ---
                    line.set_pickradius(5)
                    self.plotted_lines.append({'line': line, 'label': label, 'data': df_processed[[time_col, f0_col]]})
                    has_any_visible_data = True
                    if layer_config.get('show_points', False): ax.scatter(t_data, f0_data, color=line_color_hex, s=layer_config.get('point_size', 10), alpha=layer_config.get('point_alpha', 0.4), zorder=5)
                    if group_col_config not in grouped_data_for_mean_contour: grouped_data_for_mean_contour[group_col_config] = {'curves': [], 'color': line_color_obj}
                    grouped_data_for_mean_contour[group_col_config]['curves'].append(df_processed)
                
                # Case 3: 无分组
                else:
                    df_processed = self._process_single_dataframe(df_original.copy(), time_col, f0_col, layer_config, global_mean, global_std)
                    if df_processed is None or df_processed.empty: continue

                    t_data, f0_data = df_processed[time_col], df_processed[f0_col]
                    label = layer_config['name']
                    line_color_hex = layer_config.get('color', QColor(Qt.darkGray)).name()
                    
                    line, = ax.plot(t_data, f0_data, label=label, color=line_color_hex, zorder=10, picker=True)
                    # --- [核心修正] 设置拾取半径，替代 tolerance 参数 ---
                    line.set_pickradius(5)
                    self.plotted_lines.append({'line': line, 'label': label, 'data': df_processed[[time_col, f0_col]]})
                    has_any_visible_data = True
                    if layer_config.get('show_points', False): ax.scatter(t_data, f0_data, color=line_color_hex, s=layer_config.get('point_size', 10), alpha=layer_config.get('point_alpha', 0.4), zorder=5)

            # ... (后续的平均轮廓、图表样式设置等代码保持不变) ...
            if self.show_mean_contour_check.isChecked() and self.normalize_time_check.isChecked():
                self._plot_mean_contours(ax, grouped_data_for_mean_contour)
            if has_any_visible_data:
                self.hover_annotation = ax.text(0.98, 0.98, '', transform=ax.transAxes,
                                                ha='right', va='top', fontsize=9,
                                                bbox=dict(boxstyle='round,pad=0.3', fc='wheat', alpha=0.9),
                                                zorder=100) # <-- [核心修正] 赋予一个极高的堆叠顺序
                self.hover_annotation.set_visible(False)
            ax.set_title(self.title_edit.text(), fontsize=14); ax.set_xlabel(self.xlabel_edit.text()); ax.set_ylabel(self.ylabel_edit.text()); ax.grid(True, linestyle='--', alpha=0.6)
            ax.autoscale_view()
            if self.show_legend_check.isChecked() and has_any_visible_data: ax.legend(fontsize='small', labelspacing=1.2)
            self.figure.tight_layout(pad=1.5); self.canvas.draw()
        except Exception as e:
            QMessageBox.critical(self, "绘图失败", f"生成图表时发生错误: {e}\n\n请检查数据列和图层配置。")
            self.figure.clear(); self.canvas.draw()  
    def _process_single_dataframe(self, df, time_col, f0_col, layer_config, global_zscore_mean=None, global_zscore_std=None):
        """对单个DataFrame进行归一化、平滑等处理。"""
        # 只处理需要的列，并复制以避免修改原始数据
        df_processed = df[[time_col, f0_col]].copy()
        
        # --- [核心修正] 在此处修改插值逻辑 ---
        # 在进行任何其他处理之前，先根据开关状态进行插值
        if self.interpolate_gaps_check.isChecked():
            # 移除 limit_direction='both' 参数。
            # interpolate 的默认行为就是只填充数据点之间的 NaN 间隙，
            # 不会填充开头和结尾的 NaN。这正是我们想要的效果。
            df_processed[f0_col] = df_processed[f0_col].interpolate(method='linear')

        # 现在再丢弃所有 NaN 的行（包括那些因为没有被插值而留下的开头/结尾的NaN）
        df_processed.dropna(inplace=True)
        if df_processed.empty: return None

        # --- F0 归一化 (全局设置) ---
        norm_method = self.norm_combo.currentText()
        if norm_method == "半音 (Semitone)":
            try:
                st_ref = float(self.st_ref_edit.text())
            except ValueError:
                st_ref = 100.0 # 默认值，避免转换错误
            if st_ref <= 0: st_ref = 1 # 避免除以零
            df_processed[f0_col] = 12 * np.log2(df_processed[f0_col] / st_ref)
        elif norm_method == "Z-Score":
            z_scope = self.z_scope_combo.currentText()
            if z_scope == "按分组":
                # 对当前曲线的数据独立计算Z-Score
                mean = df_processed[f0_col].mean()
                std = df_processed[f0_col].std()
                if std == 0: std = 1 # 避免除以零
                df_processed[f0_col] = (df_processed[f0_col] - mean) / std
            elif z_scope == "按整个数据集":
                # 使用全局计算的均值和标准差
                if global_zscore_mean is not None and global_zscore_std is not None and global_zscore_std != 0:
                    df_processed[f0_col] = (df_processed[f0_col] - global_zscore_mean) / global_zscore_std
                else: # 避免全局统计无效时报错，可以弹窗警告或跳过
                    pass # 不做Z-Score，如果全局统计不健全

        # --- 时间归一化 (全局设置) ---
        if self.normalize_time_check.isChecked():
            t_min, t_max = df_processed[time_col].min(), df_processed[time_col].max()
            if t_max > t_min:
                df_processed[time_col] = 100 * (df_processed[time_col] - t_min) / (t_max - t_min)
            else:
                df_processed[time_col] = 0 # 避免单点或零时长曲线导致NaN
        
        # --- 平滑 (图层级设置) ---
        if layer_config.get('smoothing_enabled', True):
            win_val = layer_config.get('smoothing_window', 4)
            win_size = 2 * win_val + 1
            # 确保窗口大小不大于数据长度，否则 rolling 会返回 NaN
            if len(df_processed) >= win_size:
                df_processed[f0_col] = df_processed[f0_col].rolling(window=win_size, center=True, min_periods=1).mean()
            else:
                pass # 数据太短无法平滑，跳过

        return df_processed.dropna() # 再次dropna以处理平滑后的NaN
    def _plot_mean_contours(self, ax, grouped_data):
        """计算并绘制分组的平均轮廓线。"""
        # 定义一个统一的、归一化的时间轴 (0-100, 101个点)
        mean_time_axis = np.linspace(0, 100, 101)

        for group_name_str, data in grouped_data.items():
            # 确保只有在时间归一化开启时，才能绘制平均轮廓
            if not self.normalize_time_check.isChecked(): continue

            resampled_curves = []
            for df_processed in data['curves']:
                # 使用np.interp进行插值，将每条曲线重采样到统一时间轴上
                # 确保df_processed中时间列是第一列，F0列是第二列
                resampled_f0 = np.interp(mean_time_axis, df_processed.iloc[:,0], df_processed.iloc[:,1])
                resampled_curves.append(resampled_f0)
            
            if len(resampled_curves) > 0:
                # 计算平均F0
                mean_f0_curve = np.mean(np.array(resampled_curves), axis=0)
                
                # 绘制平均曲线
                # 图例标签：分组名称 + (平均)
                label = f"{group_name_str} (平均)"
                ax.plot(mean_time_axis, mean_f0_curve,
                        label=label,
                        color=data['color'].name(), # 使用分组的颜色
                        linestyle='--',
                        linewidth=3,
                        zorder=20) # 确保平均线在普通线上方

    # ==========================================================================
    # UI状态更新和辅助方法
    # ==========================================================================
    def _update_ui_state(self):
        """根据当前图层数据和选中状态更新UI控件的可用性。"""
        has_layers = bool(self.layers)
        is_layer_selected = self.current_selected_layer_index > -1
        
        self.plot_button.setEnabled(has_layers)
        self.layer_settings_group.setEnabled(is_layer_selected)

        # 检查是否有活跃的分组
        has_active_grouping = False
        # 只要 global_groups 里有任何一个 enabled 的分组，就认为有活跃分组
        for group_name_str, settings in self.global_groups.items():
            if settings.get('enabled', True):
                has_active_grouping = True
                break

        # 根据是否有活跃分组来决定全局分组设置面板的可见性 (总是可见)
        # self.grouping_group.setVisible(has_active_grouping) # 总是可见
        
        # 平均轮廓的启用状态逻辑
        self.show_mean_contour_check.setEnabled(has_active_grouping and self.normalize_time_check.isChecked())
        if not (has_active_grouping and self.normalize_time_check.isChecked()):
            self.show_mean_contour_check.setChecked(False)

    def _show_context_menu(self, pos):
        """显示画布的右键上下文菜单。"""
        menu = QMenu(self)
        
        # 定义动作和对应的图标名称
        actions_with_icons = {
            "从文件加载数据...": "open_folder",
            "separator_1": None,
            "刷新图表": "refresh",
            "重置视图": "zoom_selection",
            "separator_2": None,
            "复制图片": "copy",
            "保存图片...": "save",
            "separator_3": None,
            "清空所有图层...": "clear_contents"
        }
        
        # 创建一个函数映射
        func_map = {
            "从文件加载数据...": self._load_data_from_file_dialog, 
            "刷新图表": self._update_plot,
            "重置视图": self._reset_view,
            "复制图片": self._copy_plot_to_clipboard,
            "保存图片...": self._save_plot_image,
            "清空所有图层...": self._clear_all_data
        }

        for name, icon_key in actions_with_icons.items():
            if "separator" in name:
                menu.addSeparator()
                continue
            
            action = QAction(name, self)
            if self.icon_manager and icon_key:
                action.setIcon(self.icon_manager.get_icon(icon_key))
            
            action.triggered.connect(func_map[name])
            menu.addAction(action)
            
        menu.exec_(self.canvas.mapToGlobal(pos))
 
    def _load_data_from_file_dialog(self):
        """通过文件对话框加载数据，并添加到图层。"""
        path, _ = QFileDialog.getOpenFileName(self, "选择F0数据文件", "", "表格文件 (*.xlsx *.xls *.csv)")
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
        self.global_groups.clear() # 清空全局分组状态
        
        self.table_view.setModel(None) # 清空数据预览表格
        
        self._update_layer_table() # 更新图层列表UI (会清空列表)
        self._on_layer_selection_changed() # 模拟选择变化，清空右侧面板
        self._update_all_group_settings() # 清空数据后更新全局分组UI
        
        # 清空图表
        self.figure.clear()
        self.canvas.draw()
        
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
        """处理鼠标移动事件，用于悬浮提示。"""
        # ... (前半部分的 if not event.inaxes 和 if self._is_panning 逻辑不变) ...
        if not event.inaxes:
            if self.hover_annotation and self.hover_annotation.get_visible():
                self.hover_annotation.set_visible(False)
                self.canvas.draw_idle()
            return
        
        if self._is_panning:
            ax = event.inaxes
            if self._pan_start_pos is None or event.xdata is None or event.ydata is None: return 
            dx = event.xdata - self._pan_start_pos[0]
            dy = event.ydata - self._pan_start_pos[1]
            cur_xlim, cur_ylim = ax.get_xlim(), ax.get_ylim()
            ax.set_xlim(cur_xlim[0] - dx, cur_xlim[1] - dx)
            ax.set_ylim(cur_ylim[0] - dy, cur_ylim[1] - dy)
            self.canvas.draw_idle()
            return
            
        if self.hover_annotation is None: return
 
        found_line = False
        
        for plot_item in self.plotted_lines:
            line = plot_item['line']
            # --- [核心修正] 移除错误的 tolerance 参数 ---
            contains, ind = line.contains(event) 
            
            if contains:
                if 'ind' in ind and len(ind['ind']) > 0:
                    data_index = ind['ind'][0]
                else:
                    # Fallback in case indices are not returned as expected
                    # Find the closest point manually on the line
                    x_data, y_data = line.get_data()
                    distances = np.sqrt((x_data - event.xdata)**2 + (y_data - event.ydata)**2)
                    data_index = np.argmin(distances)

                point_data = plot_item['data'].iloc[data_index]
                time_val = point_data.iloc[0]
                f0_val = point_data.iloc[1]
                
                label = plot_item['label']
                text = f"{label}\n时间: {time_val:.3f} s\nF0: {f0_val:.1f} Hz"
                self.hover_annotation.set_text(text)
                self.hover_annotation.set_visible(True)
                self.canvas.draw_idle()
                found_line = True
                break
        
        if not found_line and self.hover_annotation.get_visible():
            self.hover_annotation.set_visible(False)
            self.canvas.draw_idle()
    # ==========================================================================
    # V2.2 拖拽事件处理 (与plotter.py保持一致)
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
                self._load_and_add_file(path) # 调用我们重构好的加载函数

    def resizeEvent(self, event):
        """当窗口大小改变时，确保覆盖层也跟着改变大小"""
        super().resizeEvent(event)
        self.drop_overlay.setGeometry(self.rect())

    # ==========================================================================
    # 外部接口 (被 PluginManager 调用)
    # ==========================================================================
    def _load_and_add_file(self, file_path):
        """核心的文件加载和添加逻辑，可被多处调用 (拖拽、文件对话框)。"""
        try:
            if file_path.lower().endswith(('.xlsx', '.xls')):
                df = pd.read_excel(file_path)
            elif file_path.lower().endswith('.csv'):
                df = pd.read_csv(file_path)
            else:
                # 如果文件类型不支持，可以选择忽略或提示
                QMessageBox.warning(self, "文件类型不支持", f"文件 '{os.path.basename(file_path)}' 的类型不支持。请选择 .csv 或 .xlsx 文件。")
                return

            self.add_data_source(df, os.path.basename(file_path))
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法读取文件 '{os.path.basename(file_path)}':\n{e}")

    def add_data_source(self, df, source_name="从外部加载"):
        """
        从外部（如音频分析模块）加载 DataFrame，并将其作为新的图层添加到可视化器中。
        :param df: 要加载的 Pandas DataFrame。
        :param source_name: 数据的来源名称，用于生成默认图层名和文件名显示。
        """
        if df is None or df.empty:
            QMessageBox.warning(self, "数据无效", "传入的 DataFrame 为空或无效。")
            return
        
        # 检查是否包含必要的列
        required_numeric_cols = df.select_dtypes(include=np.number).columns.tolist()
        if len(required_numeric_cols) < 2:
            QMessageBox.warning(self, "数据格式错误", "传入的DataFrame至少需要两列数值型数据（时间和F0）。")
            return

        # 自动生成图层名称，确保唯一性
        base_name = os.path.splitext(source_name)[0] if source_name else "新数据"
        layer_name = base_name
        counter = 1
        while any(layer['name'] == layer_name for layer in self.layers):
            layer_name = f"{base_name} ({counter})"
            counter += 1
        
        # 创建一个新的图层配置，并尝试自动填充列名和默认样式
        new_layer_config = {
            "id": str(uuid.uuid4()), # 为每个图层生成一个唯一ID
            "name": layer_name,
            "df": df,
            "data_filename": f"{source_name} (实时数据)" if source_name else "实时数据",
            "time_col": "", # 待自动检测或用户选择
            "f0_col": "", # 待自动检测或用户选择
            "group_col": "无分组", # 初始无分组
            "enabled": True, # 默认启用
            # 默认样式设置（与 _populate_layer_settings_panel 的默认值保持一致）
            "smoothing_enabled": True, # 默认启用平滑
            "smoothing_window": 4,     # 默认4，对应9点窗口
            "show_points": False,      # 默认不显示点
            "point_size": 10,
            "point_alpha": 0.4,
            "show_mean_contour": False, # 默认不显示平均轮廓
            "color_scheme": "默认", # 默认颜色方案
            "groups": {} # 初始为空，populate时填充
        }

        # --- [核心修正] 自动分配图层颜色 (针对每个图层) ---
        new_layer_config['color'] = QColor(next(self.color_cycler))


        # 尝试自动选择时间/F0列
        time_auto = next((c for c in required_numeric_cols if 'time' in c.lower() or 'timestamp' in c.lower()), required_numeric_cols[0])
        f0_auto = next((c for c in required_numeric_cols if 'f0' in c.lower() or 'hz' in c.lower()), required_numeric_cols[1] if len(required_numeric_cols) > 1 else required_numeric_cols[0])
        new_layer_config['time_col'] = time_auto
        new_layer_config['f0_col'] = f0_auto

        # 尝试选择包含“group”或“label”的列作为默认分组
        all_cols = df.columns.tolist()
        group_auto = next((c for c in all_cols if 'group' in c.lower() or 'label' in c.lower() or 'category' in c.lower()), "无分组")
        new_layer_config['group_col'] = group_auto

        self.layers.append(new_layer_config)
        self._update_layer_table() # 更新图层列表UI
        self._update_plot() # 重新绘图
# ==============================================================================
# 插件主入口类
# ==============================================================================
class IntonationVisualizerPlugin(BasePlugin):
    def __init__(self, main_window=None, plugin_manager=None):
        super().__init__(main_window, plugin_manager)
        self.visualizer_dialog = None

    def setup(self):
        if not LIBS_AVAILABLE:
            print("[Intonation Visualizer Error] Missing core dependencies. Plugin setup failed.")
            return False 
        # 尝试将插件实例注册到主程序的音频分析模块
        if hasattr(self, 'main_window') and self.main_window:
            self.audio_analysis_page = getattr(self.main_window, 'audio_analysis_page', None)
            if self.audio_analysis_page and hasattr(self.audio_analysis_page, 'spectrogram_widget'):
                # 注入一个钩子，让音频分析模块能找到本插件实例
                self.audio_analysis_page.spectrogram_widget.intonation_visualizer_plugin_active = self
                print("Intonation Visualizer hooked successfully to Audio Analysis module.")
        else:
            print("Intonation Visualizer: Running in standalone mode or main window not found.")
        return True

    def teardown(self):
        # 移除钩子
        if hasattr(self, 'audio_analysis_page') and self.audio_analysis_page:
            if getattr(getattr(self.audio_analysis_page, 'spectrogram_widget', None), 'intonation_visualizer_plugin_active', None) is self:
                del self.audio_analysis_page.spectrogram_widget.intonation_visualizer_plugin_active
                print("Intonation Visualizer unhooked.")
        if self.visualizer_dialog:
            self.visualizer_dialog.close()

    def execute(self, **kwargs):
        """插件的统一入口，负责显示窗口并加载数据"""
        # 如果窗口不存在，则创建
        if self.visualizer_dialog is None:
            parent = self.main_window if hasattr(self, 'main_window') else None
            icon_manager = getattr(parent, 'icon_manager', None) if parent else None
            self.visualizer_dialog = VisualizerDialog(parent=parent, icon_manager=icon_manager)
            self.visualizer_dialog.finished.connect(self._on_dialog_finished)

        # 检查是否有DataFrame传入，如果有，则调用新的数据添加方法
        dataframe_to_load = kwargs.get('dataframe')
        if dataframe_to_load is not None:
            source_name = kwargs.get('source_name')
            self.visualizer_dialog.add_data_source(dataframe_to_load, source_name)

        # 显示窗口
        self.visualizer_dialog.show()
        self.visualizer_dialog.raise_()
        self.visualizer_dialog.activateWindow()

    def _on_dialog_finished(self):
        """当对话框关闭时，重置实例变量"""
        self.visualizer_dialog = None

# --- 用于独立测试运行 ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    
    # 创建一个模拟的插件实例
    plugin = IntonationVisualizerPlugin()
    
    # 创建两个模拟的DataFrame
    time1 = np.linspace(0, 1.5, 150)
    f0_1 = 120 + 40 * np.sin(2 * np.pi * 1.2 * time1) + np.random.randn(150) * 5
    df1 = pd.DataFrame({'time_sec': time1, 'f0_hz': f0_1, 'group_label': '陈述句'})

    time2 = np.linspace(0, 1.2, 120)
    f0_2 = 220 - 50 * np.cos(2 * np.pi * 1.5 * time2) + np.random.randn(120) * 8
    df2 = pd.DataFrame({'time': time2, 'F0': f0_2, 'group_label': '疑问句'})

    # 通过execute方法加载数据
    plugin.execute(dataframe=df1, source_name="说话人A-男声")
    plugin.execute(dataframe=df2, source_name="说话人B-女声")

    sys.exit(app.exec_())
# ==============================================================================
# 插件主入口类
# ==============================================================================
class IntonationVisualizerPlugin(BasePlugin):
    """
    语调可视化器插件。
    负责插件的生命周期管理，并创建/显示 VisualizerDialog。
    """
    def __init__(self, main_window=None, plugin_manager=None):
        super().__init__(main_window, plugin_manager)
        self.visualizer_dialog = None # 存储对话框实例，实现单例模式

    def setup(self):
        """插件初始化设置。"""
        if not LIBS_AVAILABLE:
            print("[Intonation Visualizer Error] Missing core dependencies. Plugin setup failed.")
            return False 
        # 尝试将插件实例注册到主程序的音频分析模块，以实现数据发送功能
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
        [v2 - 已修复]
        插件的统一入口。此版本修复了 source_name 未被正确处理的问题。
        """
        # 如果窗口不存在，则创建
        if self.visualizer_dialog is None:
            parent = self.main_window if hasattr(self, 'main_window') else None
            icon_manager = getattr(parent, 'icon_manager', None) if parent else None
            self.visualizer_dialog = VisualizerDialog(parent=parent, icon_manager=icon_manager)
            self.visualizer_dialog.finished.connect(self._on_dialog_finished)

        # --- [核心修复] ---
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
    df1 = pd.DataFrame({'time_sec': time1, 'f0_hz': f0_1, 'group_label': '陈述句'})

    time2 = np.linspace(0, 1.2, 120)
    f0_2 = 220 - 50 * np.cos(2 * np.pi * 1.5 * time2) + np.random.randn(120) * 8
    df2 = pd.DataFrame({'time': time2, 'F0': f0_2, 'group_label': '疑问句'})

    # 通过execute方法加载数据
    plugin.execute(dataframe=df1, source_name="说话人A-男声")
    plugin.execute(dataframe=df2, source_name="说话人B-女声")

    sys.exit(app.exec_())

# --- END OF COMPLETE AND REFACTORED FILE ---