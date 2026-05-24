import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MCP_URL = os.environ.get("MCP_URL", "http://localhost:8000/mcp")
OUTPUT_NAME = os.environ.get("SMOKE_OUTPUT_NAME", "smoke-test")
REFERENCE_IMAGE = os.environ.get("SMOKE_REFERENCE_IMAGE", "")


async def main() -> None:
    async with streamable_http_client(MCP_URL) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools_resp = await session.list_tools()
            tool_names = [tool.name for tool in tools_resp.tools]
            print(f"Tools: {tool_names}")

            health = await session.call_tool("health", {})
            print("health:")
            print(json.dumps(health.model_dump(mode="json"), indent=2))

            modeling_tools = await session.call_tool("list_basic_modeling_tools", {})
            print("list_basic_modeling_tools:")
            print(json.dumps(modeling_tools.model_dump(mode="json"), indent=2))

            args = {
                "output_name": OUTPUT_NAME,
                "primitive": "cube",
                "views": 4,
                "reference_image": REFERENCE_IMAGE,
                "resolution": 512,
                "engine": "CYCLES",
                "samples": 96,
            }
            render = await session.call_tool("render_preview_views", args)
            print("render_preview_views:")
            render_dump = render.model_dump(mode="json")
            print(json.dumps(render_dump, indent=2))

            # Tool returns JSON text content; fail smoke test if server reports error.
            payload = None
            if render.content and getattr(render.content[0], "text", None):
                payload = json.loads(render.content[0].text)
            if not payload or not payload.get("ok"):
                raise RuntimeError(f"render_preview_views failed: {payload}")

            model_args = {
                "output_name": f"{OUTPUT_NAME}-ops",
                "operations": [
                    {
                        "action": "add_primitive",
                        "primitive": "cube",
                        "name": "Base",
                        "size": 1.5,
                        "location": [0, 0, 0.75],
                    },
                    {
                        "action": "inset_region",
                        "object": "Base",
                        "thickness": 0.15,
                        "depth": 0.0,
                    },
                    {
                        "action": "extrude_region",
                        "object": "Base",
                        "vector": [0.0, 0.0, 0.6],
                    },
                    {
                        "action": "add_modifier",
                        "object": "Base",
                        "modifier": "BEVEL",
                        "width": 0.04,
                        "segments": 3,
                        "apply": True,
                    },
                    {"action": "shade_smooth", "object": "Base"},
                ],
                "views": 4,
                "resolution": 512,
                "engine": "CYCLES",
                "samples": 64,
            }
            model_run = await session.call_tool("model_with_operations", model_args)
            print("model_with_operations:")
            model_dump = model_run.model_dump(mode="json")
            print(json.dumps(model_dump, indent=2))

            model_payload = None
            if model_run.content and getattr(model_run.content[0], "text", None):
                model_payload = json.loads(model_run.content[0].text)
            if not model_payload or not model_payload.get("ok"):
                raise RuntimeError(f"model_with_operations failed: {model_payload}")


if __name__ == "__main__":
    asyncio.run(main())
