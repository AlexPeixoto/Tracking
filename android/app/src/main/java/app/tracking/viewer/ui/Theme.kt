package app.tracking.viewer.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.tracking.viewer.data.Category
import coil.compose.AsyncImage
import java.io.File

// The desktop app's dark palette.
val Background = Color(0xFF14161B)
val Surface = Color(0xFF1B1E25)
val SurfaceHigh = Color(0xFF242832)
val Accent = Color(0xFF5B8CFF)
val Muted = Color(0xFF8A8F9C)
val Done = Color(0xFF2BB673)

private val colors = darkColorScheme(
    primary = Accent,
    onPrimary = Color.White,
    secondary = Accent,
    background = Background,
    onBackground = Color(0xFFE6E8EE),
    surface = Surface,
    onSurface = Color(0xFFE6E8EE),
    surfaceVariant = SurfaceHigh,
    onSurfaceVariant = Muted,
    surfaceContainer = Surface,
    surfaceContainerHigh = SurfaceHigh,
    surfaceContainerHighest = SurfaceHigh,
    outline = Color(0xFF2A2E38),
)

@Composable
fun TrackingTheme(content: @Composable () -> Unit) = MaterialTheme(colorScheme = colors, content = content)

fun hex(s: String): Color = runCatching { Color(android.graphics.Color.parseColor(s)) }.getOrDefault(Muted)

/** A cover from the package, or a card in the section's colours with the title on it. */
@Composable
fun Cover(dir: File, cover: String?, title: String, category: Category?, modifier: Modifier = Modifier) {
    val shape = RoundedCornerShape(8.dp)
    Box(modifier.aspectRatio(2f / 3f).clip(shape)) {
        if (cover != null) {
            AsyncImage(
                model = File(dir, cover),
                contentDescription = title,
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize(),
            )
        } else {
            val (top, bottom) = (category?.colors ?: listOf("#6b7280", "#262a33")).let {
                hex(it.getOrElse(0) { "#6b7280" }) to hex(it.getOrElse(1) { "#262a33" })
            }
            Box(
                Modifier.fillMaxSize().background(Brush.verticalGradient(listOf(top, bottom))).padding(8.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(title, color = Color.White, fontWeight = FontWeight.SemiBold, fontSize = 13.sp,
                     textAlign = TextAlign.Center, maxLines = 5, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

/** Score on 0-100 as a coloured chip: green good, amber fine, red poor. */
@Composable
fun ScoreBadge(percent: Double, modifier: Modifier = Modifier) {
    val color = when {
        percent >= 75 -> Color(0xFF2BB673)
        percent >= 60 -> Color(0xFFE0A33A)
        else -> Color(0xFFD9534F)
    }
    Box(modifier.clip(RoundedCornerShape(6.dp)).background(color).padding(horizontal = 6.dp, vertical = 2.dp)) {
        Text("${percent.toInt()}", color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold)
    }
}

fun hours(h: Double?): String? = h?.let { if (it >= 10) "${it.toInt()} h" else "%.1f h".format(it) }

fun scoreText(value: Double, max: Double): String =
    if (max == 100.0) "${value.toInt()}%" else if (value % 1.0 == 0.0) "${value.toInt()}/${max.toInt()}" else
        "%.1f/%s".format(value, if (max % 1.0 == 0.0) max.toInt().toString() else max.toString())
