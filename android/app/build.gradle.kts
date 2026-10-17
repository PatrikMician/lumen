import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Podpisový klíč je mimo repozitář (android/keystore.properties, viz README). Bez něj se release nepodepíše.
val keystoreProps = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
// ABI: výchozí jsou telefony (ARM). Pro emulátor na PC: -Pabis=x86_64
val abis = (findProperty("abis") as String? ?: "arm64-v8a,armeabi-v7a").split(",").map { it.trim() }
// Jen zkušební sestavení pro emulátor (build_android.py --emulator): prostředí serveru navíc a ladění
val testEnv = findProperty("lumenTestEnv") as String? ?: ""
val testBuild = testEnv.isNotEmpty()

android {
    namespace = "org.mican.lumen"
    compileSdk = 35

    defaultConfig {
        applicationId = "org.mican.lumen"
        minSdk = 29
        targetSdk = 35
        versionCode = (property("lumenVersionCode") as String).toInt()
        versionName = property("lumenVersion") as String
        ndk { abiFilters += abis }
        buildConfigField("String", "LUMEN_SERVER", "\"https://lumen.mican.dpdns.org\"")
        buildConfigField("String", "TEST_ENV", "\"" + testEnv.replace("\"", "") + "\"")
    }

    signingConfigs {
        if (keystoreProps.isNotEmpty()) {
            create("release") {
                storeFile = file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            isDebuggable = testBuild
            if (keystoreProps.isNotEmpty()) signingConfig = signingConfigs.getByName("release")
        }
    }

    // Python, ffmpeg a QuickJS se spouštějí jako programy, proto musí být knihovny rozbalené na disku.
    packaging { jniLibs { useLegacyPackaging = true } }

    // Server Lumenu (backend, static, Python balíčky) a ikony připraví android/build_android.py
    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("lumen-assets"))
    sourceSets["main"].res.srcDir(layout.buildDirectory.dir("lumen-res"))

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { buildConfig = true }
}

dependencies {
    implementation("io.github.junkfood02.youtubedl-android:library:0.18.1")
    implementation("io.github.junkfood02.youtubedl-android:ffmpeg:0.18.1")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
}
