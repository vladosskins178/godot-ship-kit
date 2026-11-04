# Building a signed AAB in GitHub Actions

This workflow builds a release AAB for your Godot game on every tag, signs it with your
upload key from repository secrets, verifies it, and keeps it as a build artifact.

Add these repository secrets first:

| Secret | Value |
| --- | --- |
| `UPLOAD_KEYSTORE_BASE64` | `base64 -w0 upload.keystore` |
| `UPLOAD_KEYSTORE_ALIAS` | the key alias |
| `UPLOAD_KEYSTORE_PASSWORD` | the keystore password |

Your export preset needs Gradle build enabled and `gradle_build/export_format` set to AAB.
Either commit the `android/` build template, or let `--install-template` install it during
the build (shown below).

```yaml
name: Release AAB

on:
  push:
    tags: ["v*"]

jobs:
  android:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: "17"

      - uses: android-actions/setup-android@v3

      # Downloads Godot and its export templates (both checked against Godot's
      # SHA512-SUMS.txt), installs gsk, then runs the command in args.
      - uses: vladosskins178/godot-ship-kit@v0
        with:
          godot-version: "4.6.3"
          export-templates: "true"
          args: --version

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
          KEYSTORE_BASE64: ${{ secrets.UPLOAD_KEYSTORE_BASE64 }}
          GODOT_ANDROID_KEYSTORE_RELEASE_USER: ${{ secrets.UPLOAD_KEYSTORE_ALIAS }}
          GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD: ${{ secrets.UPLOAD_KEYSTORE_PASSWORD }}
        run: |
          echo "$KEYSTORE_BASE64" | base64 -d > "$RUNNER_TEMP/upload.keystore"
          export GODOT_ANDROID_KEYSTORE_RELEASE_PATH="$RUNNER_TEMP/upload.keystore"
          gsk build --install-template -o build/game.aab
          rm -f "$RUNNER_TEMP/upload.keystore"

      - uses: actions/upload-artifact@v4
        with:
          name: aab
          path: build/game.aab
```

Secrets are passed through `env:` rather than written into the script, so they never end
up in the command line or the rendered workflow. `gsk build` also masks them in its log.

## Visual checks on pull requests

```yaml
name: Eyes

on: pull_request

jobs:
  eyes:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: vladosskins178/godot-ship-kit@v0
        with:
          args: eyes --display xvfb --strict
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: eyes-report
          path: .gsk/eyes
```

Reviewers download the artifact and open `report.html`. The job fails when a shot changed,
a reference is missing, or a capture raised an error alert.
