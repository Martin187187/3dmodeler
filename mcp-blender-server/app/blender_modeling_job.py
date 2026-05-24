import json
import math
import os
import sys

import bmesh
import bpy
from mathutils import Vector

SUPPORTED_PRIMITIVES = {"cube", "sphere", "cylinder", "cone", "torus"}
SUPPORTED_MODIFIERS = {"BEVEL", "SUBSURF", "SOLIDIFY", "CURVE", "MIRROR", "SIMPLE_DEFORM"}


def _parse_job_file_from_argv() -> str:
    if "--" not in sys.argv:
        raise RuntimeError("Expected Blender args separator '--'")
    idx = sys.argv.index("--")
    if idx + 1 >= len(sys.argv):
        raise RuntimeError("Expected a JSON job file path after '--'")
    return sys.argv[idx + 1]


def _vec3(value, default=(0.0, 0.0, 0.0)) -> Vector:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return Vector((float(value[0]), float(value[1]), float(value[2])))
        except Exception:
            return Vector(default)
    return Vector(default)


def _rgba(value, default=(0.8, 0.8, 0.8, 1.0)):
    if isinstance(value, (list, tuple)):
        if len(value) == 3:
            return (
                float(value[0]),
                float(value[1]),
                float(value[2]),
                1.0,
            )
        if len(value) == 4:
            return (
                float(value[0]),
                float(value[1]),
                float(value[2]),
                float(value[3]),
            )
    return default


def _look_at(obj, target: Vector) -> None:
    direction = target - obj.location
    if direction.length == 0:
        direction = Vector((0.0, 0.0, -1.0))
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _set_active(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _get_object(name: str, expected_types: set[str] | None = None):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"Object not found: {name}")
    if expected_types and obj.type not in expected_types:
        raise ValueError(f"Object '{name}' must be one of {sorted(expected_types)}")
    return obj


def _get_mesh_object(name: str):
    return _get_object(name, {"MESH"})


def _create_primitive(op: dict):
    primitive = str(op.get("primitive", "cube")).lower()
    if primitive not in SUPPORTED_PRIMITIVES:
        raise ValueError(f"Unsupported primitive: {primitive}")

    location = _vec3(op.get("location"), (0.0, 0.0, 0.0))
    rotation = _vec3(op.get("rotation"), (0.0, 0.0, 0.0))
    scale = _vec3(op.get("scale"), (1.0, 1.0, 1.0))

    size = float(op.get("size", 1.0))
    radius = float(op.get("radius", 1.0))
    depth = float(op.get("depth", 2.0))
    major_radius = float(op.get("major_radius", 1.0))
    minor_radius = float(op.get("minor_radius", 0.25))

    if primitive == "cube":
        bpy.ops.mesh.primitive_cube_add(size=size, location=location, rotation=rotation)
    elif primitive == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(
            radius=radius, location=location, rotation=rotation
        )
    elif primitive == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(
            radius=radius, depth=depth, location=location, rotation=rotation
        )
    elif primitive == "cone":
        bpy.ops.mesh.primitive_cone_add(
            radius1=radius, depth=depth, location=location, rotation=rotation
        )
    elif primitive == "torus":
        bpy.ops.mesh.primitive_torus_add(
            major_radius=major_radius,
            minor_radius=minor_radius,
            location=location,
            rotation=rotation,
        )

    obj = bpy.context.object
    obj.scale = scale
    if op.get("name"):
        obj.name = str(op["name"])
    return obj


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
        if hasattr(scene.cycles, "use_denoising"):
            scene.cycles.use_denoising = False
        for view_layer in scene.view_layers:
            cycles_settings = getattr(view_layer, "cycles", None)
            if cycles_settings is not None and hasattr(cycles_settings, "use_denoising"):
                cycles_settings.use_denoising = False


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


def _add_reference_image(reference_image: str) -> None:
    if not reference_image or not os.path.exists(reference_image):
        return
    image = bpy.data.images.load(reference_image)
    bpy.ops.object.empty_add(type="IMAGE", location=(0.0, -3.0, 1.4))
    empty = bpy.context.object
    empty.data = image
    empty.empty_display_size = 2.6
    empty.empty_image_side = "DOUBLE_SIDED"


def _edit_mesh(obj, edit_fn):
    _set_active(obj)
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    edit_fn(bm)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def _action_transform(op: dict):
    obj = _get_mesh_object(str(op["object"]))
    if "location" in op:
        obj.location = _vec3(op["location"], tuple(obj.location))
    if "rotation" in op:
        obj.rotation_euler = _vec3(op["rotation"], tuple(obj.rotation_euler))
    if "scale" in op:
        obj.scale = _vec3(op["scale"], tuple(obj.scale))


