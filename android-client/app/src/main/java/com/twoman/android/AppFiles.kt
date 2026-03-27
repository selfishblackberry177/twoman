package com.twoman.android

import android.content.Context
import java.io.File

object AppFiles {
    fun profileRuntimeDir(context: Context, profileId: String): File =
        File(context.noBackupFilesDir, "profiles/$profileId").apply { mkdirs() }

    fun runtimeConfigFile(context: Context, profileId: String): File =
        File(profileRuntimeDir(context, profileId), "client-config.json")

    fun runtimeLogFile(context: Context, profileId: String): File =
        File(profileRuntimeDir(context, profileId), "helper.log")
}
