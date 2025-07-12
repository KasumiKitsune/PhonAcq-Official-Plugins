# --- START OF FILE plugins/tts_splitter/splitter_main.py ---

import os
import sys
import json
import re
import numpy as np
from datetime import datetime
import tempfile

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             QSplitter, QMessageBox, QFileDialog, QTableWidget,
                             QTableWidgetItem, QHeaderView, QGroupBox, QFormLayout,
                             QSlider, QLineEdit, QComboBox, QPlainTextEdit,
                             QProgressBar, QListWidget, QListWidgetItem, QApplication,
                             QWidget, QMenu, QAbstractItemView, QRadioButton, QProgressDialog, QShortcut)
from PyQt5.QtCore import Qt, QUrl, QEvent
from PyQt5.QtGui import QIntValidator, QColor, QKeySequence
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent

# 动态导入依赖
try:
    from plugin_system import BasePlugin
    import librosa
    import soundfile as sf
    DEPENDENCIES_MISSING = False
except ImportError as e:
    DEPENDENCIES_MISSING = True
    MISSING_ERROR_MESSAGE = str(e)
    class BasePlugin:
        def __init__(self, *args, **kwargs): pass
        def setup(self): return False
        def teardown(self): pass
        def execute(self, **kwargs): pass

# ==============================================================================
# 插件主类 (保持不变)
# ==============================================================================
class TtsSplitterPlugin(BasePlugin):
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.splitter_dialog = None

    def setup(self):
        if DEPENDENCIES_MISSING:
            print(f"[错误][TTS 分割器]: 缺少核心依赖: {MISSING_ERROR_MESSAGE}")
            return False
        
        self.wordlist_editor_page = getattr(self.main_window, 'wordlist_editor_page', None)
        if self.wordlist_editor_page:
            setattr(self.wordlist_editor_page, 'tts_splitter_plugin_active', self)
            print("TTS 批量分割器已向词表编辑器注册。")
        
        return True

    def teardown(self):
        if hasattr(self, 'wordlist_editor_page') and hasattr(self.wordlist_editor_page, 'tts_splitter_plugin_active'):
            delattr(self.wordlist_editor_page, 'tts_splitter_plugin_active')
            print("TTS 批量分割器已从词表编辑器注销。")

        if self.splitter_dialog:
            self.splitter_dialog.close()
        print("TTS 批量分割器插件已卸载。")

    def execute(self, **kwargs):
        if DEPENDENCIES_MISSING:
            QMessageBox.critical(self.main_window, "依赖缺失", f"无法启动 TTS 批量分割器，缺少以下核心依赖：\n\n{MISSING_ERROR_MESSAGE}\n\n请通过 pip 安装它们。")
            return
            
        if self.splitter_dialog is None:
            self.splitter_dialog = SplitterDialog(self.main_window)
            self.splitter_dialog.finished.connect(lambda: setattr(self, 'splitter_dialog', None))
        
        wordlist_path = kwargs.get('wordlist_path')
        if wordlist_path:
            self.splitter_dialog.load_wordlist_from_path(wordlist_path)

        self.splitter_dialog.show()
        self.splitter_dialog.raise_()
        self.splitter_dialog.activateWindow()

