# --- START OF FILE plugins/quality_analyzer/launcher.py (v1.9 - Final Polish) ---

import os
import sys
import json
import numpy as np
import soundfile as sf
from functools import partial

from PyQt5.QtCore import QObject, pyqtSignal, QRunnable, QThreadPool, Qt
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLineEdit,
                             QPushButton, QDialogButtonBox, QLabel, QGroupBox,
                             QMessageBox, QTableWidgetItem, QCheckBox, QSlider,
                             QHBoxLayout)
from PyQt5.QtGui import QDoubleValidator, QIcon

try:
    from plugin_system import BasePlugin
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'modules')))
    from plugin_system import BasePlugin

# ==============================================================================
# 1. 核心分析逻辑 (无变动)
# ==============================================================================
class AudioAnalyzer:
    def __init__(self, config):
        self.config = config
    def analyze(self, filepath, module_id):
        if not os.path.exists(filepath): return []
        try:
            data, sr = sf.read(filepath, dtype='float32')
            if data.ndim > 1: data = data.mean(axis=1)
            if np.max(np.abs(data)) < 1e-6: return [{'type': 'low_volume', 'details': '音频文件为空或静音'}]
            warnings = []
            clipping_samples = np.sum(np.abs(data) >= 0.99)
            clipping_percent = (clipping_samples / len(data)) * 100
            if clipping_percent > self.config.get('clipping_threshold_percent', 0.5):
                warnings.append({'type': 'clipping', 'details': f'{clipping_percent:.1f}% 的样本被削波'})
            rms = np.sqrt(np.mean(data**2))
            rms_db = 20 * np.log10(rms) if rms > 1e-9 else -180.0
            if rms_db < self.config.get('volume_low_threshold_db', -30.0):
                warnings.append({'type': 'low_volume', 'details': f'平均音量: {rms_db:.1f} dB'})
            elif rms_db > self.config.get('volume_high_threshold_db', -3.0):
                warnings.append({'type': 'high_volume', 'details': f'平均音量: {rms_db:.1f} dB'})
            sorted_energy = np.sort(data**2)
            noise_threshold_index = int(len(sorted_energy) * 0.1)
            noise_energy = np.mean(sorted_energy[:noise_threshold_index])
            signal_energy = np.mean(sorted_energy[noise_threshold_index:])
            if noise_energy > 0 and signal_energy > 0:
                snr = 10 * np.log10(signal_energy / noise_energy)
                if snr < self.config.get('snr_low_threshold_db', 15.0):
                    warnings.append({'type': 'low_snr', 'details': f'估算信噪比: {snr:.1f} dB'})
            sd_config = self.config.get('silence_detection', {})
            if module_id not in sd_config.get('disable_for_modules', []):
                min_duration_ms = sd_config.get('min_duration_ms', 300)
                trunc_ms = sd_config.get('end_truncation_ms', 50)
                absolute_min_thresh_amp = 10**(-50.0 / 20)
                frame_size = int(sr * 0.02)
                if len(data) < frame_size: return warnings
                num_frames = len(data) // frame_size
                frames = data[:num_frames * frame_size].reshape(num_frames, frame_size)
                rms_per_frame = np.sqrt(np.mean(frames**2, axis=1))
                dynamic_silence_thresh_amp = 0
                if len(rms_per_frame) > 0:
                    noise_floor_rms = np.percentile(rms_per_frame, 5)
                    dynamic_silence_thresh_amp = noise_floor_rms * 3.162
                final_thresh = max(dynamic_silence_thresh_amp, absolute_min_thresh_amp)
                is_silent = np.abs(data) < final_thresh
                if not np.any(~is_silent):
                    total_duration_ms = (len(data) / sr) * 1000
                    if total_duration_ms > min_duration_ms:
                        warnings.append({'type': 'leading_silence', 'details': f'整个文件均为静音 ({total_duration_ms:.0f} ms)'})
                else:
                    first_sound_index = np.argmax(~is_silent)
                    leading_silence_ms = (first_sound_index / sr) * 1000
                    if leading_silence_ms > min_duration_ms:
                        warnings.append({'type': 'leading_silence', 'details': f'开头静音 {leading_silence_ms:.0f} ms'})
                    last_sound_index = len(data) - 1 - np.argmax(~is_silent[::-1])
                    trailing_silence_ms = ((len(data) - 1 - last_sound_index) / sr) * 1000
                    if trailing_silence_ms > min_duration_ms:
                        warnings.append({'type': 'trailing_silence', 'details': f'结尾静音 {trailing_silence_ms:.0f} ms'})
                    if trailing_silence_ms < trunc_ms:
                         warnings.append({'type': 'end_truncation', 'details': f'结尾过快 ({trailing_silence_ms:.0f} ms)'})
            return warnings
        except Exception as e:
            print(f"[Quality Analyzer] Error analyzing {filepath}: {e}")
            return []

