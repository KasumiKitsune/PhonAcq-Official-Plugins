# --- START OF FILE plugins/quality_analyzer/launcher.py (v2.0 - Decoupled & Enhanced) ---

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
    # ... 此类代码与上一版完全相同，无需修改 ...
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
                # [核心修改] 读取分离的阈值
                leading_ms = sd_config.get('leading_silence_ms', 300)
                trailing_ms = sd_config.get('trailing_silence_ms', 300)
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
                    if total_duration_ms > leading_ms:
                        warnings.append({'type': 'leading_silence', 'details': f'整个文件均为静音 ({total_duration_ms:.0f} ms)'})
                else:
                    first_sound_index = np.argmax(~is_silent)
                    leading_silence_ms = (first_sound_index / sr) * 1000
                    if leading_silence_ms > leading_ms: # [核心修改] 使用开头阈值
                        warnings.append({'type': 'leading_silence', 'details': f'开头静音 {leading_silence_ms:.0f} ms'})
                    last_sound_index = len(data) - 1 - np.argmax(~is_silent[::-1])
                    trailing_silence_ms = ((len(data) - 1 - last_sound_index) / sr) * 1000
                    if trailing_silence_ms > trailing_ms: # [核心修改] 使用结尾阈值
                        warnings.append({'type': 'trailing_silence', 'details': f'结尾静音 {trailing_silence_ms:.0f} ms'})
                    if trailing_silence_ms < trunc_ms:
                         warnings.append({'type': 'end_truncation', 'details': f'结尾过快 ({trailing_silence_ms:.0f} ms)'})
            return warnings
        except Exception as e:
            print(f"[Quality Analyzer] Error analyzing {filepath}: {e}")
            return []

