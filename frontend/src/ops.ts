/** File operations invoked from menus, shortcuts, and drag-and-drop. */

import { api } from './api'
import { confirmModal, promptModal } from './dialogs'
import { isOffice } from './media'
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
  for (const name of names) {
    try {
      await api.trashRestore(chatId, TRASH_DIR + name)
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
