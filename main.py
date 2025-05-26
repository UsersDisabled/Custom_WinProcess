import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import subprocess
import json
import datetime
import os
import threading
import time
import sys
import shutil

# --- 全局常量 ---
CONFIG_FILE = "process_config.json"
LOG_FILE_TXT = "process_manager_log.txt"
APP_ICON_FILE = "icon.ico"  # 您的应用程序图标文件名
TEMPLATE_EXE_NAME = "_template_dummy.exe" # 您的模板EXE文件名
MANAGED_EXES_DIR_NAME = "managed_exes" # 存放动态创建的exe的子目录名

# --- 尝试导入 psutil 并设置全局标志 ---
PSUTIL_AVAILABLE = False
psutil = None 
try:
    import psutil
    PSUTIL_AVAILABLE = True
    print("psutil 库已成功导入，将启用完整功能。")
except ImportError:
    print("警告: psutil 库未找到或无法导入。CPU/内存监控及部分高级进程管理功能将不可用。")

# --- 额外库 (Pillow 和 pystray) ---
TRAY_AVAILABLE = False
try:
    import pystray
    from PIL import Image
    TRAY_AVAILABLE = True
except ImportError:
    print("警告: pystray 或 Pillow 库未找到。系统托盘功能将不可用。")

# --- 辅助函数：获取资源路径 ---
def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS 
    except Exception:
        base_path = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base_path, relative_path)

# --- 日志记录函数 (最终修正版) ---
def log(message, app_instance=None):
    time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"[{time_str}] {message}"
    print(full_message) 
    
    try:
        with open(LOG_FILE_TXT, "a", encoding="utf-8") as f:
            f.write(full_message + "\n")
    except Exception as e:
        print(f"写入日志文件 '{LOG_FILE_TXT}' 失败: {e}")

    if app_instance and hasattr(app_instance, 'update_gui_log'): 
        try:
            if app_instance.winfo_exists(): 
                if hasattr(app_instance, 'log_text_widget') and \
                   app_instance.log_text_widget and \
                   hasattr(app_instance.log_text_widget, 'winfo_exists') and \
                   app_instance.log_text_widget.winfo_exists():
                    app_instance.after(0, app_instance.update_gui_log, full_message)
        except tk.TclError:
            pass 
        except Exception as e:
            print(f"更新GUI日志时发生其他错误 (非TclError): {e}")

