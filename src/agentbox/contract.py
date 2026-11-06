"""Task contract specification and host-side assertion checks.

Enforces execution budgets (max_steps, max_duration_s, deadline_s) and verifies
host-side assertions (file_exists, regex_match, file_hash, host_command)
so that task success is objectively verified by the trusted host.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class AssertionResult:
    name: str
    kind: str
    passed: bool
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Assertion:
    kind: str  # "file_exists", "regex_match", "file_hash", "command"
    target: str  # workspace-relative (or absolute) file path or shell command
    pattern: Optional[str] = None  # for regex_match
    expected_hash: Optional[str] = None  # for file_hash (sha256 hex)
    expected_exit_code: int = 0  # for command
    name: Optional[str] = None
    timeout_s: float = 30.0  # timeout for command assertions

    def get_name(self) -> str:
        if self.name:
            return self.name
        if self.kind == "file_exists":
            return f"file_exists: {self.target}"
        if self.kind == "regex_match":
            return f"regex_match: {self.target} =~ /{self.pattern}/"
        if self.kind == "file_hash":
            return f"file_hash: {self.target}"
        if self.kind == "command":
            return f"command: {self.target}"
        return f"{self.kind}: {self.target}"

    def check(self, workspace: str | Path) -> AssertionResult:
        ws = Path(workspace)
        name = self.get_name()

        if self.kind == "file_exists":
            p = ws / self.target if not Path(self.target).is_absolute() else Path(self.target)
            if p.exists():
                return AssertionResult(name=name, kind=self.kind, passed=True, details=f"Path exists: {self.target}")
            return AssertionResult(name=name, kind=self.kind, passed=False, details=f"Path does not exist: {self.target}")

        if self.kind == "regex_match":
            p = ws / self.target if not Path(self.target).is_absolute() else Path(self.target)
            if not p.exists():
                return AssertionResult(name=name, kind=self.kind, passed=False, details=f"File not found: {self.target}")
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                return AssertionResult(name=name, kind=self.kind, passed=False, details=f"Cannot read file {self.target}: {e}")
            if self.pattern and re.search(self.pattern, content):
                return AssertionResult(name=name, kind=self.kind, passed=True, details=f"Pattern matched in {self.target}")
            return AssertionResult(name=name, kind=self.kind, passed=False, details=f"Pattern not found in {self.target}")

        if self.kind == "file_hash":
            p = ws / self.target if not Path(self.target).is_absolute() else Path(self.target)
            if not p.exists():
                return AssertionResult(name=name, kind=self.kind, passed=False, details=f"File not found: {self.target}")
            try:
                h = hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError as e:
                return AssertionResult(name=name, kind=self.kind, passed=False, details=f"Cannot read file {self.target}: {e}")
            if h.lower() == (self.expected_hash or "").lower():
                return AssertionResult(name=name, kind=self.kind, passed=True, details=f"Hash matches sha256:{h}")
            return AssertionResult(name=name, kind=self.kind, passed=False, details=f"Hash mismatch: expected {self.expected_hash}, got {h}")

        if self.kind == "command":
            try:
                proc = subprocess.run(
                    self.target,
                    shell=True,
                    cwd=str(ws),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                )
                if proc.returncode == self.expected_exit_code:
                    return AssertionResult(
                        name=name, kind=self.kind, passed=True,
                        details=f"Command exited with code {proc.returncode}"
                    )
                out = (proc.stderr or proc.stdout).strip()[:200]
                return AssertionResult(
                    name=name, kind=self.kind, passed=False,
                    details=f"Exit code {proc.returncode} != {self.expected_exit_code}: {out}"
                )
            except subprocess.TimeoutExpired:
                return AssertionResult(
                    name=name, kind=self.kind, passed=False,
                    details=f"Command timed out after {self.timeout_s}s"
                )
            except Exception as e:
                return AssertionResult(
                    name=name, kind=self.kind, passed=False,
                    details=f"Execution error: {e}"
                )

        return AssertionResult(name=name, kind=self.kind, passed=False, details=f"Unknown assertion kind: {self.kind}")

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "kind": self.kind,
            "target": self.target,
        }
        if self.pattern:
            d["pattern"] = self.pattern
        if self.expected_hash:
            d["expected_hash"] = self.expected_hash
        if self.expected_exit_code != 0:
            d["expected_exit_code"] = self.expected_exit_code
        if self.name:
            d["name"] = self.name
        return d


def assert_file_exists(path: str, name: Optional[str] = None) -> Assertion:
    """Create an assertion that a workspace-relative file or directory exists."""
    return Assertion(kind="file_exists", target=path, name=name)


def assert_file_contains(path: str, pattern: str, name: Optional[str] = None) -> Assertion:
    """Create an assertion that a file matches a regular expression pattern."""
    return Assertion(kind="regex_match", target=path, pattern=pattern, name=name)


def assert_file_hash(path: str, expected_sha256: str, name: Optional[str] = None) -> Assertion:
    """Create an assertion that a file's SHA-256 hash matches the expected hex string."""
    return Assertion(kind="file_hash", target=path, expected_hash=expected_sha256, name=name)