def _action_extrude_region(op: dict):
    obj = _get_mesh_object(str(op["object"]))
    vec = _vec3(op.get("vector"), (0.0, 0.0, 0.25))

    def apply_extrude(bm):
        geom = bm.faces[:] + bm.edges[:] + bm.verts[:]
        result = bmesh.ops.extrude_face_region(bm, geom=geom)
        verts = [g for g in result["geom"] if isinstance(g, bmesh.types.BMVert)]
        if verts:
            bmesh.ops.translate(bm, verts=verts, vec=vec)

    _edit_mesh(obj, apply_extrude)


def _action_inset_region(op: dict):
    obj = _get_mesh_object(str(op["object"]))
    thickness = float(op.get("thickness", 0.08))
    depth = float(op.get("depth", 0.0))

    def apply_inset(bm):
        faces = bm.faces[:]
        if faces:
            bmesh.ops.inset_region(
                bm,
                faces=faces,
                thickness=thickness,
                depth=depth,
                use_even_offset=True,
            )

    _edit_mesh(obj, apply_inset)


def _apply_modifier(obj, mod_name: str):
    _set_active(obj)
    bpy.ops.object.modifier_apply(modifier=mod_name)


def _action_add_modifier(op: dict):
    obj = _get_mesh_object(str(op["object"]))
    modifier = str(op["modifier"]).upper()
    if modifier not in SUPPORTED_MODIFIERS:
        raise ValueError(f"Unsupported modifier: {modifier}")

    mod = obj.modifiers.new(name=f"{modifier}_Mod", type=modifier)
    if modifier == "BEVEL":
        mod.width = float(op.get("width", 0.03))
        mod.segments = int(op.get("segments", 3))
    elif modifier == "SUBSURF":
        mod.levels = int(op.get("levels", 2))
        mod.render_levels = int(op.get("render_levels", mod.levels))
    elif modifier == "SOLIDIFY":
        mod.thickness = float(op.get("thickness", 0.08))
    elif modifier == "CURVE":
        curve_name = str(op.get("curve_object", "")).strip()
        if not curve_name:
            raise ValueError("CURVE modifier requires 'curve_object'")
        curve_obj = _get_object(curve_name, {"CURVE"})
        mod.object = curve_obj
        mod.deform_axis = str(op.get("deform_axis", "POS_Y")).upper()
    elif modifier == "MIRROR":
        use_axis = op.get("use_axis", [True, False, False])
        if not isinstance(use_axis, (list, tuple)) or len(use_axis) != 3:
            raise ValueError("MIRROR modifier expects use_axis as [x,y,z] bool list")
        mod.use_axis[0] = bool(use_axis[0])
        mod.use_axis[1] = bool(use_axis[1])
        mod.use_axis[2] = bool(use_axis[2])
        mod.use_bisect_axis[0] = bool(op.get("bisect_x", False))
        mod.use_bisect_axis[1] = bool(op.get("bisect_y", False))
        mod.use_bisect_axis[2] = bool(op.get("bisect_z", False))
    elif modifier == "SIMPLE_DEFORM":
        mod.deform_method = str(op.get("deform_method", "BEND")).upper()
        mod.deform_axis = str(op.get("deform_axis", "Z")).upper()
        if "angle" in op:
            mod.angle = float(op.get("angle", 0.0))
        elif "factor" in op:
            mod.factor = float(op.get("factor", 0.0))
        origin_name = str(op.get("origin_object", "")).strip()
        if origin_name:
            mod.origin = _get_object(origin_name)

    if bool(op.get("apply", True)):
        _apply_modifier(obj, mod.name)


def _action_boolean_difference(op: dict):
    target = _get_mesh_object(str(op["object"]))
    cutter_op = {
        "primitive": op.get("cutter_primitive", "cube"),
        "name": op.get("cutter_name", f"{target.name}_Cutter"),
        "location": op.get("cutter_location", (0.0, 0.0, 0.0)),
        "rotation": op.get("cutter_rotation", (0.0, 0.0, 0.0)),
        "scale": op.get("cutter_scale", (0.5, 0.5, 0.5)),
    }
    cutter = _create_primitive(cutter_op)
    bool_mod = target.modifiers.new(name="BoolDiff", type="BOOLEAN")
    bool_mod.operation = "DIFFERENCE"
    bool_mod.solver = "EXACT"
    bool_mod.object = cutter

    if bool(op.get("apply", True)):
        _apply_modifier(target, bool_mod.name)
    if not bool(op.get("keep_cutter", False)):
        bpy.data.objects.remove(cutter, do_unlink=True)


