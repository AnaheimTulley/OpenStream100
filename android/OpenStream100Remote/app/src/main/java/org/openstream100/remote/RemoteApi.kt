package org.openstream100.remote

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import android.net.Uri
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

const val PROTOCOL_VERSION = 1

data class RemoteSettings(
    val server: String,
    val token: String,
    val fingerprint: String = "",
)

data class MixerChannel(
    val index: Int,
    val label: String,
    val colour: String,
    val available: Boolean,
    val muted: Boolean,
    val level: Float,
    val meterLeft: Float,
    val meterRight: Float,
    val iconPath: String?,
)

data class MixerAction(
    val index: Int,
    val id: String,
    val label: String,
)

data class MixerState(
    val revision: Long,
    val page: Int,
    val pageCount: Int,
    val channels: List<MixerChannel>,
    val actions: List<MixerAction>,
)

class RemoteApi(settings: RemoteSettings) {
    private val baseUrl = normaliseServer(settings.server)
    private val token = settings.token.trim()

    suspend fun state(): MixerState = withContext(Dispatchers.IO) {
        val payload = request("GET", "/state")
        if (payload.optInt("protocol") != PROTOCOL_VERSION) {
            error("The computer uses an unsupported remote protocol.")
        }
        val channelsJson = payload.getJSONArray("channels")
        val channels = buildList {
            for (index in 0 until channelsJson.length()) {
                val item = channelsJson.getJSONObject(index)
                add(
                    MixerChannel(
                        index = item.getInt("index"),
                        label = item.optString("label", "Disabled"),
                        colour = item.optString("color", "#5B82F6"),
                        available = item.optBoolean("available"),
                        muted = item.optBoolean("muted"),
                        level = item.optDouble("level").toFloat().coerceIn(0f, 1f),
                        meterLeft = item.optDouble("meter_left").toFloat().coerceIn(0f, 1f),
                        meterRight = item.optDouble("meter_right").toFloat().coerceIn(0f, 1f),
                        iconPath = item.optString("icon").takeIf { it.isNotBlank() },
                    )
                )
            }
        }
        val actionsJson = payload.getJSONArray("actions")
        val actions = buildList {
            for (index in 0 until actionsJson.length()) {
                val item = actionsJson.getJSONObject(index)
                add(
                    MixerAction(
                        index = item.getInt("index"),
                        id = item.optString("id", "disabled"),
                        label = item.optString("label", "Disabled"),
                    )
                )
            }
        }
        MixerState(
            revision = payload.optLong("revision"),
            page = payload.optInt("page"),
            pageCount = payload.optInt("page_count", 1).coerceAtLeast(1),
            channels = channels,
            actions = actions,
        )
    }

    suspend fun setVolume(page: Int, channel: Int, level: Float) {
        command("set_volume", page, channel, level.coerceIn(0f, 1f))
    }

    suspend fun toggleMute(page: Int, channel: Int) {
        command("toggle_mute", page, channel)
    }

    suspend fun selectPage(page: Int) {
        command("select_page", page)
    }

    suspend fun pressButton(page: Int, button: Int) {
        command("press_button", page, button)
    }

    suspend fun icon(path: String): ByteArray = withContext(Dispatchers.IO) {
        requestBytes("GET", path)
    }

    private suspend fun command(
        name: String,
        page: Int,
        channel: Int? = null,
        value: Float? = null,
    ) = withContext(Dispatchers.IO) {
        val payload = JSONObject()
            .put("protocol", PROTOCOL_VERSION)
            .put("request_id", UUID.randomUUID().toString())
            .put("command", name)
            .put("page", page)
        if (channel != null) payload.put("channel", channel)
        if (value != null) payload.put("value", value.toDouble())
        request("POST", "/command", payload)
    }

    private fun request(method: String, path: String, body: JSONObject? = null): JSONObject {
        return JSONObject(requestBytes(method, path, body).toString(Charsets.UTF_8))
    }

