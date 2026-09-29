import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import time
import re
import math
import struct
import queue
import serial
import serial.tools.list_ports

class RealtimePlotter(tk.Canvas):
    """
    归一化多单位实时示波器组件 (60 FPS 电竞级极速版)。
    支持同一 Y 轴多单位 (高度 counts / 速度 RPM / 电流 A) 标定对比。
    """
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.bg_color = "#121214"
        self.grid_color = "#23232A"
        self.target_color = "#3B82F6"
        self.diff_color = "#10B981"
        self.text_color = "#8E9297"
        
        self.axis_colors = ["#EAB308", "#EC4899", "#8B5CF6", "#06B6D4"]
        
        self.configure(bg=self.bg_color, highlightthickness=0)
        self.bind("<Configure>", self.on_resize)
        
        self.data_buffer = []
        self.paused = False
        self.window_size_ms = 5000 
        
        self.visible_curves = {
            'diff': True,
            'halls': True,
            'speeds': True,
            'currents': False
        }
        
        self.counts_per_mm = 112.5 # 减速比30 * 霍尔系数30 / 导程8mm = 112.5 counts/mm
        self.user_diff_limit_mm = 5.0 # 误差量程 (mm)，默认 5.0mm
        self.user_diff_limit = int(round(5.0 * 112.5)) # 自动换算后的 counts (5.0 * 112.5 = 563c)
        self.max_sync_limit_hall = 563
        self.max_speed_limit_rpm = 3000.0
        self.max_current_limit_deciA = 500.0
        
    def on_resize(self, event):
        self.redraw()
        
    def clear(self):
        self.data_buffer.clear()
        self.redraw()
        
    def set_curve_visible(self, curve_key, is_visible):
        self.visible_curves[curve_key] = is_visible
        self.redraw()
        
    def set_diff_limit_mm(self, limit_mm):
        try:
            val = float(limit_mm)
            if val > 0:
                self.user_diff_limit_mm = val
                self.user_diff_limit = max(1, int(round(val * self.counts_per_mm)))
                self.redraw()
                return self.user_diff_limit
        except Exception:
            pass
        return None
            
    def add_points_batch(self, points_batch):
        """批量推送数据点"""
        if self.paused or not points_batch:
            return
            
        for t_ms, data_dict in points_batch:
            self.data_buffer.append((t_ms, data_dict))
            
        # O(1) 弹出过期的头部节点
        if self.data_buffer:
            t_latest = self.data_buffer[-1][0]
            cutoff = t_latest - self.window_size_ms - 1000
            while self.data_buffer and self.data_buffer[0][0] < cutoff:
                self.data_buffer.pop(0)
        
    def redraw(self):
        self.delete("all")
        
        width = self.winfo_width()
        height = self.winfo_height()
        
        if width < 70 or height < 50:
            return
            
        pad_left = 90
        pad_right = 75
        pad_top = 35
        pad_bottom = 40
        
        plot_width = width - pad_left - pad_right
        plot_height = height - pad_top - pad_bottom
        
        # 1. 边框与背景
        self.create_rectangle(pad_left, pad_top, pad_left + plot_width, pad_top + plot_height, 
                               fill=self.bg_color, outline="#2A2A35", width=1.5)
        
        limit_hall = self.user_diff_limit if self.user_diff_limit > 0 else int(round(5.0 * self.counts_per_mm))
        limit_mm = self.user_diff_limit_mm if self.user_diff_limit_mm > 0 else (limit_hall / self.counts_per_mm)
        limit_rpm = int(self.max_speed_limit_rpm)
        limit_curr = self.max_current_limit_deciA / 100.0
        
        y_mid = pad_top + plot_height / 2.0
        half_height = plot_height / 2.0
        
        # 相对平均高度的高度差 Y 坐标转换函数 (+limit 对应画布顶部，-limit 对应画布底部，0 对应中心横线)
        def diff_to_y(d_val):
            ratio = float(d_val) / float(limit_hall)
            ratio_clamped = max(min(ratio, 1.0), -1.0) # 严格限幅在上下边框内，杜绝穿出画布
            y = y_mid - (ratio_clamped * half_height)
            return max(pad_top, min(pad_top + plot_height, y))
            
        # 速度/电流 0~1.0 全高 Y 坐标转换函数 (底部 0，顶部满量程)
        def ratio_to_y(ratio):
            ratio_clamped = max(min(ratio, 1.0), 0.0) # 严格限幅在底端到顶端
            y = pad_top + plot_height - (ratio_clamped * plot_height)
            return max(pad_top, min(pad_top + plot_height, y))
            
        # 计算当前窗口最新数据帧的平均高度 (用于实时更新中间横线数值)
        latest_avg_h = 0.0
        if self.data_buffer:
            _, latest_d = self.data_buffer[-1]
            if 'halls' in latest_d and len(latest_d['halls']) >= 4:
                latest_avg_h = sum(latest_d['halls'][:4]) / 4.0
            elif 'H0' in latest_d:
                h_vals = [latest_d.get(f'H{i}', 0) for i in range(4)]
                latest_avg_h = sum(h_vals) / 4.0
            
        # 2. 刻度与标注
        is_halls_visible = self.visible_curves.get('halls', False)
        
        # 左侧 Y 轴刻度 (以 mm 为主，附带 counts 换算值)
        self.create_text(pad_left - 10, pad_top, text=f"+{limit_mm:.1f}mm", fill="#10B981", anchor="e", font=("Consolas", 8, "bold"))
        self.create_text(pad_left - 10, pad_top + 12, text=f"(+{limit_hall}c)", fill="#8E9297", anchor="e", font=("Consolas", 7))
        
        if is_halls_visible:
            self.create_text(pad_left - 10, y_mid - 6, text="±0mm", fill="#00ADB5", anchor="e", font=("Consolas", 8, "bold"))
            self.create_text(pad_left - 10, y_mid + 7, text=f"Avg:{int(latest_avg_h)}c", fill="#00ADB5", anchor="e", font=("Consolas", 7))
        else:
            self.create_text(pad_left - 10, y_mid, text="0mm (0c)", fill="#8E9297", anchor="e", font=("Consolas", 8))
            
        self.create_text(pad_left - 10, pad_top + plot_height - 12, text=f"-{limit_mm:.1f}mm", fill="#10B981", anchor="e", font=("Consolas", 8, "bold"))
        self.create_text(pad_left - 10, pad_top + plot_height, text=f"(-{limit_hall}c)", fill="#8E9297", anchor="e", font=("Consolas", 7))
        
        # 右侧 Y 轴刻度 (转速/电流，底端 0，顶端满量程)
        self.create_text(pad_left + plot_width + 10, pad_top, text=f"{limit_rpm}RPM", fill="#3B82F6", anchor="w", font=("Consolas", 8, "bold"))
        self.create_text(pad_left + plot_width + 10, pad_top + 12, text=f"({limit_curr:.1f}A)", fill="#EAB308", anchor="w", font=("Consolas", 7))
        self.create_text(pad_left + plot_width + 10, y_mid, text=f"{limit_rpm//2}RPM", fill="#8E9297", anchor="w", font=("Consolas", 8))
        self.create_text(pad_left + plot_width + 10, pad_top + plot_height, text="0 RPM/A", fill=self.target_color, anchor="w", font=("Consolas", 8, "bold"))
        
        # 3. 水平网格线与固定在中间的平均高度中轴横线
        if is_halls_visible:
            # 仅在勾选“4轴高度差”时显示青色中轴横线与平均高度动态文字
            self.create_line(pad_left, y_mid, pad_left + plot_width, y_mid, fill="#00ADB5", width=1.8, dash=(6, 4))
            self.create_text(pad_left + 15, y_mid - 12, 
                             text=f"── 平均高度基准中轴线 Avg: {latest_avg_h:.0f}c ({latest_avg_h/self.counts_per_mm:.1f}mm) ──", 
                             fill="#00ADB5", anchor="w", font=("Consolas", 8, "bold"))
        else:
            # 未勾选时，作为普通低亮网格辅助虚线
            self.create_line(pad_left, y_mid, pad_left + plot_width, y_mid, fill=self.grid_color, dash=(2, 2))
        
        # 上下 1/2 差值辅助网格线
        y_quarter_top = pad_top + half_height * 0.5
        y_quarter_btm = pad_top + half_height * 1.5
        self.create_line(pad_left, y_quarter_top, pad_left + plot_width, y_quarter_top, fill=self.grid_color, dash=(2, 2))
        self.create_line(pad_left, y_quarter_btm, pad_left + plot_width, y_quarter_btm, fill=self.grid_color, dash=(2, 2))
        
        # 底部和顶部网格线
        self.create_line(pad_left, pad_top, pad_left + plot_width, pad_top, fill=self.grid_color, dash=(2, 2))
        self.create_line(pad_left, pad_top + plot_height, pad_left + plot_width, pad_top + plot_height, fill=self.target_color, width=1.2, dash=(4, 3))
        
        if not self.data_buffer:
            self.create_text(pad_left + plot_width/2, y_mid, 
                             text="等待数据流输入...", fill="#5E6268", font=("Segoe UI", 10))
            return
            
        # 4. 时间轴范围
        t_latest = self.data_buffer[-1][0]
        x_max = t_latest
        x_min = t_latest - self.window_size_ms
        
        def time_to_x(t_ms):
            return pad_left + ((t_ms - x_min) / self.window_size_ms) * plot_width
            
        sec_start = math.ceil(x_min / 1000.0)
        sec_end = math.floor(x_max / 1000.0)
        for s in range(sec_start, sec_end + 1):
            px = time_to_x(s * 1000.0)
            self.create_line(px, pad_top, px, pad_top + plot_height, fill=self.grid_color, dash=(2, 2))
            self.create_text(px, pad_top + plot_height + 15, text=f"{s:.1f}s", fill=self.text_color, anchor="n", font=("Consolas", 8))
            
        window_points = [d for d in self.data_buffer if d[0] >= x_min]
        if not window_points:
            return
            
        # A. 4轴转速 (虚线，对应右侧 Y 轴)
        if self.visible_curves['speeds']:
            for ch in range(4):
                pts = []
                for t_ms, d in window_points:
                    v_val = d.get(f'V{ch}', 0)
                    if 'speeds' in d and len(d['speeds']) > ch:
                        v_val = d['speeds'][ch]
                    ratio = float(v_val) / self.max_speed_limit_rpm
                    pts.extend([time_to_x(t_ms), ratio_to_y(ratio)])
                if len(pts) >= 4:
                    self.create_line(pts, fill=self.axis_colors[ch], width=1.5, dash=(4, 2))
                    
        # B. 4轴电流 (点虚线，对应右侧 Y 轴)
        if self.visible_curves['currents']:
            for ch in range(4):
                pts = []
                for t_ms, d in window_points:
                    c_val = d.get(f'I{ch}', 0)
                    if 'currents' in d and len(d['currents']) > ch:
                        c_val = d['currents'][ch]
                    ratio = float(c_val) / self.max_current_limit_deciA
                    pts.extend([time_to_x(t_ms), ratio_to_y(ratio)])
                if len(pts) >= 4:
                    self.create_line(pts, fill=self.axis_colors[ch], width=1.2, dash=(1, 3))
                    
        # C. 4轴高度差 (H0~H3 围绕中间平均高度缠绕穿梭，粗实线)
        if self.visible_curves['halls']:
            for ch in range(4):
                pts = []
                for t_ms, d in window_points:
                    if 'halls' in d and len(d['halls']) > ch:
                        h_vals = d['halls'][:4]
                    else:
                        h_vals = [d.get(f'H{i}', 0) for i in range(4)]
                    point_avg = sum(h_vals) / 4.0
                    diff_h = h_vals[ch] - point_avg # 相对当前时刻平均高度的差值！
                    pts.extend([time_to_x(t_ms), diff_to_y(diff_h)])
                if len(pts) >= 4:
                    self.create_line(pts, fill=self.axis_colors[ch], width=2.0)
                    
        # D. 同步差值 MaxDiff 曲线 (荧光绿粗实线，以中轴 0 偏差线向上升起)
        if self.visible_curves['diff']:
            pts = []
            for t_ms, d in window_points:
                diff_val = d.get('diff', 0)
                if 'diff' not in d:
                    h_vals = [d[k] for k in ['H0', 'H1', 'H2', 'H3'] if k in d]
                    diff_val = (max(h_vals) - min(h_vals)) if len(h_vals) >= 2 else 0.0
                pts.extend([time_to_x(t_ms), diff_to_y(diff_val)])
            if len(pts) >= 4:
                self.create_line(pts, fill=self.diff_color, width=2.2)

        # 图例
        self.create_line(pad_left + 10, pad_top - 18, pad_left + 25, pad_top - 18, fill=self.diff_color, width=2.5)
        self.create_text(pad_left + 30, pad_top - 18, text="同步差值(MaxDiff)", fill=self.diff_color, anchor="w", font=("Segoe UI", 8))
        
        if is_halls_visible:
            self.create_line(pad_left + 155, pad_top - 18, pad_left + 175, pad_top - 18, fill="#00ADB5", width=1.8, dash=(6, 4))
            self.create_text(pad_left + 180, pad_top - 18, text="平均高度中轴", fill="#00ADB5", anchor="w", font=("Segoe UI", 8))

        # 4轴全通道电机颜色对应表彩色小提示
        rx = pad_left + plot_width
        ry = pad_top - 18
        self.create_text(rx, ry, text="]", fill="#8E9297", anchor="e", font=("Consolas", 8, "bold"))
        rx -= 6
        self.create_text(rx, ry, text="M3(青)", fill="#06B6D4", anchor="e", font=("Consolas", 8, "bold"))
        rx -= 45
        self.create_text(rx, ry, text="M2(紫)", fill="#8B5CF6", anchor="e", font=("Consolas", 8, "bold"))
        rx -= 45
        self.create_text(rx, ry, text="M1(粉)", fill="#EC4899", anchor="e", font=("Consolas", 8, "bold"))
        rx -= 45
        self.create_text(rx, ry, text="M0(黄)", fill="#EAB308", anchor="e", font=("Consolas", 8, "bold"))
        rx -= 45
        self.create_text(rx, ry, text="[通道颜色: ", fill="#8E9297", anchor="e", font=("Consolas", 8, "bold"))


class ModernPIDAnalyzerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("2026-453/273 升降同步监控上位机 (60 FPS 电竞级极速版)")
        self.root.geometry("1150x680")
        self.root.configure(bg="#121214")
        
        self.ser = None
        self.running = False
        self.rx_thread = None
        self.start_time = None
        
        self.incoming_points_queue = queue.Queue()
        self.log_queue = queue.Queue()
        
        self.log_lines_count = 0
        self.show_stream_log = True # 高频数据流日志默认勾选开启，便于实时观察！
        
        self.rx_raw_buffer = bytearray()
        self.has_printed_header_info = False
        
        self.peak_sync_error = 0.0
        
        self.setup_styles()
        self.create_widgets()
        self.refresh_ports()
        
        # 极速响应定时引擎：16ms (60 FPS) 丝滑连贯重绘与 UI 刷盘
        self.schedule_ui_updates()
        
    def setup_styles(self):
        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        self.style.configure('TFrame', background='#121214')
        self.style.configure('Card.TFrame', background='#1E1E24', relief='flat')
        self.style.configure('TPanedwindow', background='#121214')
        
        self.style.configure('TLabel', background='#121214', foreground='#EEEEEE', font=("Segoe UI", 9))
        self.style.configure('Title.TLabel', background='#1E1E24', foreground='#00ADB5', font=("Segoe UI", 10, "bold"))
        self.style.configure('DarkTitle.TLabel', background='#121214', foreground='#00ADB5', font=("Segoe UI", 10, "bold"))
        
        self.style.configure('TCheckbutton', background='#1E1E24', foreground='#EEEEEE', font=("Segoe UI", 9))
        
        self.style.configure('TButton', background='#3A3F47', foreground='#EEEEEE', borderwidth=0, font=("Segoe UI", 9))
        self.style.map('TButton', background=[('active', '#4E5460')])
        
        self.style.configure('Action.TButton', background='#00ADB5', foreground='#FFFFFF', borderwidth=0, font=("Segoe UI", 9, "bold"))
        self.style.map('Action.TButton', background=[('active', '#00D1D9')])
        
        self.style.configure('Reset.TButton', background='#EF4444', foreground='#FFFFFF', borderwidth=0, font=("Segoe UI", 9, "bold"))
        self.style.map('Reset.TButton', background=[('active', '#F87171')])
        
        self.style.configure('TCombobox', fieldbackground='#3A3F47', background='#3A3F47', foreground='#EEEEEE')

    def create_widgets(self):
        top_bar = ttk.Frame(self.root, style='Card.TFrame')
        top_bar.pack(fill="x", padx=10, pady=5)
        
        title_lbl = ttk.Label(top_bar, text="★ 2026-453/273 MULTI-AXIS SYNC MONITOR (60 FPS ULTRA) ★", style='Title.TLabel', font=("Segoe UI", 11, "bold"))
        title_lbl.pack(side="left", padx=15, pady=10)
        
        self.conn_status_lbl = ttk.Label(top_bar, text="未连接", background="#1E1E24", foreground="#EF4444", font=("Segoe UI", 10, "bold"))
        self.conn_status_lbl.pack(side="right", padx=20, pady=10)
        
        main_container = ttk.Frame(self.root, style='TFrame')
        main_container.pack(fill="both", expand=True, padx=10, pady=5)
        
        left_panel = ttk.Frame(main_container, style='TFrame', width=300)
        left_panel.pack(side="left", fill="both", padx=(0, 5), pady=5)
        left_panel.pack_propagate(False)
        
        conn_card = ttk.Frame(left_panel, style='Card.TFrame')
        conn_card.pack(fill="x", pady=(0, 4), ipady=5)
        
        ttk.Label(conn_card, text="【物理接口配置】", style='Title.TLabel').pack(anchor="w", padx=15, pady=6)
        
        port_frame = ttk.Frame(conn_card, style='TFrame')
        port_frame.pack(fill="x", padx=15, pady=3)
        ttk.Label(port_frame, text="串口选择:", width=8).pack(side="left")
        self.port_cb = ttk.Combobox(port_frame, width=12, state="readonly")
        self.port_cb.pack(side="left", fill="x", expand=True)
        
        baud_frame = ttk.Frame(conn_card, style='TFrame')
        baud_frame.pack(fill="x", padx=15, pady=3)
        ttk.Label(baud_frame, text="波特率值:", width=8).pack(side="left")
        self.baud_cb = ttk.Combobox(baud_frame, values=["9600", "115200", "460800", "921600"], width=12, state="readonly")
        self.baud_cb.set("115200")
        self.baud_cb.pack(side="left", fill="x", expand=True)
        
        btn_conn_frame = ttk.Frame(conn_card, style='TFrame')
        btn_conn_frame.pack(fill="x", padx=15, pady=8)
        ttk.Button(btn_conn_frame, text="刷新串口", command=self.refresh_ports).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.btn_conn = ttk.Button(btn_conn_frame, text="连接设备", style='Action.TButton', command=self.toggle_connection)
        self.btn_conn.pack(side="right", fill="x", expand=True, padx=(4, 0))
        
        ana_card = ttk.Frame(left_panel, style='Card.TFrame')
        ana_card.pack(fill="both", expand=True, pady=(0, 0))
        
        ttk.Label(ana_card, text="【同步指标与状态】", style='Title.TLabel').pack(anchor="w", padx=15, pady=6)
        
        metrics_frame = ttk.Frame(ana_card, style='TFrame')
        metrics_frame.pack(fill="both", expand=True, padx=15, pady=2)
        
        self.metric_target = self.create_metric_row(metrics_frame, "目标差值:", "0", "#3B82F6")
        self.metric_current_diff = self.create_metric_row(metrics_frame, "当前差值 (Current):", "0 (0%)", "#10B981")
        self.metric_max_sync_diff = self.create_metric_row(metrics_frame, "最大差值 (Peak):", "0 (0%)", "#10B981")
        self.metric_status = self.create_metric_row(metrics_frame, "运行状态:", "待命", "#10B981")
        
        # 一键清空主按钮 (波形 -> Peak -> 日志)
        self.btn_clear_all = ttk.Button(ana_card, text="一键清空 (波形/Peak/日志)", style='Reset.TButton', command=self.clear_all)
        self.btn_clear_all.pack(fill="x", padx=15, pady=6)
        
        # 三项独立的单独操作按钮
        btn_ana_frame = ttk.Frame(ana_card, style='TFrame')
        btn_ana_frame.pack(fill="x", padx=15, pady=(3, 10))
        ttk.Button(btn_ana_frame, text="清空日志", command=self.clear_log).pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(btn_ana_frame, text="清空曲线", command=self.clear_chart).pack(side="left", fill="x", expand=True, padx=2)
        ttk.Button(btn_ana_frame, text="重置 Peak", command=self.reset_metrics).pack(side="right", fill="x", expand=True, padx=(2, 0))
        
        right_paned = ttk.PanedWindow(main_container, orient=tk.VERTICAL)
        right_paned.pack(side="right", fill="both", expand=True, padx=(5, 0), pady=5)
        
        chart_card = ttk.Frame(right_paned, style='Card.TFrame')
        
        chart_bar = ttk.Frame(chart_card, style='TFrame')
        chart_bar.pack(fill="x", padx=15, pady=6)
        ttk.Label(chart_bar, text="【归一化示波器 (高度/速度/电流同Y轴对比)】", style='Title.TLabel').pack(side="left")
        
        self.btn_pause = ttk.Button(chart_bar, text="暂停刷新", width=10, command=self.toggle_pause)
        self.btn_pause.pack(side="right")
        
        curve_opt_bar = ttk.Frame(chart_card, style='TFrame')
        curve_opt_bar.pack(fill="x", padx=15, pady=(0, 4))
        
        ttk.Label(curve_opt_bar, text="曲线选择:", foreground="#8E9297").pack(side="left", padx=(0, 5))
        
        self.var_diff = tk.BooleanVar(value=True)
        self.var_halls = tk.BooleanVar(value=True)
        self.var_speeds = tk.BooleanVar(value=True)
        self.var_currents = tk.BooleanVar(value=False)
        
        ttk.Checkbutton(curve_opt_bar, text="轴间差值(Diff)", variable=self.var_diff, command=self.on_curve_opt_change).pack(side="left", padx=5)
        ttk.Checkbutton(curve_opt_bar, text="4轴高度差(H0~H3)", variable=self.var_halls, command=self.on_curve_opt_change).pack(side="left", padx=5)
        ttk.Checkbutton(curve_opt_bar, text="4轴转速(V0~V3)", variable=self.var_speeds, command=self.on_curve_opt_change).pack(side="left", padx=5)
        ttk.Checkbutton(curve_opt_bar, text="4轴电流(I0~I3)", variable=self.var_currents, command=self.on_curve_opt_change).pack(side="left", padx=5)
        
        ttk.Label(curve_opt_bar, text="| 误差量程:", foreground="#8E9297", font=("Segoe UI", 9)).pack(side="left", padx=(10, 2))
        self.entry_diff_limit = ttk.Entry(curve_opt_bar, width=5, justify="center", font=("Consolas", 9, "bold"))
        self.entry_diff_limit.insert(0, "5.0")
        self.entry_diff_limit.pack(side="left", padx=(0, 2))
        ttk.Label(curve_opt_bar, text="mm", foreground="#8E9297", font=("Segoe UI", 9)).pack(side="left", padx=(0, 2))
        
        self.lbl_limit_calc = ttk.Label(curve_opt_bar, text="(=563c)", foreground="#6B7280", font=("Consolas", 8))
        self.lbl_limit_calc.pack(side="left", padx=(0, 5))
        
        def on_diff_limit_change(event=None):
            val_str = self.entry_diff_limit.get().strip()
            calc_c = self.plotter.set_diff_limit_mm(val_str)
            if calc_c is not None:
                self.lbl_limit_calc.configure(text=f"(={calc_c}c)")
                
        self.entry_diff_limit.bind("<Return>", on_diff_limit_change)
        self.entry_diff_limit.bind("<FocusOut>", on_diff_limit_change)
        
        # 4轴全通道电机颜色对应表彩色提示框架
        legend_frame = ttk.Frame(curve_opt_bar, style='TFrame')
        legend_frame.pack(side="right", padx=10)

        ttk.Label(legend_frame, text="[电机通道颜色: ", foreground="#8E9297", font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Label(legend_frame, text="M0(黄) ", foreground="#EAB308", font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Label(legend_frame, text="M1(粉) ", foreground="#EC4899", font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Label(legend_frame, text="M2(紫) ", foreground="#8B5CF6", font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Label(legend_frame, text="M3(青)", foreground="#06B6D4", font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Label(legend_frame, text="]", foreground="#8E9297", font=("Segoe UI", 9, "bold")).pack(side="left")
        
        self.plotter = RealtimePlotter(chart_card, width=800, height=350)
        self.plotter.pack(fill="both", expand=True, padx=15, pady=(0, 10))
        
        self.on_curve_opt_change()
        
        log_card = ttk.Frame(right_paned, style='Card.TFrame')
        
        log_head_bar = ttk.Frame(log_card, style='TFrame')
        log_head_bar.pack(fill="x", padx=15, pady=6)
        ttk.Label(log_head_bar, text="【结构化日志流】 (可上下拖动分割线)", style='Title.TLabel').pack(side="left")
        
        self.var_show_stream = tk.BooleanVar(value=False)
        ttk.Checkbutton(log_head_bar, text="显示高频数据流日志", variable=self.var_show_stream, command=self.on_stream_log_toggle).pack(side="right")
        
        self.log_txt = scrolledtext.ScrolledText(log_card, bg="#121214", fg="#EEEEEE", 
                                                 insertbackground="white", font=("Courier New", 9),
                                                 highlightthickness=0, borderwidth=0)
        self.log_txt.pack(fill="both", expand=True, padx=15, pady=(0, 8))
        
        self.log_txt.tag_config("sys", foreground="#00ADB5")
        self.log_txt.tag_config("info", foreground="#10B981")
        self.log_txt.tag_config("warn", foreground="#FFC312")
        self.log_txt.tag_config("err", foreground="#EF4444")
        self.log_txt.tag_config("purple", foreground="#E087FF", background="#3B0764", font=("Consolas", 9, "bold"))
        self.log_txt.tag_config("desc", foreground="#EAB308", font=("Segoe UI", 9, "bold"))
        
        right_paned.add(chart_card, weight=3)
        right_paned.add(log_card, weight=2)
        
    def create_metric_row(self, parent, label_text, val_text, val_color):
        row = ttk.Frame(parent, style='TFrame')
        row.pack(fill="x", pady=5)
        ttk.Label(row, text=label_text, foreground="#8E9297", font=("Segoe UI", 9)).pack(side="left")
        lbl_val = ttk.Label(row, text=val_text, foreground=val_color, font=("Consolas", 11, "bold"))
        lbl_val.pack(side="right")
        return lbl_val

    def on_curve_opt_change(self):
        if hasattr(self, 'plotter') and self.plotter is not None:
            self.plotter.set_curve_visible('diff', self.var_diff.get())
            self.plotter.set_curve_visible('halls', self.var_halls.get())
            self.plotter.set_curve_visible('speeds', self.var_speeds.get())
            self.plotter.set_curve_visible('currents', self.var_currents.get())

    def on_stream_log_toggle(self):
        self.show_stream_log = self.var_show_stream.get()

    def refresh_ports(self):
        ports = list(serial.tools.list_ports.comports())
        port_list = [p.device for p in ports]
        self.port_cb['values'] = port_list
        if port_list:
            self.port_cb.set(port_list[0])
        else:
            self.port_cb.set("")
        self.log_message("[系统] 已刷新可用物理串口列表。", "sys")

    def toggle_connection(self):
        if not self.running:
            port = self.port_cb.get()
            if not port:
                messagebox.showerror("连接错误", "未检测到可用串口！")
                return
                
            self.start_time = None
            self.rx_raw_buffer.clear()
            self.has_printed_header_info = False
            
            try:
                self.ser = serial.Serial(
                    port=port,
                    baudrate=int(self.baud_cb.get()),
                    bytesize=serial.EIGHTBITS,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.1
                )
                self.running = True
                self.conn_status_lbl.configure(text=f"已连接: {port}", foreground="#10B981")
                self.btn_conn.configure(text="断开设备", style='TButton')
                self.log_message(f"[系统] 成功打开串口 {port}，波特率 {self.baud_cb.get()} bps。", "info")
                
                self.peak_sync_error = 0.0
                self.plotter.clear()
                
                self.rx_thread = threading.Thread(target=self.rx_loop, daemon=True)
                self.rx_thread.start()
            except Exception as e:
                self.log_message(f"[连接异常]: {str(e)}", "err")
                messagebox.showerror("物理连接错误", f"无法打开串口 {port}:\n{str(e)}")
        else:
            self.stop_connection()

    def stop_connection(self):
        self.running = False
        self.start_time = None
        self.rx_raw_buffer.clear()
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.ser = None
        self.conn_status_lbl.configure(text="未连接", foreground="#EF4444")
        self.btn_conn.configure(text="连接设备", style='Action.TButton')
        self.log_message("[系统] 已断开物理串口连接。", "sys")
        self.plotter.redraw()

    def toggle_pause(self):
        self.plotter.paused = not self.plotter.paused
        if self.plotter.paused:
            self.btn_pause.configure(text="继续刷新", style='Action.TButton')
        else:
            self.btn_pause.configure(text="暂停刷新", style='TButton')

    def clear_chart(self):
        self.plotter.clear()
        self.log_message("[系统] 滑动示波器历史缓存已清空。", "sys")

    def clear_log(self):
        self.log_txt.delete('1.0', tk.END)
        self.log_lines_count = 0

    def reset_metrics(self):
        self.peak_sync_error = 0.0
        self.metric_max_sync_diff.configure(text="0 (0%)", foreground="#10B981")
        self.log_message("[系统] 最大差值 Peak 峰值监控已重置。", "sys")

    def clear_all(self):
        """一键清空：严格按顺序 1.先清空波形 -> 2.然后重置 Peak -> 3.最后清空数据日志窗口"""
        # 1. 先清空波形
        self.plotter.clear()
        
        # 2. 然后重置 Peak 峰值
        self.peak_sync_error = 0.0
        self.metric_max_sync_diff.configure(text="0 (0%)", foreground="#10B981")
        
        # 3. 最后清空日志窗口
        self.log_txt.delete('1.0', tk.END)
        self.log_lines_count = 0
        
        self.log_message("[系统] 已完成一键重置：波形已清空 -> Peak已归零 -> 日志已刷新。", "sys")

    def log_message(self, text: str, level: str = "sys", is_stream: bool = False):
        if is_stream and not self.show_stream_log:
            return
        time_str = time.strftime("[%H:%M:%S] ")
        full_line = f"{time_str}{text}\n"
        self.log_queue.put((full_line, level))

    def schedule_ui_updates(self):
        """
        60 FPS 极速刷新引擎：16ms 周期解耦调度。
        """
        try:
            points_batch = []
            latest_dict = None
            
            while not self.incoming_points_queue.empty():
                t_ms, data_dict = self.incoming_points_queue.get_nowait()
                points_batch.append((t_ms, data_dict))
                latest_dict = data_dict
                if len(points_batch) >= 200:
                    break
                    
            if points_batch:
                self.plotter.add_points_batch(points_batch)
                
                if latest_dict:
                    diff_val = latest_dict.get('diff', 0)
                    if 'diff' not in latest_dict:
                        h_vals = [latest_dict[k] for k in ['H0', 'H1', 'H2', 'H3'] if k in latest_dict]
                        diff_val = (max(h_vals) - min(h_vals)) if len(h_vals) >= 2 else 0.0
                        
                    limit_val = latest_dict.get('max_sync_diff_hall', 0)
                    if not limit_val or limit_val <= 0:
                        limit_val = getattr(self.plotter, 'diff_limit_counts', 2250)
                    if not limit_val or limit_val <= 0:
                        limit_val = 2250
                        
                    diff_pct = (diff_val / float(limit_val) * 100.0) if limit_val > 0 else 0.0
                    
                    # 按照 30% / 60% 动态阶梯色彩分级
                    if diff_pct > 60.0:
                        diff_color = "#EF4444"
                    elif diff_pct > 30.0:
                        diff_color = "#FFC312"
                    else:
                        diff_color = "#10B981"
                        
                    self.metric_current_diff.configure(text=f"{int(diff_val)} ({diff_pct:.0f}%)", foreground=diff_color)
                    
                    if diff_val > self.peak_sync_error:
                        self.peak_sync_error = diff_val
                        
                    peak_pct = (self.peak_sync_error / float(limit_val) * 100.0) if limit_val > 0 else 0.0
                    if peak_pct > 60.0:
                        peak_color = "#EF4444"
                    elif peak_pct > 30.0:
                        peak_color = "#FFC312"
                    else:
                        peak_color = "#10B981"
                    self.metric_max_sync_diff.configure(text=f"{int(self.peak_sync_error)} ({peak_pct:.0f}%)", foreground=peak_color)
                        
                    step_code = latest_dict.get('step', 0)
                    fault_code = latest_dict.get('fault', 0)
                    
                    if fault_code != 0:
                        fault_map = {1: "过流堵转急停", 2: "通信中断急停", 3: "同步差超限急停"}
                        self.metric_status.configure(text=fault_map.get(fault_code, f"异常急停({fault_code})"), foreground="#EF4444")
                    elif diff_pct > 60.0:
                        self.metric_status.configure(text=f"失步风险高 ({diff_pct:.0f}%)", foreground="#EF4444")
                    elif diff_pct > 30.0:
                        self.metric_status.configure(text=f"动态微调中 ({diff_pct:.0f}%)", foreground="#FFC312")
                    elif step_code in [2, 4, 5, 6]:
                        self.metric_status.configure(text=f"同步升降中 ({diff_pct:.0f}%)", foreground="#10B981")
                    else:
                        self.metric_status.configure(text="就绪待命", foreground="#10B981")
                        
                self.plotter.redraw()
                
            batch_lines = []
            while not self.log_queue.empty():
                item = self.log_queue.get_nowait()
                batch_lines.append(item)
                if len(batch_lines) >= 20:
                    break
                    
            if batch_lines:
                for item in batch_lines:
                    if isinstance(item, list):
                        for seg_text, seg_tag in item:
                            self.log_txt.insert(tk.END, seg_text, seg_tag)
                    else:
                        line, tag = item
                        self.log_txt.insert(tk.END, line, tag)
                    self.log_lines_count += 1
                    
                if self.log_lines_count > 150:
                    self.log_txt.delete('1.0', '41.0')
                    self.log_lines_count -= 40
                    
                self.log_txt.see(tk.END)
        except Exception:
            pass
        finally:
            self.root.after(16, self.schedule_ui_updates) # 16ms 极速响应循环

    def rx_loop(self):
        """
        后台多线程串口接收。
        """
        HEADER = b'\xaa\x55'
        TAIL = b'\r\n'
        FRAME_LEN = 78
        
        step_names = {0:"Boot", 1:"READY", 2:"SINGLE_TUNE", 3:"TUNE_DONE", 4:"TOTAL_FWD", 5:"TOTAL_REV", 6:"RUNNING", 7:"TOTAL_DONE", 8:"FAULT_STOP"}
        
        while self.running:
            if self.ser and self.ser.is_open:
                try:
                    waiting = self.ser.in_waiting
                    if waiting > 0:
                        rx_bytes = self.ser.read(waiting)
                        self.rx_raw_buffer.extend(rx_bytes)
                        
                        while len(self.rx_raw_buffer) > 0:
                            idx = self.rx_raw_buffer.find(HEADER)
                            
                            if idx > 0:
                                # A. 帧头前面有文本数据，提取并按行安全解码输出
                                prefix_bytes = self.rx_raw_buffer[:idx]
                                del self.rx_raw_buffer[:idx]
                                try:
                                    prefix_str = prefix_bytes.decode('utf-8', errors='ignore')
                                    for line in prefix_str.split('\n'):
                                        line = line.strip()
                                        printable_chars = sum(1 for c in line if 32 <= ord(c) <= 126 or ord(c) in [9, 10, 13] or '\u4e00' <= c <= '\u9fff')
                                        if len(line) >= 3 and (printable_chars / len(line)) > 0.85:
                                            self.log_message(f"Rx(Text): {line}", "info", is_stream=True)
                                            parsed = self.parse_text_line(line)
                                            if parsed:
                                                if self.start_time is None: self.start_time = time.time()
                                                curr_t_ms = int((time.time() - self.start_time) * 1000)
                                                self.incoming_points_queue.put((curr_t_ms, parsed))
                                except Exception:
                                    pass

                            elif idx == 0:
                                # B. 最前面为二进制帧头 0xAA 0x55
                                if len(self.rx_raw_buffer) < FRAME_LEN:
                                    break # 长度未满 78B，等待后续数据追加
                                    
                                frame_data = bytes(self.rx_raw_buffer[:FRAME_LEN])
                                
                                if frame_data[-2:] == TAIL:
                                    del self.rx_raw_buffer[:FRAME_LEN]
                                    try:
                                        hdr, sys_step, sys_fault, max_diff, limit_hall, abs_h0, abs_h1, abs_h2, abs_h3, raw_h0, raw_h1, raw_h2, raw_h3, dh0, dh1, dh2, dh3, v0, v1, v2, v3, i0, i1, i2, i3, e0, e1, e2, e3, tl = struct.unpack('<2sBBHH4i4i4i4h4H4B2s', frame_data)
                                        
                                        # 【通信抗噪防线】物理合理性与电磁干扰误码拦截 (Glitch Guard)
                                        is_valid_frame = True
                                        
                                        # 1. 状态码与故障码有效性 (单片机状态机 0~10)
                                        if sys_step > 15 or sys_fault > 15:
                                            is_valid_frame = False
                                            
                                        # 2. 4 轴位置绝对范围合法性 (正常行程 0~2000mm 对应 0~225000c，放宽到 -50000 ~ 500000 counts)
                                        abs_halls_curr = [abs_h0, abs_h1, abs_h2, abs_h3]
                                        for h in abs_halls_curr:
                                            if h < -50000 or h > 500000:
                                                is_valid_frame = False
                                                break
                                                
                                        # 3. 4 轴单帧突变物理极限检查 (20ms 内电机 3000RPM 最大运动 ~250 counts，单帧跳变 > 3000 counts 判定为比特翻转野值)
                                        if is_valid_frame and hasattr(self, 'last_valid_abs_halls') and self.last_valid_abs_halls is not None:
                                            for m_idx in range(4):
                                                if abs(abs_halls_curr[m_idx] - self.last_valid_abs_halls[m_idx]) > 3000:
                                                    is_valid_frame = False
                                                    break
                                                    
                                        # 4. 转速与电流物理范围合法性 (转速限在 4500 RPM 内，电流限在 15.0A 内)
                                        speeds_curr = [v0, v1, v2, v3]
                                        for spd in speeds_curr:
                                            if abs(spd) > 4500:
                                                is_valid_frame = False
                                                break
                                                
                                        currents_curr = [i0 / 100.0, i1 / 100.0, i2 / 100.0, i3 / 100.0]
                                        for cur in currents_curr:
                                            if cur < 0.0 or cur > 15.0:
                                                is_valid_frame = False
                                                break
                                                
                                        if not is_valid_frame:
                                            # 受串口长线电磁干扰破坏的异常误码帧，坚决剔除丢弃，杜绝拉爆示波器！
                                            continue
                                            
                                        self.last_valid_abs_halls = abs_halls_curr
                                        
                                        if not self.has_printed_header_info:
                                            self.has_printed_header_info = True
                                            desc = "[数据格式说明] 二进制高密度帧(78B): 帧头[0xAA,0x55] | 状态:Step/Fault(2B) | 同步差:MaxDiff/Limit(4B) | 物理伸出:AbsH0~H3(16B) | 累计绝对霍尔:RawH0~H3(16B) | 相对位移:ΔH0~ΔH3(16B) | 转速:V0~V3(8B) | 电流:I0~I3(8B) | 通信错误:E0~E3(4B) | 帧尾[\r\n]"
                                            self.log_message(desc, "desc", is_stream=False)
                                            
                                        if self.start_time is None:
                                            self.start_time = time.time()
                                        curr_t_ms = int((time.time() - self.start_time) * 1000)
                                        
                                        st_name = step_names.get(sys_step, f"Step_{sys_step}")
                                        avg_abs = (abs_h0 + abs_h1 + abs_h2 + abs_h3) / 4.0
                                        c0, c1, c2, c3 = i0/100.0, i1/100.0, i2/100.0, i3/100.0
                                        
                                        # 检查具体是哪一路电机伸出长度相较上一帧未发生变化 (采样未更新停更)
                                        abs_halls_curr = [abs_h0, abs_h1, abs_h2, abs_h3]
                                        speeds_curr = [v0, v1, v2, v3]
                                        stuck_flags = [False, False, False, False]
                                        
                                        if hasattr(self, 'prev_abs_halls_cache') and self.prev_abs_halls_cache is not None:
                                            # 在运动升降阶段 (2:SINGLE_TUNE, 4:TOTAL_FWD, 5:TOTAL_REV, 6:RUNNING)
                                            if sys_step in [2, 4, 5, 6]:
                                                for m_idx in range(4):
                                                    # 电机在旋转(速度>50)，但伸出长度与上一帧完全一致(采样未更新)
                                                    if abs_halls_curr[m_idx] == self.prev_abs_halls_cache[m_idx] and abs(speeds_curr[m_idx]) > 50:
                                                        stuck_flags[m_idx] = True
                                        self.prev_abs_halls_cache = abs_halls_curr

                                        diff_ratio = (max_diff / float(limit_hall)) if limit_hall > 0 else 0.0
                                        if sys_fault != 0 or diff_ratio > 0.60:
                                            base_tag = "err"
                                        elif diff_ratio > 0.30 or (e0 + e1 + e2 + e3) > 0:
                                            base_tag = "warn"
                                        else:
                                            base_tag = "info"
                                        
                                        # 构造分段富文本：仅将采样停更掉帧的那个具体电机绝对伸出长度数值标亮紫色！
                                        time_str = time.strftime("[%H:%M:%S] ")
                                        segments = [
                                            (f"{time_str}[{st_name}] Avg:{avg_abs:.0f}c (AbsH:[", base_tag)
                                        ]
                                        for m_idx in range(4):
                                            seg_tag = "purple" if stuck_flags[m_idx] else base_tag
                                            segments.append((f"{abs_halls_curr[m_idx]}", seg_tag))
                                            if m_idx < 3:
                                                segments.append((",", base_tag))
                                                
                                        tail_str = f"], RawH:[{raw_h0},{raw_h1},{raw_h2},{raw_h3}], ΔH:[{dh0},{dh1},{dh2},{dh3}]) | MaxDiff:{max_diff}c (Limit:{limit_hall}c, {diff_ratio*100:.0f}%) | V:[{v0},{v1},{v2},{v3}]RPM | I:[{c0:.2f},{c1:.2f},{c2:.2f},{c3:.2f}]A | CommErr:[{e0},{e1},{e2},{e3}]\n"
                                        segments.append((tail_str, base_tag))

                                        # 高频数据日志或者关键故障/状态变动(偏差>30%进入微调或异常)强制透传
                                        if self.show_stream_log or sys_fault != 0 or diff_ratio > 0.30 or (e0 + e1 + e2 + e3) > 0:
                                            self.log_queue.put(segments)
                                        
                                        data_dict = {
                                            'step': sys_step,
                                            'fault': sys_fault,
                                            'diff': max_diff,
                                            'max_sync_diff_hall': limit_hall,
                                            'H0': abs_h0, 'H1': abs_h1, 'H2': abs_h2, 'H3': abs_h3,
                                            'dh0': dh0, 'dh1': dh1, 'dh2': dh2, 'dh3': dh3,
                                            'V0': v0, 'V1': v1, 'V2': v2, 'V3': v3,
                                            'I0': c0, 'I1': c1, 'I2': c2, 'I3': c3,
                                            'halls': [abs_h0, abs_h1, abs_h2, abs_h3], # 波形绘制切换为 4 轴绝对伸出长度！
                                            'deltas': [dh0, dh1, dh2, dh3],           # 相对增量仅在日志保留
                                            'speeds': [v0, v1, v2, v3],
                                            'currents': [i0, i1, i2, i3]
                                        }
                                        
                                        self.incoming_points_queue.put((curr_t_ms, data_dict))
                                    except Exception as e:
                                        self.log_message(f"[解包解析异常]: {str(e)}", "err")
                                        del self.rx_raw_buffer[0:1]
                                else:
                                    del self.rx_raw_buffer[0:1]

                            else:
                                # C. 缓存中无二进制帧头 (idx == -1)
                                if b'\n' in self.rx_raw_buffer:
                                    try:
                                        text_str = self.rx_raw_buffer.decode('utf-8', errors='ignore')
                                        lines = text_str.split('\n')
                                        for line in lines[:-1]:
                                            line = line.strip()
                                            printable_chars = sum(1 for c in line if 32 <= ord(c) <= 126 or ord(c) in [9, 10, 13] or '\u4e00' <= c <= '\u9fff')
                                            if len(line) >= 3 and (printable_chars / len(line)) > 0.85:
                                                self.log_message(f"Rx(Text): {line}", "info", is_stream=True)
                                                parsed = self.parse_text_line(line)
                                                if parsed:
                                                    if self.start_time is None: self.start_time = time.time()
                                                    curr_t_ms = int((time.time() - self.start_time) * 1000)
                                                    self.incoming_points_queue.put((curr_t_ms, parsed))
                                        self.rx_raw_buffer = bytearray(lines[-1].encode('utf-8', errors='ignore'))
                                    except Exception:
                                        self.rx_raw_buffer.clear()
                                elif len(self.rx_raw_buffer) > 150:
                                    self.rx_raw_buffer.clear()
                                break
                except Exception as e:
                    if self.running:
                        self.log_message(f"[接收异常]: {str(e)}", "err")
                    break
            time.sleep(0.005)

    def parse_text_line(self, line: str):
        data_dict = {}
        pairs = re.findall(r'([a-zA-Z_0-9]+)\s*[:=]\s*([-+]?\d*\.\d+|\d+)', line)
        if pairs:
            for k, v in pairs:
                try:
                    data_dict[k] = float(v)
                except ValueError:
                    pass
            return data_dict
        return None

def main():
    root = tk.Tk()
    app = ModernPIDAnalyzerApp(root)
    
    def on_closing():
        app.stop_connection()
        root.destroy()
        
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

if __name__ == "__main__":
    main()
