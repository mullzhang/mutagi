"""Run one managed App Server turn using usage notifications and the interrupt API."""

from __future__ import annotations

import argparse
import json
import selectors
import subprocess
import time
from decimal import Decimal
from pathlib import Path

from mutagi.execution.usage import measured
from mutagi.runtime.storage import atomic_json


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def execute(run):
    state = json.loads((run / "state.json").read_text())
    budget = state["usage_budget"]
    rates = budget["rates"]
    usage = {
        "tokens": 0,
        "cost": "0",
        "complete": False,
        "unit": rates["unit"],
        "rates": rates,
    }
    atomic_json(run / "usage.json", usage)
    with (run / "app-server.stderr.log").open("wb") as errors:
        process = subprocess.Popen(
            ["codex", "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        buffer = b""
        next_id = 0
        responses = {}
        thread = None
        turn = None
        final_text = None
        done = None
        stopped = None
        interrupt_sent = None
        reports = 0

        def send(method, params):
            nonlocal next_id
            next_id += 1
            process.stdin.write(
                (
                    json.dumps({"id": next_id, "method": method, "params": params})
                    + "\n"
                ).encode()
            )
            process.stdin.flush()
            return next_id

        def receive():
            nonlocal buffer, final_text, done, stopped, reports, thread, turn
            if not selector.select(0.2):
                if process.poll() is not None:
                    raise RuntimeError("App Server exited before the turn completed")

                return

            import os

            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("the App Server stream closed")

            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                event = json.loads(line)
                if "id" in event and "method" not in event:
                    responses[event["id"]] = event
                    continue

                if "id" in event:
                    # Handle authorized escalations through automatic review. Other requests
                    # require human judgment and must not be silently treated as approval.
                    process.stdin.write(
                        (
                            json.dumps(
                                {
                                    "id": event["id"],
                                    "error": {
                                        "code": -32000,
                                        "message": "unattended sessions cannot answer user questions or approval requests",
                                    },
                                }
                            )
                            + "\n"
                        ).encode()
                    )
                    process.stdin.flush()
                    stopped = "requires_user_action"
                    continue

                method = event["method"]
                params = event.get("params", {})
                if method == "turn/started":
                    turn = params["turn"]["id"]
                elif method == "thread/tokenUsage/updated":
                    if params["threadId"] != thread:
                        raise RuntimeError("usage belongs to an unmanaged thread")

                    value = measured(params["tokenUsage"]["total"], rates)
                    if value["tokens"] < usage["tokens"]:
                        raise ValueError("cumulative token usage decreased")

                    usage.update(value)
                    reports += 1
                    atomic_json(run / "usage.json", usage)
                    emit({"type": "usage.updated", **value})
                    if value["tokens"] >= budget["tokens"] or Decimal(
                        value["cost"]
                    ) >= Decimal(str(budget["cost"])):
                        stopped = "usage_limit"
                elif method == "item/completed":
                    item = params["item"]
                    if item["type"] == "agentMessage":
                        emit(
                            {
                                "type": "item.completed",
                                "item": {"type": "agent_message", "text": item["text"]},
                            }
                        )
                        if item.get("phase") == "final_answer":
                            final_text = item["text"]
                elif method == "model/rerouted":
                    stopped = "unpriced_model"
                elif method == "turn/completed":
                    done = params["turn"]

        def request(method, params):
            identifier = send(method, params)
            until = time.monotonic() + 30
            while identifier not in responses:
                if time.monotonic() >= until:
                    raise TimeoutError(f"App Server did not respond to {method}")

                receive()

            response = responses.pop(identifier)
            if "error" in response:
                raise RuntimeError(f"{method}: {response['error']}")

            return response["result"]

        try:
            request(
                "initialize", {"clientInfo": {"name": "naedo_forest", "version": "1.0"}}
            )
            process.stdin.write(b'{"method":"initialized"}\n')
            process.stdin.flush()
            started = request(
                "thread/start",
                {
                    "model": state["model"],
                    "cwd": state["worktree"],
                    "sandbox": "workspace-write",
                    "approvalPolicy": "on-request",
                    "approvalsReviewer": "auto_review",
                    "serviceTier": "default",
                    "config": {"features.multi_agent": False},
                },
            )
            thread = started["thread"]["id"]
            if started["model"] != state["model"]:
                raise RuntimeError(
                    "the server selected a model that differs from the rate configuration"
                )

            emit({"type": "thread.started", "thread_id": thread})
            atomic_json(
                run / "app-server-session.json",
                {
                    "thread_id": thread,
                    "model": started["model"],
                    "service_tier": "default",
                },
            )
            response = request(
                "turn/start",
                {
                    "threadId": thread,
                    "input": [
                        {"type": "text", "text": (run / "prompt.txt").read_text()}
                    ],
                    "effort": state["effort"],
                    "serviceTierForTurn": "default",
                    "outputSchema": json.loads((run / "schema.json").read_text()),
                },
            )
            turn = response["turn"]["id"]
            while done is None:
                receive()
                if time.time() >= state["exploration_deadline"] - 5 and stopped is None:
                    stopped = "time_limit"

                if stopped and interrupt_sent is None:
                    send("turn/interrupt", {"threadId": thread, "turnId": turn})
                    interrupt_sent = time.monotonic()
                    emit(
                        {
                            "type": "turn.interrupt_requested",
                            "reason": stopped,
                            "thread_id": thread,
                            "turn_id": turn,
                        }
                    )

                if interrupt_sent is not None and time.monotonic() - interrupt_sent > 5:
                    raise TimeoutError(
                        "turn interruption did not complete; the supervisor must recover descendant processes"
                    )

            # An interrupted response may still have unreported in-flight usage.
            usage.update(
                complete=done["status"] == "completed" and reports > 0,
                reports=reports,
                turn_status=done["status"],
                stop_reason=stopped,
            )
            atomic_json(run / "usage.json", usage)
            if done["status"] != "completed" or stopped:
                emit(
                    {"type": "turn.failed", "reason": stopped, "status": done["status"]}
                )
                return 75

            if final_text is None or not usage["complete"]:
                raise RuntimeError("final message or usage is missing")

            json.loads(final_text)
            (run / "result.json").write_text(final_text + "\n")
            emit({"type": "turn.completed"})
            return 0
        finally:
            selector.close()
            process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    raise SystemExit(execute(parser.parse_args().run))
