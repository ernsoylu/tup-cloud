import { Button, Group, Loader, Menu, Text, TextInput } from '@mantine/core'
import { Link, RichTextEditor } from '@mantine/tiptap'
import { IconArrowLeft, IconHistory } from '@tabler/icons-react'
import StarterKit from '@tiptap/starter-kit'
import { useEditor } from '@tiptap/react'
import { useEffect, useState } from 'react'
import { Markdown } from 'tiptap-markdown'
import { api, fetchText, fetchVersionText } from '../api'
import { confirmModal } from '../dialogs'
import { newMarkdownName } from '../ops'
import { dirOf, formatBytes, formatWhen, useStore } from '../store'
import type { FileVersionItem, VfsEntry } from '../types'

/** Full-page markdown editor: fixed header and toolbar, only the document
 * scrolls. Opened from "New markdown file" or right-click → Edit markdown. */
export default function MarkdownEditor() {
  const target = useStore((s) => s.editorTarget)
  const setTarget = useStore((s) => s.setEditorTarget)
  const currentDrive = useStore((s) => s.currentDrive)
  const refreshIndex = useStore((s) => s.refreshIndex)
  const toast = useStore((s) => s.toast)

  const [fileName, setFileName] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [versions, setVersions] = useState<FileVersionItem[] | null>(null)

  const editor = useEditor({
    extensions: [StarterKit, Link, Markdown],
    content: '',
    onUpdate: () => setDirty(true),
  })

  const isExisting = target !== null && 'entry' in target
  const entry = isExisting ? (target as { entry: VfsEntry }).entry : null

  useEffect(() => {
    if (!target || !editor) return
    setDirty(false)
    setVersions(null)
    if ('entry' in target) {
      setFileName(target.entry.file_name)
      setLoading(true)
      fetchText(target.entry.id)
        .then((text) => editor.commands.setContent(text))
        .catch((error) => toast('error', (error as Error).message))
        .finally(() => setLoading(false))
    } else {
      setFileName(newMarkdownName())
      editor.commands.setContent('')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, editor])

  const close = async () => {
    if (dirty) {
      const discard = await confirmModal({
        title: 'Unsaved changes',
        message: 'Discard your unsaved changes?',
        confirmLabel: 'Discard',
        danger: true,
      })
      if (!discard) return
    }
    setTarget(null)
  }

  const save = async () => {
    if (!target || !editor || !currentDrive) return
    let name = fileName.trim()
    if (!name) {
      toast('error', 'Give the file a name first.')
      return
    }
    if (!/\.(md|markdown|txt)$/i.test(name)) name += '.md'
    const directory = entry ? entry.virtual_path : dirOf((target as { newIn: string }).newIn)
    const path = `${directory}${name}`
    const markdown: string = editor.storage.markdown.getMarkdown()
    setSaving(true)
    try {
      const result = await api.saveText(currentDrive, path, markdown)
      setDirty(false)
      toast('info', result.unchanged ? 'No changes to save.' : `Saved ${name}.`)
      await refreshIndex(true)
      if (!entry) {
        // First save of a new file: keep editing it as an existing file.
        const saved = useStore
          .getState()
          .entries.find((e) => e.id === result.id)
        if (saved) setTarget({ entry: saved })
      }
    } catch (error) {
      toast('error', (error as Error).message)
    } finally {
      setSaving(false)
    }
  }

  // Cmd/Ctrl+S saves while the editor page is open.
  useEffect(() => {
    if (!target) return
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        void save()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, fileName, editor, currentDrive])

  if (!target) return null

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 300,
        background: 'var(--mantine-color-body)',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <Group
        px="md"
        py={8}
        justify="space-between"
        wrap="nowrap"
        style={{ borderBottom: '1px solid var(--mantine-color-dark-4)' }}
      >
        <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
          <Button
            variant="subtle"
            size="compact-sm"
            leftSection={<IconArrowLeft size={16} />}
            onClick={() => void close()}
          >
            Back
          </Button>
          {entry ? (
            <Text ff="monospace" size="sm" c="dimmed" truncate>
              {entry.virtual_path + entry.file_name}
              {dirty ? ' •' : ''}
            </Text>
          ) : (
            <TextInput
              size="xs"
              w={260}
              value={fileName}
              onChange={(e) => setFileName(e.currentTarget.value)}
            />
          )}
        </Group>
        <Group gap="xs" wrap="nowrap">
          {entry && (
            <Menu
              position="bottom-end"
              shadow="md"
              onOpen={async () => {
                try {
                  setVersions(await api.listVersions(entry.id))
                } catch (error) {
                  toast('error', (error as Error).message)
                }
              }}
            >
              <Menu.Target>
                <Button
                  variant="default"
                  size="compact-sm"
                  leftSection={<IconHistory size={15} />}
                >
                  History
                </Button>
              </Menu.Target>
              <Menu.Dropdown>
                {versions === null && <Menu.Item disabled>Loading…</Menu.Item>}
                {versions?.length === 0 && (
                  <Menu.Item disabled>No older versions yet — every save adds one.</Menu.Item>
                )}
                {versions?.map((v) => (
                  <Menu.Item
                    key={v.id}
                    onClick={async () => {
                      try {
                        const text = await fetchVersionText(v.id)
                        editor?.commands.setContent(text)
                        setDirty(true)
                        toast('info', 'Old version loaded — Save to restore it.')
                      } catch (error) {
                        toast('error', (error as Error).message)
                      }
                    }}
                  >
                    <Text size="xs">
                      {formatWhen(v.created_at)} · {formatBytes(v.file_size) || '0 B'}
                      {v.saved_by ? ` · ${v.saved_by}` : ''}
                    </Text>
                  </Menu.Item>
                ))}
              </Menu.Dropdown>
            </Menu>
          )}
          <Button size="compact-sm" onClick={() => void save()} loading={saving} disabled={loading}>
            Save
          </Button>
        </Group>
      </Group>

      {loading ? (
        <Group justify="center" py="xl">
          <Loader size="sm" />
        </Group>
      ) : (
        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
        <RichTextEditor
          editor={editor}
          styles={{
            root: { border: 0, borderRadius: 0 },
            content: { background: 'transparent', minHeight: 'calc(100vh - 160px)' },
          }}
        >
          <RichTextEditor.Toolbar sticky stickyOffset={0}>
            <RichTextEditor.ControlsGroup>
              <RichTextEditor.Bold />
              <RichTextEditor.Italic />
              <RichTextEditor.Strikethrough />
              <RichTextEditor.Code />
              <RichTextEditor.ClearFormatting />
            </RichTextEditor.ControlsGroup>
            <RichTextEditor.ControlsGroup>
              <RichTextEditor.H1 />
              <RichTextEditor.H2 />
              <RichTextEditor.H3 />
            </RichTextEditor.ControlsGroup>
            <RichTextEditor.ControlsGroup>
              <RichTextEditor.BulletList />
              <RichTextEditor.OrderedList />
              <RichTextEditor.Blockquote />
              <RichTextEditor.CodeBlock />
              <RichTextEditor.Hr />
            </RichTextEditor.ControlsGroup>
            <RichTextEditor.ControlsGroup>
              <RichTextEditor.Link />
              <RichTextEditor.Unlink />
            </RichTextEditor.ControlsGroup>
            <RichTextEditor.ControlsGroup>
              <RichTextEditor.Undo />
              <RichTextEditor.Redo />
            </RichTextEditor.ControlsGroup>
          </RichTextEditor.Toolbar>
          <RichTextEditor.Content style={{ width: '100%' }} />
        </RichTextEditor>
        </div>
      )}
    </div>
  )
}
