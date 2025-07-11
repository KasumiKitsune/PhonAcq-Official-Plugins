# --- START OF FILE plugins/archive_manager/archive.py (v4.9 - Grouped Schema Refactor) ---

import os
import sys
import json
import shutil
import subprocess
from datetime import datetime

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
                             QTreeWidget, QTreeWidgetItem, QGridLayout) # 新增 QTreeWidget, QTreeWidgetItem
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon

try:
    # Correctly reference the module from the main application's perspective
    from modules.plugin_system import BasePlugin
except ImportError:
    # Fallback for standalone testing
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from modules.plugin_system import BasePlugin

# ==============================================================================
# 0. 可折叠框控件 (v4.7 简化版)
# ==============================================================================
class CollapsibleBox(QWidget):
    """一个可折叠的控件，无动画，折叠后向上收起。"""
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
        
        # 默认展开
        self.toggle_button.setChecked(True)
        self._set_arrow_icon()

    def _set_arrow_icon(self):
        """根据折叠状态设置按钮上的箭头图标。"""
        arrow_char = "▼" if self.toggle_button.isChecked() else "►"
        current_text = self.toggle_button.text()
        # 清理旧的箭头，只保留标题
        clean_title = current_text.lstrip('▼► ').strip()
        self.toggle_button.setText(f"{arrow_char} {clean_title}")

    def _toggle(self):
        """切换内容的显示/隐藏状态。"""
        self.content_area.setHidden(not self.toggle_button.isChecked())
        self._set_arrow_icon()

    def setContentLayout(self, layout):
        """设置可折叠框内部的内容布局。"""
        # 正确清除现有布局及其控件
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
            elif item.layout():
                # 递归清除嵌套布局
                self._clear_layout(item.layout())
                item.layout().deleteLater()
        
        self.content_layout.addLayout(layout)

    def _clear_layout(self, layout):
        """辅助函数，递归清除布局中的控件。"""
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
            else:
                self._clear_layout(item.layout())

    def toggle_collapsed(self, collapsed: bool):
        """外部控制折叠状态。"""
        self.toggle_button.setChecked(not collapsed)
        self._toggle()

