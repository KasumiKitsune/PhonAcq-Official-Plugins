# --- START OF FILE plugins/pinyin_to_ipa/pinyin_to_ipa.py (Restored Version) ---

import os
import sys
import re
import html
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QMessageBox, QComboBox, QFormLayout, QGroupBox, QPlainTextEdit, QTextBrowser,
                             QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QDialog)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor, QBrush

# 导入插件API基类
try:
    from plugin_system import BasePlugin
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

# 检查 pypinyin 是否可用
try:
    import pypinyin
    import pypinyin.style._utils as pypinyin_utils
    PYPINYIN_AVAILABLE = True
except ImportError:
    class MockPypinyin:
        def pinyin(self, *args, **kwargs): return [[]]
    pypinyin = MockPypinyin()
    class MockPypinyinUtils:
        def get_final(self, pinyin_str): return pinyin_str
    pypinyin_utils = MockPypinyinUtils()
    PYPINYIN_AVAILABLE = False


# ==========================================================
# IPA 转换核心逻辑 (此部分代码保持不变)
# ==========================================================
TONE_MARKS = {'1':'⁵⁵', '2':'³⁵', '3':'²¹⁴', '4':'⁵¹', '5':'', '3s':'²¹'}
IPA_SCHEME_Standard = {
    "initials": {'b':'p','p':'pʰ','m':'m','f':'f','d':'t','t':'tʰ','n':'n','l':'l','g':'k','k':'kʰ','h':'x','j':'tɕ','q':'tɕʰ','x':'ɕ','zh':'tʂ','ch':'tʂʰ','sh':'ʂ','r':'ɻ','z':'ts','c':'tsʰ','s':'s'},
    "finals": {'a':'a','o':'o','e':'ɤ','i':'i','u':'u','ü':'y','ê':'ɛ','er':'ɚ','ai':'aɪ','ei':'eɪ','ao':'ɑʊ','ou':'oʊ','an':'an','en':'ən','in':'in','ün':'yn','ang':'ɑŋ','eng':'ɤŋ','ing':'iŋ','ong':'ʊŋ','ia':'ia','iao':'iɑʊ','ie':'ie','iu':'ioʊ','ian':'iɛn','iang':'iɑŋ','iong':'iʊŋ','ua':'ua','uo':'uo','uai':'uaɪ','ui':'ueɪ','uan':'uan','un':'uən','uang':'wɑŋ','ueng':'wɤŋ','üe':'ye','üan':'yɛn', 'iou':'ioʊ','uei':'ueɪ','uen':'uən', 've': 'ye', 'van': 'yɛn', 'vn': 'yn'},
    "syllables": {'zi':'tsɹ̩','ci':'tsʰɹ̩','si':'sɹ̩','zhi':'tʂɻ̍','chi':'tʂʰɻ̍','shi':'ʂɻ̍','ri':'ɻ̍','yi':'i','wu':'u','yu':'y','ye':'ie','yin':'in','yun':'yn','yuan':'yɛn','ying':'iŋ', 'm':'m̩','n':'n̩','ng':'ŋ̍','hm':'hm̩','hng':'hŋ̍', 'fu': 'fʋ̩'}
}
IPA_SCHEME_Yanshi = {
    "initials": {'b':'p','p':'pʰ','m':'m','f':'f','d':'t','t':'tʰ','n':'n','l':'l','g':'k','k':'kʰ','h':'x','j':'tɕ','q':'tɕʰ','x':'ɕ','zh':'tʂ','ch':'tʂʰ','sh':'ʂ','r':'ʐ','z':'ts','c':'tsʰ','s':'s'},
    "finals": {'a':'Ą','o':'o','e':'ɣ','i':'i','u':'u','ü':'y','er':'ɚ','ai':'aɪ','ei':'eɪ','ao':'ɑʊ','ou':'oʊ','an':'an','en':'ən','in':'in','ün':'yn','ang':'ɑŋ','eng':'əŋ','ing':'iŋ','ong':'ʊŋ','ua':'uĄ','uo':'uo','uai':'uaɪ','ui':'ueɪ','uei':'ueɪ','uan':'uan','un':'uən','uen':'uən','uang':'uɑŋ','ueng':'uəŋ','ia':'iĄ','ie':'iE','iao':'iɑʊ','iu':'ioʊ','iou':'ioʊ','ian':'iæn','iang':'iɑŋ','iong':'yŋ','üe':'yE','üan':'yæn','ng':'ŋ̍'},
    "syllabic_vowels": {'zi':'ɿ','ci':'ɿ','si':'ɿ','zhi':'ʅ','chi':'ʅ','shi':'ʅ','ri':'ʅ'},
    "syllables": {'yi':'i', 'wu':'u', 'yu':'y', 'ye':'iE', 'yue': 'yE', 'yuan': 'yæn', 'yin': 'in', 'yun': 'yn', 'ying': 'iŋ'}
}
IPA_SCHEME_Kuanshi = {
    "initials": {'b':'p','p':'pʰ','m':'m','f':'f','d':'t','t':'tʰ','n':'n','l':'l','g':'k','k':'kʰ','h':'x','j':'tɕ','q':'tɕʰ','x':'ɕ','zh':'tʂ','ch':'tʂʰ','sh':'ʂ','r':'ʐ','z':'ts','c':'tsʰ','s':'s'},
    "finals": {'a':'a','o':'o','e':'e','i':'i','u':'u','ü':'y','er':'ɚ','ai':'ai','ei':'ei','ao':'ɑu','ou':'ou','an':'an','en':'ən','in':'in','ün':'yn','ang':'ɑŋ','eng':'əŋ','ing':'iŋ','ong':'uŋ','ua':'ua','uo':'uo','uai':'uai','ui':'uei','uei':'uei','uan':'uan','un':'uən','uen':'uən','uang':'uɑŋ','ueng':'uəŋ','ia':'ia','ie':'iɛ','iao':'iɑu','iu':'iou','iou':'iou','ian':'iɛn','iang':'iɑŋ','iong':'yŋ','üe':'yɛ','üan':'yɛn','ng':'ŋ'},
    "syllabic_vowels": {'zi':'ɿ','ci':'ɿ','si':'ɿ','zhi':'ʅ','chi':'ʅ','shi':'ʅ','ri':'ʅ'},
    "syllables": {'yi':'i', 'wu':'u', 'yu':'y', 'ye':'iɛ', 'yue': 'yɛ', 'yuan': 'yɛn', 'yin': 'in', 'yun': 'yn', 'ying': 'iŋ'}
}