def _action_shade_smooth(op: dict):
    obj = _get_mesh_object(str(op["object"]))
    for poly in obj.data.polygons:
        poly.use_smooth = True
    if hasattr(obj.data, "use_auto_smooth"):
        obj.data.use_auto_smooth = True
    if hasattr(obj.data, "auto_smooth_angle"):
        obj.data.auto_smooth_angle = math.radians(30.0)


def _action_duplicate(op: dict):
    source = _get_object(str(op["object"]))
    copy_obj = source.copy()
    copy_obj.data = source.data.copy()
    copy_obj.name = str(op.get("name", f"{source.name}_Copy"))
    copy_obj.location = source.location + _vec3(op.get("location_offset"), (1.0, 0.0, 0.0))
    rot_off = _vec3(op.get("rotation_offset"), (0.0, 0.0, 0.0))
    copy_obj.rotation_euler = (
        source.rotation_euler.x + rot_off.x,
        source.rotation_euler.y + rot_off.y,
        source.rotation_euler.z + rot_off.z,
    )
    scale_mul = _vec3(op.get("scale_multiplier"), (1.0, 1.0, 1.0))
    copy_obj.scale = Vector(
        (
            source.scale.x * scale_mul.x,
            source.scale.y * scale_mul.y,
            source.scale.z * scale_mul.z,
        )
    )
    bpy.context.collection.objects.link(copy_obj)


def _action_delete(op: dict):
    obj = bpy.data.objects.get(str(op["object"]))
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)


def _action_set_material(op: dict):
    obj = _get_object(str(op["object"]))
    mat_name = str(op.get("material_name", f"{obj.name}_Mat"))
    material = bpy.data.materials.get(mat_name)
    if material is None:
        material = bpy.data.materials.new(name=mat_name)
    material.use_nodes = True

    nodes = material.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf is None:
        bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")

    bsdf.inputs["Base Color"].default_value = _rgba(
        op.get("base_color"), (0.8, 0.8, 0.8, 1.0)
    )
    if "roughness" in op:
        bsdf.inputs["Roughness"].default_value = float(op["roughness"])
    if "metallic" in op:
        bsdf.inputs["Metallic"].default_value = float(op["metallic"])
    if "specular" in op:
        bsdf.inputs["Specular"].default_value = float(op["specular"])
    if "alpha" in op:
        bsdf.inputs["Alpha"].default_value = float(op["alpha"])
        material.blend_method = "BLEND"

    emission_color = op.get("emission_color")
    if emission_color is not None:
        bsdf.inputs["Emission"].default_value = _rgba(emission_color, (0.0, 0.0, 0.0, 1.0))
    if "emission_strength" in op:
        bsdf.inputs["Emission Strength"].default_value = float(op["emission_strength"])

    if obj.type in {"MESH", "CURVE", "SURFACE", "FONT"} and hasattr(obj.data, "materials"):
        if len(obj.data.materials) == 0:
            obj.data.materials.append(material)
        else:
            obj.data.materials[0] = material