# ==============================================================================
# 1. 插件配置管理器 (升级支持 V2 Schema)
# ==============================================================================
class ArchiveConfigManager:
    """管理档案库的配置，包括根目录和受试者字段定义。"""
    def __init__(self, main_window):
        plugin_dir = os.path.dirname(__file__)
        self.config_path = os.path.join(plugin_dir, 'config.json')
        # 默认根目录，优先使用主应用的基础路径，否则为用户主目录下的特定文件夹
        self.default_root = os.path.join(getattr(main_window, 'BASE_PATH', os.path.expanduser("~")), "PhonAcq_Archives")
        self.config = self._load() # 使用 self.config 存储配置

    def _load(self):
        """加载配置文件，如果不存在或无效则创建默认配置。"""
        config = {}
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass # 文件不存在或解析失败，使用默认配置

        # 设置默认值，优先使用 V2 结构
        if "archive_root" not in config:
            config["archive_root"] = self.default_root

        # 如果 V2 结构的 schema 不存在，则创建默认的 V2 结构
        if "participant_schema_v2" not in config:
            config["participant_schema_v2"] = [
                {
                    "group_name": "基本信息",
                    "columns": 2, # 这个组将是双列
                    "collapsible": True,
                    "fields": [
                        {"key": "name", "label": "姓名/代号", "type": "LineEdit", "tooltip": "受试者的姓名或唯一标识代号。"},
                        {"key": "age", "label": "年龄", "type": "LineEdit", "tooltip": ""},
                        {"key": "gender", "label": "性别", "type": "ComboBox", "options": ["", "男", "女", "非二元性别", "倾向于不透露", "其他"]},
                        {"key": "education", "label": "受教育程度", "type": "LineEdit", "tooltip": ""},
                        {"key": "occupation", "label": "职业", "type": "LineEdit", "tooltip": ""},
                        {"key": "tags", "label": "标签", "type": "LineEdit", "tooltip": "为受试者添加分类标签，多个标签请使用英文逗号 (,) 分隔。"},
                    ]
                },
                {
                    "group_name": "语言学背景",
                    "columns": 2, # 这个组也是双列
                    "collapsible": True,
                    "fields": [
                        {"key": "native_language", "label": "母语", "type": "LineEdit", "tooltip": ""},
                        {"key": "dialect", "label": "主要使用方言", "type": "LineEdit", "tooltip": ""},
                        {"key": "other_languages", "label": "其他掌握语言", "type": "TextEdit", "tooltip": "列出受试者掌握的其他语言及其熟练程度。"},
                        {"key": "language_acquisition_environment", "label": "语言习得环境", "type": "TextEdit", "tooltip": "描述受试者在成长过程中的主要语言环境。"},
                    ]
                },
                {
                    "group_name": "其他信息",
                    "columns": 1, # 这个组是单列，因为 TextEdit 适合占满整行
                    "collapsible": True,
                    "fields": [
                        {"key": "health_notes", "label": "健康状况备注", "type": "TextEdit", "tooltip": "记录与研究相关的任何健康状况，如听力、视力等。"},
                        {"key": "general_notes", "label": "综合备注", "type": "TextEdit", "tooltip": "记录其他任何与该受试者相关的信息。"},
                    ]
                }
            ]
            # 移除旧的 schema，强制升级
            config.pop("participant_schema", None)
            self._save_config(config) # 立即保存新的默认V2配置

        return config

    def _save_config(self, config_data):
        """内部方法：保存配置到文件。"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=4, ensure_ascii=False)
        except IOError as e:
            print(f"[Archive Plugin] 无法保存配置文件: {e}")

    def save(self):
        """保存当前配置到文件。"""
        self._save_config(self.config)

    def get_archive_root(self):
        """获取档案库根目录路径。"""
        return self.config.get("archive_root", self.default_root)

    def set_archive_root(self, path):
        """设置档案库根目录路径。"""
        self.config["archive_root"] = path

    def get_participant_schema(self):
        """获取旧版受试者字段定义 (为兼容性保留)。"""
        # 此方法不再被新UI使用，但为确保旧代码不崩溃而保留
        # 它会尝试从V2结构中提取扁平化的字段列表，但不包含分组信息
        schema_v2 = self.get_participant_schema_v2()
        flat_schema = []
        for group in schema_v2:
            for field in group.get("fields", []):
                # 为兼容性，将group信息也添加到扁平化字段中
                field_copy = field.copy()
                field_copy["group"] = group["group_name"]
                flat_schema.append(field_copy)
        return flat_schema

    def save_participant_schema(self, schema):
        """保存旧版受试者字段定义 (为兼容性保留)。"""
        # 此方法不再被新UI使用，实际不会被调用来保存V2结构
        print("警告: save_participant_schema 已废弃，请使用 save_participant_schema_v2。")
        # 简单地将旧的扁平化 schema 转换为一个单列的“默认组”V2结构
        # 这只是一个非常简单的兼容性处理，不推荐在实际中使用
        default_group = {
            "group_name": "默认字段",
            "columns": 1,
            "collapsible": True,
            "fields": schema
        }
        self.save_participant_schema_v2([default_group])

    def get_participant_schema_v2(self):
        """获取 V2 版受试者字段定义。"""
        return self.config.get("participant_schema_v2", [])

    def save_participant_schema_v2(self, schema):
        """保存 V2 版受试者字段定义。"""
        self.config["participant_schema_v2"] = schema
        self.save() # 保存到文件

# ==============================================================================
# 2. 数据处理逻辑层 (无功能变化)
# ==============================================================================
class ArchiveDataManager:
    """处理档案库的文件操作和数据存储。"""
    def __init__(self, root_path):
        self.root_path = root_path
        self.trash_path = os.path.join(self.root_path, ".trash")
        os.makedirs(self.root_path, exist_ok=True)
        os.makedirs(self.trash_path, exist_ok=True)

    def _log_change(self, data, action, user="default_user"):
        """记录数据变更日志。"""
        if "changelog" not in data:
            data["changelog"] = []
        log_entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user": user,
            "action": action
        }
        data["changelog"].insert(0, log_entry) # 最新日志在最前面
        return data

    def load_json(self, *path_parts):
        """加载 JSON 文件。"""
        filepath = os.path.join(self.root_path, *path_parts)
        if not os.path.exists(filepath):
            return {}
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def save_json(self, data, path_parts, action_description):
        """保存 JSON 文件并记录变更。"""
        # 实验锁定检查
        if len(path_parts) > 1 and path_parts[1] != "experiment.json":
            exp_data = self.load_json(path_parts[0], "experiment.json")
            if exp_data.get("is_locked", False):
                return False, "实验已被锁定，无法修改。"

        if action_description:
            data = self._log_change(data, action_description)
        filepath = os.path.join(self.root_path, *path_parts)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            return True, None
        except IOError as e:
            return False, str(e)

    def get_experiments(self):
        """获取所有实验名称。"""
        try:
            return sorted([d for d in os.listdir(self.root_path)
                           if os.path.isdir(os.path.join(self.root_path, d)) and not d.startswith('.')])
        except OSError:
            return []

    def get_participants(self, exp_name):
        """获取指定实验下的所有受试者文件名。"""
        exp_path = os.path.join(self.root_path, exp_name)
        if not os.path.isdir(exp_path):
            return []
        return sorted([f for f in os.listdir(exp_path)
                       if f.startswith("participant_") and f.endswith(".json")])

    def suggest_participant_id(self, experiment_name):
        """建议一个新的受试者ID。"""
        participants = self.get_participants(experiment_name)
        if not participants:
            return "p001"
        max_num = 0
        for p_file in participants:
            try:
                # 提取数字部分，例如从 "participant_p001.json" 得到 "001"
                num = int(p_file[12:-5].replace('p', ''))
            except (ValueError, IndexError):
                continue
            if num > max_num:
                max_num = num
        return f"p{max_num + 1:03d}"

    def _move_to_trash(self, item_path, original_subpath):
        """将文件或文件夹移动到回收站。"""
        if not os.path.exists(item_path):
            return False, "项目不存在"
        
        # 创建一个唯一的文件名，包含时间戳
        trash_item_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{os.path.basename(item_path)}"
        trash_dest = os.path.join(self.trash_path, trash_item_name)
        trash_info_file = f"{trash_dest}.trashinfo" # 存储恢复信息

        try:
            shutil.move(item_path, trash_dest)
            trash_info = {
                "original_path": original_subpath.replace('\\', '/'), # 存储相对路径
                "deleted_by": "default_user" # 记录删除用户
            }
            with open(trash_info_file, 'w', encoding='utf-8') as f:
                json.dump(trash_info, f, indent=4)
            return True, None
        except Exception as e:
            return False, str(e)

    def delete_experiment(self, exp_name):
        """删除一个实验（移动到回收站）。"""
        return self._move_to_trash(os.path.join(self.root_path, exp_name), exp_name)

    def delete_participant(self, exp_name, part_filename):
        """删除一个受试者档案（移动到回收站）。"""
        return self._move_to_trash(os.path.join(self.root_path, exp_name, part_filename),
                                    os.path.join(exp_name, part_filename))

    def get_trashed_items(self):
        """获取回收站中的所有项目。"""
        items = {}
        for f in os.listdir(self.trash_path):
            if f.endswith(".trashinfo"):
                item_name = f[:-10] # 移除 .trashinfo 后缀
                try:
                    with open(os.path.join(self.trash_path, f), 'r', encoding='utf-8') as info_f:
                        info = json.load(info_f)
                        info['type'] = '文件夹' if os.path.isdir(os.path.join(self.trash_path, item_name)) else '文件'
                        items[item_name] = info
                except:
                    continue # 忽略损坏的 .trashinfo 文件
        return items

    def restore_trashed_item(self, item_name):
        """从回收站恢复一个项目。"""
        trash_item_path = os.path.join(self.trash_path, item_name)
        info_path = f"{trash_item_path}.trashinfo"

        if not os.path.exists(info_path):
            return False, "恢复信息丢失"

        with open(info_path, 'r', encoding='utf-8') as f:
            info = json.load(f)
        
        dest_path = os.path.join(self.root_path, info['original_path'])

        if os.path.exists(dest_path):
            return False, "原始位置已存在同名项目"
        
        try:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True) # 确保目标目录存在
            shutil.move(trash_item_path, dest_path)
            os.remove(info_path) # 删除恢复信息文件
            return True, None
        except Exception as e:
            return False, str(e)

    def purge_trashed_item(self, item_name):
        """永久删除回收站中的项目。"""
        trash_item_path = os.path.join(self.trash_path, item_name)
        info_path = f"{trash_item_path}.trashinfo"
        try:
            if os.path.isdir(trash_item_path):
                shutil.rmtree(trash_item_path) # 删除文件夹及其内容
            elif os.path.isfile(trash_item_path):
                os.remove(trash_item_path) # 删除文件
            if os.path.exists(info_path):
                os.remove(info_path) # 删除恢复信息文件
            return True, None
        except Exception as e:
            return False, str(e)

    def toggle_experiment_lock(self, exp_name):
        """切换实验的锁定状态。"""
        data = self.load_json(exp_name, "experiment.json")
        current_state = data.get("is_locked", False)
        data["is_locked"] = not current_state
        action = "锁定实验" if not current_state else "解锁实验"
        return self.save_json(data, (exp_name, "experiment.json"), action)

    def get_archive_summary(self):
        """获取档案库的统计摘要。"""
        summary = {'exp_count': 0, 'part_count': 0, 'session_count': 0, 'recent_items': []}
        all_files = []
        for root, _, files in os.walk(self.root_path):
            if ".trash" in root.split(os.sep): # 排除回收站
                continue
            for name in files:
                if name.endswith(".json"):
                    all_files.append(os.path.join(root, name))
        
        # 按修改时间排序，获取最近的5个文件
        all_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        summary['recent_items'] = [(os.path.relpath(f, self.root_path).replace('\\', '/'), datetime.fromtimestamp(os.path.getmtime(f)).strftime('%Y-%m-%d %H:%M')) for f in all_files[:5]]

        experiments = self.get_experiments()
        summary['exp_count'] = len(experiments)
        for exp in experiments:
            participants = self.get_participants(exp)
            summary['part_count'] += len(participants)
            for part_file in participants:
                data = self.load_json(exp, part_file)
                summary['session_count'] += len(data.get("sessions", []))
        return summary

    def export_participants_to_csv(self, experiment_name, file_path):
        """将指定实验下的所有受试者信息导出为CSV。"""
        if not pd:
            return False, "Pandas 库未安装。请运行 'pip install pandas' 来启用此功能。"
        
        participants = self.get_participants(experiment_name)
        records = []
        for part_file in participants:
            data = self.load_json(experiment_name, part_file)
            record = {'id': data.get('id', '')}
            # 动态获取所有可能的字段
            # 注意: 这里需要一个通用的方式来获取所有字段的key，最好是从schema中获取
            # 暂时先用一个硬编码的列表，或者从第一个受试者中提取所有key
            # 为了通用性，这里可以从 config_manager 获取 schema V2 并遍历所有字段
            # 但为了不引入 config_manager 依赖，我们只导出几个常用字段
            # 实际应用中，这里应该根据 schema V2 动态构建列
            
            # 临时方案：从第一个受试者数据中提取所有键作为列名
            # 更完善的方案是：从 config_manager 获取 schema V2，然后遍历所有字段定义来构建列
            all_keys = set()
            for p_file in participants:
                p_data = self.load_json(experiment_name, p_file)
                all_keys.update(p_data.keys())
            
            # 排除非数据字段
            excluded_keys = {'sessions', 'changelog'}
            export_keys = sorted([k for k in all_keys if k not in excluded_keys])

            for key in export_keys:
                value = data.get(key, '')
                if isinstance(value, list): # 列表类型（如tags）转换为逗号分隔字符串
                    record[key] = ', '.join(value)
                else:
                    record[key] = value
            records.append(record)

        if not records:
            return False, "没有受试者数据可导出。"
        
        df = pd.DataFrame(records)
        try:
            df.to_csv(file_path, index=False, encoding='utf_8_sig') # 确保中文编码
            return True, None
        except Exception as e:
            return False, str(e)

    def copy_participant_to_experiment(self, source_exp, part_filename, dest_exp):
        """复制受试者档案到另一个实验。"""
        dest_data = self.load_json(dest_exp, "experiment.json")
        if dest_data.get("is_locked", False):
            return False, f"目标实验 '{dest_exp}' 已被锁定，无法复制档案。"

        source_path = os.path.join(self.root_path, source_exp, part_filename)
        dest_path = os.path.join(self.root_path, dest_exp, part_filename)

        if os.path.exists(dest_path):
            return False, f"目标实验 '{dest_exp}' 中已存在同名档案 '{part_filename}'。"
        
        try:
            shutil.copy2(source_path, dest_path) # 复制文件（包括元数据）
            copied_data = self.load_json(dest_exp, part_filename)
            self.save_json(copied_data, (dest_exp, part_filename), f"从实验 '{source_exp}' 复制而来")
            return True, None
        except IOError as e:
            return False, str(e)

    def add_session_to_participant(self, experiment_name, participant_id, session_path):
        """为受试者添加新的数据会话。"""
        part_filename = f"participant_{participant_id}.json"
        data = self.load_json(experiment_name, part_filename)
        
        if "sessions" not in data:
            data["sessions"] = []
        
        # 检查是否已存在相同的会话路径
        if any(s.get('path') == session_path for s in data["sessions"]):
            return False, "该数据文件夹已被关联到此受试者档案。"

        new_session = {
            "path": session_path,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "task": "",
            "notes": "",
            "tags": []
        }
        data["sessions"].append(new_session)
        return self.save_json(data, (experiment_name, part_filename), f"添加新会话: {os.path.basename(session_path)}")

    def update_participant_session(self, exp_name, part_id, session_index, session_data):
        """更新受试者会话的信息。"""
        part_filename = f"participant_{part_id}.json"
        data = self.load_json(exp_name, part_filename)
        
        if "sessions" in data and 0 <= session_index < len(data["sessions"]):
            data["sessions"][session_index].update(session_data)
            return self.save_json(data, (exp_name, part_filename), f"更新会话 #{session_index+1} 的信息")
        return False, "会话索引无效或受试者档案不存在。"

    def delete_participant_session(self, exp_name, part_id, session_index):
        """删除受试者会话的关联。"""
        part_filename = f"participant_{part_id}.json"
        data = self.load_json(exp_name, part_filename)
        
        if "sessions" in data and 0 <= session_index < len(data["sessions"]):
            del data["sessions"][session_index]
            return self.save_json(data, (exp_name, part_filename), f"删除会话 #{session_index+1}")
        return False, "会话索引无效或受试者档案不存在。"

    def rename_experiment(self, old_name, new_name):
        """重命名实验文件夹和内部的 experiment.json 文件。"""
        old_path = os.path.join(self.root_path, old_name)
        new_path = os.path.join(self.root_path, new_name)

        if not os.path.exists(old_path):
            return False, "原实验文件夹不存在。"
        if os.path.exists(new_path):
            return False, "新实验名称已存在。"

        try:
            # 移动文件夹
            shutil.move(old_path, new_path)
            
            # 更新内部的 experiment.json 文件（如果存在）
            exp_json_old_path = os.path.join(new_path, "experiment.json")
            if os.path.exists(exp_json_old_path):
                exp_data = self.load_json(new_name, "experiment.json") # 从新路径加载
                # 记录重命名操作
                self.save_json(exp_data, (new_name, "experiment.json"), f"实验从 '{old_name}' 重命名为 '{new_name}'")
            
            return True, None
        except Exception as e:
            # 如果移动失败，尝试回滚
            if os.path.exists(new_path) and not os.path.exists(old_path):
                try:
                    shutil.move(new_path, old_path)
                except Exception as rollback_e:
                    print(f"重命名失败后回滚失败: {rollback_e}")
            return False, str(e)


# --- [新增] 用于编辑组的对话框 ---
class GroupEditDialog(QDialog):
    """一个用于添加或编辑分组的对话框。"""
    def __init__(self, group_data=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑分组")
        self.group_data = group_data if group_data else {}
        
        self.layout = QFormLayout(self)
        
        self.name_edit = QLineEdit(self.group_data.get("group_name", ""))
        self.columns_combo = QComboBox()
        self.columns_combo.addItems(["1 (单列)", "2 (双列)"])
        
        # 设置默认列数
        current_columns = self.group_data.get("columns", 1)
        index = self.columns_combo.findText(f'{current_columns} (', Qt.MatchStartsWith)
        if index != -1:
            self.columns_combo.setCurrentIndex(index)

        self.layout.addRow("分组名称:", self.name_edit)
        self.layout.addRow("布局列数:", self.columns_combo)
        
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.layout.addRow(self.button_box)

    def get_data(self):
        """获取用户输入的组数据。"""
        # 返回更新后的数据，但保留原始的fields列表，因为fields在主对话框中管理
        new_data = {
            "group_name": self.name_edit.text().strip(),
            "columns": int(self.columns_combo.currentText().split(" ")[0]),
            "collapsible": self.group_data.get("collapsible", True), # 保持原有属性
            "fields": self.group_data.get("fields", []) # 字段列表在主对话框中管理
        }
        return new_data

# --- [新增] 字段编辑器对话框 (用于设置对话框内部) ---
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
        self.type_combo.addItems(["LineEdit", "TextEdit", "ComboBox"])
        self.options_edit = QLineEdit()
        self.options_edit.setToolTip("仅当类型为ComboBox时有效，选项用英文逗号分隔。")
        # 移除 group_edit，因为分组信息现在通过树形结构来管理，而不是字段自身
        # self.group_edit = QLineEdit() 
        # self.group_edit.setToolTip("字段所属的分组名称，例如 '基本信息'、'语言学背景'。")

        self.layout.addRow("键名 (Key):", self.key_edit)
        self.layout.addRow("标签 (Label):", self.label_edit)
        self.layout.addRow("类型 (Type):", self.type_combo)
        self.layout.addRow("选项 (Options):", self.options_edit)
        # self.layout.addRow("分组 (Group):", self.group_edit) # 移除分组行

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.layout.addRow(self.button_box)

        if self.field_data:
            self.key_edit.setText(self.field_data.get("key", ""))
            self.label_edit.setText(self.field_data.get("label", ""))
            self.type_combo.setCurrentText(self.field_data.get("type", "LineEdit"))
            self.options_edit.setText(", ".join(self.field_data.get("options", [])))
            # self.group_edit.setText(self.field_data.get("group", "")) # 移除设置分组值
            self.key_edit.setReadOnly(True) # 不允许修改Key

    def get_data(self):
        """获取用户输入的字段数据。"""
        data = {
            "key": self.key_edit.text().strip(),
            "label": self.label_edit.text().strip(),
            "type": self.type_combo.currentText(),
            "options": [opt.strip() for opt in self.options_edit.text().split(',') if opt.strip()],
            # "group": self.group_edit.text().strip() # 移除分组值
        }
        # 确保原始数据中的其他属性（如tooltip）被保留
        for k, v in self.field_data.items():
            if k not in data:
                data[k] = v
        return data

# --- [重大修改] ArchiveSettingsDialog 类 (使用 QTreeWidget 管理分组和字段) ---
class ArchiveSettingsDialog(QDialog):
    """档案库设置对话框，支持配置根目录和受试者字段定义。"""
    def __init__(self, parent):
        super().__init__(parent)
        self.parent_dialog = parent
        self.config_manager = parent.config_manager
        
        self.setWindowTitle("档案库设置")
        self.setMinimumWidth(800)

        main_layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)
        
        self.tabs.addTab(self._create_general_tab(), "常规")
        self.tabs.addTab(self._create_schema_tab(), "表单模板")

        # 注意：这里的按钮盒只管 Save 和 Cancel，字段操作按钮在 Tab 内部
        self.global_button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.global_button_box.accepted.connect(self.save_and_accept)
        self.global_button_box.rejected.connect(self.reject)
        main_layout.addWidget(self.global_button_box)
        
        self._load_settings()

    def _create_general_tab(self):
        """创建常规设置选项卡。"""
        widget = QWidget()
        layout = QFormLayout(widget)
        
        # 根目录设置
        self.root_path_edit = QLineEdit()
        self.root_path_edit.setReadOnly(True)
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._browse_root_path)
        path_layout = QHBoxLayout()
        path_layout.addWidget(self.root_path_edit)
        path_layout.addWidget(browse_btn)
        layout.addRow("档案库根目录:", path_layout)

        # 回收站入口
        self.recycle_bin_btn = QPushButton("打开回收站...")
        self.recycle_bin_btn.setIcon(self.parent_dialog.icon_manager.get_icon("delete"))
        self.recycle_bin_btn.clicked.connect(self._open_recycle_bin_from_settings)
        layout.addRow("数据管理:", self.recycle_bin_btn)

        return widget

    def _create_schema_tab(self):
        """创建受试者字段定义选项卡，使用 QTreeWidget。"""
        widget = QWidget()
        layout = QHBoxLayout(widget)

        # 左侧的树形控件
        self.schema_tree = QTreeWidget()
        self.schema_tree.setHeaderLabels(["字段/分组", "类型"])
        self.schema_tree.setColumnWidth(0, 300)
        self.schema_tree.setStyleSheet("QTreeWidget::item { padding: 8px 2px; }")
        self.schema_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.schema_tree.currentItemChanged.connect(self._update_button_states) # 连接选择变化信号
        # --- [新增] 连接右键菜单和双击信号 ---
        self.schema_tree.customContextMenuRequested.connect(self._show_schema_context_menu)
        self.schema_tree.itemDoubleClicked.connect(self._edit_item)
        # --- [新增结束] ---
        layout.addWidget(self.schema_tree, 1)

        # 右侧的按钮面板 (这部分代码保持不变)
        btn_layout = QVBoxLayout()
        self.add_group_btn = QPushButton("添加分组")
        self.add_group_btn.clicked.connect(self._add_group)
        self.add_field_btn = QPushButton("添加字段")
        self.add_field_btn.clicked.connect(self._add_field)
        self.edit_btn = QPushButton("编辑...")
        self.edit_btn.clicked.connect(self._edit_item)
        self.remove_btn = QPushButton("删除")
        self.remove_btn.clicked.connect(self._remove_item)
        self.up_btn = QPushButton("上移")
        self.up_btn.clicked.connect(lambda: self._move_item(-1))
        self.down_btn = QPushButton("下移")
        self.down_btn.clicked.connect(lambda: self._move_item(1))
        
        btn_layout.addWidget(self.add_group_btn)
        btn_layout.addWidget(self.add_field_btn)
        btn_layout.addSpacing(20)
        btn_layout.addWidget(self.edit_btn)
        btn_layout.addWidget(self.remove_btn)
        btn_layout.addSpacing(20)
        btn_layout.addWidget(self.up_btn)
        btn_layout.addWidget(self.down_btn)
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        return widget
        
    def _load_settings(self):
        """加载配置并填充UI控件。"""
        self.root_path_edit.setText(self.config_manager.get_archive_root())
        
        # 加载新的 V2 Schema 到树中
        self.schema_tree.clear()
        schema_v2 = self.config_manager.get_participant_schema_v2()
        for group_data in schema_v2:
            group_item = QTreeWidgetItem(self.schema_tree)
            group_item.setText(0, f'{group_data["group_name"]}')
            group_item.setText(1, f'{group_data["columns"]} 列布局')
            # 存储原始数据，以便编辑时使用
            group_item.setData(0, Qt.UserRole, {"type": "group", "data": group_data})
            group_item.setFlags(group_item.flags() | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled) # 确保可被选中和拖放
            
            for field_data in group_data.get("fields", []):
                field_item = QTreeWidgetItem(group_item)
                field_item.setText(0, f'{field_data["label"]} ({field_data["key"]})')
                field_item.setText(1, field_data["type"])
                field_item.setData(0, Qt.UserRole, {"type": "field", "data": field_data})
                field_item.setFlags(field_item.flags() | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled) # 确保可被选中和拖放
        self.schema_tree.expandAll() # 默认展开所有组
        self._update_button_states() # 更新按钮状态

    def _add_group(self):
        """添加新分组。"""
        dialog = GroupEditDialog(parent=self)
        if dialog.exec_() == QDialog.Accepted:
            group_data = dialog.get_data()
            # 检查分组名称是否重复
            for i in range(self.schema_tree.topLevelItemCount()):
                item = self.schema_tree.topLevelItem(i)
                existing_group_data = item.data(0, Qt.UserRole)["data"]
                if existing_group_data["group_name"] == group_data["group_name"]:
                    QMessageBox.warning(self, "重复名称", f"分组名称 '{group_data['group_name']}' 已存在，请使用不同的名称。")
                    return

            group_item = QTreeWidgetItem(self.schema_tree)
            group_item.setText(0, f'{group_data["group_name"]}')
            group_item.setText(1, f'{group_data["columns"]} 列布局')
            group_item.setData(0, Qt.UserRole, {"type": "group", "data": group_data})
            group_item.setFlags(group_item.flags() | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            self.schema_tree.setCurrentItem(group_item) # 选中新添加的组
            self.schema_tree.expandItem(group_item) # 展开新组

    def _add_field(self):
        """添加新字段到选定分组。"""
        current_item = self.schema_tree.currentItem()
        if not current_item:
            QMessageBox.warning(self, "操作无效", "请先选择一个分组以添加字段。")
            return
            
        # 找到所属的分组（如果是字段，则找到其父级；如果是组，则就是自身）
        parent_group_item = current_item if not current_item.parent() else current_item.parent()
        if not parent_group_item: return # 不应该发生，但以防万一

        dialog = FieldEditDialog(parent=self)
        if dialog.exec_() == QDialog.Accepted:
            field_data = dialog.get_data()
            if not field_data['key']:
                QMessageBox.warning(self, "错误", "键名(Key)不能为空。")
                return
            
            # 检查字段键名是否在当前组内重复
            for i in range(parent_group_item.childCount()):
                child_item = parent_group_item.child(i)
                existing_field_data = child_item.data(0, Qt.UserRole)["data"]
                if existing_field_data["key"] == field_data["key"]:
                    QMessageBox.warning(self, "重复键名", f"键名 '{field_data['key']}' 在当前分组中已存在，请使用不同的键名。")
                    return

            field_item = QTreeWidgetItem(parent_group_item)
            field_item.setText(0, f'{field_data["label"]} ({field_data["key"]})')
            field_item.setText(1, field_data["type"])
            field_item.setData(0, Qt.UserRole, {"type": "field", "data": field_data})
            field_item.setFlags(field_item.flags() | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
            self.schema_tree.setCurrentItem(field_item) # 选中新添加的字段
            parent_group_item.setExpanded(True) # 确保父组展开

    def _edit_item(self):
        """编辑选中的分组或字段。"""
        item = self.schema_tree.currentItem()
        if not item: return

        item_info = item.data(0, Qt.UserRole)
        item_type = item_info.get("type")
        item_data = item_info.get("data")

        if item_type == "group":
            dialog = GroupEditDialog(item_data, self)
            if dialog.exec_() == QDialog.Accepted:
                new_data = dialog.get_data()
                # ... (检查重复名称的代码，保持不变) ...
                for i in range(self.schema_tree.topLevelItemCount()):
                    existing_item = self.schema_tree.topLevelItem(i)
                    if existing_item is not item:
                        existing_group_data = existing_item.data(0, Qt.UserRole)["data"]
                        if existing_group_data["group_name"] == new_data["group_name"]:
                            QMessageBox.warning(self, "重复名称", f"分组名称 '{new_data['group_name']}' 已存在，请使用不同的名称。")
                            return

                # **[修改]** 使用 setData 替换整个数据载荷
                item.setData(0, Qt.UserRole, {"type": "group", "data": new_data})
                item.setText(0, f'{new_data["group_name"]}')
                item.setText(1, f'{new_data["columns"]} 列布局')

        elif item_type == "field":
            dialog = FieldEditDialog(item_data, self)
            if dialog.exec_() == QDialog.Accepted:
                new_data = dialog.get_data()
                # ... (检查重复键名的代码，保持不变) ...
                parent_group_item = item.parent()
                if parent_group_item:
                    for i in range(parent_group_item.childCount()):
                        child_item = parent_group_item.child(i)
                        if child_item is not item:
                            existing_field_data = child_item.data(0, Qt.UserRole)["data"]
                            if existing_field_data["key"] == new_data["key"]:
                                QMessageBox.warning(self, "重复键名", f"键名 '{new_data['key']}' 在当前分组中已存在，请使用不同的键名。")
                                return

                # **[修改]** 使用 setData 替换整个数据载荷
                item.setData(0, Qt.UserRole, {"type": "field", "data": new_data})
                item.setText(0, f'{new_data["label"]} ({new_data["key"]})')
                item.setText(1, new_data["type"])

    def _show_schema_context_menu(self, position):
        """在树形控件上显示右键上下文菜单。"""
        item = self.schema_tree.itemAt(position)
        if not item:
            return

        menu = QMenu(self)
        icon_manager = self.parent_dialog.icon_manager

        # 编辑操作 (图标: draw)
        action_edit = menu.addAction(icon_manager.get_icon("draw"), "编辑...")
        action_edit.triggered.connect(self._edit_item)
        action_edit.setEnabled(self.edit_btn.isEnabled()) # 复用按钮的启用状态

        # 删除操作 (图标: clear_contents)
        action_remove = menu.addAction(icon_manager.get_icon("clear_contents"), "删除")
        action_remove.triggered.connect(self._remove_item)
        action_remove.setEnabled(self.remove_btn.isEnabled())

        menu.addSeparator()

        # 上移操作 (图标: move_up)
        action_up = menu.addAction(icon_manager.get_icon("move_up"), "上移")
        action_up.triggered.connect(lambda: self._move_item(-1))
        action_up.setEnabled(self.up_btn.isEnabled())
        
        # 下移操作 (图标: move_down)
        action_down = menu.addAction(icon_manager.get_icon("move_down"), "下移")
        action_down.triggered.connect(lambda: self._move_item(1))
        action_down.setEnabled(self.down_btn.isEnabled())

        menu.exec_(self.schema_tree.mapToGlobal(position))

    def _remove_item(self):
        """删除选中的分组或字段。"""
        item = self.schema_tree.currentItem()
        if not item: return

        item_info = item.data(0, Qt.UserRole)
        item_type = item_info.get("type")

        reply = QMessageBox.question(self, "确认删除", f"您确定要删除选中的 '{item.text(0)}' 吗？" +
                                     ("这将同时删除该分组下的所有字段！" if item_type == "group" else ""),
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            parent = item.parent()
            if parent:
                parent.removeChild(item) # 删除子项
            else:
                self.schema_tree.takeTopLevelItem(self.schema_tree.indexOfTopLevelItem(item)) # 删除顶级项
            self._update_button_states() # 更新按钮状态

    def _move_item(self, direction):
        """上移或下移选中的项目。"""
        item = self.schema_tree.currentItem()
        if not item: return

        parent = item.parent()
        if parent: # 移动子项 (字段)
            index = parent.indexOfChild(item)
            if direction == -1 and index > 0: # 上移
                parent.takeChild(index)
                parent.insertChild(index - 1, item)
            elif direction == 1 and index < parent.childCount() - 1: # 下移
                parent.takeChild(index)
                parent.insertChild(index + 1, item)
        else: # 移动顶级项 (分组)
            index = self.schema_tree.indexOfTopLevelItem(item)
            if direction == -1 and index > 0: # 上移
                self.schema_tree.takeTopLevelItem(index)
                self.schema_tree.insertTopLevelItem(index - 1, item)
            elif direction == 1 and index < self.schema_tree.topLevelItemCount() - 1: # 下移
                self.schema_tree.takeTopLevelItem(index)
                self.schema_tree.insertTopLevelItem(index + 1, item)
        
        self.schema_tree.setCurrentItem(item) # 保持选中状态
        self._update_button_states() # 更新按钮状态

    def save_and_accept(self):
        """保存所有设置并接受对话框。"""
        # 1. 保存常规设置
        new_root = self.root_path_edit.text()
        if new_root != self.config_manager.get_archive_root():
            self.config_manager.set_archive_root(new_root)
            # 通知主对话框根目录已更改，需要刷新
            self.parent_dialog.on_settings_changed()

        # 2. 从树构建新的 V2 Schema
        new_schema_v2 = []
        for i in range(self.schema_tree.topLevelItemCount()):
            group_item = self.schema_tree.topLevelItem(i)
            # 获取原始存储的组数据，并更新其字段列表
            group_data = group_item.data(0, Qt.UserRole)["data"].copy() # 复制一份，避免直接修改引用
            group_data["fields"] = [] # 清空旧字段，从子节点重新填充
            
            for j in range(group_item.childCount()):
                field_item = group_item.child(j)
                field_data = field_item.data(0, Qt.UserRole)["data"]
                group_data["fields"].append(field_data)
            
            new_schema_v2.append(group_data)
            
        self.config_manager.save_participant_schema_v2(new_schema_v2)
        
        # 通知主对话框 schema 已更改，需要重新构建表单
        self.parent_dialog.on_participant_schema_changed()
        
        self.accept()

    def _update_button_states(self, current_item=None, previous_item=None): # Modified to accept signal arguments
        """根据当前选中项更新按钮的启用/禁用状态。"""
        item = self.schema_tree.currentItem() # Still get current item directly
        is_item_selected = item is not None
        is_group_selected = is_item_selected and item.parent() is None
        is_field_selected = is_item_selected and item.parent() is not None

        self.add_field_btn.setEnabled(is_group_selected or is_field_selected) # 选中组或字段都可以添加字段
        self.edit_btn.setEnabled(is_item_selected)
        self.remove_btn.setEnabled(is_item_selected)

        # 移动按钮的状态判断更复杂一些
        if is_item_selected:
            if is_group_selected: # 顶级组
                index = self.schema_tree.indexOfTopLevelItem(item)
                self.up_btn.setEnabled(index > 0)
                self.down_btn.setEnabled(index < self.schema_tree.topLevelItemCount() - 1)
            else: # 子级字段
                parent = item.parent()
                index = parent.indexOfChild(item)
                self.up_btn.setEnabled(index > 0)
                self.down_btn.setEnabled(index < parent.childCount() - 1)
        else:
            self.up_btn.setEnabled(False)
            self.down_btn.setEnabled(False)

    def _browse_root_path(self):
        """打开文件对话框让用户选择档案库根目录。"""
        path = QFileDialog.getExistingDirectory(self, "选择档案库根目录", self.root_path_edit.text())
        if path:
            self.root_path_edit.setText(path)

    def _open_recycle_bin_from_settings(self):
        """从设置对话框中打开回收站对话框。"""
        # 以当前设置对话框作为父窗口来打开回收站对话框
        recycle_bin_dialog = RecycleBinDialog(self.parent_dialog) # 父窗口是 ArchiveDialog
        # 使用 exec_() 模态显示回收站对话框，阻塞当前设置对话框，直到回收站关闭
        if recycle_bin_dialog.exec_() == QDialog.Accepted:
            # 如果回收站中发生了恢复操作，通知主对话框刷新仪表盘和列表
            self.parent_dialog._update_dashboard(lazy=True)
            # 这里的刷新不需要在这里再具体指定加载哪个列表，因为设置对话框关闭后，
            # ArchiveDialog.on_settings_clicked 会重新加载 dashboard 并根据当前状态加载对应列表

# ==============================================================================
# 3. UI 对话框 (v4.9 - 标准化布局 & 动态表单)
# ==============================================================================
class ArchiveDialog(QDialog):
    """主档案库管理对话框。"""
    def __init__(self, main_window, config_manager):
        super().__init__(main_window)
        self.main_window = main_window
        self.config_manager = config_manager
        self.icon_manager = main_window.icon_manager # 假设 main_window 有 icon_manager
        self.data_manager = ArchiveDataManager(self.config_manager.get_archive_root())
        
        self.current_view = 'dashboard' # 当前视图：dashboard, experiments, participants, sessions
        self.current_experiment = None
        self.current_participant_id = None
        self.current_selected_item_name = None # 当前列表中选中的项目名称
        self.is_current_exp_locked = False # 当前实验是否锁定

        self.participant_widgets = {} # 用于存储动态创建的受试者表单控件的引用
        
        self.setWindowTitle("档案库")
        self.resize(1200, 800)
        self.setMinimumSize(1100, 700)
        
        self._init_ui()
        self._connect_signals()
        self.load_dashboard() # 初始加载仪表盘

    def _init_ui(self):
        """初始化用户界面布局。"""
        main_layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        
        # --- 左侧面板：列表区 ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_panel.setFixedWidth(400) # 设置一个较窄的固定宽度
        
        # 搜索框
        search_widget = QWidget()
        search_layout = QHBoxLayout(search_widget)
        search_layout.setContentsMargins(0,0,0,0)
        search_layout.setSpacing(0)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("在当前列表中快速筛选...")
        self.search_box.setToolTip("输入关键词（如实验名、受试者ID）以筛选右侧列表。")
        self.clear_search_btn = QPushButton("×")
        self.clear_search_btn.setFixedSize(QSize(24,24))
        self.clear_search_btn.setToolTip("清空上方的搜索框，并显示列表中的所有项目。")
        self.clear_search_btn.setVisible(False)
        self.clear_search_btn.setStyleSheet("QPushButton { border: none; font-size: 16px; background-color: transparent; }")
        search_layout.addWidget(self.search_box)
        search_layout.addWidget(self.clear_search_btn)
        
        # 导航标签和返回按钮
        self.nav_label = QLabel()
        self.nav_label.setObjectName("SubheaderLabel") # 自定义样式名
        self.back_btn = QPushButton(" 返回")
        self.back_btn.setIcon(self.icon_manager.get_icon("prev"))
        self.back_btn.setToolTip("返回到上一级视图。\n例如，从受试者列表返回到仪表盘。")
        nav_layout = QHBoxLayout()
        nav_layout.addWidget(self.back_btn)
        nav_layout.addWidget(self.nav_label, 1)

        # 项目列表
        self.item_list = QListWidget()
        self.item_list.setSpacing(2)
        self.item_list.setContextMenuPolicy(Qt.CustomContextMenu) # 启用右键菜单
        
        # 底部按钮区
        btn_layout = QHBoxLayout()
        self.settings_btn = QPushButton()
        self.settings_btn = QPushButton(self.icon_manager.get_icon("settings"), " 设置")
        self.settings_btn.setToolTip("打开档案库设置（根目录、字段定义、回收站）")
        
        self.export_csv_btn = QPushButton("导出")
        self.export_csv_btn.setIcon(self.icon_manager.get_icon("export"))
        self.export_csv_btn.setToolTip("将当前实验下的所有受试者信息汇总并导出为一个CSV表格文件。")
        self.export_csv_btn.setVisible(False) # 默认隐藏

        self.new_experiment_btn = QPushButton("新建实验")
        self.new_experiment_btn.setToolTip("在档案库中创建一个新的实验项目文件夹。")
        
        self.new_participant_btn = QPushButton("新建受试者")
        self.new_participant_btn.setToolTip("在当前选中的实验下，创建一个新的受试者档案。")
        
        self.add_session_btn = QPushButton("关联会话")
        self.add_session_btn.setToolTip("为当前选中的受试者，关联一个包含实验数据的文件夹。")
        
        btn_layout.addWidget(self.settings_btn)
        btn_layout.addStretch() # 将按钮推到右侧
        btn_layout.addWidget(self.export_csv_btn)
        btn_layout.addWidget(self.new_experiment_btn)
        btn_layout.addWidget(self.new_participant_btn)
        btn_layout.addWidget(self.add_session_btn)
        
        left_layout.addWidget(search_widget)
        left_layout.addLayout(nav_layout)
        left_layout.addWidget(self.item_list, 1) # 列表占据剩余垂直空间
        left_layout.addLayout(btn_layout)


        # --- 右侧面板：详情区 (使用 QStackedWidget 管理不同视图的表单) ---
        right_panel_scroll = QScrollArea()
        right_panel_scroll.setWidgetResizable(True) # 允许内部控件调整大小
        right_panel_scroll.setFrameShape(QFrame.NoFrame) # 无边框
        
        self.form_stack = QStackedWidget()
        right_panel_scroll.setWidget(self.form_stack)
        
        # 添加不同的表单页面到堆栈
        self.form_stack.addWidget(self._create_dashboard_form())
        self.form_stack.addWidget(self._create_experiment_form())
        self.form_stack.addWidget(self._create_participant_form())
        self.form_stack.addWidget(self._create_session_form())
        
        
        # --- 将面板添加到 Splitter ---
        splitter.addWidget(left_panel) # 左边是列表
        splitter.addWidget(right_panel_scroll) # 右边是详情
        splitter.setSizes([400, 800]) # 设定初始宽度比例
        
        main_layout.addWidget(splitter)
        
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
        
        # 表单保存按钮的连接
        self.exp_save_btn.clicked.connect(self.on_save_experiment) 
        # self.part_save_btn.clicked.connect(self.on_save_participant) # 此连接已在 _create_participant_form 中完成
        self.session_save_btn.clicked.connect(self.on_save_session) 
        
        self.export_csv_btn.clicked.connect(self.on_export_to_csv)
        
        self.search_box.textChanged.connect(self._filter_list)
        self.clear_search_btn.clicked.connect(self.search_box.clear)

    def _create_dashboard_form(self):
        """创建仪表盘视图的表单。"""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20,20,20,20) # 内部边距
        
        title = QLabel("档案库仪表盘")
        title.setObjectName("FormTitleLabel") # 自定义样式名
        
        stats_layout = QHBoxLayout()
        self.exp_count_label = self._create_stat_box("0", "实验项目")
        self.part_count_label = self._create_stat_box("0", "受试者档案")
        self.session_count_label = self._create_stat_box("0", "数据会话")
        stats_layout.addWidget(self.exp_count_label)
        stats_layout.addWidget(self.part_count_label)
        stats_layout.addWidget(self.session_count_label)
        
        recent_group = QGroupBox("最近修改")
        recent_layout = QVBoxLayout(recent_group)
        self.recent_files_list = QListWidget()
        self.recent_files_list.setSelectionMode(QAbstractItemView.NoSelection) # 不可选中
        self.recent_files_list.setToolTip("最近被修改过的5个档案文件")
        recent_layout.addWidget(self.recent_files_list)
        
        layout.addWidget(title)
        layout.addLayout(stats_layout)
        layout.addWidget(recent_group, 1) # 最近修改列表占据剩余空间
        
        return w

    def _create_stat_box(self, number, text):
        """创建用于显示统计数据的盒子。"""
        box = QLabel(f"<div style='text-align:center;'><p style='font-size:24px; font-weight:bold; margin:0;'>{number}</p><p style='font-size:12px; color:grey;'>{text}</p></div>")
        box.setFrameShape(QFrame.StyledPanel) # 边框样式
        box.setMinimumHeight(80)
        box.setToolTip(f"当前档案库中总的{text}数量")
        return box
        
    def _create_changelog_table(self):
        """创建用于显示变更历史的表格。"""
        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["时间", "用户", "操作"])
        # 设置列宽调整模式
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch) # 最后一列伸展
        table.setEditTriggers(QAbstractItemView.NoEditTriggers) # 不可编辑
        table.setSelectionBehavior(QAbstractItemView.SelectRows) # 整行选中
        table.setToolTip("记录此档案的所有修改历史")
        return table

    def _create_experiment_form(self):
        """创建实验详情视图的表单。"""
        page_widget = QWidget()
        page_layout = QVBoxLayout(page_widget)
        page_layout.setContentsMargins(10, 5, 10, 5) # 主布局边距
        page_layout.setSpacing(10) # 组件间距

        self.exp_form_label = QLabel()
        self.exp_form_label.setObjectName("FormTitleLabel")
        page_layout.addWidget(self.exp_form_label)
        
        # 实验详情 (CollapsibleBox)
        details_box = CollapsibleBox("实验详情")
        details_f_layout = QFormLayout()
        self.exp_researcher_edit = QLineEdit()
        self.exp_date_edit = QLineEdit()
        self.exp_purpose_text = QTextEdit()
        details_f_layout.addRow("研究员:", self.exp_researcher_edit)
        details_f_layout.addRow("创建日期:", self.exp_date_edit)
        details_f_layout.addRow("研究目的/备注:", self.exp_purpose_text)
        details_box.setContentLayout(details_f_layout)
        page_layout.addWidget(details_box)

        # 变更历史 (CollapsibleBox)
        log_box = CollapsibleBox("变更历史")
        log_layout = QVBoxLayout()
        self.exp_changelog_table = self._create_changelog_table()
        log_layout.addWidget(self.exp_changelog_table)
        log_box.setContentLayout(log_layout)
        page_layout.addWidget(log_box)
        
        # 保存按钮
        self.exp_save_btn = QPushButton("保存实验信息")
        self.exp_save_btn.setToolTip("保存对以上实验信息的修改")
        page_layout.addWidget(self.exp_save_btn)

        page_layout.addStretch(1) # 填充剩余空间，将内容推到顶部

        return page_widget

    def _create_participant_form(self):
        """创建受试者详情视图的表单。"""
        w = QWidget()
        main_layout = QVBoxLayout(w)
        main_layout.setContentsMargins(10,5,10,5)
        main_layout.setSpacing(10) # 增加组件之间的间距

        self.part_form_label = QLabel()
        self.part_form_label.setObjectName("FormTitleLabel")
        main_layout.addWidget(self.part_form_label)

        # 动态字段容器 (此容器内部的内容由 _build_dynamic_participant_form 填充)
        self.participant_dynamic_fields_container = QWidget()
        self.participant_dynamic_fields_layout = QVBoxLayout(self.participant_dynamic_fields_container)
        self.participant_dynamic_fields_layout.setContentsMargins(0,0,0,0) # 内部布局的边距
        self.participant_dynamic_fields_layout.setSpacing(10) # 可折叠框之间的间距

        main_layout.addWidget(self.participant_dynamic_fields_container)

        # 变更历史 (CollapsibleBox)
        self.part_changelog_box = CollapsibleBox("变更历史")
        part_changelog_inner_layout = QVBoxLayout()
        self.part_changelog_table = self._create_changelog_table()
        part_changelog_inner_layout.addWidget(self.part_changelog_table)
        self.part_changelog_box.setContentLayout(part_changelog_inner_layout)
        main_layout.addWidget(self.part_changelog_box)

        main_layout.addStretch(1) # 放置一个有伸缩性的空间，将内容推到顶部

        # 保存按钮 (此按钮的信号在创建时已连接，避免重复)
        self.part_save_btn = QPushButton("保存受试者档案")
        self.part_save_btn.setToolTip("将当前表单中对受试者信息的所有修改进行保存。")
        self.part_save_btn.clicked.connect(self.on_save_participant) 
        main_layout.addWidget(self.part_save_btn)

        # 首次调用以构建动态表单内容
        self._build_dynamic_participant_form() 

        return w

    def _build_dynamic_participant_form(self):
        """
        根据配置管理器中的 V2 Schema 动态构建受试者表单。
        支持分组、单列或双列布局，并处理 TextEdit 控件的跨列显示。
        """
        # 清理旧的动态字段 widgets 和它们的 CollapsibleBox
        while self.participant_dynamic_fields_layout.count():
            item = self.participant_dynamic_fields_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater() # 删除 CollapsibleBox
            elif item.layout(): # 如果直接添加了布局（此处不太可能）
                self._clear_layout(item.layout())
                item.layout().deleteLater()
            
        self.participant_widgets.clear() # 清空存储的控件引用，准备重新填充

        schema_v2 = self.config_manager.get_participant_schema_v2()
        if not schema_v2:
            self.participant_dynamic_fields_layout.addWidget(QLabel("没有定义受试者字段。\n请在“设置”中进行配置。"))
            return

        # 遍历每个“分组”
        for group_data in schema_v2:
            group_name = group_data.get("group_name", "未命名分组")
            collapsible_box = CollapsibleBox(group_name)
            
            # --- 核心逻辑：根据列数选择布局 ---
            columns = group_data.get("columns", 1)
            fields = group_data.get("fields", [])
            
            if columns == 2:
                # 使用 QGridLayout 实现双列
                grid_layout = QGridLayout()
                grid_layout.setColumnStretch(1, 1) # 第一个字段列可伸展
                grid_layout.setColumnStretch(3, 1) # 第二个字段列可伸展
                grid_layout.setHorizontalSpacing(20)   # 列间距 # Fixed: Changed from setColumnSpacing
                
                row, col = 0, 0
                for field in fields:
                    widget = self._create_widget_for_field(field)
                    if widget:
                        label = QLabel(f'{field.get("label", field["key"])}:')
                        
                        # QTextEdit 这种大控件应该横跨两列
                        if field["type"] == "TextEdit":
                            if col == 1: # 如果当前在第二列，先换行
                                row += 1
                                col = 0
                            # 占据 1 行 4 列 (标签 + 两个字段空间)
                            grid_layout.addWidget(label, row, 0)
                            grid_layout.addWidget(widget, row, 1, 1, 3) 
                            row += 1 # 占满一行后，强制换行
                            col = 0 # 重置列索引
                        else:
                            grid_layout.addWidget(label, row, col * 2)
                            grid_layout.addWidget(widget, row, col * 2 + 1)
                            col += 1
                            if col >= 2: # 达到双列限制，换行
                                col = 0
                                row += 1
                
                collapsible_box.setContentLayout(grid_layout)

            else: # 默认或 columns == 1
                # 使用 QFormLayout 实现单列
                form_layout = QFormLayout()
                for field in fields:
                    widget = self._create_widget_for_field(field)
                    if widget:
                        label = QLabel(f'{field.get("label", field["key"])}:')
                        form_layout.addRow(label, widget)
                
                collapsible_box.setContentLayout(form_layout)
            
            self.participant_dynamic_fields_layout.addWidget(collapsible_box)
            if not group_data.get("collapsible", True): # 兼容未来可能有的不可折叠组
                collapsible_box.toggle_collapsed(False)

    def _create_widget_for_field(self, field_data):
        """辅助函数，根据字段定义创建对应的 QWidget 控件。"""
        key = field_data.get("key")
        field_type = field_data.get("type", "LineEdit")
        tooltip = field_data.get("tooltip", "")

        widget = None
        if field_type == "TextEdit":
            widget = QTextEdit()
            widget.setMinimumHeight(80) # 文本框高度
        elif field_type == "ComboBox":
            widget = QComboBox()
            widget.addItems(field_data.get("options", []))
        else: # 默认为 LineEdit
            widget = QLineEdit()

        if widget:
            widget.setToolTip(tooltip)
            self.participant_widgets[key] = widget # 存储控件引用
        return widget

    def _create_session_form(self):
        """创建会话详情视图的表单。"""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(10,5,10,5)
        layout.setSpacing(10) # 增加组件之间的间距

        self.session_form_label = QLabel()
        self.session_form_label.setObjectName("FormTitleLabel")
        layout.addWidget(self.session_form_label)

        # 会话详情 (QGroupBox)
        g = QGroupBox("会话详情")
        f = QFormLayout(g)
        self.session_path_edit = QLineEdit()
        self.session_path_edit.setReadOnly(True) # 路径只读
        self.session_date_edit = QLineEdit()
        self.session_task_edit = QLineEdit()
        self.session_notes_text = QTextEdit()
        self.session_tags_edit = QLineEdit()
        self.session_tags_edit.setToolTip("使用逗号分隔多个标签")
        f.addRow("数据文件夹:", self.session_path_edit)
        f.addRow("采集日期:", self.session_date_edit)
        f.addRow("采集任务类型:", self.session_task_edit)
        f.addRow("会话备注:", self.session_notes_text)
        f.addRow("标签:", self.session_tags_edit)
        layout.addWidget(g) # 直接添加 GroupBox

        layout.addStretch(1) # 放置一个有伸缩性的空间

        self.session_save_btn = QPushButton("保存会话信息")
        layout.addWidget(self.session_save_btn)
        return w

    def _populate_changelog_table(self, table, log_data):
        """填充变更历史表格。"""
        table.setRowCount(0)
        for entry in log_data:
            row_pos = table.rowCount()
            table.insertRow(row_pos)
            table.setItem(row_pos, 0, QTableWidgetItem(entry.get("timestamp", "")))
            table.setItem(row_pos, 1, QTableWidgetItem(entry.get("user", "")))
            table.setItem(row_pos, 2, QTableWidgetItem(entry.get("action", "")))

    def _update_view_state(self, view, experiment=None, participant_id=None):
        """更新当前视图状态和UI元素的可见性。"""
        self.item_list.clear() # 清空列表
        self.search_box.clear() # 清空搜索框
        self.current_view = view
        self.current_experiment = experiment
        self.current_participant_id = participant_id
        
        is_dash = view == 'dashboard'
        is_exp = view == 'experiments'
        is_part = view == 'participants'
        is_sess = view == 'sessions'
        
        # 切换堆栈页面
        if is_dash: self.form_stack.setCurrentIndex(0)
        elif is_exp: self.form_stack.setCurrentIndex(1)
        elif is_part: self.form_stack.setCurrentIndex(2)
        elif is_sess: self.form_stack.setCurrentIndex(3)
        
        self.back_btn.setVisible(not is_dash) # 仪表盘不显示返回按钮
        self.nav_label.setText("仪表盘" if is_dash else "实验列表" if is_exp else f"实验: {experiment}" if is_part else f"受试者: {participant_id}")
        
        self.new_experiment_btn.setVisible(is_exp or is_dash) # 在仪表盘和实验列表显示新建实验
        self.export_csv_btn.setVisible(is_part) # 仅在受试者列表显示导出CSV
        self.new_participant_btn.setVisible(is_part) # 仅在受试者列表显示新建受试者
        self.add_session_btn.setVisible(is_sess) # 仅在会话列表显示关联会话
        
        # 更新当前实验的锁定状态
        self.is_current_exp_locked = self.data_manager.load_json(experiment, "experiment.json").get("is_locked", False) if experiment else False
        self._update_form_lock_state() # 根据锁定状态更新表单控件

    def _update_form_lock_state(self):
        """根据当前实验的锁定状态启用/禁用表单控件和按钮。"""
        locked = self.is_current_exp_locked
        
        # 定义需要被禁用的输入控件类型
        widgets_to_disable = (QLineEdit, QTextEdit, QComboBox)
 
        # 1. 禁用实验信息表单中的输入控件
        exp_form = self.form_stack.widget(1)
        for widget in exp_form.findChildren(widgets_to_disable):
            widget.setDisabled(locked)
        self.exp_save_btn.setDisabled(locked) # 单独控制保存按钮
 
        # 2. 禁用受试者信息表单中的输入控件
        part_form = self.form_stack.widget(2)
        # 遍历所有动态创建的受试者字段控件
        for key, widget in self.participant_widgets.items():
            if isinstance(widget, widgets_to_disable):
                widget.setDisabled(locked)
        self.part_save_btn.setDisabled(locked) # 单独控制保存按钮
 
        # 3. 禁用会话信息表单中的输入控件
        session_form = self.form_stack.widget(3)
        for widget in session_form.findChildren(widgets_to_disable):
            # 特例：路径是只读的，永远不禁用
            if widget is not self.session_path_edit:
                widget.setDisabled(locked)
        self.session_save_btn.setDisabled(locked) # 单独控制保存按钮
 
        # 4. 禁用新建和添加按钮
        self.new_participant_btn.setDisabled(locked)
        self.add_session_btn.setDisabled(locked)
        
        # 提示信息
        if locked:
            self.new_participant_btn.setToolTip("实验已锁定，无法添加新的受试者。")
            self.add_session_btn.setToolTip("实验已锁定，无法关联新的会话。")
        else:
            self.new_participant_btn.setToolTip("在当前选中的实验下，创建一个新的受试者档案。")
            self.add_session_btn.setToolTip("为当前选中的受试者，关联一个包含实验数据的文件夹。")


    def _filter_list(self):
        """根据搜索框内容筛选列表项。"""
        query = self.search_box.text().lower()
        self.clear_search_btn.setVisible(bool(query)) # 有内容时显示清空按钮
        for i in range(self.item_list.count()):
            item = self.item_list.item(i)
            item.setHidden(query not in item.text().lower())

    def _add_placeholder_item(self, text):
        """在列表为空时添加一个提示项。"""
        item = QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags) # 不可选中，不可交互
        self.item_list.addItem(item)

    def _is_item_valid(self, item):
        """检查列表项是否有效（非占位符）。"""
        return item and item.flags() & Qt.ItemIsEnabled

    def _clear_forms(self):
        """清空所有表单（除仪表盘外）的内容。"""
        # 实验表单
        exp_form = self.form_stack.widget(1)
        for w in exp_form.findChildren((QLineEdit, QTextEdit)): w.clear()
        for w in exp_form.findChildren(QTableWidget): w.setRowCount(0)
        
        # 受试者表单 (调用专门的清理方法)
        self._clear_participant_form()

        # 会话表单
        session_form = self.form_stack.widget(3)
        for w in session_form.findChildren((QLineEdit, QTextEdit)): w.clear()
        for w in session_form.findChildren(QComboBox): w.setCurrentIndex(0)

    def _update_dashboard(self, lazy=False):
        """更新仪表盘数据。"""
        if lazy and self.current_view != 'dashboard':
            return # 懒惰更新：如果不在仪表盘视图，则不刷新
        
        summary = self.data_manager.get_archive_summary()
        self.exp_count_label.setText(f"<div style='text-align:center;'><p style='font-size:24px; font-weight:bold; margin:0;'>{summary['exp_count']}</p><p style='font-size:12px; color:grey;'>实验项目</p></div>")
        self.part_count_label.setText(f"<div style='text-align:center;'><p style='font-size:24px; font-weight:bold; margin:0;'>{summary['part_count']}</p><p style='font-size:12px; color:grey;'>受试者档案</p></div>")
        self.session_count_label.setText(f"<div style='text-align:center;'><p style='font-size:24px; font-weight:bold; margin:0;'>{summary['session_count']}</p><p style='font-size:12px; color:grey;'>数据会话</p></div>")
        
        self.recent_files_list.clear()
        for path, time in summary['recent_items']:
            self.recent_files_list.addItem(f"{path}\n  └ {time}")

    def load_dashboard(self):
        """加载仪表盘视图。"""
        self._update_view_state('dashboard')
        self._update_dashboard()
        self.item_list.addItems(self.data_manager.get_experiments()) # 仪表盘列表显示实验

    def load_experiment_list(self):
        """加载实验列表视图。"""
        self._update_view_state('experiments')
        experiments = self.data_manager.get_experiments()
        if not experiments:
            self._add_placeholder_item("未找到任何实验项目。点击“新建实验”开始。")
            return
        for name in experiments:
            item = QListWidgetItem(name)
            # 根据锁定状态添加图标
            if self.data_manager.load_json(name, "experiment.json").get("is_locked", False):
                item.setIcon(self.icon_manager.get_icon("lock"))
            self.item_list.addItem(item)

    def load_participant_list(self, experiment_name):
        """加载受试者列表视图。"""
        self.current_experiment = experiment_name
        self._update_view_state('participants', experiment=experiment_name)
        
        exp_data = self.data_manager.load_json(experiment_name, "experiment.json")
        is_locked = exp_data.get("is_locked", False)
        lock_icon_text = " (🔒 已锁定)" if is_locked else ""
        
        self.nav_label.setText(f"<b>实验:</b> {experiment_name}{lock_icon_text}")
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

        # 自动选中并加载列表中的第一个受试者
        if self.item_list.count() > 0:
            self.item_list.setCurrentRow(0)
        else:
            self._clear_participant_form() # 如果实验为空，则清空表单

    def load_session_list(self, exp_name, part_id):
        """加载会话列表视图。"""
        self._update_view_state('sessions', experiment=exp_name, participant_id=part_id)
        sessions = self.data_manager.load_json(exp_name, f"participant_{part_id}.json").get("sessions", [])
        if not sessions:
            self._add_placeholder_item("该受试者无关联数据会话。点击“关联会话”添加。")
            return
        for i, session in enumerate(sessions):
            display_name = os.path.basename(session.get("path", "未知路径"))
            item = QListWidgetItem(f"会话 {i+1}: {display_name}")
            item.setData(Qt.UserRole, i) # 存储会话索引
            item.setToolTip(session.get("path", "无路径信息"))
            self.item_list.addItem(item)

    def on_back_clicked(self):
        """处理返回按钮点击事件。"""
        if self.current_view == 'sessions':
            self.load_participant_list(self.current_experiment)
        elif self.current_view == 'participants':
            self.load_experiment_list() # 从受试者列表返回到实验列表
        elif self.current_view == 'experiments':
            self.load_dashboard() # 从实验列表返回到仪表盘

    def on_item_double_clicked(self, item):
        """处理列表项双击事件（进入下一级视图或打开文件夹）。"""
        if not self._is_item_valid(item): return
        
        if self.current_view == 'dashboard' or self.current_view == 'experiments':
            self.load_participant_list(item.text()) # 双击实验进入受试者列表
        elif self.current_view == 'participants':
            # 双击受试者进入会话列表
            self.load_session_list(self.current_experiment, item.data(Qt.UserRole))
        elif self.current_view == 'sessions':
            # 双击会话打开数据文件夹
            session_index = item.data(Qt.UserRole)
            session_data = self.data_manager.load_json(self.current_experiment, f"participant_{self.current_participant_id}.json")["sessions"][session_index]
            session_path = session_data.get("path")
            if session_path and os.path.isdir(session_path):
                self.open_in_explorer(session_path)
            else:
                QMessageBox.warning(self, "路径无效", "会话数据文件夹不存在或路径无效。")

    def on_item_selection_changed(self, current, _):
        """处理列表项选择变化事件（显示详情）。"""
        if not self._is_item_valid(current):
            self._clear_forms() # 清空表单
            self.form_stack.setCurrentIndex(0) # 切换回仪表盘或空白页
            return
        
        if self.current_view == 'dashboard' or self.current_view == 'experiments':
            self.display_experiment_details(current.text())
        elif self.current_view == 'participants':
            self.display_participant_details(current.text()) # 传递文件名
        elif self.current_view == 'sessions':
            self.display_session_details(current.data(Qt.UserRole)) # 传递会话索引

    def display_experiment_details(self, exp_name):
        """显示实验详情。"""
        self.form_stack.setCurrentIndex(1) # 切换到实验表单页
        data = self.data_manager.load_json(exp_name, "experiment.json")
        self.is_current_exp_locked = data.get('is_locked', False)
        
        lock_text = " (已锁定)" if self.is_current_exp_locked else ""
        self.exp_form_label.setText(f"<h3>实验: {exp_name}{lock_text}</h3>")
        
        self.exp_researcher_edit.setText(data.get("researcher",""))
        self.exp_date_edit.setText(data.get("date",""))
        self.exp_purpose_text.setPlainText(data.get("purpose",""))
        
        self._populate_changelog_table(self.exp_changelog_table, data.get("changelog", []))
        self._update_form_lock_state() # 更新锁定状态

        # 更新列表项的 tooltip
        item = self.find_item_by_text(exp_name)
        if item:
            item.setToolTip(f"实验: {exp_name}\n双击进入受试者列表")

    def display_participant_details(self, part_filename):
        """显示受试者详情。"""
        part_id = part_filename.replace("participant_", "").replace(".json", "")
        self.form_stack.setCurrentIndex(2) # 切换到受试者表单页
        self.part_form_label.setText(f"<h3>受试者: {part_id}</h3>")
        data = self.data_manager.load_json(self.current_experiment, part_filename)
        
        # 动态填充字段
        for key, widget in self.participant_widgets.items():
            value = data.get(key)
            if key == 'tags': # 特殊处理 tags 字段
                widget.setText(", ".join(value) if isinstance(value, list) else str(value) if value is not None else "")
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value) if value is not None else "")
            elif isinstance(widget, QTextEdit):
                widget.setPlainText(str(value) if value is not None else "")
            elif isinstance(widget, QComboBox):
                widget.setCurrentText(str(value) if value is not None else "")

        # 填充变更历史表
        self._populate_changelog_table(self.part_changelog_table, data.get("changelog", [])) 
        self._update_form_lock_state() # 更新锁定状态

    def display_session_details(self, session_index):
        """显示会话详情。"""
        self.form_stack.setCurrentIndex(3) # 切换到会话表单页
        data = self.data_manager.load_json(self.current_experiment, f"participant_{self.current_participant_id}.json")
        s_data = data["sessions"][session_index]
        s_name = os.path.basename(s_data.get("path","未知会话"))
        self.session_form_label.setText(f"<h3>会话: {s_name}</h3>")
        
        self.session_path_edit.setText(s_data.get("path", ""))
        self.session_date_edit.setText(s_data.get("date", ""))
        self.session_task_edit.setText(s_data.get("task", ""))
        self.session_notes_text.setPlainText(s_data.get("notes", ""))
        self.session_tags_edit.setText(", ".join(s_data.get("tags", [])))
        self._update_form_lock_state() # 更新锁定状态

    def on_list_context_menu(self, position):
        """处理列表右键菜单。"""
        item = self.item_list.itemAt(position)
        if not self._is_item_valid(item): return
        
        self.current_selected_item_name = item.text() # 存储当前选中项的名称
        menu = QMenu(self)
        
        view = self.current_view
        if view == 'dashboard': view = 'experiments' # 仪表盘上的项目实际上是实验

        if view == 'experiments':
            data = self.data_manager.load_json(self.current_selected_item_name, "experiment.json")
            lock_text = "解锁实验" if data.get("is_locked") else "锁定实验"
            action_lock = menu.addAction(self.icon_manager.get_icon("unlock" if data.get("is_locked") else "lock"), lock_text)
            action_lock.triggered.connect(self.on_toggle_lock_experiment)
            
            menu.addSeparator()
            action_open = menu.addAction(self.icon_manager.get_icon("open_folder"), "在文件浏览器中打开")
            action_open.triggered.connect(lambda: self.open_in_explorer(os.path.join(self.data_manager.root_path, self.current_selected_item_name)))
            
            menu.addSeparator()
            action_rename = menu.addAction(self.icon_manager.get_icon("rename"), "重命名...")
            action_rename.triggered.connect(self.on_rename_experiment)
            action_delete = menu.addAction(self.icon_manager.get_icon("delete"), "移至回收站...")
            action_delete.triggered.connect(self.on_delete_experiment)

        elif view == 'participants':
            action_copy = menu.addAction(self.icon_manager.get_icon("copy"), "复制到其他实验...")
            action_copy.triggered.connect(self.on_copy_participant)
            
            menu.addSeparator()
            action_delete = menu.addAction(self.icon_manager.get_icon("delete"), "移至回收站...")
            action_delete.triggered.connect(self.on_delete_participant)
            
            if self.is_current_exp_locked: # 如果实验锁定，禁用复制和删除
                action_copy.setDisabled(True)
                action_delete.setDisabled(True)

        elif view == 'sessions':
            session_index = item.data(Qt.UserRole)
            session_data = self.data_manager.load_json(self.current_experiment, f"participant_{self.current_participant_id}.json")["sessions"][session_index]
            session_path = session_data.get("path")

            action_open = menu.addAction(self.icon_manager.get_icon("open_folder"), "打开数据文件夹")
            action_open.triggered.connect(lambda: self.open_in_explorer(session_path))
            if not (session_path and os.path.isdir(session_path)): # 路径无效则禁用
                action_open.setDisabled(True)
            
            menu.addSeparator()
            action_delete = menu.addAction(self.icon_manager.get_icon("delete"), "解除关联...")
            action_delete.triggered.connect(lambda: self.on_delete_session(item.data(Qt.UserRole)))
            
            if self.is_current_exp_locked: # 如果实验锁定，禁用删除
                action_delete.setDisabled(True)

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
            QMessageBox.information(self, "成功", "实验信息已保存。")
            self.display_experiment_details(name) # 刷新显示
        else:
            QMessageBox.critical(self, "错误", f"保存失败: {error}")

    def on_save_participant(self):
        """保存受试者档案信息。"""
        if not self._is_item_valid(self.item_list.currentItem()): return
        filename = self.item_list.currentItem().text()
        data = self.data_manager.load_json(self.current_experiment, filename)
        
        # 动态收集数据
        for key, widget in self.participant_widgets.items():
            if key == 'tags': # 特殊处理 tags 字段
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
            QMessageBox.information(self, "成功", "受试者档案已保存。")
            self.display_participant_details(filename) # 刷新显示
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
            QMessageBox.information(self, "成功", "会话信息已保存。")
            self.display_session_details(session_index) # 刷新显示
        else:
            QMessageBox.critical(self, "错误", f"保存失败: {error}")

    def on_new_experiment(self):
        """创建新实验。"""
        name, ok = QInputDialog.getText(self, "新建实验", "请输入新实验的名称:")
        if ok and name:
            if name in self.data_manager.get_experiments():
                QMessageBox.warning(self, "错误", "实验名已存在，请使用不同的名称。")
                return
            data = {"date": datetime.now().strftime("%Y-%m-%d"), "researcher": "", "purpose": "", "is_locked": False, "changelog": []}
            success, error = self.data_manager.save_json(data, (name, "experiment.json"), "创建实验")
            if success:
                self.load_experiment_list() # 刷新列表
                self.find_and_select_item(name) # 选中新创建的实验
                self._update_dashboard(lazy=True) # 刷新仪表盘统计
            else:
                QMessageBox.critical(self, "错误", f"创建失败: {error}")

    def on_new_participant(self):
        """创建新受试者档案。"""
        sug_id = self.data_manager.suggest_participant_id(self.current_experiment)
        part_id, ok = QInputDialog.getText(self, "新建受试者档案", "请输入受试者唯一ID:", text=sug_id)
        if ok and part_id:
            filename = f"participant_{part_id}.json"
            if filename in self.data_manager.get_participants(self.current_experiment):
                QMessageBox.warning(self, "错误", "该ID已存在于当前实验中，请使用不同的ID。")
                return
            
            # 初始化受试者数据，包含所有 schema V2 中定义的字段
            initial_data = {"id": part_id, "sessions": [], "changelog": []}
            schema_v2 = self.config_manager.get_participant_schema_v2()
            for group in schema_v2:
                for field in group.get("fields", []):
                    key = field.get("key")
                    if key not in initial_data: # 避免覆盖id, sessions, changelog
                        if field.get("type") == "ComboBox":
                            initial_data[key] = field.get("options", [""])[0] # 默认选中第一个选项
                        elif field.get("type") == "TextEdit":
                            initial_data[key] = ""
                        else: # LineEdit
                            initial_data[key] = ""

            success, error = self.data_manager.save_json(initial_data, (self.current_experiment, filename), "创建受试者档案")
            if success:
                self.load_participant_list(self.current_experiment) # 刷新列表
                self.find_and_select_item(filename) # 选中新创建的受试者
                self._update_dashboard(lazy=True) # 刷新仪表盘统计
            else:
                QMessageBox.critical(self, "错误", f"创建失败: {error}")

    def on_add_session(self):
        """为受试者关联数据会话文件夹。"""
        # 获取结果目录的默认路径，如果未设置则使用档案库根目录
        res_dir = self.main_window.config.get('file_settings',{}).get('results_dir', self.data_manager.root_path)
        directory = QFileDialog.getExistingDirectory(self, "选择要关联的数据文件夹", res_dir)
        if directory:
            success, error = self.data_manager.add_session_to_participant(self.current_experiment, self.current_participant_id, directory)
            if success:
                self.load_session_list(self.current_experiment, self.current_participant_id) # 刷新会话列表
                self._update_dashboard(lazy=True) # 刷新仪表盘统计
            else:
                QMessageBox.warning(self, "关联失败", error)

    def on_delete_experiment(self):
        """删除选中的实验（移动到回收站）。"""
        name = self.current_selected_item_name
        if QMessageBox.warning(self, "确认操作", f"您确定要将实验 '{name}' 移至回收站吗？\n所有关联的受试者档案都将被一并移动。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            success, error = self.data_manager.delete_experiment(name)
            if success:
                self.load_experiment_list() # 刷新实验列表
                self._update_dashboard(lazy=True) # 刷新仪表盘统计
            else:
                QMessageBox.critical(self, "操作失败", error)

    def on_delete_participant(self):
        """删除选中的受试者档案（移动到回收站）。"""
        name = self.current_selected_item_name
        if QMessageBox.warning(self, "确认操作", f"您确定要将档案 '{name}' 移至回收站吗？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            success, error = self.data_manager.delete_participant(self.current_experiment, name)
            if success:
                self.load_participant_list(self.current_experiment) # 刷新受试者列表
                self._update_dashboard(lazy=True) # 刷新仪表盘统计
            else:
                QMessageBox.critical(self, "操作失败", error)

    def on_delete_session(self, session_index):
        """解除会话关联。"""
        if QMessageBox.warning(self, "确认操作", "您确定要解除此数据会话的关联吗？\n这不会删除实际数据文件夹，仅从档案中移除记录。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            success, error = self.data_manager.delete_participant_session(self.current_experiment, self.current_participant_id, session_index)
            if success:
                self.load_session_list(self.current_experiment, self.current_participant_id) # 刷新会话列表
                self._update_dashboard(lazy=True) # 刷新仪表盘统计
            else:
                QMessageBox.critical(self, "解除关联失败", error)

    def on_rename_experiment(self):
        """重命名实验。"""
        old_name = self.current_selected_item_name
        new_name, ok = QInputDialog.getText(self, "重命名实验", "请输入实验的新名称:", text=old_name)
        if ok and new_name and new_name != old_name:
            success, error = self.data_manager.rename_experiment(old_name, new_name)
            if success:
                self.load_experiment_list() # 刷新实验列表
                self.find_and_select_item(new_name) # 选中新名称的实验
            else:
                QMessageBox.critical(self, "重命名失败", error)

    def on_copy_participant(self):
        """复制受试者档案到其他实验。"""
        part_file = self.current_selected_item_name
        targets = [e for e in self.data_manager.get_experiments() if e != self.current_experiment]
        if not targets:
            QMessageBox.information(self, "无法复制", "没有其他实验可作为目标，无法复制档案。")
            return
        
        dest_exp, ok = QInputDialog.getItem(self, "选择目标实验", f"将档案 '{part_file}' 复制到:", targets, 0, False)
        if ok and dest_exp:
            success, error = self.data_manager.copy_participant_to_experiment(self.current_experiment, part_file, dest_exp)
            if success:
                QMessageBox.information(self, "成功", f"档案已成功复制到 '{dest_exp}'。")
            else:
                QMessageBox.critical(self, "复制失败", error)

    def on_toggle_lock_experiment(self):
        """切换实验的锁定状态。"""
        success, error = self.data_manager.toggle_experiment_lock(self.current_selected_item_name)
        if success:
            self.load_experiment_list() # 刷新实验列表以更新锁定图标
            self.find_and_select_item(self.current_selected_item_name) # 保持选中状态
        else:
            QMessageBox.critical(self, "操作失败", error)

    def on_export_to_csv(self):
        """导出当前实验下的所有受试者信息到CSV。"""
        if not self.current_experiment:
            QMessageBox.warning(self, "操作无效", "请先选择一个实验。")
            return
        
        default_path = os.path.join(os.path.expanduser("~"), "Downloads", f"{self.current_experiment}_participants.csv")
        file_path, _ = QFileDialog.getSaveFileName(self, "导出受试者数据", default_path, "CSV Files (*.csv)")
        if file_path:
            success, error = self.data_manager.export_participants_to_csv(self.current_experiment, file_path)
            if success:
                QMessageBox.information(self, "导出成功", f"数据已成功导出到:\n{file_path}")
            else:
                QMessageBox.critical(self, "导出失败", error)

    def on_settings_clicked(self):
        """打开档案库设置对话框。"""
        settings_dialog = ArchiveSettingsDialog(self)
        if settings_dialog.exec_() == QDialog.Accepted:
            # 如果设置被修改并保存，重新初始化数据管理器并刷新UI
            self.data_manager = ArchiveDataManager(self.config_manager.get_archive_root())
            # _build_dynamic_participant_form 会在 on_participant_schema_changed 中调用
            # load_dashboard 会在 on_settings_changed 中调用
            QMessageBox.information(self, "设置已更新", "档案库设置已更新并保存。")
            
    def on_settings_changed(self):
        """
        当常规设置（如档案库根目录）发生变化时调用。
        需要重新加载数据管理器并刷新仪表盘。
        """
        self.data_manager = ArchiveDataManager(self.config_manager.get_archive_root())
        self.load_dashboard() # 重新加载仪表盘以反映根目录变化

    def on_participant_schema_changed(self):
        """
        当受试者字段定义（Schema V2）发生变化时调用。
        需要重新构建受试者表单的动态部分。
        """
        self._build_dynamic_participant_form() # 重新构建受试者表单
        # 如果当前正在查看受试者，则重新加载其数据以匹配新表单
        if self.current_view == 'participants' and self.current_participant_id:
            part_filename = f"participant_{self.current_participant_id}.json"
            self.display_participant_details(part_filename)
        else:
            self._clear_participant_form() # 如果不在受试者视图，则清空表单

    def open_in_explorer(self, path):
        """在文件浏览器中打开指定路径。"""
        if not path or not os.path.isdir(path):
            QMessageBox.warning(self, "路径无效", f"无法打开路径:\n{path}\n请确保路径存在且是一个文件夹。")
            return
        try:
            if sys.platform == 'win32':
                os.startfile(os.path.realpath(path))
            elif sys.platform == 'darwin': # macOS
                subprocess.check_call(['open', path])
            else: # Linux
                subprocess.check_call(['xdg-open', path])
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法打开路径: {e}")

    def find_item_by_text(self, text):
        """在列表中查找指定文本的项。"""
        items = self.item_list.findItems(text, Qt.MatchExactly)
        return items[0] if items else None

    def find_and_select_item(self, text):
        """在列表中查找并选中指定文本的项。"""
        item = self.find_item_by_text(text)
        if item:
            self.item_list.setCurrentItem(item)

    def _clear_participant_form(self):
        """清空受试者表单的动态字段内容和变更历史。"""
        # 清空动态生成的字段控件的内容
        for key, widget in self.participant_widgets.items():
            if isinstance(widget, QLineEdit):
                widget.clear()
            elif isinstance(widget, QTextEdit):
                widget.setPlainText("")
            elif isinstance(widget, QComboBox):
                widget.setCurrentIndex(0) # 选中第一个选项
        # 清空变更历史表
        if hasattr(self, 'part_changelog_table'):
            self.part_changelog_table.setRowCount(0)

# ==============================================================================
# 5. 回收站对话框 (增强 ToolTips)
# ==============================================================================
class RecycleBinDialog(QDialog):
    """回收站对话框，用于恢复或永久删除已移至回收站的项目。"""
    def __init__(self, parent):
        super().__init__(parent)
        self.data_manager = parent.data_manager
        self.setResult(QDialog.Rejected) # 默认结果为取消
        self.setWindowTitle("回收站")
        self.resize(700, 500)
        self._init_ui()
        self.load_trashed_items()

    def _init_ui(self):
        """初始化回收站UI。"""
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("这里是已删除的项目。您可以选择恢复它们或永久删除。"))
        
        self.item_list = QTableWidget()
        self.item_list.setColumnCount(3)
        self.item_list.setHorizontalHeaderLabels(["删除时间", "原始路径", "类型"])
        self.item_list.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.item_list.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch) # 原始路径列伸展
        self.item_list.setSelectionBehavior(QAbstractItemView.SelectRows) # 整行选中
        self.item_list.setEditTriggers(QAbstractItemView.NoEditTriggers) # 不可编辑
        
        btn_layout = QHBoxLayout()
        self.restore_btn = QPushButton("恢复选中项")
        self.restore_btn.setToolTip("将选中的项目恢复到其在档案库中的原始位置。")
        self.purge_btn = QPushButton("永久删除选中项")
        self.purge_btn.setToolTip("警告：从回收站彻底删除选中的项目，此操作不可恢复！")
        self.close_btn = QPushButton("关闭")
        
        btn_layout.addStretch() # 将按钮推到右侧
        btn_layout.addWidget(self.restore_btn)
        btn_layout.addWidget(self.purge_btn)
        btn_layout.addWidget(self.close_btn)
        
        layout.addWidget(self.item_list)
        layout.addLayout(btn_layout)
        
        self.restore_btn.clicked.connect(self.on_restore)
        self.purge_btn.clicked.connect(self.on_purge)
        self.close_btn.clicked.connect(self.close)

    def load_trashed_items(self):
        """加载回收站中的项目并显示在表格中。"""
        self.item_list.setRowCount(0)
        items = self.data_manager.get_trashed_items()
        
        # 按删除时间倒序排序
        for name, info in sorted(items.items(), key=lambda x: x[0], reverse=True):
            row = self.item_list.rowCount()
            self.item_list.insertRow(row)
            try:
                del_time = datetime.strptime(name.split('_')[0], '%Y%m%d%H%M%S').strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                del_time = "未知时间" # 处理时间戳解析失败的情况
            
            self.item_list.setItem(row, 0, QTableWidgetItem(del_time))
            self.item_list.setItem(row, 1, QTableWidgetItem(info.get('original_path', '未知路径')))
            self.item_list.setItem(row, 2, QTableWidgetItem(info.get('type', '未知类型')))
            self.item_list.item(row, 0).setData(Qt.UserRole, name) # 存储完整的文件名作为隐藏数据

    def _get_selected_item_name(self):
        """获取当前选中项的完整文件名。"""
        items = self.item_list.selectedItems()
        if items:
            # 获取选中行的第一个单元格（删除时间）的数据，其中存储了完整的文件名
            return self.item_list.item(items[0].row(), 0).data(Qt.UserRole)
        return None

    def on_restore(self):
        """处理恢复选中项的请求。"""
        item_name = self._get_selected_item_name()
        if not item_name: return
        
        success, error = self.data_manager.restore_trashed_item(item_name)
        if success:
            self.setResult(QDialog.Accepted) # 设置对话框结果为 Accepted，通知父窗口有操作发生
            self.load_trashed_items() # 刷新列表
            QMessageBox.information(self, "恢复成功", f"项目 '{item_name.split('_', 1)[1]}' 已成功恢复。")
        else:
            QMessageBox.critical(self, "恢复失败", error)

    def on_purge(self):
        """处理永久删除选中项的请求。"""
        item_name = self._get_selected_item_name()
        if not item_name: return
        
        reply = QMessageBox.warning(self, "确认永久删除", "此操作不可撤销！确定要永久删除选中的项目吗？",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            success, error = self.data_manager.purge_trashed_item(item_name)
            if success:
                self.setResult(QDialog.Accepted) # 设置对话框结果为 Accepted
                self.load_trashed_items() # 刷新列表
                QMessageBox.information(self, "删除成功", f"项目 '{item_name.split('_', 1)[1]}' 已被永久删除。")
            else:
                QMessageBox.critical(self, "删除失败", error)

# ==============================================================================
# 6. 插件主入口 (无变化)
# ==============================================================================
class ArchivePlugin(BasePlugin):
    """档案库插件的主入口类。"""
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.dialog_instance = None
        self.config_manager = ArchiveConfigManager(main_window)

    def setup(self):
        """插件设置（加载时调用）。"""
        return True

    def teardown(self):
        """插件卸载（关闭时调用）。"""
        if self.dialog_instance:
            self.dialog_instance.close()

    def execute(self, **kwargs):
        """执行插件（用户点击菜单项时调用）。"""
        if self.dialog_instance is None or not self.dialog_instance.isVisible():
            self.dialog_instance = ArchiveDialog(self.main_window, self.config_manager)
            # 连接 finished 信号，以便在对话框关闭时清除实例
            self.dialog_instance.finished.connect(lambda: setattr(self, 'dialog_instance', None))
        self.dialog_instance.show()
        self.dialog_instance.raise_() # 将窗口带到前台
        self.dialog_instance.activateWindow() # 激活窗口

# --- END OF FILE ---
