extends Control

## Title screen. "Play" opens the diorama scene.

@onready var _play: Button = $Center/Card/Panel/Buttons/Play
@onready var _version: Label = $Version


func _ready() -> void:
	var _connected: int = _play.pressed.connect(_on_play)
	_play.grab_focus()  # so Enter / gamepad A starts the game
	_version.text = "v" + str(ProjectSettings.get_setting("application/config/version", "dev"))


func _on_play() -> void:
	var _changed: Error = get_tree().change_scene_to_file("res://scenes/diorama.tscn")
