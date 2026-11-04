extends Control

## Title screen. "Play" opens the diorama scene.


func _ready() -> void:
	var play: Button = $Center/Card/Panel/Buttons/Play
	play.pressed.connect(_on_play)
	play.grab_focus()  # so Enter / gamepad A starts the game
	$Version.text = "v" + str(ProjectSettings.get_setting("application/config/version", "dev"))


func _on_play() -> void:
	get_tree().change_scene_to_file("res://scenes/diorama.tscn")
