package com.twoman.android

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.io.File

class RuntimeStateStore(context: Context) {
    private val loggerTag = "TwomanState"
    private val stateFile = File(context.noBackupFilesDir, "runtime-status.json")

    @Synchronized
    fun read(): RuntimeStatus {
        if (!stateFile.exists()) {
            Log.d(loggerTag, "read missing path=${stateFile.absolutePath}")
            return RuntimeStatus()
        }
        return try {
            RuntimeStatus.fromJson(JSONObject(stateFile.readText(Charsets.UTF_8))).also { status ->
                Log.d(
                    loggerTag,
                    "read running=${status.running} mode=${status.mode} profile=${status.profileName} path=${stateFile.absolutePath}",
                )
            }
        } catch (_error: Exception) {
            Log.w(loggerTag, "read failed path=${stateFile.absolutePath}")
            RuntimeStatus()
        }
    }

    @Synchronized
    fun write(status: RuntimeStatus) {
        stateFile.parentFile?.mkdirs()
        val enriched = status.copy(updatedAtEpochMs = System.currentTimeMillis())
        stateFile.writeText(enriched.toJson().toString(2), Charsets.UTF_8)
        Log.i(
            loggerTag,
            "write running=${enriched.running} mode=${enriched.mode} profile=${enriched.profileName} path=${stateFile.absolutePath}",
        )
    }
}