# --- 单个进程的UI框架 ---
class ProcessFrame(tk.Frame):
    def __init__(self, master, process_name="", minimized=False, app_instance=None, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        self.app_instance = app_instance
        self.selected_var = tk.BooleanVar(value=False)
        self.process_name_var = tk.StringVar(value=process_name)
        self.minimized_var = tk.BooleanVar(value=minimized)
        self.process_popen = None
        self.psutil_process = None
        self.created_exe_path = None

        self.config(borderwidth=1, relief="groove", padx=3, pady=3)
        self.chk_select = tk.Checkbutton(self, variable=self.selected_var)
        self.chk_select.pack(side=tk.LEFT, padx=(0, 3))
        self.name_entry = tk.Entry(self, textvariable=self.process_name_var, width=35)
        self.name_entry.pack(side=tk.LEFT, padx=3, pady=2, fill=tk.X, expand=True)
        self.min_check = tk.Checkbutton(self, text="隐藏启动", variable=self.minimized_var)
        self.min_check.pack(side=tk.LEFT, padx=3)
        self.status_label = tk.Label(self, text="未运行", fg="red", width=7, anchor="w")
        self.status_label.pack(side=tk.LEFT, padx=4)
        self.cpu_label = tk.Label(self, text="CPU: --" if PSUTIL_AVAILABLE else "CPU: N/A", width=9, anchor="w")
        self.cpu_label.pack(side=tk.LEFT, padx=4)
        self.mem_label = tk.Label(self, text="Mem: --" if PSUTIL_AVAILABLE else "Mem: N/A", width=10, anchor="w")
        self.mem_label.pack(side=tk.LEFT, padx=4)
        if not PSUTIL_AVAILABLE:
            self.cpu_label.config(fg="gray")
            self.mem_label.config(fg="gray")
        btn_sub_frame = tk.Frame(self)
        btn_sub_frame.pack(side=tk.LEFT, padx=3)
        self.start_btn = tk.Button(btn_sub_frame, text="启动", command=self.start_process, width=5)
        self.start_btn.pack(side=tk.LEFT, padx=1)
        self.stop_btn = tk.Button(btn_sub_frame, text="停止", command=self.stop_process, width=5, fg="red")
        self.stop_btn.pack(side=tk.LEFT, padx=1)
        self.del_btn = tk.Button(btn_sub_frame, text="删除", command=self.request_remove_from_app, width=5)
        self.del_btn.pack(side=tk.LEFT, padx=1)
        self.update_display_status()

    def start_process(self):
        custom_name_input = self.process_name_var.get().strip()
        if not custom_name_input:
            messagebox.showwarning("输入警告", "进程名称不能为空。")
            return

        if self.is_process_running_locally():
            log(f"条目 '{custom_name_input}' 已有一个由本工具管理的实例在运行。", self.app_instance)
            messagebox.showinfo("操作提示", f"条目 '{custom_name_input}' 已在运行中。")
            return

        if os.name == 'nt' and not custom_name_input.lower().endswith(".exe"):
            custom_name_for_file = custom_name_input + ".exe"
        else:
            custom_name_for_file = custom_name_input
        
        target_exe_dir = self.app_instance.managed_exes_root_dir 
        template_exe_full_path = resource_path(TEMPLATE_EXE_NAME)

        if not os.path.exists(template_exe_full_path):
            log(f"错误: 模板EXE '{TEMPLATE_EXE_NAME}' 在路径 '{template_exe_full_path}' 未找到!", self.app_instance)
            messagebox.showerror("启动错误", f"模板文件 '{TEMPLATE_EXE_NAME}' 缺失，无法创建进程。\n请确保 '{TEMPLATE_EXE_NAME}' 与主程序在同一目录或已正确打包。")
            return
        
        # 确保 managed_exes 目录存在 (主应用已创建，这里可以省略或作为双重检查)
        # os.makedirs(target_exe_dir, exist_ok=True) 
        self.created_exe_path = os.path.join(target_exe_dir, custom_name_for_file)

        try:
            shutil.copy2(template_exe_full_path, self.created_exe_path)
            log(f"已从模板创建临时EXE: '{self.created_exe_path}'", self.app_instance)

            creationflags = 0
            # 在Windows上，始终为模板创建的EXE隐藏控制台窗口
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW
            # self.minimized_var.get() 可以用于将来如果支持启动非模板的、有窗口的普通程序时
            
            cmd_to_run_list = [self.created_exe_path] 

            self.process_popen = subprocess.Popen(
                cmd_to_run_list, 
                shell=False, 
                creationflags=creationflags, 
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace'
            )
            
            log(f"尝试启动进程: '{custom_name_input}' (运行: '{os.path.basename(self.created_exe_path)}', PID: {self.process_popen.pid})", self.app_instance)
            
            # 使用 after 调度 _check_immediate_exit，避免阻塞UI
            self.after(200, self._check_immediate_exit, custom_name_input) # 200ms 延迟

            # 附加 psutil (如果可用且进程已启动)
            # _check_immediate_exit 可能会将 self.process_popen 置为 None
            if PSUTIL_AVAILABLE and self.process_popen and self.process_popen.pid:
                try:
                    self.psutil_process = psutil.Process(self.process_popen.pid)
                    self.psutil_process.cpu_percent(interval=None) 
                except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                    log(f"启动进程 '{custom_name_input}' 后附加psutil时出错 (可能已退出): {e}", self.app_instance)
                    self.psutil_process = None
                    # 如果此时 Popen 也显示退出了，清理
                    if self.process_popen and self.process_popen.poll() is not None:
                        # _check_immediate_exit 会处理清理，这里不再重复 _cleanup_created_exe
                        self.process_popen = None
        except FileNotFoundError: 
            log(f"启动进程失败: '{self.created_exe_path}' - 文件未找到 (模板复制或执行本身失败)。", self.app_instance)
            messagebox.showerror("启动错误", f"无法启动 '{custom_name_input}':\n文件操作或执行失败。")
            self._cleanup_created_exe() 
            self.process_popen = None
        except PermissionError:
            log(f"启动进程失败: '{self.created_exe_path}' - 权限不足。", self.app_instance)
            messagebox.showerror("启动错误", f"无法启动 '{custom_name_input}':\n权限不足。")
            self._cleanup_created_exe()
            self.process_popen = None
        except Exception as e:
            log(f"启动进程 '{custom_name_input}' (文件: '{self.created_exe_path or 'N/A'}') 时发生未知错误: {e}", self.app_instance)
            messagebox.showerror("启动错误", f"无法启动 '{custom_name_input}':\n{type(e).__name__}: {e}")
            self._cleanup_created_exe()
            self.process_popen = None
        
        self.update_display_status() # 立即更新一次状态


    def _check_immediate_exit(self, original_input_name):
        """在短暂延迟后检查进程是否已退出，并记录（用于调试）"""
        if not self.process_popen: # 可能在主启动逻辑中已被设为None
            return

        return_code = self.process_popen.poll()
        if return_code is not None: # 进程已退出
            pid_for_log = self.process_popen.pid if hasattr(self.process_popen, 'pid') else 'N/A'
            log(f"进程 '{original_input_name}' (PID: {pid_for_log}) 启动后很快退出，返回码: {return_code}", self.app_instance)
            
            # 尝试读取少量可能的输出 (简化版，主要依赖日志和返回码)
            # 更复杂的stdout/stderr实时读取需要线程
            captured_stderr = ""
            try:
                if self.process_popen.stderr:
                    # 非阻塞读取不可靠，communicate会阻塞直到EOF
                    # 这里仅作简单尝试，实际中若要捕获快速退出进程的输出，需更健壮方法
                    # 对于模板EXE，它设计为持续运行，如果秒退，通常是环境问题或模板自身问题
                    pass # 暂不尝试读取，避免复杂化或阻塞
            except Exception as e_read:
                log(f"读取快速退出进程 '{original_input_name}' 的 STDERR 时出错: {e_read}", self.app_instance)
            finally:
                if self.process_popen.stdout: self.process_popen.stdout.close()
                if self.process_popen.stderr: self.process_popen.stderr.close()
            
            self._cleanup_created_exe() 
            self.process_popen = None
            if PSUTIL_AVAILABLE: self.psutil_process = None 
            self.update_display_status() # 更新UI


    def _cleanup_created_exe(self):
        if self.created_exe_path and os.path.exists(self.created_exe_path):
            try:
                os.remove(self.created_exe_path)
                log(f"已删除临时EXE: '{self.created_exe_path}'", self.app_instance)
            except Exception as e_del:
                log(f"删除临时EXE '{self.created_exe_path}' 失败: {e_del}", self.app_instance)
        self.created_exe_path = None 

    def stop_process(self):
        process_was_effectively_stopped = False 
        process_name_for_log = self.process_name_var.get()
        exe_path_for_log = self.created_exe_path or "N/A (非模板或路径未知)"

        if not self.process_popen:
            log(f"停止请求 '{process_name_for_log}': Popen对象不存在。", self.app_instance)
            self.update_display_status(); return True 

        current_pid = self.process_popen.pid 
        initial_poll = self.process_popen.poll()

        if initial_poll is not None:
            log(f"停止请求 '{process_name_for_log}' (PID: {current_pid}, 文件: {exe_path_for_log}) 已自行终止，返回码: {initial_poll}。", self.app_instance)
            self.process_popen = None 
            if PSUTIL_AVAILABLE: self.psutil_process = None
            self.update_display_status(); return True

        log(f"开始尝试停止进程: '{process_name_for_log}' (PID: {current_pid}, 文件: {exe_path_for_log})", self.app_instance)
        try:
            if PSUTIL_AVAILABLE:
                log(f"  [psutil] 尝试使用 psutil 停止 PID: {current_pid}", self.app_instance)
                try:
                    if not self.psutil_process or self.psutil_process.pid != current_pid:
                        log(f"    [psutil] psutil_process 对象无效或PID不匹配，重新获取...", self.app_instance)
                        self.psutil_process = psutil.Process(current_pid)
                    
                    if self.psutil_process.is_running():
                        log(f"    [psutil] 终止子进程 (如果有)...", self.app_instance)
                        children = self.psutil_process.children(recursive=True)
                        for child in children:
                            try: log(f"      [psutil] 终止子进程 PID: {child.pid}", self.app_instance); child.terminate(); child.wait(timeout=0.5)
                            except psutil.Error as child_err: log(f"      [psutil] 终止子进程 PID: {child.pid} 失败: {child_err}", self.app_instance)
                        
                        log(f"    [psutil] 终止主进程 PID: {current_pid}...", self.app_instance)
                        self.psutil_process.terminate()
                        try: self.psutil_process.wait(timeout=3); log(f"    [psutil] 进程 PID: {current_pid} 已成功终止。", self.app_instance); process_was_effectively_stopped = True
                        except psutil.TimeoutExpired:
                            log(f"    [psutil] 进程 PID: {current_pid} 未响应 terminate，尝试 kill...", self.app_instance); self.psutil_process.kill(); self.psutil_process.wait(timeout=1); log(f"    [psutil] 进程 PID: {current_pid} 已被 kill。", self.app_instance); process_was_effectively_stopped = True
                    else: log(f"    [psutil] 发现进程 PID: {current_pid} 在尝试终止前已停止。", self.app_instance); process_was_effectively_stopped = True
                except psutil.NoSuchProcess: log(f"    [psutil] 尝试停止时 PID: {current_pid} 已不存在。", self.app_instance); process_was_effectively_stopped = True 
                except Exception as e_psutil:
                    log(f"    [psutil] 使用psutil停止 PID: {current_pid} 出错: {e_psutil}。尝试subprocess回退...", self.app_instance)
                    if self.process_popen and self.process_popen.poll() is None: pass 
                    else: process_was_effectively_stopped = True
            
            if not process_was_effectively_stopped and self.process_popen and self.process_popen.poll() is None:
                log_prefix = f"  [subprocess]{'[fallback]' if PSUTIL_AVAILABLE else ''}"
                log(f"{log_prefix} 尝试使用 subprocess 停止 PID: {current_pid}", self.app_instance)
                try:
                    self.process_popen.terminate(); log(f"{log_prefix} 已发送 terminate 给 PID: {current_pid}。", self.app_instance)
                    try: self.process_popen.wait(timeout=1.0); log(f"{log_prefix} 进程 PID: {current_pid} 在 terminate 后1秒内已退出。", self.app_instance); process_was_effectively_stopped = True
                    except subprocess.TimeoutExpired:
                        log(f"{log_prefix} 进程 PID: {current_pid} 未响应 terminate，尝试 kill...", self.app_instance); self.process_popen.kill(); self.process_popen.wait(timeout=0.5); log(f"{log_prefix} 进程 PID: {current_pid} 已发送 kill。", self.app_instance); process_was_effectively_stopped = True
                except ProcessLookupError: log(f"{log_prefix} 尝试停止 PID: {current_pid} 时，进程已不存在。", self.app_instance); process_was_effectively_stopped = True
                except Exception as e_sub: log(f"{log_prefix} 使用 subprocess 停止 PID: {current_pid} 时出错: {e_sub}", self.app_instance)
            elif not self.process_popen: process_was_effectively_stopped = True # Popen object was already gone
        except Exception as e_outer_stop: log(f"停止进程 '{process_name_for_log}' 外层逻辑意外错误: {e_outer_stop}", self.app_instance)

        if self.process_popen and self.process_popen.poll() is not None:
            log(f"  清理已终止进程的Popen对象 (PID: {current_pid})。", self.app_instance); self.process_popen = None
            if PSUTIL_AVAILABLE: self.psutil_process = None
            process_was_effectively_stopped = True
        elif not self.process_popen: process_was_effectively_stopped = True
        
        if self.process_popen and self.process_popen.poll() is None:
            log(f"警告: 尝试停止 '{process_name_for_log}' (PID: {current_pid}) 后，进程似乎仍在运行。", self.app_instance)
            # process_was_effectively_stopped remains False if it reached here
        else: # If Popen is None or poll is not None, it's considered stopped for this frame
             process_was_effectively_stopped = True
        
        self.update_display_status(); return process_was_effectively_stopped

    def request_remove_from_app(self):
        if self.app_instance:
            self.app_instance.remove_process_frame_from_list(self)
    
    def get_config(self):
        return { "name": self.process_name_var.get(), "minimized": self.minimized_var.get() }

    def is_process_running_locally(self):
        return self.process_popen and self.process_popen.poll() is None

    def update_display_status(self):
        if self.is_process_running_locally():
            self.status_label.config(text="运行中", fg="green")
            if PSUTIL_AVAILABLE and self.psutil_process:
                try: 
                    if not self.psutil_process.is_running() or \
                       (self.process_popen and self.psutil_process.pid != self.process_popen.pid): 
                        self.process_popen = None; self.psutil_process = None; self.update_display_status(); return
                    self.cpu_label.config(text=f"CPU: {self.psutil_process.cpu_percent(interval=0.05):.1f}%", fg="black")
                    self.mem_label.config(text=f"Mem: {self.psutil_process.memory_info().rss / (1024*1024):.1f}MB", fg="black")
                except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError) as e_ps_update:
                    self.cpu_label.config(text="CPU: N/A", fg="gray"); self.mem_label.config(text="Mem: N/A", fg="gray")
                    if isinstance(e_ps_update, psutil.NoSuchProcess):
                        if self.process_popen and self.psutil_process and self.process_popen.pid == self.psutil_process.pid :
                             self.process_popen = None
                        self.psutil_process = None
                        self.status_label.config(text="未运行", fg="red") 
            elif PSUTIL_AVAILABLE and self.process_popen and self.process_popen.pid:
                    try: self.psutil_process = psutil.Process(self.process_popen.pid); self.psutil_process.cpu_percent(interval=None)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        self.psutil_process = None 
                        if not self.is_process_running_locally(): self.process_popen = None; self.status_label.config(text="未运行", fg="red")
                        self.cpu_label.config(text="CPU: N/A", fg="gray"); self.mem_label.config(text="Mem: N/A", fg="gray")
            else: self.cpu_label.config(text="CPU: N/A", fg="gray"); self.mem_label.config(text="Mem: N/A", fg="gray")
        else:
            self.status_label.config(text="未运行", fg="red")
            self.cpu_label.config(text="CPU: --" if PSUTIL_AVAILABLE else "CPU: N/A", fg="gray" if not PSUTIL_AVAILABLE else "black")
            self.mem_label.config(text="Mem: --" if PSUTIL_AVAILABLE else "Mem: N/A", fg="gray" if not PSUTIL_AVAILABLE else "black")
            if self.process_popen: self.process_popen = None 
            if PSUTIL_AVAILABLE and self.psutil_process: self.psutil_process = None

