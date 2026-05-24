import json
import math
import os
import sys

import bpy
from mathutils import Vector


def _parse_job_file_from_argv() -> str:
    if "--" not in sys.argv:
        raise RuntimeError("Expected Blender args separator '--'")
    idx = sys.argv.index("--")
    if idx + 1 >= len(sys.argv):
        raise RuntimeError("Expected a JSON job file path after '--'")
    return sys.argv[idx + 1]


def _look_at(obj, target: Vector) -> None:
    direction = target - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _add_primitive(primitive: str):
    if primitive == "cube":
        bpy.ops.mesh.primitive_cube_add(size=1.6, location=(0, 0, 0.8))
    elif primitive == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, location=(0, 0, 1.0))
    elif primitive == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(radius=0.9, depth=2.0, location=(0, 0, 1.0))
    elif primitive == "cone":
        bpy.ops.mesh.primitive_cone_add(radius1=1.0, depth=2.0, location=(0, 0, 1.0))
    elif primitive == "torus":
        bpy.ops.mesh.primitive_torus_add(
            major_radius=1.0, minor_radius=0.3, location=(0, 0, 1.0)
        )
    else:
        raise ValueError(f"Unsupported primitive: {primitive}")

    obj = bpy.context.object
    mat = bpy.data.materials.new(name="PreviewMaterial")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.24, 0.54, 0.86, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.36
    obj.data.materials.append(mat)
    return obj


def _add_reference_image(reference_image: str) -> None:
    if not reference_image or not os.path.exists(reference_image):
        return
    try:
        image = bpy.data.images.load(reference_image)
        bpy.ops.object.empty_add(type="IMAGE", location=(0.0, -2.4, 1.2))
        empty = bpy.context.object
        empty.data = image
        empty.empty_display_size = 2.2
        empty.empty_image_side = "DOUBLE_SIDED"
    except Exception as exc:
        print(f"Reference image load warning: {exc}", file=sys.stderr)


def _configure_render(scene, resolution: int, engine: str, samples: int) -> None:
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False

    if engine in {"CYCLES", "BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"}:
        scene.render.engine = engine
    else:
        scene.render.engine = "CYCLES"

    if scene.render.engine == "CYCLES":
        scene.cycles.device = "CPU"
        scene.cycles.samples = samples
        if hasattr(scene.cycles, "use_adaptive_sampling"):
            scene.cycles.use_adaptive_sampling = True
        if hasattr(scene.cycles, "adaptive_threshold"):
            scene.cycles.adaptive_threshold = 0.02
        if hasattr(scene.cycles, "adaptive_min_samples"):
            scene.cycles.adaptive_min_samples = min(32, max(8, samples // 2))
        # Debian Blender builds often omit OpenImageDenoiser; disable denoising
        # explicitly so headless CPU renders do not fail at runtime.
        if hasattr(scene.cycles, "use_denoising"):
            scene.cycles.use_denoising = False
        for view_layer in scene.view_layers:
            cycles_settings = getattr(view_layer, "cycles", None)
            if cycles_settings is not None and hasattr(cycles_settings, "use_denoising"):
                cycles_settings.use_denoising = False
    else:
        eevee = getattr(scene, "eevee", None)
        if eevee is not None:
            if hasattr(eevee, "taa_render_samples"):
                eevee.taa_render_samples = max(32, samples)
            if hasattr(eevee, "use_gtao"):
                eevee.use_gtao = True
            if hasattr(eevee, "use_bloom"):
                eevee.use_bloom = True


def _configure_world(scene) -> None:
    world = bpy.data.worlds.new("RenderWorld")
    world.use_nodes = True
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    nodes.clear()

    output = nodes.new(type="ShaderNodeOutputWorld")
    background = nodes.new(type="ShaderNodeBackground")
    sky = nodes.new(type="ShaderNodeTexSky")

    sky.sky_type = "HOSEK_WILKIE"
    sky.sun_elevation = math.radians(35.0)
    sky.sun_rotation = math.radians(35.0)
    background.inputs["Strength"].default_value = 0.8

    links.new(sky.outputs["Color"], background.inputs["Color"])
    links.new(background.outputs["Background"], output.inputs["Surface"])
    scene.world = world


def _setup_scene(job: dict):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    _configure_render(
        scene,
        resolution=int(job.get("resolution", 768)),
        engine=str(job.get("engine", "CYCLES")),
        samples=int(job.get("samples", 32)),
    )
    _configure_world(scene)

    bpy.ops.object.light_add(type="SUN", location=(5, -5, 7))
    key = bpy.context.object
    key.data.energy = 3.2
    key.rotation_euler = (math.radians(45), 0.0, math.radians(35))

    bpy.ops.object.light_add(type="AREA", location=(-2.5, -2.8, 2.8))
    fill = bpy.context.object
    fill.data.energy = 800
    fill.data.shape = "RECTANGLE"
    fill.data.size = 1.6
    fill.data.size_y = 1.0
    fill.rotation_euler = (math.radians(55), 0.0, math.radians(-30))

    bpy.ops.object.light_add(type="AREA", location=(2.8, 3.0, 2.6))
    rim = bpy.context.object
    rim.data.energy = 260
    rim.data.shape = "DISK"
    rim.data.size = 1.2
    rim.rotation_euler = (math.radians(70), 0.0, math.radians(145))

    bpy.ops.mesh.primitive_plane_add(size=20.0, location=(0, 0, 0))
    floor = bpy.context.object
    floor_mat = bpy.data.materials.new(name="FloorMaterial")
    floor_mat.use_nodes = True
    floor_bsdf = floor_mat.node_tree.nodes.get("Principled BSDF")
    if floor_bsdf:
        floor_bsdf.inputs["Base Color"].default_value = (0.92, 0.92, 0.92, 1.0)
        floor_bsdf.inputs["Roughness"].default_value = 0.6
    floor.data.materials.append(floor_mat)

    target = _add_primitive(str(job["primitive"]))
    _add_reference_image(str(job.get("reference_image") or ""))

    bpy.ops.object.camera_add(location=(3.0, -3.0, 2.0))
    camera = bpy.context.object
    camera.data.lens = 55
    camera.data.clip_start = 0.01
    camera.data.clip_end = 200
    scene.camera = camera
    _look_at(camera, target.location.copy())

    return scene, camera, target


def _render_views(scene, camera, target, output_dir: str, views: int):
    radius = 4.2
    target_pos = target.location.copy()
    for idx in range(views):
        angle = (2 * math.pi * idx) / views
        camera.location = Vector(
            (
                radius * math.cos(angle),
                radius * math.sin(angle),
                2.2,
            )
        )
        _look_at(camera, target_pos)
        render_path = os.path.join(output_dir, f"view_{idx:02d}.png")
        scene.render.filepath = render_path
        bpy.ops.render.render(write_still=True)


def main():
    job_file = _parse_job_file_from_argv()
    with open(job_file, "r", encoding="utf-8") as handle:
        job = json.load(handle)

    output_dir = job["output_dir"]
    os.makedirs(output_dir, exist_ok=True)

    scene, camera, target = _setup_scene(job)
    _render_views(scene, camera, target, output_dir, int(job["views"]))

    blend_file = job["blend_file"]
    bpy.ops.wm.save_as_mainfile(filepath=blend_file)
    print(
        json.dumps(
            {
                "ok": True,
                "output_dir": output_dir,
                "blend_file": blend_file,
            }
        )
    )


if __name__ == "__main__":
    main()
