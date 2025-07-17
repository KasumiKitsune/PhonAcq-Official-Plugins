# --- START OF FILE plugins/wordlist_editor_plus/editor_plus.py (v2.1 - Polished UX & Major Refactor) ---

import os
import sys
import json
import shutil
from functools import partial

from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QListWidget, QListWidgetItem, QStackedWidget, QTreeWidget,
                             QTreeWidgetItem, QLineEdit, QTextEdit, QGroupBox,
                             QMessageBox, QFileDialog, QSplitter, QLabel,
                             QTableWidget, QTableWidgetItem, QHeaderView, QApplication,
                             QMenu, QStyledItemDelegate)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon, QPixmap, QFontMetrics, QTextOption

# --- 插件自给自足的路径发现逻辑 ---
def _get_project_root():
    if getattr(sys, 'frozen', False): return os.path.dirname(sys.executable)
    else: return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

BASE_PATH = _get_project_root()
WORD_LIST_DIR = os.path.join(BASE_PATH, "word_lists")
DIALECT_VISUAL_WORDLIST_DIR = os.path.join(BASE_PATH, "dialect_visual_wordlists")

try:
    from plugin_system import BasePlugin
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

# ==============================================================================
# [v2.1新增] 自定义项委托，修复编辑时文本裁切问题
# ==============================================================================
class AutoResizingTextDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        # [核心修复 2] 如果是组行（顶层项）并且不是第一列，则不创建编辑器
        is_top_level = not index.parent().isValid()
        if is_top_level and index.column() > 0:
            return None # 返回None可有效禁止编辑

        # 对于其他可编辑单元格，创建编辑器
        editor = QTextEdit(parent)
        editor.setWordWrapMode(QTextOption.WordWrap)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        app_palette = QApplication.instance().palette()
        bg_color = app_palette.color(app_palette.Base)
        border_color = app_palette.color(app_palette.Highlight)

        editor.setStyleSheet(f"""
            QTextEdit {{
                background-color: {bg_color.name()};
                border: 1px solid {border_color.name()};
                border-radius: 2px;
                padding: 2px;
            }}
        """)
        
        return editor

    # ... (该类的其他方法 setEditorData, setModelData, updateEditorGeometry 保持不变) ...
    def setEditorData(self, editor, index):
        value = index.model().data(index, Qt.EditRole)
        editor.setText(value)

    def setModelData(self, editor, model, index):
        value = editor.toPlainText()
        model.setData(index, value, Qt.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        doc_height = editor.document().size().height()
        min_height = QFontMetrics(editor.font()).height() + 12
        editor.setFixedHeight(max(int(doc_height) + 60, min_height))
        editor.setGeometry(option.rect)
# ==============================================================================
# [v2.1修改] 自定义拖拽目标标签，不再处理拖拽事件
# ==============================================================================
class DropLabel(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setToolTip("拖拽图片到此区域，或双击选择图片文件。")
        self.setStyleSheet("""
            QLabel {
                border: 2px dashed #aaa; border-radius: 8px; color: #888;
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fdfdfd, stop:1 #f0f0f0);
            }
            QLabel[drag-over="true"] {
                border-color: #007AFF; background-color: #e6f2ff;
            }
        """)
        self.setProperty("drag-over", False)

    def setDragOver(self, over):
        self.setProperty("drag-over", over)
        self.style().unpolish(self)
        self.style().polish(self)

# ==============================================================================
# 插件主类 (Plugin Entry Point)
# ==============================================================================
class EditorPlusPlugin(BasePlugin):
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.editor_dialog = None
    def setup(self): return True
    def teardown(self):
        if self.editor_dialog: self.editor_dialog.close()
    def execute(self, **kwargs):
        if self.editor_dialog is None:
            self.editor_dialog = EditorPlusDialog(self.main_window)
            self.editor_dialog.finished.connect(self._on_dialog_finished)
        self.editor_dialog.show()
        self.editor_dialog.raise_()
        self.editor_dialog.activateWindow()
    def _on_dialog_finished(self): self.editor_dialog = None

# ==============================================================================
# 编辑器主对话框 (v2.1)
# ==============================================================================
class EditorPlusDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self.icon_manager = self.main_window.icon_manager
        self.current_data = None; self.current_filepath = None; self.is_dirty = False
        self.last_drag_row = -1
        self.setWindowTitle("词表编辑器 Plus v2.1")
        self.setMinimumSize(1200, 800)
        self._init_ui()
        self._connect_signals()
        self.populate_file_list()
        self.setAcceptDrops(True)

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)

        left_panel = QWidget(); left_layout = QVBoxLayout(left_panel)
        left_panel.setFixedWidth(280)
        left_layout.addWidget(QLabel("词表文件:"))
        self.file_list = QListWidget(); self.file_list.setToolTip("单击可打开词表文件进行编辑。")
        left_layout.addWidget(self.file_list)
        new_btn = QPushButton("新建词表..."); new_btn.setIcon(self.icon_manager.get_icon("new_file"))
        new_btn.clicked.connect(self.new_file)
        left_layout.addWidget(new_btn)

        right_panel = QWidget(); right_layout = QVBoxLayout(right_panel)
        self._create_metadata_editor()
        self._create_editor_stack()
        
        bottom_button_layout = QHBoxLayout()
        self.save_btn = QPushButton("保存"); self.save_btn.setIcon(self.icon_manager.get_icon("save"))
        self.save_as_btn = QPushButton("另存为..."); self.save_as_btn.setIcon(self.icon_manager.get_icon("save_as"))
        bottom_button_layout.addStretch()
        bottom_button_layout.addWidget(self.save_btn); bottom_button_layout.addWidget(self.save_as_btn)

        right_layout.addWidget(self.meta_group)
        right_layout.addWidget(self.editor_stack, 1)
        right_layout.addLayout(bottom_button_layout)

        splitter.addWidget(left_panel); splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0); splitter.setStretchFactor(1, 1)
        main_layout.addWidget(splitter)
        self.update_ui_state(is_loaded=False)

    def _create_metadata_editor(self):
        self.meta_group = QGroupBox("元数据 (Meta)")
        layout = QVBoxLayout(self.meta_group)
        self.meta_table = QTableWidget(); self.meta_table.setColumnCount(2)
        self.meta_table.setHorizontalHeaderLabels(["键 (Key)", "值 (Value)"]); self.meta_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.meta_table.setToolTip("编辑词表的元信息。\n核心的 'format' 和 'version' 键不可删除。")
        
        button_layout = QHBoxLayout()
        add_meta_btn = QPushButton("添加元数据项"); add_meta_btn.setIcon(self.icon_manager.get_icon("add"))
        remove_meta_btn = QPushButton("移除选中项"); remove_meta_btn.setIcon(self.icon_manager.get_icon("delete"))
        button_layout.addStretch()
        button_layout.addWidget(add_meta_btn); button_layout.addWidget(remove_meta_btn)
        
        layout.addWidget(self.meta_table); layout.addLayout(button_layout)
        add_meta_btn.clicked.connect(self.add_meta_item); remove_meta_btn.clicked.connect(self.remove_meta_item)

    def _create_editor_stack(self):
        self.editor_stack = QStackedWidget()
        
        # --- 标准词表编辑器 ---
        page_std = QWidget()
        layout_std = QVBoxLayout(page_std)
        self.standard_tree = QTreeWidget()

        # [核心修复 1a] 调整表头顺序，将“语言”放在中间
        self.standard_tree.setHeaderLabels(["项目", "语言", "备注/IPA"])
        
        # [核心修复 1b] 根据新的顺序，简化并固化列宽设置
        header = self.standard_tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)      # 项目列：拉伸
        header.setSectionResizeMode(1, QHeaderView.Fixed)         # 语言列：固定宽度
        header.setSectionResizeMode(2, QHeaderView.Stretch)      # 备注/IPA列：拉伸
        self.standard_tree.setColumnWidth(1, 90) # 为语言列设置一个固定的窄宽度
        
        # [核心修复 1c] 移除之前复杂的信号连接，不再需要
        # if hasattr(self.standard_tree.header(), 'sectionMoved'):
        #     try: self.standard_tree.header().sectionMoved.disconnect()
        #     except TypeError: pass

        self.standard_tree.setItemDelegate(AutoResizingTextDelegate(self.standard_tree))
        self.standard_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        
        layout_std.addWidget(self.standard_tree)
        self.editor_stack.addWidget(page_std)
        
        # --- 图文词表编辑器 (无变化) ---
        page_vis = QWidget()
        # ... (后续图文词表部分的代码保持不变) ...
        layout_vis = QVBoxLayout(page_vis)
        self.visual_table = QTableWidget()
        self.visual_table.setColumnCount(4)
        self.visual_table.setHorizontalHeaderLabels(["图片预览/管理", "ID", "提示文字", "研究者备注"])
        header_vis = self.visual_table.horizontalHeader()
        header_vis.setSectionResizeMode(2, QHeaderView.Stretch)
        header_vis.setSectionResizeMode(3, QHeaderView.Stretch)
        self.visual_table.setColumnWidth(0, 160)
        self.visual_table.setColumnWidth(1, 180)
        layout_vis.addWidget(self.visual_table)
        self.editor_stack.addWidget(page_vis)

    def _setup_tree_header(self, logicalIndex=None, oldVisualIndex=None, newVisualIndex=None):
        """
        [v2.4新增] 动态设置标准词表树的表头列宽。
        此方法通过查找列名来应用设置，不受列顺序影响，并在列被拖动后自动调用。
        """
        header = self.standard_tree.header()
        for i in range(header.count()):
            # 通过模型获取列的文本，而不是视觉上的顺序
            header_text = header.model().headerData(i, Qt.Horizontal)
            
            if header_text == "语言":
                # 为“语言”列设置固定宽度
                header.setSectionResizeMode(i, QHeaderView.Fixed)
                header.resizeSection(i, 90) # 稍微加宽到90以容纳更长的语言代码
            elif header_text == "备注/IPA":
                # 为“备注/IPA”列设置拉伸
                header.setSectionResizeMode(i, QHeaderView.Stretch)
            elif header_text == "项目":
                 # 为“项目”列也设置拉伸
                header.setSectionResizeMode(i, QHeaderView.Stretch)

    def _connect_signals(self):
        # [v2.1修改] 单击加载
        self.file_list.currentItemChanged.connect(self.on_file_selected)
        self.save_btn.clicked.connect(self.save_file); self.save_as_btn.clicked.connect(lambda: self.save_file(save_as=True))
        self.meta_table.itemChanged.connect(self.mark_as_dirty)
        # [v2.1新增] 右键菜单和双击选择图片
        self.standard_tree.customContextMenuRequested.connect(self.show_standard_tree_context_menu)
        self.standard_tree.itemChanged.connect(self.mark_as_dirty)
        self.visual_table.cellDoubleClicked.connect(self.on_visual_table_cell_double_clicked)
        self.visual_table.itemChanged.connect(self.mark_as_dirty)
        # [v2.1修复] 拖拽事件在QTableWidget上处理
        self.visual_table.setAcceptDrops(True)
        self.visual_table.dragEnterEvent = self.table_drag_enter_event
        self.visual_table.dragMoveEvent = self.table_drag_move_event
        self.visual_table.dragLeaveEvent = self.table_drag_leave_event
        self.visual_table.dropEvent = self.table_drop_event
    
    # ... 此处省略大量未修改的方法 ...
    # populate_file_list, on_file_selected, populate_editors, populate_metadata_table,
    # populate_standard_editor, populate_visual_editor, sync_data_from_ui, save_file
    # 这些方法的逻辑基本不变，为了简洁起见，不在此处重复，请保留您文件中的版本。
    # 我将只展示被修改和新增的方法。
    
    def populate_file_list(self):
        self.file_list.clear()
        for dir_path, type_icon in [(WORD_LIST_DIR, "list"), (DIALECT_VISUAL_WORDLIST_DIR, "image_gallery")]:
            if os.path.exists(dir_path):
                for filename in sorted(os.listdir(dir_path)):
                    if filename.endswith('.json'):
                        item = QListWidgetItem(self.icon_manager.get_icon(type_icon), filename)
                        item.setData(Qt.UserRole, os.path.join(dir_path, filename))
                        self.file_list.addItem(item)
    
    def on_file_selected(self, current, previous):
        if not current: return
        if self.check_unsaved_changes():
            filepath = current.data(Qt.UserRole)
            try:
                with open(filepath, 'r', encoding='utf-8') as f: self.current_data = json.load(f)
                self.current_filepath = filepath
                self.populate_editors()
                self.update_ui_state(is_loaded=True)
                self.is_dirty = False
            except Exception as e:
                QMessageBox.critical(self, "加载失败", f"无法加载或解析文件:\n{e}")
                self.clear_editors(); self.update_ui_state(is_loaded=False)

    def populate_editors(self):
        self.clear_editors()
        if not self.current_data: return
        self.populate_metadata_table()
        file_format = self.current_data.get('meta', {}).get('format', '')
        if file_format == 'standard_wordlist':
            self.editor_stack.setCurrentIndex(0); self.populate_standard_editor()
        elif file_format == 'visual_wordlist':
            self.editor_stack.setCurrentIndex(1); self.populate_visual_editor()
        else: QMessageBox.warning(self, "格式未知", f"不支持的词表格式: '{file_format}'")

    def populate_metadata_table(self):
        self.meta_table.setRowCount(0)
        meta = self.current_data.get('meta', {})
        for key, value in meta.items():
            row = self.meta_table.rowCount()
            self.meta_table.insertRow(row)
            key_item = QTableWidgetItem(key); val_item = QTableWidgetItem(str(value))
            if key in ['format', 'version']: key_item.setFlags(key_item.flags() & ~Qt.ItemIsEditable)
            self.meta_table.setItem(row, 0, key_item); self.meta_table.setItem(row, 1, val_item)
        
    def populate_standard_editor(self):
        self.standard_tree.clear()
        self.standard_tree.itemChanged.disconnect(self.mark_as_dirty)
        for group in self.current_data.get('groups', []):
            group_item = QTreeWidgetItem(self.standard_tree, [group.get('name', f"组 {group.get('id', '')}")])
            group_item.setFlags(group_item.flags() | Qt.ItemIsEditable)
            for item in group.get('items', []):
                # [核心修复] 按新的列顺序填充数据: 项目, 语言, 备注/IPA
                word_item = QTreeWidgetItem(group_item, [
                    item.get('text', ''), 
                    item.get('lang', ''),
                    item.get('note', '')
                ])
                word_item.setFlags(word_item.flags() | Qt.ItemIsEditable)
        self.standard_tree.expandAll()
        self.standard_tree.itemChanged.connect(self.mark_as_dirty)

    def populate_visual_editor(self):
        self.visual_table.setRowCount(0)
        items = self.current_data.get('items', [])
        self.visual_table.setRowCount(len(items))
        wordlist_dir = os.path.dirname(self.current_filepath)
        for i, item_data in enumerate(items):
            full_image_path = os.path.join(wordlist_dir, item_data.get('image_path', '')) if item_data.get('image_path') else ''
            thumb_label = DropLabel("拖拽/双击添加图片")
            if os.path.exists(full_image_path):
                pixmap = QPixmap(full_image_path)
                if not pixmap.isNull():
                    thumb_label.setPixmap(pixmap.scaled(150, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    thumb_label.setStyleSheet("border: 1px solid #ddd; border-radius: 4px;")
            self.visual_table.setCellWidget(i, 0, thumb_label)
            self.visual_table.setItem(i, 1, QTableWidgetItem(item_data.get('id', ''))); self.visual_table.setItem(i, 2, QTableWidgetItem(item_data.get('prompt_text', ''))); self.visual_table.setItem(i, 3, QTableWidgetItem(item_data.get('notes', '')))
        self.visual_table.resizeRowsToContents()
        
    def sync_data_from_ui(self):
        if not self.current_data: return
        # ... (元数据同步部分保持不变) ...
        new_meta = {}
        for i in range(self.meta_table.rowCount()):
            key_item = self.meta_table.item(i, 0); val_item = self.meta_table.item(i, 1)
            if key_item and val_item and key_item.text(): new_meta[key_item.text()] = val_item.text()
        self.current_data['meta'] = new_meta

        file_format = self.current_data.get('meta', {}).get('format', '')
        if file_format == 'standard_wordlist':
            new_groups = []
            for i in range(self.standard_tree.topLevelItemCount()):
                group_item = self.standard_tree.topLevelItem(i)
                group_data = {'id': i + 1, 'name': group_item.text(0), 'items': []}
                for j in range(group_item.childCount()):
                    word_item = group_item.child(j)
                    # [核心修复] 按新的列顺序读取数据，并按JSON标准格式写回
                    item_data = {
                        'text': word_item.text(0),
                        'lang': word_item.text(1),
                        'note': word_item.text(2)
                    }
                    group_data['items'].append(item_data)
                new_groups.append(group_data)
            self.current_data['groups'] = new_groups
        elif file_format == 'visual_wordlist':
            # ... (图文词表同步部分保持不变) ...
            for i in range(self.visual_table.rowCount()):
                id_item = self.visual_table.item(i, 1); prompt_item = self.visual_table.item(i, 2); notes_item = self.visual_table.item(i, 3)
                if id_item: self.current_data['items'][i]['id'] = id_item.text()
                if prompt_item: self.current_data['items'][i]['prompt_text'] = prompt_item.text()
                if notes_item: self.current_data['items'][i]['notes'] = notes_item.text()

    def save_file(self, save_as=False):
        if save_as or not self.current_filepath:
            filepath, _ = QFileDialog.getSaveFileName(self, "另存为", WORD_LIST_DIR, "JSON 文件 (*.json)")
            if not filepath: return
            self.current_filepath = filepath
        try:
            self.sync_data_from_ui()
            with open(self.current_filepath, 'w', encoding='utf-8') as f: json.dump(self.current_data, f, indent=4, ensure_ascii=False)
            self.is_dirty = False
            self.update_ui_state(is_loaded=True)
            QMessageBox.information(self, "成功", "文件已成功保存！")
            self.populate_file_list()
        except Exception as e: QMessageBox.critical(self, "保存失败", f"无法保存文件:\n{e}")

    # --- [v2.1] 右键菜单逻辑 ---
    def show_standard_tree_context_menu(self, position):
        item = self.standard_tree.itemAt(position)
        if not item: return
        
        # [v2.1修复] 应用主程序样式
        menu = QMenu()
        menu.setStyleSheet(self.main_window.styleSheet())
        
        move_up_action = menu.addAction(self.icon_manager.get_icon("move_up"), "上移")
        move_down_action = menu.addAction(self.icon_manager.get_icon("move_down"), "下移")
        menu.addSeparator()
        duplicate_action = menu.addAction(self.icon_manager.get_icon("copy"), "复制/粘贴")
        delete_action = menu.addAction(self.icon_manager.get_icon("delete"), "删除")
        
        move_up_action.triggered.connect(lambda: self.move_item(item, -1)); move_down_action.triggered.connect(lambda: self.move_item(item, 1))
        duplicate_action.triggered.connect(lambda: self.duplicate_item(item)); delete_action.triggered.connect(lambda: self.delete_item(item))
        menu.exec_(self.standard_tree.viewport().mapToGlobal(position))

    def move_item(self, item, direction):
        parent = item.parent()
        if parent:
            index = parent.indexOfChild(item); new_index = index + direction
            if 0 <= new_index < parent.childCount():
                child = parent.takeChild(index); parent.insertChild(new_index, child)
                self.standard_tree.setCurrentItem(child)
        else:
            index = self.standard_tree.indexOfTopLevelItem(item); new_index = index + direction
            if 0 <= new_index < self.standard_tree.topLevelItemCount():
                group = self.standard_tree.takeTopLevelItem(index); self.standard_tree.insertTopLevelItem(new_index, group)
                self.standard_tree.setCurrentItem(group)
        self.mark_as_dirty()

    def delete_item(self, item):
        parent = item.parent()
        if parent: parent.removeChild(item)
        else: self.standard_tree.takeTopLevelItem(self.standard_tree.indexOfTopLevelItem(item))
        self.mark_as_dirty()

    def duplicate_item(self, item):
        parent = item.parent(); new_item = QTreeWidgetItem()
        for i in range(item.columnCount()): new_item.setText(i, item.text(i))
        new_item.setFlags(item.flags())
        if parent: parent.insertChild(parent.indexOfChild(item) + 1, new_item)
        else: self.standard_tree.insertTopLevelItem(self.standard_tree.indexOfTopLevelItem(item) + 1, new_item)
        self.mark_as_dirty()

    # --- [v2.1修复 & 新增] 图片管理逻辑 ---
    def table_drag_enter_event(self, event):
        if event.mimeData().hasUrls() and len(event.mimeData().urls()) == 1:
            url = event.mimeData().urls()[0]
            if url.isLocalFile() and url.toLocalFile().lower().endswith(('.png', '.jpg', '.jpeg', '.bmp')):
                event.acceptProposedAction()
    
    def table_drag_move_event(self, event):
        row = self.visual_table.rowAt(event.pos().y()); col = self.visual_table.columnAt(event.pos().x())
        if self.last_drag_row != -1 and self.last_drag_row != row:
            label = self.visual_table.cellWidget(self.last_drag_row, 0)
            if isinstance(label, DropLabel): label.setDragOver(False)
        
        if row != -1 and col == 0:
            label = self.visual_table.cellWidget(row, 0)
            if isinstance(label, DropLabel): label.setDragOver(True)
            self.last_drag_row = row
        else:
            self.last_drag_row = -1
        event.accept()

    def table_drag_leave_event(self, event):
        if self.last_drag_row != -1:
            label = self.visual_table.cellWidget(self.last_drag_row, 0)
            if isinstance(label, DropLabel): label.setDragOver(False)
        self.last_drag_row = -1

    def table_drop_event(self, event):
        row = self.visual_table.rowAt(event.pos().y()); col = self.visual_table.columnAt(event.pos().x())
        if self.last_drag_row != -1: # Reset style
            label = self.visual_table.cellWidget(self.last_drag_row, 0)
            if isinstance(label, DropLabel): label.setDragOver(False)
        
        if row != -1 and col == 0:
            source_path = event.mimeData().urls()[0].toLocalFile()
            self._process_new_image_for_row(row, source_path)
            event.acceptProposedAction()

    def on_visual_table_cell_double_clicked(self, row, column):
        if column == 0:
            filepath, _ = QFileDialog.getOpenFileName(self, "选择图片", "", "图片文件 (*.png *.jpg *.jpeg *.bmp)")
            if filepath: self._process_new_image_for_row(row, filepath)

    def _process_new_image_for_row(self, row, source_path):
        item_id_widget = self.visual_table.item(row, 1)
        if not item_id_widget or not item_id_widget.text():
            QMessageBox.warning(self, "操作失败", "无法关联图片：该项目的 'ID' 不能为空。"); return
        item_id = item_id_widget.text()
        try:
            wordlist_dir = os.path.dirname(self.current_filepath)
            wordlist_name_no_ext = os.path.splitext(os.path.basename(self.current_filepath))[0]
            image_subfolder = os.path.join(wordlist_dir, wordlist_name_no_ext)
            os.makedirs(image_subfolder, exist_ok=True)
            _, ext = os.path.splitext(source_path)
            new_filename = f"{item_id}{ext}"
            dest_path = os.path.join(image_subfolder, new_filename)
            if os.path.exists(dest_path) and not os.path.samefile(source_path, dest_path):
                reply = QMessageBox.question(self, "文件已存在", f"文件 '{new_filename}' 已存在。是否覆盖？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if reply == QMessageBox.No: return
            shutil.copy2(source_path, dest_path)
            relative_path = os.path.join(wordlist_name_no_ext, new_filename).replace("\\", "/")
            self.current_data['items'][row]['image_path'] = relative_path
            self.update_thumbnail(row, dest_path); self.mark_as_dirty()
        except Exception as e: QMessageBox.critical(self, "图片处理失败", f"复制或重命名图片时出错:\n{e}")

    # --- [v2.1] UI状态和辅助方法 ---
    def update_thumbnail(self, row, image_path):
        cell_widget = self.visual_table.cellWidget(row, 0)
        if isinstance(cell_widget, QLabel):
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                cell_widget.setPixmap(pixmap.scaled(150, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                cell_widget.setStyleSheet("border: 1px solid #ddd; border-radius: 4px;")

    def add_meta_item(self):
        row = self.meta_table.rowCount(); self.meta_table.insertRow(row)
        self.meta_table.setItem(row, 0, QTableWidgetItem("新键")); self.meta_table.setItem(row, 1, QTableWidgetItem("新值")); self.mark_as_dirty()

    def remove_meta_item(self):
        current_row = self.meta_table.currentRow()
        if current_row != -1:
            key = self.meta_table.item(current_row, 0).text()
            if key in ['format', 'version']: QMessageBox.warning(self, "操作禁止", f"核心元数据键 '{key}' 不可删除。"); return
            self.meta_table.removeRow(current_row); self.mark_as_dirty()

    def mark_as_dirty(self, *args):
        if not self.is_dirty: self.is_dirty = True; self.update_ui_state(is_loaded=True)
    def update_ui_state(self, is_loaded):
        self.save_btn.setEnabled(is_loaded); self.save_as_btn.setEnabled(is_loaded)
        for editor in [self.meta_table, self.standard_tree, self.visual_table]: editor.setEnabled(is_loaded)
        if self.is_dirty: self.setWindowTitle(f"词表编辑器 Plus - {os.path.basename(self.current_filepath or '未命名')}*")
        elif self.current_filepath: self.setWindowTitle(f"词表编辑器 Plus - {os.path.basename(self.current_filepath)}")
        else: self.setWindowTitle("词表编辑器 Plus")
    def clear_editors(self):
        self.meta_table.setRowCount(0); self.standard_tree.clear(); self.visual_table.setRowCount(0)
    def check_unsaved_changes(self):
        if self.is_dirty:
            reply = QMessageBox.question(self, "未保存的更改", "您有未保存的更改。是否要先保存？", QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
            if reply == QMessageBox.Save: self.save_file(); return not self.is_dirty
            elif reply == QMessageBox.Cancel: return False
        return True
    def closeEvent(self, event):
        if self.check_unsaved_changes(): event.accept()
        else: event.ignore()
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            if event.mimeData().urls()[0].toLocalFile().lower().endswith('.json'): event.acceptProposedAction()
    def dropEvent(self, event):
        filepath = event.mimeData().urls()[0].toLocalFile()
        for i in range(self.file_list.count()):
            if self.file_list.item(i).data(Qt.UserRole) == filepath: self.file_list.setCurrentRow(i); break
    def new_file(self): pass # Placeholder for new file logic