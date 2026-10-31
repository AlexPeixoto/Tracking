package app.tracking.viewer.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.tracking.viewer.data.Item
import app.tracking.viewer.data.Library
import app.tracking.viewer.data.StoredLibrary

private val PLAN_LABELS = mapOf("next" to "Up next", "playing" to "Playing", "hold" to "On hold", "dropped" to "Dropped")
private val SOURCE_LABELS = mapOf(
    "disk" to "On a drive", "steam" to "In the Steam library", "device" to "On an Android device",
    "manual" to "Added by hand", "wish" to "Want to acquire",
)

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ItemScreen(lib: StoredLibrary, library: Library, item: Item) {
    val category = library.categories.firstOrNull { it.key == item.category }
    val context = LocalContext.current
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)) {
        Row {
            Cover(lib.dir, item.cover, item.title, category, Modifier.width(140.dp))
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(item.title, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                val meta = listOfNotNull(
                    category?.singular,
                    item.kind?.let { if (it == "movie") "Movie" else if (it == "series") "Series · season ${item.season ?: 1}" else null },
                    item.platform, item.year?.toString(),
                ).joinToString(" · ")
                Text(meta, color = Muted, fontSize = 13.sp)
                Spacer(Modifier.height(8.dp))
                if (item.completed) {
                    val word = category?.done ?: "Completed"
                    Text("✓ $word" + (item.completedAt?.let { " ${it.take(10)}" } ?: ""), color = Done,
                         fontWeight = FontWeight.SemiBold)
                }
                item.plan?.let { Text("Planning: ${PLAN_LABELS[it] ?: it}", color = Accent, fontSize = 13.sp) }
                val where = item.store?.let { "Played on $it" } ?: SOURCE_LABELS[item.source]
                where?.let { Text(it, color = Muted, fontSize = 13.sp) }
                item.averageScore?.let {
                    Spacer(Modifier.height(8.dp))
                    Row { ScoreBadge(it); Text("  average of ${item.scores.size}", color = Muted, fontSize = 12.sp) }
                }
            }
        }

        if (item.scores.isNotEmpty()) {
            Section("Scores")
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                item.scores.forEach { s ->
                    AssistChip(onClick = { s.url?.let { open(context, it) } },
                               label = { Text("${s.source}  ${scoreText(s.value, s.max)}") })
                }
            }
        }

        val times = listOfNotNull(
            hours(item.hltb?.main)?.let { "Main story" to it },
            hours(item.hltb?.extra)?.let { "Main + extras" to it },
            hours(item.hltb?.complete)?.let { "Completionist" to it },
            item.durationMin?.let { "Running time" to (hours(it / 60) ?: "") },
            item.playtimeMin?.takeIf { it > 0 }?.let { "Played" to (hours(it / 60) ?: "") },
        )
        if (times.isNotEmpty()) {
            Section("Time")
            FlowRow(horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                times.forEach { (label, value) ->
                    Column(Modifier.padding(bottom = 6.dp)) {
                        Text(value, fontSize = 18.sp, fontWeight = FontWeight.SemiBold)
                        Text(label, color = Muted, fontSize = 12.sp)
                    }
                }
            }
        }

        item.summary?.let {
            Section("Summary")
            Text(it, fontSize = 14.sp, lineHeight = 20.sp)
        }

        if (item.episodes.isNotEmpty()) {
            val owned = item.episodes.count { it.owned }
            Section("Episodes  ·  $owned of ${item.episodes.size} in the library")
            Card(colors = CardDefaults.cardColors(containerColor = Surface), shape = RoundedCornerShape(8.dp)) {
                item.episodes.forEachIndexed { n, e ->
                    if (n > 0) HorizontalDivider(color = SurfaceHigh)
                    Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp)) {
                        Text("${e.number}", color = if (e.owned) Accent else Muted, modifier = Modifier.width(34.dp))
                        Text(e.title ?: "Episode ${e.number}", fontSize = 14.sp, modifier = Modifier.weight(1f),
                             color = if (e.owned) androidx.compose.ui.graphics.Color.Unspecified else Muted)
                        e.score?.let { s -> Text(scoreText(s, e.max ?: 10.0), color = Muted, fontSize = 12.sp) }
                    }
                }
            }
        }

        item.url?.let {
            Spacer(Modifier.height(16.dp))
            OutlinedButton(onClick = { open(context, it) }) { Text("Open web page") }
        }
        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun Section(title: String) {
    Spacer(Modifier.height(18.dp))
    Text(title.uppercase(), color = Muted, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
         modifier = Modifier.padding(bottom = 6.dp))
}

private fun open(context: android.content.Context, url: String) {
    runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
}
