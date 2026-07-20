import { notifications } from '@mantine/notifications'
import { create } from 'zustand'
import { api, uploadFile } from './api'
import type { Drive, ObserverEventItem, Transfer, UserInfo, VfsEntry } from './types'

export const KEEP_FILE = '.keep'
export const TRASH_DIR = '/.Trash/'

export function inTrash(dir: string): boolean {
  return dirOf(dir).startsWith(TRASH_DIR)
}

export interface Clipboard {
  mode: 'copy' | 'cut'
  chatId: string
  paths: string[]
}

interface State {
  user: UserInfo | null
  authChecked: boolean
  drives: Drive[]
  currentDrive: string | null
  entries: VfsEntry[]
  loadingIndex: boolean
  currentDir: string
  selection: Set<string> // file/folder names within currentDir
  filter: string
  view: 'details' | 'grid'
  recursive: boolean
  preview: VfsEntry | null
  /** Markdown editor target: an existing file, or a new file in a folder. */
  editorTarget: { entry: VfsEntry } | { newIn: string } | null
  /** Collabora (CODE) editor target. */
  officeEntry: VfsEntry | null
  /** OpenCADStudio editor target. */
  cadEntry: VfsEntry | null
  transfers: Record<string, Transfer>
  observerEvents: ObserverEventItem[]
  showTransfers: boolean
  showObserver: boolean
  clipboard: Clipboard | null

  bootstrap: () => Promise<void>
  setUser: (user: UserInfo | null) => void
  logout: () => Promise<void>
  loadDrives: () => Promise<void>
  selectDrive: (chatId: string) => void
  setDefaultDrive: (chatId: string) => Promise<void>
  refreshIndex: (quiet?: boolean) => Promise<void>
  setDir: (dir: string) => void
  setSelection: (names: Set<string>) => void
  setFilter: (filter: string) => void
  setView: (view: 'details' | 'grid') => void
  toggleRecursive: () => void
  setPreview: (entry: VfsEntry | null) => void
  setEditorTarget: (target: { entry: VfsEntry } | { newIn: string } | null) => void
  setOfficeEntry: (entry: VfsEntry | null) => void
  setCadEntry: (entry: VfsEntry | null) => void
  toggleTransfers: () => void
  toggleObserver: () => void
  setClipboard: (clipboard: Clipboard | null) => void
  applyTransfer: (transfer: Transfer) => void
  addObserverEvent: (event: ObserverEventItem) => void
  loadObserverEvents: () => Promise<void>
  loadTransfers: () => Promise<void>
  clearFinishedTransfers: () => Promise<void>
  uploadFiles: (files: { file: File; relDir: string }[]) => Promise<void>
  toast: (kind: 'error' | 'info', text: string) => void
}

let browserSeq = 1

