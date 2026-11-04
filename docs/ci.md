# Building a signed AAB in GitHub Actions

This workflow builds a release AAB for your Godot game on every tag, signs it with your
upload key from repository secrets, verifies it, and attaches it to the run.

Add these repository secrets first:

| Secret | Value |
| --- | --- |
| `UPLOAD_KEYSTORE_BASE64` | `base64 -w0 upload.keystore` |
| `UPLOAD_KEYSTORE_ALIAS` | the key alias |
| `UPLOAD_KEYSTORE_PASSWORD` | the keystore password |

Your export preset must have Gradle build enabled and `gradle_build/export_format` set to
AAB, and the `android/` build template must be committed (or installed in a step).

```yaml
name: Release AAB

on:
  push:
    tags: ["v*"]

jobs:
  android:
    runs-on: ubuntu-24.04
    env:
      GODOT_VERSION: "4.6.3"
      GODOT: ${{ github.workspace }}/.godot-bin/godot
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: "17"

      - uses: android-actions/setup-android@v3

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install Godot and export templates
        run: |
          mkdir -p .godot-bin && cd .godot-bin
          base="https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}-stable"
          curl -fsSL -o godot.zip "$base/Godot_v${GODOT_VERSION}-stable_linux.x86_64.zip"
          unzip -q godot.zip && mv Godot_v${GODOT_VERSION}-stable_linux.x86_64 godot
          curl -fsSL -o templates.tpz "$base/Godot_v${GODOT_VERSION}-stable_export_templates.tpz"
          mkdir -p ~/.local/share/godot/export_templates/${GODOT_VERSION}.stable
          unzip -q templates.tpz && mv templates/* ~/.local/share/godot/export_templates/${GODOT_VERSION}.stable/

      - name: Install gsk
        run: pip install "godot-ship-kit @ git+https://github.com/vladosskins178/godot-ship-kit"

      - name: Point Godot at the SDK and JDK
        run: |
          keytool -keyalg RSA -genkeypair -alias androiddebugkey -keypass android \
            -keystore "$RUNNER_TEMP/debug.keystore" -storepass android \
            -dname "CN=Android Debug,O=Android,C=US" -validity 9999 -deststoretype pkcs12
          gsk configure --godot "$GODOT" --android-sdk "$ANDROID_HOME" \
            --java-sdk "$JAVA_HOME" --debug-keystore "$RUNNER_TEMP/debug.keystore"

      - name: Import
        run: $GODOT --headless --path . --import

      - name: Doctor
        run: gsk doctor

      - name: Build and verify
        env:
          GODOT_ANDROID_KEYSTORE_RELEASE_USER: ${{ secrets.UPLOAD_KEYSTORE_ALIAS }}
          GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD: ${{ secrets.UPLOAD_KEYSTORE_PASSWORD }}
        run: |
          echo "${{ secrets.UPLOAD_KEYSTORE_BASE64 }}" | base64 -d > "$RUNNER_TEMP/upload.keystore"
          export GODOT_ANDROID_KEYSTORE_RELEASE_PATH="$RUNNER_TEMP/upload.keystore"
          gsk build -o build/game.aab

      - uses: actions/upload-artifact@v4
        with:
          name: aab
          path: build/game.aab
```

Add a visual check to pull requests with the same Godot download plus
`sudo apt-get install -y xvfb libgl1-mesa-dri libx11-6 libxcursor1 libxinerama1 libxrandr2 libxi6 libxkbcommon0`,
then `gsk eyes`. Upload `.gsk/eyes` as
an artifact so reviewers can open `report.html`.
