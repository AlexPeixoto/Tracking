# Docs

How Tracking is built and the rules it follows, for people and AI sessions working on it.
The user-facing guide is the [README](../README.md) at the top of the repository.

| Read this | When you want to… |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | find your way around: every module, what it owns, how data flows, how to add a service / section / platform |
| [SCANNING.md](SCANNING.md) | understand or change *what* a scan finds: locations, games vs videos, collections, ROMs, seasons, extras, names |
| [MATCHING.md](MATCHING.md) | understand or change how titles are looked up: services, search wording, matching, covers and their fallbacks |
| [DATA.md](DATA.md) | know what is stored where: tables, settings keys, the data folder, migrations |
| [WORKFLOW.md](WORKFLOW.md) | make a change safely: tests, the replay check on a real library, building, conventions and things not to do |
| [WISHLIST.md](WISHLIST.md) | see what's done and what could come next |

## The short version

- Data flows one way: **locations → scan → snapshot + items → enrich (services) → UI**.
  The UI never writes SQL; it calls `library.py`, `scan/store.py`, `enrich.py`.
- **Tables, not code**, for anything that grows: platforms (`platforms.py`), sections
  (`categories.py`), services (`providers/__init__.py`), search wording rules
  (`providers/variants.py`), tools / extras / edition words (lists next to the code that uses them).
- **Every rule carries its real example** in a comment or docstring ("GalGun 2" is "Gal*Gun 2"
  to the services), and a test with that example.
- **Rule changes are checked against a real library**: `python -m tracking.scan.replay`
  re-processes the saved snapshots and diffs every item against a baseline. A change should
  move exactly the items it was meant to, nothing else.
