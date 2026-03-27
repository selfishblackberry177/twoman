package com.twoman.android

import android.content.Context

class SelectionStore(context: Context) {
    private val prefs = context.getSharedPreferences("selection-store", Context.MODE_PRIVATE)

    fun read(): Selection = Selection(
        profileId = prefs.getString(KEY_PROFILE_ID, "").orEmpty(),
        mode = prefs.getString(KEY_MODE, ProxyService.MODE_PROXY).orEmpty().ifBlank {
            ProxyService.MODE_PROXY
        },
    )

    fun write(selection: Selection) {
        prefs.edit()
            .putString(KEY_PROFILE_ID, selection.profileId)
            .putString(KEY_MODE, selection.mode)
            .apply()
    }

    data class Selection(
        val profileId: String = "",
        val mode: String = ProxyService.MODE_PROXY,
    )

    companion object {
        private const val KEY_PROFILE_ID = "profile_id"
        private const val KEY_MODE = "mode"
    }
}
