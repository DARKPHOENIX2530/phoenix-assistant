import sys
with open('tools.py', 'r', encoding='utf-8') as f:
    code = f.read()

# Add to TOOLS
tools_addition = '''    },
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": "Read text from a local document (.txt, .md, .py, .pdf). Extracts all text and returns it so you can analyze it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative path to the file"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "background_watch",
            "description": "Start a background thread to watch a directory for new or modified files. You will be alerted via a new memory/note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Path to watch"},
                    "interval_seconds": {"type": "integer", "description": "Seconds between checks (default 5)"}
                },
                "required": ["directory"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_github",
            "description": "Run Git and GitHub (gh) commands in the current workspace. Use this to commit, push, pull, view status, read issues, or create PRs. e.g., 'git status', 'git commit -m ...', 'gh issue list'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The exact git or gh command to run (e.g., 'git status', 'gh pr list')"}
                },
                "required": ["command"]
            }
        }
    }
]

_APPS = {'''
code = code.replace('    },\n]\n\n_APPS = {', tools_addition)

# Add to run()
run_addition = '''    if name == "read_document":
        return read_document_tool(args)
    if name == "background_watch":
        return background_watch_tool(args)
    if name == "git_github":
        return git_github_tool(args)
    raise ValueError("Unknown tool: %s" % name)'''
code = code.replace('    raise ValueError("Unknown tool: %s" % name)', run_addition)

# Append functions
funcs = '''

def read_document_tool(args):
    path = args.get("path")
    if not path:
        return "No path provided."
    import os
    if not os.path.exists(path):
        return f"File not found: {path}"
    
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        try:
            import fitz
            doc = fitz.open(path)
            text = chr(10).join(page.get_text() for page in doc)
            return f"--- {os.path.basename(path)} ---\\n{text}"
        except ImportError:
            return "PyMuPDF (fitz) is not installed. Please run: pip install PyMuPDF"
        except Exception as e:
            return f"Error reading PDF: {e}"
    else:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f"--- {os.path.basename(path)} ---\\n{f.read()}"
        except Exception as e:
            return f"Error reading file: {e}"

def background_watch_tool(args):
    directory = args.get("directory")
    interval = args.get("interval_seconds", 5)
    import os
    if not directory or not os.path.isdir(directory):
        return f"Invalid directory to watch: {directory}"

    import threading
    import time
    
    def watch_loop(target_dir, delay):
        try:
            initial = set(os.listdir(target_dir))
            while True:
                time.sleep(delay)
                current = set(os.listdir(target_dir))
                new_files = current - initial
                if new_files:
                    msg = f"Watch Alert: New file(s) detected in {target_dir}: {', '.join(new_files)}"
                    manage_notes({"action": "append", "name": "memory", "content": msg})
                    initial = current
        except Exception:
            pass

    t = threading.Thread(target=watch_loop, args=(directory, interval), daemon=True)
    t.start()
    return f"Started watching {directory} in the background. Check your memory for alerts!"

def git_github_tool(args):
    command = args.get("command", "")
    if not command:
        return "No command provided."
    import subprocess
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        output = result.stdout + "\\n" + result.stderr
        if not output.strip():
            output = "Command executed successfully with no output."
        return f"Exit code: {result.returncode}\\nOutput:\\n{output}"
    except Exception as e:
        return f"Failed to run command: {e}"
'''
with open('tools.py', 'w', encoding='utf-8') as f:
    f.write(code + funcs)