# ==============================================================================
# 分割器对话框 UI 与逻辑 (v1.4 - 收尾版)
# ==============================================================================
class SplitterDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.icon_manager = self.parent_window.icon_manager
        
        self.audio_filepath, self.audio_data, self.audio_sr = None, None, None
        self.wordlist_path, self.word_list = None, []
        self.split_points = []
        
        self.player = QMediaPlayer()
        self.temp_audio_file = None

        self.setWindowTitle("TTS 分割器")
        self.setMinimumSize(900, 1000)
        self._init_ui()
        self._connect_signals()
        self._add_tooltips()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)

        left_panel = QWidget(); left_layout = QVBoxLayout(left_panel); left_panel.setFixedWidth(350)
        load_group = QGroupBox("1. 加载文件"); load_form = QFormLayout(load_group)
        self.audio_select_btn = QPushButton("选择长音频文件..."); self.audio_label = QLabel("未选择")
        self.wordlist_select_btn = QPushButton("选择对应的词表..."); self.wordlist_label = QLabel("未选择")
        load_form.addRow(self.audio_select_btn); load_form.addRow(self.audio_label); load_form.addRow(self.wordlist_select_btn); load_form.addRow(self.wordlist_label)
        self.copy_for_tts_btn = QPushButton("复制为TTS格式")
        self.copy_for_tts_btn.setIcon(self.icon_manager.get_icon("copy"))
        self.copy_for_tts_btn.setToolTip("将当前加载的词表内容转换为以逗号分隔的格式，并复制到剪贴板。\n例如：'hello, world, apple, banana'")
        self.copy_for_tts_btn.setEnabled(False) # 默认禁用
        load_form.addRow(self.copy_for_tts_btn)

        params_group = QGroupBox("2. 分割参数 (实时预览)"); params_layout = QVBoxLayout(params_group)
        mode_layout = QHBoxLayout()
        self.precise_mode_radio = QRadioButton("精确模式"); self.full_mode_radio = QRadioButton("完整模式"); self.precise_mode_radio.setChecked(True)
        mode_layout.addWidget(self.precise_mode_radio); mode_layout.addWidget(self.full_mode_radio)
        params_layout.addLayout(mode_layout)
        
        params_form = QFormLayout(); params_form.setContentsMargins(0, 5, 0, 0); params_form.addRow(QLabel("静音阈值:"))
        self.threshold_slider = QSlider(Qt.Horizontal); self.threshold_slider.setRange(-60, -20); self.threshold_slider.setValue(-40)
        self.threshold_label = QLabel(f"{self.threshold_slider.value()} dB")
        threshold_layout = QHBoxLayout(); threshold_layout.addWidget(self.threshold_slider); threshold_layout.addWidget(self.threshold_label)
        params_form.addRow(threshold_layout)
        
        self.min_silence_input = QLineEdit("300"); self.min_silence_input.setValidator(QIntValidator(50, 5000))
        self.offset_input = QLineEdit("50"); self.offset_input.setValidator(QIntValidator(0, 1000))
        params_form.addRow("最小静音时长 (ms):", self.min_silence_input); params_form.addRow("分割点偏移 (ms):", self.offset_input)
        params_layout.addLayout(params_form)
        
        # 在 params_layout.addLayout(params_form) 之后增加智能推荐按钮
        self.recommend_btn = QPushButton("智能推荐参数")
        self.recommend_btn.setIcon(self.icon_manager.get_icon("auto_detect"))
        self.recommend_btn.setToolTip("根据当前音频的噪音水平和语速，自动推荐一组分割参数。")
        self.recommend_btn.setEnabled(False) # 默认禁用
        params_layout.addWidget(self.recommend_btn, 0, Qt.AlignRight)
        
        output_group = QGroupBox("3. 输出设置"); output_layout = QVBoxLayout(output_group)
        output_layout.addWidget(QLabel("输出文件夹:")); self.output_dir_input = QLineEdit(); self.output_dir_btn = QPushButton("浏览...")
        output_dir_layout = QHBoxLayout(); output_dir_layout.addWidget(self.output_dir_input); output_dir_layout.addWidget(self.output_dir_btn)
        output_layout.addLayout(output_dir_layout); output_layout.addWidget(QLabel("输出格式:"))
        self.output_format_combo = QComboBox(); self.output_format_combo.addItems(["WAV (推荐)", "MP3"])
        output_layout.addWidget(self.output_format_combo)

        left_layout.addWidget(load_group); left_layout.addWidget(params_group); left_layout.addWidget(output_group); left_layout.addStretch()
        
        right_panel = QWidget(); right_layout = QVBoxLayout(right_panel)
        preview_group = QGroupBox("实时预览与验证"); preview_layout = QVBoxLayout(preview_group)
        self.preview_table = QTableWidget(); self.preview_table.setColumnCount(3); self.preview_table.setHorizontalHeaderLabels(["片段 #", "对应单词 (可编辑)", "时长 (秒)"])
        self.preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch); self.preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.match_status_label = QLabel("请加载文件并设置参数"); self.match_status_label.setObjectName("StatusLabel"); self.match_status_label.setAlignment(Qt.AlignCenter)
        preview_layout.addWidget(self.match_status_label); preview_layout.addWidget(self.preview_table)
        
        process_group = QGroupBox("处理与日志"); process_layout = QVBoxLayout(process_group)
        self.process_btn = QPushButton("开始处理"); self.process_btn.setObjectName("AccentButton"); self.process_btn.setIcon(self.icon_manager.get_icon("start_session")); self.process_btn.setEnabled(False)
        self.log_display = QPlainTextEdit(); self.log_display.setReadOnly(True)
        self.progress_bar = QProgressBar()
        process_layout.addWidget(self.process_btn); process_layout.addWidget(QLabel("日志:")); process_layout.addWidget(self.log_display, 1); process_layout.addWidget(self.progress_bar)

        right_layout.addWidget(preview_group, 1); right_layout.addWidget(process_group, 1)

        splitter.addWidget(left_panel); splitter.addWidget(right_panel); splitter.setStretchFactor(0, 1); splitter.setStretchFactor(1, 2)
        main_layout.addWidget(splitter)
    
    def _connect_signals(self):
        self.audio_select_btn.clicked.connect(self.select_audio_file)
        self.wordlist_select_btn.clicked.connect(self.select_wordlist_file)
        self.output_dir_btn.clicked.connect(self.select_output_dir)
        self.process_btn.clicked.connect(self.run_processing)
        
        self.threshold_slider.valueChanged.connect(self.on_param_changed)
        self.min_silence_input.textChanged.connect(self.on_param_changed)
        self.offset_input.textChanged.connect(self.on_param_changed)
        self.precise_mode_radio.toggled.connect(self.on_param_changed)
        self.full_mode_radio.toggled.connect(self.on_param_changed)
        
        self.preview_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.preview_table.customContextMenuRequested.connect(self.show_preview_context_menu)
        # [新增] 连接键盘事件
        self.preview_table.installEventFilter(self)

        # 为新按钮连接信号
        self.recommend_btn.clicked.connect(self.apply_smart_params)
        self.copy_for_tts_btn.clicked.connect(self.copy_list_for_tts)

    def _add_tooltips(self):
        self.audio_select_btn.setToolTip("选择一个包含多个词语连续录音的音频文件。")
        self.wordlist_select_btn.setToolTip("选择一个与音频内容顺序对应的标准JSON词表。")
        self.precise_mode_radio.setToolTip("尝试精确切分出发音部分，可能会切掉词尾的弱辅音，适用于噪音较大的环境。")
        self.full_mode_radio.setToolTip("以静音段的中心为分割点，确保每个单词的完整性，推荐用于标准TTS录音。")
        self.threshold_slider.setToolTip("定义多大的声音才不被当做静音。\n数值越小(靠左)，判断越严格，可能将弱音也视为静音。\n数值越大(靠右)，判断越宽松。")
        self.min_silence_input.setToolTip("只有当一段静音的持续时间超过此值(毫秒)，才被认为是一个有效的分割点。")
        self.offset_input.setToolTip("在每个分割点前后应用的微调(毫秒)。\n正值向外扩张，保留更多空白；负值向内收缩，切得更紧。")
        self.preview_table.setToolTip("预览分割结果。\n- 右键点击行可进行试听、删除等操作。\n- 双击第二列可编辑对应的单词/文件名。\n- 选中行后按回车键可快速试听。")
        self.process_btn.setToolTip("当片段数与词表数完全匹配时，此按钮将启用。\n点击后将开始批量保存所有片段。")

    def eventFilter(self, source, event):
        # [新增] 回车键播放事件过滤器
        if source is self.preview_table and event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            selected_rows = self.preview_table.selectionModel().selectedRows()
            if selected_rows:
                self.play_segment(selected_rows[0].row())
                return True
        return super().eventFilter(source, event)
    
    def log(self, message):
        self.log_display.appendPlainText(f"[{datetime.now():%H:%M:%S}] {message}"); QApplication.processEvents()

    def _calculate_smart_params(self):
        """分析音频并返回推荐的分割参数。"""
        if self.audio_data is None or self.audio_sr is None:
            return None

        try:
            # 1. 估算本底噪声水平来推荐阈值
            # 我们取能量最低的5%部分的平均值作为噪声参考
            rms = librosa.feature.rms(y=self.audio_data, frame_length=2048, hop_length=512)[0]
            sorted_rms = np.sort(rms)
            noise_floor_rms = np.mean(sorted_rms[:int(len(sorted_rms) * 0.05)])
            
            # 转换为dB，并增加一个小的安全余量 (例如 6dB)
            # librosa.power_to_db的ref通常是1.0，所以rms可以直接用
            noise_floor_db = librosa.power_to_db([noise_floor_rms**2], ref=np.max)[0]
            recommended_threshold = int(noise_floor_db + 6)
            # 限制在滑块范围内
            recommended_threshold = max(-60, min(-20, recommended_threshold))

            # 2. 估算平均静音时长
            # 我们用一个比较宽松的阈值来初步分割，然后分析静音段的长度
            intervals = librosa.effects.split(self.audio_data, top_db=40)
            if len(intervals) > 1:
                silence_durations_ms = [(intervals[i+1][0] - intervals[i][1]) / self.audio_sr * 1000 
                                        for i in range(len(intervals) - 1)]
                # 取静音时长的中位数，这比平均值更能抵抗极端长/短静音的影响
                recommended_min_silence = int(np.median([d for d in silence_durations_ms if d > 50]))
                # 推荐值通常是中位数的一半左右，且在合理范围内
                recommended_min_silence = int(max(100, min(500, recommended_min_silence * 0.6)))
            else:
                # 如果只有一个片段，说明语速很快，用一个较小默认值
                recommended_min_silence = 200

            return {
                'threshold': recommended_threshold,
                'min_silence': recommended_min_silence
            }
        except Exception as e:
            self.log(f"智能参数推荐失败: {e}")
            return None

    def apply_smart_params(self):
        """计算并应用智能推荐的参数到UI上。"""
        params = self._calculate_smart_params()
        if params:
            self.log(f"应用智能推荐参数: 阈值={params['threshold']}dB, 最小静音={params['min_silence']}ms")
            # blockSignals防止在设置时触发不必要的多次分析
            self.threshold_slider.blockSignals(True)
            self.min_silence_input.blockSignals(True)
            
            self.threshold_slider.setValue(params['threshold'])
            self.min_silence_input.setText(str(params['min_silence']))
            
            self.threshold_slider.blockSignals(False)
            self.min_silence_input.blockSignals(False)
            
            # 手动触发一次分析和UI更新
            self.on_param_changed()

    def select_audio_file(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "选择单个长音频文件", "", "音频文件 (*.wav *.mp3 *.flac)")
        if not filepath:
            self.recommend_btn.setEnabled(False) # 确保取消时也禁用
            return

