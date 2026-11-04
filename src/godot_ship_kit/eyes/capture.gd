# gsk eyes capture runner.
#
# Started by `gsk eyes` as:  godot --path <project> -s capture.gd -- --gsk-job=<job.json>
# Loads each scene from the job, waits, runs optional actions, then saves a PNG
# of the root viewport plus a small JSON file with scene facts (camera, meshes in
# frame, meshes without materials, skeletons whose animations are not playing).
extends SceneTree

const MARK := "[gsk-eyes]"

var _job: Dictionary = {}
var _deadline_ms := 0


func _initialize() -> void:
	var job_path := ""
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--gsk-job="):
			job_path = arg.substr("--gsk-job=".length())
	if job_path == "":
		printerr(MARK, " missing --gsk-job=<file>")
		quit(2)
		return
	var text := FileAccess.get_file_as_string(job_path)
	var parsed = JSON.parse_string(text)
	if typeof(parsed) != TYPE_DICTIONARY:
		printerr(MARK, " cannot parse job file ", job_path)
		quit(2)
		return
	_job = parsed
	_deadline_ms = Time.get_ticks_msec() + int(float(_job.get("budget_seconds", 300)) * 1000.0)
	_run.call_deferred()


func _run() -> void:
	var size: Array = _job.get("size", [1080, 2400])
	var win := Vector2i(int(size[0]), int(size[1]))
	DisplayServer.window_set_size(win)
	root.size = win
	var out_dir: String = _job.get("output_dir", "user://gsk_eyes")
	DirAccess.make_dir_recursive_absolute(out_dir)
	var code := 0
	for shot in _job.get("shots", []):
		if Time.get_ticks_msec() > _deadline_ms:
			printerr(MARK, " time budget exhausted, stopping")
			code = 3
			break
		var ok: bool = await _capture(shot, out_dir)
		if not ok:
			code = 1
	print(MARK, " done")
	quit(code)


func _wait_seconds(seconds: float, min_frames: int = 2) -> void:
	var end := Time.get_ticks_msec() + int(seconds * 1000.0)
	var frames := 0
	while Time.get_ticks_msec() < end or frames < min_frames:
		await process_frame
		frames += 1
		if Time.get_ticks_msec() > _deadline_ms:
			return


func _capture(shot: Dictionary, out_dir: String) -> bool:
	var shot_name: String = shot.get("name", "shot")
	var scene_path: String = shot.get("scene", "")
	print(MARK, " BEGIN ", shot_name)
	var started := Time.get_ticks_msec()
	if scene_path != "":
		var err := change_scene_to_file(scene_path)
		if err != OK:
			printerr(MARK, " cannot load scene ", scene_path, " (error ", err, ")")
			print(MARK, " END ", shot_name)
			return false
	await process_frame
	await process_frame
	await _wait_seconds(float(shot.get("wait", 1.0)))
	for action in shot.get("actions", []):
		await _do_action(action)
	# Let the last state reach the screen before reading pixels.
	await RenderingServer.frame_post_draw
	var img := root.get_texture().get_image()
	if img == null:
		printerr(MARK, " viewport returned no image for ", shot_name)
		print(MARK, " END ", shot_name)
		return false
	var png := out_dir.path_join(shot_name + ".png")
	img.save_png(png)
	var facts := _scene_facts()
	facts["name"] = shot_name
	facts["scene"] = scene_path
	facts["size"] = [img.get_width(), img.get_height()]
	facts["seconds"] = (Time.get_ticks_msec() - started) / 1000.0
	facts["renderer"] = RenderingServer.get_current_rendering_method()
	facts["driver"] = RenderingServer.get_current_rendering_driver_name()
	facts["adapter"] = RenderingServer.get_video_adapter_name()
	var fh := FileAccess.open(out_dir.path_join(shot_name + ".json"), FileAccess.WRITE)
	if fh:
		fh.store_string(JSON.stringify(facts, "  "))
		fh.close()
	print(MARK, " END ", shot_name)
	return true


func _node(path: String) -> Node:
	if path == "" or path == ".":
		return current_scene
	if path.begins_with("/"):
		return root.get_node_or_null(NodePath(path))
	if current_scene == null:
		return null
	return current_scene.get_node_or_null(NodePath(path))


