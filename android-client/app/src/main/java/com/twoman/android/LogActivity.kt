package com.twoman.android

import android.os.Bundle
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
        binding.logText.text = if (logFile != null && logFile.exists()) {
            logFile.readText(Charsets.UTF_8).takeLast(64 * 1024)
        } else {
            ""
        }
    }
}
