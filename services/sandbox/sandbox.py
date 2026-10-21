"""Sandboxed execution.

Two backends behind one interface:
- DockerSandbox (preferred): real isolation — no network, memory/CPU/pids
  limits, read-only host, workspace mounted as the only writable volume.
- ProcessSandbox (fallback): rlimits (CPU, address space, nproc, fsize),
  new process group, env scrubbing, best-effort network namespace via
  `unshare -n`, hard timeout with group kill.

AI-generated code NEVER runs directly on the host outside these backends.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import time

# `resource` (rlimits) is Unix-only; on Windows the process backend degrades
# to plain subprocess isolation (no rlimits / process-group kill).
if sys.platform != "win32":
    import resource
from dataclasses import dataclass, field
from pathlib import Path

from core.config import get_settings
from core.errors import SandboxError
from core.logging import get_logger
from core.metrics import SANDBOX_RUNS

log = get_logger("nexusforge.sandbox")


@dataclass
class ExecutionResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_ms: int = 0
    backend: str = ""
    error: str | None = None


class Sandbox:
    backend_name = "base"

    async def run(
        self,
        argv: list[str],
        workdir: str,
        *,
        timeout: float | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> ExecutionResult:
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# Docker backend
# ---------------------------------------------------------------------------


class DockerSandbox(Sandbox):
    backend_name = "docker"

    def __init__(self) -> None:
        s = get_settings()
        self.image = s.sandbox_image
        self.memory_mb = s.sandbox_memory_mb
        self.cpus = s.sandbox_cpus
        self.pids = s.sandbox_pids_limit
        self.network = s.sandbox_network
        self._image_ready = False

    def available(self) -> bool:
        return shutil.which("docker") is not None

    async def ensure_image(self) -> None:
        if self._image_ready:
            return
        # build the sandbox image from the python slim base + project deps
        dockerfile = (
            f"FROM {get_settings().sandbox_python_image}\n"
            "WORKDIR /work\n"
            "RUN pip install --no-cache-dir pytest ruff bandit\n"
            "CMD [\"python\"]\n"
        )
        build_dir = Path(get_settings().workspace_root).parent / "sandbox-image"
        build_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        proc = await asyncio.create_subprocess_exec(
            "docker", "build", "-q", "-t", self.image, str(build_dir),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise SandboxError(f"docker image build failed: {err.decode()[:300]}")
        self._image_ready = True
        log.info("sandbox image ready", extra={"image": self.image})

    async def run(self, argv, workdir, *, timeout=None, env_extra=None) -> ExecutionResult:
        if not self.available():
            raise SandboxError("docker CLI not found")
        await self.ensure_image()
        s = get_settings()
        timeout = timeout or s.sandbox_timeout_seconds
        cmd = [
            "docker", "run", "--rm", "--init",
            "--network", "none" if not self.network else "bridge",
            f"--memory={self.memory_mb}m",
            f"--cpus={self.cpus}",
            f"--pids-limit={self.pids}",
            "--security-opt", "no-new-privileges",
            "-w", "/work",
            "-v", f"{os.path.abspath(workdir)}:/work",
        ]
        for k, v in (env_extra or {}).items():
            if k.isidentifier() and k.isupper():
                cmd += ["-e", f"{k}={v}"]
        cmd += [self.image, *argv]
        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                timed_out = False
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                timed_out = True
                out, err = b"", b"execution timed out"
        except OSError as e:
            raise SandboxError(f"failed to start docker: {e}") from e
        duration = int((time.monotonic() - t0) * 1000)
        result = ExecutionResult(
            exit_code=proc.returncode,
            stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"),
            timed_out=timed_out,
            duration_ms=duration,
            backend=self.backend_name,
        )
        SANDBOX_RUNS.labels("docker", "timeout" if timed_out else
                            "ok" if proc.returncode == 0 else "failed").inc()
        return result


# ---------------------------------------------------------------------------
# Process backend (rlimits)
# ---------------------------------------------------------------------------


class ProcessSandbox(Sandbox):
    backend_name = "process"

    def __init__(self) -> None:
        s = get_settings()
        self.memory_mb = s.sandbox_memory_mb
        self.cpus = s.sandbox_cpus
        self.pids = s.sandbox_pids_limit
        self.network = s.sandbox_network

    def _preexec(self) -> None:
        """Runs in the child after fork: apply rlimits + new process group.

        Unix only — on Windows rlimits do not exist and the child is started
        with CREATE_NEW_PROCESS_GROUP instead (see `run`).
        """
        os.setsid()
        # CPU seconds: cap at 4x the wall-clock timeout (the parent also
        # enforces the wall budget and kills the whole group)
        cpu = max(60, 4 * get_settings().sandbox_timeout_seconds)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        # address space
        mem = self.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        # process count: RLIMIT_NPROC is per-USER, so never lower it below
        # the current soft limit (that would break unrelated processes)
        cur = resource.getrlimit(resource.RLIMIT_NPROC)[0]
        nproc = max(self.pids, cur if cur != resource.RLIM_INFINITY else 0)
        resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
        # file size
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024,
                                                   64 * 1024 * 1024))

    def _can_unshare(self) -> bool:
        """Best-effort: can we create a network namespace? (requires the
        unshare binary and the CAP_SYS_ADMIN / user-namespace privilege)."""
        if shutil.which("unshare") is None:
            return False
        try:
            probe = subprocess.run(
                ["unshare", "-n", "true"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            )
            return probe.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _env(self, env_extra: dict[str, str] | None) -> dict[str, str]:
        """Scrubbed environment: no host secrets, minimal PATH.

        PYTHONPATH is inherited (never a secret) so the sandboxed
        interpreter resolves the same packages as the host interpreter —
        otherwise `python -m pytest` in the sandbox cannot import pytest.
        """
        if sys.platform == "win32":
            env = {
                "PATH": os.environ.get("PATH", "C:\\Windows\\System32;C:\\Windows"),
                "HOME": os.environ.get("USERPROFILE", os.environ.get("TEMP", "C:\\")),
                "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
                "WINDIR": os.environ.get("WINDIR", "C:\\Windows"),
                "SYSTEMDRIVE": os.environ.get("SYSTEMDRIVE", "C:"),
                "TEMP": os.environ.get("TEMP", "C:\\Windows\\Temp"),
                "TMP": os.environ.get("TMP", "C:\\Windows\\Temp"),
                "USERPROFILE": os.environ.get("USERPROFILE", "C:\\Users\\HP"),
                "APPDATA": os.environ.get("APPDATA", ""),
                "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
                "COMSPEC": os.environ.get("COMSPEC", "C:\\Windows\\system32\\cmd.exe"),
                "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
            }
        else:
            env = {
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "HOME": "/tmp",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "LC_ALL": "C.UTF-8",
            }
        # inherit the host PYTHONPATH, and make sure the host interpreter's
        # site-packages are visible so sandboxed `python -m pytest` etc.
        # resolve the same packages as the host
        parts = ["."]
        for p in os.environ.get("PYTHONPATH", "").split(os.pathsep):
            if p and p not in parts:
                parts.append(p)
        try:
            import site
            for sp in site.getsitepackages():
                if sp not in parts:
                    parts.append(sp)
        except Exception:  # pragma: no cover - site should always import
            pass
        if parts:
            env["PYTHONPATH"] = os.pathsep.join(parts)
        for k, v in (env_extra or {}).items():
            if k.isidentifier() and k.isupper():
                env[k] = v
        return env

    async def run(self, argv, workdir, *, timeout=None, env_extra=None) -> ExecutionResult:
        s = get_settings()
        timeout = timeout or s.sandbox_timeout_seconds
        workdir = os.path.abspath(workdir)
        if not Path(workdir).is_dir():
            raise SandboxError(f"workdir does not exist: {workdir}")

        # best-effort network namespace isolation when permitted
        use_unshare = (not self.network) and self._can_unshare()
        if use_unshare:
            full_argv = ["unshare", "-n", "--", *argv]
        else:
            full_argv = list(argv)

        if sys.platform == "win32" and full_argv:
            if full_argv[0] in ("python", "python3"):
                full_argv[0] = sys.executable
            elif full_argv[0] == "pytest":
                full_argv = [sys.executable, "-m", "pytest", *full_argv[1:]]

        t0 = time.monotonic()
        timed_out = False
        proc_kwargs: dict = {
            "cwd": workdir,
            "env": self._env(env_extra),
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if sys.platform == "win32":
            proc_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            proc_kwargs["preexec_fn"] = self._preexec

        try:
            proc = await asyncio.create_subprocess_exec(
                *full_argv,
                **proc_kwargs,
            )
        except OSError as e:
            raise SandboxError(f"failed to start sandbox process: {e}") from e

        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            self._kill_group(proc)
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=5)
            except asyncio.TimeoutError:
                out, err = b"", b""
        duration = int((time.monotonic() - t0) * 1000)
        result = ExecutionResult(
            exit_code=proc.returncode,
            stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"),
            timed_out=timed_out,
            duration_ms=duration,
            backend=self.backend_name,
        )
        SANDBOX_RUNS.labels("process", "timeout" if timed_out else
                            "ok" if proc.returncode == 0 else "failed").inc()
        return result

    @staticmethod
    def _kill_group(proc: asyncio.subprocess.Process) -> None:
        if sys.platform == "win32":
            # taskkill /T kills the whole process tree; /F is force.
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                )
            except (OSError, subprocess.SubprocessError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            return
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_sandbox() -> Sandbox:
    s = get_settings()
    if s.sandbox_backend == "docker":
        return DockerSandbox()
    if s.sandbox_backend == "process":
        return ProcessSandbox()
    # auto
    if DockerSandbox().available():
        log.info("sandbox backend: docker")
        return DockerSandbox()
    log.info("sandbox backend: process (docker not available)")
    return ProcessSandbox()