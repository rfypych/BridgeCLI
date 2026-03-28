#!/usr/bin/env python3
"""
Bridge Agent v4.5 (Pentest Edition) - Stable, Efficient & AI-Friendly
Run this on the target machine (e.g., WSL, remote server, Windows)

Features:
- Command execution with cwd, env, timeout control
- Background process execution + poll for output
- File: upload, download (chunked), read text, write text
- Filesystem: list, stat, mkdir, delete, move
- Session logging to .jsonl
- Stats tracking (uptime, request count, bytes)
- Shell auto-detection
- Gzip compression for large responses
- Optional API key authentication
- Beautiful colored output
"""

import http.server
import socketserver
import json
import subprocess
import os
import time
import argparse
import signal
import sys
import gzip
import base64
import hashlib
import shutil
import threading
from io import BytesIO
from datetime import datetime
import socket
import platform
import collections
import curses

# Force UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# ==================== COLORS ====================
class Color:
    """ANSI Color codes for modern-looking terminal output."""
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'
    
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_BLUE = '\033[44m'
    BLACK_ON_GREEN = '\033[42;30m'
    
    BOLD = '\033[1m'
    DIM = '\033[2m'
    ITALIC = '\033[3m'
    UNDERLINE = '\033[4m'
    RESET = '\033[0m'

    @staticmethod
    def color(text, code): return f"{code}{text}{Color.RESET}"
    @staticmethod
    def bold(text): return f"{Color.BOLD}{text}{Color.RESET}"
    @staticmethod
    def dim(text): return f"{Color.DIM}{text}{Color.RESET}"
    @staticmethod
    def badge(text, code): return f" {code} {text} {Color.RESET} "

# ==================== CONFIG ====================
DEFAULT_PORT = 8765
DEFAULT_TIMEOUT = 60
MAX_OUTPUT_SIZE = 10 * 1024 * 1024   # 10MB
API_KEY = None
START_TIME = time.time()

# ==================== STATS ====================
class Stats:
    _lock = threading.Lock()
    requests = 0
    commands_run = 0
    bytes_sent = 0
    bytes_received = 0
    errors = 0

    @classmethod
    def inc(cls, **kwargs):
        with cls._lock:
            for k, v in kwargs.items():
                setattr(cls, k, getattr(cls, k) + v)

    @classmethod
    def snapshot(cls):
        with cls._lock:
            return {
                "requests": cls.requests,
                "commands_run": cls.commands_run,
                "bytes_sent": cls.bytes_sent,
                "bytes_received": cls.bytes_received,
                "errors": cls.errors,
                "uptime_seconds": round(time.time() - START_TIME, 1)
            }

# ==================== SESSION LOG ====================
_log_lock = threading.Lock()
LOG_FILE = None  # set in main() via --log

def session_log(entry: dict):
    if not LOG_FILE:
        return
    entry["ts"] = datetime.utcnow().isoformat() + "Z"
    with _log_lock:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

# ==================== BACKGROUND PROCESSES ====================
_bg_lock = threading.Lock()
_bg_processes: dict = {}

def _bg_reader(pid_key: str, proc: subprocess.Popen):
    try:
        out, err = proc.communicate()
        with _bg_lock:
            if pid_key in _bg_processes:
                _bg_processes[pid_key]["stdout"] += out
                _bg_processes[pid_key]["stderr"] += err
                _bg_processes[pid_key]["done"] = True
                _bg_processes[pid_key]["returncode"] = proc.returncode
    except Exception as e:
        with _bg_lock:
            if pid_key in _bg_processes:
                _bg_processes[pid_key]["done"] = True
                _bg_processes[pid_key]["error"] = str(e)


# ==================== DATA STORE ====================
_last_result = {"cmd": "", "stdout": "", "stderr": "", "rc": None, "ts": ""}
log_history = collections.deque(maxlen=100)
tui_lock = threading.Lock()

def ts(): return time.strftime('%H:%M:%S')

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close(); return ip
    except Exception:
        return "127.0.0.1"

def format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024: return f"{size:.1f}{unit}" if unit != 'B' else f"{size}B"
        size /= 1024
    return f"{size:.1f}TB"

def format_duration(s):
    if s < 1: return f"{s*1000:.0f}ms"
    if s < 60: return f"{s:.1f}s"
    return f"{s/60:.1f}m"

def log_tui(tag, msg, color=""):
    # Store plain text logs in the deque
    ts_str = ts()
    with tui_lock:
        log_history.append(f"[{ts_str}] {tag:<5} {msg}")

