package com.twoman.android

import org.json.JSONObject

data class RuntimeStatus(
    val running: Boolean = false,
    val mode: String = "stopped",
    val profileId: String = "",
    val profileName: String = "",
    val brokerBaseUrl: String = "",
    val httpPort: Int = 0,
    val socksPort: Int = 0,
    val logPath: String = "",
    val message: String = "",
    val updatedAtEpochMs: Long = 0,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("running", running)
        put("mode", mode)
        put("profileId", profileId)
        put("profileName", profileName)
        put("brokerBaseUrl", brokerBaseUrl)
        put("httpPort", httpPort)
        put("socksPort", socksPort)
        put("logPath", logPath)
        put("message", message)
        put("updatedAtEpochMs", updatedAtEpochMs)
    }

    companion object {
        fun fromJson(json: JSONObject): RuntimeStatus = RuntimeStatus(
            running = json.optBoolean("running", false),
            mode = json.optString("mode", "stopped"),
            profileId = json.optString("profileId"),
            profileName = json.optString("profileName"),
            brokerBaseUrl = json.optString("brokerBaseUrl"),
            httpPort = json.optInt("httpPort", 0),
            socksPort = json.optInt("socksPort", 0),
            logPath = json.optString("logPath"),
            message = json.optString("message"),
            updatedAtEpochMs = json.optLong("updatedAtEpochMs", 0),
        )
    }
}
