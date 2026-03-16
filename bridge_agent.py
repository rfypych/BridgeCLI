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

try:
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.text import Text
    from rich import box
    from rich.console import Console
    from rich.align import Align
    RICH_INSTALLED = True
except ImportError:
    RICH_INSTALLED = False

# Force UTF-8 output on Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# ==================== CONFIG ====================
DEFAULT_PORT = 8765
DEFAULT_TIMEOUT = 60
MAX_OUTPUT_SIZE = 10 * 1024 * 1024   # 10MB
API_KEY = None
START_TIME = time.time()

# ==================== STATS ====================
class Stats:
    """Thread-safe global stats tracker."""
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
_bg_processes: dict = {}   # pid (str) -> {"proc", "stdout", "stderr", "started", "cmd"}

def _bg_reader(pid_key: str, proc: subprocess.Popen):
    """Background thread that drains stdout/stderr of a background process."""
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

# ==================== COLORS ====================
class Color:
    BLACK = '\033[30m'; RED = '\033[31m'; GREEN = '\033[32m'
    YELLOW = '\033[33m'; BLUE = '\033[34m'; MAGENTA = '\033[35m'
    CYAN = '\033[36m'; WHITE = '\033[37m'
    BRIGHT_RED = '\033[91m'; BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'; BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'; BRIGHT_CYAN = '\033[96m'; BRIGHT_WHITE = '\033[97m'
    BG_RED = '\033[41m'; BG_GREEN = '\033[42m'; BG_YELLOW = '\033[43m'
    BG_BLUE = '\033[44m'; BG_MAGENTA = '\033[45m'; BG_CYAN = '\033[46m'
    BOLD = '\033[1m'; DIM = '\033[2m'; ITALIC = '\033[3m'
    UNDERLINE = '\033[4m'; RESET = '\033[0m'
    TIME = DIM + WHITE
    SUCCESS = BRIGHT_GREEN; ERROR = BRIGHT_RED; WARN = BRIGHT_YELLOW
    INFO = BRIGHT_BLUE; CMD = BRIGHT_MAGENTA
    UPLOAD = BRIGHT_YELLOW; DOWNLOAD = BRIGHT_CYAN

    @staticmethod
    def disable():
        for attr in [a for a in dir(Color) if not a.startswith("_") and not callable(getattr(Color, a))]:
            setattr(Color, attr, '')

    @staticmethod
    def dim(t): return f"{Color.DIM}{t}{Color.RESET}"
    @staticmethod
    def bold(t): return f"{Color.BOLD}{t}{Color.RESET}"
    @staticmethod
    def color(t, c): return f"{c}{t}{Color.RESET}"
    @staticmethod
    def badge(t, c): return f"{Color.BOLD}{c} {t} {Color.RESET}"


# ==================== HELPERS ====================

# ==================== HTOP-STYLE TUI DASHBOARD ====================
console = Console() if RICH_INSTALLED else None
log_history = collections.deque(maxlen=35)  # Keep last 35 logs for the scrolling view
tui_lock = threading.Lock()
update_event = threading.Event() # Only refresh when needed to avoid blinking

