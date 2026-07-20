import { useEffect } from 'react'
import { copySelection, deleteSelection, newFolder, paste } from '../ops'
import { buildRows, parentDir, useStore } from '../store'

/** Global keyboard shortcuts (ignored while typing in inputs). */
export function useShortcuts(openUploadPicker: () => void) {
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement
      const typing =
        target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable
      const store = useStore.getState()
      if (store.editorTarget) return // the markdown editor owns the keyboard
      const meta = event.metaKey || event.ctrlKey

      if (event.key === 'Escape') {
        if (store.preview) store.setPreview(null)
        else if (typing) (target as HTMLInputElement).blur()
        else store.setSelection(new Set())
        return
      }
      if (typing) return

      if (meta && event.key.toLowerCase() === 'a') {
        event.preventDefault()
        const rows = buildRows(store.entries, store.currentDir, store.filter)
        store.setSelection(new Set(rows.map((r) => r.name)))
      } else if (meta && event.key.toLowerCase() === 'c') {
        copySelection('copy')
      } else if (meta && event.key.toLowerCase() === 'x') {
        copySelection('cut')
      } else if (meta && event.key.toLowerCase() === 'v') {
        void paste()
      } else if (meta && event.shiftKey && event.key.toLowerCase() === 'n') {
        event.preventDefault()
        void newFolder()
      } else if (event.key === 'Delete') {
        void deleteSelection()
      } else if (event.key === 'Backspace') {
        event.preventDefault()
        if (store.currentDir !== '/') store.setDir(parentDir(store.currentDir))
      } else if (event.key === 'F5' || (meta && event.key.toLowerCase() === 'r')) {
        event.preventDefault()
        void store.refreshIndex()
      } else if (event.key === '/') {
        event.preventDefault()
        document.getElementById('file-filter')?.focus()
      } else if (event.key.toLowerCase() === 'u' && !meta) {
        openUploadPicker()
      } else if (event.key.toLowerCase() === 't' && !meta) {
        store.toggleTransfers()
      } else if (event.key.toLowerCase() === 'o' && !meta) {
        store.toggleObserver()
      } else if (event.key.toLowerCase() === 'g' && !meta) {
        store.setView(store.view === 'details' ? 'grid' : 'details')
      } else if (event.key.toLowerCase() === 'r' && !meta) {
        store.toggleRecursive()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [openUploadPicker])
}
