# --- START OF FILE plugins/archive_manager/archive.py (v5.0 - Multi-Template Architecture) ---

import os
import sys
import json
import shutil
import subprocess
from datetime import datetime
from copy import deepcopy # [新增] 用于深度复制模板

try:
    import pandas as pd
except ImportError:
    print("警告：可选依赖库 pandas 未安装。CSV导出功能将不可用。请运行 'pip install pandas' 来启用。")
    pd = None

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QSplitter, QTextEdit, QFormLayout,
                             QMessageBox, QInputDialog, QFileDialog, QGroupBox, QWidget,
                             QStackedWidget, QComboBox, QMenu, QTableWidget, QTableWidgetItem,
                             QHeaderView, QAbstractItemView, QFrame, QScrollArea, QTabWidget, QDialogButtonBox,
                             QTreeWidget, QTreeWidgetItem, QGridLayout)
from PyQt5.QtCore import Qt, QSize, QEvent
from PyQt5.QtGui import QIcon

try:
    from modules.plugin_system import BasePlugin
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from modules.plugin_system import BasePlugin

# ==============================================================================
# 0. 可折叠框控件 (无变动)
# ==============================================================================
class CollapsibleBox(QWidget):
    def __init__(self, title="", parent=None):
        super(CollapsibleBox, self).__init__(parent)
        self.toggle_button = QPushButton(title)
        self.toggle_button.setStyleSheet("QPushButton { text-align: left; padding: 5px; border: 1px solid #ccc; background-color: #f0f0f0; font-weight: bold; }")
        self.toggle_button.setCheckable(True)
        
        self.content_area = QWidget()
        self.content_layout = QVBoxLayout(self.content_area)
        self.content_layout.setContentsMargins(5, 5, 5, 5)
        self.content_layout.setSpacing(5)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.toggle_button)
        main_layout.addWidget(self.content_area)

        self.toggle_button.clicked.connect(self._toggle)
        
        self.toggle_button.setChecked(True)
        self._set_arrow_icon()

    def _set_arrow_icon(self):
        arrow_char = "▼" if self.toggle_button.isChecked() else "►"
        current_text = self.toggle_button.text()
        clean_title = current_text.lstrip('▼► ').strip()
        self.toggle_button.setText(f"{arrow_char} {clean_title}")

    def _toggle(self):
        self.content_area.setHidden(not self.toggle_button.isChecked())
        self._set_arrow_icon()

    def setContentLayout(self, layout):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget(): item.widget().setParent(None)
            elif item.layout():
                self._clear_layout(item.layout())
                item.layout().deleteLater()
        self.content_layout.addLayout(layout)

    def _clear_layout(self, layout):
        if layout is None: return
        while layout.count():
            item = layout.takeAt(0); widget = item.widget()
            if widget: widget.setParent(None)
            else: self._clear_layout(item.layout())

    def toggle_collapsed(self, collapsed: bool):
        self.toggle_button.setChecked(not collapsed)
        self._toggle()

# ==============================================================================
# 1. 插件配置管理器 (升级支持 V2 Schema)
# ==============================================================================
class ArchiveConfigManager:
    def __init__(self, main_window):
        plugin_dir = os.path.dirname(__file__)
        self.config_path = os.path.join(plugin_dir, 'config.json')
        self.default_root = os.path.join(getattr(main_window, 'BASE_PATH', os.path.expanduser("~")), "PhonAcq_Archives")
        self.config = self._load()

    def _load(self):
        config = {}
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f: config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError): pass
        
        if "archive_root" not in config: config["archive_root"] = self.default_root
        
        # [核心修改] 检查并迁移旧的 V2 Schema 到新的 form_templates 结构
        if "participant_schema_v2" in config and "form_templates" not in config:
            print("正在从旧版 schema 迁移到多模板架构...")
            # 迁移时复制一份，避免影响原始数据，如果原始数据是共享的引用
            config["form_templates"] = { "默认模板": deepcopy(config["participant_schema_v2"]) }
            config.pop("participant_schema_v2") # 移除旧键
            self._save_config(config) # 立即保存迁移后的配置
        elif "form_templates" not in config:
            # 如果完全没有模板，创建默认模板
            config["form_templates"] = {
                "默认模板": [
                    {"group_name": "基本信息", "columns": 2, "collapsible": True, "fields": [{"key": "name", "label": "姓名/代号", "type": "LineEdit", "tooltip": "受试者的姓名或唯一标识代号。"}, {"key": "age", "label": "年龄", "type": "LineEdit", "tooltip": "受试者的年龄。"}, {"key": "gender", "label": "性别", "type": "ComboBox", "options": ["", "男", "女", "非二元性别", "倾向于不透露", "其他"], "tooltip": "受试者的生理性别或社会性别认同。"}, {"key": "education", "label": "受教育程度", "type": "LineEdit", "tooltip": "受试者的最高受教育水平。"}, {"key": "occupation", "label": "职业", "type": "LineEdit", "tooltip": "受试者的职业。"}, {"key": "tags", "label": "标签", "type": "LineEdit", "tooltip": "为受试者添加分类标签，多个标签请使用英文逗号 (,) 分隔。"},]},
                    {"group_name": "语言学背景", "columns": 2, "collapsible": True, "fields": [{"key": "native_language", "label": "母语", "type": "LineEdit", "tooltip": "受试者的母语。"}, {"key": "dialect", "label": "主要使用方言", "type": "LineEdit", "tooltip": "受试者当前主要使用的方言。"}, {"key": "other_languages", "label": "其他掌握语言", "type": "TextEdit", "tooltip": "列出受试者掌握的其他语言及其熟练程度。"}, {"key": "language_acquisition_environment", "label": "语言习得环境", "type": "TextEdit", "tooltip": "描述受试者在成长过程中的主要语言环境。"},]},
                    {"group_name": "其他信息", "columns": 1, "collapsible": True, "fields": [{"key": "health_notes", "label": "健康状况备注", "type": "TextEdit", "tooltip": "记录与研究相关的任何健康状况，如听力、视力等。"}, {"key": "general_notes", "label": "综合备注", "type": "TextEdit", "tooltip": "记录其他任何与该受试者相关的信息。"},]},
                ]
            }
            self._save_config(config)
        return config

    def _save_config(self, config_data):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4, ensure_ascii=False)
        except IOError as e: print(f"[Archive Plugin] 无法保存配置文件: {e}")

    def save(self): self._save_config(self.config)
    def get_archive_root(self): return self.config.get("archive_root", self.default_root)
    def set_archive_root(self, path): self.config["archive_root"] = path

    # [新增] 模板管理方法
    def get_template_names(self): return sorted(list(self.config.get("form_templates", {}).keys()))
    def get_template_schema(self, template_name): return self.config.get("form_templates", {}).get(template_name, [])
    def save_template_schema(self, template_name, schema): self.config.setdefault("form_templates", {})[template_name] = schema; self.save()
    def delete_template(self, template_name):
        if template_name in self.config.get("form_templates", {}):
            if len(self.get_template_names()) <= 1: return False, "不能删除最后一个模板。"
            del self.config["form_templates"][template_name]
            self.save()
            return True, None
        return False, "模板不存在。"
    def rename_template(self, old_name, new_name):
        if old_name in self.config.get("form_templates", {}) and new_name not in self.config.get("form_templates", {}):
            self.config["form_templates"][new_name] = self.config["form_templates"].pop(old_name)
            self.save()
            return True, None
        return False, "旧模板名不存在或新模板名已存在。"

