# --- START OF FILE welcome_page.py ---

# --- START OF FILE plugins/welcome_page/welcome_page.py (v2.3 - "Don't Show Next Time" by Default) ---

import os
import sys
import json
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QTextBrowser, QGroupBox, QSpacerItem, QSizePolicy,
                             QListWidget, QListWidgetItem, QStackedWidget, QFrame,
                             QMessageBox, QTabWidget, QWidget, QCheckBox)
from PyQt5.QtCore import Qt, QSize, QTimer, pyqtProperty, QEasingCurve, QPropertyAnimation
from PyQt5.QtGui import QFont, QIcon, QPixmap, QPainter

# 导入插件API基类
try:
    from plugin_system import BasePlugin
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

# ==============================================================================
# 内部自定义控件：AnimatedLogoLabel (从 settings_module 移植，现为插件私有)
# ==============================================================================
class AnimatedLogoLabel(QLabel):
    """
    一个专门用于“关于”页面的、支持悬停和点击缩放动画的Logo标签。
    此版本内置于 welcome_page 插件，不依赖外部导入。
    """
    MAX_HOVER_SCALE = 1.1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setAlignment(Qt.AlignCenter)
        self._base_pixmap = QPixmap()
        self._scale = 1.0
        self.scale_animation = QPropertyAnimation(self, b"_scale")
        self.scale_animation.setDuration(150)
        self.scale_animation.setEasingCurve(QEasingCurve.OutCubic)

    def setPixmap(self, pixmap):
        self._base_pixmap = pixmap
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        if self._base_pixmap.isNull():
            return QSize(128, 128) # 默认大小
        return self._base_pixmap.size() * self.MAX_HOVER_SCALE

    def minimumSizeHint(self):
        return self.sizeHint()

    @pyqtProperty(float)
    def _scale(self):
        return self.__scale
    
    @_scale.setter
    def _scale(self, value):
        self.__scale = value
        self.update()

    def paintEvent(self, event):
        if self._base_pixmap.isNull():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        
        center = self.rect().center()
        
        painter.translate(center)
        painter.scale(self._scale, self._scale)
        painter.translate(-center)
        
        target_rect = self._base_pixmap.rect()
        target_rect.moveCenter(self.rect().center())

        painter.drawPixmap(target_rect, self._base_pixmap)

    def enterEvent(self, event):
        self.scale_animation.stop()
        self.scale_animation.setEndValue(self.MAX_HOVER_SCALE)
        self.scale_animation.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.scale_animation.stop()
        self.scale_animation.setEndValue(1.0)
        self.scale_animation.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.scale_animation.stop()
            self.scale_animation.setEndValue(0.9)
            self.scale_animation.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.scale_animation.stop()
            target_scale = self.MAX_HOVER_SCALE if self.underMouse() else 1.0
            self.scale_animation.setEndValue(target_scale)
            self.scale_animation.start()
        super().mouseReleaseEvent(event)


