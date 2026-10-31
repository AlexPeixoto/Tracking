package app.tracking.viewer.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.tracking.viewer.data.Completion
import app.tracking.viewer.data.Item
import app.tracking.viewer.data.Library
import app.tracking.viewer.data.StoredLibrary
import java.time.LocalDate

/** The Completed log: one year at a time, in the order you put them on the desktop. */
@Composable
fun CompletedScreen(lib: StoredLibrary, library: Library, onOpen: (Int) -> Unit) {
    val years = remember(library) { library.completions.mapNotNull { it.year }.distinct().sortedDescending() }
    if (years.isEmpty()) {
        Empty("Nothing finished yet in this library.")
        return
    }
    var year by rememberSaveable { mutableStateOf(years.first()) }
    val categories = remember(library) { library.categories.associateBy { it.key } }
    val entries = remember(library, year) {
        library.completions.filter { it.year == year }
            .sortedWith(compareBy<Completion, Int?>(nullsLast()) { it.position }.thenBy { it.date })
    }
    Column(Modifier.fillMaxSize()) {
        LazyRow(contentPadding = PaddingValues(12.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            items(years) { y ->
                val n = library.completions.count { it.year == y }
                FilterChip(selected = y == year, onClick = { year = y }, label = { Text("$y  ($n)") })
            }
        }
        val byCategory = entries.groupingBy { categories[it.category]?.label ?: it.category }.eachCount()
        Text(byCategory.entries.joinToString("  ·  ") { "${it.value} ${it.key.lowercase()}" },
             color = Muted, fontSize = 13.sp, modifier = Modifier.padding(horizontal = 12.dp))
        CoverGrid(entries.map { Tile(it.title, it.cover, it.category, it.item) }, lib, library, onOpen)
    }
}

/** The Planning board: Up next / Playing / On hold, and what you finished this year. */
@Composable
fun PlanningScreen(lib: StoredLibrary, library: Library, onOpen: (Int) -> Unit) {
    val thisYear = LocalDate.now().year
    val columns = remember(library) {
        listOf(
            "Up next" to library.items.filter { it.plan == "next" }
                .sortedWith(compareBy<Item, Int?>(nullsLast()) { it.planPosition }).map(::tile),
            "Playing" to library.items.filter { it.plan == "playing" }.map(::tile),
            "On hold" to library.items.filter { it.plan == "hold" }.map(::tile),
            "Done this year" to library.completions.filter { it.year == thisYear }
                .map { Tile(it.title, it.cover, it.category, it.item) },
        ).filter { it.second.isNotEmpty() }
    }
    if (columns.isEmpty()) {
        Empty("Nothing on the Planning board.")
        return
    }
    var column by rememberSaveable { mutableStateOf(columns.first().first) }
    val shown = columns.firstOrNull { it.first == column } ?: columns.first()
    Column(Modifier.fillMaxSize()) {
        LazyRow(contentPadding = PaddingValues(12.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            items(columns) { (name, tiles) ->
                FilterChip(selected = name == shown.first, onClick = { column = name },
                           label = { Text("$name  (${tiles.size})") })
            }
        }
        CoverGrid(shown.second, lib, library, onOpen)
    }
}

data class Tile(val title: String, val cover: String?, val category: String, val itemId: Int?)

private fun tile(i: Item) = Tile(i.title, i.cover, i.category, i.id)

@Composable
private fun CoverGrid(tiles: List<Tile>, lib: StoredLibrary, library: Library, onOpen: (Int) -> Unit) {
    val categories = remember(library) { library.categories.associateBy { it.key } }
    LazyVerticalGrid(
        columns = GridCells.Adaptive(108.dp),
        contentPadding = PaddingValues(12.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        modifier = Modifier.fillMaxSize(),
    ) {
        items(tiles) { t ->
            val click = t.itemId?.let { id -> Modifier.clickable { onOpen(id) } } ?: Modifier
            Column(click) {
                Cover(lib.dir, t.cover, t.title, categories[t.category], Modifier.fillMaxWidth())
                Text(t.title, fontSize = 13.sp, maxLines = 2, overflow = TextOverflow.Ellipsis,
                     modifier = Modifier.padding(top = 4.dp))
            }
        }
    }
}

@Composable
fun Empty(text: String) {
    Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
        Text(text, color = Muted, fontWeight = FontWeight.Medium)
    }
}