def _action_add_curve_bezier(op: dict):
    points = op.get("points", [])
    if not isinstance(points, list) or len(points) < 2:
        raise ValueError("add_curve_bezier expects points with at least 2 coordinates")

    curve_name = str(op.get("name", "BezierCurve"))
    curve_data = bpy.data.curves.new(name=f"{curve_name}Data", type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.resolution_u = int(op.get("resolution_u", 24))
    curve_data.fill_mode = str(op.get("fill_mode", "FULL")).upper()
    curve_data.bevel_depth = float(op.get("bevel_depth", 0.0))
    curve_data.bevel_resolution = int(op.get("bevel_resolution", 4))
    if "use_fill_caps" in op:
        curve_data.use_fill_caps = bool(op.get("use_fill_caps", False))

    spline = curve_data.splines.new("BEZIER")
    spline.bezier_points.add(len(points) - 1)
    for bp, point in zip(spline.bezier_points, points):
        co = _vec3(point, (0.0, 0.0, 0.0))
        bp.co = co
        bp.handle_left_type = "AUTO"
        bp.handle_right_type = "AUTO"

    spline.use_cyclic_u = bool(op.get("cyclic", False))

    curve_obj = bpy.data.objects.new(curve_name, curve_data)
    bpy.context.collection.objects.link(curve_obj)
    curve_obj.location = _vec3(op.get("location"), (0.0, 0.0, 0.0))
    curve_obj.rotation_euler = _vec3(op.get("rotation"), (0.0, 0.0, 0.0))
    curve_obj.scale = _vec3(op.get("scale"), (1.0, 1.0, 1.0))

    if bool(op.get("as_mesh", False)):
        _set_active(curve_obj)
        bpy.ops.object.convert(target="MESH")


def _action_parent_object(op: dict):
    child = _get_object(str(op["child"]))
    parent = _get_object(str(op["parent"]))
    keep = bool(op.get("keep_transform", True))
    if keep:
        matrix_world = child.matrix_world.copy()
        child.parent = parent
        child.matrix_world = matrix_world
    else:
        child.parent = parent


def _execute_operations(operations: list[dict]):
    if not operations:
        raise ValueError("No operations provided")

    for idx, op in enumerate(operations):
        action = str(op.get("action", "")).strip().lower()
        if not action:
            raise ValueError(f"Operation #{idx} is missing 'action'")

        if action == "add_primitive":
            _create_primitive(op)
        elif action == "transform_object":
            _action_transform(op)
        elif action == "extrude_region":
            _action_extrude_region(op)
        elif action == "inset_region":
            _action_inset_region(op)
        elif action == "add_modifier":
            _action_add_modifier(op)
        elif action == "boolean_difference":
            _action_boolean_difference(op)
        elif action == "shade_smooth":
            _action_shade_smooth(op)
        elif action == "duplicate_object":
            _action_duplicate(op)
        elif action == "delete_object":
            _action_delete(op)
        elif action == "set_material":
            _action_set_material(op)
        elif action == "add_curve_bezier":
            _action_add_curve_bezier(op)
        elif action == "parent_object":
            _action_parent_object(op)
        else:
            raise ValueError(f"Unsupported action '{action}' at operation #{idx}")


def _add_default_lights():
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


def _add_floor():
    bpy.ops.mesh.primitive_plane_add(size=20.0, location=(0, 0, 0))
    floor = bpy.context.object
    floor_mat = bpy.data.materials.new(name="FloorMaterial")
    floor_mat.use_nodes = True
    floor_bsdf = floor_mat.node_tree.nodes.get("Principled BSDF")
    if floor_bsdf:
        floor_bsdf.inputs["Base Color"].default_value = (0.92, 0.92, 0.92, 1.0)
        floor_bsdf.inputs["Roughness"].default_value = 0.6
    floor.data.materials.append(floor_mat)


def _find_model_center() -> Vector:
    mesh_objs = [obj for obj in bpy.data.objects if obj.type == "MESH" and obj.name != "Plane"]
    if not mesh_objs:
        return Vector((0.0, 0.0, 1.0))
    center = Vector((0.0, 0.0, 0.0))
    for obj in mesh_objs:
        center += obj.location
    return center / len(mesh_objs)


def _render_views(scene, output_dir: str, views: int):
    target_pos = _find_model_center()
    bpy.ops.object.camera_add(location=(4.2, -4.2, 2.4))
    camera = bpy.context.object
    camera.data.lens = 55
    camera.data.clip_start = 0.01
    camera.data.clip_end = 200
    scene.camera = camera

    radius = 5.0
    for idx in range(views):
        angle = (2 * math.pi * idx) / views
        camera.location = Vector(
            (
                target_pos.x + radius * math.cos(angle),
                target_pos.y + radius * math.sin(angle),
                max(1.8, target_pos.z + 1.5),
            )
        )
        _look_at(camera, target_pos)
        scene.render.filepath = os.path.join(output_dir, f"view_{idx:02d}.png")
        bpy.ops.render.render(write_still=True)


def main():
    job_file = _parse_job_file_from_argv()
    with open(job_file, "r", encoding="utf-8") as handle:
        job = json.load(handle)

    output_dir = job["output_dir"]
    os.makedirs(output_dir, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    _configure_render(
        scene,
        resolution=int(job.get("resolution", 768)),
        engine=str(job.get("engine", "CYCLES")),
        samples=int(job.get("samples", 96)),
    )
    _configure_world(scene)
    _add_default_lights()
    _add_floor()
    _add_reference_image(str(job.get("reference_image") or ""))
    _execute_operations(list(job.get("operations", [])))
    _render_views(scene, output_dir, int(job.get("views", 4)))

    blend_file = job["blend_file"]
    bpy.ops.wm.save_as_mainfile(filepath=blend_file)
    print(json.dumps({"ok": True, "output_dir": output_dir, "blend_file": blend_file}))


if __name__ == "__main__":
    main()
