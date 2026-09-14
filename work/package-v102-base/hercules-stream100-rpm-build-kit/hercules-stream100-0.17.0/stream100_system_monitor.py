"""Dependency-free Linux system metrics for the Stream 100 display."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
from typing import Sequence


PROC_ROOT = Path("/proc")
SYS_ROOT = Path("/sys")


def clamp_ratio(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def parse_cpu_times(text: str) -> tuple[int, int] | None:
    first = text.splitlines()[0].split() if text.strip() else []
    if len(first) < 5 or first[0] != "cpu":
        return None
    try:
        values = [int(value) for value in first[1:]]
    except ValueError:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def cpu_usage_between(
    previous: tuple[int, int] | None,
    current: tuple[int, int] | None,
) -> float:
    if previous is None or current is None:
        return 0.0
    total_delta = current[0] - previous[0]
    idle_delta = current[1] - previous[1]
    if total_delta <= 0:
        return 0.0
    return clamp_ratio((total_delta - max(0, idle_delta)) / total_delta)


def parse_memory_usage(text: str) -> float:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, remainder = line.partition(":")
        if not separator:
            continue
        try:
            values[key] = int(remainder.strip().split()[0])
        except (IndexError, ValueError):
            continue
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    return clamp_ratio((total - available) / total) if total > 0 else 0.0


def parse_temperature(text: str) -> float | None:
    try:
        value = float(text.strip())
    except ValueError:
        return None
    if abs(value) >= 1000:
        value /= 1000.0
    return value if -20.0 <= value <= 150.0 else None


def temperature_ratio(value: float | None) -> float:
    """Map Celsius directly onto the controller's useful 0–100 scale."""
    return 0.0 if value is None else clamp_ratio(value / 100.0)


@dataclass(frozen=True)
class SystemSnapshot:
    cpu_usage: float
    cpu_temperature: float | None
    gpu_usage: float | None
    gpu_temperature: float | None
    memory_usage: float
    disk_usage: float

    @property
    def levels(self) -> list[float]:
        return [
            clamp_ratio(self.cpu_usage),
            clamp_ratio(self.gpu_usage or 0.0),
            clamp_ratio(self.memory_usage),
            clamp_ratio(self.disk_usage),
        ]

    @property
    def meter_levels(self) -> list[tuple[float, float]]:
        levels = self.levels
        return [
            (levels[0], temperature_ratio(self.cpu_temperature)),
            (levels[1], temperature_ratio(self.gpu_temperature)),
            (levels[2], levels[2]),
            (levels[3], levels[3]),
        ]

    @property
    def labels(self) -> list[str]:
        return ["CPU + TEMP", "GPU + TEMP", "MEMORY", "ROOT DISK"]


@dataclass(frozen=True)
class ConfiguredSystemSnapshot:
    source_ids: tuple[str, str, str, str]
    labels: tuple[str, str, str, str]
    levels: tuple[float, float, float, float]
    meter_levels: tuple[
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
    ]


