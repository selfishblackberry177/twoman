package com.twoman.android

import android.content.ClipData
import android.content.ClipboardManager
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.twoman.android.databinding.ActivityLogBinding
import java.io.File

class LogActivity : AppCompatActivity() {
    private lateinit var binding: ActivityLogBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityLogBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val state = RuntimeStateStore(this).read()
        val logFile = state.logPath.takeIf { it.isNotBlank() }?.let(::File)
        val logText = if (logFile != null && logFile.exists()) {
            logFile.readText(Charsets.UTF_8).takeLast(64 * 1024)
        } else {
            ""
        }
        binding.logText.text = logText
        binding.logText.setTextIsSelectable(true)
        binding.copyButton.setOnClickListener {
            val clipboard = getSystemService(ClipboardManager::class.java)
            clipboard?.setPrimaryClip(ClipData.newPlainText(getString(R.string.logs_title), logText))
            Toast.makeText(this, getString(R.string.toast_logs_copied), Toast.LENGTH_SHORT).show()
        }
    }
}
