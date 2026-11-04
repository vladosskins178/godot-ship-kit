# gsk eyes capture runner (template).
#
# `gsk eyes` fills in the constants and the body of _run_shots() for each device,
# writes the result next to the screenshots and starts Godot with
#   godot --path <project> -s <that file>
# Each shot loads a scene, waits, runs its actions, then saves a PNG of the root
# viewport plus a small JSON file with scene facts (camera, meshes in frame, meshes
# without materials, skeletons whose animations are not playing).
#
# The script runs under the game's own GDScript warning settings. Shot data is
# therefore generated as typed code instead of being parsed from JSON at runtime,
# and every declaration is explicitly typed, so projects that treat untyped,
# inferred or unsafe code as errors can still run it. Works on Godot 4.2+.
extends SceneTree

const MARK: String = "[gsk-eyes]"
const LIST_LIMIT: int = 25
const WIDTH: int = 0  # @@WIDTH@@
const HEIGHT: int = 0  # @@HEIGHT@@
const OUT_DIR: String = ""  # @@OUT_DIR@@
const BUDGET_MS: int = 0  # @@BUDGET_MS@@

var _deadline_ms: int = 0
var _started_ms: int = 0


func _initialize() -> void:
	_deadline_ms = Time.get_ticks_msec() + BUDGET_MS
	_run.call_deferred()


func _run() -> void:
	var win: Vector2i = Vector2i(WIDTH, HEIGHT)
	DisplayServer.window_set_size(win)
	root.size = win
	var _made: Error = DirAccess.make_dir_recursive_absolute(OUT_DIR)
	var code: int = await _run_shots()
	print(MARK, " done")
	quit(code)


func _run_shots() -> int:
	var code: int = 0
	pass  # @@SHOTS@@
	return code


func _over_budget() -> bool:
	if Time.get_ticks_msec() > _deadline_ms:
		printerr(MARK, " time budget exhausted, stopping")
		return true
	return false


func _wait_seconds(seconds: float, min_frames: int = 2) -> void:
	var end: int = Time.get_ticks_msec() + int(seconds * 1000.0)
	var frames: int = 0
	while Time.get_ticks_msec() < end or frames < min_frames:
		await process_frame
		frames += 1
		if Time.get_ticks_msec() > _deadline_ms:
			return


func _open_scene(shot_name: String, scene_path: String, wait: float) -> bool:
	print(MARK, " BEGIN ", shot_name)
	_started_ms = Time.get_ticks_msec()
	if scene_path != "":
		var err: Error = change_scene_to_file(scene_path)
		if err != OK:
			printerr(MARK, " ERROR cannot load scene ", scene_path, " (error ", err, ")")
			print(MARK, " END ", shot_name)
			return false
	await process_frame
	await process_frame
	await _wait_seconds(wait)
	return true


func _save(shot_name: String, scene_path: String) -> bool:
	# Let the last state reach the screen before reading pixels.
	await RenderingServer.frame_post_draw
	var img: Image = root.get_texture().get_image()
	if img == null:
		printerr(MARK, " ERROR viewport returned no image for ", shot_name)
		print(MARK, " END ", shot_name)
		return false
	var _saved: Error = img.save_png(OUT_DIR.path_join(shot_name + ".png"))
	var facts: Dictionary = _scene_facts()
	facts["name"] = shot_name
	facts["scene"] = scene_path
	facts["size"] = [img.get_width(), img.get_height()]
	facts["seconds"] = float(Time.get_ticks_msec() - _started_ms) / 1000.0
	# These two exist from Godot 4.3; call them dynamically so 4.2 still parses the script.
	if RenderingServer.has_method("get_current_rendering_method"):
		facts["renderer"] = RenderingServer.call("get_current_rendering_method")
	if RenderingServer.has_method("get_current_rendering_driver_name"):
		facts["driver"] = RenderingServer.call("get_current_rendering_driver_name")
	facts["adapter"] = RenderingServer.get_video_adapter_name()
	var fh: FileAccess = FileAccess.open(OUT_DIR.path_join(shot_name + ".json"), FileAccess.WRITE)
	if fh != null:
		# store_string() returns void before Godot 4.4 and bool after; call() fits both.
		var _stored: Variant = fh.call("store_string", JSON.stringify(facts, "  "))
		fh.close()
	print(MARK, " END ", shot_name)
	return true


