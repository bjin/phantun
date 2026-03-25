import os
import sys
import subprocess

def setup_userns():
    try:
        uid = os.getuid()
        gid = os.getgid()
        os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWNET)
        with open("/proc/self/setgroups", "w") as f:
            f.write("deny")
        with open("/proc/self/uid_map", "w") as f:
            f.write(f"0 {uid} 1\n")
        with open("/proc/self/gid_map", "w") as f:
            f.write(f"0 {gid} 1\n")
        
        # Bring up loopback
        subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
    except PermissionError:
        print("PermissionError: Unprivileged user namespaces might be restricted.", file=sys.stderr)
        sys.exit(1)

def enter_ns(pid):
    fd = os.open(f"/proc/{pid}/ns/net", os.O_RDONLY)
    os.setns(fd, os.CLONE_NEWNET)
    os.close(fd)

class NetNS:
    def __init__(self):
        r1, w1 = os.pipe()
        r2, w2 = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(r1)
            os.close(w2)
            os.unshare(os.CLONE_NEWNET)
            os.write(w1, b"1")
            os.read(r2, 1)
            os._exit(0)
        
        os.close(w1)
        os.close(r2)
        # Wait for child to finish unshare
        os.read(r1, 1)
        os.close(r1)
        
        self.pid = pid
        self.kill_pipe = w2

    def cleanup(self):
        os.write(self.kill_pipe, b"1")
        os.waitpid(self.pid, 0)
        os.close(self.kill_pipe)

    def popen(self, cmd, **kwargs):
        def preexec():
            enter_ns(self.pid)
        return subprocess.Popen(cmd, preexec_fn=preexec, **kwargs)

    def run(self, cmd, **kwargs):
        def preexec():
            enter_ns(self.pid)
        return subprocess.run(cmd, preexec_fn=preexec, **kwargs)
