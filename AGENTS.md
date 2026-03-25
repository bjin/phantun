# Repository Guidelines

## Project Overview
Phantun is a lightweight UDP-to-TCP obfuscator written in Rust. It enables UDP traffic to bypass restrictive firewalls by encapsulating it in a minimal, "fake" TCP stream that preserves UDP's out-of-order delivery properties without the overhead of standard TCP flow control or retransmissions.

## Architecture & Data Flow
Phantun operates at Layer 3 using TUN interfaces. It consists of two main crates in a Cargo workspace:
- `fake-tcp`: A core library that implements a minimal, userspace TCP state machine and manual packet construction (IPv4/IPv6 and TCP). It performs a basic 3-way handshake but intentionally omits congestion control and retransmissions to maintain the datagram nature of the original traffic.
- `phantun`: The application providing client and server binaries.

**Data Flow**: 
- **Client**: Listens for incoming UDP packets, translates and wraps the payloads into obfuscated "fake" TCP stream packets, and sends them via the TUN interface. (SNAT is used to masquerade the TUN traffic onto the physical network).
- **Server**: Receives the "fake" TCP connections (routed via DNAT), unwraps them, and forwards the original UDP payloads to the target UDP server.
- The architecture is highly parallel, using Tokio for asynchronous processing, multi-queue TUN support, and multiple worker tasks per connection.

## Key Directories
- `phantun/src/`: Application logic, including client and server entry points.
- `fake-tcp/src/`: Core library for L3/L4 packet parsing and construction, and the userspace TCP stack.
- `docker/`: Docker environment configurations and setup scripts.
- `debian/`, `rpm/`, `selinux/`: Linux distribution-specific packaging and policy files.
- `.github/workflows/`: CI/CD pipelines for linting, building, and extensive cross-compilation.

## Important Files
- `phantun/src/bin/client.rs`: Client entry point; handles UDP listening and connection mapping to fake TCP sockets.
- `phantun/src/bin/server.rs`: Server entry point; handles accepting fake TCP connections and forwarding traffic to UDP targets.
- `fake-tcp/src/lib.rs`: Core stack and socket logic; implements the userspace TCP state machine.
- `fake-tcp/src/packet.rs`: L3/L4 packet construction and parsing. Contains packet building benchmarks.
- `docker/phantun.sh`: Helper script that automates environment setup (sysctl, iptables/nftables) for containerized deployments.

## Development Commands
- **Build**: `cargo build`
- **Lint**: `cargo clippy`
- **Run (Client)**: `RUST_LOG=info cargo run --bin phantun_client -- --local <listen_addr> --remote <server_addr>`
- **Run (Server)**: `RUST_LOG=info cargo run --bin phantun_server -- --local <listen_port> --remote <udp_target>`
*Note: Binaries require `cap_net_admin` capabilities or root privileges to configure TUN interfaces.*

## Code Conventions & Common Patterns
- **Safe Rust**: The project is written almost entirely in safe Rust.
- **Asynchronous Processing**: Extensively uses `tokio` for handling multiple asynchronous networking tasks.
- **Direct Packet Manipulation**: Manipulates TCP/IP headers manually via `fake-tcp` to bypass standard kernel network stack restrictions on TCP streams.

## Runtime/Tooling Preferences
- **Language**: Rust (Workspace with Edition 2024, Resolver 3).
- **Environment**: Linux environments with TUN interface support. Requires kernel IP forwarding (`net.ipv4.ip_forward=1`) and explicit firewall rules (iptables/nftables).
- **Packaging Support**: The repository has extensive CI automation to cross-compile for x86_64, ARM, AArch64, and MIPS.

## Testing & QA
- **End-to-End Integration Tests**: Located in `tests/integration/`, the suite uses a custom Python framework that orchestrates unprivileged Linux user and network namespaces. This allows creating complex isolated topologies (Veth pairs, routing, NAT) to run actual `phantun_client` and `phantun_server` binaries together and verify end-to-end traffic flows. Run via `cargo build && python3 tests/integration/test_e2e.py`.
- **No Automated Unit Tests**: The codebase currently does not use `#[test]` modules for unit testing. Internal testing relies on manual validation and runtime assertions.
- **Benchmarks**: Located in `fake-tcp/src/packet.rs`, gated by the `benchmark` feature (requires nightly Rust toolchain).
- **CI Enforcement**: The GitHub Actions pipeline strictly enforces successful builds, lints (`cargo clippy -- -D warnings`), and runs the integration test suite.