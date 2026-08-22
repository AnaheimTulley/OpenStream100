package org.openstream100.remote

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.util.UUID

private val AppBackground = Color(0xFF080B10)
private val PanelBackground = Color(0xE6191F2A)
private val MutedRed = Color(0xFFF54D5B)

private data class VolumeUpdate(val page: Int, val channel: Int, val level: Float)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                OpenStream100App(this@MainActivity)
            }
        }
    }
}

@Composable
private fun OpenStream100App(activity: Activity) {
    val context = activity.applicationContext
    val localNetworkPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { }
    LaunchedEffect(Unit) {
        if (
            Build.VERSION.SDK_INT >= 37 &&
            activity.checkSelfPermission(Manifest.permission.ACCESS_LOCAL_NETWORK) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            localNetworkPermission.launch(Manifest.permission.ACCESS_LOCAL_NETWORK)
        }
    }
    val preferences = remember {
        context.getSharedPreferences("openstream100-remote", Context.MODE_PRIVATE)
    }
    var settings by remember {
        mutableStateOf(
            RemoteSettings(
                preferences.getString("server", "").orEmpty(),
                preferences.getString("token", "").orEmpty(),
                preferences.getString("fingerprint", "").orEmpty(),
            )
        )
    }
    var showingPairing by remember {
        mutableStateOf(settings.server.isBlank() || settings.token.isBlank())
    }

    Surface(color = AppBackground, modifier = Modifier.fillMaxSize()) {
        if (showingPairing || settings.server.isBlank() || settings.token.isBlank()) {
            PairingScreen(
                activity = activity,
                existingSettings = settings.takeIf {
                    it.server.isNotBlank() && it.token.isNotBlank()
                },
                onPair = { paired ->
                    preferences.edit()
                    .putString("server", paired.server)
                    .putString("token", paired.token)
                    .putString("fingerprint", paired.fingerprint)
                    .commit()
                    settings = paired
                    showingPairing = false
                },
                onReturn = { showingPairing = false },
                onForget = {
                    preferences.edit()
                        .remove("server")
                        .remove("token")
                        .remove("fingerprint")
                        .commit()
                    settings = RemoteSettings("", "", "")
                    showingPairing = true
                },
            )
        } else {
            MixerScreen(
                settings = settings,
                updateServer = { address ->
                    val updated = settings.copy(server = address)
                    preferences.edit().putString("server", address).commit()
                    settings = updated
                },
                managePairing = { showingPairing = true },
            )
        }
    }
}