def assert_command(cmd: str, expected_exit_code: int = 0, timeout_s: float = 30.0, name: Optional[str] = None) -> Assertion:
    """Create an assertion that a host shell command runs to completion in workspace with expected exit code."""
    return Assertion(kind="command", target=cmd, expected_exit_code=expected_exit_code, timeout_s=timeout_s, name=name)


@dataclass
class ContractVerdict:
    passed: bool
    summary: str
    assertions: List[AssertionResult] = field(default_factory=list)
    budget_exceeded: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "summary": self.summary,
            "assertions": [a.to_dict() for a in self.assertions],
            "budget_exceeded": self.budget_exceeded,
        }


@dataclass
class TaskContract:
    task_id: Optional[str] = None
    description: Optional[str] = None
    max_steps: Optional[int] = None
    max_duration_s: Optional[float] = None
    deadline_s: Optional[float] = None
    assertions: List[Assertion] = field(default_factory=list)

    def check_step_budget(self, current_step: int) -> Optional[Dict[str, Any]]:
        if self.max_steps is not None and current_step > self.max_steps:
            return {
                "limit": "max_steps",
                "budget": self.max_steps,
                "actual": current_step,
            }
        return None

    def check_duration_budget(self, cumulative_duration_s: float) -> Optional[Dict[str, Any]]:
        if self.max_duration_s is not None and cumulative_duration_s > self.max_duration_s:
            return {
                "limit": "max_duration_s",
                "budget": self.max_duration_s,
                "actual": round(cumulative_duration_s, 3),
            }
        return None

    def check_deadline_budget(self, elapsed_wall_clock_s: float) -> Optional[Dict[str, Any]]:
        if self.deadline_s is not None and elapsed_wall_clock_s > self.deadline_s:
            return {
                "limit": "deadline_s",
                "budget": self.deadline_s,
                "actual": round(elapsed_wall_clock_s, 3),
            }
        return None

    def evaluate(
        self,
        workspace: str | Path,
        steps: int = 0,
        cumulative_duration_s: float = 0.0,
        elapsed_wall_clock_s: float = 0.0,
    ) -> ContractVerdict:
        budget_viol = (
            self.check_step_budget(steps)
            or self.check_duration_budget(cumulative_duration_s)
            or self.check_deadline_budget(elapsed_wall_clock_s)
        )

        results = [a.check(workspace) for a in self.assertions]
        assertions_passed = all(r.passed for r in results)
        passed = assertions_passed and (budget_viol is None)

        if budget_viol:
            summary = f"Budget exceeded: {budget_viol['limit']} ({budget_viol['actual']} > {budget_viol['budget']})"
        elif not results:
            summary = "Contract satisfied (no assertions specified)"
        elif assertions_passed:
            summary = f"All assertions passed ({len(results)}/{len(results)})"
        else:
            failed_count = sum(1 for r in results if not r.passed)
            summary = f"{failed_count}/{len(results)} assertions failed"

        return ContractVerdict(
            passed=passed,
            summary=summary,
            assertions=results,
            budget_exceeded=budget_viol,
        )

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {}
        if self.task_id:
            d["task_id"] = self.task_id
        if self.description:
            d["description"] = self.description
        if self.max_steps is not None:
            d["max_steps"] = self.max_steps
        if self.max_duration_s is not None:
            d["max_duration_s"] = self.max_duration_s
        if self.deadline_s is not None:
            d["deadline_s"] = self.deadline_s
        d["assertions"] = [a.to_dict() for a in self.assertions]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskContract:
        assertions: List[Assertion] = []
        for raw in data.get("assertions", []):
            if isinstance(raw, Assertion):
                assertions.append(raw)
            elif isinstance(raw, dict):
                assertions.append(
                    Assertion(
                        kind=raw.get("kind", "file_exists"),
                        target=raw.get("target") or raw.get("path", ""),
                        pattern=raw.get("pattern"),
                        expected_hash=raw.get("expected_hash"),
                        expected_exit_code=raw.get("expected_exit_code", 0),
                        name=raw.get("name"),
                        timeout_s=float(raw.get("timeout_s", 30.0)),
                    )
                )
        return cls(
            task_id=data.get("task_id"),
            description=data.get("description"),
            max_steps=data.get("max_steps"),
            max_duration_s=data.get("max_duration_s"),
            deadline_s=data.get("deadline_s"),
            assertions=assertions,
        )
