# --- START OF FILE plugins/icon_customizer/customizer.py (v3.3 - Global Scan & Polish) ---

import os
import sys
import json
import re
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QColorDialog,
                             QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox,
                             QMessageBox, QGroupBox, QSplitter, QComboBox, QMenu, QAction,
                             QLineEdit, QGridLayout, QDialogButtonBox, QWidget, QAbstractItemView)
from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QRegExpValidator

try:
    # 尝试从主程序标准路径导入
    from plugin_system import BasePlugin
    from modules.icon_manager import IconManager
except ImportError:
    # 如果失败，假定在开发环境中，从相对路径导入
    # 这种回退机制有助于插件的独立测试
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from plugin_system import BasePlugin
    from modules.icon_manager import IconManager

# ==============================================================================
# 1. 辅助函数
# ==============================================================================
def recolor_icon(icon, color_hex):
    """
    获取一个 QIcon 和一个十六进制颜色字符串，返回一个新的、已重新着色的 QIcon。
    该函数通过在图标的非透明区域上绘制指定颜色来实现。

    Args:
        icon (QIcon): 需要重新着色的原始 QIcon 对象。
        color_hex (str): 目标颜色的十六进制字符串 (例如, "#FF0000")。

    Returns:
        QIcon: 重新着色后的 QIcon。如果原始图标为空或发生错误，则返回原始图标。
    """
    if icon.isNull():
        return icon
    try:
        # 选择最合适的尺寸进行绘制
        pixmap = icon.pixmap(icon.actualSize(icon.availableSizes()[0]))
        mask = pixmap.createMaskFromColor(Qt.transparent)

        p = QPainter(pixmap)
        p.setCompositionMode(QPainter.CompositionMode_SourceIn)
        p.setBrush(QColor(color_hex))
        p.setPen(QColor(color_hex))
        p.drawRect(pixmap.rect())
        p.end()

        pixmap.setMask(mask)
        return QIcon(pixmap)
    except Exception:
        # 在极少数情况下（如无可用尺寸），返回原始图标
        return icon

