from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
from pathlib import Path
from typing import Any
from uuid import uuid4

from .routing import POLICY, evaluate, route

from acp import (
    PROTOCOL_VERSION,
    Agent,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    SetSessionConfigOptionResponse,
    run_agent,
    text_block,
    update_agent_message,
)
from acp.interfaces import Client
from acp.schema import (
    AgentCapabilities,
    AudioContentBlock,
    ClientCapabilities,
    EmbeddedResourceContentBlock,
    HttpMcpServer,
    ImageContentBlock,
    Implementation,
    McpServerStdio,
    ResourceContentBlock,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SseMcpServer,
    TextContentBlock,
)


REQUESTED_MODEL = "vercel_ai_gateway/" + POLICY["defaultModel"]
FX_BINARY = Path(__file__).resolve().parent.parent / "bin" / "fx"
FX_BINARY_SHA256 = (FX_BINARY.parent / "fx.sha256").read_text().strip()
FX_LOG = Path("/logs/agent/fx.json")
FX_STDERR_LOG = Path("/logs/agent/fx-stderr.log")
FX_TRACE_LOG = Path("/logs/agent/fx-trace.log")


class FxAskAgent(Agent):
    """Expose the exact FX CLI ask path through Harbor Hub's ACP transport."""

    _conn: Client

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, str]] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    def on_connect(self, conn: Client) -> None:
        self._conn = conn

    @staticmethod
    def _model_option() -> SessionConfigOptionSelect:
        return SessionConfigOptionSelect(
            current_value=REQUESTED_MODEL,
            options=[
                SessionConfigSelectOption(
                    value=REQUESTED_MODEL,
                    name="Frozen fx baseline via Vercel AI Gateway",
                )
            ],
            id="model",
            name="Model",
            category="model",
            type="select",
        )

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        del protocol_version, client_capabilities, client_info, kwargs
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(),
            agent_info=Implementation(
                name="fx-jev-matrix",
                title="fx with optional Jev routing and extractive compaction",
                version="0.1.0",
            ),
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[
            HttpMcpServer | SseMcpServer | McpServerStdio
        ] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        del additional_directories, mcp_servers, kwargs
        session_id = uuid4().hex
        self._sessions[session_id] = {"cwd": cwd, "model": REQUESTED_MODEL}
        return NewSessionResponse(
            session_id=session_id,
            config_options=[self._model_option()],
        )

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str | bool,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse:
        del kwargs
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        if config_id != "model" or value != REQUESTED_MODEL:
            raise ValueError(f"unsupported model selection: {config_id}={value}")
        session["model"] = REQUESTED_MODEL
        return SetSessionConfigOptionResponse(
            config_options=[self._model_option()]
        )

    @staticmethod
    def _prompt_text(
        prompt: list[
            TextContentBlock
            | ImageContentBlock
            | AudioContentBlock
            | ResourceContentBlock
            | EmbeddedResourceContentBlock
        ],
    ) -> str:
        parts: list[str] = []
        for block in prompt:
            if isinstance(block, TextContentBlock):
                parts.append(block.text)
                continue
            text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
        return "\n".join(parts)

    @staticmethod
    def _configure_fx() -> None:
        settings_dir = Path.home() / ".fx"
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / "settings.json").write_text(
            json.dumps({"effort": "high", "auto_upgrade": False}),
            encoding="utf-8",
        )

    @staticmethod
    async def _ensure_tmux() -> None:
        if shutil.which("tmux"):
            return
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            raise RuntimeError("tmux is required but the task agent is not root")

        if shutil.which("apt-get"):
            command = [
                "/bin/sh",
                "-lc",
                "DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
                "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tmux",
            ]
        elif shutil.which("apk"):
            command = ["apk", "add", "--no-cache", "tmux"]
        elif shutil.which("dnf"):
            command = ["dnf", "install", "-y", "tmux"]
        elif shutil.which("yum"):
            command = ["yum", "install", "-y", "tmux"]
        else:
            raise RuntimeError("tmux is required and no supported package manager exists")

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = (stderr or stdout).decode("utf-8", "replace")[-1000:]
            raise RuntimeError(f"tmux installation failed: {detail}")

    @staticmethod
    def _final_text(stdout: str) -> str:
        for line in reversed(stdout.splitlines()):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            value = record.get("output") or record.get("final_output")
            if isinstance(value, str) and value:
                return value
        return stdout.strip() or "FX completed without a textual final response."

    async def prompt(
        self,
        session_id: str,
        prompt: list[
            TextContentBlock
            | ImageContentBlock
            | AudioContentBlock
            | ResourceContentBlock
            | EmbeddedResourceContentBlock
        ],
        **kwargs: Any,
    ) -> PromptResponse:
        del kwargs
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")

        instruction = self._prompt_text(prompt)
        if not instruction:
            raise ValueError("FX requires a non-empty text prompt")
        if not FX_BINARY.is_file():
            raise RuntimeError(f"pinned FX binary is missing: {FX_BINARY}")
        binary_sha256 = hashlib.sha256(FX_BINARY.read_bytes()).hexdigest()
        if binary_sha256 != FX_BINARY_SHA256:
            raise RuntimeError(
                "pinned FX binary checksum mismatch: "
                f"expected {FX_BINARY_SHA256}, got {binary_sha256}"
            )

        await self._ensure_tmux()
        self._configure_fx()
        FX_LOG.parent.mkdir(parents=True, exist_ok=True)

        if not os.environ.get("AI_GATEWAY_API_KEY"):
            raise RuntimeError("Direct Gateway credential missing; do not launch the matrix")
        if os.environ.get("FX_JEV_PREFLIGHT") == "1" and not session.get("preflight"):
            boolean_probe = await asyncio.to_thread(evaluate, "The old command completed successfully. The current task needs its exact result.", {
                "retain": {"type": "boolean", "instructions": "Is the exact old command result still needed?"}
            })
            answer = boolean_probe.get("answers", {}).get("retain", {})
            probability = answer.get("probability")
            if answer.get("type") != "boolean" or not isinstance(probability, (int, float)) or not 0 <= probability <= 1:
                raise RuntimeError("Jev boolean protocol preflight failed")
            probes = []
            for candidate in POLICY["models"]:
                probe = await asyncio.create_subprocess_exec(
                    str(FX_BINARY), "ask", "--json", "--model", candidate["id"], "--effort", "high", "--", "Reply with OK. Do not use tools.",
                    cwd=session["cwd"], stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                try:
                    out, err = await asyncio.wait_for(probe.communicate(), timeout=90)
                except TimeoutError:
                    probe.kill()
                    await probe.wait()
                    raise RuntimeError("Candidate-model preflight timed out") from None
                probes.append({"model": candidate["id"], "exitCode": probe.returncode})
                if probe.returncode != 0:
                    raise RuntimeError(f"Candidate-model preflight failed: {candidate['id']}")
            (FX_LOG.parent / "jev-preflight.json").write_text(json.dumps({"boolean": boolean_probe, "models": probes}, indent=2))
            session["preflight"] = True
        model = session["model"].removeprefix("vercel_ai_gateway/")
        if "decision" not in session:
            if os.environ.get("FX_EXPERIMENT_JEV_ROUTING") == "1":
                decision = await asyncio.to_thread(route, instruction)
                if os.environ.get("FX_JEV_REQUIRE_EVALUATION") == "1" and decision["error"]:
                    raise RuntimeError("Jev smoke evaluation failed; do not launch the matrix")
            else:
                decision = {"model": model, "reason": "routing_disabled", "classification": None}
            session["decision"] = decision
            (FX_LOG.parent / "jev-routing.json").write_text(json.dumps(decision, indent=2))
        model = session["decision"]["model"]
        env = dict(os.environ)
        env.update(
            {
                "FX_AUTO_UPGRADE": "0",
                "FX_MODEL": model,
                "FX_SOUND": "0",
                "FX_TRACE_LOG": str(FX_TRACE_LOG),
                "FX_TRACE_SCOPES": "quality,gateway,agent,history,tool,context_compaction",
            }
        )

        command = [str(FX_BINARY), "ask", "--yolo", "--json", "--model", model, "--effort", "high"]
        if saved := session.get("fx_session"):
            command.extend(["--resume-id", saved])
        command.extend(["--", instruction])
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=session["cwd"],
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self._processes[session_id] = process
        try:
            stdout_bytes, stderr_bytes = await process.communicate()
        finally:
            self._processes.pop(session_id, None)

        stdout = stdout_bytes.decode("utf-8", "replace")
        stderr = stderr_bytes.decode("utf-8", "replace")
        FX_LOG.write_text(stdout, encoding="utf-8")
        FX_STDERR_LOG.write_text(stderr, encoding="utf-8")

        trace = FX_TRACE_LOG.read_text() if FX_TRACE_LOG.exists() else ""
        (FX_LOG.parent / "jev-telemetry.json").write_text(json.dumps({
            "binarySha256": binary_sha256, "model": model,
            "routingEnabled": os.environ.get("FX_EXPERIMENT_JEV_ROUTING") == "1",
            "compactionEnabled": os.environ.get("FX_EXPERIMENT_JEV_COMPACTION") == "1",
            "evaluations": trace.count("event=jev_evaluated"),
            "extractiveCompactions": trace.count("event=jev_completed"),
            "fallbacks": trace.count("event=jev_fallback"),
            "costCaveat": "Include Jev token usage from trace and routing log; fx cost may be incomplete.",
        }, indent=2))
        for line in stdout.splitlines():
            try:
                envelope = json.loads(line)
                if isinstance(envelope, dict) and envelope.get("session_id"):
                    session["fx_session"] = envelope["session_id"]
                    usage_path = Path.home() / ".fx" / "sessions" / envelope["session_id"] / "usage-v2.json"
                    if usage_path.is_file():
                        shutil.copyfile(usage_path, FX_LOG.parent / "fx-usage.json")
            except json.JSONDecodeError:
                pass
        if process.returncode != 0:
            detail = stderr.strip() or stdout.strip() or "no diagnostic output"
            raise RuntimeError(
                f"fx ask failed with exit code {process.returncode}: {detail[-2000:]}"
            )

        await self._conn.session_update(
            session_id=session_id,
            update=update_agent_message(text_block(self._final_text(stdout))),
        )
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        del kwargs
        process = self._processes.get(session_id)
        if process is None or process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return


async def main() -> None:
    await run_agent(FxAskAgent())


if __name__ == "__main__":
    asyncio.run(main())
