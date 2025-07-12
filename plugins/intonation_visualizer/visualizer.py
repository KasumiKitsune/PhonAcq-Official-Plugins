# --- START OF COMPLETE AND UPGRADED FILE plugins/intonation_visualizer/visualizer.py ---

import os
import sys
import uuid
import pandas as pd
import numpy as np
from itertools import cycle

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog,
                             QMessageBox, QTableView, QHeaderView, QComboBox, QCheckBox,
                             QSplitter, QGroupBox, QLineEdit, QFormLayout,
                             QColorDialog, QSlider, QWidget, QScrollArea, QMenu, QFrame,
                             QTableWidget, QTableWidgetItem, QAbstractItemView, QItemDelegate,
                             QApplication, QAction, QGridLayout)
from PyQt5.QtCore import Qt, QAbstractTableModel, pyqtSignal, QEvent
from PyQt5.QtGui import QIcon, QColor, QPalette, QPixmap, QFont, QPainter

# --- 依赖库导入 ---
try:
    import matplotlib
    matplotlib.use('Qt5Agg')
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D

    # 设置中文字体，确保图表能正确显示中文
    def set_matplotlib_font():
        font_candidates = ['Microsoft YaHei', 'SimHei', 'Source Han Sans CN', 'PingFang SC', 'WenQuanYi Micro Hei', 'Arial Unicode MS']
        from matplotlib.font_manager import findfont, FontProperties
        found_font = next((font for font in font_candidates if findfont(FontProperties(family=font))), None)
        if found_font:
            matplotlib.rcParams['font.sans-serif'] = [found_font]
            matplotlib.rcParams['axes.unicode_minus'] = False
        else:
            print("[Intonation Visualizer Warning] No suitable Chinese font found for Matplotlib.")
    set_matplotlib_font()
    LIBS_AVAILABLE = True
except ImportError as e:
    print(f"[Intonation Visualizer Error] Missing core libraries: {e}. Please run 'pip install matplotlib pandas numpy'")
    LIBS_AVAILABLE = False

# --- 插件API导入 ---
try:
    from modules.plugin_system import BasePlugin
except ImportError:
    # 兼容独立运行
    class BasePlugin:
        def __init__(self, *args, **kwargs): pass
        def setup(self): pass
        def teardown(self): pass
        def execute(self, **kwargs): pass
    print("Running Intonation Visualizer in standalone mode.")


# ==============================================================================
# 辅助UI类与委托 (UI优化)
# ==============================================================================
class CustomColorPopup(QDialog):
    """一个简洁的弹出式调色板，样式与plotter.py保持一致。"""
    colorSelected = pyqtSignal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setStyleSheet("""
            QDialog {
                background-color: white;
                border: 1px solid #CCCCCC;
                border-radius: 4px;
            }
        """)
        colors = ['#d32f2f', '#f57c00', '#4caf50', '#1976d2', '#9c27b0', '#e91e63', '#b71c1c', '#e65100', '#1b5e20', '#0d47a1', '#4a148c', '#880e4f', '#fbc02d', '#8bc34a', '#00bcd4', '#03a9f4', '#ff4081', '#ff9800', '#ffcdd2', '#ffccbc', '#c8e6c9', '#bbdefb', '#e1bee7', '#fff9c4', '#a1887f', '#795548', '#8d6e63', '#00897b', '#455a64', '#546e7a', '#ffffff', '#eeeeee', '#bdbdbd', '#757575', '#424242', '#000000']
        layout = QGridLayout(); layout.setSpacing(4); layout.setContentsMargins(10, 10, 10, 10); self.setLayout(layout)
        for i, color_hex in enumerate(colors):
            row, col = divmod(i, 6)
            widget = QFrame(); widget.setFixedSize(24, 24)
            widget.setStyleSheet(f"QFrame {{ background-color: {color_hex}; border-radius: 4px; border: 1px solid #e0e0e0; }} QFrame:hover {{ border: 2px solid #0078d7; }}")
            widget.setCursor(Qt.PointingHandCursor)
            widget.mousePressEvent = lambda event, c=QColor(color_hex): self.on_color_click(c)
            layout.addWidget(widget, row, col)

    def on_color_click(self, color):
        self.colorSelected.emit(color)
        self.close()

