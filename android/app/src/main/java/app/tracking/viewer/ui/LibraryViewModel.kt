package app.tracking.viewer.ui

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import app.tracking.viewer.data.Item
import app.tracking.viewer.data.Library
import app.tracking.viewer.data.PackageStore
import app.tracking.viewer.data.StoredLibrary
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class Sort(val label: String) {
    TITLE("Title"),
    SCORE("Score"),
    RECENT("Recently finished"),
    YEAR("Release year"),
    PLAYED("Time played"),
}

/** The library section chips: "all", a category key, or "want" (Want to acquire). */
const val ALL = "all"
const val WANT = "want"

data class Filters(
    val section: String = ALL,
    val platform: String? = null,
    val query: String = "",
    val sort: Sort = Sort.TITLE,
    val hideCompleted: Boolean = false,
)

data class UiState(
    val libraries: List<StoredLibrary> = emptyList(),
    val current: StoredLibrary? = null,
    val library: Library? = null,
    val busy: String? = null,      // "Importing…" while something runs
    val message: String? = null,   // an error or a note to show once
    val filters: Filters = Filters(),
)

class LibraryViewModel(app: Application) : AndroidViewModel(app) {
    private val store = PackageStore(app)
    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    init {
        refresh(store.currentId)
    }

    private fun refresh(selectId: String?) {
        viewModelScope.launch {
            val libs = withContext(Dispatchers.IO) { store.libraries() }
            val pick = libs.firstOrNull { it.id == selectId } ?: libs.firstOrNull()
            _state.update { it.copy(libraries = libs, current = pick, library = null) }
            if (pick != null) open(pick)
        }
    }

    fun open(lib: StoredLibrary) {
        store.currentId = lib.id
        _state.update { it.copy(current = lib, busy = "Opening ${lib.manifest.name}…", filters = Filters()) }
        viewModelScope.launch {
            val loaded = withContext(Dispatchers.IO) { runCatching { store.load(lib) } }
            _state.update {
                it.copy(library = loaded.getOrNull(), busy = null,
                        message = loaded.exceptionOrNull()?.let { e -> "Couldn't open it: ${e.message}" })
            }
        }
    }

    fun import(uri: Uri) {
        _state.update { it.copy(busy = "Importing…") }
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching { store.import(uri) { msg -> _state.update { s -> s.copy(busy = msg) } } }
            }
            result.onSuccess { lib ->
                _state.update { it.copy(busy = null, message = "Imported ${lib.manifest.name}") }
                refresh(lib.id)
            }.onFailure { e ->
                _state.update { it.copy(busy = null, message = e.message ?: "Couldn't import that file.") }
            }
        }
    }

    fun remove(lib: StoredLibrary) {
        viewModelScope.launch {
            withContext(Dispatchers.IO) { store.remove(lib) }
            refresh(store.currentId)
        }
    }

    fun messageShown() = _state.update { it.copy(message = null) }

    fun setFilters(change: (Filters) -> Filters) = _state.update { it.copy(filters = change(it.filters)) }

    fun item(id: Int): Item? = _state.value.library?.items?.firstOrNull { it.id == id }
}

/** The items a library view shows for these filters, in their order. */
fun visibleItems(library: Library, f: Filters): List<Item> {
    val q = f.query.trim().lowercase()
    val shown = library.items.filter { item ->
        val inSection = when (f.section) {
            ALL -> !item.wanted
            WANT -> item.wanted
            else -> item.category == f.section && !item.wanted
        }
        inSection &&
            (f.platform == null || item.platform == f.platform) &&
            (q.isEmpty() || item.title.lowercase().contains(q)) &&
            !(f.hideCompleted && item.completed)
    }
    return when (f.sort) {
        Sort.TITLE -> shown.sortedBy { it.title.lowercase() }
        Sort.SCORE -> shown.sortedWith(compareByDescending<Item, Double?>(nullsFirst()) { it.averageScore }
            .thenBy { it.title.lowercase() })
        Sort.RECENT -> shown.sortedWith(compareByDescending<Item, String?>(nullsFirst()) { it.completedAt }
            .thenBy { it.title.lowercase() })
        Sort.YEAR -> shown.sortedWith(compareByDescending<Item, Int?>(nullsFirst()) { it.year }
            .thenBy { it.title.lowercase() })
        Sort.PLAYED -> shown.sortedWith(compareByDescending<Item, Double?>(nullsFirst()) { it.playtimeMin }
            .thenBy { it.title.lowercase() })
    }
}

/** Platforms present in a section, most items first (for the platform chips). */
fun platformsIn(library: Library, section: String): List<String> =
    library.items.filter { !it.wanted && (section == ALL || it.category == section) }
        .mapNotNull { it.platform }.groupingBy { it }.eachCount()
        .entries.sortedByDescending { it.value }.map { it.key }