# [修改] 使用不确定模式（滚动条）的进度对话框
        progress = QProgressDialog("正在加载音频，请稍候...", "取消", 0, 0, self)
        progress.setWindowModality(Qt.WindowModal)
        # 将最大值和最小值都设为0，即可激活“滚动条”模式
        progress.setRange(0, 0) 
        progress.show()
        QApplication.processEvents()
        
        try:
            self.audio_data, self.audio_sr = librosa.load(filepath, sr=None)
            self.audio_filepath = filepath
            self.audio_label.setText(os.path.basename(filepath))
            self.log("音频加载成功。")
            self.recommend_btn.setEnabled(True) # 成功加载后启用
        except Exception as e:
            self.audio_data, self.audio_sr, self.audio_filepath = None, None, None
            self.audio_label.setText("加载失败")
            self.recommend_btn.setEnabled(False) # 失败时禁用
            QMessageBox.critical(self, "音频错误", f"无法加载音频文件:\n{e}")
        finally:
            progress.close()
            self.analyze_and_preview()

    def load_wordlist_from_path(self, filepath):
        if filepath and os.path.exists(filepath):
            self.wordlist_path = filepath
            try:
                with open(filepath, 'r', encoding='utf-8') as f: data = json.load(f)
                if data.get("meta", {}).get("format") != "standard_wordlist": raise ValueError("不是有效的'standard_wordlist'格式。")
                self.word_list = [item.get("text", "") for group in data.get("groups", []) for item in group.get("items", [])]
                self.wordlist_label.setText(f"{os.path.basename(filepath)} ({len(self.word_list)}个词)"); self.log(f"已加载词表 '{os.path.basename(filepath)}'，包含 {len(self.word_list)} 个词。")
                self.copy_for_tts_btn.setEnabled(True) # 启用按钮
                
                wordlist_name_no_ext = os.path.splitext(os.path.basename(filepath))[0]
                default_output_dir = os.path.join(self.parent_window.BASE_PATH, "audio_tts", wordlist_name_no_ext)
                self.output_dir_input.setText(default_output_dir.replace("\\", "/"))

                self.analyze_and_preview()
            except Exception as e:
                self.wordlist_path, self.word_list = None, []; self.wordlist_label.setText("加载失败")
                self.copy_for_tts_btn.setEnabled(False) # 失败时禁用按钮
                QMessageBox.critical(self, "词表错误", f"无法加载或解析词表文件:\n{e}")

    def copy_list_for_tts(self):
        """将当前词表转换为逗号分隔的字符串并复制到剪贴板。"""
        if not self.word_list:
            self.log("错误：没有可复制的词表内容。")
            return
        
        try:
            # 构建TTS友好的字符串
            tts_string = ", ".join(self.word_list)
            
            # 复制到系统剪贴板
            clipboard = QApplication.clipboard()
            clipboard.setText(tts_string)
            
            self.log(f"成功！已将 {len(self.word_list)} 个单词以TTS格式复制到剪贴板。")
            # [可选] 弹出一个短暂的提示框以提供更强的反馈
            QMessageBox.information(self, "复制成功", "词表内容已成功复制到剪贴板！", QMessageBox.Ok)
            
        except Exception as e:
            self.log(f"复制为TTS格式时出错: {e}")
            QMessageBox.critical(self, "复制失败", f"无法将内容复制到剪贴板:\n{e}")

    def select_wordlist_file(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "选择JSON词表文件", os.path.join(self.parent_window.BASE_PATH, "word_lists"), "JSON 文件 (*.json)"); self.load_wordlist_from_path(filepath)

    def on_param_changed(self):
        self.threshold_label.setText(f"{self.threshold_slider.value()} dB"); self.analyze_and_preview()

    def analyze_and_preview(self):
        if self.audio_data is None or self.audio_sr is None or not self.word_list: return
        try:
            top_db = -int(self.threshold_slider.value())
            min_silence_ms = int(self.min_silence_input.text())
            min_silence_samples = int(min_silence_ms / 1000 * self.audio_sr)
            frame_length, hop_length = 2048, 512
            intervals = librosa.effects.split(self.audio_data, top_db=top_db, frame_length=frame_length, hop_length=hop_length)
            
            if self.precise_mode_radio.isChecked():
                self.split_points = [it for it in intervals if (it[1] - it[0]) > (min_silence_samples / 5)]
            else:
                if len(intervals) < 1: self.split_points = []
                else:
                    silence_midpoints = [intervals[i][1] + (intervals[i+1][0] - intervals[i][1]) // 2 for i in range(len(intervals) - 1) if (intervals[i+1][0] - intervals[i][1]) >= min_silence_samples]
                    self.split_points = []; last_split_point = 0
                    for mid_point in silence_midpoints: self.split_points.append((last_split_point, mid_point)); last_split_point = mid_point
                    self.split_points.append((last_split_point, len(self.audio_data)))
            
            self.populate_preview_table()
        except Exception as e: self.log(f"分析时出错: {e}")

    def populate_preview_table(self):
        self.preview_table.setRowCount(0); num_segments = len(self.split_points); num_words = len(self.word_list)
        if num_segments == num_words > 0:
            self.match_status_label.setText(f"✓ 匹配成功：检测到 {num_segments} 个片段。"); self.match_status_label.setStyleSheet("color: #2E7D32; font-weight: bold;"); self.process_btn.setEnabled(True)
        else:
            self.match_status_label.setText(f"✗ 不匹配：检测到 {num_segments} 个片段，但词表需要 {num_words} 个。请调整参数。"); self.match_status_label.setStyleSheet("color: #C62828; font-weight: bold;"); self.process_btn.setEnabled(False)
        max_rows = max(num_segments, num_words); self.preview_table.setRowCount(max_rows)
        for i in range(max_rows):
            item_flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
            if i < num_segments:
                start, end = self.split_points[i]; duration = (end - start) / self.audio_sr
                self.preview_table.setItem(i, 0, QTableWidgetItem(f"片段 {i+1}")); self.preview_table.setItem(i, 2, QTableWidgetItem(f"{duration:.2f}"))
                self.preview_table.item(i, 0).setFlags(item_flags); self.preview_table.item(i, 2).setFlags(item_flags)
            else:
                self.preview_table.setItem(i, 0, QTableWidgetItem("-")); self.preview_table.setItem(i, 2, QTableWidgetItem("-"))
            if i < num_words:
                word_item = QTableWidgetItem(self.word_list[i]); word_item.setFlags(item_flags | Qt.ItemIsEditable) # [修改] 允许编辑
                self.preview_table.setItem(i, 1, word_item)
            else:
                self.preview_table.setItem(i, 1, QTableWidgetItem("-"))
        self.preview_table.resizeColumnsToContents()

    def show_preview_context_menu(self, position):
        selected_rows = self.preview_table.selectionModel().selectedRows()
        if not selected_rows: return
        
        menu = QMenu(self)
        play_action = menu.addAction(self.icon_manager.get_icon("play_audio"), "试听选中片段")
        export_action = menu.addAction(self.icon_manager.get_icon("export"), f"导出选中的 {len(selected_rows)} 个片段...")
        menu.addSeparator()
        delete_action = menu.addAction(self.icon_manager.get_icon("delete"), "从预览中删除选中行")
        
        action = menu.exec_(self.preview_table.mapToGlobal(position))

        if action == play_action:
            self.play_segment(selected_rows[0].row())
        elif action == export_action:
            self.export_selected_segments()
        elif action == delete_action:
            self.delete_selected_segments()

    def play_segment(self, row):
        if not (0 <= row < len(self.split_points)): return
        start, end = self.split_points[row]; segment_data = self.audio_data[start:end]
        try:
            if self.temp_audio_file and os.path.exists(self.temp_audio_file): os.remove(self.temp_audio_file)
            fd, self.temp_audio_file = tempfile.mkstemp(suffix=".wav"); os.close(fd)
            sf.write(self.temp_audio_file, segment_data, self.audio_sr); self.player.setMedia(QMediaContent(QUrl.fromLocalFile(self.temp_audio_file))); self.player.play()
        except Exception as e: self.log(f"试听失败: {e}")

    def delete_selected_segments(self):
        selected_rows = sorted([index.row() for index in self.preview_table.selectionModel().selectedRows()], reverse=True)
        if not selected_rows: return
        
        for row in selected_rows:
            if row < len(self.split_points): del self.split_points[row]
            if row < len(self.word_list): del self.word_list[row]
            
        self.log(f"已从预览中移除 {len(selected_rows)} 行。")
        self.populate_preview_table()
    
    def export_selected_segments(self):
        selected_rows = sorted([index.row() for index in self.preview_table.selectionModel().selectedRows()])
        if not selected_rows: QMessageBox.warning(self, "未选择", "请先在预览列表中选择要导出的片段。"); return

        output_dir = QFileDialog.getExistingDirectory(self, "选择导出文件夹")
        if not output_dir: return

        try:
            offset_ms = int(self.offset_input.text())
            offset_samples = int(offset_ms / 1000 * self.audio_sr)
            
            for row in selected_rows:
                if row >= len(self.split_points): continue
                word_item = self.preview_table.item(row, 1)
                word = word_item.text() if word_item else f"segment_{row+1}"
                
                start, end = self.split_points[row]
                start = max(0, start - offset_samples); end = min(len(self.audio_data), end + offset_samples)
                segment = self.audio_data[start:end]
                
                safe_filename = re.sub(r'[\\/*?:"<>|]', "", word)
                out_path = os.path.join(output_dir, f"{safe_filename}.wav")
                sf.write(out_path, segment, self.audio_sr)
            
            self.log(f"成功导出 {len(selected_rows)} 个片段到: {output_dir}")
            QMessageBox.information(self, "导出成功", f"选中的 {len(selected_rows)} 个片段已成功保存。")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出过程中发生错误:\n{e}")

    def select_output_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出文件夹");
        if directory: self.output_dir_input.setText(directory)

    def run_processing(self):
        output_dir = self.output_dir_input.text().strip()
        if not output_dir: QMessageBox.warning(self, "输出无效", "请选择一个有效的输出文件夹。"); return
        if not os.path.exists(output_dir):
            reply = QMessageBox.question(self, "目录不存在", f"目录 '{output_dir}' 不存在，是否要创建它？", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.Yes: os.makedirs(output_dir)
            else: return

        if not (len(self.split_points) == self.preview_table.rowCount() > 0): QMessageBox.warning(self, "无法处理", "片段数与预览列表中的行数必须匹配。"); return

        self.log("开始批量处理..."); self.process_btn.setEnabled(False)
        self.progress_bar.setRange(0, len(self.split_points)); self.progress_bar.setValue(0)
        
        try:
            offset_ms = int(self.offset_input.text()); output_format = self.output_format_combo.currentText().split(' ')[0].lower()
            offset_samples = int(offset_ms / 1000 * self.audio_sr)
            
            for i in range(len(self.split_points)):
                word_item = self.preview_table.item(i, 1)
                if not word_item: continue
                word = word_item.text()
                
                self.progress_bar.setValue(i + 1); self.log(f"正在保存片段 {i+1}/{len(self.split_points)}: {word}"); QApplication.processEvents()

                start, end = self.split_points[i]
                start = max(0, start - offset_samples); end = min(len(self.audio_data), end + offset_samples)
                segment = self.audio_data[start:end]
                
                safe_filename = re.sub(r'[\\/*?:"<>|]', "", word)
                out_path = os.path.join(output_dir, f"{safe_filename}.{output_format}")
                sf.write(out_path, segment, self.audio_sr)

            self.log("所有文件处理完毕！"); QMessageBox.information(self, "处理完成", "所有音频片段已成功分割并保存。")
        except Exception as e:
            self.log(f"处理失败: {e}"); QMessageBox.critical(self, "处理失败", f"在处理过程中发生错误:\n{e}")
        finally:
            self.progress_bar.setValue(self.progress_bar.maximum()); self.process_btn.setEnabled(True)

    def closeEvent(self, event):
        if self.temp_audio_file and os.path.exists(self.temp_audio_file):
            try: os.remove(self.temp_audio_file)
            except: pass
        super().closeEvent(event)