def get_tone(pinyin_with_tone):
    if not pinyin_with_tone: return '5'
    match = re.search(r'([1-5])$', pinyin_with_tone)
    return match.group(1) if match else '5'

def apply_sandhi(words, pinyins):
    if len(words) != len(pinyins): return [(p, False) for p in pinyins]
    new_pinyins = list(pinyins); sandhi_flags = [False] * len(pinyins); original_tones = [get_tone(p) for p in pinyins]
    for i in range(len(words)):
        if words[i] == '啊' and i > 0:
            prev_pinyin_no_tone = pinyins[i-1][:-1]; prev_final = pypinyin_utils.get_final(prev_pinyin_no_tone) if prev_pinyin_no_tone else ''
            tone = get_tone(pinyins[i]); new_final = ''
            if prev_final.endswith(('a', 'o', 'e', 'i', 'ü')): new_final = f"ya{tone}"
            elif prev_final.endswith('u'): new_final = f"wa{tone}"
            elif prev_final.endswith('n'): new_final = f"na{tone}"
            elif prev_final.endswith('ng'): new_final = f"nga{tone}"
            if new_final: new_pinyins[i] = new_final; sandhi_flags[i] = True
    for i in range(len(words)):
        word = words[i]; original_tone = original_tones[i]
        if word == '一' and original_tone == '1':
            is_sandhi = False
            if i > 0 and i + 1 < len(words) and words[i-1] == words[i+1]: new_pinyins[i] = 'yi5'; is_sandhi = True
            elif i + 1 < len(words):
                next_tone = get_tone(new_pinyins[i+1])
                if next_tone == '4': new_pinyins[i] = 'yi2'; is_sandhi = True
                elif next_tone in ['1', '2', '3']: new_pinyins[i] = 'yi4'; is_sandhi = True
            if is_sandhi: sandhi_flags[i] = True
        if word == '不' and original_tone == '4':
            is_sandhi = False
            if i > 0 and i + 1 < len(words) and words[i-1] == words[i+1]: new_pinyins[i] = 'bu5'; is_sandhi = True
            elif i + 1 < len(words) and get_tone(new_pinyins[i+1]) == '4': new_pinyins[i] = 'bu2'; is_sandhi = True
            if is_sandhi: sandhi_flags[i] = True
    temp_pinyins = list(new_pinyins)
    for i in range(len(temp_pinyins)):
        if get_tone(temp_pinyins[i]) == '3':
            if i + 1 < len(temp_pinyins) and get_tone(temp_pinyins[i+1]) == '3': new_pinyins[i] = temp_pinyins[i][:-1] + '2'; sandhi_flags[i] = True
            elif i + 1 < len(temp_pinyins) and get_tone(temp_pinyins[i+1]) != '3': new_pinyins[i] = temp_pinyins[i][:-1] + '3s'; sandhi_flags[i] = True
    return list(zip(new_pinyins, sandhi_flags))

