package app.tracking.viewer.ui

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Home
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import app.tracking.viewer.data.StoredLibrary

private enum class Tab(val label: String) { LIBRARY("Library"), COMPLETED("Completed"), PLANNING("Planning") }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun App(incoming: Uri?, onIncomingHandled: () -> Unit, vm: LibraryViewModel = viewModel()) {
    val state by vm.state.collectAsStateWithLifecycle()
    val nav = rememberNavController()
    val snackbar = remember { SnackbarHostState() }
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let(vm::import)
    }
    val openPicker = { pick.launch(arrayOf("application/zip", "application/x-zip-compressed", "application/octet-stream")) }

    LaunchedEffect(incoming) {
        if (incoming != null) {
            vm.import(incoming)
            onIncomingHandled()
        }
    }
    LaunchedEffect(state.message) {
        state.message?.let {
            snackbar.showSnackbar(it)
            vm.messageShown()
        }
    }

    var tab by rememberSaveable { mutableStateOf(Tab.LIBRARY) }
    var removing by remember { mutableStateOf<StoredLibrary?>(null) }

    NavHost(nav, startDestination = "home") {
        composable("home") {
            Scaffold(
                snackbarHost = { SnackbarHost(snackbar) },
                topBar = {
                    TopAppBar(
                        colors = TopAppBarDefaults.topAppBarColors(containerColor = Background),
                        title = {
                            LibrarySwitcher(state, onOpen = vm::open, onImport = openPicker,
                                            onRemove = { removing = it })
                        },
                    )
                },
                bottomBar = {
                    if (state.library != null) {
                        NavigationBar(containerColor = Surface) {
                            Tab.entries.forEach { t ->
                                NavigationBarItem(
                                    selected = tab == t, onClick = { tab = t }, label = { Text(t.label) },
                                    icon = {
                                        Icon(when (t) {
                                            Tab.LIBRARY -> Icons.Default.Home
                                            Tab.COMPLETED -> Icons.Default.CheckCircle
                                            Tab.PLANNING -> Icons.AutoMirrored.Filled.List
                                        }, null)
                                    },
                                )
                            }
                        }
                    }
                },
            ) { padding ->
                Box(Modifier.padding(padding).fillMaxSize()) {
                    val lib = state.current
                    val library = state.library
                    when {
                        state.busy != null -> Busy(state.busy!!)
                        lib == null -> Welcome(openPicker)
                        library == null -> Empty("Couldn't open this library.")
                        else -> {
                            val open = { id: Int -> nav.navigate("item/$id") }
                            when (tab) {
                                Tab.LIBRARY -> LibraryScreen(lib, library, state.filters, vm::setFilters, open)
                                Tab.COMPLETED -> CompletedScreen(lib, library, open)
                                Tab.PLANNING -> PlanningScreen(lib, library, open)
                            }
                        }
                    }
                }
            }
        }
        composable("item/{id}", arguments = listOf(navArgument("id") { type = NavType.IntType })) { entry ->
            val item = entry.arguments?.getInt("id")?.let(vm::item)
            val lib = state.current
            val library = state.library
            Scaffold(topBar = {
                TopAppBar(
                    colors = TopAppBarDefaults.topAppBarColors(containerColor = Background),
                    title = { Text(item?.title ?: "", maxLines = 1) },
                    navigationIcon = {
                        IconButton(onClick = { nav.popBackStack() }) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                        }
                    },
                )
            }) { padding ->
                Box(Modifier.padding(padding)) {
                    if (item != null && lib != null && library != null) ItemScreen(lib, library, item)
                    else Empty("Not in this library.")
                }
            }
        }
    }

    removing?.let { lib ->
        AlertDialog(
            onDismissRequest = { removing = null },
            title = { Text("Remove ${lib.manifest.name}?") },
            text = { Text("It's removed from this phone only. You can import the package again any time.") },
            confirmButton = { TextButton(onClick = { vm.remove(lib); removing = null }) { Text("Remove") } },
            dismissButton = { TextButton(onClick = { removing = null }) { Text("Cancel") } },
        )
    }
}

@Composable
private fun LibrarySwitcher(
    state: UiState,
    onOpen: (StoredLibrary) -> Unit,
    onImport: () -> Unit,
    onRemove: (StoredLibrary) -> Unit,
) {
    var open by remember { mutableStateOf(false) }
    Box {
        TextButton(onClick = { open = true }) {
            Text(state.current?.manifest?.name ?: "Tracking", fontSize = 20.sp)
            Icon(Icons.Default.ArrowDropDown, null)
        }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            state.libraries.forEach { lib ->
                DropdownMenuItem(
                    text = { Text("${lib.manifest.name}  ·  ${lib.manifest.counts.items} items") },
                    onClick = { onOpen(lib); open = false },
                )
            }
            if (state.libraries.isNotEmpty()) HorizontalDivider()
            DropdownMenuItem(text = { Text("Import a library…") }, onClick = { onImport(); open = false })
            state.current?.let { cur ->
                DropdownMenuItem(text = { Text("Remove ${cur.manifest.name}…") }, onClick = { onRemove(cur); open = false })
            }
        }
    }
}

@Composable
private fun Welcome(onImport: () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(32.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("Your library, on your phone", fontSize = 22.sp)
        Spacer(Modifier.height(12.dp))
        Text(
            "On the computer, Tracking › Export list… › Share library (Android app) makes a package. " +
                "Copy it here (USB, Google Drive, a chat) and open it, or pick it below.",
            color = Muted, textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(20.dp))
        Button(onClick = onImport) { Text("Import a library") }
    }
}

@Composable
private fun Busy(text: String) {
    Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.Center,
           horizontalAlignment = Alignment.CenterHorizontally) {
        CircularProgressIndicator()
        Spacer(Modifier.height(12.dp))
        Text(text, color = Muted)
    }
}