@Composable
private fun PairingScreen(
    activity: Activity,
    existingSettings: RemoteSettings?,
    onPair: (RemoteSettings) -> Unit,
    onReturn: () -> Unit,
    onForget: () -> Unit,
) {
    var server by remember { mutableStateOf(existingSettings?.server.orEmpty()) }
    var token by remember { mutableStateOf("") }
    var pairingPin by remember { mutableStateOf("") }
    var pinPairingSelected by remember { mutableStateOf(false) }
    var pairingBusy by remember { mutableStateOf(false) }
    var pairingError by remember { mutableStateOf<String?>(null) }
    var pairingNotice by remember { mutableStateOf<String?>(null) }
    var discovered by remember { mutableStateOf<List<DiscoveredServer>>(emptyList()) }
    val scope = rememberCoroutineScope()
    val devicePreferences = remember(activity) {
        activity.getSharedPreferences("openstream100-remote", Context.MODE_PRIVATE)
    }
    val deviceId = remember(devicePreferences) {
        devicePreferences.getString("device_id", null) ?: UUID.randomUUID().toString().also {
            devicePreferences.edit().putString("device_id", it).commit()
        }
    }
    val deviceName = remember {
        listOf(Build.MANUFACTURER, Build.MODEL)
            .filter { it.isNotBlank() }
            .joinToString(" ")
            .ifBlank { "Android phone" }
    }
    val scanner = remember(activity) {
        val options = GmsBarcodeScannerOptions.Builder()
            .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
            .enableAutoZoom()
            .build()
        GmsBarcodeScanning.getClient(activity, options)
    }
    val requestPin: (String) -> Unit = { address ->
        server = address
        pairingPin = ""
        pinPairingSelected = true
        pairingError = null
        pairingNotice = "Requesting a pairing PIN from the computer…"
        scope.launch {
            pairingBusy = true
            runCatching {
                RemoteApi.requestPinPairing(address, deviceId, deviceName)
            }.onSuccess { expires ->
                pairingNotice =
                    "The pairing PIN is now displayed in OpenStream100 on the computer " +
                        "and expires in about $expires seconds."
            }.onFailure {
                pairingNotice =
                    "On the computer choose Pair new phone, then enter the PIN shown there."
                pairingError = it.message ?: "Automatic PIN request failed."
            }
            pairingBusy = false
        }
    }
    val scanPairingQr: () -> Unit = {
        scanner.startScan()
            .addOnSuccessListener { barcode ->
                runCatching {
                    RemoteApi.parsePairingUri(barcode.rawValue.orEmpty())
                }.onSuccess {
                    server = it.server
                    token = it.token
                    pairingError = null
                    onPair(it)
                }.onFailure {
                    pairingError = it.message ?: "Invalid pairing QR."
                }
            }
            .addOnFailureListener {
                pairingError = it.message ?: "QR scanner is unavailable."
            }
    }
    DisposableEffect(activity) {
        val discovery = OpenStreamDiscovery(activity) { found ->
            activity.runOnUiThread {
                discovered = (discovered.filterNot { it.address == found.address } + found)
                    .sortedBy { it.name }
            }
        }
        runCatching { discovery.start() }
            .onFailure { pairingError = "Automatic discovery could not start." }
        onDispose { discovery.stop() }
    }
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 56.dp, vertical = 24.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            "OpenStream100 Remote",
            color = Color.White,
            fontSize = 28.sp,
            fontWeight = FontWeight.Bold,
        )
        Text(
            "Choose a discovered mixer, scan its pairing QR, or enter the details manually.",
            color = Color(0xFF9AA8B8),
            modifier = Modifier.padding(top = 6.dp, bottom = 16.dp),
        )
        if (discovered.isNotEmpty()) {
            Row(
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                modifier = Modifier.padding(bottom = 12.dp),
            ) {
                discovered.forEach { found ->
                    OutlinedButton(
                        onClick = {
                            server = found.address
                            val saved = existingSettings
                            val matchingSavedMixer = saved != null && (
                                saved.fingerprint.isNotBlank() &&
                                    saved.fingerprint == found.fingerprint
                                )
                            if (matchingSavedMixer) {
                                pairingBusy = true
                                pairingNotice = "Reconnecting with the saved phone credential…"
                                pairingError = null
                                scope.launch {
                                    val candidate = saved.copy(server = found.address)
                                    runCatching { RemoteApi(candidate).state() }
                                        .onSuccess { onPair(candidate) }
                                        .onFailure {
                                            pairingError =
                                                "The saved credential was not accepted. Pair again with a new PIN."
                                            requestPin(found.address)
                                        }
                                    pairingBusy = false
                                }
                            } else {
                                requestPin(found.address)
                            }
                        },
                    ) {
                        Text(
                            if (found.fingerprint.isBlank()) found.name
                            else "${found.name} · ${found.fingerprint}",
                            maxLines = 1,
                        )
                    }
                }
            }
        }
        if (pinPairingSelected) {
            Text(
                pairingNotice
                    ?: "Enter the six-digit PIN shown by OpenStream100 on the computer.",
                color = Color(0xFF9AA8B8),
                modifier = Modifier.padding(bottom = 8.dp),
            )
            Row(
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.padding(bottom = 12.dp),
            ) {
                OutlinedTextField(
                    value = pairingPin,
                    onValueChange = { value ->
                        pairingPin = value.filter(Char::isDigit).take(6)
                        pairingError = null
                    },
                    label = { Text("Pairing PIN") },
                    placeholder = { Text("123456") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.weight(1f),
                )
                Button(
                    onClick = {
                        scope.launch {
                            pairingBusy = true
                            runCatching {
                                RemoteApi.pairWithPin(
                                    server = server,
                                    pin = pairingPin,
                                    deviceId = deviceId,
                                    deviceName = deviceName,
                                )
                            }.onSuccess {
                                pairingError = null
                                onPair(it)
                            }.onFailure {
                                pairingError = it.message ?: "PIN pairing failed."
                            }
                            pairingBusy = false
                        }
                    },
                    enabled = pairingPin.length == 6 && !pairingBusy,
                ) {
                    Text(if (pairingBusy) "Pairing…" else "Pair phone")
                }
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
            OutlinedTextField(
                value = server,
                onValueChange = {
                    server = it
                    pinPairingSelected = false
                },
                label = { Text("Computer address") },
                placeholder = { Text("192.168.1.20:47680") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.weight(1f),
            )
            OutlinedTextField(
                value = token,
                onValueChange = { token = it },
                label = { Text("Pairing token") },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
        }
        Row(
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            modifier = Modifier.padding(top = 16.dp),
        ) {
            Button(
                onClick = { onPair(RemoteSettings(server.trim(), token.trim())) },
                enabled = server.isNotBlank() && token.isNotBlank(),
            ) {
                Text("Open mixer")
            }
            OutlinedButton(
                onClick = scanPairingQr,
            ) {
                Text("Scan QR fallback")
            }
            if (existingSettings != null) {
                OutlinedButton(onClick = onReturn) { Text("Return to mixer") }
                OutlinedButton(onClick = onForget) { Text("Forget saved pairing") }
            }
        }
        if (pairingError != null) {
            Text(pairingError.orEmpty(), color = MutedRed, modifier = Modifier.padding(top = 8.dp))
        }
    }
}

@Composable
private fun MixerScreen(
    settings: RemoteSettings,
    updateServer: (String) -> Unit,
    managePairing: () -> Unit,
) {
    val api = remember(settings) { RemoteApi(settings) }
    val scope = rememberCoroutineScope()
    val context = androidx.compose.ui.platform.LocalContext.current
    val volumeQueues = remember(api) {
        List(4) { Channel<VolumeUpdate>(capacity = Channel.CONFLATED) }
    }
    var state by remember { mutableStateOf<MixerState?>(null) }
    var connectionError by remember { mutableStateOf<String?>(null) }

    DisposableEffect(settings.fingerprint, settings.server) {
        if (settings.fingerprint.isBlank()) {
            onDispose { }
        } else {
            val discovery = OpenStreamDiscovery(context) { found ->
                if (
                    found.fingerprint == settings.fingerprint &&
                    found.address != settings.server
                ) {
                    scope.launch { updateServer(found.address) }
                }
            }
            runCatching { discovery.start() }
            onDispose { discovery.stop() }
        }
    }

    DisposableEffect(volumeQueues) {
        onDispose { volumeQueues.forEach { it.close() } }
    }
    LaunchedEffect(api, volumeQueues) {
        coroutineScope {
            volumeQueues.forEach { queue ->
                launch {
                    for (update in queue) {
                        runCatching {
                            api.setVolume(update.page, update.channel, update.level)
                        }
                        delay(45)
                    }
                }
            }
        }
    }

    LaunchedEffect(api) {
        while (isActive) {
            runCatching { api.state() }
                .onSuccess {
                    state = it
                    connectionError = null
                }
                .onFailure { connectionError = it.message ?: "Connection failed." }
            delay(125)
        }
    }

    val current = state
    if (current == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                CircularProgressIndicator()
                Text(
                    connectionError ?: "Connecting to OpenStream100…",
                    color = if (connectionError == null) Color.White else MutedRed,
                    modifier = Modifier.padding(12.dp),
                )
                if (connectionError != null) {
                    OutlinedButton(onClick = managePairing) { Text("Find paired mixer") }
                }
            }
        }
        return
    }

    Column(Modifier.fillMaxSize().padding(10.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        MixerHeader(
            state = current,
            connected = connectionError == null,
            previousPage = {
                scope.launch {
                    api.selectPage((current.page - 1 + current.pageCount) % current.pageCount)
                }
            },
            nextPage = {
                scope.launch { api.selectPage((current.page + 1) % current.pageCount) }
            },
            managePairing = managePairing,
        )
        Row(
            modifier = Modifier.weight(1f).fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            current.channels.forEach { channel ->
                ChannelStrip(
                    channel = channel,
                    modifier = Modifier.weight(1f).fillMaxHeight(),
                    setVolume = { level ->
                        volumeQueues[channel.index].trySend(
                            VolumeUpdate(current.page, channel.index, level)
                        )
                    },
                    toggleMute = {
                        scope.launch { api.toggleMute(current.page, channel.index) }
                    },
                    loadIcon = api::icon,
                )
            }
        }
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            current.actions.forEach { action ->
                OutlinedButton(
                    onClick = { scope.launch { api.pressButton(current.page, action.index) } },
                    enabled = action.id != "disabled",
                    modifier = Modifier.weight(1f).height(42.dp),
                ) {
                    Text(action.label, maxLines = 1, fontSize = 12.sp)
                }
            }
        }
    }
}