def convert_pinyin_to_ipa(pinyin_sandhi_list, scheme):
    ipa_list = []
    for pinyin, is_sandhi in pinyin_sandhi_list:
        match = re.match(r'([a-z-üv]+)(3s|\d)?', pinyin)
        if not match: ipa_list.append(f"<{pinyin}?>"); continue
        pinyin_no_tone, tone_mark_str = match.groups()
        if tone_mark_str is None: tone_mark_str = '5'
        tone_ipa = TONE_MARKS.get(tone_mark_str, '')
        if is_sandhi and tone_ipa.strip(): tone_ipa = f'<span style="color: red;">{tone_ipa}</span>'
        ipa_syllable = ""
        if pinyin_no_tone in scheme.get("syllables", {}): ipa_syllable = scheme["syllables"][pinyin_no_tone]
        elif pinyin_no_tone in scheme.get("syllabic_vowels", {}):
            initial_part = pinyin_no_tone[:-1] if len(pinyin_no_tone) > 1 else ""
            vowel_part = scheme["syllabic_vowels"][pinyin_no_tone]; initial_ipa = scheme["initials"].get(initial_part, ""); ipa_syllable = f"{initial_ipa}{vowel_part}"
        else:
            initial = ""; final = pinyin_no_tone
            for i in ['zh','ch','sh','b','p','m','f','d','t','n','l','g','k','h','j','q','x','r','z','c','s','y','w']:
                if pinyin_no_tone.startswith(i): initial = i; final = pinyin_no_tone[len(i):]; break
            initial_ipa = scheme["initials"].get(initial, ""); final = final.replace('v', 'ü')
            if initial in ['j', 'q', 'x', 'y'] and final.startswith('u'): final = 'ü' + final[1:]
            if final == 'iu': final = 'iou'
            if final == 'ui': final = 'uei'
            if final == 'un' and initial not in ['j', 'q', 'x', 'y']: final = 'uen'
            if initial == 'y': final = 'i' + final if final else 'i'
            elif initial == 'w': final = 'u' + final if final else 'u'
            final_ipa = scheme["finals"].get(final, f"<{final}?>"); ipa_syllable = f"{initial_ipa}{final_ipa}"
        ipa_list.append(f"[{ipa_syllable}]{tone_ipa}")
    return " ".join(ipa_list).replace("] [", "][")
# ==========================================================