# ==============================================================================
# 2. 插件专属配置管理器 (v3.3 - with Global Scan)
# ==============================================================================
class IconConfigManager:
    """管理图标定制器所有配置的加载、保存和访问。"""
    DEFAULT_PROFILE = {
        "global_override": False,
        "global_color": "#FFFFFF",
        "hide_all": False,
        "icon_settings": {}
    }

    def __init__(self, main_window, plugin_manager):
        """
        初始化配置管理器。
        
        Args:
            main_window: 主窗口实例，用于访问图标管理器等。
            plugin_manager: 插件管理器实例，用于发现其他插件。
        """
        self.main_window = main_window
        self.plugin_manager = plugin_manager
        plugin_dir = os.path.dirname(__file__)
        self.config_path = os.path.join(plugin_dir, 'config.json')
        self.settings = self._load()
        self.discovered_icons = set()

    def _load(self):
        """从 config.json 加载配置。如果文件不存在或无效，则返回默认结构。"""
        default_settings = {"profiles": {"__default__": self.DEFAULT_PROFILE.copy()}}
        if not os.path.exists(self.config_path):
            return default_settings
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            # 基本的验证，确保 "profiles" 键存在
            if "profiles" not in loaded:
                return default_settings
            return loaded
        except (json.JSONDecodeError, IOError):
            return default_settings

    def save(self):
        """将当前配置保存到 config.json 文件。"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=4, ensure_ascii=False)
        except IOError as e:
            print(f"[Icon Customizer] 无法保存配置文件: {e}")

    # [核心新增] 全局图标扫描方法
    def discover_all_icons(self):
        """扫描所有已知位置（主程序、主题、插件）以发现所有可用的图标。"""
        newly_discovered = set()
        
        # 1. 扫描主程序默认图标目录
        if hasattr(self.main_window, 'icon_manager'):
            default_dir = self.main_window.icon_manager.default_icon_dir
            if default_dir and os.path.isdir(default_dir):
                for filename in os.listdir(default_dir):
                    name, ext = os.path.splitext(filename)
                    if ext.lower() in ['.png', '.svg', '.ico']:
                        newly_discovered.add(name)

            # 2. 扫描当前主题的图标目录
            theme_dir = self.main_window.icon_manager.theme_icon_dir
            if theme_dir and os.path.isdir(theme_dir):
                for filename in os.listdir(theme_dir):
                    name, ext = os.path.splitext(filename)
                    if ext.lower() in ['.png', '.svg', '.ico']:
                        newly_discovered.add(name)

        # 3. 扫描所有已发现插件的目录
        if self.plugin_manager and hasattr(self.plugin_manager, 'available_plugins'):
            for plugin_id, meta in self.plugin_manager.available_plugins.items():
                plugin_path = meta.get('path')
                if not plugin_path or not os.path.isdir(plugin_path):
                    continue
                
                # 扫描插件根目录下的 "icons" 子目录
                icons_sub_dir = os.path.join(plugin_path, 'icons')
                if os.path.isdir(icons_sub_dir):
                    for filename in os.listdir(icons_sub_dir):
                        name, ext = os.path.splitext(filename)
                        if ext.lower() in ['.png', '.svg', '.ico']:
                            newly_discovered.add(name)
                
                # 扫描 plugin.json 中直接指定的图标
                icon_file = meta.get('icon')
                if icon_file:
                    name, ext = os.path.splitext(icon_file)
                    newly_discovered.add(name)
        
        # 将新发现的图标合并到集合中
        self.discovered_icons.update(newly_discovered)
        print(f"[Icon Customizer] 发现了 {len(self.discovered_icons)} 个唯一图标。")

    def get_current_profile(self, current_theme_name):
        """获取当前主题的配置方案，如果不存在则回退到默认方案。"""
        profiles = self.settings.get("profiles", {})
        return profiles.get(current_theme_name, profiles.get("__default__", self.DEFAULT_PROFILE))

    def get_or_create_profile(self, theme_name):
        """获取指定名称的配置方案，如果不存在则基于默认方案创建。"""
        profiles = self.settings.setdefault("profiles", {})
        if theme_name not in profiles:
            default_profile = profiles.get("__default__", self.DEFAULT_PROFILE)
            profiles[theme_name] = default_profile.copy()
        return profiles[theme_name]

# ==============================================================================
# 3. [新增] 高级颜色选择对话框 (v3.3 - Circular Buttons)
# ==============================================================================
class AdvancedColorDialog(QDialog):
    """一个提供预设调色板、Hex输入和标准颜色选择器的增强型颜色对话框。"""
    
    PRESET_COLORS = [
        # --- 1. Grays & Earth Tones ---
        '#2f3542', '#57606f', '#a4b0be', '#ced6e0', '#8D6E63', '#B9770E', '#A1887F', '#6D4C41',
        # --- 2. Reds & Oranges ---
        '#e53935', '#d63031', '#ff4757', '#F06292', '#ff7f50', '#f79f1f', '#FF8A65', '#FFA000',
        # --- 3. Yellows & Greens ---
        '#fdd835', '#ffc107', '#a3cb38', '#7cb342', '#43a047', '#20c997', '#00897b', '#006266',
        # --- 4. Cyans & Blues ---
        '#12cbc4', '#00bcd4', '#26c6da', '#03a9f4', '#29b6f6', '#1976d2', '#3b6894', '#192a56',
        # --- 5. Indigos & Purples ---
        '#3f51b5', '#5c6bc0', '#5e35b1', '#673ab7', '#8e44ad', '#9b59b6', '#af7ac5', '#6f1e51',
        # --- 6. Pinks & Accent Tones ---
        '#d81b60', '#e84393', '#fd79a8', '#ec407a', '#ab47bc', '#7e57c2', '#607d8b', '#455a64',
    ]

    def __init__(self, parent=None, current_color=None):
        super().__init__(parent)
        self.setWindowTitle("选择颜色")
        self.selected_color = QColor(current_color) if current_color else QColor("#FFFFFF")
        
        main_layout = QVBoxLayout(self)
        
        # 预设调色板
        palette_group = QGroupBox("预设调色板")
        palette_layout = QGridLayout(palette_group)
        for i, color_hex in enumerate(self.PRESET_COLORS):
            btn = QPushButton()
            btn.setFixedSize(50, 40)
            # [核心修改 v3.3] 应用圆形样式
            btn.setStyleSheet(f"background-color: {color_hex}; border-radius: 25px; border: 1px solid #A4B0BE;")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, c=color_hex: self.on_palette_clicked(c))
            palette_layout.addWidget(btn, i // 8, i % 8)
        
        # 自定义输入
        custom_group = QGroupBox("自定义颜色")
        custom_layout = QHBoxLayout(custom_group)
        self.hex_input = QLineEdit(self.selected_color.name())
        self.hex_input.setValidator(QRegExpValidator(QRegExp("#[0-9A-Fa-f]{6}")))
        self.preview_label = QLabel()
        self.preview_label.setFixedSize(50, 40)
        self.update_preview()
        self.standard_picker_btn = QPushButton("更多颜色...")
        custom_layout.addWidget(QLabel("Hex:"))
        custom_layout.addWidget(self.hex_input)
        custom_layout.addWidget(self.preview_label)
        custom_layout.addWidget(self.standard_picker_btn)
        
        # 对话框按钮
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)

        main_layout.addWidget(palette_group)
        main_layout.addWidget(custom_group)
        main_layout.addWidget(self.button_box)

        # 连接信号
        self.hex_input.textChanged.connect(self.on_hex_input_changed)
        self.standard_picker_btn.clicked.connect(self.on_standard_picker)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

    def on_palette_clicked(self, color_hex):
        """当预设颜色被点击时，更新Hex输入框。"""
        self.hex_input.setText(color_hex)

    def on_hex_input_changed(self, text):
        """当Hex输入框内容改变时，验证颜色并更新预览。"""
        if QColor.isValidColor(text):
            self.selected_color.setNamedColor(text)
            self.update_preview()

    def on_standard_picker(self):
        """打开标准的QColorDialog。"""
        color = QColorDialog.getColor(self.selected_color, self, "选择自定义颜色")
        if color.isValid():
            self.hex_input.setText(color.name())

    def update_preview(self):
        """根据当前选中的颜色更新预览标签的背景色。"""
        style = f"background-color: {self.selected_color.name()}; border: 1px solid #718093; border-radius: 4px;"
        self.preview_label.setStyleSheet(style)

    @staticmethod
    def getColor(parent=None, current_color=None):
        """
        静态方法，用于创建、显示对话框并返回所选颜色。
        
        Returns:
            QColor: 如果用户点击OK，则返回选中的颜色。否则返回 None。
        """
        dialog = AdvancedColorDialog(parent, current_color)
        if dialog.exec_() == QDialog.Accepted:
            return dialog.selected_color
        return None

# ==============================================================================
# 4. UI 主对话框 (v3.3)
# ==============================================================================
class IconCustomizerDialog(QDialog):
    """插件的主界面，允许用户管理和应用图标自定义设置。"""
    def __init__(self, config_manager, main_window):
        super().__init__(main_window)
        self.config_manager = config_manager
        self.main_window = main_window
        self.current_profile_key = self.get_current_theme_key()
        
        self.setWindowTitle("图标定制器")
        self.resize(1000, 700)
        self.setMinimumSize(700, 600)
        
        self._init_ui()
        self.load_profile_data()

    def _init_ui(self):
        """初始化UI布局和控件。"""
        main_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        
        left_panel = self._create_left_panel()
        right_panel = self._create_right_panel()
        
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([300, 500]) # 初始分割比例
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.apply_btn = QPushButton("应用并刷新预览")
        self.save_close_btn = QPushButton("保存并关闭")
        self.save_close_btn.setObjectName("AccentButton") # 用于主题样式
        btn_layout.addWidget(self.apply_btn)
        btn_layout.addWidget(self.save_close_btn)
        
        main_layout.addWidget(splitter, 1)
        main_layout.addLayout(btn_layout)
        
        # 连接信号
        self.apply_btn.clicked.connect(self.apply_changes)
        self.save_close_btn.clicked.connect(self.on_save_and_close)

    def _create_left_panel(self):
        """创建左侧的控制面板。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        
        # 配置方案
        profile_group = QGroupBox("配置方案")
        profile_layout = QVBoxLayout(profile_group)
        self.profile_combo = QComboBox()
        self.profile_combo.setToolTip("选择一个主题来为其定制图标，或选择'默认配置'以应用于所有未指定的主题。")
        profile_layout.addWidget(QLabel("当前编辑的方案:"))
        profile_layout.addWidget(self.profile_combo)
        
        # 全局设置
        global_group = QGroupBox("全局设置")
        global_layout = QVBoxLayout(global_group)
        self.global_override_check = QCheckBox("启用全局颜色覆盖")
        self.global_color_btn = QPushButton("选择颜色")
        self.hide_all_check = QCheckBox("隐藏所有图标")
        global_layout.addWidget(self.global_override_check)
        global_layout.addWidget(self.global_color_btn)
        global_layout.addWidget(self.hide_all_check)
        
        # 重置
        reset_group = QGroupBox("重置")
        reset_layout = QVBoxLayout(reset_group)
        self.reset_profile_btn = QPushButton("重置当前方案")
        reset_layout.addWidget(self.reset_profile_btn)
        
        layout.addWidget(profile_group)
        layout.addWidget(global_group)
        layout.addStretch()
        layout.addWidget(reset_group)
        
        # 连接信号
        self.profile_combo.currentIndexChanged.connect(self.on_profile_changed)
        self.global_override_check.toggled.connect(self.on_global_setting_changed)
        self.global_color_btn.clicked.connect(self.on_select_global_color)
        self.hide_all_check.toggled.connect(self.on_global_setting_changed)
        self.reset_profile_btn.clicked.connect(self.on_reset_profile)
        
        return panel

    def _create_right_panel(self):
        """创建右侧的图标列表。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["预览", "图标名称", "自定义颜色", "隐藏"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.on_table_context_menu)
        layout.addWidget(self.table)
        return panel

    def get_current_theme_key(self):
        """获取当前主程序使用的主题文件名作为配置方案的键。"""
        theme_path = self.main_window.config.get("theme", "__default__")
        return os.path.basename(theme_path) if theme_path else "__default__"

    def on_profile_changed(self, index):
        """当配置方案下拉框选择改变时，加载新的方案数据。"""
        if index == -1: return
        self.current_profile_key = self.profile_combo.itemData(index)
        self.load_profile_data()

    def load_profile_data(self):
        """加载当前配置方案的数据并更新UI。"""
        self.table.clearContents()
        
        # 填充配置方案下拉框
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItem("默认配置 (应用于所有)", "__default__")
        all_profiles = self.config_manager.settings.get("profiles", {})
        for theme_name in sorted(all_profiles.keys()):
            if theme_name != "__default__":
                self.profile_combo.addItem(theme_name, theme_name)
        idx = self.profile_combo.findData(self.current_profile_key)
        if idx != -1: self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.blockSignals(False)
        
        # 加载全局设置
        profile = self.config_manager.get_or_create_profile(self.current_profile_key)
        self.global_override_check.setChecked(profile.get('global_override', False))
        self.hide_all_check.setChecked(profile.get('hide_all', False))
        self.global_color_btn.setStyleSheet(f"background-color: {profile.get('global_color', '#FFFFFF')};")
        
        # 填充图标列表
        all_known_icons = sorted(list(self.config_manager.discovered_icons))
        self.table.setRowCount(len(all_known_icons))
        for row, icon_name in enumerate(all_known_icons):
            icon_settings = profile.get('icon_settings', {}).get(icon_name, {})
            
            # 预览
            preview_icon = self._get_preview_icon(icon_name, profile)
            preview_item = QTableWidgetItem()
            preview_item.setIcon(preview_icon)
            preview_item.setTextAlignment(Qt.AlignCenter)
            preview_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            
            # 名称
            name_item = QTableWidgetItem(icon_name)
            name_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            
            # 颜色按钮
            color_btn = QPushButton()
            color = icon_settings.get('color')
            if color:
                color_btn.setStyleSheet(f"background-color: {color};")
            color_btn.clicked.connect(lambda _, r=row, n=icon_name, btn=color_btn: self.on_select_icon_color(r, n, btn))
            
            # 隐藏复选框
            hide_check = QCheckBox()
            hide_check.setChecked(icon_settings.get('hidden', False))
            hide_check.toggled.connect(lambda checked, n=icon_name: self.on_hide_toggled(n, checked))
            hide_widget = QWidget()
            hide_layout = QHBoxLayout(hide_widget)
            hide_layout.addWidget(hide_check)
            hide_layout.setAlignment(Qt.AlignCenter)
            hide_layout.setContentsMargins(0,0,0,0)

            self.table.setItem(row, 0, preview_item)
            self.table.setItem(row, 1, name_item)
            self.table.setCellWidget(row, 2, color_btn)
            self.table.setCellWidget(row, 3, hide_widget)
            
        self.table.resizeRowsToContents()

    def _get_preview_icon(self, icon_name, profile):
        """根据当前配置计算并返回图标的预览样式。"""
        if profile.get('hide_all', False):
            return QIcon()
        
        icon_settings = profile.get('icon_settings', {}).get(icon_name, {})
        if icon_settings.get('hidden', False):
            return QIcon()
        
        # 使用原始 get_icon 方法获取未被修改的图标
        original_icon = self.main_window.icon_manager.original_get_icon(icon_name)
        
        if profile.get('global_override', False) and profile.get('global_color'):
            return recolor_icon(original_icon, profile.get('global_color'))
        
        custom_color = icon_settings.get('color')
        if custom_color:
            return recolor_icon(original_icon, custom_color)
            
        return original_icon

    def on_select_global_color(self):
        """打开颜色选择器以设置全局覆盖颜色。"""
        profile = self.config_manager.get_or_create_profile(self.current_profile_key)
        current_color = profile.get('global_color')
        color = AdvancedColorDialog.getColor(self, current_color)
        if color:
            profile['global_color'] = color.name()
            self.global_color_btn.setStyleSheet(f"background-color: {color.name()};")

    def on_select_icon_color(self, row, icon_name, button):
        """打开颜色选择器为单个图标设置颜色。"""
        profile = self.config_manager.get_or_create_profile(self.current_profile_key)
        current_color = profile.get('icon_settings', {}).get(icon_name, {}).get('color')
        
        color = AdvancedColorDialog.getColor(self, current_color)
        
        icon_settings = profile.setdefault('icon_settings', {}).setdefault(icon_name, {})
        if color:
            icon_settings['color'] = color.name()
            button.setStyleSheet(f"background-color: {color.name()};")
        else: # 如果用户取消选择，则清除颜色
            if 'color' in icon_settings:
                del icon_settings['color']
            button.setStyleSheet("")
        
        self.update_row_preview(row)

    def on_table_context_menu(self, position):
        """处理表格的右键菜单请求。"""
        selected_items = self.table.selectedItems()
        selected_rows_count = len(set(item.row() for item in selected_items))
        menu = QMenu(self)
        
        if selected_rows_count > 0:
            menu.addAction(f"已选中 {selected_rows_count} 个项目").setEnabled(False)
            menu.addSeparator()
            menu.addAction("批量设置颜色...").triggered.connect(lambda: self.on_batch_operation('color'))
            menu.addAction("批量隐藏").triggered.connect(lambda: self.on_batch_operation('hide'))
            menu.addAction("批量显示").triggered.connect(lambda: self.on_batch_operation('show'))
            menu.addSeparator()
            menu.addAction("重置选中项").triggered.connect(lambda: self.on_batch_operation('reset'))
        else:
            menu.addAction("请先选择一个或多个图标").setEnabled(False)
            
        menu.exec_(self.table.viewport().mapToGlobal(position))
    
    def on_batch_operation(self, operation):
        """执行批量操作（颜色、隐藏、显示、重置）。"""
        selected_rows = sorted(list(set(item.row() for item in self.table.selectedItems())))
        if not selected_rows:
            QMessageBox.information(self, "提示", "请先在右侧列表中选择一个或多个图标。")
            return

        color = None
        if operation == 'color':
            color_obj = AdvancedColorDialog.getColor(self)
            if not color_obj: return # 用户取消
            color = color_obj.name()

        profile = self.config_manager.get_or_create_profile(self.current_profile_key)
        for row in selected_rows:
            icon_name = self.table.item(row, 1).text()
            icon_settings = profile.setdefault('icon_settings', {}).setdefault(icon_name, {})
            
            if operation == 'color':
                icon_settings['color'] = color
            elif operation == 'hide':
                icon_settings['hidden'] = True
            elif operation == 'show':
                icon_settings['hidden'] = False
            elif operation == 'reset':
                if icon_name in profile.get('icon_settings', {}):
                    del profile['icon_settings'][icon_name]

            self.update_row_ui(row)

    def on_global_setting_changed(self):
        """当全局设置复选框状态改变时，更新配置。"""
        profile = self.config_manager.get_or_create_profile(self.current_profile_key)
        profile['global_override'] = self.global_override_check.isChecked()
        profile['hide_all'] = self.hide_all_check.isChecked()

    def on_hide_toggled(self, icon_name, checked):
        """当单个图标的隐藏复选框状态改变时，更新配置。"""
        profile = self.config_manager.get_or_create_profile(self.current_profile_key)
        profile.setdefault('icon_settings', {}).setdefault(icon_name, {})['hidden'] = checked
        
        # 找到对应的行并更新预览
        all_rows = range(self.table.rowCount())
        try:
            row_index = next(i for i in all_rows if self.table.item(i, 1).text() == icon_name)
            self.update_row_preview(row_index)
        except StopIteration:
            pass # 如果找不到行，则不执行任何操作

    def on_reset_profile(self):
        """重置当前配置方案为默认值。"""
        reply = QMessageBox.question(self, "确认重置",
                                     f"您确定要重置方案 '{self.current_profile_key}' 的所有设置吗？此操作不可撤销。",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            profiles = self.config_manager.settings.get("profiles", {})
            profiles[self.current_profile_key] = self.config_manager.DEFAULT_PROFILE.copy()
            self.apply_changes() # 应用更改以刷新UI

    def update_row_preview(self, row):
        """仅更新指定行图标的预览。"""
        icon_name = self.table.item(row, 1).text()
        profile = self.config_manager.get_or_create_profile(self.current_profile_key)
        self.table.item(row, 0).setIcon(self._get_preview_icon(icon_name, profile))

    def update_row_ui(self, row):
        """更新指定行的所有UI元素（预览、颜色按钮、复选框）。"""
        self.update_row_preview(row)
        icon_name = self.table.item(row, 1).text()
        profile = self.config_manager.get_or_create_profile(self.current_profile_key)
        settings = profile.get('icon_settings', {}).get(icon_name, {})
        
        color_btn = self.table.cellWidget(row, 2)
        if isinstance(color_btn, QPushButton):
            color = settings.get('color')
            color_btn.setStyleSheet(f"background-color: {color};" if color else "")

        hide_widget = self.table.cellWidget(row, 3)
        if hide_widget:
            hide_check = hide_widget.findChild(QCheckBox)
            if hide_check:
                hide_check.setChecked(settings.get('hidden', False))

    def apply_changes(self):
        """应用所有更改并刷新主程序的UI。"""
        self.main_window.icon_manager.clear_cache()
        self.main_window.update_all_module_icons()
        self.main_window.update_pinned_plugins_ui()
        self.load_profile_data() # 重新加载对话框数据以确保同步
        QMessageBox.information(self, "应用成功", "所有图标已根据当前设置刷新！")

    def on_save_and_close(self):
        """应用更改，保存配置，然后关闭对话框。"""
        self.apply_changes()
        self.config_manager.save()
        self.accept()

# ==============================================================================
# 5. 插件主类 (v3.3 - Global Scan Logic)
# ==============================================================================
class IconCustomizerPlugin(BasePlugin):
    """图标定制器插件的主入口点。"""
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        # [核心修改 v3.3] 初始化时传入依赖
        self.config_manager = IconConfigManager(main_window, plugin_manager)
        self.dialog_instance = None
        self.original_get_icon_method = None

    def setup(self):
        """启用插件时调用，执行图标系统的修补（Monkey Patching）。"""
        print("[Icon Customizer] 正在启用并修补图标系统...")
        
        # [核心修改 v3.3] 在修补前执行一次全局扫描
        self.config_manager.discover_all_icons()
        
        self.original_get_icon_method = IconManager.get_icon
        
        # 保存原始方法，以便卸载插件时恢复
        setattr(self.main_window.icon_manager.__class__, 'original_get_icon', self.original_get_icon_method)

        # 定义新的 get_icon 方法
        def patched_get_icon(instance_self, icon_name):
            # [修改 v3.3] 插件自己不再负责发现，而是由 ConfigManager 在启动时统一处理
            # self.config_manager.discovered_icons.add(icon_name) <-- 已移除
            
            # 后续的 patched_get_icon 逻辑保持不变
            current_theme_file = os.path.basename(self.main_window.config.get("theme", "__default__")) or "__default__"
            profile = self.config_manager.get_current_profile(current_theme_file)

            if profile.get('hide_all', False):
                return QIcon()

            icon_settings = profile.get('icon_settings', {}).get(icon_name, {})
            if icon_settings.get('hidden', False):
                return QIcon()

            original_icon = self.original_get_icon_method(instance_self, icon_name)

            if profile.get('global_override', False) and profile.get('global_color'):
                return recolor_icon(original_icon, profile.get('global_color'))

            custom_color = icon_settings.get('color')
            if custom_color:
                return recolor_icon(original_icon, custom_color)

            return original_icon

        # 应用补丁
        IconManager.get_icon = patched_get_icon
        
        # 强制刷新UI
        self.main_window.icon_manager.clear_cache()
        self.main_window.update_all_module_icons()
        self.main_window.update_pinned_plugins_ui()
        print("[Icon Customizer] 图标系统已修补。")
        return True

    def teardown(self):
        """禁用插件时调用，恢复原始的图标系统。"""
        if self.original_get_icon_method:
            IconManager.get_icon = self.original_get_icon_method
            if hasattr(IconManager, 'original_get_icon'):
                delattr(IconManager, 'original_get_icon')
            
            # 强制刷新UI以恢复原始图标
            self.main_window.icon_manager.clear_cache()
            self.main_window.update_all_module_icons()
            self.main_window.update_pinned_plugins_ui()
            print("[Icon Customizer] 已恢复原始图标系统。")
        
        if self.dialog_instance:
            self.dialog_instance.close()

    def execute(self, **kwargs):
        """执行插件的主要功能，即打开定制器对话框。"""
        # [核心修改 v3.3] 每次打开对话框前都重新扫描，以捕获新安装的插件的图标
        self.config_manager.discover_all_icons()
        
        if self.dialog_instance is None:
            self.dialog_instance = IconCustomizerDialog(self.config_manager, self.main_window)
            self.dialog_instance.finished.connect(lambda: setattr(self, 'dialog_instance', None))
        
        # 确保对话框加载的是最新的主题配置
        self.dialog_instance.current_profile_key = self.dialog_instance.get_current_theme_key()
        self.dialog_instance.load_profile_data()
        
        self.dialog_instance.show()
        self.dialog_instance.raise_()
        self.dialog_instance.activateWindow()