# --- 主应用程序类 ---
class ProcessManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("动态进程创建与管理器")
        self.geometry("950x700")
        self.minsize(800, 500) 
        self.process_frames_list = []
        self.is_app_running = True

        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            self.app_base_dir = os.path.dirname(sys.executable)
        else:
            self.app_base_dir = os.path.abspath(os.path.dirname(__file__))
        
        self.managed_exes_root_dir = os.path.join(self.app_base_dir, MANAGED_EXES_DIR_NAME)
        try:
            os.makedirs(self.managed_exes_root_dir, exist_ok=True)
            log(f"受管EXE目录已确认/创建: '{self.managed_exes_root_dir}'", self)
        except Exception as e:
            log(f"创建受管EXE目录 '{self.managed_exes_root_dir}' 失败: {e}", self)
            messagebox.showerror("初始化错误", f"无法创建工作目录 '{MANAGED_EXES_DIR_NAME}'.")

        top_btn_frame = tk.Frame(self); top_btn_frame.pack(fill=tk.X, pady=5, padx=7)
        tk.Button(top_btn_frame, text="+ 添加进程", command=self.add_new_process_frame_gui).pack(side=tk.LEFT, padx=3)
        tk.Button(top_btn_frame, text="导入 TXT", command=self.import_from_txt_file).pack(side=tk.LEFT, padx=3)
        tk.Button(top_btn_frame, text="保存配置", command=self.save_configuration).pack(side=tk.LEFT, padx=3)
        self.exit_button = tk.Button(top_btn_frame, text="退出程序", command=self.quit_application_confirmed, fg="red")
        self.exit_button.pack(side=tk.RIGHT, padx=3)
        
        batch_op_frame = tk.Frame(self); batch_op_frame.pack(fill=tk.X, pady=5, padx=7)
        tk.Label(batch_op_frame, text="对选中项执行批量操作:").pack(side=tk.LEFT, padx=3)
        tk.Button(batch_op_frame, text="启动", command=self.batch_start_selected).pack(side=tk.LEFT, padx=3)
        tk.Button(batch_op_frame, text="停止", command=self.batch_stop_selected, fg="red").pack(side=tk.LEFT, padx=3)
        tk.Button(batch_op_frame, text="删除", command=self.batch_delete_selected).pack(side=tk.LEFT, padx=3)
        
        main_content_frame = tk.Frame(self); main_content_frame.pack(fill=tk.BOTH, expand=True, padx=7, pady=5)
        canvas_container_frame = tk.Frame(main_content_frame); canvas_container_frame.pack(fill=tk.BOTH, expand=True, side=tk.TOP, pady=(0,5))
        self.canvas = tk.Canvas(canvas_container_frame, borderwidth=0, highlightthickness=0)
        self.scrollbar = tk.Scrollbar(canvas_container_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_content_frame = tk.Frame(self.canvas) 
        self.scrollable_content_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas_window_id = self.canvas.create_window((0, 0), window=self.scrollable_content_frame, anchor="nw", tags="scrollable_frame")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True); self.scrollbar.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", self.on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel_windows_macos)
        self.canvas.bind_all("<Button-4>", self._on_mousewheel_linux)
        self.canvas.bind_all("<Button-5>", self._on_mousewheel_linux)
        
        log_display_frame = tk.Frame(main_content_frame); log_display_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(5,0)); log_display_frame.pack_propagate(False); log_display_frame.config(height=150)
        tk.Label(log_display_frame, text="程序运行日志:").pack(anchor=tk.NW, padx=2)
        self.log_text_widget = scrolledtext.ScrolledText(log_display_frame, state=tk.DISABLED, wrap=tk.WORD, font=("Helvetica", 9))
        self.log_text_widget.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        if PSUTIL_AVAILABLE: log("应用程序启动 (psutil可用，完整功能模式)。", self) 
        else: log("应用程序启动 (psutil不可用，功能受限模式)。", self)
        self.load_configuration()

        self.tray_icon_object = None 
        self.tray_icon_thread = None
        self.protocol("WM_DELETE_WINDOW", self.minimize_to_system_tray)

        self.status_update_thread = threading.Thread(target=self.background_status_updater, daemon=True)
        self.status_update_thread.start()

        if TRAY_AVAILABLE: self.initialize_system_tray_icon()
        else: log("系统托盘功能因缺少 pystray 或 Pillow 而未初始化。", self)

    def on_canvas_configure(self, event):
        canvas_width = event.width
        if self.canvas.winfo_exists():
            self.canvas.itemconfig(self.canvas_window_id, width=canvas_width)

    def _on_mousewheel_windows_macos(self, event):
        if not self.canvas.winfo_exists(): return
        x_root, y_root = event.x_root, event.y_root
        widget_under_mouse = self.canvas.winfo_containing(x_root, y_root)
        if widget_under_mouse in (self.canvas, self.scrollable_content_frame) or \
           (hasattr(widget_under_mouse,'master') and widget_under_mouse.master == self.scrollable_content_frame):
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_mousewheel_linux(self, event):
        if not self.canvas.winfo_exists(): return
        x_root, y_root = event.x_root, event.y_root
        widget_under_mouse = self.canvas.winfo_containing(x_root, y_root)
        if widget_under_mouse in (self.canvas, self.scrollable_content_frame) or \
           (hasattr(widget_under_mouse,'master') and widget_under_mouse.master == self.scrollable_content_frame):
            if event.num == 4: self.canvas.yview_scroll(-1, "units")
            elif event.num == 5: self.canvas.yview_scroll(1, "units")

    def update_gui_log(self, message):
        if self.log_text_widget and self.log_text_widget.winfo_exists():
            self.log_text_widget.config(state=tk.NORMAL)
            self.log_text_widget.insert(tk.END, message + "\n")
            self.log_text_widget.see(tk.END)
            self.log_text_widget.config(state=tk.DISABLED)

    def add_new_process_frame_gui(self, name="", minimized=False):
        frame = ProcessFrame(self.scrollable_content_frame, name, minimized, app_instance=self)
        frame.pack(fill=tk.X, padx=5, pady=3, anchor="n")
        self.process_frames_list.append(frame)
        self.scrollable_content_frame.update_idletasks() 
        self.canvas.config(scrollregion=self.canvas.bbox("all"))
        if self.canvas.winfo_exists():
             self.canvas.itemconfig(self.canvas_window_id, width=self.canvas.winfo_width())

    def remove_process_frame_from_list(self, frame_to_remove):
        process_name_for_confirm = frame_to_remove.process_name_var.get()
        if messagebox.askyesno("确认删除", f"确定要删除对进程 '{process_name_for_confirm}' 的监控吗？\n(如果该进程当前正由本工具管理运行，会先尝试停止它。)"):
            log(f"用户请求删除监控条目: '{process_name_for_confirm}'", self)
            if frame_to_remove.is_process_running_locally():
                frame_to_remove.stop_process() 
            self.after(100, lambda: self._execute_frame_removal(frame_to_remove, process_name_for_confirm))

    def _execute_frame_removal(self, frame_to_remove, original_name_for_log):
        try:
            if frame_to_remove.is_process_running_locally():
                log(f"删除前再次尝试停止 '{original_name_for_log}'...", self)
                frame_to_remove.stop_process()
            frame_to_remove._cleanup_created_exe() 
            if frame_to_remove in self.process_frames_list:
                self.process_frames_list.remove(frame_to_remove)
            if frame_to_remove.winfo_exists(): frame_to_remove.destroy() 
            log(f"监控条目 '{original_name_for_log}' 已从界面移除。", self)
            if self.canvas.winfo_exists() and self.scrollable_content_frame.winfo_exists():
                self.scrollable_content_frame.update_idletasks()
                self.canvas.config(scrollregion=self.canvas.bbox("all"))
        except Exception as e:
            log(f"移除监控条目 '{original_name_for_log}' 时发生错误: {e}", self)

    def import_from_txt_file(self):
        file_path = filedialog.askopenfilename(title="选择TXT文件",filetypes=[("Text Files", "*.txt")])
        if not file_path: return
        try:
            with open(file_path, "r", encoding="utf-8") as f: lines = [line.strip() for line in f if line.strip()]
            current_names = {f.process_name_var.get() for f in self.process_frames_list}
            added, skipped = 0,0
            for line in lines:
                if line not in current_names: self.add_new_process_frame_gui(line); current_names.add(line); added+=1
                else: skipped+=1; log(f"导入时跳过已存在条目: '{line}'", self)
            log(f"从 {os.path.basename(file_path)} 导入: 新增 {added}, 跳过 {skipped}", self)
            if added == 0 and skipped > 0 : messagebox.showinfo("导入提示", "文件中的所有条目均已存在。")
            elif added > 0 : messagebox.showinfo("导入成功", f"成功导入 {added} 个新条目。\n跳过 {skipped} 个重复条目。")
            else: messagebox.showinfo("导入提示", "文件为空或未导入任何新条目。")
        except Exception as e: messagebox.showerror("错误", f"导入失败: {e}"); log(f"导入TXT失败: {e}", self)

    def batch_start_selected(self):
        sel = [f for f in self.process_frames_list if f.selected_var.get()]
        if not sel: messagebox.showinfo("提示", "请选择进程"); return
        log(f"批量启动 {len(sel)} 个选定进程...", self)
        for f in sel: f.start_process()

    def batch_stop_selected(self):
        sel = [f for f in self.process_frames_list if f.selected_var.get()]
        if not sel: messagebox.showinfo("提示", "请选择进程"); return
        log(f"批量停止 {len(sel)} 个选定进程...", self)
        for f in sel: f.stop_process()

    def batch_delete_selected(self):
        sel = [f for f in self.process_frames_list if f.selected_var.get()]
        if not sel: messagebox.showinfo("提示", "请选择进程"); return
        if messagebox.askyesno("确认", f"删除选中的 {len(sel)} 个进程吗？（会尝试停止运行中的进程并清理临时文件）"):
            log(f"批量删除 {len(sel)} 个选定条目...", self)
            for f in list(sel): self.remove_process_frame_from_list(f)

    def save_configuration(self):
        data = [frame.get_config() for frame in self.process_frames_list]
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f: json.dump(data, f, indent=2, ensure_ascii=False)
            log("配置已保存到 " + CONFIG_FILE, self); messagebox.showinfo("成功", "配置已保存至 " + os.path.abspath(CONFIG_FILE))
        except Exception as e: messagebox.showerror("错误", f"保存失败: {e}"); log(f"保存配置失败: {e}", self)

    def load_configuration(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f: config_data = json.load(f)
                for frame_widget in list(self.process_frames_list): frame_widget.destroy()
                self.process_frames_list.clear()
                for item_config in config_data:
                    self.add_new_process_frame_gui(item_config.get("name",""), item_config.get("minimized", False))
                log(f"从 '{CONFIG_FILE}' 加载了 {len(config_data)} 条配置。", self)
            except Exception as e:
                log(f"加载配置文件 '{CONFIG_FILE}' 失败: {e}", self); messagebox.showerror("加载配置错误", f"无法加载 '{CONFIG_FILE}':\n{e}\n将启动空白配置。")
                if not self.process_frames_list: self.add_new_process_frame_gui()
        else:
            log(f"配置文件 '{CONFIG_FILE}' 未找到，启动空白配置。", self)
            if not self.process_frames_list: self.add_new_process_frame_gui()

    def background_status_updater(self):
        while self.is_app_running:
            try:
                for frame in list(self.process_frames_list): 
                    if not self.is_app_running: break
                    if frame.winfo_exists(): self.after(0, frame.update_display_status)
                if not self.is_app_running: break 
                time.sleep(1.8) 
            except Exception as e: log(f"状态更新线程错误: {e}", self); time.sleep(5)
        log("后台状态更新线程已停止。", self)

    def minimize_to_system_tray(self):
        if TRAY_AVAILABLE and self.tray_icon_object and self.tray_icon_object.visible: 
            self.withdraw(); log("程序已最小化到系统托盘。", self)
        else: 
            if messagebox.askokcancel("退出", "无法最小化到托盘 (可能缺少pystray/Pillow库)。您确定要退出程序吗?"):
                self.quit_application_confirmed()

    def _action_show_window_from_tray(self, icon=None, item=None):
        log("从系统托盘恢复显示主界面。", self)
        self.after(0, self.deiconify)
        self.after(10, self.lift)      
        self.after(20, self.focus_force) 

    def _action_quit_from_tray(self, icon, item):
        log("从系统托盘请求退出程序。", self)
        self.after(0, self.quit_application_confirmed)

    def quit_application_confirmed(self):
        if messagebox.askyesno("退出确认", "您确定要退出进程管理器吗？\n所有由本程序启动并仍在运行的进程都将被尝试停止，临时创建的EXE文件也将被清理。"):
            self._execute_full_shutdown()
        else:
            log("用户取消了退出操作。", self)

    def _execute_full_shutdown(self):
        log("开始执行程序关闭流程...", self)
        self.is_app_running = False 
        log("正在尝试停止所有受本程序管理的活动进程并清理临时EXE...", self)
        for frame in list(self.process_frames_list): 
            try:
                log(f"  [关闭流程] 处理条目: '{frame.process_name_var.get()}'", self)
                if frame.is_process_running_locally():
                    frame.stop_process() 
                frame._cleanup_created_exe() 
            except Exception as e_stop_cleanup:
                log(f"  [关闭流程] 处理条目 '{frame.process_name_var.get()}' 时发生错误: {e_stop_cleanup}", self)
        log("所有受管进程已处理停止，临时文件已尝试清理。", self)
        
        if TRAY_AVAILABLE and self.tray_icon_object:
            log("正在停止系统托盘图标...", self)
            try: self.tray_icon_object.stop()
            except Exception as e: log(f"停止托盘图标时出错: {e}", self)
        
        if TRAY_AVAILABLE and self.tray_icon_thread and self.tray_icon_thread.is_alive():
            log("等待系统托盘线程结束 (最多1秒)...", self); self.tray_icon_thread.join(timeout=1.0) 
            if self.tray_icon_thread.is_alive(): log("警告: 系统托盘线程超时后仍未结束。", self)
        
        if self.status_update_thread and self.status_update_thread.is_alive(): 
            log("等待状态更新线程结束 (daemon, 最多1秒)...", self); self.status_update_thread.join(timeout=1.0)
            if self.status_update_thread.is_alive(): log("警告: 状态更新线程超时后仍未结束。", self)

        log("正在关闭主应用程序窗口...", self)
        try:
            super().destroy(); log("主窗口已成功关闭。", self)
        except tk.TclError as e: log(f"关闭主窗口时发生Tkinter TclError (可能窗口已不存在): {e}", self)
        except Exception as e: log(f"关闭主窗口时发生未知错误: {e}", self)
        
        print("应用程序关闭流程执行完毕。 Python 进程即将退出。")

    def initialize_system_tray_icon(self): 
        if not TRAY_AVAILABLE:
            log("系统托盘图标未初始化，因为缺少 pystray 或 Pillow。", self)
            return
        try:
            icon_full_path = resource_path(APP_ICON_FILE)
            if not os.path.exists(icon_full_path):
                log(f"警告: 托盘图标文件 '{icon_full_path}' 未找到。将创建备用图标。", self)
                image_for_tray = Image.new('RGB', (64, 64), color='darkgrey')
                try:
                    from PIL import ImageDraw; draw = ImageDraw.Draw(image_for_tray); draw.text((10, 20), "PM", fill="white") 
                except: pass 
            else:
                image_for_tray = Image.open(icon_full_path)
        except Exception as e_icon_load:
            log(f"加载或创建托盘图标 '{APP_ICON_FILE}' 失败: {e_icon_load}. 使用纯色备用。", self)
            image_for_tray = Image.new('RGB', (64, 64), color='blue')

        tray_menu_items = (
            pystray.MenuItem('显示主界面', self._action_show_window_from_tray, default=True),
            pystray.MenuItem('退出程序', self._action_quit_from_tray)
        )
        self.tray_icon_object = pystray.Icon("process_manager_app", image_for_tray, "动态进程创建与管理器", tray_menu_items)

        def run_tray_icon_thread_func():
            try:
                log("系统托盘图标线程尝试启动...", self)
                self.tray_icon_object.run() 
            except SystemExit: 
                 log("系统托盘图标线程收到 SystemExit，正常停止。", self)
            except Exception as e_tray_run:
                log(f"系统托盘图标线程运行时发生严重错误: {e_tray_run}", self)
            log("系统托盘图标线程已结束。", self)
            
        self.tray_icon_thread = threading.Thread(target=run_tray_icon_thread_func, daemon=False) 
        self.tray_icon_thread.start()
        log("系统托盘图标功能已初始化并线程已启动。", self)

# --- 程序主入口 ---
if __name__ == "__main__":
    if not (getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')): 
        template_exe_at_source = resource_path(TEMPLATE_EXE_NAME)
        if not os.path.exists(template_exe_at_source):
            print(f"错误: 开发模式下，模板可执行文件 '{TEMPLATE_EXE_NAME}' 在路径 '{template_exe_at_source}' 未找到。")
            print("请先编译或提供此文件，并将其与主脚本放在同一目录。程序功能将受限。")

        icon_to_check = resource_path(APP_ICON_FILE)
        if not os.path.exists(icon_to_check):
            try:
                # Pillow 库需要在 TRAY_AVAILABLE 为 True 时才可使用
                if TRAY_AVAILABLE:
                    img = Image.new('RGB', (64, 64), color = 'lightgrey')
                    try: from PIL import ImageDraw; draw = ImageDraw.Draw(img); draw.text((10,20), "Icon", fill="black")
                    except: pass
                    img.save(APP_ICON_FILE) 
                    print(f"提示: 图标文件 '{APP_ICON_FILE}' 在脚本目录未找到，已自动创建一个备用图标。")
                else:
                    print(f"提示: 图标文件 '{APP_ICON_FILE}' 未找到，且 Pillow 库不可用，无法创建备用图标。")
            except Exception as e_create_icon:
                print(f"警告: 创建备用图标文件 '{APP_ICON_FILE}' 失败: {e_create_icon}。")
    else: 
        print(f"程序以打包模式运行。期望资源文件已包含。")

    main_app = ProcessManagerApp()
    try:
        main_app.mainloop()
    except KeyboardInterrupt:
        final_app_ref_for_kb_interrupt = None
        if 'main_app' in locals() and isinstance(main_app, ProcessManagerApp):
            final_app_ref_for_kb_interrupt = main_app
        if final_app_ref_for_kb_interrupt and final_app_ref_for_kb_interrupt.winfo_exists():
             log("检测到Ctrl+C中断，开始执行关闭流程...", final_app_ref_for_kb_interrupt)
             final_app_ref_for_kb_interrupt._execute_full_shutdown() 
        else: 
            print("[Ctrl+C] 应用程序实例可能未完全初始化或已销毁。")
    finally:
        final_app_ref_for_log = None
        if 'main_app' in locals() and isinstance(main_app, tk.Tk):
            # 只有当窗口仍然存在时，才传递实例给 log 函数以尝试更新GUI日志
            # 否则，log 函数会因 winfo_exists() 失败而不尝试更新GUI
            if main_app.winfo_exists(): # 再次检查
                 final_app_ref_for_log = main_app
        log("应用程序主事件循环已结束或被中断。", final_app_ref_for_log)