class PinyinToIpaPlugin(BasePlugin):
    """插件主类 (窗口模式版)，负责管理 '拼音转IPA' 功能的独立窗口。"""

    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.dialog_instance = None
        self.ToggleSwitch = getattr(main_window, 'ToggleSwitch', None)
        if self.ToggleSwitch is None:
            print("[PinyinToIPA Plugin] 错误: 无法从主窗口获取 ToggleSwitch 类。")

    def setup(self):
        if self.ToggleSwitch is None:
            return False
        print("[PinyinToIPA Plugin] 插件已准备就绪。")
        return True

    def teardown(self):
        if self.dialog_instance:
            try:
                self.dialog_instance.close()
            except Exception as e:
                print(f"[PinyinToIPA Plugin] 关闭窗口时出错: {e}")
        self.dialog_instance = None
        print("[PinyinToIPA Plugin] 插件已卸载。")

    def execute(self, **kwargs):
        try:
            if self.dialog_instance is None:
                page = self.PinyinToIpaPage(self, self.main_window, self.ToggleSwitch)
                self.dialog_instance = QDialog(self.main_window)
                self.dialog_instance.setWindowTitle("拼音转IPA工具")
                self.dialog_instance.setMinimumSize(1100, 700) # 稍微加宽以容纳表格
                layout = QVBoxLayout(self.dialog_instance)
                layout.addWidget(page)
                self.dialog_instance.setLayout(layout)
                self.dialog_instance.finished.connect(self.on_dialog_finished)

            self.dialog_instance.show()
            self.dialog_instance.raise_()
            self.dialog_instance.activateWindow()
        except Exception as e:
            import traceback
            print(f"[PinyinToIPA Plugin] 执行时出错: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self.main_window, "插件错误", f"无法打开'拼音转IPA'工具窗口:\n{e}")

    def on_dialog_finished(self):
        self.dialog_instance = None
        print("[PinyinToIPA Plugin] 工具窗口已关闭。")

    def _on_persistent_setting_changed(self, key, value):
        self.main_window.update_and_save_module_state('pinyin_to_ipa', key, value)

    # =================================================================
    # 内部类 PinyinToIpaPage (此部分代码完全不变)
    # =================================================================
    class PinyinToIpaPage(QWidget):
        def __init__(self, parent_plugin, parent_window, ToggleSwitchClass):
            super().__init__()
            self.parent_plugin = parent_plugin
            self.parent_window = parent_window
            self.ToggleSwitch = ToggleSwitchClass
            self.schemes = {"标准方案": IPA_SCHEME_Standard, "严式音标": IPA_SCHEME_Yanshi, "宽式音标": IPA_SCHEME_Kuanshi}
            
            if not PYPINYIN_AVAILABLE:
                self._init_error_ui()
            else:
                self._init_ui()

        def _init_error_ui(self):
            layout = QVBoxLayout(self)
            label = QLabel("错误: `pypinyin` 库未安装。\n此功能无法使用。\n\n请关闭程序后运行: pip install pypinyin")
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("color: red; font-size: 16px;")
            layout.addWidget(label)

        def _init_ui(self):
            main_layout = QHBoxLayout(self)
            left_panel = QWidget(); left_layout = QVBoxLayout(left_panel)
            io_group = QGroupBox("文本转换"); io_layout = QVBoxLayout(io_group)
            self.input_text = QPlainTextEdit(); self.input_text.setPlaceholderText("在此处输入或粘贴汉字，支持换行...")
            self.output_text = QTextBrowser(); self.output_text.setReadOnly(True); self.output_text.setOpenExternalLinks(False)
            self.output_text.setPlaceholderText("转换后的IPA将显示在这里...")
            io_layout.addWidget(QLabel("输入文本:")); io_layout.addWidget(self.input_text)
            io_layout.addWidget(QLabel("输出IPA:")); io_layout.addWidget(self.output_text)
            control_group = QGroupBox("转换选项"); control_layout = QFormLayout(control_group)
            
            module_states = self.parent_window.config.get("module_states", {}).get("pinyin_to_ipa", {})
            
            self.scheme_combo = QComboBox()
            self.scheme_combo.addItems(self.schemes.keys())
            saved_scheme = module_states.get("scheme", "标准方案")
            self.scheme_combo.setCurrentText(saved_scheme)
            
            self.sandhi_switch = self.ToggleSwitch()
            self.sandhi_switch.setChecked(module_states.get("sandhi_enabled", True))
            sandhi_layout = QHBoxLayout(); sandhi_layout.addWidget(self.sandhi_switch); sandhi_layout.addStretch()

            self.convert_button = QPushButton("转换"); self.convert_button.setObjectName("AccentButton")
            control_layout.addRow("转换方案:", self.scheme_combo)
            control_layout.addRow("考虑普通话音变:", sandhi_layout)

            left_layout.addWidget(io_group); left_layout.addWidget(control_group)
            left_layout.addWidget(self.convert_button, 0, Qt.AlignRight)
            right_panel = QSplitter(Qt.Vertical); right_panel.setFixedWidth(400)
            scheme_rules_group = QGroupBox("当前方案规则"); scheme_rules_layout = QVBoxLayout(scheme_rules_group); scheme_rules_layout.setContentsMargins(5, 5, 5, 5)
            self.scheme_rules_table = QTableWidget(); self.scheme_rules_table.setColumnCount(4); self.scheme_rules_table.setHorizontalHeaderLabels(["拼音", "IPA", "拼音", "IPA"])
            self.scheme_rules_table.verticalHeader().setVisible(False); self.scheme_rules_table.setEditTriggers(QTableWidget.NoEditTriggers); self.scheme_rules_table.setSelectionBehavior(QTableWidget.SelectRows); self.scheme_rules_table.setAlternatingRowColors(True)
            header = self.scheme_rules_table.horizontalHeader()
            for i in range(4): header.setSectionResizeMode(i, QHeaderView.Stretch)
            scheme_rules_layout.addWidget(self.scheme_rules_table)
            
            sandhi_rules_group = QGroupBox("普通话主要音变规则"); sandhi_rules_layout = QVBoxLayout(sandhi_rules_group); sandhi_rules_layout.setContentsMargins(5, 5, 5, 5)
            self.sandhi_rules_table = QTableWidget(); self.sandhi_rules_table.setColumnCount(2); self.sandhi_rules_table.setHorizontalHeaderLabels(["规则", "示例"])
            self.sandhi_rules_table.verticalHeader().setVisible(False); self.sandhi_rules_table.setEditTriggers(QTableWidget.NoEditTriggers); self.sandhi_rules_table.setWordWrap(True)
            header = self.sandhi_rules_table.horizontalHeader(); header.setSectionResizeMode(0, QHeaderView.Stretch); header.setSectionResizeMode(1, QHeaderView.Stretch)
            sandhi_rules_layout.addWidget(self.sandhi_rules_table)
            
            right_panel.addWidget(scheme_rules_group); right_panel.addWidget(sandhi_rules_group); right_panel.setStretchFactor(0, 2); right_panel.setStretchFactor(1, 1)
            main_layout.addWidget(left_panel, 1); main_layout.addWidget(right_panel)
            
            self.convert_button.clicked.connect(self.on_convert_clicked)
            self.scheme_combo.currentIndexChanged.connect(self.on_scheme_changed)
            self.scheme_combo.currentIndexChanged.connect(lambda: self.parent_plugin._on_persistent_setting_changed('scheme', self.scheme_combo.currentText()))
            self.sandhi_switch.stateChanged.connect(lambda state: self.parent_plugin._on_persistent_setting_changed('sandhi_enabled', bool(state)))
            
            self.sandhi_rules_table.setMouseTracking(True)
            self.sandhi_rules_table.cellEntered.connect(self.on_sandhi_cell_entered)
            self.populate_sandhi_table()
            self.on_scheme_changed(self.scheme_combo.currentIndex())

        def on_scheme_changed(self, index):
            scheme_name = self.scheme_combo.currentText(); scheme = self.schemes.get(scheme_name)
            if not scheme: return
            self.populate_scheme_table(scheme)

        def populate_scheme_table(self, scheme):
            self.scheme_rules_table.setRowCount(0); all_items = []
            all_items.append(("声母 (Initials)", None))
            for p, ipa in scheme.get('initials', {}).items(): all_items.append((p, ipa))
            all_items.append(("韵母 (Finals)", None))
            for p, ipa in scheme.get('finals', {}).items():
                if p in ['iou', 'uei', 'uen', 've', 'van', 'vn']: continue
                all_items.append((p, ipa))
            syllabics = {**scheme.get("syllables", {}), **scheme.get("syllabic_vowels", {})}
            if syllabics:
                all_items.append(("整体认读 / 舌尖元音", None))
                for p, ipa in sorted(syllabics.items()): all_items.append((p, ipa))
            row = 0; i = 0
            while i < len(all_items):
                self.scheme_rules_table.insertRow(row); p1, ipa1 = all_items[i]
                if ipa1 is None:
                    title_item = QTableWidgetItem(p1); title_item.setTextAlignment(Qt.AlignCenter)
                    font = title_item.font(); font.setBold(True); title_item.setFont(font)
                    self.scheme_rules_table.setItem(row, 0, title_item); self.scheme_rules_table.setSpan(row, 0, 1, 4); i += 1
                else:
                    p_item1 = QTableWidgetItem(p1); ipa_item1 = QTableWidgetItem(f"[{ipa1}]"); ipa_item1.setFont(QFont("Doulos SIL", 10))
                    self.scheme_rules_table.setItem(row, 0, p_item1); self.scheme_rules_table.setItem(row, 1, ipa_item1); i += 1
                    if i < len(all_items):
                        p2, ipa2 = all_items[i]
                        if ipa2 is not None:
                            p_item2 = QTableWidgetItem(p2); ipa_item2 = QTableWidgetItem(f"[{ipa2}]"); ipa_item2.setFont(QFont("Doulos SIL", 10))
                            self.scheme_rules_table.setItem(row, 2, p_item2); self.scheme_rules_table.setItem(row, 3, ipa_item2); i += 1
                row += 1
            last_row = self.scheme_rules_table.rowCount(); self.scheme_rules_table.insertRow(last_row)
            attribution_item = QTableWidgetItem("以上方案均参考自 UntPhesoca"); attribution_item.setTextAlignment(Qt.AlignCenter)
            font = attribution_item.font(); font.setItalic(True); attribution_item.setFont(font); attribution_item.setForeground(QBrush(QColor("gray")))
            self.scheme_rules_table.setItem(last_row, 0, attribution_item); self.scheme_rules_table.setSpan(last_row, 0, 1, 4)
            self.scheme_rules_table.resizeRowsToContents()
            
        def populate_sandhi_table(self):
            sandhi_data = [("上声(²¹⁴)的变调", None), ("上声 + 上声", "前一个变阳平(³⁵)。例: 你好 nǐ hǎo → ní hǎo"), ("上声 + 非上声", "变为半上(²¹)。例: 很好 hěn hǎo"),
                           ("“一”和“不”的变调", None), ("在去声(⁵¹)前", "一(yī)→yí / 不(bù)→bú。例: 一样 yíyàng / 不怕 búpà"),
                           ("在非去声前", "一(yī)→yì。例: 一天 yìtiān (不(bù)声调不变)"), ("在重叠词中", "读轻声。例: 看一看 kàn yi kan / 好不好 hǎo bu hǎo"),
                           ("“啊”的变读", None), ("前为a,o,e,i,ü", "啊(a) → 呀(ya)"), ("前为u(ao,iao)", "啊(a) → 哇(wa)"), ("前为n", "啊(a) → 哪(na)"), ("前为ng", "啊(a) → 啊(nga)")]
            self.sandhi_rules_table.setRowCount(0)
            for rule, example in sandhi_data:
                row = self.sandhi_rules_table.rowCount(); self.sandhi_rules_table.insertRow(row)
                if example is None:
                    title_item = QTableWidgetItem(rule); font = title_item.font(); font.setBold(True); title_item.setFont(font)
                    title_item.setForeground(QColor("#788C67")); self.sandhi_rules_table.setItem(row, 0, title_item); self.sandhi_rules_table.setSpan(row, 0, 1, 2)
                else:
                    rule_item = QTableWidgetItem(rule); example_item = QTableWidgetItem(example)
                    rule_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft); example_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                    self.sandhi_rules_table.setItem(row, 0, rule_item); self.sandhi_rules_table.setItem(row, 1, example_item)
            self.sandhi_rules_table.resizeRowsToContents()

        def on_sandhi_cell_entered(self, row, column):
            item = self.sandhi_rules_table.item(row, column)
            if not item: return
            font_metrics = self.sandhi_rules_table.fontMetrics(); text_width = font_metrics.horizontalAdvance(item.text()); column_width = self.sandhi_rules_table.columnWidth(column) - 15
            if text_width > column_width: item.setToolTip(item.text())
            else: item.setToolTip("")

        def on_convert_clicked(self):
            input_text = self.input_text.toPlainText()
            if not input_text.strip(): self.output_text.clear(); return
            try:
                output_lines_html = []
                for line in input_text.splitlines():
                    if not line.strip(): output_lines_html.append(""); continue
                    segments = re.split(r'([^\u4e00-\u9fff]+)', line); ipa_segments_for_line = []
                    for segment in segments:
                        if not segment: continue
                        if re.search(r'[\u4e00-\u9fff]', segment):
                            pinyin_raw_list = pypinyin.pinyin(segment, style=pypinyin.Style.TONE3, heteronym=False)
                            if not pinyin_raw_list: continue
                            pinyin_list = [p[0] for p in pinyin_raw_list if p]
                            words_list = [w[0] for w in pypinyin.pinyin(segment, style=pypinyin.Style.NORMAL, heteronym=False) if w]
                            if not pinyin_list or not words_list or len(pinyin_list) != len(words_list): continue
                            processed_pinyin_list_with_info = []
                            if self.sandhi_switch.isChecked(): processed_pinyin_list_with_info = apply_sandhi(words_list, pinyin_list)
                            else: processed_pinyin_list_with_info = [(p, False) for p in pinyin_list]
                            selected_scheme = self.schemes.get(self.scheme_combo.currentText())
                            ipa_part = convert_pinyin_to_ipa(processed_pinyin_list_with_info, selected_scheme)
                            ipa_segments_for_line.append(ipa_part)
                        else:
                            escaped_segment = html.escape(segment); ipa_segments_for_line.append(escaped_segment)
                    output_lines_html.append("".join(ipa_segments_for_line))
                final_html = "<p style='line-height: 1.6;'>" + "<br>".join(output_lines_html) + "</p>"
                self.output_text.setHtml(final_html)
            except Exception as e:
                import traceback; error_info = f"发生错误: {e}\n\n详细信息:\n{traceback.format_exc()}"; QMessageBox.critical(self, "转换失败", error_info)