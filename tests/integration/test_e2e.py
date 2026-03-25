import os
import time
import socket
import threading
import unittest
import subprocess

from framework import setup_userns, NetNS, enter_ns

def find_binary(name):
    # Tests are usually run from the project root
    for path in ["./target/debug/", "./target/release/"]:
        bin_path = os.path.join(path, name)
        if os.path.exists(bin_path):
            return bin_path
    raise FileNotFoundError(f"Could not find {name}. Did you run 'cargo build'?")

class PhantunE2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_bin = find_binary("client")
        cls.server_bin = find_binary("server")

        if not os.path.exists("/dev/net/tun"):
            raise unittest.SkipTest("/dev/net/tun does not exist. TUN device support is required for integration tests.")

    def setUp(self):
        self.client_ns = NetNS()
        self.server_ns = NetNS()
        self.processes = []

        # Setup Veth for Client
        subprocess.run(["ip", "link", "add", "veth-c", "type", "veth", "peer", "name", "veth-c-peer"], check=True)
        subprocess.run(["ip", "link", "set", "veth-c-peer", "netns", str(self.client_ns.pid)], check=True)
        subprocess.run(["ip", "addr", "add", "10.100.0.1/24", "dev", "veth-c"], check=True)
        subprocess.run(["ip", "link", "set", "veth-c", "up"], check=True)

        # Setup Veth for Server
        subprocess.run(["ip", "link", "add", "veth-s", "type", "veth", "peer", "name", "veth-s-peer"], check=True)
        subprocess.run(["ip", "link", "set", "veth-s-peer", "netns", str(self.server_ns.pid)], check=True)
        subprocess.run(["ip", "addr", "add", "10.100.1.1/24", "dev", "veth-s"], check=True)
        subprocess.run(["ip", "link", "set", "veth-s", "up"], check=True)

        subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=True, capture_output=True)

        # Configure Client NS
        self.client_ns.run(["ip", "link", "set", "lo", "up"], check=True)
        self.client_ns.run(["ip", "addr", "add", "10.100.0.2/24", "dev", "veth-c-peer"], check=True)
        self.client_ns.run(["ip", "link", "set", "veth-c-peer", "up"], check=True)
        self.client_ns.run(["ip", "route", "add", "default", "via", "10.100.0.1"], check=True)
        self.client_ns.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=True, capture_output=True)
        self.client_ns.run(["iptables", "-t", "nat", "-A", "POSTROUTING", "-o", "veth-c-peer", "-j", "MASQUERADE"], check=True)

        # Configure Server NS
        self.server_ns.run(["ip", "link", "set", "lo", "up"], check=True)
        self.server_ns.run(["ip", "addr", "add", "10.100.1.2/24", "dev", "veth-s-peer"], check=True)
        self.server_ns.run(["ip", "link", "set", "veth-s-peer", "up"], check=True)
        self.server_ns.run(["ip", "route", "add", "default", "via", "10.100.1.1"], check=True)
        self.server_ns.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=True, capture_output=True)
        self.server_ns.run(["iptables", "-t", "nat", "-A", "PREROUTING", "-p", "tcp", "-i", "veth-s-peer", "--dport", "4567", "-j", "DNAT", "--to-destination", "192.168.201.2"], check=True)

        self.echo_server_stop = threading.Event()
        self.echo_server_thread = threading.Thread(target=self.run_echo_server)
        self.echo_server_thread.start()

        ps = self.server_ns.popen([
            self.server_bin,
            "--local", "4567",
            "--remote", "127.0.0.1:9000"
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.processes.append(ps)

        pc = self.client_ns.popen([
            self.client_bin,
            "--local", "127.0.0.1:1234",
            "--remote", "10.100.1.2:4567"
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.processes.append(pc)

        # Wait for initialization
        time.sleep(1.0)

    def tearDown(self):
        self.echo_server_stop.set()
        self.echo_server_thread.join()

        for p in self.processes:
            p.terminate()
            try:
                p.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                p.kill()
            out, err = p.communicate()
            if out:
                print(f"\\n--- STDOUT/ERR of {p.args[0]} ---")
                print(out.decode())

        self.client_ns.cleanup()
        self.server_ns.cleanup()

        subprocess.run(["ip", "link", "del", "veth-c"], stderr=subprocess.DEVNULL)
        subprocess.run(["ip", "link", "del", "veth-s"], stderr=subprocess.DEVNULL)

    def run_echo_server(self):
        # Enter the server namespace for this thread
        enter_ns(self.server_ns.pid)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", 9000))
            sock.settimeout(0.1)

            while not self.echo_server_stop.is_set():
                try:
                    data, addr = sock.recvfrom(65535)
                    sock.sendto(data, addr)
                except socket.timeout:
                    pass

    def run_in_client(self, target, *args):
        """Runs a python function inside the client namespace and returns its result."""
        result = {}
        def thread_worker():
            enter_ns(self.client_ns.pid)
            try:
                result['ret'] = target(*args)
            except Exception as e:
                result['err'] = e

        t = threading.Thread(target=thread_worker)
        t.start()
        t.join()
        if 'err' in result:
            raise result['err']
        return result.get('ret')

    def _do_smoke_test(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2.0)
            sock.sendto(b"hello world", ("127.0.0.1", 1234))
            data, _ = sock.recvfrom(65535)
            return data

    def test_smoke(self):
        res = self.run_in_client(self._do_smoke_test)
        self.assertEqual(res, b"hello world")

    def _do_payload_integrity(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2.0)
            payloads = [b"a", b"12345" * 10, b"xyz", b"\x00\xff\xaa\x55" * 100]
            results = []
            for p in payloads:
                sock.sendto(p, ("127.0.0.1", 1234))
                data, _ = sock.recvfrom(65535)
                results.append(data)
            return results, payloads

    def test_payload_integrity(self):
        results, payloads = self.run_in_client(self._do_payload_integrity)
        self.assertEqual(results, payloads)

    def _do_large_payload(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2.0)
            payload = b"X" * 1400  # near MTU
            sock.sendto(payload, ("127.0.0.1", 1234))
            data, _ = sock.recvfrom(65535)
            return data, payload

    def test_large_payload(self):
        data, payload = self.run_in_client(self._do_large_payload)
        self.assertEqual(data, payload)

    def _do_multiple_streams(self):
        def worker(idx, results_dict):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(2.0)
                payload = f"stream {idx}".encode()
                sock.sendto(payload, ("127.0.0.1", 1234))
                try:
                    data, _ = sock.recvfrom(65535)
                    results_dict[idx] = data
                except Exception as e:
                    results_dict[idx] = e

        threads = []
        results = {}
        for i in range(5):
            t = threading.Thread(target=worker, args=(i, results))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        return results

    def test_multiple_streams(self):
        results = self.run_in_client(self._do_multiple_streams)
        for i in range(5):
            self.assertEqual(results[i], f"stream {i}".encode())

    def _do_throughput_burst(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(3.0)
            n = 50
            # Send in a burst
            for i in range(n):
                sock.sendto(f"burst {i}".encode(), ("127.0.0.1", 1234))

            # Receive them all
            received = set()
            for _ in range(n):
                data, _ = sock.recvfrom(65535)
                received.add(data.decode())

            return len(received), n

    def test_throughput_burst(self):
        rec_count, expected = self.run_in_client(self._do_throughput_burst)
        self.assertEqual(rec_count, expected)

    def _do_reconnect(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2.0)
            sock.sendto(b"first", ("127.0.0.1", 1234))
            res1, _ = sock.recvfrom(65535)

            time.sleep(1.5)  # wait to simulate gap

            sock.sendto(b"second", ("127.0.0.1", 1234))
            res2, _ = sock.recvfrom(65535)
            return res1, res2

    def test_reconnect(self):
        res1, res2 = self.run_in_client(self._do_reconnect)
        self.assertEqual(res1, b"first")
        self.assertEqual(res2, b"second")

    def _do_stress_concurrent_streams(self):
        import random
        def worker(idx, results_dict):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.bind(("0.0.0.0", 0))
                sock.settimeout(5.0)
                sent_payloads = set()
                received_payloads = set()

                try:
                    # Send 100 packets one by one, with random delay
                    for p_idx in range(100):
                        payload = f"stream_{idx}_packet_{p_idx}".encode()
                        sent_payloads.add(payload)
                        sock.sendto(payload, ("127.0.0.1", 1234))
                        time.sleep(random.uniform(0, 0.001))

                    # Receive exactly 100 packets
                    while len(received_payloads) < 100:
                        data, _ = sock.recvfrom(65535)
                        received_payloads.add(data)
                    results_dict[idx] = (sent_payloads, received_payloads, None)
                except Exception as e:
                    results_dict[idx] = (sent_payloads, received_payloads, e)

        threads = []
        results = {}
        for i in range(10):
            t = threading.Thread(target=worker, args=(i, results))
            threads.append(t)
            t.start()
            time.sleep(0.01)
        for t in threads:
            t.join()
        return results

    def test_stress_concurrent_streams(self):
        results = self.run_in_client(self._do_stress_concurrent_streams)
        for i in range(10):
            self.assertIn(i, results)
            sent, received, err = results[i]
            self.assertIsNone(err, f"Stream {i} failed with error: {err}")
            self.assertEqual(len(received), 100, f"Stream {i} expected 100 packets, got {len(received)}")
            self.assertEqual(sent, received, f"Stream {i} sent and received payloads do not match")

if __name__ == '__main__':
    setup_userns()
    unittest.main()
