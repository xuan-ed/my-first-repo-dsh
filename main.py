# -*- coding: utf-8 -*-
"""代写小助手 —— 主程序（GUI）

功能：读取 TXT 文档，把光标点进目标输入框后，点击“确定并开始”，
     程序在倒计时结束后通过键盘逐字输入 / 剪贴板粘贴的方式，把文档内容打进去。

依赖：仅标准库（tkinter、ctypes、threading 等）+ 同目录 typewriter.py
"""

import datetime
import os
import queue
import sys
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

import typewriter

APP_TITLE = "代写小助手"
APP_VERSION = "1.0.0"
PREVIEW_LIMIT = 800  # 预览区最多显示的字符数


def read_text_file(path):
    """稳健地读取 TXT：依次尝试 UTF-8 / GB18030 / Big5 等常见中文编码。"""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


class App:
    def __init__(self, root):
        self.root = root
        root.title("%s v%s" % (APP_TITLE, APP_VERSION))
        root.minsize(540, 600)
        try:
            root.attributes("-topmost", True)  # 窗口置顶，方便随时点“停止”
        except tk.TclError:
            pass

        self.text_content = ""      # 已加载的文档内容
        self.file_path = None
        self.state = "idle"         # idle / countdown / typing
        self.countdown_job = None   # after 任务 id
        self.stop_event = threading.Event()
        self.typing_thread = None
        self.ui_queue = queue.Queue()

        self._build_ui()
        self.root.after(100, self._poll_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._log("程序启动 v%s" % APP_VERSION)

    # ---------------- UI ----------------
    def _build_ui(self):
        style = ttk.Style(self.root)
        style.configure("Big.TButton", font=("Microsoft YaHei", 12, "bold"))
        style.configure("TLabel", font=("Microsoft YaHei", 9))
        style.configure("TLabelframe.Label", font=("Microsoft YaHei", 9, "bold"))
        style.configure("TCheckbutton", font=("Microsoft YaHei", 9))
        style.configure("TRadiobutton", font=("Microsoft YaHei", 9))

        # ① 文件选择
        frm_file = ttk.LabelFrame(self.root, text="① 选择 TXT 文件")
        frm_file.pack(fill="x", padx=10, pady=(10, 4))
        row = ttk.Frame(frm_file)
        row.pack(fill="x", padx=10, pady=6)
        self.btn_open = ttk.Button(row, text="选择文件…", command=self._choose_file)
        self.btn_open.pack(side="left")
        self.lbl_path = ttk.Label(row, text="（未选择文件）", foreground="#888888")
        self.lbl_path.pack(side="left", padx=8)
        self.lbl_stat = ttk.Label(row, text="", foreground="#555555")
        self.lbl_stat.pack(side="right")

        # ② 预览
        frm_prev = ttk.LabelFrame(self.root, text="② 内容预览（前 %d 字）" % PREVIEW_LIMIT)
        frm_prev.pack(fill="both", expand=True, padx=10, pady=4)
        self.txt_preview = scrolledtext.ScrolledText(
            frm_prev, height=10, wrap="word", state="disabled",
            font=("Microsoft YaHei", 9))
        self.txt_preview.pack(fill="both", expand=True, padx=10, pady=6)

        # ③ 设置
        frm_set = ttk.LabelFrame(self.root, text="③ 设置")
        frm_set.pack(fill="x", padx=10, pady=4)

        row1 = ttk.Frame(frm_set)
        row1.pack(fill="x", padx=10, pady=(6, 2))
        self.mode = tk.StringVar(value="type")
        ttk.Radiobutton(row1, text="键盘逐字输入", variable=self.mode, value="type").pack(side="left")
        ttk.Radiobutton(row1, text="剪贴板粘贴", variable=self.mode, value="paste").pack(side="left", padx=14)

        row2 = ttk.Frame(frm_set)
        row2.pack(fill="x", padx=10, pady=2)
        ttk.Label(row2, text="每字间隔(毫秒):").pack(side="left")
        self.var_interval = tk.IntVar(value=30)
        ttk.Spinbox(row2, from_=0, to=1000, textvariable=self.var_interval,
                    width=6).pack(side="left", padx=4)
        self.var_random = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="随机抖动(模拟人手)", variable=self.var_random).pack(side="left", padx=14)
        self.var_newline = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="换行按回车输入", variable=self.var_newline).pack(side="left")

        row3 = ttk.Frame(frm_set)
        row3.pack(fill="x", padx=10, pady=(2, 6))
        ttk.Label(row3, text="开始前倒计时(秒):").pack(side="left")
        self.var_countdown = tk.IntVar(value=3)
        ttk.Spinbox(row3, from_=0, to=10, textvariable=self.var_countdown,
                    width=6).pack(side="left", padx=4)

        # 操作区
        frm_act = ttk.Frame(self.root)
        frm_act.pack(fill="x", padx=10, pady=6)
        self.btn_start = ttk.Button(frm_act, text="确定并开始", command=self._on_start,
                                    style="Big.TButton")
        self.btn_start.pack(fill="x", ipady=8)

        # 状态 / 提示
        self.lbl_status = ttk.Label(self.root, text="就绪", anchor="w",
                                    foreground="#333333",
                                    font=("Microsoft YaHei", 10, "bold"))
        self.lbl_status.pack(fill="x", padx=12, pady=(6, 2))
        self.lbl_hint = ttk.Label(
            self.root,
            text="使用步骤：① 选 TXT 文件 → ② 把鼠标点进目标输入框 → ③ 回来点“确定并开始”"
                 "\n点击后会有倒计时，趁倒计时把光标再点回目标输入框即可；输入中随时点“停止”中断。",
            foreground="#666666", wraplength=500, justify="left")
        self.lbl_hint.pack(fill="x", padx=12, pady=(0, 10))

    # ---------------- 文件 ----------------
    def _choose_file(self):
        path = filedialog.askopenfilename(
            title="选择 TXT 文档",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            content = read_text_file(path)
        except OSError as e:
            messagebox.showerror("读取失败", "无法读取文件：\n%s" % e)
            return

        self.file_path = path
        self.text_content = content
        self.lbl_path.configure(text=os.path.basename(path))

        # 预览
        self.txt_preview.configure(state="normal")
        self.txt_preview.delete("1.0", "end")
        preview = content if len(content) <= PREVIEW_LIMIT else content[:PREVIEW_LIMIT] + "\n\n…（后面省略）"
        self.txt_preview.insert("1.0", preview)
        self.txt_preview.configure(state="disabled")

        self.lbl_stat.configure(text="共 %d 字" % len(content))
        self._set_status("已加载：%s" % os.path.basename(path))
        self._log("加载文件：%s（共 %d 字）" % (path, len(content)))

    # ---------------- 开始 / 停止 ----------------
    def _on_start(self):
        if not self.text_content:
            messagebox.showwarning("提示", "请先选择一个 TXT 文件。")
            return
        if self.state != "idle":
            return

        self.state = "countdown"
        self.stop_event.clear()
        self.btn_start.configure(text="停止", command=self._on_stop)
        seconds = max(0, int(self.var_countdown.get()))
        if seconds <= 0:
            self._begin_typing()
        else:
            self._countdown(seconds)

    def _countdown(self, remaining):
        if self.state != "countdown":
            return
        if remaining > 0:
            self._set_status("将在 %d 秒后开始，请点进目标输入框…" % remaining)
            self.countdown_job = self.root.after(1000, self._countdown, remaining - 1)
        else:
            self._begin_typing()

    def _begin_typing(self):
        self.state = "typing"
        self._set_status("开始输入…")
        self.stop_event.clear()
        interval = max(0, int(self.var_interval.get())) / 1000.0
        randomize = bool(self.var_random.get())
        mode = self.mode.get()
        keep_newline = bool(self.var_newline.get())

        content = self.text_content
        if not keep_newline:
            content = content.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")

        self._log("开始输入 | 模式=%s | 间隔=%dms | 随机=%s | 换行=%s | 字符数=%d" % (
            mode, int(self.var_interval.get()), "是" if randomize else "否",
            "是" if keep_newline else "否", len(content)))

        self.typing_thread = threading.Thread(
            target=self._typing_worker,
            args=(mode, content, interval, randomize),
            daemon=True)
        self.typing_thread.start()

    def _typing_worker(self, mode, content, interval, randomize):
        total = len(content)
        try:
            if mode == "paste":
                typewriter.set_clipboard_text(content)
                import time
                time.sleep(0.4)  # 等剪贴板稳定
                if self.stop_event.is_set():
                    self._push("done", False, 0, total)
                    return
                typewriter.send_ctrl_v()
                self._push("done", True, total, total)
            else:
                finished = typewriter.type_text(
                    content, interval=interval, randomize=randomize,
                    stop_check=self.stop_event.is_set,
                    progress=lambda i, t: self._progress(i, t))
                self._push("done", finished, finished and total or 0, total)
        except Exception as e:  # noqa: BLE001
            self._push("error", str(e), traceback.format_exc())

    def _on_stop(self):
        if self.state != "idle":
            self._log("用户停止（阶段：%s）" % self.state)
        if self.state == "countdown":
            if self.countdown_job is not None:
                self.root.after_cancel(self.countdown_job)
                self.countdown_job = None
        self.stop_event.set()
        if self.state != "idle":
            self._set_status("已停止")
        self._reset_controls()

    def _reset_controls(self):
        self.state = "idle"
        self.btn_start.configure(text="确定并开始", command=self._on_start)

    # ---------------- 进度 / 队列 ----------------
    def _progress(self, i, total):
        if i % 20 == 0 or i == total:
            self._push("progress", i, total)

    def _push(self, kind, *args):
        self.ui_queue.put((kind,) + args)

    def _poll_queue(self):
        try:
            while True:
                item = self.ui_queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    _, i, total = item
                    self._set_status("输入中… %d / %d" % (i, total))
                elif kind == "done":
                    _, ok, i, total = item
                    if ok:
                        self._set_status("完成！共输入 %d 字符" % total)
                        self._log("输入完成：共 %d 字符" % total)
                    else:
                        self._set_status("已中断（输入了 %d 字符）" % i)
                        self._log("输入中断：已输入 %d / %d 字符" % (i, total))
                    self._reset_controls()
                elif kind == "error":
                    _, msg, tb = item
                    self._log("发生错误：%s\n%s" % (msg, tb), "ERROR")
                    extra = ""
                    if "SendInput" in msg:
                        extra = "\n\n提示：若目标程序以管理员身份运行，请也以管理员身份运行本工具。"
                    messagebox.showerror(
                        "出错", "输入过程中出错：\n%s%s\n\n详细日志已保存到同目录的 运行日志.txt" % (msg, extra))
                    self._set_status("出错")
                    self._reset_controls()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _set_status(self, text):
        self.lbl_status.configure(text=text)

    def _log(self, text, level="INFO"):
        """追加一条带时间戳的日志到 exe 同目录的 运行日志.txt。"""
        try:
            if getattr(sys, "frozen", False):
                base = os.path.dirname(sys.executable)
            else:
                base = os.path.dirname(os.path.abspath(__file__))
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(os.path.join(base, "运行日志.txt"), "a", encoding="utf-8") as f:
                f.write("[%s][%s] %s\n" % (ts, level, text))
        except OSError:
            pass

    def _on_close(self):
        self.stop_event.set()
        if self.countdown_job is not None:
            self.root.after_cancel(self.countdown_job)
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