def draw_tui(stdscr, port, auth_enabled):
    curses.curs_set(0) # Hide cursor
    stdscr.nodelay(1)  # Non-blocking input
    # Pastel Rainbow Palette v3 (Peach Primary, Magenta Header, High Contrast)
    has_color = curses.has_colors()
    if has_color:
        try:
            curses.start_color()
            curses.use_default_colors()
            
            # Text & Primary Styling
            curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_MAGENTA)  # Header Pop (Lavender Pink)
            curses.init_pair(2, curses.COLOR_GREEN, -1)                    # Mint Green (Ready)
            curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_YELLOW)   # Peach Title Block
            curses.init_pair(4, curses.COLOR_RED, -1)                      # Coral Pink (Error)
            curses.init_pair(5, curses.COLOR_MAGENTA, -1)                  # Lavender Pink (System)
            curses.init_pair(6, curses.COLOR_WHITE, -1)                    # Main Text
            curses.init_pair(7, curses.COLOR_YELLOW, -1)                   # Peach Primary (Borders)
            
            # Badge Backgrounds (High Contrast for Readability)
            try:
                curses.init_pair(8, curses.COLOR_WHITE, curses.COLOR_GREEN)    # Badge Mint
                curses.init_pair(9, curses.COLOR_WHITE, curses.COLOR_YELLOW)   # Badge Peach
                curses.init_pair(10, curses.COLOR_WHITE, curses.COLOR_MAGENTA) # Badge Magenta
                curses.init_pair(11, curses.COLOR_WHITE, curses.COLOR_RED)     # Badge Coral
                curses.init_pair(12, curses.COLOR_WHITE, curses.COLOR_CYAN)    # Badge Sky Blue
            except:
                curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_GREEN)
                curses.init_pair(9, curses.COLOR_BLACK, curses.COLOR_YELLOW)
                curses.init_pair(10, curses.COLOR_BLACK, curses.COLOR_MAGENTA)
        except Exception:
            has_color = False

    while True:
        try:
            height, width = stdscr.getmaxyx()
            stdscr.erase() # erase is often safer than clear() for flickers
            
            if height < 15 or width < 50:
                stdscr.addstr(0, 0, f"Terminal too small: {width}x{height}")
                stdscr.refresh()
                curses.napms(500)
                continue

            # 1. MAIN HEADER
            tunnel = os.environ.get("TUNNEL_URL", f"http://{get_local_ip()}:{port}")
            auth_s = "🔒 API KEY" if auth_enabled else "🔓 NO AUTH"
            
            stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
            stdscr.addstr(0, 0, (" ☕ BRIDGE AGENT v4.5 ").center(width))
            stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)

            # --- SUB-HEADER (Tunnel & Auth) ---
            stdscr.addstr(1, 0, "╭" + "─" * (width - 2) + "╮", curses.color_pair(7))
            stdscr.addstr(2, 0, "│", curses.color_pair(7))
            stdscr.addstr(2, 2, " 🏠 TUNNEL ", curses.color_pair(12) | curses.A_BOLD)
            stdscr.addstr(2, 13, tunnel, curses.color_pair(12))
            stdscr.addstr(2, width - len(auth_s) - 2, auth_s, curses.color_pair(8) if auth_enabled else curses.color_pair(11) | curses.A_BOLD)
            stdscr.addstr(2, width - 1, "│", curses.color_pair(7))
            stdscr.addstr(3, 0, "├" + "─" * (width - 2) + "┤", curses.color_pair(7))

            # --- 2-COLUMN GRID ---
            side_w = 30
            main_w = width - side_w - 2
            
            # --- LEFT SIDEBAR (Panels) ---
            # SYSTEM INFO
            stdscr.addstr(4, 2, " 🍯 SYSTEM INFO ", curses.color_pair(9) | curses.A_BOLD)
            stdscr.addstr(5, 2, f"OS:   ", curses.color_pair(5))
            stdscr.addstr(5, 8, platform.system()[:side_w-10], curses.color_pair(6))
            stdscr.addstr(6, 2, f"HOST: ", curses.color_pair(5))
            stdscr.addstr(6, 8, socket.gethostname()[:side_w-10], curses.color_pair(6))
            stdscr.addstr(7, 2, f"PY:   ", curses.color_pair(5))
            stdscr.addstr(7, 8, platform.python_version(), curses.color_pair(6))

            # ANALYTICS
            panel_y = height - 12
            snap = Stats.snapshot()
            stdscr.addstr(panel_y-1, 2, " 🌻 ANALYTICS ", curses.color_pair(10) | curses.A_BOLD)
            stdscr.addstr(panel_y, 2, f"Uptime: ", curses.color_pair(7))
            stdscr.addstr(panel_y, 10, format_duration(snap['uptime_seconds']), curses.color_pair(6))
            stdscr.addstr(panel_y+1, 2, f"Reqs:   ", curses.color_pair(7))
            stdscr.addstr(panel_y+1, 10, str(snap['requests']), curses.color_pair(6))
            stdscr.addstr(panel_y+2, 2, f"Cmds:   ", curses.color_pair(7))
            stdscr.addstr(panel_y+2, 10, str(snap['commands_run']), curses.color_pair(6))
            stdscr.addstr(panel_y+3, 2, f"Err:    ", curses.color_pair(4))
            stdscr.addstr(panel_y+3, 10, str(snap['errors']), curses.color_pair(11) if snap['errors'] > 0 else curses.color_pair(8))

            # ACTIVE TASKS
            stdscr.addstr(8, 2, " ⚡ ACTIVE TASKS ", curses.color_pair(8) | curses.A_BOLD)
            with _bg_lock:
                active_tasks = [p for p in _bg_processes.items() if not p[1].get("done")]
            if not active_tasks:
                stdscr.addstr(9, 2, "Relaxing...", curses.color_pair(6) | curses.A_DIM)
            else:
                for i, (pid, info) in enumerate(active_tasks[:3]):
                    act = info.get("action_type", "bg")[:side_w-6]
                    stdscr.addstr(9 + i, 2, f"● {act}", curses.color_pair(2))

            # Vertical Divider
            for y in range(4, height - 1):
                stdscr.addstr(y, 0, "│", curses.color_pair(7))
                stdscr.addstr(y, side_w, "│", curses.color_pair(7))
                stdscr.addstr(y, width - 1, "│", curses.color_pair(7))

            # --- MAIN AREA (RIGHT) ---
            # 6. ACTIVITY LOGS (Top Section)
            out_h = 8
            log_h = height - out_h - 7
            stdscr.addstr(4, side_w + 2, " 📝 ACTIVITY LOGS ", curses.color_pair(10) | curses.A_BOLD)
            with tui_lock:
                logs = list(log_history)[-(log_h):]
            for i, line in enumerate(logs):
                color = curses.color_pair(6) # Default Cream
                line_lower = line.lower()
                
                # Extreme Granular Log Branding
                if "[READY]" in line or "[START]" in line: 
                    color = curses.color_pair(2) | curses.A_BOLD # Mint Bold
                elif "[GET]" in line:
                    color = curses.color_pair(2) # Mint
                elif any(x in line for x in ["[POST]", "[PUT]", "[DELETE]"]):
                    color = curses.color_pair(12) # Sky Blue
                elif "[EXEC]" in line:
                    if any(x in line_lower for x in ["curl", "wget", "nc", "ping", "nmap", "ssh"]):
                        color = curses.color_pair(12) # Network -> Cyan/Blue
                    elif any(x in line_lower for x in ["ls ", "cat ", "mkdir", "cd ", "pwd", "grep", "cp ", "mv "]):
                        color = curses.color_pair(7) # File Op -> Peach
                    elif any(x in line_lower for x in ["rm ", "sudo", "kill", "chmod", "chown", "apt", "pip"]):
                        color = curses.color_pair(4) # Critical -> Coral
                    else:
                        color = curses.color_pair(5) # System/Other -> Lavender
                elif "[CF]" in line: 
                    color = curses.color_pair(12) # Tunnel -> Sky Blue
                elif "[AUTH]" in line: 
                    color = curses.color_pair(10) | curses.A_BOLD # Auth -> Magenta Bold
                elif "[ERR]" in line or "[FAIL]" in line: 
                    color = curses.color_pair(4) # Error -> Coral
                elif "[BG]" in line: 
                    color = curses.color_pair(5) # BG -> Lavender
                
                stdscr.addstr(5 + i, side_w + 2, line[:main_w-4], color)

            # Horizontal Divider for Output
            stdscr.addstr(height - out_h - 2, side_w, "├" + "─" * (main_w) + "┤", curses.color_pair(7))

            # 5. LAST OUTPUT (Bottom Section)
            output_y = height - out_h - 1
            stdscr.addstr(output_y, side_w + 2, " 📜 LAST COMMAND OUTPUT ", curses.color_pair(9) | curses.A_BOLD)
            res = _last_result
            if res["cmd"]:
                cmd_line = f"🚀 {res['ts']} > {res['cmd']}"
                stdscr.addstr(output_y + 1, side_w + 2, cmd_line[:main_w-4], curses.color_pair(5) | curses.A_BOLD)
                out_lines = (res["stdout"] + res["stderr"]).splitlines()[-4:]
                for i, line in enumerate(out_lines):
                    stdscr.addstr(output_y + 2 + i, side_w + 2, "  " + line[:main_w-6], curses.color_pair(6))
            else:
                stdscr.addstr(output_y + 1, side_w + 2, "Waiting for commands...", curses.color_pair(6) | curses.A_DIM)

            # Footer (Pixel-Perfect Corners)
            try:
                footer_border = "╰" + "─" * (width - 2) + "╯"
                # Draw the border up to the second-to-last character
                stdscr.addstr(height - 1, 0, footer_border[:-1], curses.color_pair(7))
                # Insert the last corner manually to avoid scrolling errors on some terminals
                stdscr.insch(height - 1, width - 1, footer_border[-1], curses.color_pair(7))
                
                foot = " [ CTRL+C TO SHUT DOWN ] "
                stdscr.addstr(height - 1, (width // 2) - (len(foot) // 2), foot, curses.color_pair(10) | curses.A_BOLD)
            except:
                pass

            stdscr.refresh()
            curses.napms(200)
            
            c = stdscr.getch()
            if c == 3: raise KeyboardInterrupt
        except Exception as e:
            # If we hit an error, draw it to the screen so the user can see it
            try:
                stdscr.erase()
                stdscr.addstr(0, 0, f"TUI DRAW ERROR: {e}", curses.color_pair(4) | curses.A_BOLD)
                stdscr.addstr(1, 0, "Wait 5s or press Ctrl+C to exit.", curses.A_DIM)
                stdscr.refresh()
                curses.napms(5000)
            except:
                pass
            raise e

def run_tui(port, auth_enabled):
    """Run the curses TUI with a fallback if initialization fails."""
    # Save originals
    old_stdout = sys.stdout
    old_stderr = sys.stderr

    # Before taking over the screen, silence stdout/stderr so HTTP server logs don't corrupt the TUI
    class DummyWriter:
        def write(self, content): pass
        def flush(self): pass
    
    sys.stdout = DummyWriter()
    sys.stderr = DummyWriter()

    try:
        # wrapper automatically restores terminal state on crash
        curses.wrapper(draw_tui, port, auth_enabled)
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        # Restore sys.stderr to print the crash reason
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        sys.stderr = sys.__stderr__
        sys.stderr.write(f"\n{Color.BRIGHT_RED}{Color.BOLD}TUI CRASHED:{Color.RESET} {e}\n")
        if "No module named '_curses'" in str(e):
            sys.stderr.write(f"{Color.BRIGHT_YELLOW}HINT:{Color.RESET} Run 'pip install windows-curses' to enable TUI on Windows.\n")
    finally:
        # ALWAYS restore stdout/stderr
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        try:
            curses.endwin()
        except:
            pass



def detect_shells():

    """Detect available shells on the system."""
    shells = []
    candidates = (
        [("powershell", "powershell -Command echo ok"),
         ("cmd", "cmd /c echo ok"),
         ("pwsh", "pwsh -Command echo ok")]
        if platform.system() == "Windows"
        else [("bash", "bash --version"),
              ("sh", "sh --version"),
              ("zsh", "zsh --version")]
    )
    for name, test_cmd in candidates:
        try:
            r = subprocess.run(test_cmd, shell=True, capture_output=True, timeout=3)
            if r.returncode == 0:
                shells.append(name)
        except Exception:
            pass
    return shells

def ts(): return time.strftime('%H:%M:%S')


# ==================== BANNER ====================
def print_banner(port: int, auth_enabled: bool):
    ip = get_local_ip()
    auth_s = Color.color("ENABLED", Color.BRIGHT_GREEN) if auth_enabled else Color.color("DISABLED", Color.BRIGHT_RED)
    div = Color.dim('=' * 42)
    print(f"\n  {Color.BRIGHT_CYAN}{Color.BOLD}BRIDGE AGENT{Color.RESET} {Color.dim('v4.5')}")
    print(f"  {div}")
    print(f"  {Color.bold('Status:')}    {Color.badge('ONLINE', Color.BG_GREEN + Color.BLACK)}")
    print(f"  {Color.bold('Address:')}   {Color.BRIGHT_YELLOW}http://{ip}:{port}{Color.RESET}")
    print(f"  {Color.bold('Auth:')}      {auth_s}")
    print(f"  {div}")
    print(f"  {Color.dim('Actions: exec, bg, poll, list, stat, read, write, mkdir, delete, move, upload, download, stats')}")
    print(f"  {div}\n")


# ==================== COMMAND EXECUTOR ====================
class CommandExecutor:
    def execute(self, command, timeout=DEFAULT_TIMEOUT, cwd=None, env=None):
        try:
            run_env = None
            if env:
                run_env = os.environ.copy()
                run_env.update({str(k): str(v) for k, v in env.items()})

            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=cwd, env=run_env
            )
            output = {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }
            if cwd:
                output["cwd"] = cwd
            total = len(output["stdout"]) + len(output["stderr"])
            if total > MAX_OUTPUT_SIZE:
                output["stdout"] = output["stdout"][:MAX_OUTPUT_SIZE//2] + "\n... [TRUNCATED]"
                output["stderr"] = output["stderr"][:MAX_OUTPUT_SIZE//4]
                output["truncated"] = True
            return output
        except subprocess.TimeoutExpired:
            return {"error": f"Timeout after {timeout}s", "returncode": -1, "timeout": True}
        except FileNotFoundError:
            return {"error": f"cwd not found: {cwd}", "returncode": -1}
        except Exception as e:
            return {"error": str(e), "returncode": -1}

    def execute_background(self, command, cwd=None, env=None):
        """Start command in background, return pid key."""
        run_env = None
        if env:
            run_env = os.environ.copy()
            run_env.update({str(k): str(v) for k, v in env.items()})
        proc = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=cwd, env=run_env
        )
        pid_key = str(proc.pid)
        with _bg_lock:
            _bg_processes[pid_key] = {
                "proc": proc, "cmd": command,
                "stdout": "", "stderr": "",
                "done": False, "returncode": None,
                "started": time.time()
            }
        t = threading.Thread(target=_bg_reader, args=(pid_key, proc), daemon=True)
        t.start()
        return pid_key


# ==================== HTTP HANDLER ====================
class BridgeHandler(http.server.BaseHTTPRequestHandler):
    timeout = 300  # 5 minutes socket timeout to prevent eager drops
    executor = CommandExecutor()
    protocol_version = 'HTTP/1.1'
    _detected_shells = None

    def log_message(self, format, *args):
        pass  # We handle our own logging

    def send_json(self, status: int, data: dict, compress: bool = False):
        try:
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            Stats.inc(bytes_sent=len(body))
            if compress and len(body) > 1024:
                buf = BytesIO()
                with gzip.GzipFile(fileobj=buf, mode='wb') as gz:
                    gz.write(body)
                body = buf.getvalue()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Encoding", "gzip")
            else:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            pass  # Client disconnected early, normal for AI agents/tunnels
        except ConnectionResetError:
            pass  # Client reset connection
        except Exception as e:
            if "Broken pipe" in str(e) or "Connection reset" in str(e):
                pass
            else:
                log_tui("ERR", f"Send Error: {e}", "red")

    def check_auth(self):
        if not API_KEY:
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Bearer "):
            return h[7:] == API_KEY
        return False

    # ---------- GET ----------
    def do_GET(self):
        if not self.check_auth():
            self.send_json(401, {"error": "Unauthorized"}); return

        Stats.inc(requests=1)

        if BridgeHandler._detected_shells is None:
            BridgeHandler._detected_shells = detect_shells()

        accept = self.headers.get("Accept", "")
        is_browser = "text/html" in accept

        if is_browser:
            # Return HTML page with full instructions for AI/browser
            self._send_html_landing()
        else:
            # Return JSON for curl/API clients
            log_tui("GET", "/ - Health Check", "green")
            self.send_json(200, {
                "type": "pentest-bridge",
                "status": "online",
                "version": "4.0",
                "timestamp": time.time(),
                "auth_enabled": API_KEY is not None,
                "host": {
                    "os": f"{platform.system()} {platform.release()}",
                    "hostname": socket.gethostname(),
                    "cwd": os.getcwd(),
                    "python": sys.version.split()[0],
                    "shells": BridgeHandler._detected_shells
                },
                "capabilities": [
                    "exec", "bg", "poll",
                    "list", "stat", "read", "write", "mkdir", "delete", "move",
                    "pentest_env", "scan_nuclei", "enum_subdomains", "fuzz_dir", "probe_alive", "crawl_urls",
                    "upload", "download", "stats"
                ],
                "usage": {
                    "exec": {"body": {"action": "exec", "command": "...", "cwd": "(opt)", "env": {}, "timeout": 60}, "description": "Run command synchronously. Returns stdout/stderr/returncode."},
                    "bg":   {"body": {"action": "bg", "command": "...", "cwd": "(opt)", "env": {}}, "description": "Run command in background. Returns pid."},
                    "poll": {"body": {"action": "poll", "pid": "<pid>", "kill": False}, "description": "Poll background process output. kill=true to terminate."},
                    "list": {"body": {"action": "list", "path": "."}, "description": "List directory. Returns entries with name/type/size/modified/path."},
                    "stat": {"body": {"action": "stat", "path": "<path>"}, "description": "Get detailed info about a file or directory (size, hash, mtime, etc)."},
                    "read": {"body": {"action": "read", "path": "<path>", "encoding": "utf-8"}, "description": "Read text file content directly (no base64)."},
                    "write": {"body": {"action": "write", "path": "<path>", "content": "...", "mode": "write|append"}, "description": "Write text to file (no base64)."},
                    "mkdir": {"body": {"action": "mkdir", "path": "<path>"}, "description": "Create directory (including parents)."},
                    "delete": {"body": {"action": "delete", "path": "<path>", "recursive": False}, "description": "Delete file or directory."},
                    "move":  {"body": {"action": "move", "src": "<path>", "dst": "<path>"}, "description": "Move or rename file/directory."},
                    "upload":   {"body": {"action": "upload", "filename": "<path>", "data": "<base64>", "mode": "write|append"}, "description": "Upload binary file via base64."},
                    "download": {"body": {"action": "download", "filename": "<path>", "offset": 0, "chunk_size": 1048576}, "description": "Download file as base64 (chunked)."},
                    "stats": {"body": {"action": "stats"}, "description": "Get server stats: uptime, request count, bytes transferred."},
                    "pentest_env": {"body": {"action": "pentest_env"}, "description": "Check available pentest tools and paths."},
                    "scan_nuclei": {"body": {"action": "scan_nuclei", "target": "https://...", "templates": "(opt)", "severity": "(opt)"}, "description": "Run nuclei scan and return JSON array."},
                    "enum_subdomains": {"body": {"action": "enum_subdomains", "domain": "example.com"}, "description": "Run subfinder and return JSON array of subdomains."},
                    "fuzz_dir": {"body": {"action": "fuzz_dir", "target": "http://.../FUZZ", "wordlist": "(opt)"}, "description": "Run ffuf and return valid endpoints as JSON array."},
                    "probe_alive": {"body": {"action": "probe_alive", "targets": ["example.com"]}, "description": "Run httpx to check live hosts and tech stack."},
                    "crawl_urls": {"body": {"action": "crawl_urls", "target": "https://example.com"}, "description": "Run katana to crawl URLs and endpoints."}
                },
                "hint": "POST to / with JSON body to run commands or manage files on the host machine."
            })

    def _send_html_landing(self):
        """Return HTML landing page with full bridge instructions."""
        log_tui("GET", "/ - HTML Landing Page", "blue")
        host_info = f"{socket.gethostname()} ({platform.system()} {platform.release()})"
        cwd = os.getcwd().replace("\\", "\\\\")
        shells = ", ".join(BridgeHandler._detected_shells or ["unknown"])
        url = f"http://{self.headers.get('Host', 'localhost')}"
        auth_note = f'<div class="auth-box"><b>Auth required:</b> Include <code>Authorization: Bearer YOUR_KEY</code></div>' if API_KEY else ""

        # Read AI System Prompt
        prompt_content = "AI_SYSTEM_PROMPT.md not found."
        if os.path.exists("AI_SYSTEM_PROMPT.md"):
            try:
                with open("AI_SYSTEM_PROMPT.md", "r", encoding="utf-8") as f:
                    content = f.read()
                    # Automatically replace placeholders for the user
                    content = content.replace("[USER_WILL_PROVIDE_THIS_URL]", url)
                    if API_KEY:
                        content = content.replace("[USER_WILL_PROVIDE_API_KEY]", API_KEY)
                    prompt_content = content
            except Exception as e:
                prompt_content = f"Error reading prompt: {e}"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bridge Agent v4.5 | Pentest Edition</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
    :root {{
        --bg: #0d1117;
        --card-bg: #161b22;
        --border: #30363d;
        --text: #c9d1d9;
        --text-dim: #8b949e;
        --primary: #58a6ff;
        --secondary: #3fb950;
        --accent: #d29922;
        --error: #f85149;
        --glass: rgba(22, 27, 34, 0.8);
    }}

    * {{ box-sizing: border-box; }}
    body {{
        font-family: 'Inter', sans-serif;
        background: var(--bg);
        color: var(--text);
        margin: 0;
        line-height: 1.6;
        background-image: radial-gradient(circle at 2px 2px, #21262d 1px, transparent 0);
        background-size: 40px 40px;
    }}

    .container {{ max-width: 900px; margin: 0 auto; padding: 40px 20px; }}

    header {{ margin-bottom: 40px; border-bottom: 1px solid var(--border); padding-bottom: 20px; }}
    h1 {{ font-size: 2.5rem; font-weight: 800; margin: 0; display: flex; align-items: center; gap: 15px; }}
    h1 .version {{ color: var(--secondary); font-size: 1.2rem; background: rgba(63, 185, 80, 0.1); padding: 4px 12px; border-radius: 20px; border: 1px solid rgba(63, 185, 80, 0.3); }}
    .subtitle {{ color: var(--text-dim); margin-top: 10px; font-size: 1.1rem; }}

    .card {{
        background: var(--card-bg);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 24px;
        margin-bottom: 24px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        position: relative;
        overflow: hidden;
    }}

    .card h2 {{ font-size: 1.25rem; font-weight: 700; margin-top: 0; margin-bottom: 20px; display: flex; align-items: center; gap: 10px; color: var(--primary); }}
    .card h2.warn {{ color: var(--accent); }}

    pre {{
        background: #010409;
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 16px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.9rem;
        overflow-x: auto;
        color: #e6edf3;
        margin: 10px 0;
    }}

    code {{ color: #ff7b72; font-family: 'JetBrains Mono', monospace; }}

    .auth-box {{ border-left: 4px solid var(--accent); background: rgba(210, 153, 34, 0.1); padding: 12px 16px; margin: 15px 0; border-radius: 4px; }}

    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }}
    .action-item {{
        background: #0d1117;
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 14px;
        transition: transform 0.2s, border-color 0.2s;
    }}
    .action-item:hover {{ border-color: var(--primary); transform: translateY(-2px); }}
    .action-name {{ color: var(--primary); font-weight: 600; font-family: 'JetBrains Mono', monospace; margin-bottom: 4px; }}
    .action-desc {{ color: var(--text-dim); font-size: 0.85rem; }}

    .info-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
    .info-table td {{ padding: 8px 0; border-bottom: 1px solid #21262d; font-size: 0.95rem; }}
    .info-table td:first-child {{ color: var(--text-dim); width: 35%; font-weight: 500; }}

    .badge {{ background: var(--secondary); color: white; padding: 2px 10px; border-radius: 100px; font-size: 0.75rem; font-weight: bold; text-transform: uppercase; }}

    .copy-btn {{
        position: absolute;
        top: 20px;
        right: 20px;
        background: var(--primary);
        color: white;
        border: none;
        padding: 6px 14px;
        border-radius: 6px;
        font-size: 0.8rem;
        font-weight: 600;
        cursor: pointer;
        transition: opacity 0.2s, background 0.2s;
    }}
    .copy-btn:hover {{ background: #79c0ff; }}
    .copy-btn:active {{ transform: scale(0.95); }}

    .prompt-container {{ max-height: 400px; overflow-y: auto; background: #010409; border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-top: 15px; font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; white-space: pre-wrap; }}
</style>
<script>
    function copyToClipboard(id, btn) {{
        const text = document.getElementById(id).innerText;
        navigator.clipboard.writeText(text).then(() => {{
            const original = btn.innerText;
            btn.innerText = "COPIED!";
            btn.style.background = "#3fb950";
            setTimeout(() => {{
                btn.innerText = original;
                btn.style.background = "";
            }}, 2000);
        }});
    }}
</script>
</head>
<body>

<div class="container">
    <header>
        <h1>&#127757; Bridge Agent <span class="version">v4.5</span></h1>
        <p class="subtitle">Autonomous Pentesting Bridge &bull; <span class="badge">Online</span></p>
    </header>

    <div class="card">
        <h2 class="warn">&#129302; AI System Prompt</h2>
        <p>Copy this prompt and provide it to your AI agent (z.ai/Claude/GPT) to begin the autonomous pentesting session. This prompt contains all the smart action documentation and robustness guidelines.</p>
        <button class="copy-btn" onclick="copyToClipboard('prompt-text', this)">COPY PROMPT</button>
        <div class="prompt-container" id="prompt-text">{prompt_content}</div>
    </div>

    <div class="card">
        <h2>&#128187; Host Environment</h2>
        <table class="info-table">
            <tr><td>Machine</td><td>{host_info}</td></tr>
            <tr><td>Working Dir</td><td><code>{cwd}</code></td></tr>
            <tr><td>Available Shells</td><td><code>{shells}</code></td></tr>
            <tr><td>Bridge Base URL</td><td><code>{url}/</code></td></tr>
        </table>
    </div>

    <div class="card">
        <h2 class="warn">&#9889; Quick Start</h2>
        <p>Testing communication from your cloud-based AI terminal:</p>
        {auth_note}
        <pre id="curl-test">curl -s -X POST {url}/ \\
  -H "Content-Type: application/json" \\
  -d '{{"action":"exec","command":"echo hello from bridge"}}'</pre>
        <button class="copy-btn" onclick="copyToClipboard('curl-test', this)">COPY</button>
    </div>

    <div class="card">
        <h2>&#128268; Smart Actions API</h2>
        <div class="grid">
            <div class="action-item">
                <div class="action-name">exec_b64</div>
                <div class="desc">Robust execution via Base64. (Recommended)</div>
            </div>
            <div class="action-item">
                <div class="action-name">scan_nuclei</div>
                <div class="desc">Targeted vulnerability scans via Nuclei.</div>
            </div>
            <div class="action-item">
                <div class="action-name">enum_subdomains</div>
                <div class="desc">Discover attack surfaces with Subfinder.</div>
            </div>
            <div class="action-item">
                <div class="action-name">fuzz_dir</div>
                <div class="desc">Fuzz for hidden endpoints using Ffuf.</div>
            </div>
            <div class="action-item">
                <div class="action-name">probe_alive</div>
                <div class="desc">Check HTTP status and tech stack.</div>
            </div>
            <div class="action-item">
                <div class="action-name">crawl_urls</div>
                <div class="desc">Deep crawl with Katana to find parameters.</div>
            </div>
            <div class="action-item">
                <div class="action-name">write / upload</div>
                <div class="desc">Create or update local files and payloads.</div>
            </div>
            <div class="action-item">
                <div class="action-name">poll</div>
                <div class="desc">Check progress and get structured results.</div>
            </div>
    </div>
</div>

</body>
</html>"""

        body = html.encode('utf-8')
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    # ---------- POST ----------
    def do_POST(self):
        if not self.check_auth():
            self.send_json(401, {"error": "Unauthorized"}); return

        Stats.inc(requests=1)
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                self.send_json(400, {"error": "Empty request"}); return
            raw = self.rfile.read(length)
            Stats.inc(bytes_received=length)
            
            # Use raw_decode to be more robust against trailing garbage/whitespace 
            # (common AI mistakes in curl commands)
            text = raw.decode('utf-8').strip()
            try:
                data, _ = json.JSONDecoder().raw_decode(text)
            except json.JSONDecodeError:
                # Fallback to standard loads if raw_decode fails mysteriously
                data = json.loads(text)

            action = data.get("action", "exec")

            dispatch = {
                "exec":     self._handle_exec,
                "exec_b64":  self._handle_exec_b64,
                "pentest_env":     self._handle_pentest_env,
                "scan_nuclei":     self._handle_scan_nuclei,
                "enum_subdomains": self._handle_enum_subdomains,
                "fuzz_dir":        self._handle_fuzz_dir,
                "probe_alive":     self._handle_probe_alive,
                "crawl_urls":      self._handle_crawl_urls,
                "bg":       self._handle_bg,
                "poll":     self._handle_poll,
                "list":     self._handle_list,
                "stat":     self._handle_stat,
                "read":     self._handle_read,
                "read_b64":  self._handle_read_b64,
                "write":    self._handle_write,
                "mkdir":    self._handle_mkdir,
                "delete":   self._handle_delete,
                "move":     self._handle_move,
                "upload":   self._handle_upload,
                "download": self._handle_download,
                "stats":    self._handle_stats,
            }
            handler = dispatch.get(action)
            if handler:
                handler(data)
            else:
                valid = ", ".join(dispatch.keys())
                self.send_json(400, {"error": f"Unknown action: '{action}'. Valid: {valid}"})

        except json.JSONDecodeError as e:
            Stats.inc(errors=1)
            self.send_json(400, {"error": f"Invalid JSON: {e}. Tip: Check for trailing characters or unescaped quotes."})
        except Exception as e:
            Stats.inc(errors=1)
            self.send_json(500, {"error": str(e)})

    # ---------- EXEC ----------
    def _handle_exec(self, data):
        cmd = data.get("command", "")
        if not cmd:
            self.send_json(400, {"error": "No command"}); return
        timeout = min(data.get("timeout", DEFAULT_TIMEOUT), 300)
        cwd = data.get("cwd") or None
        env = data.get("env") or None
        log_tui("EXEC", f"{cmd[:50]}...", "magenta")
        t0 = time.time()
        result = self.executor.execute(cmd, timeout, cwd=cwd, env=env)
        elapsed = time.time() - t0
        
        # Capture for TUI
        global _last_result
        _last_result = {
            "cmd": cmd,
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "rc": result.get("returncode"),
            "ts": ts()
        }
        
        Stats.inc(commands_run=1)
        session_log({"action": "exec", "cmd": cmd, "cwd": cwd, "rc": result.get("returncode"), "elapsed": round(elapsed, 3)})
        self.send_json(200, result, compress=True)

    # ---------- EXEC BASE64 (Robust against escaping issues) ----------
    def _handle_exec_b64(self, data):
        b64_cmd = data.get("command_b64", data.get("command", ""))
        if not b64_cmd:
            self.send_json(400, {"error": "No command_b64"}); return
        try:
            cmd = base64.b64decode(b64_cmd).decode('utf-8')
        except Exception as e:
            self.send_json(400, {"error": f"Invalid base64 encoding: {e}"}); return

        data["command"] = cmd
        self._handle_exec(data)

    # ---------- BACKGROUND ----------
    def _handle_bg(self, data):
        cmd = data.get("command", "")
        if not cmd:
            self.send_json(400, {"error": "No command"}); return
        cwd = data.get("cwd") or None
        env = data.get("env") or None
        cwd_hint = f" {Color.dim('@ ' + cwd)}" if cwd else ""
        log_tui("BG", f"{cmd[:50]}... -> PID {pid}", "yellow")
        try:
            pid = self.executor.execute_background(cmd, cwd=cwd, env=env)
            Stats.inc(commands_run=1)
            session_log({"action": "bg", "cmd": cmd, "pid": pid})
            self.send_json(200, {"pid": pid, "status": "running", "command": cmd})
        except Exception as e:
            Stats.inc(errors=1)
            self.send_json(500, {"error": str(e)})

    # ---------- POLL ----------
    def _handle_poll(self, data):
        pid = str(data.get("pid", ""))
        kill = data.get("kill", False)
        if not pid:
            self.send_json(400, {"error": "No pid"}); return
        with _bg_lock:
            proc_info = _bg_processes.get(pid)
        if not proc_info:
            self.send_json(404, {"error": f"No background process with pid={pid}"}); return

        if kill and not proc_info.get("done"):
            try:
                proc_info["proc"].terminate()
                log_tui("KILL", f"pid={pid}", "red")
            except Exception:
                pass

        done = proc_info.get("done", False)
        log_tui("POLL", f"pid={pid} (done)" if done else f"pid={pid} (running)", "cyan")

        response = {
            "pid": pid,
            "command": proc_info.get("cmd"),
            "done": done,
            "returncode": proc_info.get("returncode"),
            "elapsed": round(time.time() - proc_info.get("started", time.time()), 2)
        }

        if not done:
            self.send_json(200, response)
            return

        # Parse output if it's a smart action and hasn't been parsed yet
        if "parsed_result" not in proc_info:
            action_type = proc_info.get("action_type")
            stdout = proc_info.get("stdout", "")
            stderr = proc_info.get("stderr", "")
            parsed_result = {}

            if action_type == "scan_nuclei":
                parsed = []
                for line in stdout.strip().split("\n"):
                    if line.strip():
                        try: parsed.append(json.loads(line))
                        except: pass
                parsed_result = {
                    "target": proc_info.get("target"),
                    "vulnerabilities": parsed,
                    "count": len(parsed),
                    "raw_stderr": stderr
                }
            elif action_type == "enum_subdomains":
                subs = [s for s in stdout.strip().split("\n") if s.strip()]
                parsed_result = {
                    "domain": proc_info.get("domain"),
                    "subdomains": subs,
                    "count": len(subs),
                    "raw_stderr": stderr
                }
            elif action_type == "fuzz_dir":
                tmp_path = proc_info.get("tmp_path")
                results = []
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        with open(tmp_path, "r") as f:
                            ffuf_data = json.load(f)
                            for r in ffuf_data.get("results", []):
                                results.append({
                                    "url": r.get("url"), "status": r.get("status"),
                                    "length": r.get("length"), "words": r.get("words"),
                                    "redirectlocation": r.get("redirectlocation")
                                })
                    except: pass
                    try: os.remove(tmp_path)
                    except: pass
                parsed_result = {
                    "target": proc_info.get("target"),
                    "results": results,
                    "count": len(results),
                    "stderr": stderr
                }
            elif action_type == "probe_alive":
                parsed = []
                for line in stdout.strip().split("\n"):
                    if line.strip():
                        try: parsed.append(json.loads(line))
                        except: pass
                tmp_path = proc_info.get("tmp_path")
                if tmp_path and os.path.exists(tmp_path):
                    try: os.remove(tmp_path)
                    except: pass
                parsed_result = {
                    "alive": parsed,
                    "count": len(parsed),
                    "stderr": stderr
                }
            elif action_type == "crawl_urls":
                parsed = []
                for line in stdout.strip().split("\n"):
                    if line.strip():
                        try:
                            obj = json.loads(line)
                            clean_obj = {
                                "url": obj.get("request", {}).get("endpoint", obj.get("url")),
                                "method": obj.get("request", {}).get("method", "GET")
                            }
                            if clean_obj not in parsed:
                                parsed.append(clean_obj)
                        except: pass
                parsed_result = {
                    "target": proc_info.get("target"),
                    "endpoints": parsed,
                    "count": len(parsed),
                    "stderr": stderr
                }
            else:
                # Normal exec/bg
                parsed_result = {
                    "stdout": stdout,
                    "stderr": stderr
                }
            proc_info["parsed_result"] = parsed_result

        # Merge parsed_result into response
        response.update(proc_info["parsed_result"])

        # If output is massively large, the AI/tunnel might drop the connection while transferring.
        # Ensure we don't blow up memory here, JSON encoding large dicts is safe enough but can be big.
        # The gzip compression in send_json handles the transfer size nicely.

        self.send_json(200, response, compress=True)

    # ---------- LIST ----------
    def _handle_list(self, data):
        path = data.get("path", ".")
        abs_path = os.path.abspath(path)
        try:
            if not os.path.exists(abs_path):
                self.send_json(404, {"error": f"Path not found: {abs_path}"}); return
            entries = []
            if os.path.isfile(abs_path):
                s = os.stat(abs_path)
                entries.append({"name": os.path.basename(abs_path), "type": "file",
                                 "size": s.st_size, "modified": s.st_mtime, "path": abs_path})
            else:
                for name in sorted(os.listdir(abs_path)):
                    full = os.path.join(abs_path, name)
                    try:
                        s = os.stat(full)
                        entries.append({"name": name,
                                        "type": "dir" if os.path.isdir(full) else "file",
                                        "size": s.st_size if os.path.isfile(full) else None,
                                        "modified": s.st_mtime, "path": full})
                    except PermissionError:
                        entries.append({"name": name, "type": "unknown", "error": "permission denied"})

            log_tui("LIST", abs_path, "blue")
            self.send_json(200, {"path": abs_path, "entries": entries, "count": len(entries)})
        except PermissionError:
            self.send_json(403, {"error": "Permission denied"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # ---------- STAT ----------
    def _handle_stat(self, data):
        path = data.get("path", "")
        if not path:
            self.send_json(400, {"error": "No path"}); return
        abs_path = os.path.abspath(path)
        try:
            if not os.path.exists(abs_path):
                self.send_json(404, {"error": f"Not found: {abs_path}"}); return
            s = os.stat(abs_path)
            result = {
                "path": abs_path,
                "exists": True,
                "type": "dir" if os.path.isdir(abs_path) else "file",
                "size": s.st_size,
                "size_human": format_size(s.st_size),
                "modified": s.st_mtime,
                "modified_iso": datetime.utcfromtimestamp(s.st_mtime).isoformat() + "Z",
                "created": getattr(s, 'st_birthtime', s.st_ctime),
            }
            # Compute MD5 for files under 50MB
            if os.path.isfile(abs_path) and s.st_size < 50 * 1024 * 1024:
                h = hashlib.md5()
                with open(abs_path, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        h.update(chunk)
                result["md5"] = h.hexdigest()
            log_tui("STAT", abs_path, "blue")
            self.send_json(200, result)
        except PermissionError:
            self.send_json(403, {"error": "Permission denied"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # ---------- READ TEXT ----------
    def _handle_read(self, data):
        path = data.get("path", "")
        if not path:
            self.send_json(400, {"error": "No path"}); return
        encoding = data.get("encoding", "utf-8")
        abs_path = os.path.abspath(path)
        try:
            if not os.path.exists(abs_path):
                self.send_json(404, {"error": f"Not found: {abs_path}"}); return
            size = os.path.getsize(abs_path)
            if size > MAX_OUTPUT_SIZE:
                self.send_json(413, {"error": f"File too large ({format_size(size)}). Use 'download' for binary/large files."}); return
            with open(abs_path, "r", encoding=encoding, errors="replace") as f:
                content = f.read()
            log_tui("READ", abs_path, "cyan")
            self.send_json(200, {"path": abs_path, "content": content, "size": size, "encoding": encoding}, compress=True)
        except UnicodeDecodeError:
            self.send_json(400, {"error": "Cannot decode as text. Use 'download' for binary files."})
        except PermissionError:
            self.send_json(403, {"error": "Permission denied"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # ---------- READ BASE64 (Robust against escaping issues) ----------
    def _handle_read_b64(self, data):
        path = data.get("path", "")
        if not path:
            self.send_json(400, {"error": "No path"}); return
        abs_path = os.path.abspath(path)
        try:
            if not os.path.exists(abs_path):
                self.send_json(404, {"error": f"Not found: {abs_path}"}); return
            size = os.path.getsize(abs_path)
            with open(abs_path, "rb") as f:
                content = f.read()
            log_tui("RDB6", abs_path, "cyan")
            self.send_json(200, {
                "path": abs_path, 
                "data": base64.b64encode(content).decode('utf-8'), 
                "size": size
            }, compress=True)
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # ---------- WRITE TEXT ----------
    def _handle_write(self, data):
        path = data.get("path", "")
        # Robustness: support both 'content' and 'data' aliases
        content = data.get("content", data.get("data", ""))
        mode = data.get("mode", "write")
        encoding = data.get("encoding", "utf-8")
        if not path:
            self.send_json(400, {"error": "No path"}); return
        abs_path = os.path.abspath(path)
        try:
            dir_path = os.path.dirname(abs_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            write_mode = "a" if mode == "append" else "w"
            with open(abs_path, write_mode, encoding=encoding) as f:
                f.write(content)
            size = os.path.getsize(abs_path)
            log_tui("WRIT", abs_path, "yellow")
            self.send_json(200, {"success": True, "path": abs_path, "size": size})
        except PermissionError:
            self.send_json(403, {"error": "Permission denied"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # ---------- MKDIR ----------
    def _handle_mkdir(self, data):
        path = data.get("path", "")
        if not path:
            self.send_json(400, {"error": "No path"}); return
        abs_path = os.path.abspath(path)
        try:
            os.makedirs(abs_path, exist_ok=True)
            log_tui("MKDR", abs_path, "blue")
            self.send_json(200, {"success": True, "path": abs_path})
        except PermissionError:
            self.send_json(403, {"error": "Permission denied"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # ---------- DELETE ----------
    def _handle_delete(self, data):
        path = data.get("path", "")
        recursive = data.get("recursive", False)
        if not path:
            self.send_json(400, {"error": "No path"}); return
        abs_path = os.path.abspath(path)
        try:
            if not os.path.exists(abs_path):
                self.send_json(404, {"error": f"Not found: {abs_path}"}); return
            if os.path.isdir(abs_path):
                if recursive:
                    shutil.rmtree(abs_path)
                else:
                    os.rmdir(abs_path)  # will fail if not empty — intentional safety
            else:
                os.remove(abs_path)
            log_tui("DEL", abs_path, "red")
            self.send_json(200, {"success": True, "path": abs_path})
        except OSError as e:
            self.send_json(400, {"error": str(e) + " (use recursive=true for non-empty dirs)"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # ---------- MOVE ----------
    def _handle_move(self, data):
        src = data.get("src", "")
        dst = data.get("dst", "")
        if not src or not dst:
            self.send_json(400, {"error": "Both 'src' and 'dst' required"}); return
        abs_src = os.path.abspath(src)
        abs_dst = os.path.abspath(dst)
        try:
            if not os.path.exists(abs_src):
                self.send_json(404, {"error": f"Not found: {abs_src}"}); return
            shutil.move(abs_src, abs_dst)
            log_tui("MOVE", f"{abs_src} -> {abs_dst}", "blue")
            self.send_json(200, {"success": True, "src": abs_src, "dst": abs_dst})
        except PermissionError:
            self.send_json(403, {"error": "Permission denied"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # ---------- UPLOAD BINARY ----------
    def _handle_upload(self, data):
        filename = data.get("filename", "uploaded_file")
        filedata = data.get("data", "")
        mode = data.get("mode", "write")
        try:
            content = base64.b64decode(filedata)
            write_mode = "ab" if mode == "append" else "wb"
            dir_path = os.path.dirname(filename)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(filename, write_mode) as f:
                f.write(content)
            log_tui("UP", filename, "yellow")
            self.send_json(200, {"success": True, "filename": filename, "size": len(content)})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # ---------- DOWNLOAD BINARY ----------
    def _handle_download(self, data):
        filename = data.get("filename", "")
        chunk_size = data.get("chunk_size", 1024 * 1024)
        offset = data.get("offset", 0)
        if not filename:
            self.send_json(400, {"error": "No filename"}); return
        try:
            if not os.path.exists(filename):
                self.send_json(404, {"error": "File not found"}); return
            file_size = os.path.getsize(filename)
            with open(filename, "rb") as f:
                f.seek(offset)
                chunk = f.read(chunk_size)
                actual_offset = f.tell()
            progress = f"{format_size(actual_offset)}/{format_size(file_size)}"
            log_tui("DOWN", filename, "cyan")
            self.send_json(200, {
                "success": True, "filename": filename,
                "data": base64.b64encode(chunk).decode('utf-8'),
                "offset": actual_offset, "total_size": file_size,
                "done": actual_offset >= file_size
            })
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # ---------- STATS ----------
    def _handle_stats(self, _data):
        snap = Stats.snapshot()
        log_tui("STAT", "Server stats requested", "blue")
        self.send_json(200, snap)


    # ---------- PENTEST ENVIRONMENT ----------
    def _handle_pentest_env(self, data):
        tools = ['nuclei', 'ffuf', 'subfinder', 'httpx', 'katana', 'nmap', 'sqlmap', 'go', 'jq']
        available = {}
        for t in tools:
            r = subprocess.run(f"which {t}", shell=True, capture_output=True, text=True)
            available[t] = r.stdout.strip() if r.returncode == 0 else None

        home = os.path.expanduser("~")
        env_info = {
            "tools": available,
            "wordlists_path": f"{home}/wordlists",
            "nuclei_templates_path": f"{home}/nuclei-templates",
            "go_path": f"{home}/go/bin"
        }
        log_tui("PENT", "Checked tools", "blue")
        self.send_json(200, env_info)

    # ---------- SCAN NUCLEI ----------
    def _handle_scan_nuclei(self, data):
        target = data.get("target")
        if not target:
            self.send_json(400, {"error": "No target specified"}); return

        templates = data.get("templates", "")
        severity = data.get("severity", "")

        cmd = f"nuclei -u {target} -silent -jsonl"
        if templates:
            cmd += f" -t {templates}"
        if severity:
            cmd += f" -severity {severity}"

        log_tui("NUCL", target, "yellow")

        try:
            pid = self.executor.execute_background(cmd)
            with _bg_lock:
                _bg_processes[pid]["action_type"] = "scan_nuclei"
                _bg_processes[pid]["target"] = target

            Stats.inc(commands_run=1)
            self.send_json(200, {"pid": pid, "status": "running", "command": cmd, "message": "Run poll action with this pid to get results."})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # ---------- ENUMERATION SUBDOMAINS ----------
    def _handle_enum_subdomains(self, data):
        domain = data.get("domain")
        if not domain:
            self.send_json(400, {"error": "No domain specified"}); return

        log_tui("SUBF", domain, "yellow")

        cmd = f"subfinder -d {domain} -silent"
        try:
            pid = self.executor.execute_background(cmd)
            with _bg_lock:
                _bg_processes[pid]["action_type"] = "enum_subdomains"
                _bg_processes[pid]["domain"] = domain

            Stats.inc(commands_run=1)
            self.send_json(200, {"pid": pid, "status": "running", "command": cmd, "message": "Run poll action with this pid to get results."})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # ---------- FUZZ DIR ----------
    def _handle_fuzz_dir(self, data):
        target = data.get("target")
        if not target or "FUZZ" not in target:
            self.send_json(400, {"error": "Target must contain 'FUZZ' keyword (e.g., http://target.com/FUZZ)"}); return

        home = os.path.expanduser("~")
        wordlist = data.get("wordlist", f"{home}/wordlists/SecLists-master/Discovery/Web-Content/common.txt")

        log_tui("FFUF", target, "yellow")

        import tempfile
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json")
        os.close(tmp_fd)

        cmd = f"ffuf -u '{target}' -w '{wordlist}' -silent -o '{tmp_path}' -of json"

        try:
            pid = self.executor.execute_background(cmd)
            with _bg_lock:
                _bg_processes[pid]["action_type"] = "fuzz_dir"
                _bg_processes[pid]["target"] = target
                _bg_processes[pid]["tmp_path"] = tmp_path

            Stats.inc(commands_run=1)
            self.send_json(200, {"pid": pid, "status": "running", "command": cmd, "message": "Run poll action with this pid to get results."})
        except Exception as e:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except: pass
            self.send_json(500, {"error": str(e)})

    # ---------- PROBE ALIVE (HTTPX) ----------
    def _handle_probe_alive(self, data):
        targets = data.get("targets", [])
        if not targets:
            self.send_json(400, {"error": "No targets specified. Provide an array of domains."}); return

        log_tui("HTTPX", f"{len(targets)} targets", "yellow")

        import tempfile
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(tmp_fd, 'w') as f:
            for t in targets:
                f.write(f"{t}\n")

        cmd = f"httpx -l '{tmp_path}' -silent -json -title -tech-detect -status-code"

        try:
            pid = self.executor.execute_background(cmd)
            with _bg_lock:
                _bg_processes[pid]["action_type"] = "probe_alive"
                _bg_processes[pid]["tmp_path"] = tmp_path

            Stats.inc(commands_run=1)
            self.send_json(200, {"pid": pid, "status": "running", "command": cmd, "message": "Run poll action with this pid to get results."})
        except Exception as e:
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except: pass
            self.send_json(500, {"error": str(e)})

    # ---------- CRAWL URLS (KATANA) ----------
    def _handle_crawl_urls(self, data):
        target = data.get("target")
        if not target:
            self.send_json(400, {"error": "No target specified"}); return

        depth = data.get("depth", 3)

        log_tui("KATN", target, "yellow")

        cmd = f"katana -u '{target}' -d {depth} -silent -json"

        try:
            pid = self.executor.execute_background(cmd)
            with _bg_lock:
                _bg_processes[pid]["action_type"] = "crawl_urls"
                _bg_processes[pid]["target"] = target

            Stats.inc(commands_run=1)
            self.send_json(200, {"pid": pid, "status": "running", "command": cmd, "message": "Run poll action with this pid to get results."})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # ---------- OPTIONS ----------
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()


# ==================== SERVER ====================
class ReuseAddrServer(socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def signal_handler(signum, frame):
    # Raise SystemExit to trigger cleanup in main thread
    sys.exit(0)


def main():
    global API_KEY, LOG_FILE

    # Ensure Go binary path is in environment so pentest tools work seamlessly
    home_go_bin = os.path.expanduser("~/go/bin")
    if home_go_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + home_go_bin


    parser = argparse.ArgumentParser(description="Bridge Agent v4.5 (Pentest Edition) - Remote Terminal Bridge")
    parser.add_argument("--port", "-p", type=int, default=DEFAULT_PORT)
    parser.add_argument("--api-key", "-k", type=str, default=None)
    parser.add_argument("--log", "-l", type=str, default=None, help="Session log file (.jsonl)")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()



    API_KEY = args.api_key or os.environ.get("BRIDGE_API_KEY")
    LOG_FILE = args.log or os.environ.get("BRIDGE_LOG_FILE")

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    port = args.port
    auth_enabled = API_KEY is not None

    try:
        server = ReuseAddrServer(("", port), BridgeHandler)
    except OSError as e:
        if "Address already in use" in str(e) or "10048" in str(e):
            sys.stderr.write(f"\nFATAL: Port {port} is already in use! Please use another port.\n")
            sys.exit(1)
        else:
            raise

    # 1. Start the HTTP Server in a Background Daemon Thread
    http_thread = threading.Thread(target=server.serve_forever, daemon=True)
    http_thread.start()

    log_tui("READY", f"Listening on port {port}...")

    # 2. Start the Curses TUI in the Main Thread (Blocking)
    run_tui(port, auth_enabled)

if __name__ == "__main__":
    main()