class BridgeTUI:
    @staticmethod
    def generate_layout(port, auth_enabled):
        layout = Layout()
        layout.split(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="footer", size=3)
        )
        layout["main"].split_row(
            Layout(name="logs", ratio=3),
            Layout(name="sidebar", ratio=1)
        )
        layout["sidebar"].split_column(
            Layout(name="stats", size=9),
            Layout(name="tasks")
        )
        return BridgeTUI.update_layout(layout, port, auth_enabled)

    @staticmethod
    def update_layout(layout, port, auth_enabled):
        with tui_lock:
            # Header
            ip = get_local_ip()
            tunnel = os.environ.get("TUNNEL_URL", f"http://{ip}:{port}")
            auth_s = "[bold #50fa7b]ENABLED[/]" if auth_enabled else "[bold #ff5555]DISABLED[/]"
            header_text = Text.from_markup(f"[bold #bd93f9]🚀 Bridge Agent v4.5 (Pentest Edition)[/] | [#8be9fd]{tunnel}[/] | Auth: {auth_s}")
            layout["header"].update(Panel(Align.center(header_text, vertical="middle"), border_style="#bd93f9", box=box.HEAVY))

            # Logs (Using a Text object that joins deque)
            log_text = Text()
            for entry in list(log_history):
                log_text.append(Text.from_markup(entry + "\n"))
            layout["logs"].update(Panel(log_text, title="[bold #50fa7b]📡 Live AI Activity Logs[/]", border_style="#50fa7b", box=box.ROUNDED))

            # Stats
            snap = Stats.snapshot()
            up_str = format_duration(snap["uptime_seconds"])
            stats_table = Table(box=box.SIMPLE, show_header=False, expand=True)
            stats_table.add_column("Key", style="bold #ffb86c")
            stats_table.add_column("Value", style="#8be9fd", justify="right")
            stats_table.add_row("Uptime", up_str)
            stats_table.add_row("Requests", str(snap["requests"]))
            stats_table.add_row("Commands", str(snap["commands_run"]))
            stats_table.add_row("Errors", f"[bold #ff5555]{snap['errors']}[/]" if snap['errors'] > 0 else "0")
            stats_table.add_row("Bytes Sent", format_size(snap["bytes_sent"]))
            stats_table.add_row("Bytes Recv", format_size(snap["bytes_received"]))
            layout["stats"].update(Panel(stats_table, title="[bold #ffb86c]📊 Analytics[/]", border_style="#ffb86c", box=box.ROUNDED))

            # Active Tasks
            tasks_table = Table(expand=True, box=box.SIMPLE)
            tasks_table.add_column("PID", style="bold #8be9fd")
            tasks_table.add_column("Type", style="bold #ff79c6")
            tasks_table.add_column("Time", justify="right", style="#f8f8f2")

            with _bg_lock:
                active_tasks = [p for p in _bg_processes.items() if not p[1].get("done")]

            for pid, info in active_tasks[-15:]:
                action_type = info.get("action_type", "bg")
                elapsed = format_duration(time.time() - info.get("started", time.time()))
                tasks_table.add_row(pid[:8], action_type.upper()[:10], elapsed)

            if not active_tasks:
                tasks_table.add_row("-", "Idle", "-")

            layout["tasks"].update(Panel(tasks_table, title="[bold #ff79c6]⚙️ Active Tasks[/]", border_style="#ff79c6", box=box.ROUNDED))

            # Footer
            footer_text = Text("Press Ctrl+C to Stop • Terminal selection/copying might require holding Shift depending on your terminal.", justify="center", style="#6272a4")
            layout["footer"].update(Panel(footer_text, style="#6272a4", box=box.ROUNDED))

            return layout

def log_tui(tag, msg, color="#f8f8f2"):
    ts_str = ts()
    if RICH_INSTALLED:
        with tui_lock:
            # We don't print to console, we just append to the layout's history deque
            log_history.append(f"[#6272a4][{ts_str}][/] [{color}]{tag:<5}[/] {msg}")
        update_event.set() # Trigger a screen refresh
    else:
        print(f"[{ts_str}] {tag} {msg}")

def legacy_print(*args, **kwargs):
    pass # Silence normal prints to prevent layout tearing
  # We silence normal prints because they break the TUI

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close(); return ip
    except Exception:
        return "127.0.0.1"

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

def format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f}{unit}" if unit != 'B' else f"{size}B"
        size /= 1024
    return f"{size:.1f}TB"

