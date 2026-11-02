# Changelog

All notable changes are documented here. This project follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-02

### Added

- Local, read-only dashboard for NVIDIA GPUs on SSH-reachable hosts.
- GPU, process, memory, power and temperature views with Chinese / English UI support.
- macOS launcher scripts, mock demo and offline unit tests.
- NVML-based GPU utilization history, recent mean / peak summaries and coverage indicators.
- Automatic SSH host discovery, incremental host setup and `doctor` prerequisite checks.
- Source-package metadata, a `gpu-monitor` console entry, citation metadata and release guidance.

### Changed

- Exporter ports are allocated automatically; explicit legacy ports remain supported.
- Exporter cache verification and recovery from an unresponsive local exporter were strengthened.