@Composable
private fun MixerHeader(
    state: MixerState,
    connected: Boolean,
    previousPage: () -> Unit,
    nextPage: () -> Unit,
    managePairing: () -> Unit,
) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Image(
            painter = painterResource(R.drawable.ic_launcher_foreground),
            contentDescription = null,
            modifier = Modifier.size(30.dp).padding(end = 4.dp),
        )
        Text(
            "OpenStream100",
            color = Color.White,
            fontWeight = FontWeight.Bold,
            fontSize = 19.sp,
        )
        Text(
            if (connected) "  ● Connected" else "  ● Reconnecting",
            color = if (connected) Color(0xFF36D380) else Color(0xFFF6BE40),
            fontSize = 12.sp,
        )
        Spacer(Modifier.weight(1f))
        OutlinedButton(onClick = previousPage, enabled = state.pageCount > 1) { Text("‹") }
        Text(
            "Page ${state.page + 1} / ${state.pageCount}",
            color = Color.White,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(horizontal = 12.dp),
        )
        OutlinedButton(onClick = nextPage, enabled = state.pageCount > 1) { Text("›") }
        OutlinedButton(onClick = managePairing, modifier = Modifier.padding(start = 8.dp)) {
            Text("Pairing")
        }
    }
}

@Composable
private fun ChannelStrip(
    channel: MixerChannel,
    modifier: Modifier,
    setVolume: (Float) -> Unit,
    toggleMute: () -> Unit,
    loadIcon: suspend (String) -> ByteArray,
) {
    val accent = remember(channel.colour) {
        runCatching { Color(android.graphics.Color.parseColor(channel.colour)) }
            .getOrDefault(Color(0xFF5B82F6))
    }
    Column(
        modifier = modifier
            .background(PanelBackground, RoundedCornerShape(12.dp))
            .padding(8.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(Modifier.fillMaxWidth().height(4.dp).background(accent, RoundedCornerShape(3.dp)))
        RemoteChannelIcon(channel, loadIcon)
        Text(
            channel.label,
            color = Color.White,
            fontWeight = FontWeight.SemiBold,
            textAlign = TextAlign.Center,
            maxLines = 1,
            modifier = Modifier.padding(top = 5.dp),
        )
        Text(
            if (channel.available) "${(channel.level * 100).toInt()}%" else "Waiting for audio",
            color = if (channel.available) Color(0xFFD8E0EA) else Color(0xFF7F8B99),
            fontSize = 12.sp,
        )
        VerticalMixerControl(
            channel = channel,
            accent = accent,
            onCommit = setVolume,
            modifier = Modifier.weight(1f).fillMaxWidth().padding(vertical = 4.dp),
        )
        Button(
            onClick = toggleMute,
            enabled = channel.available,
            colors = ButtonDefaults.buttonColors(
                containerColor = if (channel.muted) MutedRed else Color(0xFF303A48),
            ),
            modifier = Modifier.fillMaxWidth().height(38.dp),
        ) {
            Text(if (channel.muted) "UNMUTE" else "MUTE", fontSize = 11.sp)
        }
    }
}

@Composable
private fun RemoteChannelIcon(
    channel: MixerChannel,
    loadIcon: suspend (String) -> ByteArray,
) {
    var bitmap by remember(channel.iconPath) { mutableStateOf<androidx.compose.ui.graphics.ImageBitmap?>(null) }
    LaunchedEffect(channel.iconPath) {
        bitmap = channel.iconPath?.let { path ->
            runCatching {
                val bytes = loadIcon(path)
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
            }.getOrNull()
        }
    }
    if (bitmap != null) {
        Image(
            bitmap = bitmap!!,
            contentDescription = "${channel.label} icon",
            modifier = Modifier.size(32.dp).padding(top = 3.dp),
        )
    } else {
        Box(
            contentAlignment = Alignment.Center,
            modifier = Modifier
                .size(32.dp)
                .padding(top = 3.dp)
                .background(Color(0xFF303A48), CircleShape),
        ) {
            Text(
                text = (channel.index + 1).toString(),
                color = Color(0xFF9AA8B8),
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
            )
        }
    }
}

@Composable
private fun VerticalMixerControl(
    channel: MixerChannel,
    accent: Color,
    onCommit: (Float) -> Unit,
    modifier: Modifier = Modifier,
) {
    var localLevel by remember(channel.index) { mutableFloatStateOf(channel.level) }
    var dragging by remember(channel.index) { mutableStateOf(false) }
    var pendingLevel by remember(channel.index) { mutableStateOf<Float?>(null) }
    LaunchedEffect(channel.level, dragging, pendingLevel) {
        if (dragging) return@LaunchedEffect
        val pending = pendingLevel
        if (pending == null || kotlin.math.abs(channel.level - pending) < 0.015f) {
            localLevel = channel.level
            pendingLevel = null
        } else {
            delay(900)
            if (!dragging) {
                localLevel = channel.level
                pendingLevel = null
            }
        }
    }

    Canvas(
        modifier = modifier.pointerInput(channel.index, channel.available) {
            if (!channel.available) return@pointerInput
            detectVerticalDragGestures(
                onDragStart = { offset ->
                    dragging = true
                    pendingLevel = null
                    localLevel = (1f - offset.y / size.height).coerceIn(0f, 1f)
                    onCommit(localLevel)
                },
                onVerticalDrag = { change, _ ->
                    change.consume()
                    localLevel = (1f - change.position.y / size.height).coerceIn(0f, 1f)
                    onCommit(localLevel)
                },
                onDragEnd = {
                    dragging = false
                    pendingLevel = localLevel
                    onCommit(localLevel)
                },
                onDragCancel = { dragging = false },
            )
        }
    ) {
        val centre = size.width / 2f
        val trackWidth = size.width.coerceAtMost(64.dp.toPx())
        val trackLeft = centre - trackWidth / 2f
        val gap = 4.dp.toPx()
        val barWidth = (trackWidth - gap) / 2f
        drawRoundRect(
            color = Color(0xFF252D39),
            topLeft = Offset(trackLeft, 0f),
            size = Size(trackWidth, size.height),
        )
        if (!channel.muted) {
            val leftHeight = channel.meterLeft * size.height
            val rightHeight = channel.meterRight * size.height
            drawRoundRect(
                color = accent.copy(alpha = 0.82f),
                topLeft = Offset(trackLeft, size.height - leftHeight),
                size = Size(barWidth, leftHeight),
            )
            drawRoundRect(
                color = accent.copy(alpha = 0.82f),
                topLeft = Offset(trackLeft + barWidth + gap, size.height - rightHeight),
                size = Size(barWidth, rightHeight),
            )
        }
        val markerY = (1f - localLevel) * size.height
        drawLine(
            color = Color.White,
            start = Offset(trackLeft - 5.dp.toPx(), markerY),
            end = Offset(trackLeft + trackWidth + 5.dp.toPx(), markerY),
            strokeWidth = 4.dp.toPx(),
            cap = StrokeCap.Round,
        )
    }
}