# ==============================================================================
# 2. 设置对话框 (使用滑块)
# ==============================================================================
class AnalyzerSettingsDialog(QDialog):
    def __init__(self, config_path, parent=None):
        super().__init__(parent)
        self.config_path = config_path
        self.settings = self._load_config()
        self.setWindowTitle("语音质量分析器 - 设置")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        
        def create_slider_row(label, value, min_val, max_val, suffix, tooltip, is_float=False):
            slider = QSlider(Qt.Horizontal)
            slider.setRange(min_val, max_val)
            slider.setValue(int(value * 10) if is_float else int(value))
            slider.setToolTip(tooltip)
            
            label_widget = QLabel()
            
            def update_label(val):
                display_val = val / 10.0 if is_float else val
                label_widget.setText(f"{display_val:.1f} {suffix}" if is_float else f"{display_val} {suffix}")

            slider.valueChanged.connect(update_label)
            update_label(slider.value())
            
            row_layout = QHBoxLayout()
            row_layout.addWidget(slider)
            row_layout.addWidget(label_widget)
            return row_layout, slider

        threshold_group = QGroupBox("严重警告阈值设置")
        threshold_form = QFormLayout(threshold_group)
        
        layout_vol_low, self.vol_low_slider = create_slider_row("音量过低 (dBFS)", self.settings.get('volume_low_threshold_db', -30.0), -60, -20, "dB", "低于此平均音量将触发警告。")
        layout_vol_high, self.vol_high_slider = create_slider_row("音量过高 (dBFS)", self.settings.get('volume_high_threshold_db', -3.0), -20, 0, "dB", "高于此平均音量将触发警告。")
        layout_clip, self.clip_slider = create_slider_row("信号削波 (%)", self.settings.get('clipping_threshold_percent', 0.5), 1, 100, "%", "削波样本超过此百分比将触发警告。", is_float=True)
        layout_snr, self.snr_slider = create_slider_row("信噪比 (dB)", self.settings.get('snr_low_threshold_db', 15.0), 5, 40, "dB", "低于此估算信噪比将触发警告。")

        threshold_form.addRow("音量过低:", layout_vol_low)
        threshold_form.addRow("音量过高:", layout_vol_high)
        threshold_form.addRow("削波比例:", layout_clip)
        threshold_form.addRow("最低信噪比:", layout_snr)
        layout.addWidget(threshold_group)

        sd_config = self.settings.get('silence_detection', {})
        sd_group = QGroupBox("提示性信息阈值设置")
        sd_form = QFormLayout(sd_group)
        layout_sd_duration, self.sd_duration_slider = create_slider_row("静音时长 (ms)", sd_config.get('min_duration_ms', 300), 100, 2000, "ms", "开头/结尾静音超过此时长将触发提示。")
        layout_sd_trunc, self.sd_trunc_slider = create_slider_row("截断时长 (ms)", sd_config.get('end_truncation_ms', 50), 10, 200, "ms", "结尾静音短于此时长将被视为截断。")
        
        self.disable_for_accent_check = QCheckBox("对'标准朗读'模块禁用此检测")
        self.disable_for_accent_check.setToolTip("标准朗读任务通常包含提示音，其静音时长不固定，建议禁用此检测。")
        self.disable_for_accent_check.setChecked('accent_collection' in sd_config.get('disable_for_modules', []))
        
        sd_form.addRow("最长静音:", layout_sd_duration)
        sd_form.addRow("最短结尾:", layout_sd_trunc)
        sd_form.addRow(self.disable_for_accent_check)
        layout.addWidget(sd_group)

        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.on_save)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _load_config(self):
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f: return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"enabled": True, "volume_low_threshold_db": -30.0, "volume_high_threshold_db": -3.0, "clipping_threshold_percent": 0.5, "snr_low_threshold_db": 15.0, "silence_detection": {"disable_for_modules": ["accent_collection"], "min_duration_ms": 300, "end_truncation_ms": 50}}

    def on_save(self):
        self.settings['volume_low_threshold_db'] = self.vol_low_slider.value()
        self.settings['volume_high_threshold_db'] = self.vol_high_slider.value()
        self.settings['clipping_threshold_percent'] = self.clip_slider.value() / 10.0
        self.settings['snr_low_threshold_db'] = self.snr_slider.value()
        sd_config = self.settings.setdefault('silence_detection', {})
        sd_config['min_duration_ms'] = self.sd_duration_slider.value()
        sd_config['end_truncation_ms'] = self.sd_trunc_slider.value()
        disabled_modules = sd_config.setdefault('disable_for_modules', [])
        if self.disable_for_accent_check.isChecked():
            if 'accent_collection' not in disabled_modules: disabled_modules.append('accent_collection')
        else:
            if 'accent_collection' in disabled_modules: disabled_modules.remove('accent_collection')
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f: json.dump(self.settings, f, indent=4)
            QMessageBox.information(self, "成功", "设置已保存。")
            self.accept()
        except Exception as e: QMessageBox.critical(self, "错误", f"无法保存设置: {e}")

