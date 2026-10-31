package app.tracking.viewer.data

import android.content.Context
import android.net.Uri
import kotlinx.serialization.json.Json
import java.io.File
import java.io.InputStream
import java.util.UUID
import java.util.zip.ZipInputStream

/**
 * The libraries imported on this phone, each unpacked into its own folder:
 * files/libraries/<id>/{manifest.json, library.json, covers/}. Read-only: nothing here
 * ever changes a library, only imports, opens or removes one.
 */
class PackageStore(private val root: File, private val context: Context?) {
    constructor(context: Context) : this(File(context.filesDir, "libraries"), context)

    init {
        root.mkdirs()
    }

    private val prefs = context?.getSharedPreferences("tracking", Context.MODE_PRIVATE)
    private var current: String? = null  // without a Context (tests)

    val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    class ImportError(message: String) : Exception(message)

    fun libraries(): List<StoredLibrary> =
        // ".importing-…" folders are imports in progress, not libraries (yet).
        root.listFiles().orEmpty().filter { it.isDirectory && !it.name.startsWith(".") }.mapNotNull { dir ->
            runCatching {
                StoredLibrary(dir.name, json.decodeFromString<Manifest>(File(dir, MANIFEST).readText()), dir)
            }.getOrNull()
        }.sortedBy { it.manifest.name.lowercase() }

    var currentId: String?
        get() = prefs?.getString("current", null) ?: current
        set(value) {
            current = value
            prefs?.edit()?.putString("current", value)?.apply()
        }

    fun load(lib: StoredLibrary): Library =
        json.decodeFromString(File(lib.dir, LIBRARY).readText())

    /** Unpack a package from a content:// or file:// uri. Returns the imported library. */
    fun import(uri: Uri, onProgress: (String) -> Unit = {}): StoredLibrary {
        val stream = try {
            context!!.contentResolver.openInputStream(uri)
        } catch (e: SecurityException) {
            throw ImportError(NO_ACCESS)
        } catch (e: java.io.FileNotFoundException) {
            // A plain file path from some file managers: Android doesn't let apps read those.
            throw ImportError(if (uri.scheme == "file") NO_ACCESS else "That file isn't there any more.")
        } ?: throw ImportError("Can't open that file.")
        return stream.use { import(it, onProgress) }
    }

    fun import(input: InputStream, onProgress: (String) -> Unit = {}): StoredLibrary {
        val tmp = File(root, ".importing-" + UUID.randomUUID()).apply { mkdirs() }
        try {
            var files = 0
            ZipInputStream(input.buffered()).use { zip ->
                while (true) {
                    val entry = zip.nextEntry ?: break
                    val out = File(tmp, entry.name).canonicalFile
                    // Never write outside the library's own folder ("../" in a zip).
                    if (!out.path.startsWith(tmp.canonicalPath + File.separator)) continue
                    if (entry.isDirectory) {
                        out.mkdirs()
                    } else {
                        out.parentFile?.mkdirs()
                        out.outputStream().use { zip.copyTo(it) }
                        if (++files % 200 == 0) onProgress("Unpacking… $files files")
                    }
                }
            }
            val manifestFile = File(tmp, MANIFEST)
            if (!manifestFile.exists() || !File(tmp, LIBRARY).exists()) {
                throw ImportError("This isn't a Tracking library package.")
            }
            val manifest = json.decodeFromString<Manifest>(manifestFile.readText())
            if (manifest.format != PACKAGE_FORMAT) throw ImportError("This isn't a Tracking library package.")
            if (manifest.version > PACKAGE_VERSION) {
                throw ImportError("This package was made by a newer Tracking. Update the app to open it.")
            }
            // Re-importing a library with the same name replaces it.
            libraries().filter { it.manifest.name == manifest.name }.forEach { it.dir.deleteRecursively() }
            val id = UUID.randomUUID().toString()
            val dest = File(root, id)
            java.nio.file.Files.move(tmp.toPath(), dest.toPath())
            currentId = id
            return StoredLibrary(id, manifest, dest)
        } catch (e: ImportError) {
            tmp.deleteRecursively()
            throw e
        } catch (e: Exception) {
            tmp.deleteRecursively()
            // Say what went wrong: the class name tells a missing permission from a bad zip.
            throw ImportError("Couldn't read that file (${e.javaClass.simpleName}: ${e.message ?: "no details"})")
        }
    }

    fun remove(lib: StoredLibrary) {
        lib.dir.deleteRecursively()
        if (currentId == lib.id) currentId = libraries().firstOrNull()?.id
    }

    companion object {
        const val MANIFEST = "manifest.json"
        const val LIBRARY = "library.json"
        const val NO_ACCESS = "The app wasn't allowed to read that file. Use Import a library in the app " +
            "and pick the file there instead."
    }
}
