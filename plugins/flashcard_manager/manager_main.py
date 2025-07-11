# --- START OF FILE plugins/flashcard_manager/manager_main.py ---

import os
import sys
import json
import shutil
import subprocess
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QListWidget, QListWidgetItem, QStackedWidget, QWidget,
                             QSplitter, QMessageBox, QFileDialog, QTableWidget,
                             QTableWidgetItem, QHeaderView, QFormLayout, QTextBrowser,
                             QMenu, QGroupBox, QShortcut)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QPixmap, QKeySequence

# 动态导入依赖
try:
    from plugin_system import BasePlugin
    from modules.dialect_visual_collector_module import ScalableImageLabel
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from modules.plugin_system import BasePlugin
    from modules.dialect_visual_collector_module import ScalableImageLabel


# ==============================================================================
# 插件主类 (保持不变)
# ==============================================================================
class FlashcardManagerPlugin(BasePlugin):
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.manager_dialog = None

    def setup(self):
        print("速记卡管理器插件已准备就绪。")
        return True

    def teardown(self):
        if self.manager_dialog:
            self.manager_dialog.close()
        print("速记卡管理器插件已卸载。")

    def execute(self, **kwargs):
        if self.manager_dialog is None:
            self.manager_dialog = ManagerDialog(self.main_window)
            self.manager_dialog.finished.connect(lambda: setattr(self, 'manager_dialog', None))
        
        self.manager_dialog.show()
        self.manager_dialog.raise_()
        self.manager_dialog.activateWindow()

