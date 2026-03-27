package com.twoman.android

import android.util.Base64
import org.json.JSONObject
import java.util.UUID

data class ClientProfile(
    val id: String = UUID.randomUUID().toString(),
    val name: String,
    val brokerBaseUrl: String,
    val clientToken: String,
    val verifyTls: Boolean = false,
    val http2Ctl: Boolean = true,
    val http2Data: Boolean = false,
    val httpPort: Int = 28167,
    val socksPort: Int = 21167,
    val httpTimeoutSeconds: Int = 30,
    val flushDelaySeconds: Double = 0.01,
    val maxBatchBytes: Int = 65536,
    val dataUploadMaxBatchBytes: Int = 65536,
    val dataUploadFlushDelaySeconds: Double = 0.004,
    val vpnDnsServers: List<String> = listOf("1.1.1.1", "8.8.8.8"),
    val idleRepollCtlSeconds: Double = 0.05,
    val idleRepollDataSeconds: Double = 0.1,
    val traceEnabled: Boolean = false,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("id", id)
        put("name", name)
        put("brokerBaseUrl", brokerBaseUrl)
        put("clientToken", clientToken)
        put("verifyTls", verifyTls)
        put("http2Ctl", http2Ctl)
        put("http2Data", http2Data)
        put("httpPort", httpPort)
        put("socksPort", socksPort)
        put("httpTimeoutSeconds", httpTimeoutSeconds)
        put("flushDelaySeconds", flushDelaySeconds)
        put("maxBatchBytes", maxBatchBytes)
        put("dataUploadMaxBatchBytes", dataUploadMaxBatchBytes)
        put("dataUploadFlushDelaySeconds", dataUploadFlushDelaySeconds)
        put("vpnDnsServers", org.json.JSONArray(vpnDnsServers))
        put("idleRepollCtlSeconds", idleRepollCtlSeconds)
        put("idleRepollDataSeconds", idleRepollDataSeconds)
        put("traceEnabled", traceEnabled)
    }

    fun toRuntimeConfig(logPath: String): JSONObject = JSONObject().apply {
        put("transport", "http")
        put("broker_base_url", brokerBaseUrl)
        put("client_token", clientToken)
        put("listen_host", "127.0.0.1")
        put("http_listen_port", httpPort)
        put("socks_listen_port", socksPort)
        put("log_path", logPath)
        put("http_timeout_seconds", httpTimeoutSeconds)
        put("flush_delay_seconds", flushDelaySeconds)
        put("max_batch_bytes", maxBatchBytes)
        put("verify_tls", verifyTls)
        put("streaming_up_lanes", org.json.JSONArray())
        put("vpn_dns_servers", org.json.JSONArray(vpnDnsServers))
        put(
            "upload_profiles",
            JSONObject().apply {
                put(
                    "data",
                    JSONObject().apply {
                        put("max_batch_bytes", dataUploadMaxBatchBytes)
                        put("flush_delay_seconds", dataUploadFlushDelaySeconds)
                    },
                )
            },
        )
        put(
            "idle_repoll_delay_seconds",
            JSONObject().apply {
                put("ctl", idleRepollCtlSeconds)
                put("data", idleRepollDataSeconds)
            },
        )
        put(
            "http2_enabled",
            JSONObject().apply {
                put("ctl", http2Ctl)
                put("data", http2Data)
            },
        )
    }

    fun toShareText(): String {
        val exportJson = JSONObject().apply {
            put("name", name)
            put("brokerBaseUrl", brokerBaseUrl)
            put("clientToken", clientToken)
            put("verifyTls", verifyTls)
            put("http2Ctl", http2Ctl)
            put("http2Data", http2Data)
            put("httpPort", httpPort)
            put("socksPort", socksPort)
            put("httpTimeoutSeconds", httpTimeoutSeconds)
            put("flushDelaySeconds", flushDelaySeconds)
            put("maxBatchBytes", maxBatchBytes)
            put("dataUploadMaxBatchBytes", dataUploadMaxBatchBytes)
            put("dataUploadFlushDelaySeconds", dataUploadFlushDelaySeconds)
            put("vpnDnsServers", org.json.JSONArray(vpnDnsServers))
            put("idleRepollCtlSeconds", idleRepollCtlSeconds)
            put("idleRepollDataSeconds", idleRepollDataSeconds)
            put("traceEnabled", traceEnabled)
        }
        val encoded = Base64.encodeToString(
            exportJson.toString().toByteArray(Charsets.UTF_8),
            Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING,
        )
        return "$SHARE_PREFIX$encoded"
    }

    companion object {
        private const val SHARE_PREFIX = "twoman://profile?data="

        fun fromJson(json: JSONObject): ClientProfile = ClientProfile(
            id = json.optString("id").ifBlank { UUID.randomUUID().toString() },
            name = json.optString("name"),
            brokerBaseUrl = json.optString("brokerBaseUrl"),
            clientToken = json.optString("clientToken"),
            verifyTls = json.optBoolean("verifyTls", false),
            http2Ctl = json.optBoolean("http2Ctl", true),
            http2Data = json.optBoolean("http2Data", false),
            httpPort = json.optInt("httpPort", 28167),
            socksPort = json.optInt("socksPort", 21167),
            httpTimeoutSeconds = json.optInt("httpTimeoutSeconds", 30),
            flushDelaySeconds = json.optDouble("flushDelaySeconds", 0.01),
            maxBatchBytes = json.optInt("maxBatchBytes", 65536),
            dataUploadMaxBatchBytes = json.optInt("dataUploadMaxBatchBytes", 65536),
            dataUploadFlushDelaySeconds = json.optDouble("dataUploadFlushDelaySeconds", 0.004),
            vpnDnsServers = json.optJSONArray("vpnDnsServers")?.let { array ->
                buildList {
                    for (index in 0 until array.length()) {
                        val value = array.optString(index).trim()
                        if (value.isNotEmpty()) add(value)
                    }
                }.ifEmpty { listOf("1.1.1.1", "8.8.8.8") }
            } ?: listOf("1.1.1.1", "8.8.8.8"),
            idleRepollCtlSeconds = json.optDouble("idleRepollCtlSeconds", 0.05),
            idleRepollDataSeconds = json.optDouble("idleRepollDataSeconds", 0.1),
            traceEnabled = json.optBoolean("traceEnabled", false),
        )

        fun fromShareText(rawText: String): ClientProfile {
            val text = rawText.trim()
            val json = when {
                text.startsWith(SHARE_PREFIX) -> {
                    val encoded = text.removePrefix(SHARE_PREFIX)
                    val decoded = Base64.decode(encoded, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
                    JSONObject(String(decoded, Charsets.UTF_8))
                }
                text.matches(Regex("^[A-Za-z0-9_-]+$")) -> {
                    val decoded = Base64.decode(text, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
                    JSONObject(String(decoded, Charsets.UTF_8))
                }
                text.startsWith("{") -> JSONObject(text)
                else -> error("Invalid import text")
            }
            return fromJson(json).copy(id = UUID.randomUUID().toString())
        }
    }
}
