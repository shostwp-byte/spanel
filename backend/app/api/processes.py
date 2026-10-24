from fastapi import APIRouter, Depends, HTTPException
import os

from app.api.deps import get_current_user
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.services.shell import shell

router = APIRouter(prefix="/processes", tags=["processes"])

@router.get("")
def get_processes(threads: bool = False, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    if os.name == 'nt':
        return {
            "summary": "top - 18:43:56 up 176 days, 21:01, 1 user, load average: 2.79, 3.13, 3.87\nTasks: 262 total, 2 running, 260 sleeping, 0 stopped, 0 zombie\n%Cpu(s):  9.6 us, 10.4 sy, 0.0 ni, 79.1 id, 0.0 wa, 0.0 hi, 0.9 si, 0.0 st\nKiB Mem :  7944.9 total, 1622.0 free, 3352.2 used, 2970.7 buff/cache\nKiB Swap:  4096.0 total, 3603.1 free, 492.9 used. 4275.1 avail Mem",
            "items": [
                {"pid": "52931", "user": "netspaho", "pr": "20", "ni": "0", "virt": "182576", "res": "87788", "shr": "19408", "s": "R", "cpu": "94.1", "mem": "1.1", "time": "0:01.51", "cmd": "php-fpm: pool netspaho"},
                {"pid": "52945", "user": "root", "pr": "20", "ni": "0", "virt": "9424", "res": "3864", "shr": "3304", "s": "R", "cpu": "11.8", "mem": "0.0", "time": "0:00.03", "cmd": "/usr/bin/top -c -b -n 1"}
            ]
        }
    
    try:
        # Note: setting COLUMNS=512 prevents command line truncation in top's output
        helper_args = ["threads"] if threads else []
        top_fallback = ["env", "LC_ALL=C", "COLUMNS=512", "top", *(["-H"] if threads else []), "-b", "-n", "1", "-c"]
        res = shell.privileged(
            "process-top",
            helper_args=helper_args,
            check=False,
            timeout=10,
            fallback=top_fallback,
        )
        if res.returncode != 0 or not (res.stdout or "").strip():
            res = shell.run(["env", "COLUMNS=512", "top", *(["-H"] if threads else []), "-b", "-n", "1", "-c"])
        lines = res.stdout.split("\n")
        
        summary_lines = []
        items = []
        parsing_processes = False
        
        for line in lines:
            if not line.strip():
                continue
            if line.strip().startswith("PID") or line.strip().startswith("PID\t"):
                parsing_processes = True
                continue
                
            if not parsing_processes:
                summary_lines.append(line)
            else:
                parts = line.strip().split(None, 11)
                if len(parts) >= 12:
                    items.append({
                        "pid": parts[0],
                        "user": parts[1],
                        "pr": parts[2],
                        "ni": parts[3],
                        "virt": parts[4],
                        "res": parts[5],
                        "shr": parts[6],
                        "s": parts[7],
                        "cpu": parts[8],
                        "mem": parts[9],
                        "time": parts[10],
                        "cmd": parts[11]
                    })
        if any((item.get("user") or "").endswith("+") for item in items):
            ps_res = shell.privileged(
                "process-ps",
                helper_args=[],
                check=False,
                timeout=5,
                fallback=["ps", "-eo", "pid,user:32,pcpu,rss,nlwp,comm,args", "--no-headers"],
            )
            if ps_res.returncode == 0 and (ps_res.stdout or "").strip():
                pid_to_user = {}
                for ps_line in ps_res.stdout.splitlines():
                    ps_parts = ps_line.strip().split(None, 2)
                    if len(ps_parts) >= 2:
                        pid_to_user[ps_parts[0]] = ps_parts[1]
                for item in items:
                    full_user = pid_to_user.get(str(item.get("pid")))
                    if full_user:
                        item["user"] = full_user
        return {
            "summary": "\n".join(summary_lines),
            "items": items
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{pid}/kill")
def kill_process(pid: int, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    if pid <= 2 or pid == os.getpid():
        raise HTTPException(status_code=400, detail="Invalid or protected PID")
    if os.name == 'nt':
        return {"success": True}

    import signal
    import subprocess

    try:
        priv_res = shell.privileged(
            "process-kill",
            helper_args=[str(pid)],
            check=False,
            timeout=5,
            fallback=["kill", "-9", str(pid)],
        )
        if priv_res.returncode == 0:
            return {"success": True}
        err_msg = (priv_res.stderr or priv_res.stdout or "").strip()
        if "No such process" in err_msg:
            raise HTTPException(status_code=404, detail=f"Process {pid} not found")
        if "refusing to kill" in err_msg or "protected" in err_msg:
            raise HTTPException(status_code=400, detail=err_msg)
        os.kill(pid, signal.SIGKILL)
        return {"success": True}
    except ProcessLookupError as exc:
        raise HTTPException(status_code=404, detail=f"Process {pid} not found") from exc
    except PermissionError:
        res = subprocess.run(["kill", "-9", str(pid)], capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise HTTPException(status_code=400, detail=res.stderr.strip() or f"Failed to kill process {pid}")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