    private fun requestBytes(
        method: String,
        path: String,
        body: JSONObject? = null,
    ): ByteArray {
        val endpoint = if (path.startsWith("/api/")) {
            "$baseUrl$path"
        } else {
            "$baseUrl/api/v1$path"
        }
        val connection = URL(endpoint).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = 1_500
            connection.readTimeout = 1_500
            connection.setRequestProperty("Accept", "application/json")
            connection.setRequestProperty("Authorization", "Bearer $token")
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(body.toString().toByteArray()) }
            }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val bytes = stream?.use { input ->
                ByteArrayOutputStream().use { output ->
                    input.copyTo(output)
                    output.toByteArray()
                }
            } ?: byteArrayOf()
            if (status !in 200..299) {
                val message = runCatching {
                    JSONObject(bytes.toString(Charsets.UTF_8)).optString("error")
                }.getOrNull()
                error(message?.takeIf { it.isNotBlank() } ?: "Computer returned HTTP $status.")
            }
            return bytes
        } finally {
            connection.disconnect()
        }
    }

    companion object {
        suspend fun requestPinPairing(
            server: String,
            deviceId: String,
            deviceName: String,
        ): Int = withContext(Dispatchers.IO) {
            val baseUrl = normaliseServer(server)
            val payload = JSONObject()
                .put("protocol", PROTOCOL_VERSION)
                .put("device_id", deviceId)
                .put("device_name", deviceName)
            val connection = URL("$baseUrl/api/v1/pair/request")
                .openConnection() as HttpURLConnection
            try {
                connection.requestMethod = "POST"
                connection.connectTimeout = 2_000
                connection.readTimeout = 2_000
                connection.doOutput = true
                connection.setRequestProperty("Accept", "application/json")
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { output ->
                    output.write(payload.toString().toByteArray())
                }
                val status = connection.responseCode
                val stream = if (status in 200..299) {
                    connection.inputStream
                } else {
                    connection.errorStream
                }
                val response = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
                val document = runCatching { JSONObject(response) }.getOrElse {
                    error("The computer returned an invalid pairing response.")
                }
                if (status !in 200..299) {
                    error(document.optString("error").ifBlank { "Could not request a PIN." })
                }
                document.optJSONObject("pairing")?.optInt("expires_in", 120) ?: 120
            } finally {
                connection.disconnect()
            }
        }

        suspend fun pairWithPin(
            server: String,
            pin: String,
            deviceId: String,
            deviceName: String,
        ): RemoteSettings = withContext(Dispatchers.IO) {
            val cleanPin = pin.filter(Char::isDigit)
            require(cleanPin.length == 6) { "Enter the six-digit PIN shown on the computer." }
            val baseUrl = normaliseServer(server)
            val payload = JSONObject()
                .put("protocol", PROTOCOL_VERSION)
                .put("pin", cleanPin)
                .put("device_id", deviceId)
                .put("device_name", deviceName)
            val connection = URL("$baseUrl/api/v1/pair/complete")
                .openConnection() as HttpURLConnection
            try {
                connection.requestMethod = "POST"
                connection.connectTimeout = 2_000
                connection.readTimeout = 2_000
                connection.doOutput = true
                connection.setRequestProperty("Accept", "application/json")
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { output ->
                    output.write(payload.toString().toByteArray())
                }
                val status = connection.responseCode
                val stream = if (status in 200..299) {
                    connection.inputStream
                } else {
                    connection.errorStream
                }
                val response = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
                val document = runCatching { JSONObject(response) }.getOrElse {
                    error("The computer returned an invalid pairing response.")
                }
                if (status !in 200..299) {
                    error(document.optString("error").ifBlank { "Pairing failed." })
                }
                val token = document.optString("token")
                require(token.length >= 32) { "The computer did not return a valid device token." }
                RemoteSettings(
                    server = baseUrl,
                    token = token,
                    fingerprint = document.optString("token_fingerprint"),
                )
            } finally {
                connection.disconnect()
            }
        }

        fun normaliseServer(value: String): String {
            val trimmed = value.trim().trimEnd('/')
            require(trimmed.isNotBlank()) { "Enter the computer address." }
            return if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
                trimmed
            } else {
                "http://$trimmed"
            }
        }

        fun parsePairingUri(value: String): RemoteSettings {
            val uri = Uri.parse(value.trim())
            require(uri.scheme == "openstream100" && uri.host == "pair") {
                "This is not an OpenStream100 pairing code."
            }
            require(uri.getQueryParameter("protocol") == PROTOCOL_VERSION.toString()) {
                "The pairing code uses an unsupported protocol."
            }
            val server = uri.getQueryParameter("server").orEmpty()
            val token = uri.getQueryParameter("token").orEmpty()
            val fingerprint = uri.getQueryParameter("fingerprint").orEmpty()
            require(server.isNotBlank() && token.length >= 32) {
                "The pairing code is incomplete."
            }
            return RemoteSettings(server, token, fingerprint)
        }
    }
}