# ==============================================================================
# 3. 后台工作器 (无变动)
# ==============================================================================
class AnalysisSignals(QObject):
    finished = pyqtSignal(str, int, list)
class AnalysisWorker(QRunnable):
    def __init__(self, analyzer, module_id, filepath, row):
        super().__init__()
        self.analyzer = analyzer; self.module_id = module_id; self.filepath = filepath; self.row = row
        self.signals = AnalysisSignals()
    def run(self):
        warnings = self.analyzer.analyze(self.filepath, self.module_id)
        self.signals.finished.emit(self.module_id, self.row, warnings)

# ==============================================================================
# 4. 插件主类 (增加分级图标逻辑)
# ==============================================================================
class QualityAnalyzerPlugin(BasePlugin):
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.plugin_dir = os.path.dirname(__file__)
        self.config_path = os.path.join(self.plugin_dir, 'config.json')
        self.config = self._load_config()
        self.analyzer = AudioAnalyzer(self.config)
        self.settings_dialog = None
        self.hooked_modules = {}
        self.thread_pool = QThreadPool()
        self._load_icons()
        self.warning_type_map = {'low_volume': '音量过低', 'high_volume': '音量过高', 'clipping': '信号削波', 'low_snr': '信噪比低', 'leading_silence': '开头静音过长', 'trailing_silence': '结尾静音过长', 'end_truncation': '结尾截断'}
        # [新增] 定义严重警告类型
        self.critical_warnings = {'low_volume', 'high_volume', 'clipping', 'low_snr', 'end_truncation'}
        
    def _load_config(self):
        if not os.path.exists(self.config_path):
            default_config = {"enabled": True, "volume_low_threshold_db": -30.0, "volume_high_threshold_db": -3.0, "clipping_threshold_percent": 0.5, "snr_low_threshold_db": 15.0, "silence_detection": {"disable_for_modules": ["accent_collection"], "min_duration_ms": 300, "end_truncation_ms": 50}}
            try:
                with open(self.config_path, 'w', encoding='utf-8') as f: json.dump(default_config, f, indent=4)
                return default_config
            except Exception: return {}
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f: return json.load(f)
        except (json.JSONDecodeError, IOError): return {}
        
    def _load_icons(self):
        self.warning_icon = QIcon()
        self.info_icon = QIcon()
        icon_dir = os.path.join(self.plugin_dir, 'icons')
        
        # 加载严重警告图标
        for ext in ['.svg', '.png']:
            path = os.path.join(icon_dir, f"warning{ext}")
            if os.path.exists(path): self.warning_icon = QIcon(path); break
        
        # 加载提示性信息图标
        for ext in ['.svg', '.png']:
            path = os.path.join(icon_dir, f"info{ext}")
            if os.path.exists(path): self.info_icon = QIcon(path); break

    def setup(self):
        target_modules = {'accent_collection': 'accent_collection_page', 'dialect_visual_collector': 'dialect_visual_page', 'voicebank_recorder': 'voicebank_recorder_page'}
        for module_id, attr_name in target_modules.items():
            module_instance = getattr(self.main_window, attr_name, None)
            if module_instance:
                setattr(module_instance, 'quality_analyzer_plugin', self)
                self.hooked_modules[module_id] = module_instance
                print(f"[Quality Analyzer] 已向模块 '{attr_name}' 注册。")
            else:
                print(f"[Quality Analyzer] 警告: 未在主窗口中找到模块实例 '{attr_name}'。")
        return len(self.hooked_modules) > 0
        
    def teardown(self):
        for module_id, module_instance in self.hooked_modules.items():
            if hasattr(module_instance, 'quality_analyzer_plugin'): delattr(module_instance, 'quality_analyzer_plugin')
        print(f"[Quality Analyzer] 已从 {len(self.hooked_modules)} 个模块注销。")
        self.hooked_modules.clear()
        self.thread_pool.clear()
        if self.settings_dialog: self.settings_dialog.close()
        
    def execute(self, **kwargs):
        if self.settings_dialog is None:
            self.settings_dialog = AnalyzerSettingsDialog(self.config_path, self.main_window)
            self.settings_dialog.finished.connect(self._on_settings_closed)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()
        
    def _on_settings_closed(self):
        self.settings_dialog = None
        self.config = self._load_config()
        self.analyzer = AudioAnalyzer(self.config)
        print("[Quality Analyzer] 配置已重新加载。")
        
    def analyze_and_update_ui(self, module_id, filepath, row):
        if not self.config.get('enabled', True): return
        worker = AnalysisWorker(self.analyzer, module_id, filepath, row)
        worker.signals.finished.connect(self._on_analysis_finished)
        self.thread_pool.start(worker)
        
    def _on_analysis_finished(self, module_id, row, warnings):
        module_instance = self.hooked_modules.get(module_id)
        if not module_instance: return
        if hasattr(module_instance, 'list_widget'):
            table = module_instance.list_widget
            if row >= table.rowCount(): return
            item = table.item(row, 0)
        elif hasattr(module_instance, 'item_list_widget'):
            list_widget = module_instance.item_list_widget
            if row >= list_widget.count(): return
            item = list_widget.item(row)
        else: return
        if not item: return
        original_tooltip = item.text()
        
        if not warnings:
            item.setIcon(self.main_window.icon_manager.get_icon("success"))
            item.setToolTip(original_tooltip)
        else:
            # [核心修复] 根据警告类型设置不同图标
            has_critical = any(w['type'] in self.critical_warnings for w in warnings)
            if has_critical:
                item.setIcon(self.warning_icon)
            else:
                item.setIcon(self.info_icon)
            
            html = f"<b>{original_tooltip}</b><hr>"
            html += "<b>质量报告:</b><br>"
            warning_list_html = []
            for warning in warnings:
                formatted_type = self.warning_type_map.get(warning['type'], warning['type'])
                details = warning.get('details', '无详情')
                warning_list_html.append(f"• <b>{formatted_type}:</b> {details}")
            html += "<br>".join(warning_list_html)
            item.setToolTip(html)