# Headless Blender MCP Starter

This repository now includes a Dockerized headless Blender MCP server that can:

- expose MCP tools over `streamable-http`
- run Blender in background mode
- generate multi-view renders of a simple 3D object
- execute basic modeling operations (primitive creation, extrude/inset, modifiers, booleans, transforms)
- save the resulting `.blend` file
- optionally load a reference image from `data/refs/`

## Structure

- `docker-compose.yml`: MCP service + smoke test service
- `mcp-blender-server/app/server.py`: MCP tool server
- `mcp-blender-server/app/blender_render_job.py`: Blender Python render job
- `mcp-blender-server/scripts/smoke_test.py`: MCP client smoke test
- `data/input/`: optional inputs for future workflows
- `data/refs/`: reference images used by tool calls
- `data/output/`: generated renders and `.blend` files

## Prerequisites

- Docker Desktop with Compose support

## Start MCP Server

```bash
docker compose up --build blender-mcp
```

MCP endpoint:

- `http://localhost:8000/mcp`

## Run Smoke Test

In another terminal:

```bash
docker compose run --rm mcp-smoke-test
```

The smoke test will:

1. connect to MCP
2. list tools
3. call `health`
4. call `render_preview_views`

Outputs will be created in:

- `data/output/smoke-test/`

Expected files:

- `data/output/smoke-test/smoke-test.blend`
- `data/output/smoke-test/view_00.png`
- `data/output/smoke-test/view_01.png`
- `data/output/smoke-test/view_02.png`
- `data/output/smoke-test/view_03.png`

## Use a Reference Image

Place an image in `data/refs/`, for example:

- `data/refs/chair.png`

Then run smoke test with env override:

```bash
docker compose run --rm -e SMOKE_REFERENCE_IMAGE=chair.png mcp-smoke-test
```

## Execute Any JSON Plan (Persistence Layer)

Use a plan file under `data/input/plans/`:

- example: `data/input/plans/plant-example-v1.json`

Run the generic executor:

```bash
docker compose run --rm mcp-plan-runner
```

Override the plan file:

```bash
docker compose run --rm -e PLAN_FILE=/workspace/input/plans/your-plan.json mcp-plan-runner
```

Each run persists:

- run report: `data/output/plan-runs/<plan-id>-<timestamp>.run.json`
- plan snapshot: `data/output/plan-runs/<plan-id>-<timestamp>.plan.json`

This is the recommended pattern for arbitrary LLM-produced JSON plans.

## MCP Tools

- `health()`
- `render_preview_views(output_name, primitive, views, reference_image, resolution, engine, samples)`
- `list_basic_modeling_tools()`
- `model_with_operations(output_name, operations, views, resolution, engine, samples, reference_image)`

Recent modeling actions added for better reference matching:

- `set_material`
- `add_curve_bezier`
- `parent_object`
- `add_modifier` extended with `CURVE`, `MIRROR`, `SIMPLE_DEFORM`
- `duplicate_object` extended with rotation/scale offsets

## Important: MCP vs LLM

- MCP is the protocol/transport for tool calling.
- It does **not** automatically provide Blender modeling capabilities.
- You still need to expose explicit modeling tools (now provided via `model_with_operations`).

## Example: Basic Modeling Ops

`model_with_operations` takes an ordered `operations` array. Example:

```json
{
  "output_name": "blockout-v1",
  "operations": [
    {"action": "add_primitive", "primitive": "cube", "name": "Base", "size": 1.5, "location": [0, 0, 0.75]},
    {"action": "inset_region", "object": "Base", "thickness": 0.15, "depth": 0.0},
    {"action": "extrude_region", "object": "Base", "vector": [0, 0, 0.6]},
    {"action": "add_modifier", "object": "Base", "modifier": "BEVEL", "width": 0.04, "segments": 3, "apply": true},
    {"action": "shade_smooth", "object": "Base"}
  ],
  "views": 6,
  "resolution": 768,
  "engine": "CYCLES",
  "samples": 96
}
```

## Notes for LLM Integration

This server is MCP-native, so any MCP-capable LLM orchestrator can call it directly.
For your next phase, you can add a separate agent service that:

1. accepts text prompt + reference image
2. plans tool calls
3. invokes `render_preview_views`
4. returns output paths and artifacts