class ColorWidget(QWidget):
    """表格中用于显示和选择颜色的小部件，使用自定义弹窗。"""
    colorChanged = pyqtSignal(QColor)

    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.set_color(color)
        self.setFixedSize(50, 30)
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
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 4, 4)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if not self.popup:
                self.popup = CustomColorPopup(self)
                self.popup.colorSelected.connect(self.on_color_selected)
            self.popup.move(self.mapToGlobal(self.rect().bottomLeft()))
            self.popup.show()
            
    def on_color_selected(self, color):
        if color.isValid():
            self.set_color(color)
            self.colorChanged.emit(color)

class ReadOnlyDelegate(QItemDelegate):
    """使表格某些列只读的委托"""
    def createEditor(self, parent, option, index):
        return None # 返回None表示不可编辑

# ==============================================================================
# 核心UI类：语调可视化与建模器 V2.1 (UI优化)
# ==============================================================================
class VisualizerDialog(QDialog):
    # 定义了颜色主题和线条样式，方便统一管理
    COLOR_SCHEMES = {
        "色觉友好": ['#332288', '#117733', '#44AA99', '#88CCEE', '#DDCC77', '#CC6677', '#AA4499', '#882255'],
        "经典亮色": ['#e41a1c', '#377eb8', '#4daf4a', '#984ea3', '#ff7f00', '#a65628', '#f781bf'],
        "默认": ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f'],
    }

    def __init__(self, parent=None, icon_manager=None):
        super().__init__(parent)
        if not LIBS_AVAILABLE:
            QMessageBox.critical(self, "依赖缺失", "需要 'matplotlib', 'pandas' 和 'numpy' 库。")
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(0, self.reject); return

        self.setWindowTitle("语调可视化与建模 V2.1")
        self.resize(1400, 900)
        self.setMinimumSize(1200, 750)
        self.icon_manager = icon_manager

        # V2.0 核心数据结构：使用列表管理所有数据源
        self.data_sources = []
        self.color_cycler = cycle(self.COLOR_SCHEMES['默认'])

        # 交互状态变量
        self._is_panning = False
        self._pan_start_pos = None
        self.hover_annotation = None

        self._init_ui()
        self._connect_signals()
        self._update_ui_state()
        self.setAcceptDrops(True)
        self._create_drop_overlay()
        
    def _create_drop_overlay(self):
        """创建用于拖拽提示的覆盖层"""
        self.drop_overlay = QLabel("拖拽 CSV / Excel 文件到此处进行分析", self)
        self.drop_overlay.setAlignment(Qt.AlignCenter)
        font = self.font()
        font.setPointSize(20)
        font.setBold(True)
        self.drop_overlay.setFont(font)
        
        # 设置样式
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
        main_layout = QHBoxLayout(self)
        main_splitter = QSplitter(Qt.Horizontal)

        self.left_panel = self._create_left_panel()
        
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(0,0,0,0)
        
        bg_color = self.palette().color(QPalette.Window).name()
        self.figure = Figure(facecolor=bg_color) 
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setContextMenuPolicy(Qt.CustomContextMenu)
        self.canvas.setToolTip("图表区域:\n- 左键拖拽: 平移视图\n- Ctrl+滚轮: 缩放视图\n- 右键: 功能菜单")
        
        self.right_panel = self._create_right_panel()

        center_layout.addWidget(self.canvas)
        
        main_splitter.addWidget(self.left_panel)
        main_splitter.addWidget(center_panel)
        main_splitter.addWidget(self.right_panel)
        main_splitter.setSizes([450, 600, 350]) # 调整初始比例

        main_layout.addWidget(main_splitter)

    def _create_left_panel(self):
        """创建左侧面板，包含数据源管理表格和全局处理选项"""
        panel = QWidget(); panel.setMinimumWidth(450)
        layout = QVBoxLayout(panel)

        # 1. 数据源管理模块
        source_group = QGroupBox("数据源管理")
        source_layout = QVBoxLayout(source_group)
        self.source_table = QTableWidget()
        self.source_table.setColumnCount(5)
        self.source_table.setHorizontalHeaderLabels(["可见", "颜色", "名称", "分组", "删除"])
        self.source_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.source_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.source_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.source_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.source_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.source_table.verticalHeader().setVisible(False)
        self.source_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.source_table.setItemDelegateForColumn(1, ReadOnlyDelegate(self)) # 颜色列不可直接编辑
        
        source_layout.addWidget(self.source_table)
        
        # 2. 全局数据处理模块
        processing_group = QGroupBox("全局数据处理")
        proc_layout = QFormLayout(processing_group)

        # F0归一化
        self.norm_combo = QComboBox(); self.norm_combo.addItems(["原始值 (Hz)", "半音 (Semitone)", "Z-Score"])
        self.norm_combo.setToolTip("选择对F0值进行变换的方式：\n- 原始值: 不做任何处理。\n- 半音: 转换为对数尺度的半音。\n- Z-Score: 对F0进行标准化，消除个体音高差异。")
        self.st_ref_edit = QLineEdit("100"); self.z_scope_combo = QComboBox(); self.z_scope_combo.addItems(["按分组", "按整个数据集"])
        self.st_param_widget = QWidget(); st_layout = QHBoxLayout(self.st_param_widget); st_layout.setContentsMargins(0,0,0,0); st_layout.addWidget(QLabel("基准(Hz):")); st_layout.addWidget(self.st_ref_edit)
        self.z_param_widget = QWidget(); z_layout = QHBoxLayout(self.z_param_widget); z_layout.setContentsMargins(0,0,0,0); z_layout.addWidget(QLabel("范围:")); z_layout.addWidget(self.z_scope_combo)
        self.z_param_widget.setVisible(False)
        
        # 曲线平滑
        self.smoothing_group = QGroupBox("曲线平滑 (移动平均)")
        self.smoothing_group.setCheckable(True); self.smoothing_group.setChecked(True)
        smoothing_layout = QFormLayout(self.smoothing_group)
        self.smoothing_window_slider = QSlider(Qt.Horizontal); self.smoothing_window_slider.setRange(1, 25); self.smoothing_window_slider.setValue(4)
        self.smoothing_window_slider.setToolTip("移动平均的窗口大小，值越大曲线越平滑。")
        self.smoothing_label = QLabel("窗口: 9 点")
        smoothing_layout.addRow(self.smoothing_label, self.smoothing_window_slider)

        proc_layout.addRow("F0归一化:", self.norm_combo)
        proc_layout.addRow(self.st_param_widget); proc_layout.addRow(self.z_param_widget)
        proc_layout.addRow(self.smoothing_group)

        layout.addWidget(source_group)
        layout.addWidget(processing_group)
        layout.addStretch()
        return panel

    def _create_right_panel(self):
        """创建右侧面板，包含显示选项和分组对比功能"""
        panel = QWidget(); panel.setMinimumWidth(350)
        layout = QVBoxLayout(panel)

        # 1. 图表样式
        style_group = QGroupBox("图表样式"); style_layout = QFormLayout(style_group)
        self.title_edit = QLineEdit("语调曲线对比"); self.xlabel_edit = QLineEdit("时间"); self.ylabel_edit = QLineEdit("F0 (Hz)")
        self.show_legend_check = QCheckBox("显示图例"); self.show_legend_check.setChecked(True)
        self.normalize_time_check = QCheckBox("时间归一化 (0-100%)")
        self.normalize_time_check.setToolTip("勾选后，每条曲线的时间轴将被归一化到0-100%，\n便于对比不同时长的句子的语调形态。")
        style_layout.addRow("标题:", self.title_edit); style_layout.addRow("X轴:", self.xlabel_edit); style_layout.addRow("Y轴:", self.ylabel_edit)
        style_layout.addRow(self.normalize_time_check); style_layout.addRow(self.show_legend_check)

        # 2. 显示选项
        display_group = QGroupBox("显示选项"); display_layout = QFormLayout(display_group)
        self.show_points_check = QCheckBox("显示数据点"); self.show_points_check.setToolTip("在曲线上方显示原始的F0数据点。")
        self.point_size_slider = QSlider(Qt.Horizontal); self.point_size_slider.setRange(2, 50); self.point_size_slider.setValue(10)
        self.point_alpha_slider = QSlider(Qt.Horizontal); self.point_alpha_slider.setRange(10, 100); self.point_alpha_slider.setValue(40)
        display_layout.addRow(self.show_points_check); display_layout.addRow("点大小:", self.point_size_slider); display_layout.addRow("点透明度:", self.point_alpha_slider)

        # 3. 分组对比与建模
        grouping_group = QGroupBox("分组对比与建模"); grouping_layout = QFormLayout(grouping_group)
        self.group_filter_combo = QComboBox(); self.group_filter_combo.addItem("显示所有分组")
        self.show_mean_contour_check = QCheckBox("显示分组平均轮廓")
        self.show_mean_contour_check.setToolTip("勾选后，将为每个分组计算并绘制一条平均语调轮廓线。\n(必须勾选时间归一化)")
        grouping_layout.addRow("分组筛选:", self.group_filter_combo); grouping_layout.addRow(self.show_mean_contour_check)

        layout.addWidget(style_group); layout.addWidget(display_group); layout.addWidget(grouping_group); layout.addStretch()
        return panel

    def _connect_signals(self):
        # 左侧面板
        self.source_table.itemChanged.connect(self._on_table_item_changed)
        self.norm_combo.currentTextChanged.connect(self._on_norm_changed)
        self.smoothing_window_slider.valueChanged.connect(self._update_smoothing_label)
        self.smoothing_group.toggled.connect(self._update_plot)
        
        # 右侧面板
        for w in [self.title_edit, self.xlabel_edit, self.ylabel_edit]:
            w.editingFinished.connect(self._update_plot)
        self.normalize_time_check.stateChanged.connect(self._on_time_norm_changed)
        for w in [self.show_legend_check, self.show_points_check, self.show_mean_contour_check]:
            w.stateChanged.connect(self._update_plot)
        for w in [self.point_size_slider, self.point_alpha_slider]:
            w.valueChanged.connect(self._update_plot)
        self.group_filter_combo.currentTextChanged.connect(self._update_plot)

        # 画布交互
        self.canvas.customContextMenuRequested.connect(self._show_context_menu)
        self.canvas.mpl_connect('button_press_event', self._on_mouse_press)
        self.canvas.mpl_connect('button_release_event', self._on_mouse_release)
        self.canvas.mpl_connect('motion_notify_event', self._on_mouse_move)

    # ==============================================================================
    # V2.0 核心功能实现 (数据管理、UI更新、绘图逻辑)
    # ==============================================================================

    def add_data_source(self, df, source_name=""):
        """V2.0 新的数据加载逻辑：添加而非替换"""
        if not isinstance(df, pd.DataFrame) or df.empty:
            QMessageBox.warning(self, "数据无效", "传入的数据不是有效的Pandas DataFrame。")
            return
        
        # 查找时间列和F0列
        time_col = next((c for c in df.columns if 'time' in c.lower()), df.columns[0])
        f0_col = next((c for c in df.columns if 'f0' in c.lower()), df.columns[1] if len(df.columns) > 1 else df.columns[0])

        # 创建新的数据源条目
        source_item = {
            'id': str(uuid.uuid4()),
            'name': source_name or f"数据源 {len(self.data_sources) + 1}",
            'group': '默认分组',
            'color': QColor(next(self.color_cycler)),
            'visible': True,
            'dataframe': df,
            'time_col': time_col,
            'f0_col': f0_col
        }
        self.data_sources.append(source_item)
        
        # 更新UI
        self._add_row_to_table(source_item)
        self._update_group_filter_combo()
        self._update_plot()
        self._update_ui_state()

    def _add_row_to_table(self, source_item):
        """向表格中添加一行来代表一个新的数据源"""
        table = self.source_table
        row = table.rowCount()
        table.insertRow(row)

        # 0 - 可见性复选框
        vis_check = QCheckBox(); vis_check.setChecked(source_item['visible'])
        vis_check.stateChanged.connect(lambda state, r=row: self._on_visibility_changed(state, r))
        cell_widget = QWidget(); cell_layout = QHBoxLayout(cell_widget); cell_layout.addWidget(vis_check); cell_layout.setAlignment(Qt.AlignCenter); cell_layout.setContentsMargins(0,0,0,0)
        table.setCellWidget(row, 0, cell_widget)

        # 1 - 颜色按钮
        color_widget = ColorWidget(source_item['color'])
        color_widget.colorChanged.connect(lambda color, r=row: self._on_color_changed(color, r))
        # --- 新增开始 ---
        color_cell_widget = QWidget()
        color_cell_layout = QHBoxLayout(color_cell_widget)
        color_cell_layout.addWidget(color_widget)
        color_cell_layout.setAlignment(Qt.AlignCenter)
        color_cell_layout.setContentsMargins(0,0,0,0)
        table.setCellWidget(row, 1, color_cell_widget)
        # --- 新增结束 ---
        
        # 2 - 名称
        name_item = QTableWidgetItem(source_item['name']); table.setItem(row, 2, name_item)
        
        # 3 - 分组
        group_item = QTableWidgetItem(source_item['group']); table.setItem(row, 3, group_item)
        
        # 4 - 删除按钮 (UI优化)
        del_btn = QPushButton("") # 修改: 添加文本
        if self.icon_manager:
            del_btn.setIcon(self.icon_manager.get_icon("delete"))
        # del_btn.setFixedSize(24, 24) # 修改: 注释或删除此行
        del_btn.setToolTip("删除此数据源")
        del_btn.setStyleSheet("QPushButton { border: none; background-color: transparent; padding: 0px; } QPushButton:hover { border-radius: 4px; }") # 修改: 增加内边距
        del_btn.clicked.connect(lambda _, r=row: self._remove_source_by_row(r))
        # --- 新增居中容器 ---
        del_cell_widget = QWidget()
        del_cell_layout = QHBoxLayout(del_cell_widget)
        del_cell_layout.addWidget(del_btn)
        del_cell_layout.setAlignment(Qt.AlignCenter)
        del_cell_layout.setContentsMargins(0,0,0,0)
        table.setCellWidget(row, 4, del_cell_widget)

    def _update_plot(self):
        """V2.0 核心绘图逻辑"""
        if not hasattr(self, 'figure'): return # 防止初始化未完成时调用
        
        try:
            # 准备绘图
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            
            # 获取当前筛选的分组
            current_filter = self.group_filter_combo.currentText()
            
            # 存储每个分组的数据，用于后续计算平均值
            grouped_data_for_mean = {}

            # 遍历所有数据源进行绘制
            for source in self.data_sources:
                if not source['visible']: continue
                
                # 应用分组筛选
                if current_filter != "显示所有分组" and source['group'] != current_filter:
                    continue

                df = source['dataframe'].copy()
                time_col, f0_col = source['time_col'], source['f0_col']
                
                # 数据预处理
                processed_df = self._process_single_dataframe(df, time_col, f0_col, source['group'])
                if processed_df is None or processed_df.empty: continue

                t_data, f0_data = processed_df[time_col], processed_df[f0_col]
                
                # 绘制曲线
                ax.plot(t_data, f0_data, label=source['name'], color=source['color'].name(), zorder=10)
                
                # 绘制数据点
                if self.show_points_check.isChecked():
                    alpha = self.point_alpha_slider.value() / 100.0
                    size = self.point_size_slider.value()
                    ax.scatter(t_data, f0_data, color=source['color'].name(), s=size, alpha=alpha, zorder=5)
                
                # 为计算平均轮廓存储处理后的数据
                if source['group'] not in grouped_data_for_mean:
                    grouped_data_for_mean[source['group']] = {'curves': [], 'color': source['color']}
                grouped_data_for_mean[source['group']]['curves'].append(processed_df)

            # 绘制平均轮廓线
            if self.show_mean_contour_check.isChecked() and self.normalize_time_check.isChecked():
                self._plot_mean_contours(ax, grouped_data_for_mean)

            # 设置图表样式
            ax.set_title(self.title_edit.text(), fontsize=14)
            ax.set_xlabel(self.xlabel_edit.text())
            ax.set_ylabel(self.ylabel_edit.text())
            ax.grid(True, linestyle='--', alpha=0.6)
            if self.show_legend_check.isChecked():
                ax.legend()
            
            # 刷新画布
            self.figure.tight_layout()
            self.canvas.draw()
        except Exception as e:
            print(f"Plotting Error: {e}")
            import traceback
            traceback.print_exc()

    def _process_single_dataframe(self, df, time_col, f0_col, group_name):
        """对单个DataFrame进行归一化、平滑等处理"""
        df = df[[time_col, f0_col]].copy().dropna()
        if df.empty: return None

        # F0 归一化
        norm_method = self.norm_combo.currentText()
        if norm_method == "半音 (Semitone)":
            st_ref = float(self.st_ref_edit.text() or 100)
            df[f0_col] = 12 * np.log2(df[f0_col] / st_ref)
        elif norm_method == "Z-Score":
            if self.z_scope_combo.currentText() == "按分组":
                # 需要访问整个分组的数据，这里简化为对当前曲线进行Z-Score
                df[f0_col] = (df[f0_col] - df[f0_col].mean()) / df[f0_col].std()
            else: # 按整个数据集 - 这是一个复杂操作，简化为对所有可见数据
                all_f0 = pd.concat([s['dataframe'][s['f0_col']] for s in self.data_sources if s['visible']]).dropna()
                df[f0_col] = (df[f0_col] - all_f0.mean()) / all_f0.std()

        # 时间归一化
        if self.normalize_time_check.isChecked():
            t_min, t_max = df[time_col].min(), df[time_col].max()
            if t_max > t_min:
                df[time_col] = 100 * (df[time_col] - t_min) / (t_max - t_min)
            else:
                df[time_col] = 0
        
        # 平滑
        if self.smoothing_group.isChecked():
            win = 2 * self.smoothing_window_slider.value() + 1
            df[f0_col] = df[f0_col].rolling(window=win, center=True, min_periods=1).mean()
        
        return df.dropna()

    def _plot_mean_contours(self, ax, grouped_data):
        """计算并绘制分组的平均轮廓线"""
        # 定义一个统一的、归一化的时间轴 (0-100, 101个点)
        mean_time_axis = np.linspace(0, 100, 101)

        for group_name, data in grouped_data.items():
            resampled_curves = []
            for df_processed in data['curves']:
                # 使用np.interp进行插值，将每条曲线重采样到统一时间轴上
                resampled_f0 = np.interp(mean_time_axis, df_processed[df_processed.columns[0]], df_processed[df_processed.columns[1]])
                resampled_curves.append(resampled_f0)
            
            if len(resampled_curves) > 0:
                # 计算平均F0
                mean_f0_curve = np.mean(np.array(resampled_curves), axis=0)
                
                # 绘制平均曲线
                ax.plot(mean_time_axis, mean_f0_curve,
                        label=f"{group_name} (平均)",
                        color=data['color'].name(),
                        linestyle='--',
                        linewidth=3,
                        zorder=20)

    # ==============================================================================
    # UI槽函数与事件处理
    # ==============================================================================
    
    def _on_table_item_changed(self, item):
        """处理表格中名称或分组的编辑"""
        row = item.row()
        col = item.column()
        if row >= len(self.data_sources): return
        
        source = self.data_sources[row]
        if col == 2: # 名称列
            source['name'] = item.text()
        elif col == 3: # 分组列
            source['group'] = item.text()
            self._update_group_filter_combo()
        
        self._update_plot()

    def _on_visibility_changed(self, state, row):
        if row < len(self.data_sources):
            self.data_sources[row]['visible'] = (state == Qt.Checked)
            self._update_plot()

    def _on_color_changed(self, color, row):
        if row < len(self.data_sources):
            self.data_sources[row]['color'] = color
            self._update_plot()

    def _remove_source_by_row(self, row):
        if row < len(self.data_sources):
            reply = QMessageBox.question(self, "确认删除", f"确定要删除数据源 '{self.data_sources[row]['name']}' 吗？",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                del self.data_sources[row]
                self.source_table.removeRow(row)
                self._update_group_filter_combo()
                self._update_plot()
                self._update_ui_state()

    def _update_group_filter_combo(self):
        """更新分组筛选下拉框的选项"""
        current_selection = self.group_filter_combo.currentText()
        self.group_filter_combo.blockSignals(True)
        self.group_filter_combo.clear()
        self.group_filter_combo.addItem("显示所有分组")
        
        all_groups = sorted(list(set(s['group'] for s in self.data_sources)))
        self.group_filter_combo.addItems(all_groups)
        
        if current_selection in all_groups:
            self.group_filter_combo.setCurrentText(current_selection)
        
        self.group_filter_combo.blockSignals(False)

    def _update_ui_state(self):
        has_data = bool(self.data_sources)
        self.right_panel.setEnabled(has_data)
        self.show_mean_contour_check.setEnabled(has_data and self.normalize_time_check.isChecked())
        if not self.normalize_time_check.isChecked():
            self.show_mean_contour_check.setChecked(False)

    def _on_time_norm_changed(self, state):
        """处理时间归一化复选框的状态变化，并更新UI。"""
        self._update_ui_state()
        self._update_plot()

    def _on_norm_changed(self, norm_method):
        is_st = (norm_method == "半音 (Semitone)"); is_z = (norm_method == "Z-Score")
        self.st_param_widget.setVisible(is_st); self.z_param_widget.setVisible(is_z)
        self._update_plot()

    def _update_smoothing_label(self, value):
        window_size = 2 * value + 1; self.smoothing_label.setText(f"窗口: {window_size} 点")
        self._update_plot()

    def _show_context_menu(self, pos):
        """显示带有图标的右键菜单 (UI优化)"""
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
            "清空所有数据...": "clear_contents"
        }
        
        # 创建一个函数映射
        func_map = {
            "从文件加载数据...": self._load_data_from_file,
            "刷新图表": self._update_plot,
            "重置视图": self._reset_view,
            "复制图片": self._copy_plot_to_clipboard,
            "保存图片...": self._save_plot_image,
            "清空所有数据...": self._clear_all_data
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

    def _load_data_from_file(self):
        """通过文件对话框加载数据"""
        path, _ = QFileDialog.getOpenFileName(self, "选择F0数据文件", "", "表格文件 (*.xlsx *.xls *.csv)")
        if path:
            self._load_and_add_file(path)

    def _load_and_add_file(self, file_path):
        """核心的文件加载和添加逻辑，可被多处调用"""
        try:
            if file_path.lower().endswith(('.xlsx', '.xls')):
                df = pd.read_excel(file_path)
            elif file_path.lower().endswith('.csv'):
                df = pd.read_csv(file_path)
            else:
                # 如果文件类型不支持，可以选择忽略或提示
                return

            self.add_data_source(df, os.path.basename(file_path))
        except Exception as e:
            QMessageBox.critical(self, "加载失败", f"无法读取文件 '{os.path.basename(file_path)}':\n{e}")


    def _clear_all_data(self):
        if QMessageBox.question(self, "确认", "确定要清空所有数据和配置吗？", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.data_sources.clear()
            self.source_table.setRowCount(0)
            self._update_group_filter_combo()
            self._update_plot()
            self._update_ui_state()

    # --- 画布交互方法 (与V1.0类似) ---
    def _reset_view(self): self._update_plot()
    def _copy_plot_to_clipboard(self): QApplication.clipboard().setPixmap(self.canvas.grab())
    def _save_plot_image(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存图片", f"{self.title_edit.text()}.png", "PNG (*.png);;PDF (*.pdf);;SVG (*.svg)")
        if path: self.figure.savefig(path, dpi=300, facecolor='white', bbox_inches='tight')
    def wheelEvent(self, event):
        if self.canvas.underMouse() and event.modifiers() == Qt.ControlModifier:
            ax = self.figure.gca(); cur_xlim, cur_ylim = ax.get_xlim(), ax.get_ylim()
            xdata, ydata = event.x(), self.canvas.height() - event.y()
            mx, my = ax.transData.inverted().transform_point((xdata, ydata))
            factor = 1.1 if event.angleDelta().y() > 0 else 1 / 1.1
            new_xlim = [mx - (mx - x) / factor for x in cur_xlim]
            new_ylim = [my - (my - y) / factor for y in cur_ylim]
            ax.set_xlim(new_xlim); ax.set_ylim(new_ylim); self.canvas.draw()
        else: super().wheelEvent(event)
    def _on_mouse_press(self, event):
        if event.inaxes and event.button == 1: self._is_panning = True; self._pan_start_pos = (event.xdata, event.ydata); self.canvas.setCursor(Qt.ClosedHandCursor)
    def _on_mouse_release(self, event):
        if self._is_panning: self._is_panning = False; self.canvas.setCursor(Qt.ArrowCursor)
    def _on_mouse_move(self, event):
        if not event.inaxes: return
        if self._is_panning:
            ax = event.inaxes; dx, dy = event.xdata - self._pan_start_pos[0], event.ydata - self._pan_start_pos[1]
            xlim, ylim = ax.get_xlim(), ax.get_ylim()
            ax.set_xlim(xlim[0] - dx, xlim[1] - dx); ax.set_ylim(ylim[0] - dy, ylim[1] - dy); self.canvas.draw_idle()

# ==============================================================================
# V2.2 新增: 拖拽事件处理
# ==============================================================================
    SUPPORTED_EXTENSIONS = ('.csv', '.xlsx', '.xls')

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
        # 尝试挂接到主程序的音频分析模块
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
        if (dataframe_to_load := kwargs.get('dataframe')) is not None:
            source_name = kwargs.get('source_name', '')
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
    df1 = pd.DataFrame({'time_sec': time1, 'f0_hz': f0_1})

    time2 = np.linspace(0, 1.2, 120)
    f0_2 = 220 - 50 * np.cos(2 * np.pi * 1.5 * time2) + np.random.randn(120) * 8
    df2 = pd.DataFrame({'time': time2, 'F0': f0_2})

    # 通过execute方法加载数据
    plugin.execute(dataframe=df1, source_name="陈述句-男声")
    plugin.execute(dataframe=df2, source_name="疑问句-女声")

    sys.exit(app.exec_())

# --- END OF COMPLETE AND UPGRADED FILE ---
