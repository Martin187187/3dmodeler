import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

SERVER_NAME = "BlenderHeadlessMCP"
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))
BLENDER_BIN = os.environ.get("BLENDER_BIN", "blender")
DEFAULT_ENGINE = os.environ.get("BLENDER_ENGINE", "CYCLES")
DEFAULT_SAMPLES = int(os.environ.get("BLENDER_SAMPLES", "96"))
MCP_LOG_LEVEL = os.environ.get("MCP_LOG_LEVEL", "INFO")

ROOT_WORKSPACE = Path("/workspace")
INPUT_DIR = ROOT_WORKSPACE / "input"
REFS_DIR = ROOT_WORKSPACE / "refs"
OUTPUT_DIR = ROOT_WORKSPACE / "output"
BLENDER_SCRIPT = Path(__file__).parent / "blender_render_job.py"
BLENDER_MODELING_SCRIPT = Path(__file__).parent / "blender_modeling_job.py"
SAFE_NAME = re.compile(r"^[a-zA-Z0-9_.-]+$")

ALLOWED_ENGINES = {"CYCLES", "BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"}
ALLOWED_PRIMITIVES = {"cube", "sphere", "cylinder", "cone", "torus"}

mcp = FastMCP(
    SERVER_NAME,
    stateless_http=True,
    json_response=True,
    host=MCP_HOST,
    port=MCP_PORT,
    streamable_http_path="/mcp",
    log_level=MCP_LOG_LEVEL,
)


def _ensure_workspace_dirs() -> None:
    for folder in (INPUT_DIR, REFS_DIR, OUTPUT_DIR):
        folder.mkdir(parents=True, exist_ok=True)


def _safe_output_name(output_name: str) -> str:
    if not output_name or not SAFE_NAME.match(output_name):
        raise ValueError(
            "Invalid output_name. Use letters, digits, dot, underscore, or dash."
        )
    return output_name


def _resolve_reference_image(reference_image: str) -> str | None:
    if not reference_image:
        return None
    candidate = Path(reference_image)
    if candidate.is_absolute():
        if REFS_DIR not in candidate.parents:
            raise ValueError("Absolute reference_image must be inside /workspace/refs.")
        resolved = candidate
    else:
        resolved = (REFS_DIR / candidate).resolve()
        if REFS_DIR not in resolved.parents and resolved != REFS_DIR:
            raise ValueError("reference_image resolves outside /workspace/refs.")

    if not resolved.exists():
        raise FileNotFoundError(f"reference_image not found: {resolved}")
    return str(resolved)


