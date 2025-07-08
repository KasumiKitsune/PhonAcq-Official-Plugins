# -*- coding: utf-8 -*-

import os
import sys
import json
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
                             QPlainTextEdit, QScrollArea, QWidget, QFrame,
                             QApplication, QMessageBox, QComboBox, QGroupBox, QCheckBox, QSizePolicy, QLayout)
from PyQt5.QtCore import Qt, QSize, QTimer, QRect, QPoint
from PyQt5.QtGui import QFont

# 导入插件API基类
try:
    from plugin_system import BasePlugin
except ImportError:
    # 如果直接运行此文件，为方便测试，添加模块路径
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

# ==============================================================================
# 自定义流式布局 (Custom Flow Layout)
# ==============================================================================
class FlowLayout(QLayout):
    """一个自定义布局，允许子控件像文本一样自动换行。"""
    def __init__(self, parent=None, margin=-1, h_spacing=-1, v_spacing=-1):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(h_spacing if h_spacing >= 0 else self.spacing())

        self.itemList = []
        self.m_h_spacing = h_spacing
        self.m_v_spacing = v_spacing

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self.itemList.append(item)

    def count(self):
        return len(self.itemList)

    def itemAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.itemList:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only):
        x = rect.x()
        y = rect.y()
        line_height = 0

        for item in self.itemList:
            space_x = self.spacing()
            space_y = self.spacing()
            if self.m_h_spacing >= 0:
                space_x = self.m_h_spacing
            if self.m_v_spacing >= 0:
                space_y = self.m_v_spacing

            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y()

# ==============================================================================
# 插件主类
# ==============================================================================
class IpaKeyboardPlugin(BasePlugin):
    """IPA符号键盘插件。"""
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.dialog_instance = None

    def setup(self):
        print("[IPA Keyboard] 插件已准备就绪。")
        return True

    def teardown(self):
        if self.dialog_instance:
            self.dialog_instance.close()
        print("[IPA Keyboard] 插件已卸载。")

    def execute(self, **kwargs):
        if self.dialog_instance is None:
            self.dialog_instance = IpaKeyboardDialog(self.main_window)
            self.dialog_instance.finished.connect(self.on_dialog_finished)
        
        self.dialog_instance.show()
        self.dialog_instance.raise_()
        self.dialog_instance.activateWindow()

    def on_dialog_finished(self):
        self.dialog_instance = None

