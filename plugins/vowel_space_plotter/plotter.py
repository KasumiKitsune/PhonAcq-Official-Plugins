# --- START OF MODIFIED FILE plugins/vowel_space_plotter/plotter.py ---

import os
import sys
import re
import pandas as pd
import numpy as np
from itertools import cycle
from copy import deepcopy # 用于深度复制图层配置

# PyQt5 模块导入
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog,
                             QMessageBox, QTableView, QHeaderView, QComboBox, QCheckBox,
                             QSplitter, QGroupBox, QLineEdit, QFormLayout,
                             QColorDialog, QSlider, QWidget, QScrollArea, QMenu, QFrame, QGridLayout, QApplication,
                             QTableWidget, QTableWidgetItem, QDialogButtonBox)
from PyQt5.QtCore import Qt, QAbstractTableModel, QSize, pyqtSignal
from PyQt5.QtGui import QIcon, QColor, QPalette, QPixmap, QFont, QCursor

# Matplotlib 和 textgrid 库导入
try:
    import matplotlib
    matplotlib.use('Qt5Agg') # 指定 Matplotlib 后端
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    from matplotlib.patches import Ellipse # 用于绘制椭圆
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
        self.df = None # 存储加载的 DataFrame
        self.tg = None # 存储加载的 TextGrid
        # 深度复制一份配置，避免直接修改传入的字典，确保“取消”操作不影响原数据
        self.config = deepcopy(existing_config) if existing_config else {}
        self.parent_dialog = parent # 保存对主对话框 (PlotterDialog) 的引用，用于获取颜色方案

        self.setWindowTitle("配置数据图层")
        self.setMinimumWidth(500)
        self._init_ui()
        self._connect_signals()
        if self.config:
            self._populate_from_config()
        
        # 确保在初始化时更新组合框，即使没有预设配置
        # 这也处理了初始加载df时自动填充列名的情况
        self._update_combos()

    def _init_ui(self):
        """初始化对话框的用户界面。"""
        layout = QVBoxLayout(self)
        form_layout = QFormLayout()
        
        # 图层名称输入框
        self.name_edit = QLineEdit(self.config.get('name', ''))
        self.name_edit.setPlaceholderText("例如：说话人A-男声")
        self.name_edit.setToolTip("为该数据图层指定一个唯一的名称。")
        
        # 数据文件选择
        data_layout = QHBoxLayout()
        self.load_data_btn = QPushButton("选择文件...")
        self.load_data_btn.setToolTip("加载包含共振峰数据的 Excel (.xlsx, .xls) 或 CSV (.csv) 文件。")
        self.data_file_label = QLabel(self.config.get('data_filename', "未选择"))
        self.data_file_label.setWordWrap(True)
        data_layout.addWidget(self.load_data_btn)
        data_layout.addWidget(self.data_file_label, 1)

        # TextGrid文件选择
        tg_layout = QHBoxLayout()
        self.load_tg_btn = QPushButton("选择文件...")
        self.load_tg_btn.setToolTip("加载 TextGrid (.TextGrid) 文件为数据点添加标签。\n数据文件必须包含 'timestamp' 列。")
        self.tg_file_label = QLabel(self.config.get('tg_filename', "未选择 (可选)"))
        self.tg_file_label.setWordWrap(True)
        tg_layout.addWidget(self.load_tg_btn)
        tg_layout.addWidget(self.tg_file_label, 1)

        # 数据列指定（F1, F2, 分组）
        self.f1_combo = QComboBox()
        self.f1_combo.setToolTip("选择代表第一共振峰 (F1) 的数据列，将作为图表的 Y 轴。")
        self.f2_combo = QComboBox()
        self.f2_combo.setToolTip("选择代表第二共振峰 (F2) 的数据列，将作为图表的 X 轴。")
        self.group_by_combo = QComboBox()
        self.group_by_combo.setToolTip("选择用于对数据点进行分组的列。\n选择'无分组'则所有点使用相同样式。\n使用TextGrid后，可选择 'textgrid_label' 进行分组。")
        
        # 颜色方案选择（图层级别）
        self.color_scheme_combo = QComboBox()
        # 从父对话框获取所有颜色方案名称
        self.color_scheme_combo.addItems(self.parent_dialog.COLOR_SCHEMES.keys())
        self.color_scheme_combo.setToolTip("为该图层内的分组选择一个独立的颜色方案。")
        
        # 锁定图层复选框
        self.lock_check = QCheckBox("锁定图层")
        self.lock_check.setToolTip("锁定后，图层将无法在主界面被配置或移除，防止误操作。")

        # 将所有控件添加到表单布局
        form_layout.addRow("图层名称:", self.name_edit)
        form_layout.addRow("数据文件:", data_layout)
        form_layout.addRow("TextGrid:", tg_layout)
        form_layout.addRow(QFrame(frameShape=QFrame.HLine)) # 分隔线
        form_layout.addRow("F1 (Y轴):", self.f1_combo)
        form_layout.addRow("F2 (X轴):", self.f2_combo)
        form_layout.addRow("分组依据:", self.group_by_combo)
        form_layout.addRow("颜色方案:", self.color_scheme_combo)
        form_layout.addRow(self.lock_check)

        layout.addLayout(form_layout)
        
        # 标准确定/取消按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _connect_signals(self):
        """连接UI控件的信号到槽函数。"""
        self.load_data_btn.clicked.connect(self._load_data)
        self.load_tg_btn.clicked.connect(self._load_textgrid)

    def _populate_from_config(self):
        """根据传入的配置字典填充UI控件。"""
        self.df = self.config.get('df') # 恢复 DataFrame 对象
        self.tg = self.config.get('tg') # 恢复 TextGrid 对象
        
        # 恢复文件名标签显示
        if 'data_filename' in self.config:
            self.data_file_label.setText(self.config['data_filename'])
        if 'tg_filename' in self.config:
            self.tg_file_label.setText(self.config['tg_filename'])

        # 恢复颜色方案和锁定状态
        self.color_scheme_combo.setCurrentText(self.config.get('color_scheme', '默认'))
        self.lock_check.setChecked(self.config.get('locked', False))

        self._update_combos() # 先更新组合框内容
        # 再设置当前选中项
        self.f1_combo.setCurrentText(self.config.get('f1_col', ''))
        self.f2_combo.setCurrentText(self.config.get('f2_col', ''))
        self.group_by_combo.setCurrentText(self.config.get('group_col', ''))

    def _load_data(self):
        """加载数据文件（Excel或CSV）到DataFrame。"""
        path, _ = QFileDialog.getOpenFileName(self, "选择数据文件", "", "表格文件 (*.xlsx *.xls *.csv)")
        if not path: return
        try:
            df = pd.read_excel(path) if path.lower().endswith(('.xlsx', '.xls')) else pd.read_csv(path)
            self.df = df
            self.data_file_label.setText(os.path.basename(path))
            self.config['data_filename'] = os.path.basename(path)
            
            # 如果图层名称为空，则使用文件名作为默认名称
            if not self.name_edit.text():
                self.name_edit.setText(os.path.splitext(os.path.basename(path))[0])
            
            # 清除旧的TextGrid信息，因为数据文件变了
            self.tg = None
            self.tg_file_label.setText("未选择 (可选)")
            self.config.pop('tg_filename', None) # 移除旧的tg文件名配置

            self._update_combos() # 更新列选择组合框
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法读取数据文件: {e}")
            self.df = None # 加载失败则清空df

    def _load_textgrid(self):
        """加载 TextGrid 文件并将其标签应用到 DataFrame。"""
        if self.df is None or 'timestamp' not in self.df.columns:
            QMessageBox.warning(self, "需要时间戳", "请先加载一个包含 'timestamp' 列的数据文件。")
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择 TextGrid 文件", "", "TextGrid 文件 (*.TextGrid)")
        if not path: return
        try:
            self.tg = textgrid.TextGrid.fromFile(path)
            self.tg_file_label.setText(os.path.basename(path))
            self.config['tg_filename'] = os.path.basename(path)
            self._apply_textgrid() # 应用后会自动更新combos
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法解析 TextGrid 文件: {e}")
            self.tg = None # 加载失败则清空tg

    def _update_combos(self):
        """根据当前加载的 DataFrame 更新 F1, F2 和分组列的下拉选项。"""
        self.f1_combo.clear()
        self.f2_combo.clear()
        self.group_by_combo.clear()
        self.group_by_combo.addItem("无分组") # 默认选项

        if self.df is None: return

        numeric_cols = self.df.select_dtypes(include=np.number).columns.tolist()
        all_cols = self.df.columns.tolist()

        self.f1_combo.addItems(numeric_cols)
        self.f2_combo.addItems(numeric_cols)
        
        # 添加非数值列作为分组选项
        non_numeric_cols = [col for col in all_cols if col not in numeric_cols]
        if non_numeric_cols:
            self.group_by_combo.addItems(non_numeric_cols)
        
        # 如果有数值列，也添加进去（可选分组）
        if numeric_cols:
            if non_numeric_cols: # 如果前面有非数值列，加个分隔符
                self.group_by_combo.insertSeparator(self.group_by_combo.count())
            self.group_by_combo.addItems(numeric_cols)

        # 尝试自动选择F1/F2列
        f1 = next((c for c in numeric_cols if 'f1' in c.lower()), numeric_cols[0] if numeric_cols else "")
        f2 = next((c for c in numeric_cols if 'f2' in c.lower()), numeric_cols[1] if len(numeric_cols) > 1 else "")
        self.f1_combo.setCurrentText(f1)
        self.f2_combo.setCurrentText(f2)

        # 尝试自动选择TextGrid标签列作为分组
        if 'textgrid_label' in all_cols:
            self.group_by_combo.setCurrentText('textgrid_label')
        else:
            # 尝试选择包含“vowel”或“label”的列作为默认分组
            default_group = next((c for c in all_cols if 'vowel' in c.lower() or 'label' in c.lower()), "无分组")
            self.group_by_combo.setCurrentText(default_group)

    def _apply_textgrid(self):
        """将 TextGrid 的标注应用到 DataFrame，创建 'textgrid_label' 列。"""
        if self.df is None or self.tg is None: return
        
        # 移除旧的 textgrid_label 列，如果存在
        if 'textgrid_label' in self.df.columns:
            self.df = self.df.drop(columns=['textgrid_label'])

        label_col = pd.Series(np.nan, index=self.df.index, dtype=object)
        
        # 简单合并所有IntervalTier的标注
        for tier in self.tg:
            if isinstance(tier, textgrid.IntervalTier):
                for interval in tier:
                    if interval.mark:
                        # 确保 timestamp 列存在
                        if 'timestamp' in self.df.columns:
                            mask = (self.df['timestamp'] >= interval.minTime) & (self.df['timestamp'] < interval.maxTime)
                            label_col.loc[mask] = interval.mark # 使用 .loc 进行基于标签的赋值
                        else:
                            QMessageBox.warning(self, "TextGrid匹配警告", "DataFrame中缺少 'timestamp' 列，无法匹配TextGrid标注。")
                            break # 跳出当前tier的循环
            if 'timestamp' not in self.df.columns: break # 如果没有timestamp，则停止所有tier的匹配
        
        self.df['textgrid_label'] = label_col
        self._update_combos() # 更新组合框，以便选择新的 'textgrid_label' 列
        self.group_by_combo.setCurrentText('textgrid_label') # 自动选中TextGrid标签作为分组

    def get_layer_config(self):
        """
        获取当前对话框中配置的图层信息。
        返回一个字典，包含图层名称、DataFrame、TextGrid对象、列名等。
        """
        if self.df is None:
            QMessageBox.warning(self, "输入无效", "请先加载数据文件。")
            return None
        
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "输入无效", "请输入图层名称。")
            return None
        
        f1_col = self.f1_combo.currentText()
        f2_col = self.f2_combo.currentText()
        group_col = self.group_by_combo.currentText()

        if not f1_col or not f2_col:
            QMessageBox.warning(self, "输入无效", "请为 F1 和 F2 指定数据列。")
            return None
            
        # 收集当前图层的所有样式设置，以便保存
        current_layer_settings = {
            "point_size": self.config.get('point_size', 15),
            "point_alpha": self.config.get('point_alpha', 0.3),
            "marker": self.config.get('marker', '圆点'),
            "mean_enabled": self.config.get('mean_enabled', False),
            "mean_marker": self.config.get('mean_marker', '加号'),
            "mean_size": self.config.get('mean_size', 100),
            "ellipse_enabled": self.config.get('ellipse_enabled', False),
            "ellipse_std": self.config.get('ellipse_std', '2 (95%)'),
            "ellipse_style": self.config.get('ellipse_style', '实线'),
            "ellipse_width": self.config.get('ellipse_width', 2),
            "groups": deepcopy(self.config.get('groups', {})) # 深度复制分组设置
        }

        # 更新配置字典
        self.config.update({
            "name": name,
            "df": self.df,
            "tg": self.tg,
            "data_filename": self.data_file_label.text(), # 保存文件名用于显示
            "tg_filename": self.tg_file_label.text(),     # 保存tg文件名用于显示
            "f1_col": f1_col,
            "f2_col": f2_col,
            "group_col": group_col,
            "enabled": self.config.get('enabled', True), # 默认启用
            "color_scheme": self.color_scheme_combo.currentText(), # 保存图层独立的颜色方案
            "locked": self.lock_check.isChecked(),
            **current_layer_settings # 将所有样式设置也合并进来
        })
        return self.config