# ---------------------------------------------------------------- actions


func _node(path: String) -> Node:
	if path == "" or path == ".":
		return current_scene
	if path.begins_with("/"):
		return root.get_node_or_null(NodePath(path))
	if current_scene == null:
		return null
	return current_scene.get_node_or_null(NodePath(path))


func _tap(x: float, y: float, then_wait: float) -> void:
	var vp_size: Vector2 = Vector2(root.size)
	var pos: Vector2 = Vector2(x * vp_size.x, y * vp_size.y)
	for pressed: bool in [true, false]:
		var ev: InputEventMouseButton = InputEventMouseButton.new()
		ev.button_index = MOUSE_BUTTON_LEFT
		ev.pressed = pressed
		ev.position = pos
		ev.global_position = pos
		Input.parse_input_event(ev)
		await process_frame
	await _wait_seconds(then_wait, 1)


func _press(action: String, then_wait: float) -> void:
	if not InputMap.has_action(StringName(action)):
		printerr(MARK, " ERROR action ", action, " is not in the Input Map")
		return
	for pressed: bool in [true, false]:
		var ev: InputEventAction = InputEventAction.new()
		ev.action = StringName(action)
		ev.pressed = pressed
		Input.parse_input_event(ev)
		await process_frame
	await _wait_seconds(then_wait, 1)


func _call_node(path: String, method: String, args: Array) -> void:
	var target: Node = _node(path)
	if target == null:
		printerr(MARK, " ERROR action target not found: ", path)
		return
	if not target.has_method(StringName(method)):
		printerr(MARK, " ERROR ", path, " has no method ", method)
		return
	var _result: Variant = target.callv(StringName(method), args)
	await process_frame


func _set_node(path: String, property: String, value: Variant) -> void:
	var target: Node = _node(path)
	if target == null:
		printerr(MARK, " ERROR action target not found: ", path)
		return
	target.set(StringName(property), value)
	await process_frame


# ---------------------------------------------------------------- scene facts


func _scene_facts() -> Dictionary:
	var has_3d: bool = false
	var camera_path: String = ""
	var meshes: int = 0
	var on_screen: int = 0
	var off_screen: Array[String] = []
	var tiny: Array[String] = []
	var no_material: Array[String] = []
	var idle_skeletons: Array[String] = []
	var overflowing: Array[String] = []
	var scene: Node = current_scene
	if scene != null:
		var cam: Camera3D = root.get_camera_3d()
		if cam != null:
			camera_path = str(scene.get_path_to(cam)) if scene.is_ancestor_of(cam) else str(cam.get_path())
		var vp_rect: Rect2 = Rect2(Vector2.ZERO, Vector2(root.size))
		var screen_area: float = vp_rect.size.x * vp_rect.size.y
		var stack: Array[Node] = [scene]
		while not stack.is_empty():
			var n: Node = stack.pop_back()
			for child: Node in n.get_children():
				stack.push_back(child)
			if n is Node3D:
				has_3d = true
			var mi: MeshInstance3D = n as MeshInstance3D
			if mi != null and mi.is_visible_in_tree() and mi.mesh != null:
				meshes += 1
				var label: String = str(scene.get_path_to(mi))
				if _missing_material(mi):
					no_material.append(label)
				if cam != null:
					var r: Rect2 = _screen_rect(cam, mi)
					if r.size == Vector2.ZERO or not r.intersects(vp_rect):
						off_screen.append(label)
					else:
						on_screen += 1
						var clipped: Rect2 = r.intersection(vp_rect)
						if clipped.size.x * clipped.size.y < screen_area * 0.0001:
							tiny.append(label)
			var ap: AnimationPlayer = n as AnimationPlayer
			if ap != null and _is_idle_rig(ap):
				idle_skeletons.append(str(scene.get_path_to(ap)))
			var ctl: Control = n as Control
			if ctl != null and ctl.is_visible_in_tree():
				var note: String = _overflow(ctl)
				if note != "":
					overflowing.append(str(scene.get_path_to(ctl)) + " " + note)
	return {
		"has_3d": has_3d,
		"camera_3d": camera_path,
		"meshes": meshes,
		"meshes_on_screen": on_screen,
		"off_screen": _limited(off_screen),
		"tiny": _limited(tiny),
		"no_material": _limited(no_material),
		"idle_skeletons": _limited(idle_skeletons),
		"overflowing": _limited(overflowing),
	}