def _run_blender_job(job_payload: dict) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(job_payload, tmp)
        tmp_path = tmp.name

    cmd = [
        BLENDER_BIN,
        "-b",
        "-P",
        str(job_payload.get("_script", BLENDER_SCRIPT)),
        "--",
        tmp_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    os.unlink(tmp_path)

    if result.returncode != 0:
        return {
            "ok": False,
            "return_code": result.returncode,
            "stderr_tail": result.stderr[-4000:],
            "stdout_tail": result.stdout[-4000:],
        }
    return {
        "ok": True,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }


def _resolve_output_dir(output_name: str) -> Path:
    safe_name = _safe_output_name(output_name)
    output_dir = (OUTPUT_DIR / safe_name).resolve()
    if OUTPUT_DIR not in output_dir.parents and output_dir != OUTPUT_DIR:
        raise ValueError("output path escapes /workspace/output")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _validate_render_settings(views: int, resolution: int, engine: str) -> str:
    if views < 1 or views > 36:
        raise ValueError("views must be between 1 and 36")
    if resolution < 128 or resolution > 4096:
        raise ValueError("resolution must be between 128 and 4096")

    normalized = engine.upper()
    if normalized not in ALLOWED_ENGINES:
        raise ValueError(f"engine must be one of: {sorted(ALLOWED_ENGINES)}")
    return normalized


@mcp.tool()
def health() -> dict:
    """Return server and Blender runtime health details."""
    _ensure_workspace_dirs()
    try:
        version_cmd = subprocess.run(
            [BLENDER_BIN, "--version"], capture_output=True, text=True
        )
        blender_version = version_cmd.stdout.splitlines()[0] if version_cmd.stdout else ""
        return {
            "ok": version_cmd.returncode == 0,
            "server": SERVER_NAME,
            "blender_bin": BLENDER_BIN,
            "blender_version": blender_version,
            "workspace": str(ROOT_WORKSPACE),
            "refs_dir": str(REFS_DIR),
            "output_dir": str(OUTPUT_DIR),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def render_preview_views(
    output_name: str,
    primitive: Literal["cube", "sphere", "cylinder", "cone", "torus"] = "cube",
    views: int = 4,
    reference_image: str = "",
    resolution: int = 768,
    engine: str = DEFAULT_ENGINE,
    samples: int = DEFAULT_SAMPLES,
) -> dict:
    """Create a simple 3D object, render multiple views, and save a .blend file."""
    _ensure_workspace_dirs()
    safe_name = _safe_output_name(output_name)
    primitive = primitive.lower()
    if primitive not in ALLOWED_PRIMITIVES:
        raise ValueError(f"Unsupported primitive: {primitive}")
    engine = _validate_render_settings(views=views, resolution=resolution, engine=engine)
    output_dir = _resolve_output_dir(safe_name)

    ref_path = _resolve_reference_image(reference_image) if reference_image else None

    blend_file = output_dir / f"{safe_name}.blend"
    payload = {
        "_script": str(BLENDER_SCRIPT),
        "primitive": primitive,
        "views": views,
        "resolution": resolution,
        "engine": engine,
        "samples": samples,
        "output_dir": str(output_dir),
        "blend_file": str(blend_file),
        "reference_image": ref_path,
    }

    render_result = _run_blender_job(payload)
    if not render_result["ok"]:
        return {"ok": False, "job": payload, "error": render_result}

    renders = [str(output_dir / f"view_{idx:02d}.png") for idx in range(views)]
    return {
        "ok": True,
        "primitive": primitive,
        "views": views,
        "engine": engine,
        "samples": samples,
        "blend_file": str(blend_file),
        "renders": renders,
        "output_dir": str(output_dir),
        "blender_log": render_result,
    }


@mcp.tool()
def list_basic_modeling_tools() -> dict:
    """Return supported basic modeling actions for model_with_operations()."""
    return {
        "ok": True,
        "tool_name": "model_with_operations",
        "notes": [
            "MCP exposes tools, but the LLM still needs explicit Blender actions.",
            "Use these action schemas in the operations list.",
            "Each call is stateless: it creates a fresh scene and writes outputs.",
        ],
        "actions": [
            {
                "action": "add_primitive",
                "required": ["primitive"],
                "fields": {
                    "primitive": ["cube", "sphere", "cylinder", "cone", "torus"],
                    "name": "string",
                    "location": "[x,y,z]",
                    "rotation": "[rx,ry,rz] radians",
                    "scale": "[sx,sy,sz]",
                    "size": "number (cube/cone)",
                    "radius": "number (sphere/cylinder/cone/torus)",
                    "depth": "number (cylinder/cone)",
                    "major_radius": "number (torus)",
                    "minor_radius": "number (torus)",
                },
            },
            {
                "action": "transform_object",
                "required": ["object"],
                "fields": {
                    "object": "object name",
                    "location": "[x,y,z]",
                    "rotation": "[rx,ry,rz] radians",
                    "scale": "[sx,sy,sz]",
                },
            },
            {
                "action": "extrude_region",
                "required": ["object"],
                "fields": {
                    "object": "object name",
                    "vector": "[x,y,z] translation for extruded region",
                },
            },
            {
                "action": "inset_region",
                "required": ["object"],
                "fields": {
                    "object": "object name",
                    "thickness": "number",
                    "depth": "number",
                },
            },
            {
                "action": "add_modifier",
                "required": ["object", "modifier"],
                "fields": {
                    "object": "object name",
                    "modifier": [
                        "BEVEL",
                        "SUBSURF",
                        "SOLIDIFY",
                        "CURVE",
                        "MIRROR",
                        "SIMPLE_DEFORM",
                    ],
                    "width": "number (BEVEL)",
                    "segments": "int (BEVEL)",
                    "levels": "int (SUBSURF)",
                    "render_levels": "int (SUBSURF)",
                    "thickness": "number (SOLIDIFY)",
                    "curve_object": "curve name (CURVE)",
                    "deform_axis": "axis enum, e.g. POS_Y or Z",
                    "use_axis": "[x,y,z] bool list (MIRROR)",
                    "deform_method": "BEND/TWIST/TAPER/STRETCH (SIMPLE_DEFORM)",
                    "angle": "radians (SIMPLE_DEFORM bend/twist)",
                    "factor": "number (SIMPLE_DEFORM taper/stretch)",
                    "origin_object": "object name (SIMPLE_DEFORM optional)",
                    "apply": "bool (default true)",
                },
            },
            {
                "action": "boolean_difference",
                "required": ["object"],
                "fields": {
                    "object": "target object",
                    "cutter_primitive": ["cube", "sphere", "cylinder", "cone", "torus"],
                    "cutter_name": "string",
                    "cutter_location": "[x,y,z]",
                    "cutter_rotation": "[rx,ry,rz] radians",
                    "cutter_scale": "[sx,sy,sz]",
                    "apply": "bool (default true)",
                    "keep_cutter": "bool (default false)",
                },
            },
            {
                "action": "shade_smooth",
                "required": ["object"],
                "fields": {"object": "object name"},
            },
            {
                "action": "duplicate_object",
                "required": ["object"],
                "fields": {
                    "object": "source object name",
                    "name": "new object name",
                    "location_offset": "[x,y,z]",
                    "rotation_offset": "[rx,ry,rz] radians",
                    "scale_multiplier": "[sx,sy,sz]",
                },
            },
            {
                "action": "delete_object",
                "required": ["object"],
                "fields": {"object": "object name"},
            },
            {
                "action": "set_material",
                "required": ["object"],
                "fields": {
                    "object": "mesh/curve object name",
                    "material_name": "string",
                    "base_color": "[r,g,b] or [r,g,b,a]",
                    "roughness": "number",
                    "metallic": "number",
                    "specular": "number",
                    "alpha": "number",
                    "emission_color": "[r,g,b] or [r,g,b,a]",
                    "emission_strength": "number",
                },
            },
            {
                "action": "add_curve_bezier",
                "required": ["name", "points"],
                "fields": {
                    "name": "curve object name",
                    "points": "[[x,y,z], ...] at least 2 points",
                    "bevel_depth": "number, tube radius",
                    "bevel_resolution": "int",
                    "resolution_u": "int",
                    "fill_mode": "FULL/HALF/BACK/FRONT",
                    "use_fill_caps": "bool",
                    "cyclic": "bool",
                    "location": "[x,y,z]",
                    "rotation": "[rx,ry,rz]",
                    "scale": "[sx,sy,sz]",
                    "as_mesh": "bool",
                },
            },
            {
                "action": "parent_object",
                "required": ["child", "parent"],
                "fields": {
                    "child": "child object name",
                    "parent": "parent object name",
                    "keep_transform": "bool (default true)",
                },
            },
        ],
    }


@mcp.tool()
def model_with_operations(
    output_name: str,
    operations: list[dict[str, Any]],
    views: int = 4,
    resolution: int = 768,
    engine: str = DEFAULT_ENGINE,
    samples: int = DEFAULT_SAMPLES,
    reference_image: str = "",
) -> dict:
    """Build a model from modeling operations, render multi-view previews, and save .blend."""
    _ensure_workspace_dirs()
    if not operations:
        raise ValueError("operations must contain at least one action")
    if len(operations) > 256:
        raise ValueError("operations is too large; max is 256 actions")

    safe_name = _safe_output_name(output_name)
    engine = _validate_render_settings(views=views, resolution=resolution, engine=engine)
    output_dir = _resolve_output_dir(safe_name)
    ref_path = _resolve_reference_image(reference_image) if reference_image else None

    blend_file = output_dir / f"{safe_name}.blend"
    payload = {
        "_script": str(BLENDER_MODELING_SCRIPT),
        "operations": operations,
        "views": views,
        "resolution": resolution,
        "engine": engine,
        "samples": samples,
        "output_dir": str(output_dir),
        "blend_file": str(blend_file),
        "reference_image": ref_path,
    }
    result = _run_blender_job(payload)
    if not result["ok"]:
        return {"ok": False, "job": payload, "error": result}

    renders = [str(output_dir / f"view_{idx:02d}.png") for idx in range(views)]
    return {
        "ok": True,
        "views": views,
        "engine": engine,
        "samples": samples,
        "operation_count": len(operations),
        "blend_file": str(blend_file),
        "renders": renders,
        "output_dir": str(output_dir),
        "blender_log": result,
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