# ==============================================================================
# NewExperimentDialog (新建实验对话框)
# ==============================================================================
class NewExperimentDialog(QDialog):
    def __init__(self, templates, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建实验")
        layout = QFormLayout(self)
        self.name_edit = QLineEdit()
        self.template_combo = QComboBox()
        if templates:
            self.template_combo.addItems(templates)
        layout.addRow("实验名称:", self.name_edit)
        layout.addRow("选择表单模板:", self.template_combo)
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addRow(self.button_box)
    def get_data(self):
        return self.name_edit.text().strip(), self.template_combo.currentText()

# ==============================================================================
# 2. 数据处理逻辑层 (无功能变化)
# ==============================================================================
class ArchiveDataManager:
    def __init__(self, root_path):
        self.root_path = root_path
        self.trash_path = os.path.join(self.root_path, ".trash")
        os.makedirs(self.root_path, exist_ok=True)
        os.makedirs(self.trash_path, exist_ok=True)

    def _log_change(self, data, action, user="default_user"):
        if "changelog" not in data: data["changelog"] = []
        log_entry = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "user": user, "action": action}
        data["changelog"].insert(0, log_entry)
        return data

    def load_json(self, *path_parts):
        filepath = os.path.join(self.root_path, *path_parts)
        if not os.path.exists(filepath): return {}
        try:
            with open(filepath, 'r', encoding='utf-8') as f: return json.load(f)
        except (json.JSONDecodeError, IOError): return {}

    def save_json(self, data, path_parts, action_description):
        if len(path_parts) > 1 and path_parts[1] != "experiment.json":
            exp_data = self.load_json(path_parts[0], "experiment.json")
            if exp_data.get("is_locked", False): return False, "实验已被锁定，无法修改。"
        if action_description: data = self._log_change(data, action_description)
        filepath = os.path.join(self.root_path, *path_parts); os.makedirs(os.path.dirname(filepath), exist_ok=True)
        try:
            with open(filepath, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4, ensure_ascii=False)
            return True, None
        except IOError as e: return False, str(e)

    def get_experiments(self):
        try: return sorted([d for d in os.listdir(self.root_path) if os.path.isdir(os.path.join(self.root_path, d)) and not d.startswith('.')])
        except OSError: return []

    def get_participants(self, exp_name):
        exp_path = os.path.join(self.root_path, exp_name);
        if not os.path.isdir(exp_path): return []
        return sorted([f for f in os.listdir(exp_path) if f.startswith("participant_") and f.endswith(".json")])

    def suggest_participant_id(self, experiment_name):
        participants = self.get_participants(experiment_name);
        if not participants: return "p001"
        max_num = 0
        for p_file in participants:
            try: num = int(p_file[12:-5].replace('p', ''))
            except (ValueError, IndexError): continue
            if num > max_num: max_num = num
        return f"p{max_num + 1:03d}"

    def _move_to_trash(self, item_path, original_subpath):
        if not os.path.exists(item_path): return False, "项目不存在"
        trash_item_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{os.path.basename(item_path)}"; trash_dest = os.path.join(self.trash_path, trash_item_name)
        trash_info_file = f"{trash_dest}.trashinfo"
        try:
            shutil.move(item_path, trash_dest)
            trash_info = {"original_path": original_subpath.replace('\\', '/'), "deleted_by": "default_user"}
            with open(trash_info_file, 'w', encoding='utf-8') as f: json.dump(trash_info, f, indent=4)
            return True, None
        except Exception as e: return False, str(e)

    def delete_experiment(self, exp_name): return self._move_to_trash(os.path.join(self.root_path, exp_name), exp_name)
    def delete_participant(self, exp_name, part_filename): return self._move_to_trash(os.path.join(self.root_path, exp_name, part_filename), os.path.join(exp_name, part_filename))
    
    def get_trashed_items(self):
        items = {}
        for f in os.listdir(self.trash_path):
            if f.endswith(".trashinfo"):
                item_name = f[:-10]
                try:
                    with open(os.path.join(self.trash_path, f), 'r', encoding='utf-8') as info_f:
                        info = json.load(info_f)
                        info['type'] = '文件夹' if os.path.isdir(os.path.join(self.trash_path, item_name)) else '文件'
                        items[item_name] = info
                except: continue
        return items

    def restore_trashed_item(self, item_name):
        trash_item_path = os.path.join(self.trash_path, item_name); info_path = f"{trash_item_path}.trashinfo"
        if not os.path.exists(info_path): return False, "恢复信息丢失"
        with open(info_path, 'r', encoding='utf-8') as f: info = json.load(f)
        dest_path = os.path.join(self.root_path, info['original_path'])
        if os.path.exists(dest_path): return False, "原始位置已存在同名项目"
        try:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.move(trash_item_path, dest_path); os.remove(info_path)
            return True, None
        except Exception as e: return False, str(e)

    def purge_trashed_item(self, item_name):
        trash_item_path = os.path.join(self.trash_path, item_name); info_path = f"{trash_item_path}.trashinfo"
        try:
            if os.path.isdir(trash_item_path): shutil.rmtree(trash_item_path)
            elif os.path.isfile(trash_item_path): os.remove(trash_item_path)
            if os.path.exists(info_path): os.remove(info_path)
            return True, None
        except Exception as e: return False, str(e)

    def toggle_experiment_lock(self, exp_name):
        data = self.load_json(exp_name, "experiment.json"); current_state = data.get("is_locked", False)
        data["is_locked"] = not current_state; action = "锁定实验" if not current_state else "解锁实验"
        return self.save_json(data, (exp_name, "experiment.json"), action)

    def get_archive_summary(self):
        """获取档案库的统计摘要，并包含最近修改的详细信息。"""
        summary = {'exp_count': 0, 'part_count': 0, 'session_count': 0, 'recent_items': []}
        all_files = []
    
        # 遍历文件系统，收集所有 .json 文件
        for root, _, files in os.walk(self.root_path):
            if ".trash" in root.split(os.sep): # 排除回收站
                continue
            for name in files:
                if name.endswith(".json"):
                    all_files.append(os.path.join(root, name))
    
        # 按修改时间降序排序
        all_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    
        # [核心修改] 获取最近10个文件的详细信息
        for f_path in all_files[:10]:
            try:
                rel_path = os.path.relpath(f_path, self.root_path).replace('\\', '/')
                mod_time = datetime.fromtimestamp(os.path.getmtime(f_path)).strftime('%Y-%m-%d %H:%M')
            
                # 读取文件内容以获取最新的changelog
                with open(f_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            
                latest_log = data.get("changelog", [{}])[0] # 安全地获取第一条日志
                action_desc = latest_log.get("action", "未知操作")
            
                summary['recent_items'].append({
                    "path": rel_path,
                    "time": mod_time,
                    "action": action_desc
                })
            except (IOError, json.JSONDecodeError, IndexError):
                # 如果文件读取失败或changelog为空，则跳过
                continue

        # 统计总数（这部分逻辑不变）
        experiments = self.get_experiments()
        summary['exp_count'] = len(experiments)
        for exp in experiments:
            participants = self.get_participants(exp)
            summary['part_count'] += len(participants)
            for part_file in participants:
                data = self.load_json(exp, part_file)
                summary['session_count'] += len(data.get("sessions", []))
            
        return summary

    # [修改] 接受 export_keys_info 来动态确定导出列
    def export_participants_to_csv(self, experiment_name, file_path, export_keys_info):
        if not pd: return False, "Pandas 库未安装。请运行 'pip install pandas' 来启用此功能。"
        participants_filenames = self.get_participants(experiment_name); records = []
        for part_filename in participants_filenames:
            data = self.load_json(experiment_name, part_filename); record = {}
            for key_info in export_keys_info:
                key = key_info['key']; label = key_info['label']
                value = data.get(key, '');
                if isinstance(value, list): record[label] = ', '.join(value)
                else: record[label] = value
            records.append(record)
        if not records: return False, "没有受试者数据可导出。"
        df = pd.DataFrame(records)
        try:
            df.to_csv(file_path, index=False, encoding='utf_8_sig')
            return True, None
        except Exception as e: return False, str(e)

    def copy_participant_to_experiment(self, source_exp, part_filename, dest_exp):
        dest_data = self.load_json(dest_exp, "experiment.json")
        if dest_data.get("is_locked", False): return False, f"目标实验 '{dest_exp}' 已被锁定，无法复制档案。"
        source_path = os.path.join(self.root_path, source_exp, part_filename); dest_path = os.path.join(self.root_path, dest_exp, part_filename)
        if os.path.exists(dest_path): return False, f"目标实验 '{dest_exp}' 中已存在同名档案 '{part_filename}'。"
        try:
            shutil.copy2(source_path, dest_path)
            copied_data = self.load_json(dest_exp, part_filename); self.save_json(copied_data, (dest_exp, part_filename), f"从实验 '{source_exp}' 复制而来")
            return True, None
        except IOError as e: return False, str(e)

    def add_session_to_participant(self, experiment_name, participant_id, session_path):
        part_filename = f"participant_{participant_id}.json"; data = self.load_json(experiment_name, part_filename)
        if "sessions" not in data: data["sessions"] = []
        if any(s.get('path') == session_path for s in data["sessions"]): return False, "该数据文件夹已被关联到此受试者档案。"
        new_session = {"path": session_path, "date": datetime.now().strftime("%Y-%m-%d"), "task": "", "notes": "", "tags": []}
        data["sessions"].append(new_session)
        return self.save_json(data, (experiment_name, part_filename), f"添加新会话: {os.path.basename(session_path)}")

    def update_participant_session(self, exp_name, part_id, session_index, session_data):
        part_filename = f"participant_{part_id}.json"; data = self.load_json(exp_name, part_filename)
        if "sessions" in data and 0 <= session_index < len(data["sessions"]):
            data["sessions"][session_index].update(session_data); return self.save_json(data, (exp_name, part_filename), f"更新会话 #{session_index+1} 的信息")
        return False, "会话索引无效或受试者档案不存在。"

    def delete_participant_session(self, exp_name, part_id, session_index):
        part_filename = f"participant_{part_id}.json"; data = self.load_json(exp_name, part_filename)
        if "sessions" in data and 0 <= session_index < len(data["sessions"]):
            del data["sessions"][session_index]; return self.save_json(data, (exp_name, part_filename), f"删除会话 #{session_index+1}")
        return False, "会话索引无效或受试者档案不存在。"

    def rename_experiment(self, old_name, new_name):
        old_path = os.path.join(self.root_path, old_name); new_path = os.path.join(self.root_path, new_name)
        if not os.path.exists(old_path): return False, "原实验文件夹不存在。"
        if os.path.exists(new_path): return False, "新实验名称已存在。"
        try:
            shutil.move(old_path, new_path)
            exp_json_old_path = os.path.join(new_path, "experiment.json")
            if os.path.exists(exp_json_old_path):
                exp_data = self.load_json(new_name, "experiment.json"); self.save_json(exp_data, (new_name, "experiment.json"), f"实验从 '{old_name}' 重命名为 '{new_name}'")
            return True, None
        except Exception as e:
            if os.path.exists(new_path) and not os.path.exists(old_path):
                try: shutil.move(new_path, old_path)
                except Exception as rollback_e: print(f"重命名失败后回滚失败: {rollback_e}")
            return False, str(e)

# ==============================================================================
# GroupEditDialog (新增)
# ==============================================================================
class GroupEditDialog(QDialog):
    def __init__(self, group_data=None, parent=None):
        super().__init__(parent); self.setWindowTitle("编辑分组")
        self.group_data = group_data if group_data else {}
        self.layout = QFormLayout(self)
        self.name_edit = QLineEdit(self.group_data.get("group_name", ""))
        self.columns_combo = QComboBox(); self.columns_combo.addItems(["1 (单列)", "2 (双列)"])
        current_columns = self.group_data.get("columns", 1); index = self.columns_combo.findText(f'{current_columns} (', Qt.MatchStartsWith)
        if index != -1: self.columns_combo.setCurrentIndex(index)
        self.layout.addRow("分组名称:", self.name_edit); self.layout.addRow("布局列数:", self.columns_combo)
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel); self.button_box.accepted.connect(self.accept); self.button_box.rejected.connect(self.reject)
        self.layout.addRow(self.button_box)
    def get_data(self):
        new_data = {
            "group_name": self.name_edit.text().strip(),
            "columns": int(self.columns_combo.currentText().split(" ")[0]),
            "collapsible": self.group_data.get("collapsible", True),
            "fields": self.group_data.get("fields", [])
        }
        return new_data