# A Control that sticks out of the screen while not covering it (backgrounds are
# usually larger than the screen on purpose). Only the outermost offender is reported.
func _overflow(ctl: Control) -> String:
	var view: Rect2 = root.get_visible_rect()
	var r: Rect2 = ctl.get_global_rect()
	if r.size.x <= 0.0 or r.size.y <= 0.0 or not r.intersects(view):
		return ""
	if r.size.x >= view.size.x and r.size.y >= view.size.y:
		return ""
	var parent: Control = ctl.get_parent() as Control
	if parent != null and parent.is_visible_in_tree() and _overflow(parent) != "":
		return ""
	var sides: Array[String] = []
	var margin: float = 2.0
	if r.position.x < view.position.x - margin:
		sides.append("left %d px" % int(view.position.x - r.position.x))
	if r.end.x > view.end.x + margin:
		sides.append("right %d px" % int(r.end.x - view.end.x))
	if r.position.y < view.position.y - margin:
		sides.append("top %d px" % int(view.position.y - r.position.y))
	if r.end.y > view.end.y + margin:
		sides.append("bottom %d px" % int(r.end.y - view.end.y))
	if sides.is_empty():
		return ""
	return "(past the " + ", ".join(sides) + ")"


func _limited(items: Array[String]) -> Array[String]:
	if items.size() <= LIST_LIMIT:
		return items
	var out: Array[String] = []
	for i: int in LIST_LIMIT:
		out.append(items[i])
	out.append("... %d more" % (items.size() - LIST_LIMIT))
	return out


func _is_idle_rig(ap: AnimationPlayer) -> bool:
	var rig_root: Node = ap.get_node_or_null(ap.root_node)
	if rig_root == null or not _has_skeleton(rig_root) or ap.is_playing() or _driven_by_tree(ap):
		return false
	for anim_name: String in ap.get_animation_list():
		if anim_name != "RESET":
			return true
	return false


func _missing_material(mi: MeshInstance3D) -> bool:
	if mi.material_override != null:
		return false
	for i: int in mi.mesh.get_surface_count():
		if mi.get_surface_override_material(i) == null and mi.mesh.surface_get_material(i) == null:
			return true
	return false


func _screen_rect(cam: Camera3D, mi: MeshInstance3D) -> Rect2:
	var aabb: AABB = mi.global_transform * mi.get_aabb()
	var pts: Array[Vector2] = []
	var behind: int = 0
	for i: int in 8:
		var corner: Vector3 = aabb.get_endpoint(i)
		if cam.is_position_behind(corner):
			behind += 1
			continue
		pts.append(cam.unproject_position(corner))
	if behind > 0 and behind < 8:
		# The box crosses the camera plane; projecting the visible corners alone
		# under-estimates it, so treat it as covering the whole view.
		return Rect2(Vector2.ZERO, Vector2(root.size))
	if pts.is_empty():
		return Rect2()
	var r: Rect2 = Rect2(pts[0], Vector2.ZERO)
	for p: Vector2 in pts:
		r = r.expand(p)
	if r.size == Vector2.ZERO:
		r.size = Vector2(1, 1)
	return r


func _has_skeleton(n: Node) -> bool:
	if n is Skeleton3D:
		return true
	for child: Node in n.get_children():
		if _has_skeleton(child):
			return true
	return false


func _driven_by_tree(ap: AnimationPlayer) -> bool:
	var parent: Node = ap.get_parent()
	if parent == null:
		return false
	for child: Node in parent.get_children():
		var tree: AnimationTree = child as AnimationTree
		if tree != null and tree.active:
			return true
	return false
