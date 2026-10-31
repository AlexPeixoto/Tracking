package app.tracking.viewer.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.io.ByteArrayOutputStream
import java.nio.file.Files
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * Imports a real package (TRACKING_TEST_PACKAGE, e.g. one exported by the desktop app) the way
 * the app does, minus Android's file picker. Skipped when the variable isn't set.
 */
class PackageStoreTest {
    private fun tinyPackage(name: String): ByteArray {
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { zip ->
            fun put(path: String, text: String) {
                zip.putNextEntry(ZipEntry(path)); zip.write(text.toByteArray()); zip.closeEntry()
            }
            put("manifest.json", """{"format":"$PACKAGE_FORMAT","version":1,"name":"$name","counts":{"items":1}}""")
            put("library.json", """{"items":[{"id":1,"title":"Okami","category":"game","cover":"covers/0.webp"}]}""")
            put("covers/0.webp", "x")
        }
        return out.toByteArray()
    }

    @Test
    fun importingTwiceReplacesTheLibrary() {
        // The first version deleted its own import: the folder being unpacked had the same name.
        val root = Files.createTempDirectory("libraries").toFile()
        try {
            val store = PackageStore(root, null)
            val first = store.import(tinyPackage("Mine").inputStream())
            val again = store.import(tinyPackage("Mine").inputStream())
            store.import(tinyPackage("A friend's").inputStream())
            assertEquals(listOf("A friend's", "Mine"), store.libraries().map { it.manifest.name })
            assertTrue(!first.dir.exists() && File(again.dir, "covers/0.webp").exists())
            assertEquals("Okami", store.load(again).items.single().title)
            assertTrue(root.listFiles()!!.none { it.name.startsWith(".importing") })
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun refusesOtherZips() {
        val root = Files.createTempDirectory("libraries").toFile()
        val out = ByteArrayOutputStream()
        ZipOutputStream(out).use { it.putNextEntry(ZipEntry("photo.jpg")); it.write(1); it.closeEntry() }
        try {
            val e = runCatching { PackageStore(root, null).import(out.toByteArray().inputStream()) }.exceptionOrNull()
            assertTrue(e is PackageStore.ImportError && e.message!!.contains("isn't a Tracking library"))
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun importsARealPackage() {
        val path = System.getenv("TRACKING_TEST_PACKAGE") ?: return
        val root = Files.createTempDirectory("libraries").toFile()
        try {
            val store = PackageStore(root, null)
            val lib = File(path).inputStream().use { store.import(it) }
            val library = store.load(lib)
            println("imported ${lib.manifest.name}: ${library.items.size} items, ${library.completions.size} completions")
            assertEquals(lib.manifest.counts.items, library.items.size)
            assertTrue(library.items.filter { it.cover != null }.all { File(lib.dir, it.cover!!).exists() })
            assertEquals(listOf(lib.id), store.libraries().map { it.id })
        } finally {
            root.deleteRecursively()
        }
    }
}
