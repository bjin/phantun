import os
import sys
import subprocess

def setup_userns():
    """
    Creates a new user and network namespace, mapping the current unprivileged
    user to 'root' (UID/GID 0) inside the namespace.

    This allows the test suite to execute networking commands (like `ip link`,
    `iptables`, and TUN interface creation) without requiring actual host root privileges.

    Requirements:
    - Host kernel must have User Namespaces enabled:
        - Debian/Ubuntu/Arch: `sysctl kernel.unprivileged_userns_clone=1`
        - RHEL/CentOS/Fedora: `sysctl user.max_user_namespaces` > 0
    - `/dev/net/tun` must be accessible.

    Note: Writing "deny" to /proc/self/setgroups is strictly required by the kernel
    before writing to /proc/self/gid_map to prevent unprivileged users from dropping
    groups and bypassing negative permissions.
    """
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
    """
    Represents an isolated network namespace.

    Since Python's thread model doesn't play well with namespaces (os.setns affects the whole process),
    we create a persistent dummy child process that enters a new network namespace and just sleeps.
    We can then run commands inside this namespace by referencing the child process's PID
    (e.g., via `/proc/<pid>/ns/net`).
    """
    def __init__(self):
        # Pipe 1: Child -> Parent (Signals when child has finished unshare)
        r1, w1 = os.pipe()
        # Pipe 2: Parent -> Child (Signals when child should exit, closing the namespace)
        r2, w2 = os.pipe()

        pid = os.fork()
        if pid == 0:
            os.close(r1)
            os.close(w2)

            # Child enters a new network namespace
            os.unshare(os.CLONE_NEWNET)

            # Signal parent that the unshare is complete and the namespace exists
            os.write(w1, b"1")

            # Block until parent writes to w2 (which happens in cleanup())
            os.read(r2, 1)
            os._exit(0)

        os.close(w1)
        os.close(r2)

        # Parent blocks until child signals it has finished creating the namespace
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
