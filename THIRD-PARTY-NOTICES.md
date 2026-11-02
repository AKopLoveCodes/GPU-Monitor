# Third-party notices

GPU Monitor
Copyright (c) 2026 GPU Monitor Authors
License: MIT (see [LICENSE](LICENSE))

This product uses software developed by third parties.

---

## nvidia_gpu_exporter

- Project: <https://github.com/utkuozdemir/nvidia_gpu_exporter>
- Copyright (c) 2024 Utku Özdemir
- License: MIT (full text below)
- Version used: official release binary **v1.16.0**, pinned and SHA-256 verified by
  [`scripts/bootstrap.py`](scripts/bootstrap.py)
- How it is used: this repository **does not contain, modify or redistribute** its source
  code or binaries. On first start, `scripts/bootstrap.py` downloads the official release
  archive from GitHub and runs the binary as a separate subprocess on `127.0.0.1`; GPU
  metrics are read from its `/metrics` HTTP endpoint. All `nvidia_smi_*` metric names and
  semantics come from that project.
- The downloaded binary is stored outside version control in `.tools/`, together with the
  upstream license text extracted from the same archive.

### MIT License

Copyright (c) 2024 Utku Özdemir

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## NVIDIA software (not distributed)

Monitored servers must already have an NVIDIA driver installed by their own
administrator; `nvidia-smi` is executed read-only over SSH on those servers. GPU Monitor
does not bundle, download or redistribute any NVIDIA driver, toolkit or binary, and makes
no claim of affiliation with or endorsement by NVIDIA Corporation. NVIDIA and GeForce are
trademarks of NVIDIA Corporation, referenced here descriptively only.