# ==============================================================================
# 管理器对话框 UI 与逻辑
# ==============================================================================
class ManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.icon_manager = self.parent_window.icon_manager
        
        base_flashcard_dir = os.path.join(self.parent_window.BASE_PATH, "flashcards")
        self.VISUAL_DIR = os.path.join(base_flashcard_dir, "visual_wordlists")
        self.COMMON_DIR = os.path.join(base_flashcard_dir, "common_wordlists")
        
        base_path = self.parent_window.BASE_PATH
        self.SOURCE_STD_DIR = os.path.join(base_path, "word_lists") 
        self.SOURCE_VISUAL_DIR = os.path.join(base_path, "dialect_visual_wordlists")

        self.visual_preview_data = []
        self.visual_preview_index = -1

        self.setWindowTitle("速记卡管理器")
        self.setMinimumSize(1100, 700)
        self._init_ui()
        self._connect_signals()
        self._add_shortcuts() # [新增] 添加快捷键
        self.populate_wordlist()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)

        # --- 左侧面板 ---
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_widget.setFixedWidth(350)
        
        left_layout.addWidget(QLabel("速记卡词表:"))
        self.wordlist_widget = QListWidget()
        self.wordlist_widget.setToolTip("所有可用于“速记卡”模块的词表。\n右键单击可进行更多操作。")
        
        import_btn = QPushButton("导入词表...")
        import_btn.setIcon(self.icon_manager.get_icon("add_row"))
        import_btn.setToolTip("从主词表库或其他位置导入新的词表用于学习。")
        
        # [修改] 移除删除和刷新按钮，它们将移至右键菜单
        
        left_layout.addWidget(self.wordlist_widget, 1)
        left_layout.addWidget(import_btn)

        # --- 右侧面板 ---
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        
        self.preview_stack = QStackedWidget()
        
        # 预览面板1: 标准词表
        self.standard_preview_widget = QTableWidget()
        self.standard_preview_widget.setEditTriggers(QTableWidget.NoEditTriggers)
        self.standard_preview_widget.setAlternatingRowColors(True)

        # 预览面板2: 图文词表 ([核心重构] UI)
        self.visual_preview_widget = QWidget()
        visual_layout = QVBoxLayout(self.visual_preview_widget)
        self.visual_image_label = ScalableImageLabel("图片预览区")

        # 使用 QFormLayout 展示结构化信息
        self.visual_info_group = QGroupBox("条目信息")
        info_layout = QFormLayout(self.visual_info_group)
        self.info_id_label = QLabel()
        self.info_prompt_label = QLabel()
        self.info_prompt_label.setWordWrap(True)
        self.info_notes_browser = QTextBrowser() # 使用 QTextBrowser 显示备注
        self.info_notes_browser.setReadOnly(True)
        self.info_notes_browser.setFixedHeight(100) # 给一个固定高度
        info_layout.addRow("<b>ID:</b>", self.info_id_label)
        info_layout.addRow("<b>提示文字:</b>", self.info_prompt_label)
        info_layout.addRow("<b>备注:</b>", self.info_notes_browser)
        
        self.visual_nav_bar = QWidget()
        visual_nav_layout = QHBoxLayout(self.visual_nav_bar)
        self.visual_prev_btn = QPushButton("上一个")
        self.visual_next_btn = QPushButton("下一个")
        self.visual_progress_label = QLabel("条目 0 / 0")
        visual_nav_layout.addWidget(self.visual_prev_btn)
        visual_nav_layout.addStretch()
        visual_nav_layout.addWidget(self.visual_progress_label)
        visual_nav_layout.addStretch()
        visual_nav_layout.addWidget(self.visual_next_btn)
        
        visual_layout.addWidget(self.visual_image_label, 1)
        visual_layout.addWidget(self.visual_info_group)
        visual_layout.addWidget(self.visual_nav_bar)

        self.preview_stack.addWidget(self.standard_preview_widget)
        self.preview_stack.addWidget(self.visual_preview_widget)
        
        right_layout.addWidget(QLabel("内容预览:"))
        right_layout.addWidget(self.preview_stack, 1)
        
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        
        main_layout.addWidget(splitter)
        
        import_btn.clicked.connect(self.import_wordlist)

    def _connect_signals(self):
        self.wordlist_widget.currentItemChanged.connect(self.on_selection_changed)
        self.visual_prev_btn.clicked.connect(lambda: self.navigate_visual_preview(-1))
        self.visual_next_btn.clicked.connect(lambda: self.navigate_visual_preview(1))
        # [新增] 连接右键菜单信号
        self.wordlist_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.wordlist_widget.customContextMenuRequested.connect(self.show_wordlist_context_menu)

    def _add_shortcuts(self):
        # [新增] 添加快捷键
        QShortcut(QKeySequence(Qt.Key_Left), self, self.visual_prev_btn.click)
        QShortcut(QKeySequence(Qt.Key_Right), self, self.visual_next_btn.click)

    def show_wordlist_context_menu(self, position):
        # [新增] 右键菜单逻辑
        item = self.wordlist_widget.itemAt(position)
        if not item:
            return

        menu = QMenu(self)
        
        show_action = menu.addAction(self.icon_manager.get_icon("open_folder"), "在文件浏览器中显示")
        menu.addSeparator()
        duplicate_action = menu.addAction(self.icon_manager.get_icon("copy"), "创建副本")
        delete_action = menu.addAction(self.icon_manager.get_icon("delete"), "删除")
        menu.addSeparator()
        refresh_action = menu.addAction(self.icon_manager.get_icon("refresh"), "刷新列表")
        
        action = menu.exec_(self.wordlist_widget.mapToGlobal(position))
        
        if action == show_action:
            self._show_in_explorer(item)
        elif action == duplicate_action:
            self._duplicate_wordlist(item)
        elif action == delete_action:
            self._delete_wordlist(item)
        elif action == refresh_action:
            self.populate_wordlist()

    def _show_in_explorer(self, item):
        # [新增] 定位文件逻辑
        _, filepath = item.data(Qt.UserRole)
        if not os.path.exists(filepath):
            QMessageBox.warning(self, "文件不存在", "该文件可能已被移动或删除。")
            self.populate_wordlist()
            return
        
        try:
            if sys.platform == 'win32':
                subprocess.run(['explorer', '/select,', os.path.normpath(filepath)])
            elif sys.platform == 'darwin':
                subprocess.run(['open', '-R', filepath])
            else: # Linux
                subprocess.run(['xdg-open', os.path.dirname(filepath)])
        except Exception as e:
            QMessageBox.critical(self, "操作失败", f"无法打开文件所在位置: {e}")

    def _duplicate_wordlist(self, item):
        # [新增] 复制文件逻辑
        _, src_path = item.data(Qt.UserRole)
        if not os.path.exists(src_path):
            QMessageBox.warning(self, "文件不存在", "无法创建副本，源文件可能已被移动或删除。")
            self.populate_wordlist()
            return

        base, ext = os.path.splitext(os.path.basename(src_path))
        dest_path = os.path.join(os.path.dirname(src_path), f"{base}_copy{ext}")
        i = 1
        while os.path.exists(dest_path):
            dest_path = os.path.join(os.path.dirname(src_path), f"{base}_copy_{i}{ext}")
            i += 1
        
        try:
            shutil.copy2(src_path, dest_path)
            # 如果是图文词表，也复制图片文件夹
            if "[图文]" in item.text():
                img_src_dir = os.path.join(os.path.dirname(src_path), base)
                if os.path.isdir(img_src_dir):
                    img_dest_dir = os.path.join(os.path.dirname(dest_path), os.path.splitext(os.path.basename(dest_path))[0])
                    shutil.copytree(img_src_dir, img_dest_dir)

            self.populate_wordlist()
        except Exception as e:
            QMessageBox.critical(self, "操作失败", f"无法创建副本: {e}")

    def _delete_wordlist(self, item):
        # [修改] 复用已有的删除逻辑
        self.delete_wordlist(item_to_delete=item)

    # ... (populate_wordlist, on_selection_changed, _populate_standard_preview, _populate_visual_preview 保持不变) ...
    def populate_wordlist(self):
        self.wordlist_widget.clear()
        for dir_path, prefix in [(self.COMMON_DIR, "[标准]"), (self.VISUAL_DIR, "[图文]")]:
            if os.path.exists(dir_path):
                for filename in sorted(os.listdir(dir_path)):
                    if filename.endswith(".json"):
                        item = QListWidgetItem(f"{prefix} {filename}")
                        item.setData(Qt.UserRole, (prefix, os.path.join(dir_path, filename)))
                        self.wordlist_widget.addItem(item)
        if self.wordlist_widget.count() > 0: self.wordlist_widget.setCurrentRow(0)

    def on_selection_changed(self, current, previous):
        if not current: return
        list_type, filepath = current.data(Qt.UserRole)
        if list_type == "[标准]": self.preview_stack.setCurrentWidget(self.standard_preview_widget); self._populate_standard_preview(filepath)
        elif list_type == "[图文]": self.preview_stack.setCurrentWidget(self.visual_preview_widget); self._populate_visual_preview(filepath)

    def _populate_standard_preview(self, filepath):
        self.standard_preview_widget.clear()
        try:
            with open(filepath, 'r', encoding='utf-8') as f: data = json.load(f).get('groups', [])
            self.standard_preview_widget.setColumnCount(4); self.standard_preview_widget.setHorizontalHeaderLabels(["组别", "单词/短语", "备注", "语言"])
            row_count = sum(len(group.get('items', [])) for group in data); self.standard_preview_widget.setRowCount(row_count)
            current_row = 0
            for group in data:
                group_id = str(group.get('id', ''))
                for item in group.get('items', []):
                    self.standard_preview_widget.setItem(current_row, 0, QTableWidgetItem(group_id)); self.standard_preview_widget.setItem(current_row, 1, QTableWidgetItem(item.get('text', '')))
                    self.standard_preview_widget.setItem(current_row, 2, QTableWidgetItem(item.get('note', ''))); self.standard_preview_widget.setItem(current_row, 3, QTableWidgetItem(item.get('lang', '')))
                    current_row += 1
            self.standard_preview_widget.resizeColumnsToContents(); self.standard_preview_widget.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        except Exception as e: QMessageBox.critical(self, "预览失败", f"无法解析标准词表文件:\n{e}")

    def _populate_visual_preview(self, filepath):
        self.visual_preview_data.clear(); self.visual_preview_index = -1
        try:
            with open(filepath, 'r', encoding='utf-8') as f: self.visual_preview_data = json.load(f).get('items', [])
            if self.visual_preview_data: self.visual_preview_index = 0; self.update_visual_preview_display()
            else: self.visual_image_label.set_pixmap(None); self.info_id_label.setText(""); self.info_prompt_label.setText("此图文词表为空。"); self.info_notes_browser.setText(""); self.visual_progress_label.setText("条目 0 / 0")
        except Exception as e: QMessageBox.critical(self, "预览失败", f"无法解析图文词表文件:\n{e}")
        
    def update_visual_preview_display(self):
        # [核心重构] 更新此方法以填充新的UI控件
        if not (0 <= self.visual_preview_index < len(self.visual_preview_data)):
            return
            
        item = self.visual_preview_data[self.visual_preview_index]
        
        img_path = item.get('image_path', '')
        base_dir = os.path.dirname(self.wordlist_widget.currentItem().data(Qt.UserRole)[1])
        full_img_path = os.path.join(base_dir, img_path)
        pixmap = QPixmap(full_img_path)
        if pixmap.isNull():
            self.visual_image_label.set_pixmap(None)
            self.visual_image_label.setText(f"图片未找到:\n{img_path}")
        else:
            self.visual_image_label.set_pixmap(pixmap)
            
        # 更新文本信息
        self.info_id_label.setText(item.get('id', 'N/A'))
        self.info_prompt_label.setText(item.get('prompt_text', 'N/A'))
        self.info_notes_browser.setText(item.get('notes', 'N/A'))
        
        self.visual_progress_label.setText(f"条目 {self.visual_preview_index + 1} / {len(self.visual_preview_data)}")

    def navigate_visual_preview(self, direction):
        if not self.visual_preview_data: return
        new_index = self.visual_preview_index + direction
        if 0 <= new_index < len(self.visual_preview_data): self.visual_preview_index = new_index; self.update_visual_preview_display()

    # ... (import_wordlist 保持不变) ...
    def import_wordlist(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "选择要导入的词表文件", self.SOURCE_STD_DIR, "JSON 词表文件 (*.json)")
        if not filepath: return
        try:
            with open(filepath, 'r', encoding='utf-8') as f: data = json.load(f)
            file_format = data.get("meta", {}).get("format")
            if file_format == "standard_wordlist": target_dir = self.COMMON_DIR
            elif file_format == "visual_wordlist": target_dir = self.VISUAL_DIR
            else: QMessageBox.warning(self, "格式不支持", "选择的文件不是有效的标准词表或图文词表。"); return
            shutil.copy2(filepath, target_dir)
            if file_format == "visual_wordlist":
                source_basename = os.path.splitext(os.path.basename(filepath))[0]; source_img_dir = os.path.join(os.path.dirname(filepath), source_basename)
                if os.path.isdir(source_img_dir):
                    target_img_dir = os.path.join(target_dir, source_basename)
                    if os.path.exists(target_img_dir): QMessageBox.information(self, "提示", f"图片文件夹 '{source_basename}' 已存在于目标位置，未重复复制。")
                    else: shutil.copytree(source_img_dir, target_img_dir)
            QMessageBox.information(self, "导入成功", f"词表 '{os.path.basename(filepath)}' 已成功导入到速记卡模块。"); self.populate_wordlist()
        except Exception as e: QMessageBox.critical(self, "导入失败", f"导入文件时发生错误:\n{e}")

    def delete_wordlist(self, item_to_delete=None):
        # [修改] 使其可以接收一个item作为参数
        if item_to_delete:
            current_item = item_to_delete
        else:
            current_item = self.wordlist_widget.currentItem()
        
        if not current_item:
            QMessageBox.warning(self, "未选择", "请先在列表中选择一个要删除的词表。")
            return
            
        list_type, filepath = current_item.data(Qt.UserRole)
        
        reply = QMessageBox.question(self, "确认删除", f"您确定要永久删除词表 '{os.path.basename(filepath)}' 吗？\n\n{'如果这是图文词表，其关联的图片文件夹也会被一并删除。\n' if list_type == '[图文]' else ''}此操作不可撤销！", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            try:
                os.remove(filepath)
                if list_type == '[图文]':
                    basename = os.path.splitext(os.path.basename(filepath))[0]
                    img_dir_path = os.path.join(os.path.dirname(filepath), basename)
                    if os.path.isdir(img_dir_path): shutil.rmtree(img_dir_path)
                self.populate_wordlist()
            except Exception as e:
                QMessageBox.critical(self, "删除失败", f"删除文件时出错:\n{e}")