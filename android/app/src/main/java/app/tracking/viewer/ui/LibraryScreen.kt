package app.tracking.viewer.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.tracking.viewer.data.Library
import app.tracking.viewer.data.StoredLibrary

@Composable
fun LibraryScreen(
    lib: StoredLibrary,
    library: Library,
    filters: Filters,
    onFilters: ((Filters) -> Filters) -> Unit,
    onOpen: (Int) -> Unit,
) {
    val items = remember(library, filters) { visibleItems(library, filters) }
    val platforms = remember(library, filters.section) {
        if (library.categories.firstOrNull { it.key == filters.section }?.platforms == true || filters.section == ALL)
            platformsIn(library, filters.section) else emptyList()
    }
    val categories = remember(library) { library.categories.associateBy { it.key } }
    val sections = remember(library) {
        listOf(ALL to "All") +
            library.categories.filter { c -> library.items.any { it.category == c.key && !it.wanted } }
                .map { it.key to it.label } +
            (if (library.items.any { it.wanted }) listOf(WANT to "Want to acquire") else emptyList())
    }

    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(
            value = filters.query,
            onValueChange = { q -> onFilters { it.copy(query = q) } },
            placeholder = { Text("Search") },
            leadingIcon = { Icon(Icons.Default.Search, null) },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
        )
        LazyRow(contentPadding = PaddingValues(horizontal = 12.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            items(sections) { (key, label) ->
                FilterChip(selected = filters.section == key, label = { Text(label) },
                           onClick = { onFilters { it.copy(section = key, platform = null) } })
            }
        }
        if (platforms.size > 1) {
            LazyRow(contentPadding = PaddingValues(horizontal = 12.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                items(platforms) { p ->
                    FilterChip(selected = filters.platform == p, label = { Text(p) },
                               onClick = { onFilters { it.copy(platform = if (it.platform == p) null else p) } })
                }
            }
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("${items.size} items", color = Muted, fontSize = 13.sp, modifier = Modifier.weight(1f))
            FilterChip(selected = filters.hideCompleted, label = { Text("Hide completed") },
                       onClick = { onFilters { it.copy(hideCompleted = !it.hideCompleted) } })
            var open by remember { mutableStateOf(false) }
            Box {
                TextButton(onClick = { open = true }) { Text(filters.sort.label) }
                DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
                    Sort.entries.forEach { s ->
                        DropdownMenuItem(text = { Text(s.label) }, onClick = {
                            onFilters { it.copy(sort = s) }
                            open = false
                        })
                    }
                }
            }
        }
        LazyVerticalGrid(
            columns = GridCells.Adaptive(108.dp),
            contentPadding = PaddingValues(12.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            modifier = Modifier.fillMaxSize(),
        ) {
            items(items, key = { it.id }) { item ->
                Column(Modifier.clickable { onOpen(item.id) }) {
                    Box {
                        Cover(lib.dir, item.cover, item.title, categories[item.category], Modifier.fillMaxWidth())
                        item.averageScore?.let {
                            ScoreBadge(it, Modifier.align(Alignment.TopEnd).padding(4.dp))
                        }
                        if (item.completed) {
                            Icon(Icons.Default.Check, "Completed", tint = Color.White,
                                 modifier = Modifier.align(Alignment.BottomEnd).padding(4.dp))
                        }
                    }
                    Text(item.title, fontSize = 13.sp, maxLines = 2, overflow = TextOverflow.Ellipsis,
                         modifier = Modifier.padding(top = 4.dp))
                    val sub = listOfNotNull(item.platform, item.year?.toString()).joinToString(" · ")
                    if (sub.isNotEmpty()) Text(sub, fontSize = 11.sp, color = Muted, maxLines = 1)
                }
            }
        }
    }
}