# ==============================================================================
# 2. 设置对话框 (UI修改)
# ==============================================================================
class AnalyzerSettingsDialog(QDialog):
    # ... __init__ 和 _load_config 基本不变 ...
    def __init__(self, config_path, parent=None):
        super().__init__(parent)
        self.config_path = config_path; self.settings = self._load_config()
        self.setWindowTitle("语音质量分析器 - 设置"); self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        
        def create_slider_row(label, value, min_val, max_val, suffix, tooltip, is_float=False):
            # ... 此辅助函数不变 ...
            slider = QSlider(Qt.Horizontal); slider.setRange(min_val, max_val)
            slider.setValue(int(value * 10) if is_float else int(value)); slider.setToolTip(tooltip)
            label_widget = QLabel()
            def update_label(val): display_val = val / 10.0 if is_float else val; label_widget.setText(f"{display_val:.1f} {suffix}" if is_float else f"{display_val} {suffix}")
            slider.valueChanged.connect(update_label); update_label(slider.value())
            row_layout = QHBoxLayout(); row_layout.addWidget(slider); row_layout.addWidget(label_widget)
            return row_layout, slider

        # 严重警告部分
        threshold_group = QGroupBox("严重警告阈值 (建议重录)"); threshold_form = QFormLayout(threshold_group)
        layout_vol_low, self.vol_low_slider = create_slider_row("音量过低 (dBFS)", self.settings.get('volume_low_threshold_db', -30.0), -60, -20, "dB", "低于此平均音量将触发警告。")
        layout_vol_high, self.vol_high_slider = create_slider_row("音量过高 (dBFS)", self.settings.get('volume_high_threshold_db', -3.0), -20, 0, "dB", "高于此平均音量将触发警告。")
        layout_clip, self.clip_slider = create_slider_row("信号削波 (%)", self.settings.get('clipping_threshold_percent', 0.5), 1, 100, "%", "削波样本超过此百分比将触发警告。", is_float=True)
        layout_snr, self.snr_slider = create_slider_row("信噪比 (dB)", self.settings.get('snr_low_threshold_db', 15.0), 5, 40, "dB", "低于此估算信噪比将触发警告。")
        # [核心修改] 将截断检测移到严重警告组
        sd_config_for_trunc = self.settings.get('silence_detection', {})
        layout_sd_trunc, self.sd_trunc_slider = create_slider_row("最短结尾时长 (ms)", sd_config_for_trunc.get('end_truncation_ms', 50), 10, 200, "ms", "结尾静音短于此时长将被视为'结尾截断'，这是一个严重问题。")
        threshold_form.addRow("音量过低:", layout_vol_low); threshold_form.addRow("音量过高:", layout_vol_high); threshold_form.addRow("削波比例:", layout_clip); threshold_form.addRow("最低信噪比:", layout_snr); threshold_form.addRow("结尾截断:", layout_sd_trunc)
        layout.addWidget(threshold_group)

        # 提示性信息部分
        sd_config = self.settings.get('silence_detection', {})
        sd_group = QGroupBox("提示性信息阈值 (可忽略)"); sd_form = QFormLayout(sd_group)
        # [核心修改] 分离为两个滑块
        layout_leading_silence, self.leading_silence_slider = create_slider_row("开头静音 (ms)", sd_config.get('leading_silence_ms', 300), 100, 2000, "ms", "开头静音超过此时长将触发提示。")
        layout_trailing_silence, self.trailing_silence_slider = create_slider_row("结尾静音 (ms)", sd_config.get('trailing_silence_ms', 300), 100, 2000, "ms", "结尾静音超过此时长将触发提示。")
        self.disable_for_accent_check = QCheckBox("对'标准朗读'模块禁用静音/截断检测"); self.disable_for_accent_check.setToolTip("标准朗读任务通常包含提示音，其静音时长不固定，建议禁用此检测。")
        self.disable_for_accent_check.setChecked('accent_collection' in sd_config.get('disable_for_modules', []))
        sd_form.addRow("最长开头静音:", layout_leading_silence); sd_form.addRow("最长结尾静音:", layout_trailing_silence); sd_form.addRow(self.disable_for_accent_check)
        layout.addWidget(sd_group)

        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); button_box.accepted.connect(self.on_save); button_box.rejected.connect(self.reject); layout.addWidget(button_box)

    def _load_config(self):
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f: return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"enabled": True, "volume_low_threshold_db": -30.0, "volume_high_threshold_db": -3.0, "clipping_threshold_percent": 0.5, "snr_low_threshold_db": 15.0, "silence_detection": {"disable_for_modules": ["accent_collection"], "leading_silence_ms": 300, "trailing_silence_ms": 300, "end_truncation_ms": 50}}

    def on_save(self):
        self.settings['volume_low_threshold_db'] = self.vol_low_slider.value(); self.settings['volume_high_threshold_db'] = self.vol_high_slider.value(); self.settings['clipping_threshold_percent'] = self.clip_slider.value() / 10.0; self.settings['snr_low_threshold_db'] = self.snr_slider.value()
        sd_config = self.settings.setdefault('silence_detection', {});
        # [核心修改] 保存分离的阈值
        sd_config['leading_silence_ms'] = self.leading_silence_slider.value()
        sd_config['trailing_silence_ms'] = self.trailing_silence_slider.value()
        sd_config['end_truncation_ms'] = self.sd_trunc_slider.value()
        disabled_modules = sd_config.setdefault('disable_for_modules', [])
        if self.disable_for_accent_check.isChecked():
            if 'accent_collection' not in disabled_modules: disabled_modules.append('accent_collection')
        else:
            if 'accent_collection' in disabled_modules: disabled_modules.remove('accent_collection')
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f: json.dump(self.settings, f, indent=4)
            QMessageBox.information(self, "成功", "设置已保存。"); self.accept()
        except Exception as e: QMessageBox.critical(self, "错误", f"无法保存设置: {e}")

# ==============================================================================
# 3. 后台工作器 (无变动)
# ==============================================================================
class AnalysisSignals(QObject):
    finished = pyqtSignal(str, int, list)
class AnalysisWorker(QRunnable):
    def __init__(self, analyzer, module_id, filepath, row):
        super().__init__(); self.analyzer = analyzer; self.module_id = module_id; self.filepath = filepath; self.row = row
        self.signals = AnalysisSignals()
    def run(self):
        warnings = self.analyzer.analyze(self.filepath, self.module_id)
        self.signals.finished.emit(self.module_id, self.row, warnings)