# ==============================================================================
# 核心UI类：绘图器对话框 (最终版)
# ==============================================================================
class PlotterDialog(QDialog):
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
            # 延迟关闭对话框，确保错误消息能显示
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(0, self.reject); return

        self.setWindowTitle("元音空间图绘制器")
        self.resize(1300, 850) # 初始窗口大小
        self.setMinimumSize(1100, 700) # 最小窗口大小
        self.icon_manager = icon_manager # 获取图标管理器实例
        
        # --- 核心数据结构 ---
        self.layers = [] # 存储所有图层的配置信息 (列表中的每个元素是一个字典)
        self.current_selected_layer_index = -1 # 当前在图层表格中选中的图层索引

        # --- 交互功能的状态变量 ---
        self._is_panning = False # 标记是否正在平移图表
        self._pan_start_pos = None # 平移起始点（数据坐标）
        self.plotted_collections = [] # 存储所有绘图对象（Matplotlib artists），用于鼠标交互
        self.hover_annotation = None # 用于显示鼠标悬停信息的文本对象

        # 初始化UI和连接信号
        self._init_ui()
        self._connect_signals()
        self._update_ui_state() # 初始化UI控件的可用状态

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
        """创建左侧面板，包含图层管理器和绘图操作按钮。"""
        panel = QWidget()
        panel.setFixedWidth(350) # 固定宽度
        layout = QVBoxLayout(panel)
        
        # 图层管理器组框
        layer_group = QGroupBox("图层管理器")
        layer_layout = QVBoxLayout(layer_group)

        # 图层列表表格
        self.layer_table = QTableWidget()
        self.layer_table.setColumnCount(2) # 只有两列：名称和锁定图标
        self.layer_table.setHorizontalHeaderLabels(["图层名称", ""]) # 表头标签
        self.layer_table.horizontalHeader().setVisible(False) # 隐藏表头
        self.layer_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch) # 名称列自适应宽度
        self.layer_table.setColumnWidth(1, 40) # 锁定图标列固定宽度
        self.layer_table.setSelectionBehavior(QTableWidget.SelectRows) # 选中整行
        self.layer_table.setSelectionMode(QTableWidget.SingleSelection) # 单选
        self.layer_table.verticalHeader().setVisible(False) # 隐藏行号
        self.layer_table.setToolTip("右键单击进行操作，双击可配置图层。")
        self.layer_table.setContextMenuPolicy(Qt.CustomContextMenu) # 启用右键菜单

        # 添加新图层按钮
        btn_layout = QHBoxLayout()
        self.add_layer_btn = QPushButton(" 添加新图层...")
        if self.icon_manager: self.add_layer_btn.setIcon(self.icon_manager.get_icon("add_row"))
        btn_layout.addWidget(self.add_layer_btn)
        btn_layout.addStretch() # 将按钮推到左侧

        layer_layout.addWidget(self.layer_table)
        layer_layout.addLayout(btn_layout)

        # 绘图操作组框
        action_group = QGroupBox("绘图操作")
        action_layout = QVBoxLayout(action_group)
        self.plot_button = QPushButton(" 更新图表")
        self.plot_button.setToolTip("根据当前所有设置，重新绘制图表。")
        if self.icon_manager: self.plot_button.setIcon(self.icon_manager.get_icon("chart"))
        action_layout.addWidget(self.plot_button)

        # 将组框添加到面板布局
        layout.addWidget(layer_group, 1) # 图层管理器可伸缩
        layout.addWidget(action_group)
        return panel

    def _create_right_panel(self):
        """创建右侧面板，包含全局设置和上下文敏感的图层设置。"""
        # 使用 QScrollArea 确保在小屏幕上所有选项可见
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(420) # 固定宽度
        scroll.setFrameShape(QScrollArea.NoFrame) # 无边框
        
        # 滚动区域内的主要容器
        panel = QWidget()
        layout = QVBoxLayout(panel)
        scroll.setWidget(panel)

        # --- 全局设置组框 ---
        global_group = QGroupBox("全局设置")
        global_layout = QFormLayout(global_group)
        self.title_edit = QLineEdit("元音空间图")
        self.xlabel_edit = QLineEdit("F2 (Hz)")
        self.ylabel_edit = QLineEdit("F1 (Hz)")
        self.title_edit.setToolTip("设置图表的总标题。")
        self.xlabel_edit.setToolTip("设置图表 X 轴的标签文本。")
        self.ylabel_edit.setToolTip("设置图表 Y 轴的标签文本。")
        global_layout.addRow("图表标题:", self.title_edit)
        global_layout.addRow("X轴标签:", self.xlabel_edit)
        global_layout.addRow("Y轴标签:", self.ylabel_edit)
        
        self.flip_x_check = QCheckBox("翻转 X 轴 (F2)")
        self.flip_x_check.setChecked(True)
        self.flip_x_check.setToolTip("勾选后，X 轴数值将从右向左递增，符合语音学惯例。")
        self.flip_y_check = QCheckBox("翻转 Y 轴 (F1)")
        self.flip_y_check.setChecked(True)
        self.flip_y_check.setToolTip("勾选后，Y 轴数值将从下向上递增，符合语音学惯例。")
        
        self.x_min_edit, self.x_max_edit = QLineEdit(), QLineEdit()
        self.x_min_edit.setPlaceholderText("自动")
        self.x_max_edit.setPlaceholderText("自动")
        self.x_min_edit.setToolTip("设置X轴的最小值。留空则自动计算。")
        self.x_max_edit.setToolTip("设置X轴的最大值。留空则自动计算。")
        x_range_layout = QHBoxLayout()
        x_range_layout.addWidget(self.x_min_edit)
        x_range_layout.addWidget(QLabel("到"))
        x_range_layout.addWidget(self.x_max_edit)
        
        self.y_min_edit, self.y_max_edit = QLineEdit(), QLineEdit()
        self.y_min_edit.setPlaceholderText("自动")
        self.y_max_edit.setPlaceholderText("自动")
        self.y_min_edit.setToolTip("设置Y轴的最小值。留空则自动计算。")
        self.y_max_edit.setToolTip("设置Y轴的最大值。留空则自动计算。")
        y_range_layout = QHBoxLayout()
        y_range_layout.addWidget(self.y_min_edit)
        y_range_layout.addWidget(QLabel("到"))
        y_range_layout.addWidget(self.y_max_edit)
        
        global_layout.addRow(self.flip_x_check)
        global_layout.addRow(self.flip_y_check)
        global_layout.addRow("X轴范围:", x_range_layout)
        global_layout.addRow("Y轴范围:", y_range_layout)
        
        self.show_legend_check = QCheckBox("显示图例")
        self.show_legend_check.setChecked(True)
        self.show_legend_check.setToolTip("是否在图表上显示图例（仅在分组时有效）。")
        global_layout.addRow(self.show_legend_check)

        # --- 图层设置组框 (上下文敏感，默认禁用) ---
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

    def _connect_signals(self):
        """连接所有UI控件的信号到槽函数。"""
        # 左侧面板 - 图层管理
        self.add_layer_btn.clicked.connect(self._add_layer)
        self.layer_table.customContextMenuRequested.connect(self._show_layer_context_menu) # 右键菜单
        self.layer_table.itemDoubleClicked.connect(self._on_layer_double_clicked) # 双击配置
        self.layer_table.itemChanged.connect(self._on_layer_renamed) # 重命名完成
        self.layer_table.itemSelectionChanged.connect(self._on_layer_selection_changed) # 选中行变化
        self.plot_button.clicked.connect(self._plot_data) # 更新图表

        # 右侧 - 全局设置
        self.title_edit.textChanged.connect(self._plot_data); self.xlabel_edit.textChanged.connect(self._plot_data); self.ylabel_edit.textChanged.connect(self._plot_data)
        self.flip_x_check.stateChanged.connect(self._plot_data); self.flip_y_check.stateChanged.connect(self._plot_data)
        self.x_min_edit.textChanged.connect(self._plot_data); self.x_max_edit.textChanged.connect(self._plot_data); self.y_min_edit.textChanged.connect(self._plot_data); self.y_max_edit.textChanged.connect(self._plot_data)
        self.show_legend_check.stateChanged.connect(self._plot_data)
        
        # 右侧 - 图层设置 (所有变化都触发 _on_current_layer_setting_changed)
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
        
        # 图层专属颜色方案应用
        self.apply_color_scheme_btn_layer.clicked.connect(self._apply_color_scheme_to_current_layer)
        
        # 画布交互 (鼠标平移、滚轮缩放、右键菜单)
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
                x_data, y_data = event.x(), self.canvas.height() - event.y()
                
                # Matplotlib的transform_point需要的是(x_pixel, y_pixel)
                trans = ax.transData.inverted()
                mouse_x, mouse_y = trans.transform_point((x_data, y_data))

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
                # 智能分配标记形状（如果未在LayerConfigDialog中指定）
                if 'marker' not in config:
                    used_markers = {l.get('marker') for l in self.layers if 'marker' in l}
                    available = [m for m in self.MARKER_STYLES.keys() if m not in used_markers]
                    config['marker'] = cycle(available or self.MARKER_STYLES.keys()).__next__() # 确保总能取到
                
                # 智能分配初始分组颜色（如果未在LayerConfigDialog中指定）
                if 'groups' not in config:
                    config['groups'] = {} # 初始化为空字典，后续populate时填充
                
                self.layers.append(config)
                self._update_layer_table() # 更新图层列表UI
                self._update_ui_state() # 更新UI状态（如按钮启用状态）
                self._plot_data() # 重新绘图

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
        self._plot_data() # 移除图层后重绘

    def _config_layer(self, row_to_config=None):
        """配置指定行或当前选中行的图层。"""
        current_row = row_to_config if row_to_config is not None else self.layer_table.currentRow()
        if current_row < 0: return # 没有选中行或无效行
        
        config_to_edit = self.layers[current_row]
        # 如果图层被锁定，则不能配置
        if config_to_edit.get('locked', False):
            QMessageBox.information(self, "图层已锁定", "该图层已被锁定，请先解锁后再进行配置。")
            return

        dialog = LayerConfigDialog(existing_config=config_to_edit, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            new_config = dialog.get_layer_config() # 获取更新后的配置
            if new_config:
                self.layers[current_row] = new_config # 更新图层列表中的配置
                self._update_layer_table_row(current_row) # 只更新该行UI
                self._on_layer_selection_changed() # 模拟选择变化，刷新右侧面板
                self._plot_data() # 配置可能影响图表，重绘

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
        name_item.setFlags(name_item.flags() | Qt.ItemIsEditable if not layer.get('locked', False) else name_item.flags() & ~Qt.ItemIsEditable)
        
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
        row = self.layer_table.rowAt(pos.y())
        if row < 0: return # 未选中有效行

        menu = QMenu(self)
        layer = self.layers[row]
        is_enabled = layer.get('enabled', True)
        is_locked = layer.get('locked', False)

        # 显示/隐藏动作
        if is_enabled: toggle_action = menu.addAction(self.icon_manager.get_icon("hidden"), "隐藏图层")
        else: toggle_action = menu.addAction(self.icon_manager.get_icon("show"), "显示图层")
        
        # 锁定/解锁动作
        if is_locked: lock_action = menu.addAction(self.icon_manager.get_icon("unlock"), "解锁图层")
        else: lock_action = menu.addAction(self.icon_manager.get_icon("lock"), "锁定图层")
        
        menu.addSeparator()
        
        # 其他操作，根据锁定状态禁用
        rename_action = menu.addAction(self.icon_manager.get_icon("rename"), "重命名...")
        rename_action.setEnabled(not is_locked)
        config_action = menu.addAction(self.icon_manager.get_icon("settings"), "配置...")
        config_action.setEnabled(not is_locked)
        remove_action = menu.addAction(self.icon_manager.get_icon("delete"), "移除图层")
        remove_action.setEnabled(not is_locked)
        
        menu.addSeparator()
        
        # 保存单层图片动作
        save_action = menu.addAction(self.icon_manager.get_icon("save"), "保存单层图片...")
        save_action.setEnabled(is_enabled) # 只有显示的图层才能保存单层图片
        
        # 执行菜单并根据选择执行动作
        action = menu.exec_(self.layer_table.mapToGlobal(pos))
        
        if action == toggle_action: self._toggle_layer_visibility(row)
        elif action == lock_action: self._toggle_layer_lock(row)
        elif action == rename_action: self.layer_table.editItem(self.layer_table.item(row, 0)) # 触发编辑
        elif action == config_action: self._config_layer(row)
        elif action == remove_action: self._remove_layer(row)
        elif action == save_action: self._save_single_layer_image(row)

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
        """处理图层列表选中行变化，更新数据预览表格和右侧图层设置面板。"""
        current_row = self.layer_table.currentRow()
        self.current_selected_layer_index = current_row # 更新当前选中索引

        if current_row > -1 and current_row < len(self.layers):
            layer_config = self.layers[current_row]
            # 更新数据预览表格
            self.table_view.setModel(PandasModel(layer_config.get('df', pd.DataFrame())))
            
            # 填充右侧图层设置面板
            self._populate_layer_settings_panel(layer_config)
            
            # --- [核心修正] ---
            # 检查图层是否被锁定
            is_locked = layer_config.get('locked', False)
            
            # 根据锁定状态更新标题并启用/禁用整个图层设置组
            title = f"图层设置 ({layer_config['name']})"
            if is_locked:
                title += " (已锁定)"
            self.layer_settings_group.setTitle(title)
            self.layer_settings_group.setEnabled(not is_locked)

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
        核心绘图逻辑。遍历所有启用的图层，并根据其各自的配置绘制数据点、平均值和椭圆。
        """
        try:
            self.figure.clear() # 清空画布
            ax = self.figure.add_subplot(111) # 添加子图
            self.plotted_collections.clear() # 清空之前绘制的集合，用于鼠标交互
            self.hover_annotation = None # 清空悬浮提示

            has_any_visible_data = False # 标记是否有任何数据被绘制，用于决定是否显示图例

            for layer_config in self.layers:
                # 只处理启用的图层
                if not layer_config.get('enabled', True): continue
                
                df = layer_config.get('df')
                f1_col, f2_col, group_col = layer_config.get('f1_col'), layer_config.get('f2_col'), layer_config.get('group_col')

                # 确保DataFrame和所需的列都有效
                if df is None or not all(c in df.columns for c in [f1_col, f2_col]): continue
                
                # 从图层配置中获取样式参数
                layer_marker_text = layer_config.get('marker', '圆点')
                layer_marker = self.MARKER_STYLES.get(layer_marker_text, 'o')
                point_size = layer_config.get('point_size', 15)
                point_alpha = layer_config.get('point_alpha', 0.3)
                
                # 检查是否进行分组绘制（如果分组列有效且不是“无分组”）
                if group_col != "无分组" and group_col in df.columns:
                    plot_df_base = df.dropna(subset=[f1_col, f2_col, group_col])
                    groups_in_layer = layer_config.get('groups', {}) # 获取该图层专属的分组设置
                    
                    for group_name_str, group_settings in groups_in_layer.items():
                        if not group_settings.get('enabled', True): continue # 跳过被禁用的分组
                        
                        # 确保比较时类型一致，从DataFrame中筛选出当前分组的数据
                        group_data = plot_df_base[plot_df_base[group_col].astype(str) == group_name_str]
                        if group_data.empty: continue # 如果分组数据为空，跳过

                        f1, f2 = group_data[f1_col], group_data[f2_col]
                        color_hex = group_settings['color'].name() # 获取分组颜色（十六进制字符串）
                        
                        # 图例标签格式：图层名称 - 分组名称
                        label = f"{layer_config['name']} - {group_name_str}"
                        
                        # 绘制散点图
                        collection = ax.scatter(f2, f1, label=label, color=color_hex, marker=layer_marker, s=point_size, alpha=point_alpha, picker=True)
                        self.plotted_collections.append({'collection': collection, 'label': label, 'data': group_data[[f1_col, f2_col]]})
                        has_any_visible_data = True

                        # 绘制平均值点（如果启用）
                        if layer_config.get('mean_enabled', False):
                            self._plot_mean(f2, f1, ax, color_hex, layer_config)
                        # 绘制标准差椭圆（如果启用且数据点足够）
                        if layer_config.get('ellipse_enabled', False) and len(f1) > 2:
                            self._plot_ellipse(f2, f1, ax, color_hex, layer_config)
                else: # 无分组的图层
                    plot_df = df.dropna(subset=[f1_col, f2_col])
                    if plot_df.empty: continue # 如果数据为空，跳过

                    f1, f2 = plot_df[f1_col], plot_df[f2_col]
                    label = layer_config['name']
                    
                    # 为无分组图层使用默认颜色（例如：深灰色）
                    # 也可以从 layer_config 中添加一个 ungrouped_color 属性
                    color_hex = QColor(Qt.darkGray).name()
                    
                    # 绘制散点图
                    collection = ax.scatter(f2, f1, label=label, color=color_hex, marker=layer_marker, s=point_size, alpha=point_alpha, picker=True)
                    self.plotted_collections.append({'collection': collection, 'label': label, 'data': plot_df[[f1_col, f2_col]]})
                    has_any_visible_data = True

            # 创建悬浮提示文本框（只创建一次）
            # 注意：这里需要确保 ax 是有效的，即至少有一个图层被绘制
            if has_any_visible_data:
                self.hover_annotation = ax.text(0.98, 0.98, '', transform=ax.transAxes, ha='right', va='top', fontsize=9, bbox=dict(boxstyle='round,pad=0.3', fc='wheat', alpha=0.9))
                self.hover_annotation.set_visible(False)
            
            # 设置图表标题和轴标签
            ax.set_title(self.title_edit.text(), fontsize=14); ax.set_xlabel(self.xlabel_edit.text()); ax.set_ylabel(self.ylabel_edit.text()); ax.grid(True, linestyle='--', alpha=0.6)
            
            # 设置轴范围（如果用户输入了）
            try:
                if self.x_min_edit.text() and self.x_max_edit.text(): ax.set_xlim(float(self.x_min_edit.text()), float(self.x_max_edit.text()))
                if self.y_min_edit.text() and self.y_max_edit.text(): ax.set_ylim(float(self.y_min_edit.text()), float(self.y_max_edit.text()))
            except ValueError: pass # 忽略无效输入

            # 翻转轴（如果勾选了）
            if self.flip_x_check.isChecked(): ax.invert_xaxis()
            if self.flip_y_check.isChecked(): ax.invert_yaxis()
            
            # 显示图例（如果勾选且有数据）
            if self.show_legend_check.isChecked() and has_any_visible_data:
                # labelspacing 参数增加图例条目之间的垂直间距，改善长标签显示
                ax.legend(fontsize='small', labelspacing=1.2)

            self.figure.tight_layout(pad=1.5); self.canvas.draw()
        except Exception as e:
            QMessageBox.critical(self, "绘图失败", f"生成图表时发生错误: {e}\n\n请检查数据列和图层配置。")
            # 清空图表以防止显示部分错误数据
            self.figure.clear()
            self.canvas.draw()

    def _plot_mean(self, x, y, ax, color_hex, layer_config):
        """
        绘制平均值点。
        :param color_hex: 当前分组的颜色（十六进制字符串）。
        :param layer_config: 包含该图层平均值点样式设置的字典。
        """
        mean_x, mean_y = x.mean(), y.mean()
        marker_char = self.MARKER_STYLES.get(layer_config.get('mean_marker', '加号'), '+')
        kwargs = {
            'color': color_hex,
            's': layer_config.get('mean_size', 100),
            'marker': marker_char,
            'zorder': 10 # 确保平均值点在数据点上方
        }
        # 如果标记不是填充类型，则添加白色边框
        if marker_char not in ['+', 'x', '|', '_']: kwargs.update({'edgecolors': 'white', 'linewidths': 1.5})
        ax.scatter(mean_x, mean_y, **kwargs)

    def _plot_ellipse(self, x, y, ax, color_hex, layer_config):
        """
        绘制标准差椭圆。
        :param color_hex: 当前分组的颜色（十六进制字符串）。
        :param layer_config: 包含该图层椭圆样式设置的字典。
        """
        cov = np.cov(x, y)
        mean_x, mean_y = np.mean(x), np.mean(y)
        
        # 计算椭圆的轴长和旋转角度
        lambda_, v = np.linalg.eig(cov) # 特征值和特征向量
        lambda_ = np.sqrt(lambda_) # 特征值平方根得到半轴长
        
        # 获取标准差倍数
        std_multiplier_str = layer_config.get('ellipse_std', '2 (95%)')
        std_multiplier = float(std_multiplier_str.split()[0]) # 从字符串中提取数值
        
        # 创建椭圆对象
        ell = Ellipse(
            xy=(mean_x, mean_y),
            width=lambda_[0] * std_multiplier * 2, # 宽度
            height=lambda_[1] * std_multiplier * 2, # 高度
            angle=np.rad2deg(np.arccos(v[0, 0])), # 旋转角度
            edgecolor=color_hex, # 边框颜色
            facecolor='none', # 无填充
            linestyle=self.LINE_STYLES.get(layer_config.get('ellipse_style', '实线'), '-'), # 线条样式
            linewidth=layer_config.get('ellipse_width', 2) # 线条宽度
        )
        ax.add_patch(ell) # 将椭圆添加到图表

    # ==========================================================================
    # UI状态更新和辅助方法
    # ==========================================================================
    def _update_ui_state(self):
        """根据当前图层数据和选中状态更新UI控件的可用性。"""
        has_layers = bool(self.layers)
        is_layer_selected = self.current_selected_layer_index > -1
        
        self.plot_button.setEnabled(has_layers)
        self.layer_settings_group.setEnabled(is_layer_selected)

        # 检查当前选中的图层是否有活动的分组
        has_active_grouping_in_selected_layer = False
        if is_layer_selected:
            layer = self.layers[self.current_selected_layer_index]
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
 
        # 核心操作
        refresh_action = context_menu.addAction("刷新图表")
        reset_view_action = context_menu.addAction("重置视图/缩放")
        if self.icon_manager: 
            refresh_action.setIcon(self.icon_manager.get_icon("refresh"))
            reset_view_action.setIcon(self.icon_manager.get_icon("zoom_selection")) 
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
        
        if action == refresh_action: self._plot_data()
        elif action == reset_view_action: self._reset_view()
        elif action == copy_action: self._copy_plot_to_clipboard()
        elif action == save_action: self._save_plot_image()
        elif action == clear_action: self._clear_all_data()
 
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

    def _plot_mean(self, x, y, ax, color_hex, layer_config):
        """
        绘制平均值点。
        :param color_hex: 当前分组的颜色（十六进制字符串）。
        :param layer_config: 包含该图层平均值点样式设置的字典。
        """
        mean_x, mean_y = x.mean(), y.mean()
        marker_char = self.MARKER_STYLES.get(layer_config.get('mean_marker', '加号'), '+')
        kwargs = {
            'color': color_hex,
            's': layer_config.get('mean_size', 100),
            'marker': marker_char,
            'zorder': 10 # 确保平均值点在数据点上方
        }
        # 如果标记不是填充类型，则添加白色边框
        if marker_char not in ['+', 'x', '|', '_']: kwargs.update({'edgecolors': 'white', 'linewidths': 1.5})
        ax.scatter(mean_x, mean_y, **kwargs)

    def _plot_ellipse(self, x, y, ax, color_hex, layer_config):
        """
        绘制标准差椭圆。
        :param color_hex: 当前分组的颜色（十六进制字符串）。
        :param layer_config: 包含该图层椭圆样式设置的字典。
        """
        cov = np.cov(x, y) # 计算协方差矩阵
        mean_x, mean_y = np.mean(x), np.mean(y) # 计算平均值
        
        # 计算椭圆的轴长和旋转角度
        lambda_, v = np.linalg.eig(cov) # 特征值和特征向量
        lambda_ = np.sqrt(lambda_) # 特征值平方根得到半轴长
        
        # 获取标准差倍数
        std_multiplier_str = layer_config.get('ellipse_std', '2 (95%)')
        std_multiplier = float(std_multiplier_str.split()[0]) # 从字符串中提取数值
        
        # 创建椭圆对象
        ell = Ellipse(
            xy=(mean_x, mean_y),
            width=lambda_[0] * std_multiplier * 2, # 宽度
            height=lambda_[1] * std_multiplier * 2, # 高度
            angle=np.rad2deg(np.arccos(v[0, 0])), # 旋转角度
            edgecolor=color_hex, # 边框颜色
            facecolor='none', # 无填充
            linestyle=self.LINE_STYLES.get(layer_config.get('ellipse_style', '实线'), '-'), # 线条样式
            linewidth=layer_config.get('ellipse_width', 2) # 线条宽度
        )
        ax.add_patch(ell) # 将椭圆添加到图表
    
    def load_dataframe(self, df, source_name="来自音频分析模块"):
        """
        从外部（如音频分析模块）加载 DataFrame，并将其作为新的图层添加到绘图器中。
        """
        if df is None or df.empty:
            QMessageBox.warning(self, "加载失败", "传入的 DataFrame 为空或无效。")
            return
        
        # 自动生成图层名称
        layer_name = f"{source_name} - {len(self.layers) + 1}"
        
        # 创建一个新的图层配置，并尝试自动填充列名和默认样式
        new_layer_config = {
            "name": layer_name,
            "df": df,
            "tg": None, # 初始无TextGrid
            "data_filename": f"{source_name} (实时数据)",
            "tg_filename": "未选择 (可选)",
            "f1_col": "", # 待自动检测或用户选择
            "f2_col": "", # 待自动检测或用户选择
            "group_col": "无分组", # 初始无分组
            "enabled": True, # 默认启用
            "locked": False, # 默认未锁定
            # 默认样式设置（与_populate_layer_settings_panel的默认值保持一致）
            "point_size": 15,
            "point_alpha": 0.3,
            "marker": "圆点", 
            "mean_enabled": False,
            "mean_marker": "加号",
            "mean_size": 100,
            "ellipse_enabled": False,
            "ellipse_std": "2 (95%)",
            "ellipse_style": "实线",
            "ellipse_width": 2,
            "color_scheme": "默认", # 默认颜色方案
            "groups": {} # 初始为空，populate时填充
        }

        # 尝试自动选择F1/F2列
        numeric_cols = df.select_dtypes(include=np.number).columns.tolist()
        f1_auto = next((c for c in numeric_cols if 'f1' in c.lower()), numeric_cols[0] if numeric_cols else "")
        f2_auto = next((c for c in numeric_cols if 'f2' in c.lower()), numeric_cols[1] if len(numeric_cols) > 1 else "")
        new_layer_config['f1_col'] = f1_auto
        new_layer_config['f2_col'] = f2_auto

        # 尝试选择包含“vowel”或“label”的列作为默认分组
        all_cols = df.columns.tolist()
        default_group = next((c for c in all_cols if 'vowel' in c.lower() or 'label' in c.lower()), "无分组")
        new_layer_config['group_col'] = default_group

        self.layers.append(new_layer_config)
        self._update_layer_table() # 更新图层列表UI
        self._plot_data() # 重新绘图

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
        """插件执行入口点。当用户从插件菜单点击或从其他模块调用时触发。"""
        # 实现单例模式：如果对话框已存在则显示，否则创建新的
        if self.plotter_dialog is None:
            self.plotter_dialog = PlotterDialog(parent=self.main_window, icon_manager=getattr(self.main_window, 'icon_manager', None))
            # 连接对话框的 finished 信号，当对话框关闭时清除引用
            self.plotter_dialog.finished.connect(self._on_dialog_finished)
        
        # 检查是否有 DataFrame 参数传入，如果有则加载为新图层
        dataframe_to_load = kwargs.get('dataframe')
        if dataframe_to_load is not None:
            self.plotter_dialog.load_dataframe(dataframe_to_load)

        # 显示对话框并将其置于顶层，确保用户能看到它
        self.plotter_dialog.show()
        self.plotter_dialog.raise_()
        self.plotter_dialog.activateWindow()

    def _on_dialog_finished(self):
        """绘图器对话框关闭时的回调。"""
        self.plotter_dialog = None

# --- END OF MODIFIED FILE ---