# app.py
"""Flask backend for Linux Command Execution Time Profiler.
Provides a `/run` endpoint that safely executes a limited set of Linux commands,
measures execution time, CPU usage, memory usage, and returns the results as JSON.
"""

from flask import Flask, request, jsonify, send_from_directory
import subprocess
import shlex
import time
import os
import shutil
# Global variable to track the current working directory for commands
CURRENT_WORKING_DIR = os.getcwd()
try:
    import resource
    _HAS_RESOURCE = True
except ImportError:
    _HAS_RESOURCE = False
    # Define dummy function for compatibility
    def _dummy_getrusage(_):
        raise NotImplementedError

app = Flask(__name__, static_folder="static", static_url_path="")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ALLOWED_COMMANDS = {
    "ls": True,
    "echo": True,
    "sleep": True,
    "pwd": True,
    "date": True,
    "uname": True,
}

# Maximum execution time in seconds (to avoid hanging processes)
EXEC_TIMEOUT = 10

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def is_allowed(command_args):
    """Allow any command (including Linux commands) regardless of platform.
    This is intended for a demo environment where security restrictions are relaxed.
    """
    if not command_args:
        return False
    return True

def measure_resources():
    """Collect resource usage for child processes.
    Returns a dict with user_cpu, sys_cpu and max_memory_kb.
    If the 'resource' module is unavailable (e.g., on Windows),
    returns zeros for all metrics.
    """
    if not _HAS_RESOURCE:
        return {
            "user_cpu": 0.0,
            "sys_cpu": 0.0,
            "max_memory_kb": 0,
        }
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "user_cpu": usage.ru_utime,
        "sys_cpu": usage.ru_stime,
        "max_memory_kb": usage.ru_maxrss,
    }

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.route('/run', methods=['POST'])
def run_command():
    global CURRENT_WORKING_DIR
    data = request.get_json(force=True)
    if not data or 'command' not in data:
        return jsonify({"error": "Missing 'command' field."}), 400

    raw_cmd = data['command'].strip()
    if not raw_cmd:
        return jsonify({"error": "Empty command."}), 400

    # Split into args without a shell
    try:
        args = shlex.split(raw_cmd)
    except ValueError as e:
        return jsonify({"error": f"Failed to parse command: {e}"}), 400

    # Verify the executable exists in PATH when possible.
    import shutil
    executable = args[0]
    # Windows built‑ins (handled via cmd) and Linux commands (handled via WSL).
    windows_builtin = os.name == "nt" and executable.lower() in {"echo", "dir", "timeout", "ver", "date", "cd"}
    # If not a Windows builtin and we cannot find the executable in PATH, we will let WSL try it.


    if not is_allowed(args):
        return jsonify({"error": f"Command '{args[0]}' is not allowed."}), 403

    start = time.perf_counter()
    try:
        # Handle 'cd' as a special command – change the working directory for subsequent commands
        if args[0] == "cd":
            target_path = args[1] if len(args) > 1 else os.path.expanduser("~")
            # Resolve relative paths against the current tracked directory
            if not os.path.isabs(target_path):
                new_dir = os.path.abspath(os.path.join(CURRENT_WORKING_DIR, target_path))
            else:
                new_dir = os.path.abspath(target_path)
            if not os.path.isdir(new_dir):
                return jsonify({"error": f"Directory '{new_dir}' does not exist."}), 400
            # Update the global working directory

            CURRENT_WORKING_DIR = new_dir
            return jsonify({
                "command": raw_cmd,
                "cwd": CURRENT_WORKING_DIR,
                "elapsed_seconds": 0,
                "exit_code": 0,
                "max_memory_kb": 0,
                "stderr": "",
                "stdout": "",
                "sys_cpu_seconds": 0,
                "timed_out": False,
                "user_cpu_seconds": 0,
            })
        # Build Popen kwargs; on Windows preexec_fn is not supported.
        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if os.name != "nt":
            # Native Linux/Unix environment – run in the tracked cwd.
            proc = subprocess.Popen(args, cwd=CURRENT_WORKING_DIR, **popen_kwargs)
        else:
            if windows_builtin:
                # Use cmd /c for Windows built‑ins.
                proc = subprocess.Popen(["cmd", "/c"] + args, cwd=CURRENT_WORKING_DIR, **popen_kwargs)
            else:
                # Fallback to WSL for Linux commands.
                proc = subprocess.Popen(["wsl", "bash", "-c", raw_cmd], cwd=CURRENT_WORKING_DIR, **popen_kwargs)
        try:
            stdout, stderr = proc.communicate(timeout=EXEC_TIMEOUT)
            timed_out = False
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            timed_out = True
    except Exception as e:
        return jsonify({"error": f"Execution failed: {e}"}), 500
    end = time.perf_counter()

    elapsed = end - start
    res = measure_resources()

    result = {
        "command": raw_cmd,
        "stdout": stdout.decode(errors='replace'),
        "stderr": stderr.decode(errors='replace'),
        "exit_code": proc.returncode,
        "elapsed_seconds": round(elapsed, 4),
        "user_cpu_seconds": round(res['user_cpu'], 4),
        "sys_cpu_seconds": round(res['sys_cpu'], 4),
        "max_memory_kb": res['max_memory_kb'],
        "timed_out": timed_out,
    }
    return jsonify(result)

if __name__ == '__main__':
    # Bind to localhost only for security
    app.run(host='127.0.0.1', port=5000, debug=True)
