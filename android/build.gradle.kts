// Tracking viewer for Android: browses a library package exported by the desktop app
// (docs/PACKAGE.md). Build: gradlew assembleDebug (or open this folder in Android Studio).
plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.1.0" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.1.0" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "2.1.0" apply false
}