class SystemMonitor:
    """Collect low-cost metrics from procfs, sysfs, and optional nvidia-smi."""

    def __init__(
        self,
        proc_root: Path = PROC_ROOT,
        sys_root: Path = SYS_ROOT,
        disk_path: Path = Path("/"),
    ) -> None:
        self.proc_root = proc_root
        self.sys_root = sys_root
        self.disk_path = disk_path
        self.previous_cpu = self._read_cpu_times()
        self.nvidia_smi = shutil.which("nvidia-smi")

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _read_cpu_times(self) -> tuple[int, int] | None:
        return parse_cpu_times(self._read_text(self.proc_root / "stat"))

    def _cpu_usage(self) -> float:
        current = self._read_cpu_times()
        usage = cpu_usage_between(self.previous_cpu, current)
        self.previous_cpu = current
        return usage

    def _cpu_temperature(self) -> float | None:
        preferred: list[float] = []
        hwmon_root = self.sys_root / "class" / "hwmon"
        for monitor in sorted(hwmon_root.glob("hwmon*")):
            name = self._read_text(monitor / "name").strip().casefold()
            for source in sorted(monitor.glob("temp*_input")):
                value = parse_temperature(self._read_text(source))
                if value is None:
                    continue
                label = self._read_text(
                    source.with_name(source.name.replace("_input", "_label"))
                ).strip().casefold()
                if name in {"coretemp", "k10temp", "zenpower", "cpu_thermal"} or any(
                    token in label for token in ("package", "tctl", "tdie", "cpu")
                ):
                    preferred.append(value)
        return max(preferred) if preferred else None

    def _sysfs_gpu(self) -> tuple[float | None, float | None]:
        for source in self.gpu_sources():
            usage, temperature = self._sysfs_gpu_source(source["id"])
            if usage is not None:
                return usage, temperature
        return None, None

    def _gpu_cards(self) -> list[Path]:
        return [
            card
            for card in sorted((self.sys_root / "class" / "drm").glob("card[0-9]*"))
            if card.name[4:].isdigit() and (card / "device").exists()
        ]

    @staticmethod
    def _pci_address(card: Path) -> str:
        try:
            return card.joinpath("device").resolve().name
        except OSError:
            return card.name

    def _gpu_name(self, card: Path) -> str:
        address = self._pci_address(card)
        try:
            result = subprocess.run(
                ["lspci", "-s", address],
                check=False,
                capture_output=True,
                text=True,
                timeout=0.5,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0 and result.stdout.strip():
            description = result.stdout.strip().partition(": ")[2]
            description = description.replace("Advanced Micro Devices, Inc. [AMD/ATI] ", "")
            description = description.replace("NVIDIA Corporation ", "")
            if description:
                return description
        vendor = self._read_text(card / "device" / "vendor").strip()
        product = self._read_text(card / "device" / "device").strip()
        return f"GPU {vendor}:{product}" if vendor or product else card.name

    def gpu_sources(self) -> list[dict[str, str]]:
        sources: list[dict[str, str]] = []
        for card in self._gpu_cards():
            address = self._pci_address(card)
            name = self._gpu_name(card)
            short_name = name.split("[")[0].strip()
            if len(short_name) > 28:
                short_name = short_name[:27].rstrip() + "…"
            sources.append(
                {
                    "id": f"gpu:{address}",
                    "label": f"GPU — {name} ({address})",
                    "display_label": f"GPU {short_name}",
                }
            )
        return sources

    def available_sources(self) -> list[dict[str, str]]:
        sources = [
            {"id": "cpu", "label": "CPU usage + temperature", "display_label": "CPU + TEMP"},
            *self.gpu_sources(),
            {"id": "memory", "label": "Memory usage", "display_label": "MEMORY"},
        ]
        mounts: list[tuple[str, str]] = [("/", "Root disk usage")]
        try:
            result = subprocess.run(
                ["findmnt", "-rn", "-o", "TARGET"],
                check=False,
                capture_output=True,
                text=True,
                timeout=0.5,
            )
            for target in result.stdout.splitlines() if result.returncode == 0 else []:
                if target.startswith("/") and target not in {item[0] for item in mounts}:
                    if target in {"/home", "/boot", "/boot/efi"}:
                        mounts.append((target, f"Disk usage — {target}"))
        except (OSError, subprocess.TimeoutExpired):
            pass
        sources.extend(
            {
                "id": f"disk:{target}",
                "label": label,
                "display_label": "ROOT DISK" if target == "/" else f"DISK {target.upper()}",
            }
            for target, label in mounts
        )
        return sources

    def _sysfs_gpu_source(self, source_id: str) -> tuple[float | None, float | None]:
        address = source_id.removeprefix("gpu:")
        card = next(
            (item for item in self._gpu_cards() if self._pci_address(item) == address),
            None,
        )
        if card is None:
            return None, None
        device = card / "device"
        usage = None
        for usage_path in (device / "gpu_busy_percent", card / "gt_busy_percent"):
            try:
                usage = clamp_ratio(float(self._read_text(usage_path).strip()) / 100.0)
                break
            except ValueError:
                continue
        temperatures = [
            value
            for source in (device / "hwmon").glob("hwmon*/temp*_input")
            if (value := parse_temperature(self._read_text(source))) is not None
        ]
        return usage, max(temperatures) if temperatures else None

    def _nvidia_gpu(self) -> tuple[float | None, float | None]:
        if self.nvidia_smi is None:
            return None, None
        try:
            result = subprocess.run(
                [
                    self.nvidia_smi,
                    "--query-gpu=utilization.gpu,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=0.8,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None, None
        if result.returncode != 0 or not result.stdout.strip():
            return None, None
        fields = [field.strip() for field in result.stdout.splitlines()[0].split(",")]
        if len(fields) < 2:
            return None, None
        try:
            return clamp_ratio(float(fields[0]) / 100.0), parse_temperature(fields[1])
        except ValueError:
            return None, None

    def _gpu(self) -> tuple[float | None, float | None]:
        usage, temperature = self._sysfs_gpu()
        if usage is not None:
            return usage, temperature
        nvidia_usage, nvidia_temperature = self._nvidia_gpu()
        return (
            nvidia_usage,
            temperature if temperature is not None else nvidia_temperature,
        )

    def _memory_usage(self) -> float:
        return parse_memory_usage(self._read_text(self.proc_root / "meminfo"))

    def _disk_usage(self, path: Path | None = None) -> float:
        try:
            values = os.statvfs(self.disk_path if path is None else path)
        except OSError:
            return 0.0
        total = values.f_blocks
        available = values.f_bavail
        return clamp_ratio((total - available) / total) if total > 0 else 0.0

    def sample(self) -> SystemSnapshot:
        gpu_usage, gpu_temperature = self._gpu()
        return SystemSnapshot(
            cpu_usage=self._cpu_usage(),
            cpu_temperature=self._cpu_temperature(),
            gpu_usage=gpu_usage,
            gpu_temperature=gpu_temperature,
            memory_usage=self._memory_usage(),
            disk_usage=self._disk_usage(),
        )

    def default_source_ids(self) -> list[str]:
        gpu_ids = [source["id"] for source in self.gpu_sources()]
        return ["cpu", gpu_ids[0] if gpu_ids else "memory", "memory", "disk:/"]

    def sample_selected(self, source_ids: Sequence[str]) -> ConfiguredSystemSnapshot:
        available = {source["id"]: source for source in self.available_sources()}
        defaults = self.default_source_ids()
        selected = list(source_ids[:4])
        selected.extend(defaults[len(selected):])
        cpu_usage: float | None = None
        cpu_temperature: float | None = None
        readings: list[tuple[float, float]] = []
        labels: list[str] = []
        resolved_ids: list[str] = []
        for index, requested_id in enumerate(selected):
            source_id = requested_id if requested_id in available else defaults[index]
            source = available.get(source_id, {"display_label": "UNAVAILABLE"})
            if source_id == "cpu":
                if cpu_usage is None:
                    cpu_usage = self._cpu_usage()
                    cpu_temperature = self._cpu_temperature()
                reading = (cpu_usage, temperature_ratio(cpu_temperature))
            elif source_id.startswith("gpu:"):
                usage, temperature = self._sysfs_gpu_source(source_id)
                reading = (clamp_ratio(usage or 0.0), temperature_ratio(temperature))
            elif source_id == "memory":
                value = self._memory_usage()
                reading = (value, value)
            elif source_id.startswith("disk:"):
                value = self._disk_usage(Path(source_id[5:] or "/"))
                reading = (value, value)
            else:
                reading = (0.0, 0.0)
            resolved_ids.append(source_id)
            labels.append(source["display_label"])
            readings.append(reading)
        return ConfiguredSystemSnapshot(
            tuple(resolved_ids), tuple(labels),
            tuple(reading[0] for reading in readings), tuple(readings),
        )
