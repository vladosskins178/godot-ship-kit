# Security

`gsk` reads signing configuration (keystore paths, aliases, passwords) to check that it
is complete. It never prints, logs or stores those values, and gsk itself makes no network
requests (Gradle, started by `gsk build`, may download build dependencies).

If you find a way to make `gsk` reveal a secret, write outside the project or its own
`.gsk/` folder (other than the editor settings file `gsk configure` is asked to change),
or run unexpected commands, please report it privately through GitHub's
[security advisory form](https://github.com/vladosskins178/godot-ship-kit/security/advisories/new)
rather than a public issue. You will get a reply within a few days.