def format_duration(s):
    if s < 1: return f"{s*1000:.0f}ms"
    if s < 60: return f"{s:.1f}s"
    return f"{s/60:.1f}m"

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
        host_info = f"{socket.gethostname()} &bull; {platform.system()} {platform.release()}"
        cwd = os.getcwd().replace("\\", "\\\\")
        shells = ", ".join(BridgeHandler._detected_shells or ["unknown"])
        url = f"http://{self.headers.get('Host', 'localhost')}"
        auth_note = "<b>Auth required:</b> Add header <code>Authorization: Bearer YOUR_KEY</code><br>" if API_KEY else ""

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Bridge Agent v4.5 (Pentest Edition)</title>
<style>
  body {{ font-family: monospace; background: #0d1117; color: #c9d1d9; margin: 0; padding: 2rem; }}
  h1 {{ color: #58a6ff; margin-bottom: 0; }}
  .sub {{ color: #8b949e; margin-bottom: 2rem; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1.2rem 1.5rem; margin-bottom: 1.2rem; }}
  .card h2 {{ color: #3fb950; margin: 0 0 0.8rem 0; font-size: 1rem; }}
  .card h2.warn {{ color: #d29922; }}
  pre {{ background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 1rem; overflow-x: auto; color: #e6edf3; margin: 0.5rem 0; }}
  code {{ color: #ff7b72; }}
  .badge {{ display: inline-block; background: #238636; color: #fff; border-radius: 4px; padding: 2px 8px; font-size: 0.8rem; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; }}
  .action {{ background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 0.6rem 0.8rem; }}
  .action .name {{ color: #58a6ff; font-weight: bold; }}
  .action .desc {{ color: #8b949e; font-size: 0.85rem; margin-top: 2px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td {{ padding: 4px 8px; border-bottom: 1px solid #21262d; }}
  td:first-child {{ color: #8b949e; width: 30%; }}
</style>
</head>
<body>
<h1>&#127757; Bridge Agent <span style="color:#3fb950">v4.5</span></h1>
<p class="sub">Remote Terminal Bridge &bull; <span class="badge">ONLINE</span></p>

<div class="card">
  <h2 class="warn">&#129302; Hey AI! Read this carefully.</h2>
  <p>This is a <b>remote terminal bridge</b> running on the user's local machine.<br>
  You are seeing this in a browser/webpage — but to actually USE this bridge, you need to <b>use curl in your terminal</b>, not browse it.</p>
  {auth_note}
  <b>Quick start — run this in your terminal:</b>
  <pre>curl -s -X POST {url}/ \\
  -H "Content-Type: application/json" \\
  -d '{{"action":"exec","command":"echo hello from bridge"}}'</pre>
  <b>Response will be:</b>
  <pre>{{"stdout": "hello from bridge\\n", "stderr": "", "returncode": 0}}</pre>
</div>

<div class="card">
  <h2>&#128187; Host Info</h2>
  <table>
    <tr><td>Machine</td><td>{host_info}</td></tr>
    <tr><td>Working Dir</td><td>{cwd}</td></tr>
    <tr><td>Shells</td><td>{shells}</td></tr>
    <tr><td>Bridge URL</td><td>{url}/</td></tr>
  </table>
</div>

<div class="card">
  <h2>&#9889; Available Actions</h2>
  <div class="grid">
    <div class="action"><div class="name">exec</div><div class="desc">Run command synchronously (cwd, env, timeout)</div></div>
    <div class="action"><div class="name">bg</div><div class="desc">Run command in background, returns pid</div></div>
    <div class="action"><div class="name">poll</div><div class="desc">Get output of background process</div></div>
    <div class="action"><div class="name">list</div><div class="desc">List directory contents</div></div>
    <div class="action"><div class="name">stat</div><div class="desc">File info: size, md5, modified date</div></div>
    <div class="action"><div class="name">read</div><div class="desc">Read text file (no base64)</div></div>
    <div class="action"><div class="name">write</div><div class="desc">Write text file (no base64)</div></div>
    <div class="action"><div class="name">mkdir</div><div class="desc">Create directory</div></div>
    <div class="action"><div class="name">delete</div><div class="desc">Delete file or folder</div></div>
    <div class="action"><div class="name">move</div><div class="desc">Move or rename file/folder</div></div>
    <div class="action"><div class="name">upload</div><div class="desc">Upload binary file (base64)</div></div>
    <div class="action"><div class="name">download</div><div class="desc">Download file as base64 (chunked)</div></div>
    <div class="action"><div class="name">stats</div><div class="desc">Server uptime, request count, bytes</div></div>
  </div>
</div>

<div class="card">
  <h2>&#128196; Example curl commands</h2>
<pre># Run a command
curl -s -X POST {url}/ -H "Content-Type: application/json" \\
  -d '{{"action":"exec","command":"dir"}}'

# List directory
curl -s -X POST {url}/ -H "Content-Type: application/json" \\
  -d '{{"action":"list","path":"."}}'

# Read a file
curl -s -X POST {url}/ -H "Content-Type: application/json" \\
  -d '{{"action":"read","path":"README.md"}}'

# Background process + poll
curl -s -X POST {url}/ -H "Content-Type: application/json" \\
  -d '{{"action":"bg","command":"npm run dev"}}' | python -c "import sys,json; print(json.load(sys.stdin)['pid'])"

curl -s -X POST {url}/ -H "Content-Type: application/json" \\
  -d '{{"action":"poll","pid":"<PID_HERE"}}'</pre>
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
            data = json.loads(raw.decode('utf-8'))
            action = data.get("action", "exec")

            dispatch = {
                "exec":     self._handle_exec,
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
            self.send_json(400, {"error": f"Invalid JSON: {e}"})
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
        cwd_hint = f" {Color.dim('@ ' + cwd)}" if cwd else ""
        log_tui("EXEC", f"{cmd[:50]}...", "magenta")
        t0 = time.time()
        result = self.executor.execute(cmd, timeout, cwd=cwd, env=env)
        elapsed = time.time() - t0
        Stats.inc(commands_run=1)
        out_size = len(result.get("stdout", "")) + len(result.get("stderr", ""))
        rc = result.get("returncode")
        status = (Color.color("DONE", Color.BRIGHT_GREEN) if rc == 0
                  else Color.color("TIMEOUT", Color.BRIGHT_YELLOW) if result.get("timeout")
                  else Color.color(f"FAIL:{rc}", Color.BRIGHT_RED))
        pass
        session_log({"action": "exec", "cmd": cmd, "cwd": cwd, "rc": rc, "elapsed": round(elapsed, 3)})
        self.send_json(200, result, compress=True)

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

    # ---------- WRITE TEXT ----------
    def _handle_write(self, data):
        path = data.get("path", "")
        content = data.get("content", "")
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
    print(f"\n  {Color.WARN}Shutting down bridge...{Color.RESET}\n")
    sys.exit(0)


def main():
    global API_KEY, LOG_FILE

    # Ensure Go binary path is in environment so pentest tools work seamlessly
    home_go_bin = os.path.expanduser("~/go/bin")
    if home_go_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + home_go_bin


    if not sys.stdout.isatty():
        Color.disable()

    parser = argparse.ArgumentParser(description="Bridge Agent v4.5 (Pentest Edition) - Remote Terminal Bridge")
    parser.add_argument("--port", "-p", type=int, default=DEFAULT_PORT)
    parser.add_argument("--api-key", "-k", type=str, default=None)
    parser.add_argument("--log", "-l", type=str, default=None, help="Session log file (.jsonl)")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()

    if args.no_color:
        Color.disable()

    API_KEY = args.api_key or os.environ.get("BRIDGE_API_KEY")
    LOG_FILE = args.log or os.environ.get("BRIDGE_LOG_FILE")

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    port = args.port
    auth_enabled = API_KEY is not None

    if RICH_INSTALLED:
        try:
            server = ReuseAddrServer(("", port), BridgeHandler)
        except OSError as e:
            if "Address already in use" in str(e) or "10048" in str(e):
                print(f"FATAL: Port {port} is already in use! Please use another port.")
                sys.exit(1)
            else:
                raise

        log_tui("READY", f"Listening on port {port}...", "bold #50fa7b")

        def tui_loop(layout, p, auth):
            # We use screen=True for htop-style fullscreen mode.
            # We use refresh_per_second=4, but we only generate layout heavily when updated or 1x per second
            with Live(layout, refresh_per_second=4, screen=True, transient=False) as live:
                while True:
                    # Update screen if new logs arrived or every 1 second (to update task timers)
                    if update_event.wait(timeout=1.0):
                        update_event.clear()
                    live.update(BridgeTUI.update_layout(layout, p, auth))

        layout = BridgeTUI.generate_layout(port, auth_enabled)
        tui_thread = threading.Thread(target=tui_loop, args=(layout, port, auth_enabled), daemon=True)
        tui_thread.start()

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    else:
        print_banner(args.port, auth_enabled)
        try:
            server = ReuseAddrServer(("", port), BridgeHandler)
            print(f"READY Listening for connections on port {port}...")
            server.serve_forever()
        except OSError as e:
            if "Address already in use" in str(e) or "10048" in str(e):
                print(f"FATAL: Port {port} is already in use!")
                sys.exit(1)
            else:
                raise
        except KeyboardInterrupt:
            print("Stopped.")


if __name__ == "__main__":
    main()
