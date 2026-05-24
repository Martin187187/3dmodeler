import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MCP_URL = os.environ.get("MCP_URL", "http://localhost:8000/mcp")
PLAN_FILE = os.environ.get("PLAN_FILE", "/workspace/input/plans/plant-example-v1.json")
RUNS_DIR = os.environ.get("PLAN_RUNS_DIR", "/workspace/output/plan-runs")


def _load_plan(path: str) -> dict[str, Any]:
    plan_path = Path(path)
    if not plan_path.exists():
        raise FileNotFoundError(f"Plan file not found: {plan_path}")
    with plan_path.open("r", encoding="utf-8") as handle:
        plan = json.load(handle)
    if not isinstance(plan, dict):
        raise ValueError("Plan must be a JSON object")
    calls = plan.get("calls")
    if not isinstance(calls, list) or not calls:
        raise ValueError("Plan must include non-empty 'calls' list")
    if len(calls) > 256:
        raise ValueError("Plan has too many calls (max 256)")

    for idx, call in enumerate(calls):
        if not isinstance(call, dict):
            raise ValueError(f"Call #{idx} must be an object")
        tool = call.get("tool")
        args = call.get("args", {})
        if not isinstance(tool, str) or not tool:
            raise ValueError(f"Call #{idx} missing tool name")
        if not isinstance(args, dict):
            raise ValueError(f"Call #{idx} args must be an object")
    return plan


def _extract_text_json(response_dump: dict[str, Any]) -> dict[str, Any] | None:
    content = response_dump.get("content", [])
    if not content:
        return None
    first = content[0]
    text = first.get("text") if isinstance(first, dict) else None
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


async def _execute_plan(plan: dict[str, Any]) -> dict[str, Any]:
    calls = plan["calls"]
    plan_id = str(plan.get("plan_id", "plan"))
    started_at = datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []

    async with streamable_http_client(MCP_URL) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            for index, call in enumerate(calls):
                tool = call["tool"]
                args = call.get("args", {})
                response = await session.call_tool(tool, args)
                response_dump = response.model_dump(mode="json")
                parsed_json = _extract_text_json(response_dump)

                step = {
                    "index": index,
                    "tool": tool,
                    "args": args,
                    "response": response_dump,
                    "parsed_json": parsed_json,
                }
                results.append(step)

                if parsed_json is not None and parsed_json.get("ok") is False:
                    raise RuntimeError(
                        f"Tool '{tool}' returned ok=false at step {index}: {parsed_json}"
                    )

    finished_at = datetime.now(timezone.utc)
    return {
        "ok": True,
        "plan_id": plan_id,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "call_count": len(calls),
        "results": results,
        "last_parsed_json": results[-1].get("parsed_json") if results else None,
    }


def _persist_run(run_report: dict[str, Any], plan: dict[str, Any], runs_dir: str) -> dict[str, str]:
    root = Path(runs_dir)
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plan_id = str(run_report.get("plan_id", "plan"))
    safe_id = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in plan_id)
    base = root / f"{safe_id}-{timestamp}"

    report_path = base.with_suffix(".run.json")
    plan_path = base.with_suffix(".plan.json")

    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(run_report, handle, indent=2)
    with plan_path.open("w", encoding="utf-8") as handle:
        json.dump(plan, handle, indent=2)

    return {"report_path": str(report_path), "plan_snapshot_path": str(plan_path)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute an MCP tool plan JSON.")
    parser.add_argument(
        "--plan-file",
        default=PLAN_FILE,
        help="Path to plan JSON file (default from PLAN_FILE env).",
    )
    parser.add_argument(
        "--runs-dir",
        default=RUNS_DIR,
        help="Directory for persisted run reports (default from PLAN_RUNS_DIR env).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plan = _load_plan(args.plan_file)
    print("loaded_plan:")
    print(json.dumps(plan, indent=2))

    report = asyncio.run(_execute_plan(plan))
    persisted = _persist_run(report, plan, args.runs_dir)

    print("run_report_summary:")
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "plan_id": report["plan_id"],
                "call_count": report["call_count"],
                "last_parsed_json": report.get("last_parsed_json"),
                "persisted": persisted,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