export const useStore = create<State>((set, get) => ({
  user: null,
  authChecked: false,
  drives: [],
  currentDrive: null,
  entries: [],
  loadingIndex: false,
  currentDir: '/',
  selection: new Set<string>(),
  filter: '',
  view: 'details',
  recursive: false,
  preview: null,
  editorTarget: null,
  officeEntry: null,
  cadEntry: null,
  transfers: {},
  observerEvents: [],
  showTransfers: true,
  showObserver: false,
  clipboard: null,

  bootstrap: async () => {
    try {
      const user = await api.me()
      set({ user, authChecked: true })
      await get().loadDrives()
      await Promise.all([get().loadTransfers(), get().loadObserverEvents()])
    } catch {
      set({ user: null, authChecked: true })
    }
  },

  setUser: (user) => set({ user }),

  logout: async () => {
    try {
      await api.logout()
    } finally {
      set({ user: null, drives: [], entries: [], currentDrive: null })
    }
  },

  loadDrives: async () => {
    const drives = await api.drives()
    set({ drives })
    const current = get().currentDrive
    if (!current || !drives.some((d) => d.chat_id === current)) {
      const preferred = get().user?.default_chat_id
      const landing =
        (preferred && drives.find((d) => d.chat_id === preferred)) || drives[0]
      if (landing) get().selectDrive(landing.chat_id)
      else set({ currentDrive: null, entries: [] })
    }
  },

  setDefaultDrive: async (chatId) => {
    const user = get().user
    if (!user) return
    const next = user.default_chat_id === chatId ? '' : chatId // toggle off = clear
    try {
      await api.setDefaultDrive(next)
      set({ user: { ...user, default_chat_id: next } })
      get().toast('info', next ? 'Default drive saved.' : 'Default drive cleared.')
    } catch (error) {
      get().toast('error', (error as Error).message)
    }
  },

  selectDrive: (chatId) => {
    set({ currentDrive: chatId, currentDir: '/', selection: new Set(), filter: '' })
    void get().refreshIndex()
  },

  refreshIndex: async (quiet = false) => {
    const chatId = get().currentDrive
    if (!chatId) return
    if (!quiet) set({ loadingIndex: true })
    try {
      const entries = await api.index(chatId)
      if (get().currentDrive === chatId) set({ entries, loadingIndex: false })
    } catch (error) {
      set({ loadingIndex: false })
      if (!quiet) get().toast('error', `Failed to load index: ${(error as Error).message}`)
    }
  },

  setDir: (dir) => set({ currentDir: dir, selection: new Set(), filter: '' }),
  setSelection: (selection) => set({ selection }),
  setFilter: (filter) => set({ filter }),
  setView: (view) => set({ view }),
  toggleRecursive: () => set((s) => ({ recursive: !s.recursive, selection: new Set<string>() })),
  setPreview: (preview) => set({ preview }),
  setEditorTarget: (editorTarget) => set({ editorTarget }),
  setOfficeEntry: (officeEntry) => set({ officeEntry }),
  setCadEntry: (cadEntry) => set({ cadEntry }),
  toggleTransfers: () => set((s) => ({ showTransfers: !s.showTransfers })),
  toggleObserver: () => set((s) => ({ showObserver: !s.showObserver })),
  setClipboard: (clipboard) => set({ clipboard }),

  applyTransfer: (transfer) =>
    set((s) => ({ transfers: { ...s.transfers, [transfer.id]: transfer } })),

  addObserverEvent: (event) =>
    set((s) => {
      const rest = s.observerEvents.filter((e) => e.id !== event.id)
      return { observerEvents: [event, ...rest].slice(0, 100), showObserver: true }
    }),

  loadObserverEvents: async () => {
    try {
      set({ observerEvents: await api.observerEvents() })
    } catch {
      /* feed is non-critical */
    }
  },

  loadTransfers: async () => {
    try {
      const list = await api.transfers()
      set({ transfers: Object.fromEntries(list.map((t) => [t.id, t])) })
    } catch {
      /* non-critical */
    }
  },

  clearFinishedTransfers: async () => {
    await api.clearFinished()
    set((s) => ({
      transfers: Object.fromEntries(
        Object.entries(s.transfers).filter(
          ([, t]) => t.status === 'queued' || t.status === 'running',
        ),
      ),
    }))
  },

  uploadFiles: async (items) => {
    const chatId = get().currentDrive
    if (!chatId) return
    const baseDir = get().currentDir
    set({ showTransfers: true })
    for (const { file, relDir } of items) {
      const dest = joinDir(baseDir, relDir)
      const browserId = `browser-${browserSeq++}`
      const placeholder: Transfer = {
        id: browserId,
        kind: 'browser',
        chat_id: chatId,
        file_name: file.name,
        dest_dir: dest,
        size: file.size,
        sent: 0,
        status: 'running',
        error: '',
        created_at: new Date().toISOString(),
      }
      get().applyTransfer(placeholder)
      try {
        const transfer = await uploadFile(file, chatId, dest, (sent) => {
          get().applyTransfer({ ...placeholder, sent })
        })
        set((s) => {
          const next = { ...s.transfers }
          delete next[browserId]
          next[transfer.id] = transfer
          return { transfers: next }
        })
      } catch (error) {
        get().applyTransfer({
          ...placeholder,
          status: 'failed',
          error: (error as Error).message,
        })
        get().toast('error', `${file.name}: ${(error as Error).message}`)
      }
    }
  },

  toast: (kind, text) =>
    notifications.show({
      color: kind === 'error' ? 'red' : 'blue',
      message: text,
      autoClose: 6000,
    }),
}))

export function joinDir(base: string, rel: string): string {
  if (!rel) return base
  const combined = `${base}/${rel}`.split('/').filter(Boolean).join('/')
  return `/${combined}`
}

// --- derivations (pure, mirrors the desktop GUI's models.py) -----------------

export interface Row {
  kind: 'folder' | 'file'
  name: string // display name; in recursive mode a subpath like "sub/file.txt"
  entry: VfsEntry | null
  size: number
  mediaKind: string
  modified: string
  origin: string
  tags: string
}

export function dirOf(dir: string): string {
  return dir.endsWith('/') ? dir : `${dir}/`
}

export function parentDir(dir: string): string {
  const parts = dir.split('/').filter(Boolean)
  parts.pop()
  return parts.length ? `/${parts.join('/')}/` : '/'
}