# ==============================================================================
# 4. 插件主类 (核心修改)
# ==============================================================================
class QualityAnalyzerPlugin(BasePlugin):
    # ... __init__, _load_config, _load_icons 基本不变 ...
    def __init__(self, main_window, plugin_manager):
        super().__init__(main_window, plugin_manager)
        self.plugin_dir = os.path.dirname(__file__); self.config_path = os.path.join(self.plugin_dir, 'config.json')
        self.config = self._load_config(); self.analyzer = AudioAnalyzer(self.config); self.settings_dialog = None
        self.hooked_modules = {}; self.thread_pool = QThreadPool()
        self._load_icons()
        self.warning_type_map = {'low_volume': '音量过低', 'high_volume': '音量过高', 'clipping': '信号削波', 'low_snr': '信噪比低', 'leading_silence': '开头静音过长', 'trailing_silence': '结尾静音过长', 'end_truncation': '结尾截断'}
        # [核心修改] 将结尾截断加入严重警告
        self.critical_warnings = {'low_volume', 'high_volume', 'clipping', 'low_snr', 'end_truncation'}
    
    def _load_config(self):
        if not os.path.exists(self.config_path):
            default_config = {"enabled": True, "volume_low_threshold_db": -30.0, "volume_high_threshold_db": -3.0, "clipping_threshold_percent": 0.5, "snr_low_threshold_db": 15.0, "silence_detection": {"disable_for_modules": ["accent_collection"], "leading_silence_ms": 300, "trailing_silence_ms": 300, "end_truncation_ms": 50}}
            try:
                with open(self.config_path, 'w', encoding='utf-8') as f: json.dump(default_config, f, indent=4)
                return default_config
            except Exception: return {}
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f: return json.load(f)
        except (json.JSONDecodeError, IOError): return {}
        
    def _load_icons(self):
        self.warning_icon = QIcon(); self.info_icon = QIcon(); icon_dir = os.path.join(self.plugin_dir, 'icons')
        for ext in ['.svg', '.png']:
            path = os.path.join(icon_dir, f"warning{ext}")
            if os.path.exists(path): self.warning_icon = QIcon(path); break
        for ext in ['.svg', '.png']:
            path = os.path.join(icon_dir, f"info{ext}")
            if os.path.exists(path): self.info_icon = QIcon(path); break

    def setup(self):
        # [核心修改] 修正 dialect_visual_collector_module 的属性名
        target_modules = {
            'accent_collection': 'accent_collection_page',
            'dialect_visual_collector': 'dialect_visual_page', # 之前可能是 'dialect_visual_collector_module'
            'voicebank_recorder': 'voicebank_recorder_page'
        }
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
        # ... 此方法不变 ...
        for module_id, module_instance in self.hooked_modules.items():
            if hasattr(module_instance, 'quality_analyzer_plugin'): delattr(module_instance, 'quality_analyzer_plugin')
        print(f"[Quality Analyzer] 已从 {len(self.hooked_modules)} 个模块注销。")
        self.hooked_modules.clear(); self.thread_pool.clear()
        if self.settings_dialog: self.settings_dialog.close()
        
    def execute(self, **kwargs):
        # ... 此方法不变 ...
        if self.settings_dialog is None:
            self.settings_dialog = AnalyzerSettingsDialog(self.config_path, self.main_window)
            self.settings_dialog.finished.connect(self._on_settings_closed)
        self.settings_dialog.show(); self.settings_dialog.raise_(); self.settings_dialog.activateWindow()
        
    def _on_settings_closed(self):
        # ... 此方法不变 ...
        self.settings_dialog = None; self.config = self._load_config(); self.analyzer = AudioAnalyzer(self.config)
        print("[Quality Analyzer] 配置已重新加载。")
        
    def analyze_and_update_ui(self, module_id, filepath, row):
        # ... 此方法不变 ...
        if not self.config.get('enabled', True): return
        worker = AnalysisWorker(self.analyzer, module_id, filepath, row); worker.signals.finished.connect(self._on_analysis_finished)
        self.thread_pool.start(worker)
        
    def _on_analysis_finished(self, module_id, row, warnings):
        """[核心修改] 调用宿主模块的回调函数，而不是直接操作UI。"""
        module_instance = self.hooked_modules.get(module_id)
        if module_instance and hasattr(module_instance, 'update_item_quality_status'):
            # 将分析结果交还给宿主模块处理
            module_instance.update_item_quality_status(row, warnings)