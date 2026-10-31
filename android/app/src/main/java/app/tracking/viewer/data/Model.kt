package app.tracking.viewer.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/*
 * The library package, as written by the desktop app (tracking/package.py).
 * Field by field this follows docs/PACKAGE.md; unknown fields are ignored, so a
 * newer desktop app adding fields doesn't break an older viewer.
 */

const val PACKAGE_FORMAT = "tracking-library-package"
const val PACKAGE_VERSION = 1

@Serializable
data class Manifest(
    val format: String = "",
    val version: Int = 0,
    val name: String = "Library",
    val created: String? = null,
    val app: String? = null,
    val counts: Counts = Counts(),
)

@Serializable
data class Counts(val items: Int = 0, val completions: Int = 0, val covers: Int = 0)

@Serializable
data class Category(
    val key: String,
    val label: String,
    val singular: String = label,
    val done: String = "Completed",
    val episodic: Boolean = false,
    val platforms: Boolean = false,
    val colors: List<String> = listOf("#6b7280", "#262a33"),
)

@Serializable
data class Score(val source: String, val value: Double, val max: Double, val url: String? = null) {
    /** 0-100, to compare scores from sites with different scales. */
    val percent: Double get() = if (max > 0) value / max * 100 else 0.0
}

@Serializable
data class Rating(val value: Double, val max: Double? = null, val source: String? = null)

@Serializable
data class Hltb(val main: Double? = null, val extra: Double? = null, val complete: Double? = null)

@Serializable
data class Episode(
    val season: Int,
    val number: Int,
    val title: String? = null,
    val aired: String? = null,
    val score: Double? = null,
    val max: Double? = null,
    val owned: Boolean = false,
)

@Serializable
data class Item(
    val id: Int,
    val title: String,
    val category: String,
    val kind: String? = null,
    val season: Int? = null,
    val platform: String? = null,
    val year: Int? = null,
    val source: String = "disk",
    val store: String? = null,
    val completed: Boolean = false,
    @SerialName("completed_at") val completedAt: String? = null,
    val plan: String? = null,
    @SerialName("plan_position") val planPosition: Int? = null,
    @SerialName("playtime_min") val playtimeMin: Double? = null,
    @SerialName("last_played") val lastPlayed: String? = null,
    val hltb: Hltb? = null,
    @SerialName("duration_min") val durationMin: Double? = null,
    @SerialName("episodes_owned") val episodesOwned: Int = 0,
    @SerialName("last_episode") val lastEpisode: Int? = null,
    val rating: Rating? = null,
    val scores: List<Score> = emptyList(),
    val summary: String? = null,
    val url: String? = null,
    val cover: String? = null,
    val episodes: List<Episode> = emptyList(),
) {
    val wanted: Boolean get() = source == "wish"

    /** Average of every site's score on 0-100, or null when nobody scored it. */
    val averageScore: Double? get() = scores.takeIf { it.isNotEmpty() }?.map { it.percent }?.average()
}

@Serializable
data class Completion(
    val title: String,
    val category: String,
    val platform: String? = null,
    val date: String,
    val position: Int? = null,
    val item: Int? = null,
    val cover: String? = null,
) {
    val year: Int? get() = date.take(4).toIntOrNull()
}

@Serializable
data class Library(
    val categories: List<Category> = emptyList(),
    val items: List<Item> = emptyList(),
    val completions: List<Completion> = emptyList(),
)

/** A library imported on this phone: where its files are, and what it says about itself. */
data class StoredLibrary(val id: String, val manifest: Manifest, val dir: java.io.File)