func _do_action(action: Dictionary) -> void:
	if action.has("wait"):
		await _wait_seconds(float(action["wait"]), 1)
		return
	if action.has("tap"):
		var at: Array = action["tap"]
		var vp_size := Vector2(root.size)
		var pos := Vector2(float(at[0]) * vp_size.x, float(at[1]) * vp_size.y)
		for pressed in [true, false]:
			var ev := InputEventMouseButton.new()
			ev.button_index = MOUSE_BUTTON_LEFT
			ev.pressed = pressed
			ev.position = pos
			ev.global_position = pos
			Input.parse_input_event(ev)
			await process_frame
		await _wait_seconds(float(action.get("then_wait", 0.3)), 1)
		return
	if action.has("press"):
		for pressed in [true, false]:
			var ev := InputEventAction.new()
			ev.action = StringName(action["press"])
			ev.pressed = pressed
			Input.parse_input_event(ev)
			await process_frame
		await _wait_seconds(float(action.get("then_wait", 0.3)), 1)
		return
	var target := _node(String(action.get("node", "")))
	if target == null:
		printerr(MARK, " action target not found: ", action.get("node", ""))
		return
	if action.has("call"):
		target.callv(String(action["call"]), action.get("args", []))
	elif action.has("set"):
		target.set(String(action["set"]), action.get("value"))
	await process_frame


func _scene_facts() -> Dictionary:
	var facts := {
		"has_3d": false,
		"camera_3d": "",
		"meshes": 0,
		"meshes_on_screen": 0,
		"off_screen": [],
		"tiny": [],
		"no_material": [],
		"idle_skeletons": [],
	}
	var scene := current_scene
	if scene == null:
		return facts
	var cam := root.get_camera_3d()
	if cam:
		facts["camera_3d"] = str(scene.get_path_to(cam)) if scene.is_ancestor_of(cam) else str(cam.get_path())
	var vp_rect := Rect2(Vector2.ZERO, Vector2(root.size))
	var screen_area := vp_rect.size.x * vp_rect.size.y
	var stack: Array[Node] = [scene]
	while not stack.is_empty():
		var n: Node = stack.pop_back()
		for c in n.get_children():
			stack.push_back(c)
		if n is Node3D:
			facts["has_3d"] = true
		if n is MeshInstance3D and n.is_visible_in_tree() and n.mesh != null:
			var mi := n as MeshInstance3D
			facts["meshes"] += 1
			var label := str(scene.get_path_to(mi))
			if _missing_material(mi):
				facts["no_material"].append(label)
			if cam:
				var r := _screen_rect(cam, mi)
				if r.size == Vector2.ZERO or not r.intersects(vp_rect):
					facts["off_screen"].append(label)
				else:
					facts["meshes_on_screen"] += 1
					var clipped := r.intersection(vp_rect)
					if clipped.size.x * clipped.size.y < screen_area * 0.0001:
						facts["tiny"].append(label)
		if n is AnimationPlayer:
			var ap := n as AnimationPlayer
			var owner_root := ap.get_node_or_null(ap.root_node)
			if owner_root and _has_skeleton(owner_root) and not ap.is_playing() and not _driven_by_tree(ap):
				var anims := Array(ap.get_animation_list()).filter(func(a): return a != "RESET")
				if anims.size() > 0:
					facts["idle_skeletons"].append(str(scene.get_path_to(ap)))
	for key in ["off_screen", "tiny", "no_material", "idle_skeletons"]:
		var arr: Array = facts[key]
		if arr.size() > 25:
			facts[key] = arr.slice(0, 25) + ["... %d more" % (arr.size() - 25)]
	return facts


func _missing_material(mi: MeshInstance3D) -> bool:
	if mi.material_override != null:
		return false
	for i in mi.mesh.get_surface_count():
		if mi.get_surface_override_material(i) == null and mi.mesh.surface_get_material(i) == null:
			return true
	return false


func _screen_rect(cam: Camera3D, mi: MeshInstance3D) -> Rect2:
	var aabb := mi.global_transform * mi.get_aabb()
	var pts: Array[Vector2] = []
	var behind := 0
	for i in 8:
		var corner := aabb.get_endpoint(i)
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
	var r := Rect2(pts[0], Vector2.ZERO)
	for p in pts:
		r = r.expand(p)
	if r.size == Vector2.ZERO:
		r.size = Vector2(1, 1)
	return r


func _has_skeleton(n: Node) -> bool:
	if n is Skeleton3D:
		return true
	for c in n.get_children():
		if _has_skeleton(c):
			return true
	return false


func _driven_by_tree(ap: AnimationPlayer) -> bool:
	var parent := ap.get_parent()
	if parent == null:
		return false
	for c in parent.get_children():
		if c is AnimationTree and (c as AnimationTree).active:
			return true
	return false
