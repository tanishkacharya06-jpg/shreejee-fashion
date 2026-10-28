plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.shreejee.fashion"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.shreejee.fashion"
        minSdk = 24
        targetSdk = 34
        versionCode = 4
        versionName = "1.0.3"
        // ===== EDIT THESE THREE LINES =====
        // 1) Your Gist id (the long code at the end of your Gist's web address).
        buildConfigField("String", "GIST_ID", "\"f8600735ea36e813d7828271e8f31c9b\"")
        // 2) Leave empty while you use Quick Tunnel. Later, if you get a fixed domain, put it here (https://shop.example.com).
        buildConfigField("String", "FIXED_URL", "\"\"")
        // 3) Owner WhatsApp / phone shown on the "shop is offline" page.
        buildConfigField("String", "SHOP_PHONE", "\"918340453008\"")
    }
    signingConfigs {
        // Fixed key committed with the project, so every build is signed the same and installs as an update.
        getByName("debug") {
            storeFile = file("debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
    }
    buildTypes {
        release { isMinifyEnabled = false }
    }
    buildFeatures { buildConfig = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
}
