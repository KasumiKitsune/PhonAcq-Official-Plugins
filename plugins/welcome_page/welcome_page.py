# --- START OF FILE plugins/welcome_page/welcome_page.py (v1.2 Refined) ---

import os
import sys
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QTextBrowser, QGroupBox, QSpacerItem, QSizePolicy,
                             QListWidget, QListWidgetItem, QStackedWidget, QFrame, QMessageBox, QTabWidget, QWidget)
from PyQt5.QtCore import Qt, QSize, QTimer, QDateTime
from PyQt5.QtGui import QFont, QIcon, QPixmap

# 导入插件API基类
try:
    from plugin_system import BasePlugin
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

# ==============================================================================
# 插件主类 (保持不变)
# ==============================================================================
class WelcomePagePlugin(BasePlugin):
    """
    一个演示插件，用于展示PhonAcq Assistant的欢迎信息和快速开始指南。
    """
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.dialog_instance = None

    def setup(self):
        print(f"'{self.main_window.windowTitle()}' 说：欢迎页面插件已准备就绪！")
        return True

    def teardown(self):
        if self.dialog_instance:
            self.dialog_instance.close()
        self.dialog_instance = None
        print("欢迎页面插件已卸载！")

    def execute(self, **kwargs):
        try:
            if self.dialog_instance is None:
                self.dialog_instance = WelcomeDialog(self.main_window)
                self.dialog_instance.finished.connect(self.on_dialog_finished)

            self.dialog_instance.show()
            self.dialog_instance.raise_()
            self.dialog_instance.activateWindow()
        except Exception as e:
            import traceback
            print(f"执行欢迎页面插件时出错: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self.main_window, "插件执行错误", f"无法打开'欢迎与开始'页面:\n{e}")

    def on_dialog_finished(self):
        self.dialog_instance = None

# ==============================================================================
# [核心重构] 欢迎页面对话框类 (v1.2)
# ==============================================================================
class WelcomeDialog(QDialog):
    """一个精致的、带导航的“关于与快速导航”对话框。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.icon_manager = self.parent_window.icon_manager

        self.setWindowTitle("关于 PhonAcq Assistant")
        self.setMinimumSize(750, 550)
        self.resize(800, 600)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self._init_ui()

    def _init_ui(self):
        # --- 主布局：水平分割 ---
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- 1. 左侧导航栏 ---
        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(200)
        # 使用自定义的 objectName，便于QSS进行特殊样式定义
        self.nav_list.setObjectName("WelcomeNavList")
        
        # --- 2. 右侧内容区 ---
        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("WelcomeContentStack")

        main_layout.addWidget(self.nav_list)
        main_layout.addWidget(self.content_stack, 1)

        # --- 创建并添加页面 ---
        self._create_about_page()
        self._create_quick_start_page()
        self._create_credits_page()

        # --- 连接信号 ---
        self.nav_list.currentItemChanged.connect(self.change_page)
        
        # --- 初始化状态 ---
        self.nav_list.setCurrentRow(0)

    def change_page(self, current, previous):
        if current:
            self.content_stack.setCurrentIndex(self.nav_list.row(current))

    def _create_nav_item(self, text, icon_name, page_widget):
        """辅助函数，用于创建导航项和对应的内容页面。"""
        item = QListWidgetItem(self.icon_manager.get_icon(icon_name), text)
        item.setSizeHint(QSize(0, 50)) # 增加行高
        self.nav_list.addItem(item)
        self.content_stack.addWidget(page_widget)

    def _create_styled_text_browser(self):
        """创建一个带有统一HTML和CSS样式的文本浏览器。"""
        browser = QTextBrowser()
        browser.setReadOnly(True)
        browser.setOpenExternalLinks(True)
        
        # [核心修改] 直接在这里设置基础字体
        font = QFont("Microsoft YaHei", 12) # 使用微软雅黑，12号字
        browser.setFont(font)

        doc = browser.document()
        # [核心修改] 调整CSS以适应更大的基础字体
        doc.setDefaultStyleSheet("""
            p, li { line-height: 1.7; color: #333; } /* 增加行高以提高可读性 */
            h2 { font-size: 22px; font-weight: bold; color: #1C2A40; border-bottom: 2px solid #C5D9E8; padding-bottom: 5px; margin-bottom: 15px;}
            b { color: #3B6894; }
            code { background-color: #E8EEF4; padding: 2px 5px; border-radius: 0px; font-family: Consolas, 'Courier New', monospace;}
            hr { border: 1px solid #E0E0E0; }
        """)
        return browser

    # --- 页面创建方法 ---
    def _create_about_page(self):
        page_widget = QWidget()
        layout = QVBoxLayout(page_widget)
        layout.setContentsMargins(25, 20, 25, 20)

        header_layout = QHBoxLayout()
        icon_label = QLabel()
        app_icon_pixmap = self.icon_manager.get_icon("app_logo").pixmap(64, 64)
        icon_label.setPixmap(app_icon_pixmap)
        header_layout.addWidget(icon_label)
        
        title_layout = QVBoxLayout()
        title_label = QLabel("PhonAcq Assistant")
        # [核心修改] 明确设置标题字体
        title_label.setFont(QFont("Microsoft YaHei", 26, QFont.Bold))
        version_label = QLabel(f"版本 v4.1 (Odyssey)  |  最后更新: {QDateTime.currentDateTime().toString('yyyy-MM-dd')}")
        version_label.setObjectName("VersionLabel")
        # [核心修改] 明确设置版本标签字体
        version_label.setFont(QFont("Microsoft YaHei", 10))

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
        self._add_bottom_buttons(layout)

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
        self._add_bottom_buttons(layout)
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
                <li><b>Pandas & OpenPyXL:</b> 使得与Excel等表格数据的交互变得简单。</li>
                <li><b>Pypinyin:</b> 为汉语拼音到IPA的转换提供了核心支持。</li>
                <li><i>以及其他所有在项目中使用的第三方库...</i></li>
            </ul>
            <p>同时，也感谢所有为本项目提供反馈和建议的用户和测试者。</p>
        """)

        layout.addWidget(browser, 1)
        self._add_bottom_buttons(layout)
        self._create_nav_item("鸣谢", "thanks", page_widget)

    def _add_bottom_buttons(self, layout):
        """辅助函数，用于在页面底部添加统一的按钮栏。"""
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("background-color: #e0e0e0;")

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        settings_btn = QPushButton("打开程序设置")
        settings_btn.clicked.connect(self.go_to_settings)
        help_btn = QPushButton("查看帮助文档")
        help_btn.clicked.connect(self.go_to_help)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(settings_btn)
        button_layout.addWidget(help_btn)
        button_layout.addWidget(close_btn)
        
        layout.addWidget(line)
        layout.addLayout(button_layout)
        
    def go_to_settings(self):
        """跳转到主程序的“程序设置”标签页。"""
        self.accept()
        self._navigate_to_tab("系统与帮助", "程序设置")

    def go_to_help(self):
        """跳转到主程序的“帮助文档”标签页。"""
        self.accept()
        self._navigate_to_tab("系统与帮助", "帮助文档")

    def _navigate_to_tab(self, main_tab_name, sub_tab_name):
        """通用导航函数。"""
        main_tab_index = -1
        for i in range(self.parent_window.main_tabs.count()):
            if self.parent_window.main_tabs.tabText(i) == main_tab_name:
                main_tab_index = i
                break
        
        if main_tab_index != -1:
            self.parent_window.main_tabs.setCurrentIndex(main_tab_index)
            sub_tab_widget = self.parent_window.main_tabs.widget(main_tab_index)
            if isinstance(sub_tab_widget, QStackedWidget) or isinstance(sub_tab_widget, QTabWidget):
                sub_tab_index = -1
                for j in range(sub_tab_widget.count()):
                    if sub_tab_widget.tabText(j) == sub_tab_name:
                        sub_tab_index = j
                        break
                if sub_tab_index != -1:
                    QTimer.singleShot(0, lambda: sub_tab_widget.setCurrentIndex(sub_tab_index))