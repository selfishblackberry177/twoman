package com.twoman.android

import android.content.Context
import org.json.JSONObject
import java.io.File

data class RuntimeListenState(
    val httpPort: Int,
    val socksPort: Int,
)

object AppFiles {
    fun profileRuntimeDir(context: Context, profileId: String): File =
        File(context.noBackupFilesDir, "profiles/$profileId").apply { mkdirs() }

    fun runtimeConfigFile(context: Context, profileId: String): File =
        File(profileRuntimeDir(context, profileId), "client-config.json")

    fun runtimeLogFile(context: Context, profileId: String): File =
        File(profileRuntimeDir(context, profileId), "helper.log")

    fun runtimeListenStateFile(context: Context, profileId: String): File =
        File(profileRuntimeDir(context, profileId), "listen-state.json")

    fun readRuntimeListenState(context: Context, profileId: String): RuntimeListenState? {
        val file = runtimeListenStateFile(context, profileId)
        if (!file.exists()) {
            return null
        }
        return runCatching {
            val json = JSONObject(file.readText(Charsets.UTF_8))
            RuntimeListenState(
                httpPort = json.optInt("http_port", 0),
                socksPort = json.optInt("socks_port", 0),
            )
        }.getOrNull()
    }
}
