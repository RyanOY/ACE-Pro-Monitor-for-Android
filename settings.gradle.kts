import org.gradle.api.initialization.resolve.RepositoriesMode

pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // usb-serial-for-android 只发布在 JitPack（Maven Central 上没有），必须显式声明
        maven { url = uri("https://jitpack.io") }
    }
}

rootProject.name = "AceSolo"
include(":app")
