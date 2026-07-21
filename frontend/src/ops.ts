/** File operations invoked from menus, shortcuts, and drag-and-drop. */

import { api } from './api'
import { confirmModal, promptModal } from './dialogs'
import { isCad, isEda, isOffice } from './media'
import { buildRows, dirOf, inTrash, KEEP_FILE, Row, TRASH_DIR, useStore } from './store'

export function rowPath(row: Row): string {
  const dir = dirOf(useStore.getState().currentDir)
  return row.kind === 'folder' ? `${dir}${row.name}/` : `${dir}${row.name}`
}

export function isMarkdown(name: string): boolean {
  return /\.(md|markdown)$/i.test(name)
}

export function openRow(row: Row): void {
  const store = useStore.getState()
  if (row.kind === 'folder') store.setDir(rowPath(row))
  else if (row.entry && isCad(row.entry.file_name) && !inTrash(store.currentDir))
    store.setCadEntry(row.entry)
  else if (row.entry && isEda(row.entry.file_name) && !inTrash(store.currentDir))
    store.setEdaEntry(row.entry)
  else if (row.entry && isOffice(row.entry.file_name) && !inTrash(store.currentDir))
    store.setOfficeEntry(row.entry) // Collabora is both viewer and editor
  else if (row.entry) store.setPreview(row.entry) // preview by default, edit via context menu
}

const OFFICE_BASES: Record<string, { base: string; ext: string }> = {
  document: { base: 'New Document', ext: 'docx' },
  spreadsheet: { base: 'New Spreadsheet', ext: 'xlsx' },
  presentation: { base: 'New Presentation', ext: 'pptx' },
  drawing: { base: 'New Drawing', ext: 'odg' },
}

/** Create a blank office file (auto-named like markdown) and open it in CODE. */
export async function createOfficeFile(
  docType: 'document' | 'spreadsheet' | 'presentation' | 'drawing',
): Promise<void> {
  const store = useStore.getState()
  const chatId = store.currentDrive
  if (!chatId) return
  const { base, ext } = OFFICE_BASES[docType]
  const dir = dirOf(store.currentDir)
  const taken = new Set(
    store.entries
      .filter((e) => e.virtual_path === dir)
      .map((e) => e.file_name.toLowerCase()),
  )
  let name = `${base}.${ext}`
  for (let n = 1; taken.has(name.toLowerCase()) && n < 1000; n++) name = `${base}_${n}.${ext}`
  try {
    const result = await api.newOfficeFile(chatId, `${dir}${name}`, docType)
    await store.refreshIndex(true)
    const entry = useStore.getState().entries.find((e) => e.id === result.id)
    if (entry) store.setOfficeEntry(entry)
  } catch (error) {
    store.toast('error', (error as Error).message)
  }
}

// Minimal R12 DXF (header + empty tables/blocks/entities) — plain text, so it
// goes through the same save pipeline as markdown and parses in any CAD app.
const BLANK_DXF = [
  '0', 'SECTION', '2', 'HEADER', '9', '$ACADVER', '1', 'AC1009', '0', 'ENDSEC',
  '0', 'SECTION', '2', 'TABLES', '0', 'ENDSEC',
  '0', 'SECTION', '2', 'BLOCKS', '0', 'ENDSEC',
  '0', 'SECTION', '2', 'ENTITIES', '0', 'ENDSEC',
  '0', 'EOF', '',
].join('\n')

/** Create a blank .dxf (auto-named like office files) and open it in OpenCADStudio. */
export async function createCadFile(): Promise<void> {
  const store = useStore.getState()
  const chatId = store.currentDrive
  if (!chatId) return
  const dir = dirOf(store.currentDir)
  const taken = new Set(
    store.entries
      .filter((e) => e.virtual_path === dir)
      .map((e) => e.file_name.toLowerCase()),
  )
  let name = 'New CAD Drawing.dxf'
  for (let n = 1; taken.has(name.toLowerCase()) && n < 1000; n++) name = `New CAD Drawing_${n}.dxf`
  try {
    const result = await api.saveText(chatId, `${dir}${name}`, BLANK_DXF)
    await store.refreshIndex(true)
    const entry = useStore.getState().entries.find((e) => e.id === result.id)
    if (entry) store.setCadEntry(entry)
  } catch (error) {
    store.toast('error', (error as Error).message)
  }
}