# ==============================================================================
# 插件主类 (v2.3)
# ==============================================================================
class WelcomePagePlugin(BasePlugin):
    """
    一个演示插件，用于展示PhonAcq Assistant的欢迎信息和快速开始指南。
    此版本增加了在程序首次启动时自动弹出的功能，且默认下次不再显示。
    """
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.dialog_instance = None
        self.config_path = os.path.join(os.path.dirname(__file__), 'config.json')

        # [核心修改] 重构首次启动逻辑
        self.show_this_time = False
        if not os.path.exists(self.config_path):
            # 1. 如果配置文件不存在，说明是首次运行
            print("WelcomePage: First run detected, will show welcome screen once.")
            # 2. 设置一个临时标志，用于在本次启动时显示窗口
            self.show_this_time = True
            # 3. 设置默认配置，即“下次不显示”，这样复选框会默认未勾选
            self.config = {"show_on_startup": False}
        else:
            # 如果配置文件已存在，则正常加载
            self.config = self._load_config()
            # 根据加载的配置决定本次是否显示
            self.show_this_time = self.config.get("show_on_startup", False)

    def _load_config(self):
        """安全地加载插件的配置文件。"""
        defaults = {"show_on_startup": False}
        if not os.path.exists(self.config_path):
            return defaults
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            defaults.update(config_data)
            return defaults
        except (json.JSONDecodeError, IOError) as e:
            print(f"WelcomePage: Failed to load config.json, using defaults: {e}")
            return defaults

    def _save_config(self):
        """将当前配置保存到文件。"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4)
        except IOError as e:
            print(f"无法保存欢迎页面插件配置: {e}")

    def setup(self):
        """插件初始化设置。"""
        print(f"'{self.main_window.windowTitle()}' 说：欢迎页面插件已准备就绪！")
        
        # [核心修改] 使用临时标志来决定是否显示，而不是直接读取配置
        if self.show_this_time:
            # 使用 QTimer.singleShot 确保在主窗口完全显示后再弹出对话框
            QTimer.singleShot(500, self.execute) # 延迟500ms，体验更好
            
        return True

    def teardown(self):
        """插件卸载清理。"""
        if self.dialog_instance:
            self.dialog_instance.close()
        self.dialog_instance = None
        print("欢迎页面插件已卸载！")

    def execute(self, **kwargs):
        """插件执行入口点，负责创建和显示对话框。"""
        try:
            if self.dialog_instance is None:
                self.dialog_instance = WelcomeDialog(self, self.main_window)
                self.dialog_instance.finished.connect(self.on_dialog_finished)

            self.dialog_instance.show()
            self.dialog_instance.raise_()
            self.dialog_instance.activateWindow()
        except Exception as e:
            import traceback
            print(f"执行欢迎页面插件时出错: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self.main_window, "插件执行错误", f"无法打开'欢迎与开始'页面:\n{e}")

    def on_dialog_finished(self):
        """对话框关闭时的回调。"""
        # [核心新增] 确保在对话框关闭时，无论如何都保存一次配置。
        # 这对于首次运行时创建配置文件至关重要，能将默认的 "show_on_startup": False 写入磁盘。
        self._save_config()
        self.dialog_instance = None

# ==============================================================================
# 欢迎页面对话框类 (v2.3)
# ==============================================================================
class WelcomeDialog(QDialog):
    """一个精致的、带导航的“关于与快速导航”对话框。"""
    def __init__(self, plugin_instance, parent=None):
        super().__init__(parent)
        self.plugin = plugin_instance # 保存对插件实例的引用
        self.parent_window = parent
        self.icon_manager = self.parent_window.icon_manager

        self.setWindowTitle("欢迎使用 PhonAcq Assistant")
        self.setMinimumSize(750, 550)
        self.resize(800, 600)
        
        self._init_ui()

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(200)
        self.nav_list.setObjectName("WelcomeNavList")
        
        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("WelcomeContentStack")

        main_layout.addWidget(self.nav_list)
        main_layout.addWidget(self.content_stack, 1)

        self._create_about_page()
        self._create_quick_start_page()
        self._create_credits_page()

        self.nav_list.currentItemChanged.connect(self.change_page)
        self.nav_list.setCurrentRow(0)

    def change_page(self, current, previous):
        if current:
            self.content_stack.setCurrentIndex(self.nav_list.row(current))

    def _create_nav_item(self, text, icon_name, page_widget):
        item = QListWidgetItem(self.icon_manager.get_icon(icon_name), text)
        item.setSizeHint(QSize(0, 50))
        self.nav_list.addItem(item)
        self.content_stack.addWidget(page_widget)

    def _create_styled_text_browser(self):
        browser = QTextBrowser()
        browser.setReadOnly(True)
        browser.setOpenExternalLinks(True)
        browser.setFont(QFont("Microsoft YaHei", 12))
        doc = browser.document()
        doc.setDefaultStyleSheet("""
            p, li { line-height: 1.7; color: #333; }
            h2 { font-size: 22px; font-weight: bold; color: #1C2A40; border-bottom: 2px solid #C5D9E8; padding-bottom: 5px; margin-bottom: 15px;}
            b { color: #3B6894; }
            code { background-color: #E8EEF4; padding: 2px 5px; border-radius: 4px; font-family: Consolas, 'Courier New', monospace;}
            hr { border: 1px solid #E0E0E0; }
        """)
        return browser

    def _create_about_page(self):
        page_widget = QWidget()
        layout = QVBoxLayout(page_widget)
        layout.setContentsMargins(25, 20, 25, 20)

        header_layout = QHBoxLayout()
        logo_label = AnimatedLogoLabel()
        
        def get_app_root_path():
            if getattr(sys, 'frozen', False):
                return os.path.dirname(sys.executable)
            else:
                return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

        app_root = get_app_root_path()
        custom_logo_path = os.path.join(app_root, "assets", "logo.png")
        
        logo_pixmap = QPixmap()
        if os.path.exists(custom_logo_path):
            logo_pixmap.load(custom_logo_path)
        else:
            app_icon = self.icon_manager.get_icon("app_logo")
            logo_pixmap = app_icon.pixmap(QSize(96, 96))
        
        final_pixmap = logo_pixmap.scaled(QSize(96, 96), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        logo_label.setPixmap(final_pixmap)
        header_layout.addWidget(logo_label)
        
        title_layout = QVBoxLayout()
        title_label = QLabel(
            f"<span style='font-size:16pt; font-weight:bold;'>PhonAcq</span>"
            f"<span style='font-size:14pt; font-weight:bold;'> Assistant</span>"
        )
        
        version_str = "v1.7.5" 
        version_label = QLabel(f"版本 {version_str}")
        version_label.setObjectName("VersionLabel")
        version_label.setFont(QFont("Microsoft YaHei", 12))

        title_layout.addWidget(title_label)
        title_layout.addWidget(version_label)
        title_layout.setSpacing(2)
        header_layout.addLayout(title_layout)
        header_layout.addStretch()

        browser = self._create_styled_text_browser()
        browser.setHtml("""
            <h2>关于本应用</h2>
            <p>
            <b>PhonAcq Assistant</b> 是一款专为语言学研究者设计的<b>多功能语音数据处理平台</b>。
            我们的核心使命是：赋能语言学研究者，通过直观的图形界面、流程自动化和严谨的数据管理，
            让语音数据的采集、管理、分析与标注变得<b>极致简单高效</b>。
            </p>
            <p>本项目为开源软件，您可以在我们的代码仓库中找到所有源代码和文档。</p>
        """)
        layout.addLayout(header_layout)
        layout.addWidget(browser, 1)
        self._add_bottom_controls(layout)
        self._create_nav_item("关于", "info", page_widget)

    def _create_quick_start_page(self):
        page_widget = QWidget()
        layout = QVBoxLayout(page_widget)
        layout.setContentsMargins(25, 20, 25, 20)
        browser = self._create_styled_text_browser()
        browser.setHtml("""
            <h2>快速开始指南</h2>
            <p>不确定从哪里开始？这里是一些最常用的功能入口：</p>
            <ul>
                <li><b>数据采集：</b>前往 <code>数据采集</code> 标签页，开始<b>标准朗读采集</b>或<b>看图说话采集</b>。</li>
                <li><b>准备词表：</b>在 <code>数据准备</code> 中，使用<b>通用词表编辑器</b>或<b>图文词表编辑器</b>创建您的研究材料。</li>
                <li><b>管理数据：</b>通过 <code>资源管理</code> -> <b>音频数据管理器</b> 查看、播放和整理您的录音。</li>
                <li><b>高级分析：</b>在 <code>资源管理</code> -> <b>音频分析</b> 模块深入探究语音的声学特征。</li>
            </ul>
        """)
        layout.addWidget(browser, 1)
        self._add_bottom_controls(layout)
        self._create_nav_item("快速开始", "quick_start", page_widget)

    def _create_credits_page(self):
        page_widget = QWidget()
        layout = QVBoxLayout(page_widget)
        layout.setContentsMargins(25, 20, 25, 20)
        browser = self._create_styled_text_browser()
        browser.setHtml("""
            <h2>鸣谢</h2>
            <p>PhonAcq Assistant 的开发离不开以下优秀的开源项目和库。我们对它们的作者和社区表示诚挚的感谢。</p>
            <ul>
                <li><b>PyQt5:</b> 构成了本应用图形用户界面的基石。</li>
                <li><b>Librosa:</b> 为音频分析模块提供了强大的信号处理和特征提取能力。</li>
                <li><b>SoundFile & SoundDevice:</b> 提供了可靠的音频文件读写和设备I/O功能。</li>
                <li><i>以及其他所有在项目中使用的第三方库...</i></li>
            </ul>
        """)
        layout.addWidget(browser, 1)
        self._add_bottom_controls(layout)
        self._create_nav_item("鸣谢", "thanks", page_widget)

    def _add_bottom_controls(self, layout):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        
        # 复选框的勾选状态直接与插件的 config 字典绑定。
        # 由于我们在插件初始化时对首次运行设置了 config["show_on_startup"] = False，
        # 所以这里会自动显示为未勾选状态。
        self.show_on_startup_checkbox = QCheckBox("启动时显示此欢迎页面")
        self.show_on_startup_checkbox.setChecked(self.plugin.config.get("show_on_startup", False))
        self.show_on_startup_checkbox.stateChanged.connect(self.on_checkbox_changed)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        settings_btn = QPushButton("打开程序设置"); settings_btn.clicked.connect(self.go_to_settings)
        help_btn = QPushButton("查看帮助文档"); help_btn.clicked.connect(self.go_to_help)
        close_btn = QPushButton("关闭"); close_btn.clicked.connect(self.accept)
        button_layout.addWidget(settings_btn); button_layout.addWidget(help_btn); button_layout.addWidget(close_btn)
        
        layout.addWidget(line)
        layout.addWidget(self.show_on_startup_checkbox)
        layout.addLayout(button_layout)
    
    def on_checkbox_changed(self, state):
        """当复选框状态改变时，更新插件的配置并立即保存。"""
        self.plugin.config["show_on_startup"] = (state == Qt.Checked)
        self.plugin._save_config()

    def go_to_settings(self):
        self.accept()
        self._navigate_to_tab("系统与帮助", "程序设置")

    def go_to_help(self):
        self.accept()
        self._navigate_to_tab("系统与帮助", "帮助文档")

    def _navigate_to_tab(self, main_tab_name, sub_tab_name):
        if hasattr(self.parent_window, '_navigate_to_tab'):
             self.parent_window._navigate_to_tab(main_tab_name, sub_tab_name)

# --- END OF FILE ---