# ==============================================================================
# UI 对话框类
# ==============================================================================
class IpaKeyboardDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data_path = os.path.join(os.path.dirname(__file__), 'data')
        self.schemes = []
        self.current_scheme_data = {}
        self.filter_checkboxes = {}

        self.setWindowTitle("IPA 符号键盘")
        self.resize(1000, 700) 
        self.setMinimumSize(600, 500)

        self._load_schemes()
        self._init_ui()
        self.on_scheme_changed()

    def _load_schemes(self):
        """加载方案列表。"""
        try:
            schemes_file = os.path.join(self.data_path, 'schemes.json')
            with open(schemes_file, 'r', encoding='utf-8') as f:
                self.schemes = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "方案加载失败", f"无法加载方案索引文件 'schemes.json':\n{e}")
            self.schemes = []

    def _init_ui(self):
        """初始化用户界面，采用双栏布局。"""
        main_layout = QHBoxLayout(self)
        
        # --- 左侧栏 (搜索与筛选) ---
        left_scroll_area = QScrollArea()
        left_scroll_area.setWidgetResizable(True)
        left_scroll_area.setFrameShape(QFrame.NoFrame)
        
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(5, 5, 5, 5)
        left_layout.setSpacing(10)

        scheme_box = QGroupBox("方案")
        scheme_layout = QHBoxLayout(scheme_box)
        self.scheme_combo = QComboBox()
        for scheme in self.schemes:
            self.scheme_combo.addItem(scheme['name'], scheme)
        scheme_layout.addWidget(self.scheme_combo)

        search_box = QGroupBox("搜索")
        search_layout = QHBoxLayout(search_box)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("按名称/标签搜索 (空格分隔)...")
        self.search_input.setClearButtonEnabled(True)
        search_layout.addWidget(self.search_input)

        self.filter_group_box = QGroupBox("筛选")
        self.filter_main_layout = QVBoxLayout(self.filter_group_box)
        
        left_layout.addWidget(scheme_box)
        left_layout.addWidget(search_box)
        left_layout.addWidget(self.filter_group_box, 1)
        left_scroll_area.setWidget(left_panel)

        # --- 右侧栏 (符号键盘和暂存区) ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(5, 5, 5, 5)
        right_layout.setSpacing(10)

        symbol_box = QGroupBox("符号键盘")
        symbol_box_layout = QVBoxLayout(symbol_box)
        self.symbol_scroll_area = QScrollArea()
        self.symbol_scroll_area.setWidgetResizable(True)
        self.symbol_scroll_area.setFrameShape(QFrame.NoFrame)
        
        self.button_container = QWidget()
        self.symbol_flow_layout = FlowLayout(self.button_container, margin=5, h_spacing=5, v_spacing=5)
        self.symbol_scroll_area.setWidget(self.button_container)
        symbol_box_layout.addWidget(self.symbol_scroll_area)
        
        output_group = QGroupBox("文本暂存区")
        output_layout = QHBoxLayout(output_group)
        self.output_text = QPlainTextEdit()
        self.output_text.setPlaceholderText("点击上方符号可在此处编辑...")
        self.output_text.setFont(QFont("Doulos SIL", 12))
        self.copy_button = QPushButton("复制")
        self.clear_button = QPushButton("清空")
        output_btn_layout = QVBoxLayout()
        output_btn_layout.addWidget(self.copy_button)
        output_btn_layout.addWidget(self.clear_button)
        output_btn_layout.addStretch()
        output_layout.addWidget(self.output_text, 1)
        output_layout.addLayout(output_btn_layout)
        
        right_layout.addWidget(symbol_box, 1)
        right_layout.addWidget(output_group)

        # --- 组合主布局 ---
        main_layout.addWidget(left_scroll_area, 1)
        main_layout.addWidget(right_panel, 2)

        # --- 连接信号与槽 ---
        self.search_input.textChanged.connect(self.populate_symbols)
        self.scheme_combo.currentIndexChanged.connect(self.on_scheme_changed)
        self.copy_button.clicked.connect(self.copy_to_clipboard)
        self.clear_button.clicked.connect(self.output_text.clear)

    def on_scheme_changed(self):
        """当方案切换时，重新加载数据并更新UI。"""
        scheme_info = self.scheme_combo.currentData()
        if not scheme_info:
            self.current_scheme_data = {}
            return

        try:
            scheme_file = os.path.join(self.data_path, scheme_info['file'])
            with open(scheme_file, 'r', encoding='utf-8') as f:
                self.current_scheme_data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "方案加载失败", f"无法加载方案文件 '{scheme_info['file']}':\n{e}")
            self.current_scheme_data = {}

        self.generate_filter_buttons()
        self.populate_symbols()

    def generate_filter_buttons(self):
        """为当前方案动态生成筛选器按钮，每个类别另起一行。"""
        while self.filter_main_layout.count():
            child = self.filter_main_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        self.filter_checkboxes.clear()
        feature_groups = self.current_scheme_data.get("features", {})
        
        if not feature_groups:
            self.filter_group_box.setVisible(False)
            return
        
        self.filter_group_box.setVisible(True)

        for group_name, features in feature_groups.items():
            group_box = QGroupBox(group_name)
            flow_layout = FlowLayout(group_box, margin=5, h_spacing=10, v_spacing=5)
            
            for feature in features:
                checkbox = QCheckBox(feature)
                checkbox.stateChanged.connect(self.populate_symbols)
                flow_layout.addWidget(checkbox)
                self.filter_checkboxes[feature] = checkbox

            self.filter_main_layout.addWidget(group_box)
        
        self.filter_main_layout.addStretch()

    def populate_symbols(self):
        """根据搜索和筛选条件，填充符号按钮，并根据tag权重排序。"""
        # 1. 清空旧的按钮
        while self.symbol_flow_layout.count():
            item = self.symbol_flow_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        # 2. 获取筛选和搜索条件
        search_text = self.search_input.text().lower().strip()
        active_filters = {feature for feature, cb in self.filter_checkboxes.items() if cb.isChecked()}
        
        # 3. 初步筛选符号
        symbols_to_process = self.current_scheme_data.get('symbols', [])
        
        if active_filters:
            symbols_to_process = [
                s for s in symbols_to_process 
                if active_filters.issubset(set(s.get('features', [])))
            ]
        
        # 如果没有搜索词，直接显示筛选后的结果
        if not search_text:
            for symbol_info in symbols_to_process:
                self._create_symbol_button(symbol_info)
            return

        # 4. 对符号进行评分和排序
        scored_symbols = []
        for symbol_info in symbols_to_process:
            score = self._calculate_search_score(symbol_info, search_text)
            if score > 0:
                scored_symbols.append((score, symbol_info))
        
        scored_symbols.sort(key=lambda item: item[0], reverse=True)
        
        # 5. 创建并添加按钮
        for _, symbol_info in scored_symbols:
            self._create_symbol_button(symbol_info)

    def _calculate_search_score(self, symbol_info, search_text):
        """计算单个符号相对于搜索词的匹配分数。"""
        search_keywords = search_text.split()
        
        tags_string = symbol_info.get('tags', '')
        name_string = symbol_info.get('name', '').lower()
        symbol_string = symbol_info.get('symbol', '')
        
        tags_list = [t.strip() for t in tags_string.split(',')]
        
        total_score = 0
        for keyword in search_keywords:
            keyword_score = 0
            
            # 检查标签 (最高权重)
            try:
                idx = tags_list.index(keyword)
                keyword_score = max(keyword_score, 1000 / (idx + 1))
            except ValueError:
                for i, tag in enumerate(tags_list):
                    if tag.startswith(keyword):
                        keyword_score = max(keyword_score, 500 / (i + 1))
                        break

            # 检查名称 (中等权重)
            if keyword in name_string:
                keyword_score = max(keyword_score, 10)

            # 检查符号本身 (最低权重)
            if keyword in symbol_string:
                keyword_score = max(keyword_score, 1)

            if keyword_score == 0:
                return 0 # 一个关键字不匹配，则总分为0
            
            total_score += keyword_score

        return total_score

    def _create_symbol_button(self, symbol_info):
        """辅助函数，用于创建单个符号按钮。"""
        btn = QPushButton(symbol_info['symbol'])
        btn.setFixedSize(80, 45)
        btn.setFont(QFont("Doulos SIL", 16))
        tooltip_text = f"<b>{symbol_info['name']}</b><br>特征: {', '.join(symbol_info.get('features', []))}"
        btn.setToolTip(tooltip_text)
        btn.clicked.connect(lambda ch, s=symbol_info['symbol']: self.on_symbol_clicked(s))
        self.symbol_flow_layout.addWidget(btn)

    def on_symbol_clicked(self, symbol):
        """处理符号按钮点击事件。"""
        self.output_text.insertPlainText(symbol)
        self.output_text.setFocus()

    def copy_to_clipboard(self):
        """复制暂存区内容到剪贴板。"""
        clipboard = QApplication.clipboard()
        clipboard.setText(self.output_text.toPlainText())
        self.copy_button.setText("已复制!")
        self.copy_button.setStyleSheet("background-color: #4CAF50; color: white;")
        QTimer.singleShot(1500, self.reset_copy_button_style)

    def reset_copy_button_style(self):
        """恢复复制按钮的原始样式。"""
        self.copy_button.setText("复制")
        self.copy_button.setStyleSheet("")

