package com.twoman.android

import android.content.Context
import org.json.JSONArray
import java.io.File

class ProfileStore(context: Context) {
    private val storeFile = File(context.filesDir, "profiles.json")

    @Synchronized
    fun loadProfiles(): List<ClientProfile> {
        if (!storeFile.exists()) {
            return emptyList()
        }
        val raw = storeFile.readText(Charsets.UTF_8).trim()
        if (raw.isEmpty()) {
            return emptyList()
        }
        val array = JSONArray(raw)
        return buildList {
            for (index in 0 until array.length()) {
                add(ClientProfile.fromJson(array.getJSONObject(index)))
            }
        }
    }

    @Synchronized
    fun saveProfiles(profiles: List<ClientProfile>) {
        storeFile.parentFile?.mkdirs()
        val array = JSONArray()
        profiles.forEach { array.put(it.toJson()) }
        storeFile.writeText(array.toString(2), Charsets.UTF_8)
    }
}