# ==============================================================================
# FieldEditDialog (新增)
# ==============================================================================
class FieldEditDialog(QDialog):
    """一个用于添加或编辑字段的对话框。"""
    def __init__(self, field_data=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑字段")
        self.field_data = field_data if field_data else {}
        
        self.layout = QFormLayout(self)
        self.key_edit = QLineEdit()
        self.key_edit.setToolTip("内部使用的唯一英文键名，例如 'native_language'。")
        self.label_edit = QLineEdit()
        self.label_edit.setToolTip("显示给用户的标签名称，例如 '母语'。")
        
        self.type_combo = QComboBox()
        # [核心修改] 定义所有字段类型及其描述
        field_types = {
            "LineEdit": "单行文本框，适用于简短的文本输入。",
            "TextEdit": "多行文本框，适用于较长的段落或备注。",
            "ComboBox": "下拉选择框，用户只能从预设的选项中选择。",
            "DateEdit": "日期输入框，会自动格式化数字为 'YYYY-MM-DD' 格式。"
        }
        
        # [核心修改] 填充 ComboBox 并设置 Tooltip
        for name, tooltip in field_types.items():
            self.type_combo.addItem(name)
            # 为每个 item 设置对应的 tooltip
            self.type_combo.setItemData(self.type_combo.count() - 1, tooltip, Qt.ToolTipRole)

        self.options_edit = QLineEdit()
        self.options_edit.setToolTip("仅当类型为 'ComboBox' 时有效，选项用英文逗号 (,) 分隔。")
        self.tooltip_edit = QLineEdit()
        self.tooltip_edit.setToolTip("当鼠标悬停在生成的表单字段上时，显示的帮助提示文本。")

        self.layout.addRow("键名 (Key):", self.key_edit)
        self.layout.addRow("标签 (Label):", self.label_edit)
        self.layout.addRow("类型 (Type):", self.type_combo)
        self.layout.addRow("选项 (Options):", self.options_edit)
        self.layout.addRow("提示 (Tooltip):", self.tooltip_edit)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.layout.addRow(self.button_box)

        if self.field_data:
            self.key_edit.setText(self.field_data.get("key", ""))
            self.label_edit.setText(self.field_data.get("label", ""))
            self.type_combo.setCurrentText(self.field_data.get("type", "LineEdit"))
            self.options_edit.setText(", ".join(self.field_data.get("options", [])))
            self.tooltip_edit.setText(self.field_data.get("tooltip", ""))
            self.key_edit.setReadOnly(True)

    def get_data(self):
        """获取用户输入的字段数据。"""
        data = {
            "key": self.key_edit.text().strip(),
            "label": self.label_edit.text().strip(),
            "type": self.type_combo.currentText(),
            "options": [opt.strip() for opt in self.options_edit.text().split(',') if opt.strip()],
            "tooltip": self.tooltip_edit.text().strip()
        }
        for k, v in self.field_data.items():
            if k not in data: data[k] = v
        return data

# ==============================================================================
# ArchiveSettingsDialog (重大修改)
# ==============================================================================
class ArchiveSettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent); self.parent_dialog = parent
        self.config_manager = parent.config_manager
        self.setWindowTitle("档案库设置"); self.setMinimumWidth(800)
        main_layout = QVBoxLayout(self); self.tabs = QTabWidget(); main_layout.addWidget(self.tabs)
        self.tabs.addTab(self._create_general_tab(), "常规"); self.tabs.addTab(self._create_schema_tab(), "表单模板")
        self.global_button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); self.global_button_box.accepted.connect(self.save_and_accept); self.global_button_box.rejected.connect(self.reject)
        main_layout.addWidget(self.global_button_box); self._load_settings()

    def _create_general_tab(self):
        widget = QWidget(); layout = QFormLayout(widget)
        self.root_path_edit = QLineEdit(); self.root_path_edit.setReadOnly(True)
        browse_btn = QPushButton("浏览..."); browse_btn.clicked.connect(self._browse_root_path)
        path_layout = QHBoxLayout(); path_layout.addWidget(self.root_path_edit); path_layout.addWidget(browse_btn)
        layout.addRow("档案库根目录:", path_layout)
        self.recycle_bin_btn = QPushButton("打开回收站..."); self.recycle_bin_btn.setIcon(self.parent_dialog.icon_manager.get_icon("delete"))
        self.recycle_bin_btn.clicked.connect(self._open_recycle_bin_from_settings)
        layout.addRow("数据管理:", self.recycle_bin_btn)
        return widget

    def _create_schema_tab(self):
        """创建受试者字段定义选项卡，使用 QTreeWidget。"""
        widget = QWidget()
        main_layout = QVBoxLayout(widget)
    
        # 模板管理部分
        template_group = QGroupBox("模板管理")
        template_layout = QHBoxLayout(template_group)
        template_layout.addWidget(QLabel("当前模板:"))
        self.template_selector_combo = QComboBox()
        self.template_selector_combo.setToolTip("选择要编辑的表单模板。")
        self.new_template_btn = QPushButton("新建...")
        self.rename_template_btn = QPushButton("重命名...")
        self.duplicate_template_btn = QPushButton("复制...")
        self.delete_template_btn = QPushButton("删除")
        template_layout.addWidget(self.template_selector_combo, 1)
        template_layout.addWidget(self.new_template_btn)
        template_layout.addWidget(self.rename_template_btn)
        template_layout.addWidget(self.duplicate_template_btn)
        template_layout.addWidget(self.delete_template_btn)
        main_layout.addWidget(template_group)

        # 字段编辑部分
        splitter = QSplitter(Qt.Horizontal)
    
        tree_widget = QWidget()
        tree_layout = QVBoxLayout(tree_widget)
        self.schema_tree = QTreeWidget()
        self.schema_tree.setHeaderLabels(["字段/分组", "类型"])
        self.schema_tree.setColumnWidth(0, 300)
    
        # [核心修改] 禁用双击展开/折叠功能
        self.schema_tree.setExpandsOnDoubleClick(False)
    
        self.schema_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.schema_tree.customContextMenuRequested.connect(self._show_schema_context_menu)
        self.schema_tree.itemDoubleClicked.connect(self._edit_item)
        tree_layout.addWidget(self.schema_tree)

        btn_layout = QVBoxLayout()
        self.add_group_btn = QPushButton("添加分组")
        self.add_field_btn = QPushButton("添加字段")
        self.edit_btn = QPushButton("编辑...")
        self.remove_btn = QPushButton("删除")
        self.up_btn = QPushButton("上移")
        self.down_btn = QPushButton("下移")
        btn_layout.addWidget(self.add_group_btn); btn_layout.addWidget(self.add_field_btn); btn_layout.addSpacing(20)
        btn_layout.addWidget(self.edit_btn); btn_layout.addWidget(self.remove_btn); btn_layout.addSpacing(20)
        btn_layout.addWidget(self.up_btn); btn_layout.addWidget(self.down_btn); btn_layout.addStretch()
    
        btn_widget = QWidget()
        btn_widget.setLayout(btn_layout)
    
        splitter.addWidget(tree_widget)
        splitter.addWidget(btn_widget)
        splitter.setSizes([600, 200])
        main_layout.addWidget(splitter, 1)

        # 连接信号
        self.template_selector_combo.currentIndexChanged.connect(self._on_template_selected)
        self.new_template_btn.clicked.connect(self._new_template)
        self.rename_template_btn.clicked.connect(self._rename_template)
        self.duplicate_template_btn.clicked.connect(self._duplicate_template)
        self.delete_template_btn.clicked.connect(self._delete_template)
    
        self.add_group_btn.clicked.connect(self._add_group)
        self.add_field_btn.clicked.connect(self._add_field)
        self.edit_btn.clicked.connect(self._edit_item)
        self.remove_btn.clicked.connect(self._remove_item)
        self.up_btn.clicked.connect(lambda: self._move_item(-1))
        self.down_btn.clicked.connect(lambda: self._move_item(1))
        self.schema_tree.currentItemChanged.connect(self._update_button_states)
    
        return widget
        
    def _load_settings(self):
        self.root_path_edit.setText(self.config_manager.get_archive_root())
        self.template_selector_combo.blockSignals(True)
        self.template_selector_combo.clear(); self.template_selector_combo.addItems(self.config_manager.get_template_names())
        self.template_selector_combo.blockSignals(False)
        self._on_template_selected()

    def _on_template_selected(self):
        template_name = self.template_selector_combo.currentText();
        if not template_name: self.schema_tree.clear(); return
        self.schema_tree.clear(); schema = self.config_manager.get_template_schema(template_name)
        for group_data in schema:
            group_item = QTreeWidgetItem(self.schema_tree); group_item.setText(0, f'{group_data["group_name"]}'); group_item.setText(1, f'{group_data["columns"]} 列布局')
            group_item.setData(0, Qt.UserRole, {"type": "group", "data": group_data})
            group_item.setFlags(group_item.flags() | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled) # Ensure draggable
            for field_data in group_data.get("fields", []):
                field_item = QTreeWidgetItem(group_item); field_item.setText(0, f'{field_data["label"]} ({field_data["key"]})'); field_item.setText(1, field_data["type"])
                field_item.setData(0, Qt.UserRole, {"type": "field", "data": field_data})
                field_item.setFlags(field_item.flags() | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled) # Ensure draggable
        self.schema_tree.expandAll(); self._update_button_states()

    def save_and_accept(self):
        current_template = self.template_selector_combo.currentText()
        if current_template:
            new_schema = []
            for i in range(self.schema_tree.topLevelItemCount()):
                group_item = self.schema_tree.topLevelItem(i); group_data = group_item.data(0, Qt.UserRole)["data"].copy(); group_data["fields"] = []
                for j in range(group_item.childCount()):
                    field_item = group_item.child(j); field_data = field_item.data(0, Qt.UserRole)["data"]; group_data["fields"].append(field_data)
                new_schema.append(group_data)
            self.config_manager.save_template_schema(current_template, new_schema)
        new_root = self.root_path_edit.text();
        if new_root != self.config_manager.get_archive_root(): self.config_manager.set_archive_root(new_root); self.parent_dialog.on_settings_changed()
        self.config_manager.save()
        self.parent_dialog.on_participant_schema_changed()
        self.accept()

    def _add_group(self):
        dialog = GroupEditDialog(parent=self);
        if dialog.exec_() == QDialog.Accepted:
            group_data = dialog.get_data()
            for i in range(self.schema_tree.topLevelItemCount()):
                item = self.schema_tree.topLevelItem(i); existing_group_data = item.data(0, Qt.UserRole)["data"]
                if existing_group_data["group_name"] == group_data["group_name"]:
                    QMessageBox.warning(self, "重复名称", f"分组名称 '{group_data['group_name']}' 已存在，请使用不同的名称。"); return
            group_item = QTreeWidgetItem(self.schema_tree); group_item.setText(0, f'{group_data["group_name"]}')
            group_item.setText(1, f'{group_data["columns"]} 列布局'); group_item.setData(0, Qt.UserRole, {"type": "group", "data": group_data})
            group_item.setFlags(group_item.flags() | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            self.schema_tree.setCurrentItem(group_item); self.schema_tree.expandItem(group_item)

    def _add_field(self):
        current_item = self.schema_tree.currentItem();
        if not current_item: QMessageBox.warning(self, "操作无效", "请先选择一个分组以添加字段。"); return
        parent_group_item = current_item if not current_item.parent() else current_item.parent()
        if not parent_group_item: return
        dialog = FieldEditDialog(parent=self);
        if dialog.exec_() == QDialog.Accepted:
            field_data = dialog.get_data();
            if not field_data['key']: QMessageBox.warning(self, "错误", "键名(Key)不能为空。"); return
            for i in range(parent_group_item.childCount()):
                child_item = parent_group_item.child(i); existing_field_data = child_item.data(0, Qt.UserRole)["data"]
                if existing_field_data["key"] == field_data["key"]:
                    QMessageBox.warning(self, "重复键名", f"键名 '{field_data['key']}' 在当前分组中已存在，请使用不同的键名。"); return
            field_item = QTreeWidgetItem(parent_group_item); field_item.setText(0, f'{field_data["label"]} ({field_data["key"]})')
            field_item.setText(1, field_data["type"]); field_item.setData(0, Qt.UserRole, {"type": "field", "data": field_data})
            field_item.setFlags(field_item.flags() | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            self.schema_tree.setCurrentItem(field_item); parent_group_item.setExpanded(True)

    def _edit_item(self):
        item = self.schema_tree.currentItem();
        if not item: return
        item_info = item.data(0, Qt.UserRole); item_type = item_info.get("type"); item_data = item_info.get("data")
        if item_type == "group":
            dialog = GroupEditDialog(item_data, self);
            if dialog.exec_() == QDialog.Accepted:
                new_data = dialog.get_data();
                for i in range(self.schema_tree.topLevelItemCount()):
                    existing_item = self.schema_tree.topLevelItem(i);
                    if existing_item is not item:
                        existing_group_data = existing_item.data(0, Qt.UserRole)["data"]
                        if existing_group_data["group_name"] == new_data["group_name"]:
                            QMessageBox.warning(self, "重复名称", f"分组名称 '{new_data['group_name']}' 已存在，请使用不同的名称。"); return
                item.setData(0, Qt.UserRole, {"type": "group", "data": new_data}); item.setText(0, f'{new_data["group_name"]}')
                item.setText(1, f'{new_data["columns"]} 列布局')
        elif item_type == "field":
            dialog = FieldEditDialog(item_data, self);
            if dialog.exec_() == QDialog.Accepted:
                new_data = dialog.get_data();
                parent_group_item = item.parent();
                if parent_group_item:
                    for i in range(parent_group_item.childCount()):
                        child_item = parent_group_item.child(i);
                        if child_item is not item:
                            existing_field_data = child_item.data(0, Qt.UserRole)["data"]
                            if existing_field_data["key"] == new_data["key"]:
                                QMessageBox.warning(self, "重复键名", f"键名 '{new_data['key']}' 在当前分组中已存在，请使用不同的键名。"); return
                item.setData(0, Qt.UserRole, {"type": "field", "data": new_data}); item.setText(0, f'{new_data["label"]} ({new_data["key"]})')
                item.setText(1, new_data["type"])

    def _show_schema_context_menu(self, position):
        item = self.schema_tree.itemAt(position);
        if not item: return
        menu = QMenu(self); icon_manager = self.parent_dialog.icon_manager
        action_edit = menu.addAction(icon_manager.get_icon("draw"), "编辑..."); action_edit.triggered.connect(self._edit_item); action_edit.setEnabled(self.edit_btn.isEnabled())
        action_remove = menu.addAction(icon_manager.get_icon("clear_contents"), "删除"); action_remove.triggered.connect(self._remove_item); action_remove.setEnabled(self.remove_btn.isEnabled())
        menu.addSeparator()
        action_up = menu.addAction(icon_manager.get_icon("move_up"), "上移"); action_up.triggered.connect(lambda: self._move_item(-1)); action_up.setEnabled(self.up_btn.isEnabled())
        action_down = menu.addAction(icon_manager.get_icon("move_down"), "下移"); action_down.triggered.connect(lambda: self._move_item(1)); action_down.setEnabled(self.down_btn.isEnabled())
        menu.exec_(self.schema_tree.mapToGlobal(position))

    def _remove_item(self):
        item = self.schema_tree.currentItem();
        if not item: return
        item_info = item.data(0, Qt.UserRole); item_type = item_info.get("type")
        reply = QMessageBox.question(self, "确认删除", f"您确定要删除选中的 '{item.text(0)}' 吗？" + ("这将同时删除该分组下的所有字段！" if item_type == "group" else ""), QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            parent = item.parent();
            if parent: parent.removeChild(item)
            else: self.schema_tree.takeTopLevelItem(self.schema_tree.indexOfTopLevelItem(item))
            self._update_button_states()

    def _move_item(self, direction):
        item = self.schema_tree.currentItem();
        if not item: return
        parent = item.parent();
        if parent:
            index = parent.indexOfChild(item);
            if direction == -1 and index > 0: parent.takeChild(index); parent.insertChild(index - 1, item)
            elif direction == 1 and index < parent.childCount() - 1: parent.takeChild(index); parent.insertChild(index + 1, item)
        else:
            index = self.schema_tree.indexOfTopLevelItem(item);
            if direction == -1 and index > 0: self.schema_tree.takeTopLevelItem(index); self.schema_tree.insertTopLevelItem(index - 1, item)
            elif direction == 1 and index < self.schema_tree.topLevelItemCount() - 1: self.schema_tree.takeTopLevelItem(index); self.schema_tree.insertTopLevelItem(index + 1, item)
        self.schema_tree.setCurrentItem(item); self._update_button_states()

    def _new_template(self):
        name, ok = QInputDialog.getText(self, "新建模板", "请输入新模板的名称:");
        if ok and name:
            if name in self.config_manager.get_template_names(): QMessageBox.warning(self, "错误", "模板名称已存在。"); return
            self.config_manager.save_template_schema(name, deepcopy(self.config_manager.get_template_schema(self.template_selector_combo.currentText() if self.template_selector_combo.currentText() else "默认模板")))
            self._load_settings(); self.template_selector_combo.setCurrentText(name)

    def _rename_template(self):
        old_name = self.template_selector_combo.currentText();
        if not old_name: return
        new_name, ok = QInputDialog.getText(self, "重命名模板", f"为 '{old_name}' 输入新名称:", text=old_name);
        if ok and new_name and new_name != old_name:
            success, error = self.config_manager.rename_template(old_name, new_name);
            if success: self._load_settings(); self.template_selector_combo.setCurrentText(new_name)
            else: QMessageBox.critical(self, "错误", error)

    def _duplicate_template(self):
        source_name = self.template_selector_combo.currentText();
        if not source_name: return
        new_name, ok = QInputDialog.getText(self, "复制模板", f"为 '{source_name}' 的副本输入新名称:", text=f"{source_name}_副本");
        if ok and new_name:
            if new_name in self.config_manager.get_template_names(): QMessageBox.warning(self, "错误", "模板名称已存在。"); return
            self.config_manager.save_template_schema(new_name, deepcopy(self.config_manager.get_template_schema(source_name)))
            self._load_settings(); self.template_selector_combo.setCurrentText(new_name)

    def _delete_template(self):
        name_to_delete = self.template_selector_combo.currentText();
        if not name_to_delete: return
        reply = QMessageBox.warning(self, "确认删除", f"您确定要永久删除模板 '{name_to_delete}' 吗？\n此操作不可撤销。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No);
        if reply == QMessageBox.Yes:
            success, error = self.config_manager.delete_template(name_to_delete);
            if success: self._load_settings()
            else: QMessageBox.critical(self, "删除失败", error)

    def _update_button_states(self, current_item=None, previous_item=None):
        item = self.schema_tree.currentItem();
        is_item_selected = item is not None; is_group_selected = is_item_selected and item.parent() is None
        is_field_selected = is_item_selected and item.parent() is not None
        self.add_field_btn.setEnabled(is_group_selected or is_field_selected); self.edit_btn.setEnabled(is_item_selected)
        self.remove_btn.setEnabled(is_item_selected)
        if is_item_selected:
            if is_group_selected:
                index = self.schema_tree.indexOfTopLevelItem(item); self.up_btn.setEnabled(index > 0)
                self.down_btn.setEnabled(index < self.schema_tree.topLevelItemCount() - 1)
            else:
                parent = item.parent(); index = parent.indexOfChild(item); self.up_btn.setEnabled(index > 0)
                self.down_btn.setEnabled(index < parent.childCount() - 1)
        else: self.up_btn.setEnabled(False); self.down_btn.setEnabled(False)

    def _browse_root_path(self):
        path = QFileDialog.getExistingDirectory(self, "选择档案库根目录", self.root_path_edit.text());
        if path: self.root_path_edit.setText(path)

    def _open_recycle_bin_from_settings(self):
        recycle_bin_dialog = RecycleBinDialog(self.parent_dialog);
        if recycle_bin_dialog.exec_() == QDialog.Accepted: self.parent_dialog._update_dashboard(lazy=True)

# ==============================================================================
# 3. UI 对话框 (v4.9 - 标准化布局 & 动态表单)
# ==============================================================================
class ArchiveDialog(QDialog):
    def __init__(self, main_window, config_manager):
        super().__init__(main_window); self.main_window = main_window; self.config_manager = config_manager
        self.icon_manager = main_window.icon_manager
        self.data_manager = ArchiveDataManager(self.config_manager.get_archive_root())
        self.current_view = 'dashboard'; self.current_experiment = None
        self.current_participant_id = None; self.current_selected_item_name = None
        self.is_current_exp_locked = False
        self.participant_widgets = {}
        self.setWindowTitle("档案库"); self.resize(1200, 800); self.setMinimumSize(1100, 700)
        self._init_ui(); self._connect_signals(); self.load_dashboard()

    def _init_ui(self):
        """初始化用户界面布局，包含一个完全统一的全局底部操作栏。"""
        main_layout = QVBoxLayout(self)
    
        # --- 顶部的 Splitter 区域 (不变) ---
        splitter = QSplitter(Qt.Horizontal)
    
        # 左侧面板
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_panel.setFixedWidth(400)
    
        search_widget = QWidget(); search_layout = QHBoxLayout(search_widget); search_layout.setContentsMargins(0,0,0,0); search_layout.setSpacing(0)
        self.search_box = QLineEdit(); self.search_box.setPlaceholderText("在当前列表中快速筛选...")
        self.clear_search_btn = QPushButton("×"); self.clear_search_btn.setFixedSize(QSize(24,24)); self.clear_search_btn.setVisible(False); self.clear_search_btn.setStyleSheet("QPushButton { border: none; font-size: 16px; background-color: transparent; }")
        search_layout.addWidget(self.search_box); search_layout.addWidget(self.clear_search_btn)
    
        self.nav_label = QLabel(); self.nav_label.setObjectName("SubheaderLabel")
        self.back_btn = QPushButton(" 返回"); self.back_btn.setIcon(self.icon_manager.get_icon("prev"))
        nav_layout = QHBoxLayout(); nav_layout.addWidget(self.back_btn); nav_layout.addWidget(self.nav_label, 1)
    
        self.item_list = QListWidget(); self.item_list.setSpacing(2); self.item_list.setContextMenuPolicy(Qt.CustomContextMenu)
    
        # [核心修改] 移除左侧底部的按钮布局
        # left_btn_layout = QHBoxLayout() ... (这整块都将被删除)
    
        left_layout.addWidget(search_widget)
        left_layout.addLayout(nav_layout)
        left_layout.addWidget(self.item_list, 1)
        # left_layout.addLayout(left_btn_layout) # 删除这一行

        # 右侧面板 (不变)
        right_panel_scroll = QScrollArea(); right_panel_scroll.setWidgetResizable(True); right_panel_scroll.setFrameShape(QFrame.NoFrame)
        self.form_stack = QStackedWidget(); right_panel_scroll.setWidget(self.form_stack)
        self.form_stack.addWidget(self._create_dashboard_form()); self.form_stack.addWidget(self._create_experiment_form())
        self.form_stack.addWidget(self._create_participant_form()); self.form_stack.addWidget(self._create_session_form())
    
        splitter.addWidget(left_panel); splitter.addWidget(right_panel_scroll); splitter.setSizes([400, 800])
    
        main_layout.addWidget(splitter, 1)

        # --- [核心修改] 创建一个包含所有操作按钮的全局底部栏 ---
        bottom_bar = QFrame()
        bottom_bar.setFrameShape(QFrame.HLine)
        bottom_bar.setFrameShadow(QFrame.Sunken)
    
        self.global_action_layout = QHBoxLayout()
        self.global_action_layout.setContentsMargins(0, 8, 0, 0)

        # 创建所有可能的操作按钮
        self.settings_btn = QPushButton(self.icon_manager.get_icon("settings"), " 设置")
        self.export_csv_btn = QPushButton("导出"); self.export_csv_btn.setIcon(self.icon_manager.get_icon("export"))
        self.new_experiment_btn = QPushButton(self.icon_manager.get_icon("paste"), "新建实验")
        self.new_participant_btn = QPushButton(self.icon_manager.get_icon("add_row"), "新建受试者")
        self.add_session_btn = QPushButton(self.icon_manager.get_icon("link"), "关联会话")
        self.exp_save_btn = QPushButton("保存实验信息"); self.exp_save_btn.setIcon(self.icon_manager.get_icon("save_2"))
        self.exp_save_btn.setObjectName("AccentButton")
        self.part_save_btn = QPushButton("保存受试者档案"); self.part_save_btn.setIcon(self.icon_manager.get_icon("save_2"))
        self.part_save_btn.setObjectName("AccentButton")
        self.session_save_btn = QPushButton("保存会话信息"); self.session_save_btn.setIcon(self.icon_manager.get_icon("save_2"))
        self.session_save_btn.setObjectName("AccentButton")
    
        # 添加左侧的“设置”按钮
        self.global_action_layout.addWidget(self.settings_btn)
        self.global_action_layout.addStretch() # 添加伸缩项，将其他按钮推到右侧

        # 添加所有其他操作按钮
        self.global_action_layout.addWidget(self.export_csv_btn)
        self.global_action_layout.addWidget(self.new_experiment_btn)
        self.global_action_layout.addWidget(self.new_participant_btn)
        self.global_action_layout.addWidget(self.add_session_btn)
        self.global_action_layout.addWidget(self.exp_save_btn)
        self.global_action_layout.addWidget(self.part_save_btn)
        self.global_action_layout.addWidget(self.session_save_btn)
    
        main_layout.addWidget(bottom_bar)
        main_layout.addLayout(self.global_action_layout)
        
    def _connect_signals(self):
        """连接UI控件的信号到槽函数。"""
        self.item_list.currentItemChanged.connect(self.on_item_selection_changed)
        self.item_list.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.item_list.customContextMenuRequested.connect(self.on_list_context_menu)
    
        self.back_btn.clicked.connect(self.on_back_clicked)
        self.settings_btn.clicked.connect(self.on_settings_clicked)
        self.new_experiment_btn.clicked.connect(self.on_new_experiment)
        self.new_participant_btn.clicked.connect(self.on_new_participant)
        self.add_session_btn.clicked.connect(self.on_add_session)
    
        # [核心修改] 将所有保存按钮的连接移到这里
        self.exp_save_btn.clicked.connect(self.on_save_experiment) 
        self.part_save_btn.clicked.connect(self.on_save_participant)
        self.session_save_btn.clicked.connect(self.on_save_session) 
    
        self.export_csv_btn.clicked.connect(self.on_export_to_csv)
    
        self.search_box.textChanged.connect(self._filter_list)
        self.clear_search_btn.clicked.connect(self.search_box.clear)

        # [核心修改] 将日期输入框的信号连接和事件过滤器安装移到这里
        self.exp_date_edit.textChanged.connect(lambda: self._format_date_input(self.exp_date_edit))
        self.exp_date_edit.installEventFilter(self)
    
        self.session_date_edit.textChanged.connect(lambda: self._format_date_input(self.session_date_edit))
        self.session_date_edit.installEventFilter(self)

    def eventFilter(self, source, event):
        """事件过滤器，用于处理日期输入框的回车事件。"""
        # 检查事件源是否是我们关心的两个日期输入框之一
        if source in [self.exp_date_edit, self.session_date_edit]:
            # 检查是否是按键事件，并且是回车键
            if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
                # 检查输入框当前是否为空
                if not source.text().strip():
                    # 设置为当天的日期
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    source.setText(today_str)
                    return True # 返回True，表示事件已被处理，不再向后传递

        # 对于所有其他事件，调用父类的默认实现
        return super().eventFilter(source, event)

    def _create_dashboard_form(self):
        w = QWidget(); layout = QVBoxLayout(w); layout.setContentsMargins(20,20,20,20)
        title = QLabel("档案库仪表盘"); title.setObjectName("FormTitleLabel")
        stats_layout = QHBoxLayout(); self.exp_count_label = self._create_stat_box("0", "实验项目")
        self.part_count_label = self._create_stat_box("0", "受试者档案"); self.session_count_label = self._create_stat_box("0", "数据会话")
        stats_layout.addWidget(self.exp_count_label); stats_layout.addWidget(self.part_count_label); stats_layout.addWidget(self.session_count_label)
        recent_group = QGroupBox("最近修改"); recent_layout = QVBoxLayout(recent_group)
        self.recent_files_list = QListWidget(); self.recent_files_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.recent_files_list.setToolTip("最近被修改过的5个档案文件")
        recent_layout.addWidget(self.recent_files_list)
        layout.addWidget(title); layout.addLayout(stats_layout); layout.addWidget(recent_group, 1)
        return w

    def _create_stat_box(self, number, text):
        box = QLabel(f"<div style='text-align:center;'><p style='font-size:24px; font-weight:bold; margin:0;'>{number}</p><p style='font-size:12px; color:grey;'>{text}</p></div>")
        box.setFrameShape(QFrame.StyledPanel); box.setMinimumHeight(80)
        box.setToolTip(f"当前档案库中总的{text}数量"); return box
        
    def _create_changelog_table(self):
        table = QTableWidget(); table.setColumnCount(3); table.setHorizontalHeaderLabels(["时间", "用户", "操作"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents); table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch); table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows); table.setToolTip("记录此档案的所有修改历史")
        return table

    def _format_date_input(self, widget):
        """自动格式化并进行合理性校验的日期输入框，用户只需输入数字。"""
        widget.blockSignals(True)
    
        original_text = widget.text()
        cursor_pos = widget.cursorPosition()
    
        # 清理文本，只保留数字，并限制长度为8
        clean_text = ''.join(filter(str.isdigit, original_text))[:8]
    
        # [核心修改] 合理性检测与自动补零
        year, month, day = "", "", ""
        if len(clean_text) > 0:
            year = clean_text[:4]
    
        if len(clean_text) > 4:
            # 月份处理
            m_part = clean_text[4:6]
            if len(m_part) == 1 and int(m_part) > 1: # 如果月份第一位是2-9，自动补0
                m_part = "0" + m_part
            elif len(m_part) == 2:
                m_int = int(m_part)
                if m_int == 0: m_part = "01"
                elif m_int > 12: m_part = "12"
            month = m_part
    
        if len(clean_text) > 6:
            # 日期处理
            d_part = clean_text[6:8]
            if len(d_part) == 1 and int(d_part) > 3: # 如果日期第一位是4-9，自动补0
                d_part = "0" + d_part
            elif len(d_part) == 2:
                d_int = int(d_part)
                if d_int == 0: d_part = "01"
                elif d_int > 31: d_part = "31"
            day = d_part
    
        # 重新组合格式化的文本
        parts = []
        if year: parts.append(year)
        if month: parts.append(month)
        if day: parts.append(day)
    
        # 根据组合后的部分重新插入连字符
        if len(parts) > 1:
            formatted_text = "-".join(parts)
        else:
            formatted_text = "".join(parts)
        
        widget.setText(formatted_text)

        # 简单的光标位置恢复逻辑
        if len(original_text) < len(formatted_text) and (cursor_pos == 4 or cursor_pos == 7):
            widget.setCursorPosition(cursor_pos + 1)
        else:
            widget.setCursorPosition(cursor_pos)
        
        widget.blockSignals(False)

    def _create_experiment_form(self):
        """创建实验详情视图的表单。"""
        page_widget = QWidget()
        page_layout = QVBoxLayout(page_widget)
        page_layout.setContentsMargins(10, 5, 10, 5)
        page_layout.setSpacing(10)

        self.exp_form_label = QLabel()
        self.exp_form_label.setObjectName("FormTitleLabel")
        page_layout.addWidget(self.exp_form_label)
    
        details_box = CollapsibleBox("实验详情")
        details_f_layout = QFormLayout()
        self.exp_researcher_edit = QLineEdit()
        self.exp_date_edit = QLineEdit()
    
        # [核心修改] 移除信号连接和事件过滤器安装
        # self.exp_date_edit.textChanged.connect(...)
        # self.exp_date_edit.installEventFilter(self)
        self.exp_date_edit.setPlaceholderText("例如: 20250715 (回车填入当天日期)")
    
        self.exp_purpose_text = QTextEdit()
        details_f_layout.addRow("研究员:", self.exp_researcher_edit)
        details_f_layout.addRow("创建日期:", self.exp_date_edit)
        details_f_layout.addRow("研究目的/备注:", self.exp_purpose_text)
        details_box.setContentLayout(details_f_layout)
        page_layout.addWidget(details_box)

        log_box = CollapsibleBox("变更历史")
        log_layout = QVBoxLayout()
        self.exp_changelog_table = self._create_changelog_table()
        log_layout.addWidget(self.exp_changelog_table)
        log_box.setContentLayout(log_layout)
        page_layout.addWidget(log_box)
    
        page_layout.addStretch(1)

        return page_widget

    def _create_participant_form(self):
        """创建受试者详情视图的表单，包含固定的底部按钮栏。"""
        page_widget = QWidget()
        main_layout = QVBoxLayout(page_widget)
        main_layout.setContentsMargins(10, 5, 10, 10) # 底部边距稍大以容纳按钮
        main_layout.setSpacing(10)

        # 1. 标题
        self.part_form_label = QLabel()
        self.part_form_label.setObjectName("FormTitleLabel")
        main_layout.addWidget(self.part_form_label)

        # 2. 创建一个可以滚动的区域
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        main_layout.addWidget(scroll_area, 1) # 让滚动区域占据所有可用空间

        # 3. 创建一个容器来放置所有可滚动的内容
        scroll_content_widget = QWidget()
        scroll_area.setWidget(scroll_content_widget)
        scroll_layout = QVBoxLayout(scroll_content_widget)
        scroll_layout.setContentsMargins(0, 0, 5, 0) # 右侧留出滚动条空间
        scroll_layout.setSpacing(10)

        # 4. 动态字段容器 (将被放入滚动布局中)
        self.participant_dynamic_fields_container = QWidget()
        self.participant_dynamic_fields_layout = QVBoxLayout(self.participant_dynamic_fields_container)
        self.participant_dynamic_fields_layout.setContentsMargins(0, 0, 0, 0)
        self.participant_dynamic_fields_layout.setSpacing(10)
        scroll_layout.addWidget(self.participant_dynamic_fields_container)

        # 5. 变更历史 (也将被放入滚动布局中)
        self.part_changelog_box = CollapsibleBox("变更历史")
        part_changelog_inner_layout = QVBoxLayout()
        self.part_changelog_table = self._create_changelog_table()
    
        # [核心修改] 为变更历史表格设置一个最小高度
        self.part_changelog_table.setMinimumHeight(200)
    
        part_changelog_inner_layout.addWidget(self.part_changelog_table)
        self.part_changelog_box.setContentLayout(part_changelog_inner_layout)
        scroll_layout.addWidget(self.part_changelog_box)


        # 6. 为按钮栏预留空间，但按钮本身已移至全局操作栏
        # 在这个方法中不再创建按钮，按钮的显隐由 _update_view_state 控制

        # 7. 首次调用以构建动态表单的骨架
        self._build_dynamic_participant_form()

        return page_widget

    def _build_dynamic_participant_form(self):
        while self.participant_dynamic_fields_layout.count():
            item = self.participant_dynamic_fields_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.participant_widgets.clear()
        
        template_name = "默认模板"
        if self.current_experiment:
            exp_data = self.data_manager.load_json(self.current_experiment, "experiment.json")
            template_name = exp_data.get("template_name", template_name)
        
        schema = self.config_manager.get_template_schema(template_name)
        
        if not schema:
            self.participant_dynamic_fields_layout.addWidget(QLabel(f"模板 '{template_name}' 为空或未找到。\n请在“设置”中进行配置。"))
            return

        for group_data in schema:
            group_name = group_data.get("group_name", "未命名分组"); collapsible_box = CollapsibleBox(group_name)
            columns = group_data.get("columns", 1); fields = group_data.get("fields", [])
            if columns == 2:
                grid_layout = QGridLayout(); grid_layout.setColumnStretch(1, 1); grid_layout.setColumnStretch(3, 1); grid_layout.setHorizontalSpacing(20)
                row, col = 0, 0
                for field in fields:
                    widget = self._create_widget_for_field(field)
                    if widget:
                        label = QLabel(f'{field.get("label", field["key"])}:')
                        if field["type"] == "TextEdit":
                            if col == 1: row += 1; col = 0
                            grid_layout.addWidget(label, row, 0); grid_layout.addWidget(widget, row, 1, 1, 3); row += 1; col = 0
                        else:
                            grid_layout.addWidget(label, row, col * 2); grid_layout.addWidget(widget, row, col * 2 + 1); col += 1
                            if col >= 2: col = 0; row += 1
                collapsible_box.setContentLayout(grid_layout)
            else:
                form_layout = QFormLayout()
                for field in fields:
                    widget = self._create_widget_for_field(field)
                    if widget: form_layout.addRow(QLabel(f'{field.get("label", field["key"])}:'), widget)
                collapsible_box.setContentLayout(form_layout)
            self.participant_dynamic_fields_layout.addWidget(collapsible_box)
            if not group_data.get("collapsible", True): collapsible_box.toggle_collapsed(False)

    def _create_widget_for_field(self, field_data):
        """辅助函数，根据字段定义创建对应的 QWidget 控件。"""
        key = field_data.get("key")
        field_type = field_data.get("type", "LineEdit")
        tooltip = field_data.get("tooltip", "")

        widget = None
        if field_type == "TextEdit":
            widget = QTextEdit()
            widget.setMinimumHeight(80)
        elif field_type == "ComboBox":
            widget = QComboBox()
            widget.addItems(field_data.get("options", []))
        elif field_type == "DateEdit":
            # [核心修改] 创建一个 QLineEdit 并应用日期格式化逻辑
            widget = QLineEdit()
            widget.textChanged.connect(lambda: self._format_date_input(widget))
            widget.installEventFilter(self)
            widget.setPlaceholderText("例如: 20250715")
        else: # 默认为 LineEdit
            widget = QLineEdit()

        if widget:
            widget.setToolTip(tooltip)
            self.participant_widgets[key] = widget
        return widget

    def _create_session_form(self):
        """创建会话详情视图的表单。"""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(10, 5, 10, 10)
        layout.setSpacing(10)

        self.session_form_label = QLabel()
        self.session_form_label.setObjectName("FormTitleLabel")
        layout.addWidget(self.session_form_label)

        g = QGroupBox("会话详情")
        f = QFormLayout(g)
        self.session_path_edit = QLineEdit()
        self.session_path_edit.setReadOnly(True)
        self.session_date_edit = QLineEdit()

        # [核心修改] 移除信号连接和事件过滤器安装
        # self.session_date_edit.textChanged.connect(...)
        # self.session_date_edit.installEventFilter(self)
        self.session_date_edit.setPlaceholderText("例如: 20250715 (回车填入当天)")

        self.session_task_edit = QLineEdit()
        self.session_notes_text = QTextEdit()
        self.session_tags_edit = QLineEdit()
        self.session_tags_edit.setToolTip("使用逗号分隔多个标签")
        f.addRow("数据文件夹:", self.session_path_edit)
        f.addRow("采集日期:", self.session_date_edit)
        f.addRow("采集任务类型:", self.session_task_edit)
        f.addRow("会话备注:", self.session_notes_text)
        f.addRow("标签:", self.session_tags_edit)
        layout.addWidget(g)
    
        layout.addStretch(1)
    
        return w

    def _populate_changelog_table(self, table, log_data):
        table.setRowCount(0);
        for entry in log_data:
            row_pos = table.rowCount(); table.insertRow(row_pos)
            table.setItem(row_pos, 0, QTableWidgetItem(entry.get("timestamp", "")))
            table.setItem(row_pos, 1, QTableWidgetItem(entry.get("user", "")))
            table.setItem(row_pos, 2, QTableWidgetItem(entry.get("action", "")))

    def _update_view_state(self, view, experiment=None, participant_id=None):
        """更新当前视图状态和UI元素的可见性。"""
        self.item_list.clear()
        self.search_box.clear()
        self.current_view = view
        self.current_experiment = experiment
        self.current_participant_id = participant_id
    
        is_dash = view == 'dashboard'
        is_exp = view == 'experiments'
        is_part = view == 'participants'
        is_sess = view == 'sessions'
    
        # 切换堆栈页面 (不变)
        if is_dash: self.form_stack.setCurrentIndex(0)
        elif is_exp: self.form_stack.setCurrentIndex(1)
        elif is_part: self.form_stack.setCurrentIndex(2)
        elif is_sess: self.form_stack.setCurrentIndex(3)
    
        # 左侧导航栏控制 (不变)
        self.back_btn.setVisible(not is_dash)
        self.nav_label.setText("仪表盘" if is_dash else "实验列表" if is_exp else f"实验: {experiment}" if is_part else f"受试者: {participant_id}")
    
        # [核心修改] 统一控制全局底部栏所有按钮的显隐
        self.settings_btn.setVisible(True) # 设置按钮始终可见
        self.new_experiment_btn.setVisible(is_exp or is_dash)
        self.export_csv_btn.setVisible(is_part)
        self.new_participant_btn.setVisible(is_part)
        self.add_session_btn.setVisible(is_sess)
        self.exp_save_btn.setVisible(is_exp)
        self.part_save_btn.setVisible(is_part)
        self.session_save_btn.setVisible(is_sess)
    
        # 更新锁定状态 (不变)
        self.is_current_exp_locked = self.data_manager.load_json(experiment, "experiment.json").get("is_locked", False) if experiment else False
        self._update_form_lock_state()

    def _update_form_lock_state(self):
        locked = self.is_current_exp_locked
        widgets_to_disable = (QLineEdit, QTextEdit, QComboBox)
        exp_form = self.form_stack.widget(1)
        for widget in exp_form.findChildren(widgets_to_disable): widget.setDisabled(locked)
        self.exp_save_btn.setDisabled(locked)
        part_form = self.form_stack.widget(2)
        for key, widget in self.participant_widgets.items():
            if isinstance(widget, widgets_to_disable): widget.setDisabled(locked)
        self.part_save_btn.setDisabled(locked)
        session_form = self.form_stack.widget(3)
        for widget in session_form.findChildren(widgets_to_disable):
            if widget is not self.session_path_edit: widget.setDisabled(locked)
        self.session_save_btn.setDisabled(locked)
        self.new_participant_btn.setDisabled(locked); self.add_session_btn.setDisabled(locked)
        if locked:
            self.new_participant_btn.setToolTip("实验已锁定，无法添加新的受试者。"); self.add_session_btn.setToolTip("实验已锁定，无法关联新的会话。")
        else:
            self.new_participant_btn.setToolTip("在当前选中的实验下，创建一个新的受试者档案。"); self.add_session_btn.setToolTip("为当前选中的受试者，关联一个包含实验数据的文件夹。")

    def _filter_list(self):
        query = self.search_box.text().lower(); self.clear_search_btn.setVisible(bool(query))
        for i in range(self.item_list.count()):
            item = self.item_list.item(i); item.setHidden(query not in item.text().lower())

    def _add_placeholder_item(self, text):
        item = QListWidgetItem(text); item.setFlags(Qt.NoItemFlags); self.item_list.addItem(item)
    def _is_item_valid(self, item): return item and item.flags() & Qt.ItemIsEnabled
    def _clear_forms(self):
        exp_form = self.form_stack.widget(1);
        for w in exp_form.findChildren((QLineEdit, QTextEdit)): w.clear()
        for w in exp_form.findChildren(QTableWidget): w.setRowCount(0)
        self._clear_participant_form()
        session_form = self.form_stack.widget(3);
        for w in session_form.findChildren((QLineEdit, QTextEdit)): w.clear()
        for w in session_form.findChildren(QComboBox): w.setCurrentIndex(0)

    def _elide_text(self, text, max_width):
        """
        如果文本像素宽度超过最大宽度，则截断文本并添加省略号。
        :param text: 原始文本字符串。
        :param max_width: 允许的最大像素宽度。
        :return: 截断后的文本字符串。
        """
        metrics = self.recent_files_list.fontMetrics()
        if metrics.horizontalAdvance(text) <= max_width:
            return text
    
        # [核心修复] 将 elideText 修正为 elidedText
        return metrics.elidedText(text, Qt.ElideRight, max_width)

    def _update_dashboard(self, lazy=False):
        """更新仪表盘数据，并使用文本截断和Tooltip显示最近修改列表。"""
        if lazy and self.current_view != 'dashboard':
            return

        summary = self.data_manager.get_archive_summary()
    
        # 更新统计盒子 (不变)
        self.exp_count_label.setText(f"<div style='text-align:center;'><p style='font-size:24px; font-weight:bold; margin:0;'>{summary['exp_count']}</p><p style='font-size:12px; color:grey;'>实验项目</p></div>")
        self.part_count_label.setText(f"<div style='text-align:center;'><p style='font-size:24px; font-weight:bold; margin:0;'>{summary['part_count']}</p><p style='font-size:12px; color:grey;'>受试者档案</p></div>")
        self.session_count_label.setText(f"<div style='text-align:center;'><p style='font-size:24px; font-weight:bold; margin:0;'>{summary['session_count']}</p><p style='font-size:12px; color:grey;'>数据会话</p></div>")
    
        # 更新最近修改列表的显示逻辑
        self.recent_files_list.clear()
        if not summary['recent_items']:
            self.recent_files_list.addItem("暂无修改记录。")
            return # [新增] 如果没有项目，直接返回
    
        # [核心修改] 动态计算可用宽度
        # 减去图标宽度、边距和一些缓冲空间
        available_width = self.recent_files_list.viewport().width() - self.recent_files_list.iconSize().width() - 30 
        if available_width <= 50: # 如果宽度非常小，给一个默认值
            available_width = 250

        for item_info in summary['recent_items']:
            action = item_info.get("action", "未知操作")
            path = item_info.get("path", "未知文件")
            time = item_info.get("time", "未知时间")
        
            # 根据操作类型选择图标 (不变)
            icon_name = "edit"
            action_lower = action.lower()
            if "创建" in action_lower or "新建" in action_lower: icon_name = "add_row"
            elif "删除" in action_lower or "解除" in action_lower: icon_name = "delete"
            elif "更新" in action_lower or "重命名" in action_lower: icon_name = "draw"
            elif "锁定" in action_lower: icon_name = "lock"
            elif "解锁" in action_lower: icon_name = "unlock"
        
            # 创建列表项并设置图标
            list_item = QListWidgetItem()
            list_item.setIcon(self.icon_manager.get_icon(icon_name))
        
            # [核心修改] 构建完整文本，然后截断，并设置Tooltip
        
            # 1. 构建完整的、未经截断的文本用于显示和Tooltip
            full_display_text = f"{action}\n└ {path} @ {time}"
        
            # 2. 构建截断后的显示文本
            # 我们只截断第一行（操作+路径），时间保持完整
            line1_full = f"{action}: {path}"
            line1_elided = self._elide_text(line1_full, available_width)
            display_text_elided = f"{line1_elided}\n└ {time}"
        
            # 3. 设置截断后的文本和完整的Tooltip
            list_item.setText(display_text_elided)
            list_item.setToolTip(full_display_text)
        
            self.recent_files_list.addItem(list_item)

    def load_dashboard(self):
        self._update_view_state('dashboard'); self._update_dashboard()
        self.item_list.addItems(self.data_manager.get_experiments())

    def load_experiment_list(self):
        self._update_view_state('experiments'); experiments = self.data_manager.get_experiments()
        if not experiments: self._add_placeholder_item("未找到任何实验项目。点击“新建实验”开始。"); return
        for name in experiments:
            item = QListWidgetItem(name);
            if self.data_manager.load_json(name, "experiment.json").get("is_locked", False): item.setIcon(self.icon_manager.get_icon("lock"))
            self.item_list.addItem(item)

    def load_participant_list(self, experiment_name):
        """加载受试者列表视图，并确保使用正确的模板重构表单。"""
        self.current_experiment = experiment_name
        self._update_view_state('participants', experiment=experiment_name)
    
        # [核心修复] 在切换视图后，立即调用表单构建方法
        # 此时 self.current_experiment 已经更新，_build_dynamic_participant_form
        # 内部的逻辑会读取到新实验指定的模板。
        self._build_dynamic_participant_form()

        exp_data = self.data_manager.load_json(experiment_name, "experiment.json")
        is_locked = exp_data.get("is_locked", False)
        template_name = exp_data.get("template_name", "默认模板") # 获取模板名用于显示
        lock_icon_text = " (🔒 已锁定)" if is_locked else ""
    
        # 在导航标签中同时显示实验名和所用模板名，增加透明度
        self.nav_label.setText(f"<b>实验:</b> {experiment_name}{lock_icon_text}<br><small>模板: {template_name}</small>")
        self.part_form_label.setText("请从左侧列表选择一个受试者进行查看或编辑")
    
        participants = self.data_manager.get_participants(experiment_name)
        if not participants:
            self._add_placeholder_item("该实验下没有受试者档案。点击“新建受试者”开始。")
            self._clear_participant_form() # 如果实验为空，则清空表单
            return

        for part_file in participants:
            item = QListWidgetItem(part_file)
            item.setData(Qt.UserRole, part_file[12:-5]) # 存储 'p001' 等ID
            self.item_list.addItem(item)

        if self.item_list.count() > 0:
            self.item_list.setCurrentRow(0)
        else:
            self._clear_participant_form() # 再次确保，如果过滤后列表为空，则清空表单

    def load_session_list(self, exp_name, part_id):
        self._update_view_state('sessions', experiment=exp_name, participant_id=part_id)
        sessions = self.data_manager.load_json(exp_name, f"participant_{part_id}.json").get("sessions", [])
        if not sessions: self._add_placeholder_item("该受试者无关联数据会话。点击“关联会话”添加。"); return
        for i, session in enumerate(sessions):
            display_name = os.path.basename(session.get("path", "未知路径")); item = QListWidgetItem(f"会话 {i+1}: {display_name}")
            item.setData(Qt.UserRole, i); item.setToolTip(session.get("path", "无路径信息")); self.item_list.addItem(item)

    def on_back_clicked(self):
        if self.current_view == 'sessions': self.load_participant_list(self.current_experiment)
        elif self.current_view == 'participants': self.load_experiment_list()
        elif self.current_view == 'experiments': self.load_dashboard()

    def on_item_double_clicked(self, item):
        if not self._is_item_valid(item): return
        if self.current_view == 'dashboard' or self.current_view == 'experiments': self.load_participant_list(item.text())
        elif self.current_view == 'participants': self.load_session_list(self.current_experiment, item.data(Qt.UserRole))
        elif self.current_view == 'sessions':
            session_index = item.data(Qt.UserRole); session_data = self.data_manager.load_json(self.current_experiment, f"participant_{self.current_participant_id}.json")["sessions"][session_index]
            session_path = session_data.get("path");
            if session_path and os.path.isdir(session_path): self.open_in_explorer(session_path)
            else: QMessageBox.warning(self, "路径无效", "会话数据文件夹不存在或路径无效。")

    def on_item_selection_changed(self, current, _):
        if not self._is_item_valid(current):
            self._clear_forms(); self.form_stack.setCurrentIndex(0); return
        if self.current_view == 'dashboard' or self.current_view == 'experiments': self.display_experiment_details(current.text())
        elif self.current_view == 'participants': self.display_participant_details(current.text())
        elif self.current_view == 'sessions': self.display_session_details(current.data(Qt.UserRole))

    def display_experiment_details(self, exp_name):
        """显示实验详情。"""
        self.form_stack.setCurrentIndex(1)
        data = self.data_manager.load_json(exp_name, "experiment.json")
        self.is_current_exp_locked = data.get('is_locked', False)
    
        lock_text = " (🔒 已锁定)" if self.is_current_exp_locked else ""
    
        # [核心修改] 启用自动换行并确保富文本被解析
        self.exp_form_label.setTextFormat(Qt.RichText) # 1. 确保能解析HTML标签
        self.exp_form_label.setWordWrap(True)          # 2. 启用自动换行
        self.exp_form_label.setText(f"<h3>实验: {exp_name}{lock_text}</h3>")
    
        self.exp_researcher_edit.setText(data.get("researcher",""))
        self.exp_date_edit.setText(data.get("date",""))
        self.exp_purpose_text.setPlainText(data.get("purpose",""))
    
        self._populate_changelog_table(self.exp_changelog_table, data.get("changelog", []))
        self._update_form_lock_state()

        item = self.find_item_by_text(exp_name)
        if item:
            item.setToolTip(f"实验: {exp_name}\n双击进入受试者列表")

    def display_participant_details(self, part_filename):
        part_id = part_filename.replace("participant_", "").replace(".json", ""); self.form_stack.setCurrentIndex(2)
        self.part_form_label.setText(f"<h3>受试者: {part_id}</h3>")
        data = self.data_manager.load_json(self.current_experiment, part_filename)
        for key, widget in self.participant_widgets.items():
            value = data.get(key);
            if key == 'tags':
                widget.setText(", ".join(value) if isinstance(value, list) else str(value) if value is not None else "")
            elif isinstance(widget, QLineEdit): widget.setText(str(value) if value is not None else "")
            elif isinstance(widget, QTextEdit): widget.setPlainText(str(value) if value is not None else "")
            elif isinstance(widget, QComboBox): widget.setCurrentText(str(value) if value is not None else "")
        self._populate_changelog_table(self.part_changelog_table, data.get("changelog", []))
        self._update_form_lock_state()

    def display_session_details(self, session_index):
        self.form_stack.setCurrentIndex(3); data = self.data_manager.load_json(self.current_experiment, f"participant_{self.current_participant_id}.json")
        s_data = data["sessions"][session_index]; s_name = os.path.basename(s_data.get("path","未知会话"))
        self.session_form_label.setText(f"<h3>会话: {s_name}</h3>")
        self.session_path_edit.setText(s_data.get("path", "")); self.session_date_edit.setText(s_data.get("date", ""))
        self.session_task_edit.setText(s_data.get("task", "")); self.session_notes_text.setPlainText(s_data.get("notes", ""))
        self.session_tags_edit.setText(", ".join(s_data.get("tags", []))); self._update_form_lock_state()

    def on_list_context_menu(self, position):
        item = self.item_list.itemAt(position);
        if not self._is_item_valid(item): return
        self.current_selected_item_name = item.text(); menu = QMenu(self)
        view = self.current_view;
        if view == 'dashboard': view = 'experiments'
        if view == 'experiments':
            data = self.data_manager.load_json(self.current_selected_item_name, "experiment.json")
            lock_text = "解锁实验" if data.get("is_locked") else "锁定实验"
            action_lock = menu.addAction(self.icon_manager.get_icon("unlock" if data.get("is_locked") else "lock"), lock_text)
            action_lock.triggered.connect(self.on_toggle_lock_experiment)
            menu.addSeparator()
            action_open = menu.addAction(self.icon_manager.get_icon("open_folder"), "在文件浏览器中打开")
            action_open.triggered.connect(lambda: self.open_in_explorer(os.path.join(self.data_manager.root_path, self.current_selected_item_name)))
            menu.addSeparator()
            action_rename = menu.addAction(self.icon_manager.get_icon("rename"), "重命名..."); action_rename.triggered.connect(self.on_rename_experiment)
            action_delete = menu.addAction(self.icon_manager.get_icon("delete"), "移至回收站..."); action_delete.triggered.connect(self.on_delete_experiment)
        elif view == 'participants':
            action_copy = menu.addAction(self.icon_manager.get_icon("copy"), "复制到其他实验...")
            action_copy.triggered.connect(self.on_copy_participant)
            menu.addSeparator()
            action_delete = menu.addAction(self.icon_manager.get_icon("delete"), "移至回收站...")
            action_delete.triggered.connect(self.on_delete_participant)
            if self.is_current_exp_locked: action_copy.setDisabled(True); action_delete.setDisabled(True)
        elif view == 'sessions':
            session_index = item.data(Qt.UserRole); session_data = self.data_manager.load_json(self.current_experiment, f"participant_{self.current_participant_id}.json")["sessions"][session_index]
            session_path = session_data.get("path")
            action_open = menu.addAction(self.icon_manager.get_icon("open_folder"), "打开数据文件夹")
            action_open.triggered.connect(lambda: self.open_in_explorer(session_path))
            if not (session_path and os.path.isdir(session_path)): action_open.setDisabled(True)
            menu.addSeparator()
            action_delete = menu.addAction(self.icon_manager.get_icon("delete"), "解除关联...")
            action_delete.triggered.connect(lambda: self.on_delete_session(item.data(Qt.UserRole)))
            if self.is_current_exp_locked: action_delete.setDisabled(True)
        menu.exec_(self.item_list.mapToGlobal(position))

    def on_save_experiment(self):
        """保存实验信息。"""
        if not self._is_item_valid(self.item_list.currentItem()): return
        name = self.item_list.currentItem().text()
        data = self.data_manager.load_json(name, "experiment.json")
        data.update({
            "researcher": self.exp_researcher_edit.text(),
            "date": self.exp_date_edit.text(),
            "purpose": self.exp_purpose_text.toPlainText()
        })
        success, error = self.data_manager.save_json(data, (name, "experiment.json"), "更新实验信息")
        if success:
            # [核心修改] 使用 QMessageBox 代替状态栏提示
            QMessageBox.information(self, "成功", "实验信息已成功保存。")
            self.display_experiment_details(name)
        else:
            QMessageBox.critical(self, "错误", f"保存失败: {error}")

    def on_save_participant(self):
        """保存受试者档案信息。"""
        if not self._is_item_valid(self.item_list.currentItem()): return
        filename = self.item_list.currentItem().text()
        data = self.data_manager.load_json(self.current_experiment, filename)
    
        for key, widget in self.participant_widgets.items():
            if key == 'tags':
                tags = [tag.strip() for tag in widget.text().split(',') if tag.strip()]
                data[key] = tags
            elif isinstance(widget, QLineEdit):
                data[key] = widget.text()
            elif isinstance(widget, QTextEdit):
                data[key] = widget.toPlainText()
            elif isinstance(widget, QComboBox):
                data[key] = widget.currentText()
            
        success, error = self.data_manager.save_json(data, (self.current_experiment, filename), "更新受试者信息")
        if success:
            # [核心修改] 使用 QMessageBox 代替状态栏提示
            QMessageBox.information(self, "成功", "受试者档案已成功保存。")
            self.display_participant_details(filename)
        else: 
            QMessageBox.critical(self, "错误", f"保存失败: {error}")

    def on_save_session(self):
        """保存会话信息。"""
        if not self._is_item_valid(self.item_list.currentItem()): return
        session_index = self.item_list.currentItem().data(Qt.UserRole)
        tags = [tag.strip() for tag in self.session_tags_edit.text().split(',') if tag.strip()]
        session_data = {
            "path": self.session_path_edit.text(),
            "date": self.session_date_edit.text(),
            "task": self.session_task_edit.text(),
            "notes": self.session_notes_text.toPlainText(),
            "tags": tags
        }
        success, error = self.data_manager.update_participant_session(self.current_experiment, self.current_participant_id, session_index, session_data)
        if success:
            # [核心修改] 使用 QMessageBox 代替状态栏提示
            QMessageBox.information(self, "成功", "会话信息已成功保存。")
            self.display_session_details(session_index)
        else:
            QMessageBox.critical(self, "错误", f"保存失败: {error}")

    def on_new_experiment(self):
        templates = self.config_manager.get_template_names();
        if not templates: QMessageBox.critical(self, "错误", "没有可用的表单模板，请先在设置中创建一个。"); return
        dialog = NewExperimentDialog(templates, self);
        if dialog.exec_() == QDialog.Accepted:
            name, template_name = dialog.get_data();
            if name and template_name:
                if name in self.data_manager.get_experiments(): QMessageBox.warning(self, "错误", "实验名已存在。"); return
                data = {"date": datetime.now().strftime("%Y-%m-%d"), "researcher": "", "purpose": "", "is_locked": False, "changelog": [], "template_name": template_name}
                success, error = self.data_manager.save_json(data, (name, "experiment.json"), "创建实验");
                if success: self.load_experiment_list(); self.find_and_select_item(name); self._update_dashboard(lazy=True)
                else: QMessageBox.critical(self, "错误", f"创建失败: {error}")

    def on_new_participant(self):
        """创建新受试者档案，并确保使用实验指定的正确模板。"""
        sug_id = self.data_manager.suggest_participant_id(self.current_experiment)
        part_id, ok = QInputDialog.getText(self, "新建受试者档案", "请输入受试者唯一ID:", text=sug_id)
    
        if ok and part_id:
            filename = f"participant_{part_id}.json"
            if filename in self.data_manager.get_participants(self.current_experiment):
                QMessageBox.warning(self, "错误", "该ID已存在于当前实验中，请使用不同的ID。")
                return
        
            # [核心修复] 从当前实验的元数据中获取模板名称
            exp_data = self.data_manager.load_json(self.current_experiment, "experiment.json")
            template_name = exp_data.get("template_name", "默认模板") # 如果没找到，安全地回退到“默认模板”
        
            # 使用获取到的模板名称来加载正确的 schema
            schema = self.config_manager.get_template_schema(template_name)
        
            if not schema:
                QMessageBox.critical(self, "模板错误", f"无法加载实验指定的模板 '{template_name}'。\n将使用一个空的档案结构。")

            # 初始化受试者数据，包含所有 schema 中定义的字段
            initial_data = {"id": part_id, "sessions": [], "changelog": []}
            for group in schema:
                for field in group.get("fields", []):
                    key = field.get("key")
                    if key and key not in initial_data: # 避免覆盖id, sessions, changelog
                        # 根据字段类型设置默认值
                        if field.get("type") == "ComboBox":
                            # 如果有选项，默认是第一个，否则是空字符串
                            initial_data[key] = field.get("options", [""])[0] if field.get("options") else ""
                        else: # LineEdit, TextEdit, DateEdit 的默认值都是空字符串
                            initial_data[key] = ""

            success, error = self.data_manager.save_json(initial_data, (self.current_experiment, filename), f"创建受试者档案 (使用模板: {template_name})")
        
            if success:
                self.load_participant_list(self.current_experiment)
                self.find_and_select_item(filename)
                self._update_dashboard(lazy=True)
            else:
                QMessageBox.critical(self, "错误", f"创建失败: {error}")

    def on_add_session(self):
        res_dir = self.main_window.config.get('file_settings',{}).get('results_dir', self.data_manager.root_path)
        directory = QFileDialog.getExistingDirectory(self, "选择要关联的数据文件夹", res_dir);
        if directory:
            success, error = self.data_manager.add_session_to_participant(self.current_experiment, self.current_participant_id, directory);
            if success: self.load_session_list(self.current_experiment, self.current_participant_id); self._update_dashboard(lazy=True)
            else: QMessageBox.warning(self, "关联失败", error)

    def on_delete_experiment(self):
        name = self.current_selected_item_name;
        if QMessageBox.warning(self, "确认操作", f"您确定要将实验 '{name}' 移至回收站吗？\n所有关联的受试者档案都将被一并移动。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            success, error = self.data_manager.delete_experiment(name);
            if success: self.load_experiment_list(); self._update_dashboard(lazy=True)
            else: QMessageBox.critical(self, "操作失败", error)

    def on_delete_participant(self):
        name = self.current_selected_item_name;
        if QMessageBox.warning(self, "确认操作", f"您确定要将档案 '{name}' 移至回收站吗？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            success, error = self.data_manager.delete_participant(self.current_experiment, name);
            if success: self.load_participant_list(self.current_experiment); self._update_dashboard(lazy=True)
            else: QMessageBox.critical(self, "操作失败", error)

    def on_delete_session(self, session_index):
        if QMessageBox.warning(self, "确认操作", "您确定要解除此数据会话的关联吗？\n这不会删除实际数据文件夹，仅从档案中移除记录。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            success, error = self.data_manager.delete_participant_session(self.current_experiment, self.current_participant_id, session_index);
            if success: self.load_session_list(self.current_experiment, self.current_participant_id); self._update_dashboard(lazy=True)
            else: QMessageBox.critical(self, "解除关联失败", error)

    def on_rename_experiment(self):
        old_name = self.current_selected_item_name; new_name, ok = QInputDialog.getText(self, "重命名实验", "请输入实验的新名称:", text=old_name);
        if ok and new_name and new_name != old_name:
            success, error = self.data_manager.rename_experiment(old_name, new_name);
            if success: self.load_experiment_list(); self.find_and_select_item(new_name)
            else: QMessageBox.critical(self, "重命名失败", error)

    def on_copy_participant(self):
        part_file = self.current_selected_item_name; targets = [e for e in self.data_manager.get_experiments() if e != self.current_experiment];
        if not targets: QMessageBox.information(self, "无法复制", "没有其他实验可作为目标，无法复制档案。"); return
        dest_exp, ok = QInputDialog.getItem(self, "选择目标实验", f"将档案 '{part_file}' 复制到:", targets, 0, False);
        if ok and dest_exp:
            success, error = self.data_manager.copy_participant_to_experiment(self.current_experiment, part_file, dest_exp);
            if success: QMessageBox.information(self, "成功", f"档案已成功复制到 '{dest_exp}'。")
            else: QMessageBox.critical(self, "复制失败", error)

    def on_toggle_lock_experiment(self):
        success, error = self.data_manager.toggle_experiment_lock(self.current_selected_item_name);
        if success: self.load_experiment_list(); self.find_and_select_item(self.current_selected_item_name)
        else: QMessageBox.critical(self, "操作失败", error)

    def on_export_to_csv(self):
        if not self.current_experiment: QMessageBox.warning(self, "操作无效", "请先选择一个实验。"); return
        exp_data = self.data_manager.load_json(self.current_experiment, "experiment.json")
        template_name = exp_data.get("template_name", "默认模板")
        schema = self.config_manager.get_template_schema(template_name)
        export_keys_info = []
        # [核心修改] 始终包含 'id' 字段作为第一列
        export_keys_info.append({'key': 'id', 'label': '受试者ID'})
        for group in schema:
            for field in group.get("fields", []):
                # 避免重复添加 'id'，并且只添加非复杂类型（如sessions, changelog）
                if field['key'] not in ['id', 'sessions', 'changelog']:
                    export_keys_info.append({'key': field['key'], 'label': field['label']})
        
        default_path = os.path.join(os.path.expanduser("~"), "Downloads", f"{self.current_experiment}_participants.csv")
        file_path, _ = QFileDialog.getSaveFileName(self, "导出受试者数据", default_path, "CSV Files (*.csv)");
        if file_path:
            success, error = self.data_manager.export_participants_to_csv(self.current_experiment, file_path, export_keys_info);
            if success: QMessageBox.information(self, "导出成功", f"数据已成功导出到:\n{file_path}")
            else: QMessageBox.critical(self, "导出失败", error)

    def on_settings_clicked(self):
        settings_dialog = ArchiveSettingsDialog(self);
        if settings_dialog.exec_() == QDialog.Accepted:
            self.data_manager = ArchiveDataManager(self.config_manager.get_archive_root());
            QMessageBox.information(self, "设置已更新", "档案库设置已更新并保存。"); self.on_settings_changed()
            
    def on_settings_changed(self):
        self.data_manager = ArchiveDataManager(self.config_manager.get_archive_root()); self.load_dashboard()

    def on_participant_schema_changed(self):
        self._build_dynamic_participant_form();
        if self.current_view == 'participants' and self.current_participant_id:
            part_filename = f"participant_{self.current_participant_id}.json"; self.display_participant_details(part_filename)
        else: self._clear_participant_form()

    def open_in_explorer(self, path):
        if not path or not os.path.isdir(path):
            QMessageBox.warning(self, "路径无效", f"无法打开路径:\n{path}\n请确保路径存在且是一个文件夹。"); return
        try:
            if sys.platform == 'win32': os.startfile(os.path.realpath(path))
            elif sys.platform == 'darwin': subprocess.check_call(['open', path])
            else: subprocess.check_call(['xdg-open', path])
        except Exception as e: QMessageBox.critical(self, "错误", f"无法打开路径: {e}")

    def find_item_by_text(self, text):
        items = self.item_list.findItems(text, Qt.MatchExactly); return items[0] if items else None
    def find_and_select_item(self, text):
        item = self.find_item_by_text(text);
        if item: self.item_list.setCurrentItem(item)
    def _clear_participant_form(self):
        for key, widget in self.participant_widgets.items():
            if isinstance(widget, QLineEdit): widget.clear()
            elif isinstance(widget, QTextEdit): widget.setPlainText("")
            elif isinstance(widget, QComboBox): widget.setCurrentIndex(0)
        if hasattr(self, 'part_changelog_table'): self.part_changelog_table.setRowCount(0)

# ==============================================================================
# 5. 回收站对话框 (增强 ToolTips)
# ==============================================================================
class RecycleBinDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent); self.data_manager = parent.data_manager
        self.setResult(QDialog.Rejected); self.setWindowTitle("回收站"); self.resize(700, 500)
        self._init_ui(); self.load_trashed_items()

    def _init_ui(self):
        layout = QVBoxLayout(self); layout.addWidget(QLabel("这里是已删除的项目。您可以选择恢复它们或永久删除。"))
        self.item_list = QTableWidget(); self.item_list.setColumnCount(3)
        self.item_list.setHorizontalHeaderLabels(["删除时间", "原始路径", "类型"])
        self.item_list.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.item_list.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.item_list.setSelectionBehavior(QAbstractItemView.SelectRows); self.item_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        btn_layout = QHBoxLayout(); self.restore_btn = QPushButton("恢复选中项"); self.restore_btn.setToolTip("将选中的项目恢复到其在档案库中的原始位置。")
        self.purge_btn = QPushButton("永久删除选中项"); self.purge_btn.setToolTip("警告：从回收站彻底删除选中的项目，此操作不可恢复！")
        self.close_btn = QPushButton("关闭")
        btn_layout.addStretch(); btn_layout.addWidget(self.restore_btn); btn_layout.addWidget(self.purge_btn); btn_layout.addWidget(self.close_btn)
        layout.addWidget(self.item_list); layout.addLayout(btn_layout)
        self.restore_btn.clicked.connect(self.on_restore); self.purge_btn.clicked.connect(self.on_purge); self.close_btn.clicked.connect(self.close)

    def load_trashed_items(self):
        self.item_list.setRowCount(0); items = self.data_manager.get_trashed_items()
        for name, info in sorted(items.items(), key=lambda x: x[0], reverse=True):
            row = self.item_list.rowCount(); self.item_list.insertRow(row)
            try: del_time = datetime.strptime(name.split('_')[0], '%Y%m%d%H%M%S').strftime('%Y-%m-%d %H:%M:%S')
            except ValueError: del_time = "未知时间"
            self.item_list.setItem(row, 0, QTableWidgetItem(del_time)); self.item_list.setItem(row, 1, QTableWidgetItem(info.get('original_path', '未知路径')))
            self.item_list.setItem(row, 2, QTableWidgetItem(info.get('type', '未知类型'))); self.item_list.item(row, 0).setData(Qt.UserRole, name)

    def _get_selected_item_name(self):
        items = self.item_list.selectedItems();
        if items: return self.item_list.item(items[0].row(), 0).data(Qt.UserRole)
        return None

    def on_restore(self):
        item_name = self._get_selected_item_name();
        if not item_name: return
        success, error = self.data_manager.restore_trashed_item(item_name);
        if success: self.setResult(QDialog.Accepted); self.load_trashed_items(); QMessageBox.information(self, "恢复成功", f"项目 '{item_name.split('_', 1)[1]}' 已成功恢复。")
        else: QMessageBox.critical(self, "恢复失败", error)

    def on_purge(self):
        item_name = self._get_selected_item_name();
        if not item_name: return
        reply = QMessageBox.warning(self, "确认永久删除", "此操作不可撤销！确定要永久删除选中的项目吗？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No);
        if reply == QMessageBox.Yes:
            success, error = self.data_manager.purge_trashed_item(item_name);
            if success: self.setResult(QDialog.Accepted); self.load_trashed_items(); QMessageBox.information(self, "删除成功", f"项目 '{item_name.split('_', 1)[1]}' 已被永久删除。")
            else: QMessageBox.critical(self, "删除失败", error)

# ==============================================================================
# 6. 插件主入口 (无变化)
# ==============================================================================
class ArchivePlugin(BasePlugin):
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager); self.dialog_instance = None
        self.config_manager = ArchiveConfigManager(main_window)

    def setup(self): return True

    def teardown(self):
        if self.dialog_instance: self.dialog_instance.close()

    def execute(self, **kwargs):
        if self.dialog_instance is None or not self.dialog_instance.isVisible():
            self.dialog_instance = ArchiveDialog(self.main_window, self.config_manager)
            self.dialog_instance.finished.connect(lambda: setattr(self, 'dialog_instance', None))
        self.dialog_instance.show(); self.dialog_instance.raise_(); self.dialog_instance.activateWindow()

# --- END OF FILE ---