export function allDirs(entries: VfsEntry[]): string[] {
  const dirs = new Set<string>(['/'])
  for (const e of entries) {
    if (e.virtual_path.startsWith(TRASH_DIR)) continue // bin has its own view
    const parts = e.virtual_path.split('/').filter(Boolean)
    let acc = '/'
    for (const part of parts) {
      acc = `${acc}${part}/`
      dirs.add(acc)
    }
  }
  return [...dirs].sort()
}

export function trashCount(entries: VfsEntry[]): number {
  return entries.filter((e) => e.virtual_path.startsWith(TRASH_DIR) && e.file_name !== KEEP_FILE)
    .length
}

function childDirsOf(dirs: string[], dir: string): string[] {
  const prefix = dirOf(dir)
  const children = new Set<string>()
  for (const d of dirs) {
    if (d !== prefix && d.startsWith(prefix)) {
      const rest = d.slice(prefix.length).split('/').filter(Boolean)
      if (rest.length >= 1) children.add(`${prefix}${rest[0]}/`)
    }
  }
  return [...children].sort()
}

export function childDirs(entries: VfsEntry[], dir: string): string[] {
  return childDirsOf(allDirs(entries), dir)
}

/** Folder paths inside the Recycle Bin, derived from trashed entries. */
function trashDirs(entries: VfsEntry[]): string[] {
  const dirs = new Set<string>([TRASH_DIR])
  for (const e of entries) {
    if (!e.virtual_path.startsWith(TRASH_DIR)) continue
    const parts = e.virtual_path.slice(TRASH_DIR.length).split('/').filter(Boolean)
    let acc = TRASH_DIR
    for (const part of parts) {
      acc = `${acc}${part}/`
      dirs.add(acc)
    }
  }
  return [...dirs].sort()
}

export function buildRows(
  entries: VfsEntry[],
  dir: string,
  filter: string,
  recursive = false,
): Row[] {
  const prefix = dirOf(dir)
  const toRow = (e: VfsEntry, name: string): Row => ({
    kind: 'file',
    name,
    entry: e,
    size: e.file_size,
    mediaKind: e.media_kind || 'document',
    modified: e.upload_timestamp,
    origin: e.origin,
    tags: e.tags,
  })
  let rows: Row[]
  if (prefix.startsWith(TRASH_DIR)) {
    // Recycle Bin: browse like a normal filesystem rooted at /.Trash/.
    if (recursive) {
      rows = entries
        .filter((e) => e.virtual_path.startsWith(prefix) && e.file_name !== KEEP_FILE)
        .map((e) => toRow(e, `${e.virtual_path.slice(prefix.length)}${e.file_name}`))
    } else {
      const folders: Row[] = childDirsOf(trashDirs(entries), prefix).map((d) => ({
        kind: 'folder',
        name: d.slice(prefix.length, -1),
        entry: null,
        size: 0,
        mediaKind: '',
        modified: '',
        origin: '',
        tags: '',
      }))
      const files = entries
        .filter((e) => e.virtual_path === prefix && e.file_name !== KEEP_FILE)
        .map((e) => toRow(e, e.file_name))
      rows = [...folders, ...files]
    }
  } else if (recursive) {
    // ls -R style: every file at or below the current folder, shown by subpath.
    rows = entries
      .filter(
        (e) =>
          e.virtual_path.startsWith(prefix) &&
          !e.virtual_path.startsWith(TRASH_DIR) &&
          e.file_name !== KEEP_FILE,
      )
      .map((e) => toRow(e, `${e.virtual_path.slice(prefix.length)}${e.file_name}`))
  } else {
    const folders: Row[] = childDirs(entries, prefix).map((d) => ({
      kind: 'folder',
      name: d.slice(prefix.length, -1),
      entry: null,
      size: 0,
      mediaKind: '',
      modified: '',
      origin: '',
      tags: '',
    }))
    const files = entries
      .filter((e) => e.virtual_path === prefix && e.file_name !== KEEP_FILE)
      .map((e) => toRow(e, e.file_name))
    rows = [...folders, ...files]
  }
  const needle = filter.trim().toLowerCase()
  if (!needle) return rows
  if (needle.startsWith('#')) {
    const tag = needle.slice(1)
    return rows.filter((r) => r.tags.split(' ').some((t) => t.includes(tag)))
  }
  return rows.filter(
    (r) => r.name.toLowerCase().includes(needle) || r.tags.toLowerCase().includes(needle),
  )
}

export function formatBytes(n: number): string {
  if (n === 0) return ''
  const units = ['B', 'KB', 'MB', 'GB']
  let value = n
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i++
  }
  return `${value >= 10 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`
}

export function formatWhen(iso: string): string {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString()
}