/** v4 uuid from getRandomValues — crypto.randomUUID needs a secure context,
 * and the stack is commonly reached over plain LAN http. */
function uuidv4(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

// Blank Signex schematic — the exact serialisation Signex v0.14 produces for
// an empty A4 sheet (TOML envelope + TSV bulk blocks), with a fresh uuid per
// file. Plain text, so it flows through the same save pipeline as markdown.
const blankSnxsch = () => `format = "snxsch/1"
schematic_id = "${uuidv4()}"
version = 1
generator = "signex"
generator_version = "0.14.0"
paper_size = "A4"
root_sheet_page = "1"


[sheets.components]
content = """
uuid  ref  library  pos_x  pos_y  rotation  value  mpn
"""

[sheets.wires]
content = """
uuid  net  start_x  start_y  end_x  end_y  stroke_width
"""

[sheets.junctions]
content = """
uuid  pos_x  pos_y  diameter
"""

[sheets.labels]
content = """
uuid  text  pos_x  pos_y  rotation  kind  shape  font_size  justify  justify_v
"""
`

/** Create a blank .snxsch (auto-named like office files) and open it in Signex. */
export async function createEdaFile(): Promise<void> {
  const store = useStore.getState()
  const chatId = store.currentDrive
  if (!chatId) return
  const dir = dirOf(store.currentDir)
  const taken = new Set(
    store.entries
      .filter((e) => e.virtual_path === dir)
      .map((e) => e.file_name.toLowerCase()),
  )
  let name = 'New Schematic.snxsch'
  for (let n = 1; taken.has(name.toLowerCase()) && n < 1000; n++) name = `New Schematic_${n}.snxsch`
  try {
    const result = await api.saveText(chatId, `${dir}${name}`, blankSnxsch())
    await store.refreshIndex(true)
    const entry = useStore.getState().entries.find((e) => e.id === result.id)
    if (entry) store.setEdaEntry(entry)
  } catch (error) {
    store.toast('error', (error as Error).message)
  }
}

/** First free "New Markdown File[_n].md" name in the current folder. */
export function newMarkdownName(): string {
  const store = useStore.getState()
  const taken = new Set(
    store.entries
      .filter((e) => e.virtual_path === dirOf(store.currentDir))
      .map((e) => e.file_name.toLowerCase()),
  )
  if (!taken.has('new markdown file.md')) return 'New Markdown File.md'
  for (let n = 1; n < 1000; n++) {
    if (!taken.has(`new markdown file_${n}.md`)) return `New Markdown File_${n}.md`
  }
  return `New Markdown File_${Date.now()}.md`
}

export async function restoreRows(names: string[]): Promise<void> {
  const store = useStore.getState()
  const chatId = store.currentDrive
  if (!chatId) return
  const dir = dirOf(store.currentDir) // somewhere inside /.Trash/
  for (const name of names) {
    try {
      await api.trashRestore(chatId, dir + name)
    } catch (error) {
      store.toast('error', `${name}: ${(error as Error).message}`)
    }
  }
  store.setSelection(new Set())
  await store.refreshIndex(true)
}

export async function deleteSelection(): Promise<void> {
  const store = useStore.getState()
  const chatId = store.currentDrive
  if (!chatId || store.selection.size === 0) return
  const rows = currentRows().filter((r) => store.selection.has(r.name))
  const label =
    rows.length === 1 ? `“${rows[0].name}”` : `${rows.length} items`
  const permanent = inTrash(store.currentDir)
  const confirmed = await confirmModal(
    permanent
      ? {
          title: 'Delete forever',
          message: `Permanently delete ${label} and all saved versions? This cannot be undone.`,
          confirmLabel: 'Delete forever',
          danger: true,
        }
      : {
          title: 'Move to Recycle Bin',
          message: `Move ${label} to the Recycle Bin? Folders must be empty to delete.`,
          confirmLabel: 'Move to bin',
        },
  )
  if (!confirmed) return
  for (const row of rows) {
    try {
      if (row.kind === 'folder') await api.rmdir(chatId, rowPath(row))
      else await api.rm(chatId, rowPath(row))
    } catch (error) {
      store.toast('error', `${row.name}: ${(error as Error).message}`)
    }
  }
  store.setSelection(new Set())
  await store.refreshIndex(true)
}

export function copySelection(mode: 'copy' | 'cut'): void {
  const store = useStore.getState()
  const chatId = store.currentDrive
  if (!chatId || store.selection.size === 0) return
  const rows = currentRows().filter((r) => store.selection.has(r.name))
  const files = rows.filter((r) => r.kind === 'file')
  if (files.length === 0) {
    store.toast('info', 'Only files can be copied or moved.')
    return
  }
  if (files.length < rows.length) store.toast('info', 'Folders were left out of the clipboard.')
  store.setClipboard({ mode, chatId, paths: files.map(rowPath) })
  store.toast('info', `${files.length} file(s) ready to ${mode === 'copy' ? 'copy' : 'move'}.`)
}

export async function paste(): Promise<void> {
  const store = useStore.getState()
  const clipboard = store.clipboard
  const chatId = store.currentDrive
  if (!clipboard || !chatId) return
  if (inTrash(store.currentDir)) {
    store.toast('info', 'Restore files out of the Recycle Bin instead of pasting into it.')
    return
  }
  if (clipboard.chatId !== chatId) {
    store.toast('error', 'Cross-drive copy/move is not supported (Telegram file IDs are chat-scoped).')
    return
  }
  const dest = store.currentDir
  for (const src of clipboard.paths) {
    try {
      if (clipboard.mode === 'copy') await api.cp(chatId, src, dest)
      else await api.mv(chatId, src, dest)
    } catch (error) {
      store.toast('error', `${src}: ${(error as Error).message}`)
    }
  }
  if (clipboard.mode === 'cut') store.setClipboard(null)
  await store.refreshIndex(true)
}

export async function moveOrCopyNames(
  names: string[],
  destDir: string,
  copy: boolean,
): Promise<void> {
  const store = useStore.getState()
  const chatId = store.currentDrive
  if (!chatId) return
  const dir = dirOf(store.currentDir)
  for (const name of names) {
    try {
      if (copy) await api.cp(chatId, `${dir}${name}`, destDir)
      else await api.mv(chatId, `${dir}${name}`, destDir)
    } catch (error) {
      store.toast('error', `${name}: ${(error as Error).message}`)
    }
  }
  await store.refreshIndex(true)
}

export async function newFolder(): Promise<void> {
  const store = useStore.getState()
  const chatId = store.currentDrive
  if (!chatId) return
  const name = await promptModal({
    title: 'New folder',
    label: 'Folder name',
    placeholder: 'e.g. documents',
    confirmLabel: 'Create',
  })
  if (!name || !name.trim()) return
  try {
    await api.mkdir(chatId, `${dirOf(store.currentDir)}${name.trim()}`)
    await store.refreshIndex(true)
  } catch (error) {
    store.toast('error', (error as Error).message)
  }
}

function currentRows(): Row[] {
  const store = useStore.getState()
  return buildRows(store.entries, store.currentDir, '', store.recursive)
}

// --- OS drag & drop: walk dropped folders, skipping dotfiles (tup rule) -------

export interface DroppedFile {
  file: File
  relDir: string
}

export async function collectDropped(items: DataTransferItemList): Promise<DroppedFile[]> {
  const collected: DroppedFile[] = []
  const walkers: Promise<void>[] = []
  for (const item of Array.from(items)) {
    const entry = item.webkitGetAsEntry?.()
    if (entry) walkers.push(walkEntry(entry, '', collected))
    else {
      const file = item.getAsFile()
      if (file && !file.name.startsWith('.')) collected.push({ file, relDir: '' })
    }
  }
  await Promise.all(walkers)
  return collected
}

async function walkEntry(
  entry: FileSystemEntry,
  relDir: string,
  out: DroppedFile[],
): Promise<void> {
  if (entry.name.startsWith('.') && relDir !== '') return
  if (entry.name.startsWith('.') && entry.isDirectory) return
  if (entry.isFile) {
    if (entry.name.startsWith('.')) return
    const file = await new Promise<File>((resolve, reject) =>
      (entry as FileSystemFileEntry).file(resolve, reject),
    )
    out.push({ file, relDir })
    return
  }
  if (entry.isDirectory) {
    const dir = relDir ? `${relDir}/${entry.name}` : entry.name
    const reader = (entry as FileSystemDirectoryEntry).createReader()
    let batch: FileSystemEntry[]
    do {
      batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
        reader.readEntries(resolve, reject),
      )
      await Promise.all(batch.map((child) => walkEntry(child, dir, out)))
    } while (batch.length > 0)
  }
}

export { KEEP_FILE }
